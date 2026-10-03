import React from 'react';
import type { MetricDefinition, CaseDetails, PrimaryCategory } from '../../../types';
import { suggestMetricRowSplit } from './metricRowSplit';

type Content = {
  statement: string; primary_category: PrimaryCategory | null;
  subject: string; conditions: string[]; actions: string[]; exceptions: string[];
  metric_definition: MetricDefinition | null; case_details: CaseDetails | null;
};
type Props = {
  item: Content; hideStatement?: boolean;
  onLocate?: (field: string, index?: number) => void;
  onEdit?: (field: string, index?: number) => void;
  sourcePrecision?: 'field' | 'paragraph';
};

// 旧候选和模型响应可能把案例措施返回为字符串数组。展示和编辑时保留每一步，
// 避免将数组交给字符串方法或 textarea 导致核对界面崩溃。
const readableText = (value: unknown): string => {
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) return value.map(readableText).filter(Boolean).join('；');
  return value == null ? '' : String(value);
};

export const normalizeCaseDetails = (value: CaseDetails | null): CaseDetails | null => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  return {
    background: readableText(value.background),
    actions: readableText(value.actions),
    results: readableText(value.results),
    limitations: readableText(value.limitations),
  };
};

export const StructuredReview: React.FC<Props> = ({ item, hideStatement, onLocate, onEdit, sourcePrecision = 'paragraph' }) => {
  const field = (name: string, label: string, rawValue: unknown, index?: number) => {
    const value = readableText(rawValue).trim();
    if (!value) return null;
    if (hideStatement && name !== 'statement' && value === item.statement?.trim() && value.length > 25) {
      return <div key={name + index} style={{ fontSize: 12, color: 'var(--warning-text)' }}>「{label}」与核心陈述整段重复，请修改后核对。</div>;
    }
    return <div key={name + index} className="zx-hover-row" style={{ display: 'flex', gap: 12, alignItems: 'flex-start', lineHeight: 1.65,
      padding: '9px 8px', margin: '0 -8px', borderRadius: 'var(--radius-sm)', borderBottom: '1px solid var(--bg-sunken)' }}>
      <span style={{ flexShrink: 0, width: 84, fontSize: 13, color: 'var(--text-muted)' }}>{label}</span>
      <button type="button" onClick={() => onLocate?.(name, index)} title={onLocate ? '点击定位关联原文' : undefined}
        style={{ flex: 1, minWidth: 0, whiteSpace: 'pre-wrap', textAlign: 'left', padding: 0,
          border: 0, background: 'none', cursor: onLocate ? 'pointer' : 'default',
          color: 'var(--text-primary)' }}>{value}</button>
      {onLocate && <button type="button" onClick={() => onLocate(name, index)}
        title={sourcePrecision === 'field' ? '定位已有字段证据；如仅有段落证据则定位段落' : '仅可定位关联段落，无法精确定位字段'}
        className="zx-hover-reveal"
        style={{ border: 0, background: 'none', color: 'var(--brand-accent)', cursor: 'pointer', flexShrink: 0, fontSize: 12 }}>原文↗</button>}
      {onEdit && <button type="button" onClick={() => onEdit(name, index)} className="zx-hover-reveal"
        style={{ border: 0, background: 'none', color: 'var(--text-secondary)', cursor: 'pointer', flexShrink: 0, fontSize: 12 }}>修改</button>}
    </div>;
  };
  const list = (key: 'conditions' | 'actions' | 'exceptions', label: string, ordered = false) =>
    (item[key] || []).filter(Boolean).map((value, idx) =>
      field(key, ordered ? `${label} ${idx + 1}` : label, value, idx));
  const metric = item.metric_definition;
  const rows = metric?.rows || [];
  return <div data-testid="structured-review" style={{ fontSize: 13 }}>
    {!hideStatement && field('statement', item.primary_category === '专家经验' ? '判断依据' : '主要要求', item.statement)}
    {item.primary_category === '制度与标准' && <>
      {field('subject', '责任主体', item.subject === '原文未明确责任主体' || item.subject === '物业责任主体' ? '' : item.subject)}
      {list('conditions', '适用条件')}
      {list('actions', '执行要求', true)}
      {list('exceptions', '禁止/例外')}
    </>}
    {item.primary_category === '方法与工具' && <>
      {field('subject', '责任主体', item.subject === '原文未明确责任主体' || item.subject === '物业责任主体' ? '' : item.subject)}
      {list('conditions', '执行前提')}
      {list('actions', '步骤', true)}
      {list('exceptions', '停止/禁止')}
    </>}
    {item.primary_category === '指标数据' && <>
      {rows.length > 0 ? <div style={{ overflowX: 'auto' }}><table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
        <thead><tr>{['检查项', '要求', '条件 / 关联及口径', '原文'].map(t => <th key={t} style={{ padding: '8px 6px', borderBottom: '1px solid var(--border-color)', textAlign: 'left', color: 'var(--text-muted)', fontSize: 12, fontWeight: 500 }}>{t}</th>)}</tr></thead>
        <tbody>{rows.map((r, idx) => <tr key={idx} onClick={() => onLocate?.('metric_definition', idx)}
          title={onLocate ? '点击指标行定位关联原文' : undefined} style={{ cursor: onLocate ? 'pointer' : 'default', borderBottom: '1px solid var(--bg-sunken)' }}>
          <td style={{ padding: '9px 6px', verticalAlign: 'top' }}>{r.name || '待明确'}</td>
          <td style={{ padding: '9px 6px', verticalAlign: 'top' }}>
            {[r.relation, r.value, r.unit,
              r.value && r.unit && r.relation && suggestMetricRowSplit(r) ? '' : r.note]
              .filter(Boolean).join(' ') || '待明确'}
            {suggestMetricRowSplit(r) && <div style={{ color: 'var(--warning-text)', fontSize: 12 }}>
              定性栏包含重复的定量阈值，编辑时可整理。
            </div>}
          </td>
          <td style={{ padding: '9px 6px', verticalAlign: 'top' }}>{[r.condition, r.linkage, r.period && `统计周期：${r.period}`].filter(Boolean).join('；') || '—'}</td>
          <td style={{ padding: '9px 6px', verticalAlign: 'top' }}>
            {onLocate && <button onClick={(event) => { event.stopPropagation(); onLocate('metric_definition', idx); }} type="button" style={{ border: 0, background: 'none', color: 'var(--brand-accent)', cursor: 'pointer' }}>定位↗</button>}
            {onEdit && <button onClick={(event) => { event.stopPropagation(); onEdit('metric_definition', idx); }} type="button" style={{ border: 0, background: 'none', cursor: 'pointer' }}>修改</button>}
          </td></tr>)}</tbody></table></div> : <>
        {field('metric_definition', '检查项', metric?.name || '')}
        {field('metric_definition', '达标要求', [metric?.criteria, metric?.unit].filter(Boolean).join(' '))}
        {field('metric_definition', '统计周期', metric?.period || '')}
        {metric?.criteria && metric.criteria.length > 35 && <div style={{ color: 'var(--warning-text)' }}>旧指标可能仍为整段描述，请逐项核对；不会自动修改旧数据。</div>}
      </>}
      {list('conditions', '适用条件')}
      {list('exceptions', '禁止/例外')}
    </>}
    {item.primary_category === '项目案例' && <>
      {field('case_details', '背景', item.case_details?.background || '')}
      {field('case_details', '措施', readableText(item.case_details?.actions) || item.actions.join('；'))}
      {field('case_details', '结果', item.case_details?.results || '')}
      {field('case_details', '适用限制', item.case_details?.limitations || '')}
    </>}
    {item.primary_category === '专家经验' && <>
      {list('conditions', '适用情境')}
      {list('actions', '建议', true)}
      {list('exceptions', '不适用情形')}
    </>}
    {!item.primary_category && list('actions', '执行事项', true)}
  </div>;
};
