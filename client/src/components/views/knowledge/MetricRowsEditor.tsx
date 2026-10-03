import React from 'react';
import type { MetricDefinition, MetricRow } from '../../../types';
import { suggestMetricRowSplit } from './metricRowSplit';

type Props = { value: MetricDefinition | null; title: string; onChange: (value: MetricDefinition) => void };
const blank = (): MetricRow => ({ name: '', relation: '', value: '', unit: '', condition: '', period: '', linkage: '', note: '' });
const columns: Array<[keyof MetricRow, string]> = [
  ['name', '检查项'], ['relation', '比较关系'], ['value', '数值 / 范围'], ['unit', '单位'],
  ['condition', '适用条件'], ['period', '统计周期（可空）'], ['linkage', '与其他行的关联'], ['note', '定性要求'],
];
export const MetricRowsEditor: React.FC<Props> = ({ value, title, onChange }) => {
  const rows = value?.rows || [];
  const update = (next: MetricRow[]) => onChange({
    name: value?.name || title, unit: value?.unit || '', period: value?.period || '',
    criteria: value?.criteria || '', ...value, rows: next,
  });
  return <div style={{ background: 'white', padding: 12, border: '1px solid var(--border-color)',
    borderRadius: 'var(--radius-sm)', display: 'flex', flexDirection: 'column', gap: 8 }}>
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
      <strong style={{ fontSize: 12 }}>逐项核对指标 <span style={{ fontWeight: 400, color: 'var(--text-muted)' }}>时限、次数、比例请分别填写比较关系、数值和单位；纯定量要求的「定性要求」应留空。</span></strong>
      <button type="button" className="btn-secondary" style={{ fontSize: 12, padding: '4px 8px' }}
        onClick={() => update([...rows, blank()])}>＋ 新增检查项</button>
    </div>
    {rows.length === 0 && <div style={{ color: 'var(--text-muted)', fontSize: 12 }}>
      旧数据继续使用下方的原有字段；如需逐项核对，请主动新增检查项，不会自动拆分或覆盖旧数据。
    </div>}
    {rows.map((row, index) => <div key={index} style={{ padding: 8, border: '1px solid var(--border-color)',
      borderRadius: 'var(--radius-sm)', display: 'grid', gridTemplateColumns: 'repeat(2,minmax(0,1fr))', gap: 8 }}>
      <div style={{ gridColumn: '1 / -1', display: 'flex', justifyContent: 'space-between' }}>
        <strong style={{ fontSize: 12 }}>检查项 {index + 1}</strong>
        <button type="button" onClick={() => update(rows.filter((_, i) => i !== index))}
          style={{ border: 0, background: 'none', color: 'var(--danger-text)', cursor: 'pointer' }}>删除此行</button>
      </div>
      {columns.map(([key, label]) => <label key={key} style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
        {label}<input type="text" value={row[key] || ''} placeholder={label} onChange={event => {
          const copy = rows.map(r => ({ ...r }));
          copy[index][key] = event.target.value;
          update(copy);
        }} style={{ width: '100%', height: 29, padding: '4px 6px', boxSizing: 'border-box',
          border: '1px solid var(--border-color)', borderRadius: 'var(--radius-sm)' }} />
      </label>)}
      {suggestMetricRowSplit(row) ? (
        <div style={{ gridColumn: '1 / -1', padding: '8px 10px', borderRadius: 4,
          background: 'var(--warning-bg)', color: 'var(--warning-text)', fontSize: 12,
          display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
          <span>“定性要求”实际上是定量阈值，已能拆为比较关系、数值和单位；不会修改其他字段。</span>
          <button type="button" className="btn-secondary" style={{ flexShrink: 0, fontSize: 12 }}
            onClick={() => {
              const split = suggestMetricRowSplit(row);
              if (split) update(rows.map((r, i) => i === index ? split : r));
            }}>整理为定量要求</button>
        </div>
      ) : row.note && /\d+(?:\.\d+)?\s*(?:MPa|kPa|分钟|小时|秒|天|次|%|℃|毫米|厘米|米|元)/i.test(row.note) ? (
        <div style={{ gridColumn: '1 / -1', color: 'var(--warning-text)', fontSize: 12 }}>
          定性要求中包含数字；可能同时包含其他条件，或与上方阈值不一致，请对照原文手动拆分，不自动覆盖。
        </div>
      ) : null}
    </div>)}
    {rows.length > 0 && value?.criteria && <span style={{ fontSize: 12, color: 'var(--warning-text)' }}>
      原有达标基准仍保存在兼容字段中；请确认上方检查项完整，旧文本不会在此自动删改。
    </span>}
  </div>;
};

