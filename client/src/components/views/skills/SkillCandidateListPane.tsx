import React, { useCallback, useEffect, useState } from 'react';
import { Loader2, RotateCcw } from 'lucide-react';
import { api } from '../../../services/api';
import { SkillExperience, SkillListItem, SkillListResponse } from '../../../types';
import { SegmentedTabs } from '../../ui/SegmentedTabs';
import { ErrorNotice, errorMessage } from './sceneShared';
import { STATUS_TONE, formatTime, plainText, tableCellStyle as cell, tableHeadStyle } from './skillReviewShared';
import { usePolling } from './usePolling';

interface Props {
  onOpenSkill: (skillId: string, queue: string[]) => void;
}

const STATUS_OPTIONS = [
  { value: '', label: '全部状态' },
  { value: 'pending_review', label: '待审核' },
  { value: 'needs_recheck', label: '待复核' },
  { value: 'generating', label: '生成中' },
  { value: 'validation_failed', label: '校验未通过' },
  { value: 'approved', label: '已通过（待测试）' },
  { value: 'rejected', label: '已驳回' },
];

const head: React.CSSProperties = {
  ...tableHeadStyle,
  whiteSpace: 'nowrap',
};
const selectStyle: React.CSSProperties = {
  height: '32px',
  padding: '0 8px',
  fontSize: 'var(--font-size-sm)',
  border: '1px solid var(--border-color)',
  borderRadius: 'var(--radius-sm)',
  backgroundColor: 'var(--bg-primary)',
  color: 'var(--text-primary)',
  maxWidth: '220px',
};

type View = 'candidates' | 'experiences';

/** M02-D 候选列表（FR09）与待沉淀经验清单（S13） */
export const SkillCandidateListPane: React.FC<Props> = ({ onOpenSkill }) => {
  const [view, setView] = useState<View>('candidates');
  const [data, setData] = useState<SkillListResponse | null>(null);
  const [experiences, setExperiences] = useState<SkillExperience[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [sceneId, setSceneId] = useState('');
  const [status, setStatus] = useState('');
  const [batchId, setBatchId] = useState('');
  const [unsupported, setUnsupported] = useState<'' | 'yes' | 'no'>('');
  const [recheck, setRecheck] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      if (view === 'candidates') {
        setData(await api.listSkills({ scene_id: sceneId, status, batch_id: batchId, has_unsupported: unsupported, needs_recheck: recheck }));
      } else {
        setExperiences((await api.listSkillExperiences()).items);
      }
      setError(null);
    } catch (err) {
      setError(errorMessage(err, '列表加载失败，请稍后再试'));
    } finally {
      setLoading(false);
    }
  }, [view, sceneId, status, batchId, unsupported, recheck]);

  useEffect(() => {
    load();
  }, [load]);

  // 有生成中的候选时定时刷新
  const hasGenerating = !!data?.items.some((i) => i.status === 'generating');
  usePolling(hasGenerating && view === 'candidates', load);

  const items = data?.items || [];
  const queue = items.map((i) => i.skill_id);
  const pendingCount = data?.status_counts?.pending_review || 0;

  return (
    <div style={{ padding: '20px 32px 48px', flex: 1, overflow: 'auto' }} data-testid="skill-candidate-list">
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '16px' }}>
        <SegmentedTabs<View>
          value={view}
          onChange={setView}
          options={[
            { key: 'candidates', label: '候选', count: pendingCount || undefined, testId: 'list-view-candidates', title: '数字为待审核数' },
            { key: 'experiences', label: '待沉淀经验', testId: 'list-view-experiences' },
          ]}
        />
        <div style={{ flex: 1 }} />
        <button type="button" className="btn-ghost btn-sm" onClick={load} title="刷新">
          {loading ? <Loader2 size={14} className="spin-slow" /> : <RotateCcw size={14} />}刷新
        </button>
      </div>
      {error && <div style={{ marginBottom: '12px' }}><ErrorNotice message={error} /></div>}

      {view === 'candidates' ? (
        <>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', alignItems: 'center', marginBottom: '12px' }} data-testid="list-filters">
            <select style={selectStyle} value={sceneId} onChange={(e) => setSceneId(e.target.value)} data-testid="filter-scene" aria-label="场景">
              <option value="">全部场景</option>
              {(data?.filters.scenes || []).map(([id, name]) => <option key={id} value={id}>{name || '（场景已删除）'}</option>)}
            </select>
            <select style={selectStyle} value={status} onChange={(e) => setStatus(e.target.value)} data-testid="filter-status" aria-label="状态">
              {STATUS_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
            <select style={selectStyle} value={batchId} onChange={(e) => setBatchId(e.target.value)} data-testid="filter-batch" aria-label="生成记录">
              <option value="">全部生成记录</option>
              {(data?.filters.batches || []).map((b) => (
                <option key={b.batch_id} value={b.batch_id}>{`${b.scene_name || ''} ${formatTime(b.initiated_at)}`.trim()}</option>
              ))}
            </select>
            <select style={selectStyle} value={unsupported} onChange={(e) => setUnsupported(e.target.value as '' | 'yes' | 'no')} data-testid="filter-unsupported" aria-label="无依据项">
              <option value="">有无依据项：不限</option>
              <option value="yes">有无依据项</option>
              <option value="no">没有无依据项</option>
            </select>
            <label style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', fontSize: 'var(--font-size-sm)', color: 'var(--text-secondary)' }}>
              <input type="checkbox" checked={recheck} onChange={(e) => setRecheck(e.target.checked)} data-testid="filter-recheck" />只看待复核
            </label>
            <span style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)' }} data-testid="list-total">共 {data?.total ?? 0} 条</span>
          </div>
          <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)', marginBottom: '8px' }}>
            排序：待复核在前，待审核按生成把握度从低到高，最需要人看的排在前面。
          </div>
          {items.length === 0 ? (
            <div className="zx-card" style={{ padding: '24px', fontSize: 'var(--font-size-sm)', color: 'var(--text-muted)' }}>
              {loading ? '正在加载…' : '没有符合条件的候选。'}
            </div>
          ) : (
            <div className="zx-card" style={{ padding: 0, overflow: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: '980px' }} data-testid="skill-list-table">
                <thead>
                  <tr>
                    <th style={head}>名称</th>
                    <th style={head}>场景</th>
                    <th style={head}>状态</th>
                    <th style={head}>生成把握度</th>
                    <th style={head}>无依据项</th>
                    <th style={head}>引用知识</th>
                    <th style={head}>当前版本</th>
                    <th style={head}>生成记录</th>
                    <th style={head}>更新时间</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((item) => (
                    <ListRow key={item.skill_id} item={item} onOpen={() => onOpenSkill(item.skill_id, queue)} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      ) : (
        <ExperienceList items={experiences} loading={loading} onOpenSkill={(id) => onOpenSkill(id, [])} />
      )}
    </div>
  );
};

const ListRow: React.FC<{ item: SkillListItem; onOpen: () => void }> = ({ item, onOpen }) => (
  <tr
    data-testid="skill-list-row"
    data-skill-id={item.skill_id}
    data-status={item.status}
    onClick={onOpen}
    className="zx-hover-row"
    style={{ cursor: 'pointer' }}
  >
    <td style={cell}>
      <div style={{ fontWeight: 500, color: 'var(--text-primary)' }}>{item.name}</div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px', marginTop: '4px' }}>
        {item.similar_skills.length > 0 && (
          <span className="zx-tag warning" title={item.similar_skills.map((s) => s.name).join('、')} data-testid="mark-similar">与已有 Skill 相似</span>
        )}
        {item.atom_changed && <span className="zx-tag danger" data-testid="mark-atom-changed">知识已变更</span>}
        {item.has_draft && <span className="zx-tag neutral">有未提交的修改</span>}
        {item.regenerate_count > 0 && <span className="zx-tag neutral">已重生成 {item.regenerate_count} 次</span>}
      </div>
      {item.status === 'needs_recheck' && item.stale_reason && (
        <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--warning-text)', marginTop: '4px' }} data-testid="stale-reason">
          需要复核：{item.stale_reason}
        </div>
      )}
      {item.status === 'validation_failed' && item.error_summary.length > 0 && (
        <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--danger-text)', marginTop: '4px' }}>{item.error_summary.join('；')}</div>
      )}
    </td>
    <td style={cell}>{item.scene_name || '—'}</td>
    <td style={cell}><span className={`zx-tag ${STATUS_TONE[item.status] ?? 'neutral'}`}>{item.status_label}</span></td>
    <td style={cell}>{item.confidence_level || '—'}</td>
    <td style={cell}>{item.unsupported_count ?? '—'}</td>
    <td style={cell}>{item.ref_count}</td>
    <td style={cell}>{item.version_number ? `第 ${item.version_number} 版` : '—'}</td>
    <td style={cell}>{item.batch_initiated_at ? formatTime(item.batch_initiated_at) : '—'}</td>
    <td style={cell}>{formatTime(item.updated_at)}</td>
  </tr>
);

const ExperienceList: React.FC<{ items: SkillExperience[] | null; loading: boolean; onOpenSkill: (id: string) => void }> = ({ items, loading, onOpenSkill }) => (
  <div data-testid="experience-list">
    <div style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-secondary)', marginBottom: '12px', lineHeight: 1.6 }}>
      审核中标为「专家补充」的内容会记在这里，方便以后整理成正式知识。目前只做记录。
    </div>
    {!items || items.length === 0 ? (
      <div className="zx-card" style={{ padding: '24px', fontSize: 'var(--font-size-sm)', color: 'var(--text-muted)' }}>
        {loading ? '正在加载…' : '还没有专家补充的内容。'}
      </div>
    ) : (
      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
        {items.map((e) => (
          <div key={e.experience_id} className="zx-card" style={{ padding: '12px 16px' }} data-testid="experience-row">
            <div style={{ display: 'flex', gap: '8px', alignItems: 'center', marginBottom: '6px' }}>
              <span className="zx-tag neutral">{plainText(e.source_kind)}</span>
              <button type="button" className="btn-ghost btn-sm" onClick={() => onOpenSkill(e.skill_id)} style={{ padding: 0, height: 'auto' }}>
                {e.skill_name || e.skill_id}{e.version_number ? ` · 第 ${e.version_number} 版` : ''}
              </button>
              <span style={{ flex: 1 }} />
              <span style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)' }}>{e.created_by_name} · {formatTime(e.created_at)}</span>
            </div>
            <div style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-primary)', lineHeight: 1.6 }}>{plainText(e.content)}</div>
            <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-secondary)', marginTop: '4px' }}>理由：{e.reason}</div>
          </div>
        ))}
      </div>
    )}
  </div>
);
