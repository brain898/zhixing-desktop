import React from 'react';
import { ArrowDown, ArrowUp, Plus, X } from 'lucide-react';
import { SkillContent, SkillIoField, SkillStep } from '../../../types';

/** M02-D 审核：共享常量、文案转换与小组件 */

export const TBD = 'TBD_EXPERT';

// 界面不出现「原子」等术语（M02-B 交接说明第 12 节）；服务端与 Schema 取值保持原样，只在展示时转换
// 模型写的说明里常夹着 Schema 字段名，展示时换成中文（先替换更长的名称）
const FIELD_WORDS: [RegExp, string][] = [
  [/not_applies_to/g, '不适用范围'],
  [/applies_to/g, '适用范围'],
  [/risk_boundary/g, '风险边界'],
  [/escalation_conditions/g, '人工升级条件'],
  [/knowledge_refs/g, '知识引用'],
  [/used_in_steps/g, '使用步骤'],
  [/preconditions/g, '前置条件'],
  [/trigger_description/g, '触发描述'],
  [/output_template/g, '输出模板'],
  [/generation_confidence/g, '生成把握度'],
  [/unsupported_items/g, '无依据项'],
  [/metric_definition/g, '指标口径'],
  [/source_ref/g, '口径依据'],
];

export const plainText = (text: unknown): string => {
  if (text === null || text === undefined) return '';
  let value = typeof text === 'string' ? text : JSON.stringify(text);
  for (const [re, word] of FIELD_WORDS) value = value.replace(re, word);
  return value.replace(/TBD_EXPERT/g, '待专家补充').replace(/原子/g, '知识');
};

export const TASK_TYPES = ['计算核对', '判断分级', '流程指引', '诊断建议'];
export const STEP_KINDS = ['输入校验', '知识检索', '计算', '规则判断', '生成表达', '人工确认'] as const;
export const ON_FAIL = ['补问', '暂停', '转人工', '返回不可计算'] as const;
export const IO_TYPES = ['text', 'number', 'boolean', 'enum', 'date', 'period', 'file'] as const;
export const IO_TYPE_LABELS: Record<string, string> = {
  text: '文本', number: '数值', boolean: '是 / 否', enum: '选项', date: '日期', period: '期间', file: '文件',
};
export const REF_ROLES = ['前置条件', '判断规则', '执行动作', '例外处理', '指标口径', '案例参考'];
export const BASIS_OPTIONS = [
  { value: '有原子依据', label: '有知识依据' },
  { value: '无依据', label: '无依据' },
  { value: '通用操作', label: '通用操作' },
  { value: '专家补充', label: '专家补充' },
] as const;
export const basisLabel = (value: string) => BASIS_OPTIONS.find((b) => b.value === value)?.label || value;
// 服务端规则：「通用操作」只允许用于这两类步骤
export const GENERIC_STEP_KINDS = ['输入校验', '人工确认'];
export const ON_FAIL_TEXT: Record<string, string> = {
  补问: '信息不全时追问', 暂停: '出问题时暂停', 转人工: '出问题时转人工', 返回不可计算: '算不了就返回「不可计算」',
};
// 问题类型的界面说法；data-kind 仍用服务端原值
export const PROBLEM_KIND_TEXT: Record<string, string> = {
  待专家补充: '待你补充', 无依据步骤: '没有出处', 疑似无依据数值: '数字没有出处', 例外未落点: '特殊情况没对应',
};

/** 审核页用「第 N 步」代替系统步骤编号（编号只用于版本对齐）；找不到的编号原样保留 */
export const stepLabel = (order: Map<string, number>, sid: string) => {
  const n = order.get(sid);
  return n ? `第 ${n} 步` : `步骤 ${sid}`;
};
export const humanizeSteps = (text: string, order: Map<string, number>) =>
  text.replace(/步骤\s*(s\d+)/g, (_m, sid: string) => stepLabel(order, sid));

export const STATUS_TONE: Record<string, string> = {
  pending_review: 'warning',
  validation_failed: 'danger',
  generating: 'neutral',
  approved: '',
  rejected: 'neutral',
  needs_recheck: 'warning',
};

export const UNSUPPORTED_RESOLUTION_TEXT: Record<string, string> = {
  delete: '删除',
  add_ref: '补依据',
  expert: '专家补充',
  landed: '已补落点',
  edited: '修改后消除',
};

export const GROUP_LABELS: Record<string, string> = {
  A: '身份与边界',
  B: '知识依赖',
  C: '输入输出',
  D: '执行步骤',
  E: '风险与人工升级',
  G: '维护信息',
};

export const formatTime = (iso: string | null | undefined): string => {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getMonth() + 1}月${d.getDate()}日 ${pad(d.getHours())}:${pad(d.getMinutes())}`;
};

export const saveBlob = (blob: Blob, filename: string) => {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
};

export const inputStyle: React.CSSProperties = {
  width: '100%',
  height: '32px',
  padding: '0 10px',
  fontSize: 'var(--font-size-sm)',
  border: '1px solid var(--border-color)',
  borderRadius: 'var(--radius-sm)',
  backgroundColor: 'var(--bg-primary)',
  color: 'var(--text-primary)',
  boxSizing: 'border-box',
};

export const textAreaStyle: React.CSSProperties = {
  ...inputStyle,
  height: 'auto',
  minHeight: '56px',
  padding: '6px 10px',
  lineHeight: 1.6,
  resize: 'vertical',
  fontFamily: 'inherit',
};

export const tbdStyle: React.CSSProperties = {
  backgroundColor: 'var(--warning-bg)',
  borderColor: 'var(--warning-border)',
};

export const smallLabel: React.CSSProperties = {
  fontSize: 'var(--font-size-xs)',
  color: 'var(--text-muted)',
};

export const tableCellStyle: React.CSSProperties = {
  padding: '10px 12px', borderBottom: '1px solid var(--border-color)',
  fontSize: 'var(--font-size-sm)', textAlign: 'left', verticalAlign: 'middle',
};
export const tableHeadStyle: React.CSSProperties = {
  ...tableCellStyle, fontSize: 'var(--font-size-xs)', fontWeight: 600,
  color: 'var(--text-muted)', backgroundColor: 'var(--bg-secondary)',
};

export const iconButton: React.CSSProperties = {
  border: 'none',
  background: 'none',
  color: 'var(--text-muted)',
  cursor: 'pointer',
  padding: '4px',
  display: 'inline-flex',
  alignItems: 'center',
};

export const FieldBlock: React.FC<{
  label: string;
  hint?: string;
  highlight?: boolean;
  children: React.ReactNode;
  testId?: string;
}> = ({ label, hint, highlight, children, testId }) => (
  <div
    data-testid={testId}
    data-highlight={highlight ? 'true' : undefined}
    style={{
      display: 'flex',
      flexDirection: 'column',
      gap: '6px',
      padding: highlight ? '8px' : 0,
      margin: highlight ? '0 -8px' : 0,
      borderRadius: 'var(--radius-sm)',
      backgroundColor: highlight ? 'var(--brand-50)' : undefined,
      transition: 'background-color 160ms ease',
    }}
  >
    <div style={{ display: 'flex', alignItems: 'baseline', gap: '8px' }}>
      <span style={{ fontSize: 'var(--font-size-sm)', fontWeight: 500, color: 'var(--text-secondary)' }}>{label}</span>
      {hint && <span style={smallLabel}>{hint}</span>}
    </div>
    {children}
  </div>
);

/** 字符串列表编辑：TBD_EXPERT 显示为空输入框并高亮，提示「待专家补充」 */
export const StringListEditor: React.FC<{
  items: string[];
  onChange: (items: string[]) => void;
  placeholder?: string;
  addText?: string;
  disabled?: boolean;
  testId?: string;
}> = ({ items, onChange, placeholder = '请输入', addText = '添加一条', disabled, testId }) => (
  <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }} data-testid={testId}>
    {items.map((item, idx) => {
      const tbd = item === TBD;
      return (
        <div key={idx} style={{ display: 'flex', gap: '6px', alignItems: 'center' }}>
          <input
            type="text"
            value={tbd ? '' : item}
            placeholder={tbd ? '待专家补充（通过前必须填写）' : placeholder}
            data-tbd={tbd ? 'true' : undefined}
            disabled={disabled}
            onChange={(e) => {
              const next = [...items];
              next[idx] = e.target.value;
              onChange(next);
            }}
            style={{ ...inputStyle, ...(tbd ? tbdStyle : {}) }}
          />
          {!disabled && (
            <button type="button" style={iconButton} title="删除这一条" aria-label="删除这一条"
              onClick={() => onChange(items.filter((_, i) => i !== idx))}>
              <X size={14} />
            </button>
          )}
        </div>
      );
    })}
    {items.length === 0 && <div style={smallLabel}>暂无</div>}
    {!disabled && (
      <div>
        <button type="button" className="btn-ghost btn-sm" onClick={() => onChange([...items, ''])}>
          <Plus size={13} />{addText}
        </button>
      </div>
    )}
  </div>
);

export const MoveButtons: React.FC<{ index: number; total: number; onMove: (to: number) => void }> = ({ index, total, onMove }) => (
  <>
    <button type="button" style={iconButton} disabled={index === 0} title="上移" aria-label="上移" onClick={() => onMove(index - 1)}>
      <ArrowUp size={14} />
    </button>
    <button type="button" style={iconButton} disabled={index === total - 1} title="下移" aria-label="下移" onClick={() => onMove(index + 1)}>
      <ArrowDown size={14} />
    </button>
  </>
);

export const moveItem = <T,>(list: T[], from: number, to: number): T[] => {
  if (to < 0 || to >= list.length) return list;
  const next = [...list];
  const [item] = next.splice(from, 1);
  next.splice(to, 0, item);
  return next;
};

export const ModalShell: React.FC<{
  title: string;
  label?: string;
  subtitle?: React.ReactNode;
  width?: number;
  height?: number;
  busy?: boolean;
  bodyStyle?: React.CSSProperties;
  testId?: string;
  onClose: () => void;
  footer?: React.ReactNode;
  children: React.ReactNode;
}> = ({ title, label, subtitle, width = 560, height, busy, bodyStyle, testId, onClose, footer, children }) => (
  <div className="zx-modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && !busy && onClose()}>
    <div className="zx-modal" role="dialog" aria-modal="true" aria-label={label || title} style={{ width: `${width}px`, height, maxWidth: '94vw' }} data-testid={testId}>
      <div className="zx-modal-header">
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="zx-modal-title">{title}</div>
          {subtitle && <div style={{ ...smallLabel, marginTop: '2px' }}>{subtitle}</div>}
        </div>
        <button type="button" className="btn-ghost btn-sm btn-icon" onClick={onClose} disabled={busy} aria-label="关闭"><X size={16} /></button>
      </div>
      <div className="zx-modal-body" style={{ padding: '16px 24px 20px', ...bodyStyle }}>{children}</div>
      {footer && <div className="zx-modal-footer" style={{ justifyContent: 'flex-end' }}>{footer}</div>}
    </div>
  </div>
);

const asList = <T,>(value: unknown): T[] => (Array.isArray(value) ? (value as T[]) : []);

/**
 * 补齐 Schema 中可省略或模型可能漏写的数组字段（如通用操作步骤不写 refs），
 * 界面各处可直接按数组处理；只补空数组，不改变已有取值。
 */
export const normalizeContent = (raw: SkillContent): SkillContent => {
  const applies = (raw.applies_to || {}) as Partial<SkillContent['applies_to']>;
  return {
    ...raw,
    applies_to: {
      customer_types: asList<string>(applies.customer_types),
      property_types: asList<string>(applies.property_types),
      conditions: asList<string>(applies.conditions),
    },
    not_applies_to: asList<string>(raw.not_applies_to),
    knowledge_refs: asList<SkillContent['knowledge_refs'][number]>(raw.knowledge_refs)
      .map((r) => ({ ...r, used_in_steps: asList<string>(r.used_in_steps) })),
    inputs: asList<SkillIoField>(raw.inputs),
    preconditions: asList<SkillContent['preconditions'][number]>(raw.preconditions),
    outputs: asList<SkillIoField>(raw.outputs),
    steps: asList<SkillStep>(raw.steps).map((s) => ({ ...s, refs: asList<string>(s.refs) })),
    risk_boundary: asList<string>(raw.risk_boundary),
    escalation_conditions: asList<string>(raw.escalation_conditions),
  };
};

/** 渲染出错时只影响审核区域，不让整个应用白屏 */
export class ReviewErrorBoundary extends React.Component<
  { onBack: () => void; children: React.ReactNode },
  { error: Error | null }
> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error) {
    console.error('Skill 审核页面渲染出错', error);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div style={{ padding: '24px 32px', display: 'flex', flexDirection: 'column', gap: '12px', maxWidth: '640px' }} data-testid="review-error-boundary">
        <div style={{ fontSize: 'var(--font-size-section)', fontWeight: 600 }}>这个页面显示出错了</div>
        <div style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-secondary)', lineHeight: 1.6 }}>
          内容没有被修改。可以返回后重新打开；如果仍然出错，请把下面的错误信息发给开发同学。
        </div>
        <code style={{ fontSize: 'var(--font-size-xs)', color: 'var(--danger-text)', backgroundColor: 'var(--danger-bg)', padding: '8px 10px', borderRadius: 'var(--radius-sm)', whiteSpace: 'pre-wrap' }}>
          {this.state.error.message}
        </code>
        <div>
          <button type="button" className="btn-secondary" onClick={() => { this.setState({ error: null }); this.props.onBack(); }}>返回</button>
        </div>
      </div>
    );
  }
}

/** 把 Schema 取值转成可读文字（差异对照、修改清单用） */
export const readableValue = (value: unknown): string => {
  if (value === null || value === undefined || value === '') return '（空）';
  if (typeof value === 'boolean') return value ? '是' : '否';
  if (typeof value === 'string') return plainText(value);
  if (Array.isArray(value) && value.every((v) => typeof v === 'string')) return plainText((value as string[]).join('；'));
  if (typeof value === 'object') {
    const obj = value as Record<string, unknown>;
    if (typeof obj.action === 'string' && typeof obj.step_id === 'string') {
      return `${obj.step_id} ${plainText(obj.kind)}：${plainText(obj.action)}（${basisLabel(String(obj.basis || ''))}）`;
    }
    if (typeof obj.key === 'string' && typeof obj.label === 'string') {
      return `${obj.label}（${IO_TYPE_LABELS[String(obj.type)] || obj.type}${obj.unit ? `，${obj.unit}` : ''}${obj.required ? '，必需' : ''}）`;
    }
    if (typeof obj.text === 'string') return plainText(obj.text);
  }
  return plainText(value);
};

/** M02-E：受影响字段的中文位置说明（稳定路径 -> 界面文字，不出现字段名） */
export const describePathText = (path: string): string => {
  const step = /^steps\[([^\]]+)\]/.exec(path);
  if (step) return `步骤 ${step[1]}`;
  if (path.startsWith('preconditions[')) return '前置条件';
  const io = /^(inputs|outputs)\[([^\]]+)\]/.exec(path);
  if (io) return `${io[1] === 'inputs' ? '输入' : '输出'}「${io[2]}」的口径依据`;
  if (path.startsWith('knowledge_refs[')) return '依据知识列表';
  return plainText(path);
};
