import React, { useCallback, useEffect, useState } from 'react';
import { ArrowLeft, Check, Circle, Loader2, Minus, RotateCcw, X } from 'lucide-react';
import { api } from '../../../services/api';
import {
  SkillBatchDetail,
  SkillBatchStage,
  SkillCandidateDetail,
  SkillCandidateSummary,
  SkillSplitTask,
} from '../../../types';
import { CATEGORY_SHORT, ErrorNotice, errorMessage } from './sceneShared';
import { formatTime, ModalShell, tableCellStyle as cellStyle, tableHeadStyle as headCellStyle } from './skillReviewShared';
import { usePolling } from './usePolling';

interface BatchDetailPaneProps {
  batchId: string;
  onBack: () => void;
  /** M02-D：打开审核工作台 */
  onOpenSkill: (skillId: string, queue: string[]) => void;
}

// 模型写的说明文字面向业务人员展示：统一用「知识」称呼（界面文案原则，见 M02-B 交接说明第 12 节）
const plainText = (text: string | null | undefined): string => (text || '').replace(/原子/g, '知识');

const BATCH_TONE: Record<string, string> = {
  running: 'neutral',
  completed: '',
  partial: 'warning',
  failed: 'danger',
  no_tasks: 'warning',
};

const CANDIDATE_TONE: Record<string, string> = {
  pending_review: '',
  validation_failed: 'danger',
  generating: 'neutral',
  approved: '',
  rejected: 'neutral',
  needs_recheck: 'warning',
};

const StageIcon: React.FC<{ status: SkillBatchStage['status'] }> = ({ status }) => {
  const base: React.CSSProperties = {
    width: '24px',
    height: '24px',
    borderRadius: '50%',
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    flexShrink: 0,
  };
  if (status === 'done')
    return <span style={{ ...base, backgroundColor: 'var(--brand-600)', color: '#fff' }}><Check size={14} /></span>;
  if (status === 'running')
    return <span style={{ ...base, backgroundColor: 'var(--brand-50)', color: 'var(--brand-600)' }}><Loader2 size={14} className="spin-slow" /></span>;
  if (status === 'failed')
    return <span style={{ ...base, backgroundColor: 'var(--danger-bg)', color: 'var(--danger-text)' }}><X size={14} /></span>;
  if (status === 'skipped')
    return <span style={{ ...base, backgroundColor: 'var(--bg-sunken)', color: 'var(--text-muted)' }}><Minus size={14} /></span>;
  return <span style={{ ...base, backgroundColor: 'var(--bg-sunken)', color: 'var(--text-muted)' }}><Circle size={10} /></span>;
};

const STAGE_STATUS_TEXT: Record<SkillBatchStage['status'], string> = {
  waiting: '等待中',
  running: '进行中',
  done: '已完成',
  failed: '未完成',
  skipped: '未进行',
};

const sectionTitle: React.CSSProperties = {
  fontSize: 'var(--font-size-section)',
  fontWeight: 600,
  color: 'var(--text-primary)',
  margin: '0 0 12px',
};

/** M02-C 生成记录详情：四段进度、本次使用的知识、拆分结果、候选列表（最小可查看版本） */
export const BatchDetailPane: React.FC<BatchDetailPaneProps> = ({ batchId, onBack, onOpenSkill }) => {
  const [data, setData] = useState<SkillBatchDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [viewSkillId, setViewSkillId] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await api.getSkillBatch(batchId));
      setError(null);
    } catch (err) {
      setError(errorMessage(err, '生成记录加载失败，请稍后再试'));
    }
  }, [batchId]);

  useEffect(() => {
    load();
  }, [load]);

  const running = data?.status === 'running';
  usePolling(running, load);

  if (!data) {
    return (
      <div style={{ padding: '24px 32px', display: 'flex', flexDirection: 'column', gap: '12px', maxWidth: '560px' }}>
        {error ? (
          <>
            <ErrorNotice message={error} />
            <div style={{ display: 'flex', gap: '8px' }}>
              <button type="button" className="btn-secondary" onClick={onBack}><ArrowLeft size={14} />返回</button>
              <button type="button" className="btn-secondary" onClick={load}><RotateCcw size={14} />重试</button>
            </div>
          </>
        ) : (
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: 'var(--text-muted)', fontSize: 'var(--font-size-sm)' }}>
            <Loader2 size={14} className="spin-slow" />正在加载生成记录…
          </div>
        )}
      </div>
    );
  }

  const split = data.task_split;
  const poolAtoms = data.atom_pool?.atoms || [];
  const byTag = poolAtoms.filter((a) => a.recall_source === 'tag').length;

  return (
    <div style={{ padding: '20px 32px 48px', overflow: 'auto', flex: 1 }} data-testid="batch-detail">
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '16px' }}>
        <button type="button" className="btn-ghost btn-sm" onClick={onBack} data-testid="batch-back">
          <ArrowLeft size={14} />返回选择场景
        </button>
      </div>

      <div style={{ display: 'flex', alignItems: 'flex-start', gap: '16px', marginBottom: '20px' }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
            <h2 style={{ fontSize: 'var(--font-size-title)', fontWeight: 600, margin: 0, color: 'var(--text-primary)' }}>
              {data.scene_name || '已删除的场景'} · 生成记录
            </h2>
            <span className={`zx-tag ${BATCH_TONE[data.status] ?? 'neutral'}`} data-testid="batch-status">
              {running && <Loader2 size={12} className="spin-slow" />}
              {data.status_label}
            </span>
          </div>
          <div style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-secondary)', marginTop: '6px', lineHeight: 1.6 }}>
            {formatTime(data.initiated_at)} 发起
            {data.focus_note ? ` · 侧重：${data.focus_note}` : ''}
            {data.status_reason ? ` · ${plainText(data.status_reason)}` : ''}
          </div>
        </div>
        <button type="button" className="btn-ghost btn-sm" onClick={load} title="刷新">
          <RotateCcw size={13} />刷新
        </button>
      </div>

      {error && <div style={{ marginBottom: '12px' }}><ErrorNotice message={error} onClose={() => setError(null)} /></div>}

      {/* 四段进度：查找知识 → 拆分任务 → 生成候选 → 检查入库 */}
      <div
        className="zx-card"
        data-testid="batch-stages"
        style={{ padding: '16px 20px', display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: '12px', marginBottom: '24px' }}
      >
        {data.stages.map((stage, idx) => (
          <div key={stage.key} data-testid={`stage-${stage.key}`} data-status={stage.status} style={{ display: 'flex', gap: '10px', alignItems: 'flex-start' }}>
            <StageIcon status={stage.status} />
            <div style={{ minWidth: 0 }}>
              <div style={{ fontSize: 'var(--font-size-sm)', fontWeight: 600, color: 'var(--text-primary)' }}>
                {idx + 1}. {stage.label}
              </div>
              <div style={{ fontSize: 'var(--font-size-xs)', color: stage.status === 'failed' ? 'var(--danger-text)' : 'var(--text-muted)', marginTop: '2px', lineHeight: 1.5 }}>
                {stage.detail || STAGE_STATUS_TEXT[stage.status]}
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* 候选 Skill（最小列表，完整审核在后续开放） */}
      <section style={{ marginBottom: '28px' }}>
        <h3 style={sectionTitle}>生成的 Skill 候选</h3>
        {data.candidates.length === 0 ? (
          <div style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-muted)' }}>
            {running ? '候选生成后会出现在这里。' : '这一批没有产生候选。'}
          </div>
        ) : (
          <div className="zx-card" style={{ padding: 0, overflow: 'hidden' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }} data-testid="candidate-table">
              <thead>
                <tr>
                  <th style={headCellStyle}>名称</th>
                  <th style={headCellStyle}>状态</th>
                  <th style={headCellStyle}>生成把握度</th>
                  <th style={headCellStyle}>无依据项</th>
                  <th style={headCellStyle}>引用知识</th>
                  <th style={{ ...headCellStyle, width: '184px' }} />
                </tr>
              </thead>
              <tbody>
                {data.candidates.map((c) => (
                  <CandidateRow key={c.skill_id} candidate={c} onView={() => setViewSkillId(c.skill_id)}
                    onReview={() => onOpenSkill(c.skill_id, data.candidates.map((x) => x.skill_id))} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* 拆分结果 */}
      <section style={{ marginBottom: '28px' }}>
        <h3 style={sectionTitle}>拆分出的任务</h3>
        {!split ? (
          <div style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-muted)' }}>{running ? '正在拆分…' : '没有完成拆分。'}</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
            {split.tasks.length === 0 && (
              <div style={{ fontSize: 'var(--font-size-sm)', color: 'var(--warning-text)' }} data-testid="no-task-reason">
                没有拆出可生成的任务：{plainText(split.no_task_reason)}
              </div>
            )}
            {split.tasks.map((task) => (
              <SplitTaskCard key={task.task_key} task={task} tasks={split.tasks} />
            ))}
            {split.unused_atoms.length > 0 && (
              <details className="zx-card" style={{ padding: '12px 16px' }} data-testid="unused-atoms">
                <summary style={{ cursor: 'pointer', fontSize: 'var(--font-size-sm)', fontWeight: 600, color: 'var(--text-primary)' }}>
                  没有用上的知识（{split.unused_atoms.length} 条）
                </summary>
                <div style={{ marginTop: '8px', display: 'flex', flexDirection: 'column', gap: '6px' }}>
                  {split.unused_atoms.map((u) => (
                    <div key={u.atom_version_id} style={{ fontSize: 'var(--font-size-xs)', lineHeight: 1.6 }}>
                      <span style={{ color: 'var(--text-primary)', fontWeight: 500 }}>{u.title}</span>
                      <span style={{ color: 'var(--text-muted)' }}> · {plainText(u.reason)}</span>
                    </div>
                  ))}
                </div>
              </details>
            )}
          </div>
        )}
      </section>

      {/* 本次使用的知识（原子池快照） */}
      <section>
        <h3 style={sectionTitle}>本次找到的知识</h3>
        {!data.atom_pool ? (
          <div style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-muted)' }}>{running ? '正在查找…' : '没有找到知识。'}</div>
        ) : (
          <>
            <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)', marginBottom: '8px' }} data-testid="pool-summary">
              共 {poolAtoms.length} 条：{byTag} 条带有这个场景的标签，{poolAtoms.length - byTag} 条内容相关，由系统补充找到。
            </div>
            {!!data.atom_pool.stats && data.atom_pool.stats.eligible > data.atom_pool.stats.returned && (
              <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--warning-text)', marginBottom: '8px', lineHeight: 1.6 }} data-testid="pool-truncated">
                这个场景可用的知识有 {data.atom_pool.stats.eligible} 条，一次最多使用 {data.atom_pool.stats.returned} 条，本次优先选了与场景最相关、最新确认的知识。发起时填写侧重说明，可以让系统优先选用相关的知识。
              </div>
            )}
            <div className="zx-card" style={{ padding: '4px 16px' }}>
              {poolAtoms.map((atom) => (
                <div
                  key={atom.atom_version_id}
                  data-testid="pool-atom"
                  style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '8px 0', borderBottom: '1px solid var(--border-color)' }}
                >
                  <span style={{ flex: 1, minWidth: 0, fontSize: 'var(--font-size-sm)', color: 'var(--text-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {atom.title}
                  </span>
                  {atom.primary_category && <span className="zx-tag neutral">{CATEGORY_SHORT[atom.primary_category] || atom.primary_category}</span>}
                  {atom.recall_source === 'semantic' ? (
                    <span className="zx-tag warning" title="这条知识没有这个场景的标签，是按内容相关补充找到的">内容相关</span>
                  ) : (
                    <span className="zx-tag" title="知识带有这个场景包含的标签">场景标签</span>
                  )}
                </div>
              ))}
            </div>
          </>
        )}
      </section>

      {viewSkillId && <CandidateJsonModal skillId={viewSkillId} onClose={() => setViewSkillId(null)} />}
    </div>
  );
};

const CandidateRow: React.FC<{ candidate: SkillCandidateSummary; onView: () => void; onReview: () => void }> = ({ candidate, onView, onReview }) => (
  <tr data-testid="candidate-row" data-status={candidate.status}>
    <td style={cellStyle}>
      <div style={{ fontWeight: 500, color: 'var(--text-primary)' }}>{candidate.name}</div>
      {candidate.similar_skills.length > 0 && (
        <span className="zx-tag warning" style={{ marginTop: '4px' }} title={candidate.similar_skills.map((s) => s.name).join('、')}>
          与已有 Skill 相似
        </span>
      )}
      {candidate.status === 'validation_failed' && candidate.error_summary.length > 0 && (
        <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--danger-text)', marginTop: '4px', lineHeight: 1.5 }}>
          {candidate.error_summary.join('；')}
        </div>
      )}
    </td>
    <td style={cellStyle}>
      <span className={`zx-tag ${CANDIDATE_TONE[candidate.status] ?? 'neutral'}`}>{candidate.status_label}</span>
    </td>
    <td style={cellStyle}>{candidate.confidence_level || '—'}</td>
    <td style={cellStyle}>{candidate.unsupported_count ?? '—'}</td>
    <td style={cellStyle}>{candidate.ref_count}</td>
    <td style={cellStyle}>
      <div style={{ display: 'flex', gap: '6px' }}>
        <button type="button" className="btn-secondary btn-sm" onClick={onView} data-testid="candidate-view">查看内容</button>
        <button type="button" className="btn-primary btn-sm" onClick={onReview} data-testid="candidate-review">去审核</button>
      </div>
    </td>
  </tr>
);

const SplitTaskCard: React.FC<{ task: SkillSplitTask; tasks: SkillSplitTask[] }> = ({ task, tasks }) => {
  const nameOf = (key: string) => tasks.find((t) => t.task_key === key)?.name || key;
  const resultLabel = task.status === 'skipped' ? '未进入生成' : task.result?.status_label || '等待生成';
  const tone =
    task.status === 'skipped' || task.result?.status === 'call_failed' || task.result?.status === 'validation_failed'
      ? 'warning'
      : task.result?.status === 'stored'
        ? ''
        : 'neutral';
  return (
    <div className="zx-card" data-testid="split-task" data-status={task.status} style={{ padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: '6px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
        <span style={{ fontSize: 'var(--font-size-base)', fontWeight: 600, color: 'var(--text-primary)' }}>{task.name}</span>
        <span className="zx-tag neutral">{task.task_type}</span>
        <span className={`zx-tag ${tone}`}>{resultLabel}</span>
        {task.result?.repair_count ? <span className="zx-tag neutral">自动修正 {task.result.repair_count} 次</span> : null}
        {task.possible_duplicate_of.length > 0 && (
          <span className="zx-tag warning">可能与「{task.possible_duplicate_of.map((d) => nameOf(d.task_key)).join('、')}」重复</span>
        )}
      </div>
      <div style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-secondary)', lineHeight: 1.6 }}>{task.goal}</div>
      {task.split_reason && (
        <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)', lineHeight: 1.6 }}>为什么这样拆：{plainText(task.split_reason)}</div>
      )}
      <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)', lineHeight: 1.6 }}>
        用到 {task.atoms.length} 条知识：{task.atoms.map((a) => a.title || '（已失效）').join('、')}
      </div>
      {task.skip_reason && <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--warning-text)' }}>{plainText(task.skip_reason)}</div>}
      {task.hints
        .filter((h) => !h.startsWith('已剔除'))
        .map((h) => (
          <div key={h} style={{ fontSize: 'var(--font-size-xs)', color: 'var(--warning-text)' }}>{plainText(h)}</div>
        ))}
    </div>
  );
};

const CandidateJsonModal: React.FC<{ skillId: string; onClose: () => void }> = ({ skillId, onClose }) => {
  const [data, setData] = useState<SkillCandidateDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .getSkillCandidate(skillId)
      .then((res) => alive && setData(res))
      .catch((err) => alive && setError(errorMessage(err, '内容加载失败，请稍后再试')));
    return () => {
      alive = false;
    };
  }, [skillId]);

  const hardIssues = data?.issues.filter((i) => i.level === 'hard_error') || [];

  return (
    <ModalShell title={data?.name || '候选内容'} label="候选完整内容" width={820} height={680} testId="candidate-json-modal"
      onClose={onClose} subtitle="只读查看。审核与修改在审核工作台中进行。"
      bodyStyle={{ padding: '12px 24px 20px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
      {error && <ErrorNotice message={error} />}
      {!data && !error && (
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: 'var(--text-muted)', fontSize: 'var(--font-size-sm)' }}>
          <Loader2 size={14} className="spin-slow" />正在加载…
        </div>
      )}
      {data && data.status === 'validation_failed' && (
        <div
          style={{ padding: '10px 12px', borderRadius: 'var(--radius-sm)', backgroundColor: 'var(--danger-bg)', border: '1px solid var(--danger-border)', fontSize: 'var(--font-size-sm)', color: 'var(--danger-text)', lineHeight: 1.6 }}
          data-testid="candidate-failed-issues"
        >
          已自动修正 {data.repair_count ?? 0} 次仍有 {hardIssues.length} 处不符合要求，没有进入审核：
          <ul style={{ margin: '4px 0 0', paddingLeft: '18px' }}>
            {Array.from(new Set(hardIssues.map((i) => i.plain))).map((plain) => (
              <li key={plain}>{plain}（{hardIssues.filter((i) => i.plain === plain).length} 处）</li>
            ))}
          </ul>
        </div>
      )}
      {data && (
        <pre
          data-testid="candidate-json"
          style={{
            margin: 0,
            flex: 1,
            minHeight: 0,
            overflow: 'auto',
            padding: '12px 14px',
            borderRadius: 'var(--radius-sm)',
            backgroundColor: 'var(--bg-secondary)',
            border: '1px solid var(--border-color)',
            fontSize: 'var(--font-size-xs)',
            lineHeight: 1.6,
            fontFamily: 'Consolas, "SFMono-Regular", Menlo, monospace',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-all',
          }}
        >
          {data.skill_json ? JSON.stringify(data.skill_json, null, 2) : '没有可显示的内容'}
        </pre>
      )}
    </ModalShell>
  );
};
