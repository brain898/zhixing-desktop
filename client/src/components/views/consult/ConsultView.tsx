import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Bot, Plus, RefreshCw } from 'lucide-react';
import { api } from '../../../services/api';
import { ConsultHandoff, ConsultSession, ConsultSkill } from '../../../types';
import { EmptyState } from '../../ui/EmptyState';
import { SegmentedTabs } from '../../ui/SegmentedTabs';
import { usePolling } from '../skills/usePolling';
import { ConsultForm } from './ConsultForm';
import { ConsultReport, valueText } from './ConsultReport';
import { useConsultResize } from './useConsultResize';
import './consult.css';

type Tab = 'consult' | 'skills' | 'handoffs';
const statusText = (status: string) => ({ routing: '正在选择 Skill', awaiting_input: '等待补充信息', running: '正在咨询', completed: '已完成', failed: '咨询失败', pending: '等待执行', queued: '等待执行', needs_human: '需人工', uncomputable: '不可计算', skipped: '未执行', open: '待处理', resolved: '已处理' }[status] || status);

export const ConsultView: React.FC = () => {
  const [tab, setTab] = useState<Tab>('consult');
  const [skills, setSkills] = useState<ConsultSkill[]>([]);
  const [history, setHistory] = useState<ConsultSession[]>([]);
  const [handoffs, setHandoffs] = useState<ConsultHandoff[]>([]);
  const [selected, setSelected] = useState<ConsultSession | null>(null);
  const [handoffId, setHandoffId] = useState<string | null>(null);
  const [question, setQuestion] = useState('');
  const [note, setNote] = useState('');
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const resize = useConsultResize(tab === 'consult' && !loading);
  const selectedId = useRef<string | null>(null);
  const selectionRequest = useRef(0);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; selectionRequest.current += 1; }; }, []);

  const loadLists = useCallback(async () => {
    const results = await Promise.all([api.getConsultSkills(), api.getConsultSessions(), api.getConsultHandoffs()]);
    if (!mounted.current) return;
    setSkills(results[0].items); setHistory(results[1].items); setHandoffs(results[2].items);
  }, []);
  const refresh = useCallback(async () => {
    try { await loadLists(); if (mounted.current) setError(''); }
    catch (err) { if (mounted.current) setError(err instanceof Error ? err.message : '咨询记录加载失败'); }
    finally { if (mounted.current) setLoading(false); }
  }, [loadLists]);
  useEffect(() => { void refresh(); }, [refresh]);

  const select = async (id: string) => {
    selectedId.current = id;
    const request = ++selectionRequest.current;
    setError('');
    try { const detail = await api.getConsultSession(id); if (mounted.current && selectionRequest.current === request && selectedId.current === id) setSelected(detail); }
    catch (err) { if (mounted.current && selectionRequest.current === request) setError(err instanceof Error ? err.message : '咨询详情加载失败'); }
  };
  const poll = async () => {
    const id = selectedId.current;
    if (id) await select(id);
    await refresh();
  };
  usePolling(!!selected && ['routing', 'running'].includes(selected.status), poll, 1000);

  const act = async (operation: () => Promise<unknown>, updateDetail = false) => {
    setBusy(true); setError('');
    try {
      await operation(); await loadLists();
      if (updateDetail && selectedId.current) await select(selectedId.current);
    } catch (err) { setError(err instanceof Error ? err.message : '操作失败，请重试'); }
    finally { setBusy(false); }
  };
  const start = async () => act(async () => {
    const session = await api.createConsultSession(question.trim());
    setQuestion(''); await select(session.id);
  });
  const newQuestion = () => { selectedId.current = null; selectionRequest.current += 1; setSelected(null); setError(''); setReason(''); };
  const handoff = handoffs.find(item => item.id === handoffId);

  return <div className="zx-consult" data-testid="consult-view">
    <div className="zx-topbar"><div className="zx-breadcrumb"><span>Agent 咨询</span><span>/</span><span className="current">{tab === 'consult' ? '咨询' : tab === 'skills' ? '可用 Skill' : '转人工记录'}</span></div>
      <SegmentedTabs<Tab> value={tab} onChange={setTab} options={[{ key: 'consult', label: '咨询', testId: 'tab-consult' }, { key: 'skills', label: '可用 Skill', testId: 'tab-consult-skills' }, { key: 'handoffs', label: '转人工记录', testId: 'tab-consult-handoffs', count: handoffs.filter(h => h.status === 'open').length || undefined }]} />
      <button className="btn-ghost" aria-label="刷新咨询记录" onClick={refresh}><RefreshCw size={15} /></button>
    </div>
    {error && <div role="alert" className="zx-consult-error zx-consult-global-error">{error}<button className="btn-ghost" onClick={refresh}>重新加载</button></div>}
    {loading ? <div className="zx-consult-loading">正在加载咨询工作台…</div> : tab === 'consult' ? <div className={`zx-consult-columns${resize.dragging ? ' zx-consult-resizing' : ''}`} ref={resize.containerRef} style={resize.style}>
      <aside className="zx-consult-history"><button className="btn-secondary" onClick={newQuestion}><Plus size={14} />新咨询</button><h3>咨询历史</h3>
        {history.length ? history.map(item => <button className={`zx-consult-history-item ${selected?.id === item.id ? 'active' : ''}`} data-testid="consult-history-item" key={item.id} onClick={() => void select(item.id)}><span>{item.question}</span><small>{statusText(item.status)} · {item.created_at.slice(0, 16).replace('T', ' ')}</small></button>) : <p className="zx-consult-muted">还没有咨询记录。</p>}
      </aside>
      <div className="zx-consult-dialog" id="consult-dialog">
        {!selected ? <><h2>把问题说清楚，按已审核 Skill 咨询</h2><p className="zx-consult-muted">每次咨询对应一个问题。系统会选择可用 Skill，必要时请你补充信息。</p><label className="zx-consult-field"><span>你的问题</span><textarea aria-label="你的问题" rows={6} value={question} onChange={e => setQuestion(e.target.value)} placeholder="请描述实际情况、已知数据和希望解决的问题" /></label><button className="btn-primary" disabled={busy || !question.trim()} onClick={start}>{busy ? '正在提交…' : '开始咨询'}</button>
          <div className="zx-consult-examples"><h3>当前可处理的问题</h3>{skills.filter(s => s.available && s.trigger_description).slice(0, 4).map(skill => <button key={skill.id} onClick={() => setQuestion(skill.trigger_description || '')}>{skill.trigger_description}</button>)}{!skills.some(s => s.available) && <p className="zx-consult-muted">还没有上架可用 Skill，可以先到「可用 Skill」上架。仍可提问，系统会尝试整理知识库依据。</p>}</div>
        </> : <><h2>本次咨询</h2><p className="zx-consult-question">{selected.question}</p><div className="zx-consult-progress" data-testid="consult-progress">{selected.progress?.message || statusText(selected.status)}</div>
          {selected.status === 'awaiting_input' && <ConsultForm key={`${selected.id}:${selected.status}`} fields={selected.form?.fields || []} busy={busy} onSubmit={async values => { setBusy(true); try { await api.submitConsultInputs(selected.id, values); await select(selected.id); await loadLists(); } finally { setBusy(false); } }} />}
          {selected.status === 'failed' && <div className="zx-consult-error" role="alert" data-testid="consult-failure"><strong>{selected.failure?.stage || '处理咨询'}时失败</strong><p>{selected.error || '本次咨询失败，请重试。'}</p>{selected.failure?.error_code === 'legacy_unknown' && <p>这条旧记录没有保留具体原因。重试后若仍失败，会显示新的失败原因。</p>}<button className="btn-secondary" disabled={busy} onClick={() => act(() => api.retryConsultSession(selected.id), true)}>{selected.failure?.retryable === false ? '处理问题后重试本次咨询' : '重试本次咨询'}</button></div>}
          {!!selected.runs?.length && <div className="zx-consult-execution"><h3>执行记录</h3>{selected.runs.map((item, i) => <details key={item.id || i}><summary>第 {i + 1} 个 · {item.name || item.skill_name || '咨询能力'} · {statusText(item.status)}</summary><p>所用版本：第 {item.version_number || '未知'} 版</p><p>输入：{valueText(item.inputs)}</p><p>结果：{valueText(item.output?.summary || item.output?.outputs)}</p>{!!item.validation && <p>{valueText(item.validation)}</p>}</details>)}</div>}
          {selected.handoffs?.some(h => h.status === 'open') ? <p className="zx-consult-notice">已记录转人工，等待处理。可在「转人工记录」查看。</p> : <div className="zx-consult-handoff-action"><label className="zx-consult-field"><span>需要人工协助的原因（选填）</span><input aria-label="转人工原因" value={reason} onChange={e => setReason(e.target.value)} /></label><button className="btn-secondary" disabled={busy} onClick={() => act(() => api.handoffConsultSession(selected.id, reason), true)}>转人工</button></div>}
        </>}
      </div>
      <div className="zx-consult-resizer" data-testid="consult-resizer" {...resize.separatorProps} />
      <div className="zx-consult-report-pane" id="consult-report-pane">{selected?.report ? <ConsultReport report={selected.report} runs={selected.runs || []} /> : <EmptyState icon={<Bot size={24} />} title="咨询报告会显示在这里" description="完成后可查看结果、计算复算、风险边界和原文依据。" />}</div>
    </div> : tab === 'skills' ? <div className="zx-consult-list" data-testid="consult-skills"><h2>哪些 Skill 可以用于咨询</h2><p className="zx-consult-muted">只会上架已通过的当前版本。同名 Skill 只能上架一个，知识或版本发生变化时会暂停。</p>
      {skills.length ? skills.map(skill => <div className="zx-consult-skill" data-testid="consult-skill-item" key={skill.id}><div><h3>{skill.name}</h3><p>{skill.goal}</p><p className="zx-consult-muted">{skill.trigger_description}</p><small>版本 {skill.version_number || '暂无'} · {skill.consult_status}</small>{skill.pause_reason && <p className="zx-consult-notice">{skill.pause_reason}</p>}</div><div className="zx-consult-skill-actions">{skill.published && <button className="btn-secondary" disabled={busy} onClick={() => act(() => api.unpublishConsultSkill(skill.id))}>下架</button>}{(!skill.published || !skill.available) && <button className="btn-primary" disabled={busy} onClick={() => act(() => api.publishConsultSkill(skill.id))}>{skill.published ? '重新上架' : '上架'}</button>}</div></div>) : <EmptyState icon={<Bot size={24} />} title="暂无已通过的 Skill" description="Skill 工厂完成审核后，会出现在这里。" />}
    </div> : <div className="zx-consult-handoffs" data-testid="consult-handoffs"><div className="zx-consult-handoff-list"><h2>转人工记录</h2>{handoffs.length ? handoffs.map(item => <button data-testid="consult-handoff-item" className={`zx-consult-history-item ${handoffId === item.id ? 'active' : ''}`} key={item.id} onClick={() => { setHandoffId(item.id); setNote(''); }}><span>{item.question || '咨询记录'}</span><small>{statusText(item.status)} · {item.source === 'system' ? '系统发现需确认事项' : '主动申请'}</small></button>) : <p className="zx-consult-muted">暂无转人工记录。</p>}</div><div className="zx-consult-handoff-detail">{handoff ? <><h2>{handoff.question || '人工确认事项'}</h2><p>{statusText(handoff.status)}</p><h3>转人工原因</h3><ul>{handoff.reasons.map((item, i) => <li key={i}>{valueText(item)}</li>)}</ul><button className="btn-secondary" onClick={() => { setTab('consult'); void select(handoff.session_id); }}>查看本次咨询</button><details><summary>咨询摘要</summary><p>{valueText(handoff.snapshot?.report ? (handoff.snapshot.report as any).summary : handoff.snapshot)}</p></details>{handoff.status === 'open' ? <><label className="zx-consult-field"><span>处理说明</span><textarea aria-label="处理说明" rows={5} value={note} onChange={e => setNote(e.target.value)} /></label><button className="btn-primary" disabled={busy || !note.trim()} onClick={() => act(() => api.resolveConsultHandoff(handoff.id, note))}>标记已处理</button></> : <><h3>处理说明</h3><p>{handoff.note || '已处理'}</p><small>{handoff.resolved_at}</small></>}</> : <EmptyState icon={<Bot size={24} />} title="选择一条记录查看" description="可查看原因和咨询摘要，处理后留下说明。" />}</div></div>}
  </div>;
};
