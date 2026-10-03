import React, { useState } from 'react';
import { ConsultFormField } from '../../../types';

export const ConsultForm: React.FC<{ fields: ConsultFormField[]; busy: boolean; onSubmit: (values: Record<string, Record<string, unknown>>) => Promise<void> }> = ({ fields, busy, onSubmit }) => {
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [error, setError] = useState('');
  const keyOf = (field: ConsultFormField) => `${field.skill_id}:${field.key}`;
  const change = (field: ConsultFormField, value: unknown) => setValues(current => ({ ...current, [keyOf(field)]: value }));
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError('');
    const payload: Record<string, Record<string, unknown>> = {};
    for (const field of fields) {
      const raw = values[keyOf(field)];
      if (raw === undefined || raw === '' || (field.type === 'period' && (!Array.isArray(raw) || raw.some(v => !v)))) {
        if (field.required !== false) { setError(`请填写${field.label}`); return; }
        continue;
      }
      const value = field.type === 'number' ? Number(raw) : field.type === 'period' && Array.isArray(raw) ? { start: raw[0], end: raw[1] } : raw;
      if (field.type === 'number' && !Number.isFinite(value)) { setError(`${field.label}请填写有效数字`); return; }
      payload[field.skill_id] = { ...payload[field.skill_id], [field.key]: value };
    }
    try { await onSubmit(payload); } catch (err) { setError(err instanceof Error ? err.message : '补充信息提交失败'); }
  };
  return <form onSubmit={submit} className="zx-consult-form" data-testid="consult-input-form">
    <h3>还需要补充这些信息</h3>
    <p className="zx-consult-muted">填写后会继续本次咨询。文件信息请直接用文字说明。</p>
    {fields.map(field => {
      const key = keyOf(field);
      const value = values[key];
      const label = `${field.label}${field.unit ? `（${field.unit}）` : ''}`;
      return <label className="zx-consult-field" key={key}>
        <span>{label}{field.required !== false ? ' *' : ''}</span>
        {field.type === 'enum' ? <select aria-label={label} value={value === undefined ? '' : String(value)} onChange={e => change(field, (field.allowed_values || []).find(v => String(v) === e.target.value))} required={field.required !== false}>
          <option value="">请选择</option>{(field.allowed_values || []).map((option, i) => <option key={i} value={String(option)}>{String(option)}</option>)}
        </select> : field.type === 'boolean' ? <select aria-label={label} value={value === undefined ? '' : String(value)} onChange={e => change(field, e.target.value === '' ? undefined : e.target.value === 'true')} required={field.required !== false}>
          <option value="">请选择</option><option value="true">是</option><option value="false">否</option>
        </select> : field.type === 'period' ? <span className="zx-consult-period">
          <input aria-label={`${field.label}开始日期`} type="date" required={field.required !== false} value={Array.isArray(value) ? String(value[0] || '') : ''} onChange={e => change(field, [e.target.value, Array.isArray(value) ? value[1] : ''])} />
          <span>至</span><input aria-label={`${field.label}结束日期`} type="date" required={field.required !== false} value={Array.isArray(value) ? String(value[1] || '') : ''} onChange={e => change(field, [Array.isArray(value) ? value[0] : '', e.target.value])} />
        </span> : <input aria-label={label} type={field.type === 'number' ? 'number' : 'text'} placeholder={field.type === 'date' ? '例如 2026-10-01 或 2026-10-01 09:30' : undefined} step={field.type === 'number' ? 'any' : undefined} required={field.required !== false} value={value === undefined ? '' : String(value)} onChange={e => change(field, e.target.value)} />}
        {field.description && <small>{field.description}</small>}
      </label>;
    })}
    {error && <p role="alert" className="zx-consult-error">{error}</p>}
    <button type="submit" className="btn-primary" disabled={busy}>{busy ? '正在提交…' : '提交并继续'}</button>
  </form>;
};
