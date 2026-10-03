import React, { useEffect, useMemo, useState } from 'react';
import { Download, Loader2 } from 'lucide-react';
import { api } from '../../../services/api';
import { SkillCompareResponse, SkillFieldDiff, SkillReviewRecord, SkillVersionInfo } from '../../../types';
import { SegmentedTabs } from '../../ui/SegmentedTabs';
import { ErrorNotice, errorMessage } from './sceneShared';
import {
  GROUP_LABELS,
  UNSUPPORTED_RESOLUTION_TEXT,
  basisLabel,
  formatTime,
  plainText,
  readableValue,
  saveBlob,
  smallLabel,
} from './skillReviewShared';

type View = 'summary' | 'compare' | 'list';

// 字段对照按 A~E 组对齐（FR13）
const FIELD_LAYOUT: { group: string; fields: { key: string; label: string }[] }[] = [
  { group: 'A', fields: [
    { key: 'name', label: '名称' }, { key: 'goal', label: '业务目标' }, { key: 'trigger_description', label: '触发描述' },
    { key: 'task_type', label: '任务类型' }, { key: 'applies_to', label: '适用范围' }, { key: 'not_applies_to', label: '不适用范围' },
  ] },
  { group: 'B', fields: [{ key: 'knowledge_refs', label: '知识引用' }] },
  { group: 'C', fields: [
    { key: 'inputs', label: '输入字段' }, { key: 'preconditions', label: '前置条件' }, { key: 'outputs', label: '输出字段' },
    { key: 'output_template', label: '输出模板' },
  ] },
  { group: 'D', fields: [{ key: 'steps', label: '执行步骤' }] },
  { group: 'E', fields: [{ key: 'risk_boundary', label: '风险边界' }, { key: 'escalation_conditions', label: '人工升级条件' }] },
  { group: 'G', fields: [{ key: 'maintainer', label: '维护人' }] },
];
const ID_FIELD: Record<string, string> = { steps: 'step_id', inputs: 'key', outputs: 'key', knowledge_refs: 'atom_version_id' };

interface Props {
  skillId: string;
  skillName: string;
  versions: SkillVersionInfo[];
  records: SkillReviewRecord[];
  currentVersionId: string | null;
  titleOf: (vid: string) => string;
}

/** 修改记录：修改摘要、字段对照、修改清单；任意两个版本可比较，默认 AI 原稿对最新版本；导出（FR13） */
export const SkillDiffPane: React.FC<Props> = ({ skillId, skillName, versions, records, currentVersionId, titleOf }) => {
  const original = versions.find((v) => v.version_kind === 'ai_original') || versions[0];
  const latest = versions.find((v) => v.version_id === currentVersionId) || versions[versions.length - 1];
  const [fromId, setFromId] = useState(original?.version_id || '');
  const [toId, setToId] = useState(latest?.version_id || '');
  const [view, setView] = useState<View>('summary');
  const [data, setData] = useState<SkillCompareResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [exporting, setExporting] = useState<string | null>(null);

  useEffect(() => {
    if (!fromId || !toId) return;
    let alive = true;
    api.compareSkillVersions(skillId, fromId, toId)
      .then((res) => alive && (setData(res), setError(null)))
      .catch((err) => alive && setError(errorMessage(err, '修改记录加载失败')));
    return () => { alive = false; };
  }, [skillId, fromId, toId]);

  if (versions.length === 0) {
    return <div style={{ padding: '24px 32px', ...smallLabel }}>这条 Skill 还没有版本。</div>;
  }

  const doExport = async (format: 'markdown' | 'json') => {
    setExporting(format);
    try {
      const blob = await api.exportSkillDiff(skillId, format, fromId, toId);
      const a = versions.find((v) => v.version_id === fromId)?.version_number;
      const b = versions.find((v) => v.version_id === toId)?.version_number;
      saveBlob(blob, `${skillName || skillId}_v${a}-v${b}_差异.${format === 'json' ? 'json' : 'md'}`);
    } catch (err) {
      setError(errorMessage(err, '导出失败，请稍后再试'));
    } finally {
      setExporting(null);
    }
  };

  const versionSelect = (value: string, onChange: (v: string) => void, testId: string) => (
    <select value={value} onChange={(e) => onChange(e.target.value)} data-testid={testId}
      style={{ height: '32px', padding: '0 8px', fontSize: 'var(--font-size-sm)', border: '1px solid var(--border-color)', borderRadius: 'var(--radius-sm)', backgroundColor: 'var(--bg-primary)' }}>
      {versions.map((v) => <option key={v.version_id} value={v.version_id}>第 {v.version_number} 版 · {v.version_kind_label}</option>)}
    </select>
  );

  return (
    <div style={{ padding: '16px 32px 48px', flex: 1, overflow: 'auto' }} data-testid="diff-pane">
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '10px', marginBottom: '16px' }}>
        <span style={smallLabel}>比较</span>
        {versionSelect(fromId, setFromId, 'diff-from')}
        <span style={smallLabel}>和</span>
        {versionSelect(toId, setToId, 'diff-to')}
        <SegmentedTabs<View>
          value={view}
          onChange={setView}
          options={[
            { key: 'summary', label: '修改摘要', testId: 'diff-view-summary' },
            { key: 'compare', label: '字段对照', testId: 'diff-view-compare' },
            { key: 'list', label: '修改清单', count: data?.diffs.length || undefined, testId: 'diff-view-list' },
          ]}
        />
        <span style={{ flex: 1 }} />
        <button type="button" className="btn-secondary btn-sm" onClick={() => doExport('markdown')} disabled={!!exporting} data-testid="export-md">
          {exporting === 'markdown' ? <Loader2 size={13} className="spin-slow" /> : <Download size={13} />}导出 Markdown
        </button>
        <button type="button" className="btn-secondary btn-sm" onClick={() => doExport('json')} disabled={!!exporting} data-testid="export-json">
          {exporting === 'json' ? <Loader2 size={13} className="spin-slow" /> : <Download size={13} />}导出 JSON
        </button>
      </div>
      {error && <div style={{ marginBottom: '12px' }}><ErrorNotice message={error} /></div>}
      {!data ? (
        <div style={smallLabel}><Loader2 size={13} className="spin-slow" /> 正在加载…</div>
      ) : view === 'summary' ? (
        <SummaryView data={data} records={records} />
      ) : view === 'compare' ? (
        <CompareView data={data} titleOf={titleOf} />
      ) : (
        <ChangeList diffs={data.diffs} titleOf={titleOf} />
      )}
    </div>
  );
};

const Stat: React.FC<{ label: string; value: React.ReactNode; testId?: string }> = ({ label, value, testId }) => (
  <div className="zx-card" style={{ padding: '12px 16px', minWidth: '120px' }} data-testid={testId}>
    <div style={smallLabel}>{label}</div>
    <div style={{ fontSize: '20px', fontWeight: 600, color: 'var(--text-primary)', marginTop: '4px' }}>{value}</div>
  </div>
);

const SummaryView: React.FC<{ data: SkillCompareResponse; records: SkillReviewRecord[] }> = ({ data, records }) => {
  const s = data.summary;
  const res = Object.entries(s.unsupported_resolutions || {});
  return (
    <div data-testid="diff-summary">
      <div style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-secondary)', marginBottom: '12px' }}>
        第 {data.from.version_number} 版（{data.from.version_kind_label}，{formatTime(data.from.created_at)}）→
        第 {data.to.version_number} 版（{data.to.version_kind_label}，{formatTime(data.to.created_at)}）
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '10px', marginBottom: '16px' }}>
        <Stat label="修改字段数" value={s.changed_field_count} testId="stat-changed" />
        <Stat label="新增步骤" value={s.steps_added} testId="stat-steps-added" />
        <Stat label="删除步骤" value={s.steps_deleted} testId="stat-steps-deleted" />
        <Stat label="步骤顺序" value={s.steps_reordered ? '有调整' : '未调整'} />
      </div>
      <div className="zx-card" style={{ padding: '12px 16px', marginBottom: '16px', fontSize: 'var(--font-size-sm)', lineHeight: 1.8 }}>
        <div data-testid="summary-groups">各部分改动：{Object.keys(s.by_group).length === 0 ? '无' :
          Object.entries(s.by_group).sort().map(([g, n]) => `${g} ${GROUP_LABELS[g] || ''} ${n} 处`).join('；')}</div>
        <div data-testid="summary-unsupported">无依据项处理：{res.length === 0 ? '无' :
          res.map(([k, v]) => `${UNSUPPORTED_RESOLUTION_TEXT[k] || k} ${v} 项`).join('；')}</div>
      </div>
      <div style={{ fontSize: 'var(--font-size-section)', fontWeight: 600, margin: '8px 0' }}>审核记录</div>
      {records.length === 0 ? <div style={smallLabel}>还没有审核记录。</div> : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }} data-testid="review-records">
          {records.map((r) => (
            <div key={r.record_id} className="zx-card" style={{ padding: '10px 14px', fontSize: 'var(--font-size-sm)' }} data-testid="review-record">
              <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                <span className="zx-tag neutral">{r.action_label}</span>
                <span>{r.operator_name}</span>
                <span style={smallLabel}>{formatTime(r.created_at)}</span>
                {r.to_version_number && r.to_version_number !== r.from_version_number && <span style={smallLabel}>产生第 {r.to_version_number} 版</span>}
                {r.detail?.state === 'running' && <span className="zx-tag neutral">生成中</span>}
                {r.detail?.state === 'failed' && <span className="zx-tag warning">未成功：{r.detail?.message}</span>}
              </div>
              {r.reject_reason && <div style={{ marginTop: '4px' }}>原因：{r.reject_reason}</div>}
              {r.comment && <div style={{ marginTop: '4px', color: 'var(--text-secondary)' }}>意见：{plainText(r.comment)}</div>}
              {r.regenerate_groups.length > 0 && <div style={{ ...smallLabel, marginTop: '4px' }}>重写部分：{r.regenerate_groups.map((g) => `${g} ${GROUP_LABELS[g] || ''}`).join('、')}</div>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

const renderItem = (field: string, item: any, titleOf: (vid: string) => string): string => {
  if (!item) return '';
  if (field === 'knowledge_refs') return `${titleOf(item.atom_version_id)}（${item.role}）`;
  if (field === 'steps') {
    return `${item.kind}${item.condition ? `｜条件：${plainText(item.condition)}` : ''}｜${plainText(item.action)}｜${basisLabel(item.basis)}${item.expert_reason ? `（${item.expert_reason}）` : ''}｜失败时${item.on_fail}${(item.refs || []).length ? `｜依据：${item.refs.map(titleOf).join('、')}` : ''}`;
  }
  return readableValue(item);
};

const CompareView: React.FC<{ data: SkillCompareResponse; titleOf: (vid: string) => string }> = ({ data, titleOf }) => {
  const byField = useMemo(() => {
    const map = new Map<string, SkillFieldDiff[]>();
    data.diffs.forEach((d) => map.set(d.field, [...(map.get(d.field) || []), d]));
    return map;
  }, [data]);
  const a = data.from.skill_json || {};
  const b = data.to.skill_json || {};
  const cellStyle = (tone?: 'add' | 'del' | 'mod'): React.CSSProperties => ({
    padding: '8px 10px',
    fontSize: 'var(--font-size-sm)',
    lineHeight: 1.6,
    color: 'var(--text-primary)',
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-word',
    borderRadius: 'var(--radius-sm)',
    backgroundColor: tone === 'add' ? 'var(--success-bg)' : tone === 'del' ? 'var(--danger-bg)' : tone === 'mod' ? 'var(--highlight-bg)' : 'var(--bg-secondary)',
  });

  const renderField = (key: string) => {
    const diffs = byField.get(key) || [];
    const idField = ID_FIELD[key];
    if (idField) {
      const aItems: any[] = Array.isArray(a[key]) ? a[key] : [];
      const bItems: any[] = Array.isArray(b[key]) ? b[key] : [];
      const ids = [...bItems.map((i) => i[idField]), ...aItems.map((i) => i[idField]).filter((id) => !bItems.some((x) => x[idField] === id))];
      return ids.map((id) => {
        const left = aItems.find((i) => i[idField] === id);
        const right = bItems.find((i) => i[idField] === id);
        const changed = diffs.some((d) => d.path.startsWith(`${key}[${id}]`));
        const tone = !left ? 'add' : !right ? 'del' : changed ? 'mod' : undefined;
        const props = diffs.filter((d) => d.path.startsWith(`${key}[${id}].`)).map((d) => d.label.split(' · ').slice(-1)[0]);
        return (
          <div key={id} style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px', marginBottom: '6px' }}
            data-testid="compare-item" data-changed={tone ? tone : undefined} data-item-id={id}>
            <div style={cellStyle(left ? (tone === 'del' ? 'del' : tone === 'mod' ? 'mod' : undefined) : undefined)}>
              {left ? <><b>{key === 'knowledge_refs' ? '' : `${id} `}</b>{renderItem(key, left, titleOf)}</> : <span style={smallLabel}>（无）</span>}
            </div>
            <div style={cellStyle(right ? (tone === 'add' ? 'add' : tone === 'mod' ? 'mod' : undefined) : undefined)}>
              {right ? <><b>{key === 'knowledge_refs' ? '' : `${id} `}</b>{renderItem(key, right, titleOf)}</> : <span style={smallLabel}>（已删除）</span>}
              {props.length > 0 && <div style={{ ...smallLabel, marginTop: '2px' }}>改动：{props.join('、')}</div>}
            </div>
          </div>
        );
      });
    }
    const changed = diffs.length > 0;
    return (
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px' }} data-testid="compare-item" data-changed={changed ? 'mod' : undefined}>
        <div style={cellStyle(changed ? 'mod' : undefined)}>{readableField(a[key])}</div>
        <div style={cellStyle(changed ? 'mod' : undefined)}>{readableField(b[key])}</div>
      </div>
    );
  };

  return (
    <div data-testid="diff-compare">
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px', marginBottom: '10px', fontSize: 'var(--font-size-sm)', fontWeight: 600 }}>
        <div>第 {data.from.version_number} 版 · {data.from.version_kind_label}</div>
        <div>第 {data.to.version_number} 版 · {data.to.version_kind_label}</div>
      </div>
      {FIELD_LAYOUT.map((g) => (
        <div key={g.group} style={{ marginBottom: '18px' }}>
          <div style={{ fontSize: 'var(--font-size-section)', fontWeight: 600, marginBottom: '8px', paddingBottom: '6px', borderBottom: '1px solid var(--border-color)' }}>
            {g.group} {GROUP_LABELS[g.group]}
          </div>
          {g.fields.map((f) => (
            <div key={f.key} style={{ marginBottom: '10px' }} data-testid="compare-field" data-field={f.key} data-changed={byField.has(f.key) ? 'true' : undefined}>
              <div style={{ ...smallLabel, marginBottom: '4px' }}>
                {f.label}{byField.has(f.key) && <span className="zx-tag warning" style={{ marginLeft: '6px' }}>有改动 {byField.get(f.key)!.length} 处</span>}
              </div>
              {renderField(f.key)}
            </div>
          ))}
        </div>
      ))}
    </div>
  );
};

const readableField = (value: unknown): string => {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    const obj = value as Record<string, string[]>;
    if ('customer_types' in obj) {
      return [`客户类型：${(obj.customer_types || []).join('、') || '—'}`, `业态：${(obj.property_types || []).join('、') || '—'}`,
        `条件：${(obj.conditions || []).join('、') || '—'}`].join('\n');
    }
  }
  if (Array.isArray(value)) return value.map((v) => `· ${readableValue(v)}`).join('\n') || '（空）';
  return readableValue(value);
};

const ChangeList: React.FC<{ diffs: SkillFieldDiff[]; titleOf: (vid: string) => string }> = ({ diffs, titleOf }) => {
  if (diffs.length === 0) return <div style={smallLabel} data-testid="diff-list-empty">两个版本内容相同。</div>;
  const groups = Array.from(new Set(diffs.map((d) => d.group))).sort();
  const show = (d: SkillFieldDiff, value: unknown) =>
    d.field === 'knowledge_refs' && value && typeof value === 'object' && 'atom_version_id' in (value as any)
      ? renderItem('knowledge_refs', value, titleOf)
      : d.field === 'steps' && value && typeof value === 'object' && 'step_id' in (value as any)
        ? renderItem('steps', value, titleOf)
        : readableValue(value);
  return (
    <div data-testid="diff-list">
      {groups.map((g) => (
        <div key={g} style={{ marginBottom: '16px' }}>
          <div style={{ fontSize: 'var(--font-size-section)', fontWeight: 600, marginBottom: '8px' }}>{g} {GROUP_LABELS[g] || ''}</div>
          {diffs.filter((d) => d.group === g).map((d) => (
            <div key={d.path + d.op} className="zx-card" style={{ padding: '10px 14px', marginBottom: '6px' }} data-testid="diff-row" data-path={d.path} data-op={d.op}>
              <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' }}>
                <span className={`zx-tag ${d.op === 'delete' ? 'danger' : d.op === 'add' ? '' : 'warning'}`}>{d.op_label}</span>
                <span style={{ fontSize: 'var(--font-size-sm)', fontWeight: 500 }}>{d.label}</span>
                <code style={{ ...smallLabel, fontFamily: 'Consolas, monospace' }}>{d.path}</code>
              </div>
              <div style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-secondary)', marginTop: '6px', lineHeight: 1.6 }}>
                {d.op === 'reorder' ? (
                  <div>顺序：{(d.before as string[]).join(' → ')} 改为 {(d.after as string[]).join(' → ')}</div>
                ) : (
                  <>
                    {d.op !== 'add' && <div><span style={smallLabel}>修改前：</span>{show(d, d.before)}</div>}
                    {d.op !== 'delete' && <div><span style={smallLabel}>修改后：</span>{show(d, d.after)}</div>}
                  </>
                )}
                {d.reason && <div style={{ marginTop: '2px' }}><span style={smallLabel}>理由：</span>{d.reason}</div>}
              </div>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
};
