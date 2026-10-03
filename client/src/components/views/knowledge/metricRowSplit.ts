import type { MetricRow } from '../../../types';

// 只匹配整个“定性要求”就是一个明确的单项数值阈值；混合描述与区间须人工检查。
const quantitativeNote = /^\s*(?:必须|应当|应|须|需|严格)?\s*(不得超过|不可超过|不超过|不高于|不大于|至多|最多|小于等于|不低于|不少于|不小于|至少|大于等于|小于|低于|大于|高于|等于|≤|≦|≥|≧|<=|>=|<|>|=)?\s*(\d+(?:\.\d+)?)\s*(MPa|kPa|分钟|小时|秒|天|次|%|℃|毫米|厘米|米|元)\s*(以内|以下|以上|之内)?\s*[。；！!]?\s*$/i;
const under = ['不得超过', '不可超过', '不超过', '不高于', '不大于', '至多', '最多', '小于等于', '≤', '≦', '<='];
const over = ['不低于', '不少于', '不小于', '至少', '大于等于', '≥', '≧', '>='];
const canonical = (relation: string): string => {
  if (under.includes(relation)) return '≤';
  if (over.includes(relation)) return '≥';
  if (['小于', '低于', '<'].includes(relation)) return '<';
  if (['大于', '高于', '>'].includes(relation)) return '>';
  if (['等于', '='].includes(relation)) return '=';
  return '';
};

export const suggestMetricRowSplit = (row: MetricRow): MetricRow | null => {
  const match = quantitativeNote.exec(row.note || '');
  if (!match) return null;
  const before = canonical(match[1] || '');
  const after = ['以内', '以下', '之内'].includes(match[4] || '')
    ? '≤' : match[4] === '以上' ? '≥' : '';
  if (before && after && before !== after) return null;
  const relation = before || after;
  if (!relation || (row.relation && canonical(row.relation) !== relation)) return null;
  if (row.value && Number(row.value) !== Number(match[2])) return null;
  if (row.unit && row.unit.toLowerCase() !== match[3].toLowerCase()) return null;
  return {
    ...row,
    relation: row.relation || relation,
    value: row.value || match[2],
    unit: row.unit || match[3],
    note: '',
  };
};
