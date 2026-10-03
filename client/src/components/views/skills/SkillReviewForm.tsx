import React from 'react';
import { Check, Link2, Pencil, Plus, Trash2, X } from 'lucide-react';
import { SkillAtomPanelItem, SkillContent, SkillIoField, SkillStep } from '../../../types';
import {
  BASIS_OPTIONS,
  FieldBlock,
  IO_TYPES,
  IO_TYPE_LABELS,
  MoveButtons,
  ON_FAIL,
  ON_FAIL_TEXT,
  STEP_KINDS,
  StringListEditor,
  TASK_TYPES,
  TBD,
  iconButton,
  inputStyle,
  moveItem,
  plainText,
  smallLabel,
  tbdStyle,
  textAreaStyle,
} from './skillReviewShared';

// ---------------------------------------------------------------------------
// 审核分段：默认只读，逐段「修改」。审核清单（FR12）集中在「通过」对话框中逐项确认，check 为对应清单项
// ---------------------------------------------------------------------------

export type SectionKey = 'identity' | 'scope' | 'refs' | 'io' | 'steps' | 'risk' | 'maintain';

export const SECTIONS: { key: SectionKey; title: string; nav: string; hint: string; check?: string }[] = [
  { key: 'identity', title: '它做什么', nav: '概要', hint: '名称、业务目标、用户会怎么提出', check: 'goal' },
  { key: 'scope', title: '什么时候用，什么时候不用', nav: '范围', hint: '适用与不适用范围', check: 'scope' },
  { key: 'steps', title: '按什么步骤做', nav: '步骤', hint: '点一步，右侧显示它依据的知识', check: 'steps' },
  { key: 'io', title: '需要什么信息，给出什么结果', nav: '输入与输出', hint: '输入、前置条件、输出', check: 'outputs' },
  { key: 'risk', title: '哪些情况必须转人工', nav: '转人工', hint: '风险边界与人工升级条件', check: 'escalation' },
  { key: 'refs', title: '依据哪些知识', nav: '依据', hint: '右侧逐条列出，可展开看原文', check: 'refs' },
  { key: 'maintain', title: '维护信息', nav: '维护', hint: '选填' },
];

const FIELD_SECTION: Record<string, SectionKey> = {
  name: 'identity', goal: 'identity', trigger_description: 'identity', task_type: 'identity',
  applies_to: 'scope', not_applies_to: 'scope',
  knowledge_refs: 'refs',
  inputs: 'io', preconditions: 'io', outputs: 'io', output_template: 'io',
  steps: 'steps',
  risk_boundary: 'risk', escalation_conditions: 'risk',
  maintainer: 'maintain',
};

export const sectionOfPath = (path: string | null | undefined): SectionKey => {
  const root = (path || '').split(/[.[]/)[0];
  return FIELD_SECTION[root] || 'identity';
};

export const stepOfPath = (path: string | null | undefined): string | null => {
  const m = /^steps\[([^\]]+)\]/.exec(path || '');
  return m ? m[1] : null;
};

interface SectionCardProps {
  sectionKey: SectionKey;
  canEdit: boolean;
  editing: boolean;
  problems: number;
  onToggleEdit: () => void;
  children: React.ReactNode;
}

export const SectionCard: React.FC<SectionCardProps> = ({ sectionKey, canEdit, editing, problems, onToggleEdit, children }) => {
  const meta = SECTIONS.find((s) => s.key === sectionKey)!;
  return (
    <section
      id={`section-${sectionKey}`}
      data-testid={`section-${sectionKey}`}
      data-editing={editing ? 'true' : undefined}
      style={{ marginBottom: '28px', scrollMarginTop: '16px' }}
    >
      <div style={{ display: 'flex', alignItems: 'baseline', gap: '10px', marginBottom: '10px' }}>
        <h2 style={{ margin: 0, fontSize: 'var(--font-size-section)', fontWeight: 700, color: 'var(--text-primary)' }}>{meta.title}</h2>
        <span style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-muted)' }}>{meta.hint}</span>
        <span style={{ flex: 1 }} />
        {problems > 0 && <span className="zx-tag warning" data-testid="section-problems">{problems} 处要处理</span>}
        {canEdit && (
          <button type="button" className={editing ? 'btn-primary btn-sm' : 'btn-ghost btn-sm'} onClick={onToggleEdit} data-testid={`edit-${sectionKey}`}>
            {editing ? <><Check size={13} />完成</> : <><Pencil size={13} />修改</>}
          </button>
        )}
      </div>
      <div style={{ backgroundColor: 'var(--bg-primary)', borderRadius: 'var(--radius-lg)', padding: sectionKey === 'steps' && !editing ? '8px' : '14px 18px',
        boxShadow: editing ? '0 0 0 2px var(--brand-300)' : 'var(--shadow-sm)' }}>
        {children}
      </div>
    </section>
  );
};

// ---------------------------------------------------------------------------
// 只读展示
// ---------------------------------------------------------------------------

const TbdMark: React.FC = () => (
  <span data-tbd="true" style={{ ...tbdStyle, border: '1px solid var(--warning-border)', color: 'var(--warning-text)', borderRadius: 'var(--radius-xs)', padding: '0 6px', fontSize: 'var(--font-size-xs)' }}>
    待专家补充
  </span>
);

const Text: React.FC<{ value: unknown; empty?: string }> = ({ value, empty = '（未填写）' }) => {
  if (value === TBD) return <TbdMark />;
  if (value === null || value === undefined || String(value).trim() === '') return <span style={smallLabel}>{empty}</span>;
  return <>{plainText(value)}</>;
};

const Row: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
  <div style={{ display: 'flex', gap: '12px', padding: '5px 0', fontSize: 'var(--font-size-base)', lineHeight: 1.65 }}>
    <span style={{ width: '104px', flexShrink: 0, color: 'var(--text-muted)', fontSize: 'var(--font-size-sm)', paddingTop: '1px' }}>{label}</span>
    <div style={{ flex: 1, minWidth: 0, color: 'var(--text-primary)' }}>{children}</div>
  </div>
);

const Bullets: React.FC<{ items: string[]; empty?: string }> = ({ items, empty = '（未填写）' }) =>
  items.length === 0 ? <span style={smallLabel}>{empty}</span> : (
    <ul style={{ margin: 0, paddingLeft: '18px' }}>
      {items.map((t, i) => <li key={i} style={{ padding: '1px 0' }}><Text value={t} /></li>)}
    </ul>
  );

const ioText = (f: SkillIoField) =>
  `${f.label || f.key}（${IO_TYPE_LABELS[f.type] || f.type}${f.unit ? `，单位 ${f.unit}` : ''}${f.type === 'enum' && f.allowed_values?.length ? `：${f.allowed_values.join('、')}` : ''}${f.required ? '，必需' : '，可不填'}）`;

interface ViewProps {
  content: SkillContent;
  titleOf: (vid: string) => string;
}

export const IdentityView: React.FC<ViewProps & { confidence?: { level: string; reason: string } | null }> = ({ content, confidence }) => (
  <>
    <Row label="名称"><Text value={content.name} /></Row>
    <Row label="业务目标"><Text value={content.goal} /></Row>
    <Row label="用户会这样提"><Text value={content.trigger_description} /></Row>
    <Row label="任务类型"><Text value={content.task_type} /></Row>
    {confidence && (
      <details style={{ marginTop: '6px', fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)', lineHeight: 1.6 }} data-testid="confidence-note">
        <summary style={{ cursor: 'pointer' }}>生成时的把握度：{confidence.level}（点开看生成时说明的不确定之处）</summary>
        <div style={{ marginTop: '4px', color: 'var(--text-secondary)' }}>{plainText(confidence.reason)}</div>
      </details>
    )}
  </>
);

export const ScopeView: React.FC<ViewProps> = ({ content }) => {
  const a = content.applies_to;
  const parts = [
    a.customer_types.length ? `客户：${a.customer_types.join('、')}` : '',
    a.property_types.length ? `业态：${a.property_types.join('、')}` : '',
  ].filter(Boolean);
  return (
    <>
      <Row label="适用">
        {parts.length > 0 && <div>{parts.join('；')}</div>}
        <Bullets items={a.conditions} empty={parts.length ? '' : '（未填写）'} />
      </Row>
      <Row label="不适用"><Bullets items={content.not_applies_to} /></Row>
    </>
  );
};

export const RefsView: React.FC<{ atoms: SkillAtomPanelItem[]; editing: boolean }> = ({ atoms, editing }) => {
  const ineligible = atoms.filter((a) => !a.eligible).length;
  const notLanded = atoms.filter((a) => a.exceptions_not_landed.length > 0).length;
  const mismatch = atoms.filter((a) => a.default_roles.length > 0 && !a.default_roles.includes(a.role)).length;
  return (
    <div style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-secondary)', lineHeight: 1.7 }}>
      <div>共 {atoms.length} 条依据知识，列在右侧，点开可看原文。{editing ? '现在可以在右侧修改用途、移除或添加依据。' : ''}</div>
      {ineligible > 0 && <div style={{ color: 'var(--danger-text)' }}>有 {ineligible} 条已停用或失效，需要移除或替换。</div>}
      {notLanded > 0 && <div style={{ color: 'var(--warning-text)' }}>有 {notLanded} 条知识写了特殊情况，转人工或不适用范围里还没有对应。</div>}
      {mismatch > 0 && <div>有 {mismatch} 条的角色和它的知识类型不太一致，请确认。</div>}
    </div>
  );
};

export const IoView: React.FC<ViewProps> = ({ content, titleOf }) => (
  <>
    <Row label="需要的信息"><Bullets items={content.inputs.map(ioText)} /></Row>
    <Row label="前置条件">
      <Bullets items={content.preconditions.map((p) => (p.ref ? `${p.text}（依据：${titleOf(p.ref)}）` : p.text))} empty="无" />
    </Row>
    <Row label="给出的结果"><Bullets items={content.outputs.map(ioText)} /></Row>
    {content.output_template && <Row label="输出模板"><div style={{ whiteSpace: 'pre-wrap' }}>{content.output_template}</div></Row>}
  </>
);

const stepBasisText = (step: SkillStep) => {
  if (step.refs.length > 0) return `依据 ${step.refs.length} 条`;
  if (step.basis === '专家补充') return step.expert_reason ? `我方补充：${step.expert_reason}` : '我方补充';
  if (step.basis === '通用操作') return '常规操作';
  return '没有出处';
};

/** 步骤流：序号节点 + 正文；未选中时正文收成一行，选中后展开并在右侧列出依据 */
export const StepsView: React.FC<{
  content: SkillContent;
  selectedStep: string | null;
  highlightSteps: Set<string>;
  flaggedPaths: Set<string>;
  onSelectStep: (sid: string | null) => void;
  renderQuickActions?: (step: SkillStep) => React.ReactNode;
}> = ({ content, selectedStep, highlightSteps, flaggedPaths, onSelectStep, renderQuickActions }) => (
  <ol style={{ margin: 0, padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: '2px' }}>
    {content.steps.map((step, idx) => {
      const selected = selectedStep === step.step_id;
      const highlighted = highlightSteps.has(step.step_id);
      const flagged = [...flaggedPaths].some((p) => p.startsWith(`steps[${step.step_id}]`));
      const open = selected || highlighted || flagged;
      const quick = flagged && renderQuickActions ? renderQuickActions(step) : null;
      const nodeColor = selected ? 'var(--brand-600)' : flagged ? 'var(--warning-text)' : 'var(--text-secondary)';
      return (
        <li
          key={step.step_id}
          id={`step-${step.step_id}`}
          data-testid="step-card"
          data-step-id={step.step_id}
          data-highlight={selected || highlighted ? 'true' : undefined}
          data-flagged={flagged ? 'true' : undefined}
          onClick={() => onSelectStep(selected ? null : step.step_id)}
          style={{ display: 'flex', gap: '14px', padding: '12px 14px', borderRadius: 'var(--radius-md)', cursor: 'pointer', scrollMarginTop: '16px',
            backgroundColor: selected ? 'var(--bg-primary)' : highlighted ? 'var(--brand-50)' : flagged ? 'var(--warning-bg)' : undefined,
            boxShadow: selected ? '0 0 0 2px var(--brand-500), var(--shadow-md)' : flagged ? 'inset 0 0 0 1px var(--warning-border)' : undefined,
            transition: 'background-color 160ms ease, box-shadow 160ms ease' }}
        >
          <button type="button" data-testid="step-select" aria-label={`第 ${idx + 1} 步`}
            onClick={(e) => { e.stopPropagation(); onSelectStep(selected ? null : step.step_id); }}
            style={{ width: '28px', height: '28px', borderRadius: '50%', flexShrink: 0, cursor: 'pointer', padding: 0,
              display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: 'var(--font-size-sm)', fontWeight: 700,
              border: selected ? 'none' : `1.5px solid ${flagged ? 'var(--warning-border)' : 'var(--border-strong)'}`,
              backgroundColor: selected ? 'var(--brand-600)' : flagged ? 'var(--warning-bg)' : 'var(--bg-primary)',
              color: selected ? '#fff' : nodeColor }}>
            {idx + 1}
          </button>
          <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: '4px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap', fontSize: 'var(--font-size-xs)' }}>
              <span style={{ fontWeight: 600, color: 'var(--text-secondary)' }}>{step.kind}</span>
              <span style={{ fontWeight: 600, color: step.refs.length ? (selected ? 'var(--brand-600)' : 'var(--text-muted)') : step.basis === '无依据' ? 'var(--warning-text)' : 'var(--text-muted)' }}>
                {stepBasisText(step)}
              </span>
              <span style={{ color: 'var(--text-muted)' }}>{ON_FAIL_TEXT[step.on_fail] || `失败时${step.on_fail}`}</span>
            </div>
            {step.condition && <div style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-secondary)' }}>当 {plainText(step.condition)} 时：</div>}
            <div style={{ fontSize: 'var(--font-size-base)', color: 'var(--text-primary)', lineHeight: 1.7,
              ...(open ? {} : { overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }) }}>
              <Text value={step.action} />
            </div>
            {quick && <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', marginTop: '4px' }} onClick={(e) => e.stopPropagation()}>{quick}</div>}
          </div>
        </li>
      );
    })}
  </ol>
);

export const RiskView: React.FC<ViewProps> = ({ content }) => (
  <>
    <Row label="风险边界"><Bullets items={content.risk_boundary} /></Row>
    <Row label="转人工的情形"><Bullets items={content.escalation_conditions} /></Row>
  </>
);

// ---------------------------------------------------------------------------
// 编辑
// ---------------------------------------------------------------------------

interface EditProps {
  content: SkillContent;
  onChange: (next: SkillContent) => void;
}

const isTbd = (v: unknown) => v === TBD;

export const IdentityEditor: React.FC<EditProps> = ({ content, onChange }) => {
  const set = (patch: Partial<SkillContent>) => onChange({ ...content, ...patch });
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
      <FieldBlock label="名称" hint="动宾结构，不超过 20 字">
        <input style={inputStyle} value={content.name} maxLength={40} data-testid="field-name" onChange={(e) => set({ name: e.target.value })} />
      </FieldBlock>
      <FieldBlock label="业务目标">
        <textarea style={textAreaStyle} value={content.goal} data-testid="field-goal" onChange={(e) => set({ goal: e.target.value })} />
      </FieldBlock>
      <FieldBlock label="用户会这样提" hint="只描述一个任务">
        <textarea style={textAreaStyle} value={content.trigger_description} onChange={(e) => set({ trigger_description: e.target.value })} />
      </FieldBlock>
      <FieldBlock label="任务类型">
        <select style={{ ...inputStyle, width: '200px' }} value={content.task_type} onChange={(e) => set({ task_type: e.target.value })}>
          {TASK_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
      </FieldBlock>
    </div>
  );
};

export const ScopeEditor: React.FC<EditProps> = ({ content, onChange }) => {
  const set = (patch: Partial<SkillContent>) => onChange({ ...content, ...patch });
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
      <FieldBlock label="适用范围" hint="客户类型、业态、其他条件至少填一项">
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: '12px' }}>
          {(['customer_types', 'property_types', 'conditions'] as const).map((k) => (
            <div key={k}>
              <div style={{ ...smallLabel, marginBottom: '4px' }}>{{ customer_types: '客户类型', property_types: '业态', conditions: '其他条件' }[k]}</div>
              <StringListEditor items={content.applies_to[k]} onChange={(items) => set({ applies_to: { ...content.applies_to, [k]: items } })} />
            </div>
          ))}
        </div>
      </FieldBlock>
      <FieldBlock label="不适用范围" hint="明确不处理的情形" highlight={content.not_applies_to.some(isTbd)} testId="field-not-applies">
        <StringListEditor items={content.not_applies_to} onChange={(items) => set({ not_applies_to: items })} />
      </FieldBlock>
    </div>
  );
};

export const IoSectionEditor: React.FC<EditProps & {
  baseKeys: { inputs: Set<string>; outputs: Set<string> };
  refOptions: { value: string; label: string }[];
  selectedAtom: string | null;
  flaggedPaths: Set<string>;
}> = ({ content, onChange, baseKeys, refOptions, selectedAtom, flaggedPaths }) => {
  const set = (patch: Partial<SkillContent>) => onChange({ ...content, ...patch });
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
      <FieldBlock label="需要的信息（输入字段）">
        <IoEditor fields={content.inputs} baseKeys={baseKeys.inputs} refOptions={refOptions}
          selectedAtom={selectedAtom} prefix="inputs" flaggedPaths={flaggedPaths} onChange={(inputs) => set({ inputs })} testId="inputs-editor" />
      </FieldBlock>
      <FieldBlock label="前置条件">
        <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
          {content.preconditions.map((pre, idx) => (
            <div key={idx} style={{ display: 'flex', gap: '6px', alignItems: 'center' }}>
              <input style={{ ...inputStyle, ...(isTbd(pre.text) ? tbdStyle : {}) }} value={isTbd(pre.text) ? '' : pre.text}
                placeholder={isTbd(pre.text) ? '待专家补充' : '执行前必须满足的条件'}
                onChange={(e) => set({ preconditions: content.preconditions.map((p, i) => (i === idx ? { ...p, text: e.target.value } : p)) })} />
              <select style={{ ...inputStyle, width: '180px' }} value={pre.ref || ''}
                onChange={(e) => set({ preconditions: content.preconditions.map((p, i) => (i === idx ? { ...p, ref: e.target.value || null } : p)) })}>
                <option value="">不指定依据</option>
                {refOptions.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
              <button type="button" style={iconButton} aria-label="删除前置条件"
                onClick={() => set({ preconditions: content.preconditions.filter((_, i) => i !== idx) })}><X size={14} /></button>
            </div>
          ))}
          <div><button type="button" className="btn-ghost btn-sm"
            onClick={() => set({ preconditions: [...content.preconditions, { text: '', ref: null }] })}><Plus size={13} />添加前置条件</button></div>
        </div>
      </FieldBlock>
      <FieldBlock label="给出的结果（输出字段）">
        <IoEditor fields={content.outputs} baseKeys={baseKeys.outputs} refOptions={refOptions}
          selectedAtom={selectedAtom} prefix="outputs" flaggedPaths={flaggedPaths} onChange={(outputs) => set({ outputs })} testId="outputs-editor" />
      </FieldBlock>
      <FieldBlock label="输出模板" hint="选填，面向用户的文字格式">
        <textarea style={textAreaStyle} value={content.output_template || ''} onChange={(e) => set({ output_template: e.target.value })} />
      </FieldBlock>
    </div>
  );
};

export const StepsEditor: React.FC<EditProps & {
  refOptions: { value: string; label: string }[];
  titleOf: (vid: string) => string;
  selectedStep: string | null;
  highlightSteps: Set<string>;
  flaggedPaths: Set<string>;
  onSelectStep: (sid: string | null) => void;
  onAllocateStepId: () => string;
  onPickForStep: (sid: string) => void;
}> = ({ content, onChange, refOptions, titleOf, selectedStep, highlightSteps, flaggedPaths, onSelectStep, onAllocateStepId, onPickForStep }) => {
  const set = (patch: Partial<SkillContent>) => onChange({ ...content, ...patch });
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
      {content.steps.map((step, idx) => (
        <StepCard
          key={step.step_id}
          step={step}
          index={idx}
          total={content.steps.length}
          selected={selectedStep === step.step_id}
          highlighted={highlightSteps.has(step.step_id)}
          flagged={[...flaggedPaths].some((p) => p.startsWith(`steps[${step.step_id}]`))}
          refOptions={refOptions}
          titleOf={titleOf}
          onSelect={() => onSelectStep(selectedStep === step.step_id ? null : step.step_id)}
          onChange={(patch) => set({ steps: content.steps.map((s, i) => (i === idx ? { ...s, ...patch } : s)) })}
          onMove={(to) => set({ steps: moveItem(content.steps, idx, to) })}
          onDelete={() => set({ steps: content.steps.filter((_, i) => i !== idx) })}
          onPick={() => onPickForStep(step.step_id)}
        />
      ))}
      <div>
        <button type="button" className="btn-secondary btn-sm" data-testid="step-add"
          onClick={() => {
            const sid = onAllocateStepId();
            set({ steps: [...content.steps, { step_id: sid, kind: '人工确认', action: '', refs: [], basis: '通用操作', on_fail: '暂停' }] });
            onSelectStep(sid);
          }}>
          <Plus size={13} />新增步骤
        </button>
        <span style={{ ...smallLabel, marginLeft: '8px' }}>步骤编号由系统分配，删除后不再复用</span>
      </div>
    </div>
  );
};

export const RiskEditor: React.FC<EditProps> = ({ content, onChange }) => {
  const set = (patch: Partial<SkillContent>) => onChange({ ...content, ...patch });
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
      <FieldBlock label="风险边界" hint="结果可能被误用的情形" highlight={content.risk_boundary.some(isTbd)} testId="field-risk">
        <StringListEditor items={content.risk_boundary} onChange={(items) => set({ risk_boundary: items })} />
      </FieldBlock>
      <FieldBlock label="转人工的情形" hint="知识中的例外优先写在这里或分支步骤" highlight={content.escalation_conditions.some(isTbd)} testId="field-escalation">
        <StringListEditor items={content.escalation_conditions} onChange={(items) => set({ escalation_conditions: items })} testId="escalation-editor" />
      </FieldBlock>
    </div>
  );
};

export const MaintainerEditor: React.FC<EditProps & { editing: boolean }> = ({ content, onChange, editing }) =>
  editing ? (
    <input style={{ ...inputStyle, width: '260px' }} value={content.maintainer || ''} maxLength={50} placeholder="维护人（选填）"
      onChange={(e) => onChange({ ...content, maintainer: e.target.value })} data-testid="field-maintainer" />
  ) : (
    <Row label="维护人"><Text value={content.maintainer} empty="未指定" /></Row>
  );

// ---------------------------------------------------------------------------
// 步骤与输入输出的编辑卡片
// ---------------------------------------------------------------------------

const StepCard: React.FC<{
  step: SkillStep;
  index: number;
  total: number;
  selected: boolean;
  highlighted: boolean;
  flagged: boolean;
  refOptions: { value: string; label: string }[];
  titleOf: (vid: string) => string;
  onSelect: () => void;
  onChange: (patch: Partial<SkillStep>) => void;
  onMove: (to: number) => void;
  onDelete: () => void;
  onPick: () => void;
}> = ({ step, index, total, selected, highlighted, flagged, refOptions, titleOf, onSelect, onChange, onMove, onDelete, onPick }) => {
  const available = refOptions.filter((o) => !step.refs.includes(o.value));
  const selectSm: React.CSSProperties = { ...inputStyle, width: 'auto', height: '28px', fontSize: 'var(--font-size-xs)' };
  return (
    <div
      id={`step-${step.step_id}`}
      data-testid="step-card"
      data-step-id={step.step_id}
      data-highlight={highlighted || selected ? 'true' : undefined}
      data-flagged={flagged ? 'true' : undefined}
      className="zx-card"
      style={{
        padding: '12px 14px',
        borderLeft: flagged ? '3px solid var(--warning-text)' : undefined,
        boxShadow: selected ? '0 0 0 2px var(--brand-500)' : highlighted ? '0 0 0 2px var(--brand-300)' : undefined,
        backgroundColor: highlighted || selected ? 'var(--brand-50)' : flagged ? 'var(--warning-bg)' : undefined,
        transition: 'box-shadow 160ms ease, background-color 160ms ease',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap' }}>
        <button type="button" className="zx-tag" onClick={onSelect} data-testid="step-select"
          style={{ border: 0, cursor: 'pointer', fontWeight: 600 }} title="点击查看依据的知识">第 {index + 1} 步</button>
        <select style={selectSm} value={step.kind} aria-label="步骤类别"
          onChange={(e) => onChange({ kind: e.target.value as SkillStep['kind'] })}>
          {STEP_KINDS.map((k) => <option key={k} value={k}>{k}</option>)}
        </select>
        <select style={{ ...selectSm, ...(step.basis === '无依据' ? tbdStyle : {}) }} value={step.basis}
          aria-label="依据状态" data-testid="step-basis"
          onChange={(e) => onChange({ basis: e.target.value as SkillStep['basis'] })}>
          {BASIS_OPTIONS.map((b) => <option key={b.value} value={b.value}>{b.label}</option>)}
        </select>
        <span style={smallLabel}>失败时</span>
        <select style={selectSm} value={step.on_fail} aria-label="失败处理"
          onChange={(e) => onChange({ on_fail: e.target.value as SkillStep['on_fail'] })}>
          {ON_FAIL.map((k) => <option key={k} value={k}>{k}</option>)}
        </select>
        <span style={{ flex: 1 }} />
        <MoveButtons index={index} total={total} onMove={onMove} />
        <button type="button" style={iconButton} onClick={onDelete} title="删除步骤" aria-label="删除步骤" data-testid="step-delete"><Trash2 size={14} /></button>
      </div>
      <input style={{ ...inputStyle, marginTop: '8px', height: '30px', fontSize: 'var(--font-size-xs)' }} value={step.condition || ''}
        placeholder="执行条件：为空表示按顺序执行，填写后表示只在该条件下执行"
        onChange={(e) => onChange({ condition: e.target.value || null })} />
      <textarea style={{ ...textAreaStyle, marginTop: '6px', ...(step.action === TBD ? tbdStyle : {}) }} value={step.action === TBD ? '' : step.action}
        placeholder={step.action === TBD ? '待专家补充' : '这一步做什么'} data-testid="step-action"
        onChange={(e) => onChange({ action: e.target.value })} />
      {step.basis === '专家补充' && (
        <input style={{ ...inputStyle, marginTop: '6px', ...(!(step.expert_reason || '').trim() ? tbdStyle : {}) }}
          value={step.expert_reason || ''} placeholder="专家补充的理由（必填），例如：项目惯例、制度未写明的经验"
          data-testid="step-expert-reason" onChange={(e) => onChange({ expert_reason: e.target.value })} />
      )}
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap', marginTop: '8px' }}>
        <Link2 size={13} color="var(--text-muted)" />
        {step.refs.length === 0 && <span style={smallLabel}>没有依据知识</span>}
        {step.refs.map((vid) => (
          <span key={vid} className="zx-tag neutral" style={{ display: 'inline-flex', alignItems: 'center', gap: '4px', maxWidth: '260px' }}>
            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{titleOf(vid)}</span>
            <button type="button" style={{ ...iconButton, padding: 0 }} aria-label="移除依据"
              onClick={() => onChange({ refs: step.refs.filter((r) => r !== vid) })}><X size={12} /></button>
          </span>
        ))}
        {available.length > 0 && (
          <select style={{ ...selectSm, maxWidth: '200px' }} value="" aria-label="添加已引用的知识"
            onChange={(e) => {
              if (!e.target.value) return;
              onChange({ refs: [...step.refs, e.target.value], basis: step.basis === '无依据' || step.basis === '通用操作' ? '有原子依据' : step.basis });
            }}>
            <option value="">＋ 用已引用的知识</option>
            {available.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        )}
        <button type="button" className="btn-ghost btn-sm" onClick={onPick} data-testid="step-pick">从知识库添加</button>
      </div>
    </div>
  );
};

const IoEditor: React.FC<{
  fields: SkillIoField[];
  baseKeys: Set<string>;
  refOptions: { value: string; label: string }[];
  selectedAtom: string | null;
  prefix: 'inputs' | 'outputs';
  flaggedPaths: Set<string>;
  onChange: (fields: SkillIoField[]) => void;
  testId: string;
}> = ({ fields, baseKeys, refOptions, selectedAtom, prefix, flaggedPaths, onChange, testId }) => {
  const update = (idx: number, patch: Partial<SkillIoField>) => onChange(fields.map((f, i) => (i === idx ? { ...f, ...patch } : f)));
  const cellInput: React.CSSProperties = { ...inputStyle, height: '28px', fontSize: 'var(--font-size-xs)' };
  const newKey = () => {
    let n = fields.length + 1;
    const keys = new Set(fields.map((f) => f.key));
    while (keys.has(`field_${n}`) || baseKeys.has(`field_${n}`)) n += 1;
    return `field_${n}`;
  };
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }} data-testid={testId}>
      {fields.map((f, idx) => {
        const flagged = [...flaggedPaths].some((p) => p.startsWith(`${prefix}[${f.key}]`));
        return (
          <div key={idx} data-testid="io-row" data-key={f.key}
            style={{ display: 'grid', gridTemplateColumns: '110px minmax(0, 1.4fr) 88px 56px minmax(0, 1fr) minmax(0, 1fr) auto', gap: '6px', alignItems: 'center',
              padding: '4px', borderRadius: 'var(--radius-sm)',
              backgroundColor: selectedAtom && f.source_ref === selectedAtom ? 'var(--brand-50)' : flagged ? 'var(--warning-bg)' : undefined }}>
            <input style={cellInput} value={f.key} disabled={baseKeys.has(f.key)} title={baseKeys.has(f.key) ? '已有字段的键不能修改' : '字段键'}
              onChange={(e) => update(idx, { key: e.target.value.replace(/\s+/g, '_') })} aria-label="字段键" />
            <input style={cellInput} value={f.label} placeholder="显示名" onChange={(e) => update(idx, { label: e.target.value })} aria-label="显示名" />
            <select style={cellInput} value={f.type} aria-label="类型"
              onChange={(e) => update(idx, { type: e.target.value as SkillIoField['type'] })}>
              {IO_TYPES.map((t) => <option key={t} value={t}>{IO_TYPE_LABELS[t]}</option>)}
            </select>
            <label style={{ display: 'inline-flex', alignItems: 'center', gap: '3px', fontSize: 'var(--font-size-xs)', color: 'var(--text-secondary)' }}>
              <input type="checkbox" checked={!!f.required} onChange={(e) => update(idx, { required: e.target.checked })} />必需
            </label>
            {f.type === 'number' ? (
              <input style={{ ...cellInput, ...(!(f.unit || '').trim() ? tbdStyle : {}) }} value={f.unit || ''} placeholder="单位（必填）"
                onChange={(e) => update(idx, { unit: e.target.value })} aria-label="单位" data-testid="io-unit" />
            ) : f.type === 'enum' ? (
              <input style={cellInput} value={(f.allowed_values || []).join('、')} placeholder="可选值，用顿号分隔"
                onChange={(e) => update(idx, { allowed_values: e.target.value.split(/[、,，]/).map((v) => v.trim()).filter(Boolean) })} aria-label="可选值" />
            ) : <span style={smallLabel}>—</span>}
            <select style={cellInput} value={f.source_ref || ''} aria-label="口径依据"
              onChange={(e) => update(idx, { source_ref: e.target.value || null })}>
              <option value="">口径依据：无</option>
              {refOptions.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
            <span style={{ display: 'inline-flex' }}>
              <MoveButtons index={idx} total={fields.length} onMove={(to) => onChange(moveItem(fields, idx, to))} />
              <button type="button" style={iconButton} aria-label="删除字段" onClick={() => onChange(fields.filter((_, i) => i !== idx))}><X size={14} /></button>
            </span>
          </div>
        );
      })}
      <div><button type="button" className="btn-ghost btn-sm"
        onClick={() => onChange([...fields, { key: newKey(), label: '', type: 'text', required: true }])}><Plus size={13} />添加字段</button></div>
    </div>
  );
};
