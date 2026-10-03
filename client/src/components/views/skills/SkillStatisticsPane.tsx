import React, { useEffect, useState } from 'react';
import { Loader2, RotateCcw } from 'lucide-react';
import { api } from '../../../services/api';
import { SkillStatisticMetrics, SkillStatisticRate, SkillStatistics } from '../../../types';
import { ErrorNotice, errorMessage } from './sceneShared';

const rate = (r: SkillStatisticRate) => r.value === null ? '暂无数据' : `${(r.value * 100).toFixed(1)}%（${r.numerator}/${r.denominator}）`;
const average = (m: SkillStatisticMetrics) => m.average_modified_fields === null ? '暂无数据' : `${m.average_modified_fields.toFixed(1)}（${m.modified_field_total}/${m.manual_review_record_count} 次）`;
const distribution = (values: Record<string, number>, labels: Record<string, string> = {}) =>
  Object.entries(values).map(([key, n]) => `${labels[key] || key} ${n}`).join('；') || '暂无记录';

/** FR13：候选数、校验与审核口径分开，分布展示实际事件次数。 */
export const SkillStatisticsPane: React.FC = () => {
  const [data, setData] = useState<SkillStatistics | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [scene, setScene] = useState('');
  const [group, setGroup] = useState<'scene' | 'batch'>('scene');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let active = true;
    setLoading(true); setError(null);
    api.getSkillStatistics().then((value) => { if (active) setData(value); })
      .catch((err) => { if (active) setError(errorMessage(err, '统计加载失败，请重试')); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [revision]);
  const rows = data ? group === 'scene'
    ? data.by_scene.filter((r) => !scene || r.scene_id === scene).map((r) => ({ ...r, id: r.scene_id, label: r.name }))
    : data.by_batch.filter((r) => !scene || r.scene_id === scene).map((r) => ({ ...r, id: r.batch_id, label: `${r.scene_name} · ${new Date(r.initiated_at).toLocaleString()} · ${r.batch_id.slice(-6)}` })) : [];
  const summary = scene ? data?.by_scene.find((s) => s.scene_id === scene) : data?.summary;
  return <div data-testid="skill-statistics" style={{ padding: '24px 32px', fontSize: 14, color: 'var(--text-primary)', minWidth: 0 }}>
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginBottom: 16 }}>
      <h2 style={{ fontSize: 16, margin: 0, flex: 1 }}>生成与审核统计</h2>
      <label>场景 <select aria-label="统计场景" value={scene} onChange={(e) => setScene(e.target.value)} style={{ fontSize: 14 }}>
        <option value="">全部场景</option>{data?.by_scene.map((s) => <option key={s.scene_id} value={s.scene_id}>{s.name}</option>)}
      </select></label>
      <label>汇总方式 <select aria-label="统计汇总方式" value={group} onChange={(e) => setGroup(e.target.value as 'scene' | 'batch')} style={{ fontSize: 14 }}>
        <option value="scene">按场景</option><option value="batch">按批次</option>
      </select></label>
      <button className="btn-secondary" onClick={() => setRevision((n) => n + 1)} disabled={loading}><RotateCcw size={14} />刷新</button>
    </div>
    {loading ? <p><Loader2 size={14} className="spin-slow" /> 正在加载统计…</p> : error ? <ErrorNotice message={error} /> : data && summary && <>
      <p data-testid="statistics-summary" className="zx-stat-strip">
        <span className="zx-stat-chip">候选 <strong>{summary.candidate_count}</strong> 个</span>
        <span className="zx-stat-chip">校验通过率 <strong>{rate(summary.validation_pass_rate)}</strong></span>
        <span className="zx-stat-chip">审核通过率 <strong>{rate(summary.review_pass_rate)}</strong></span>
        <span className="zx-stat-chip">平均修改字段 <strong>{average(summary)}</strong></span>
      </p>
      <p style={{ color: 'var(--text-secondary)', fontSize: 12, margin: '0 0 14px' }}>当前状态：{distribution(summary.status_counts, data.status_labels)}。尚无最终校验记录 {summary.validation_unrecorded_count} 个，尚无有效审核决定 {summary.review_pending_count} 个。</p>
      <div style={{ overflowX: 'auto', borderRadius: 'var(--radius-md)', background: 'var(--bg-primary)', boxShadow: 'var(--shadow-md)' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 880, fontSize: 14 }}>
          <thead><tr>{['场景 / 批次', '候选数', '校验通过率', '审核通过率', '平均修改字段', '驳回原因（次）', '无依据项处理（次）'].map((h) => <th key={h} style={{ padding: 12, textAlign: 'left', fontWeight: 600, fontSize: 13, color: 'var(--text-secondary)', background: 'var(--bg-secondary)', borderBottom: '1px solid var(--border-color)' }}>{h}</th>)}</tr></thead>
          <tbody>{rows.map((r) => <tr key={r.id} data-testid="statistics-row">
            {[r.label, r.candidate_count, rate(r.validation_pass_rate), rate(r.review_pass_rate), average(r), distribution(r.rejection_reasons), distribution(r.unsupported_resolutions, data.resolution_labels)].map((v, i) => <td key={i} style={{ padding: 12, verticalAlign: 'top', borderBottom: '1px solid var(--border-color)', overflowWrap: 'anywhere' }}>{v}</td>)}
          </tr>)}</tbody>
        </table>
        {!rows.length && <p style={{ padding: 16 }}>还没有生成记录。统计会在实际生成与审核后更新。</p>}
      </div>
      <details style={{ marginTop: 20 }}><summary style={{ cursor: 'pointer' }}>统计口径</summary>
        {Object.entries(data.definitions).map(([key, text]) => <p key={key} style={{ fontSize: 12, lineHeight: 1.7 }}>{text}</p>)}
        <p style={{ fontSize: 12 }}>比例只代表结构校验和审核记录，不代表业务准确率。没有分母时显示「暂无数据」。</p>
      </details>
    </>}
  </div>;
};
