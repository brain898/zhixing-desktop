import React, { useEffect, useRef, useState } from 'react';
import { Loader2, X } from 'lucide-react';
import { SceneFormPayload } from '../../../types';
import { ModalShell } from './skillReviewShared';

export interface ActionMenuItem {
  label: string;
  onSelect: () => void;
  danger?: boolean;
}

interface ActionMenuProps {
  /** 触发按钮的内容 */
  trigger: React.ReactNode;
  /** 触发按钮的可访问名称 */
  label: string;
  items: ActionMenuItem[];
  triggerClassName?: string;
  triggerStyle?: React.CSSProperties;
  align?: 'left' | 'right';
  disabled?: boolean;
}

/** 点击后弹出的操作菜单，避免把低频操作全部铺在界面上 */
export const ActionMenu: React.FC<ActionMenuProps> = ({
  trigger,
  label,
  items,
  triggerClassName,
  triggerStyle,
  align = 'left',
  disabled,
}) => {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false);
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', esc);
    return () => {
      document.removeEventListener('mousedown', close);
      document.removeEventListener('keydown', esc);
    };
  }, [open]);

  return (
    <span ref={ref} style={{ position: 'relative', display: 'inline-flex' }}>
      <button
        type="button"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={disabled}
        className={triggerClassName}
        style={triggerStyle}
        onClick={() => setOpen((v) => !v)}
      >
        {trigger}
      </button>
      {open && (
        <div
          role="menu"
          style={{
            position: 'absolute',
            top: 'calc(100% + 4px)',
            [align]: 0,
            zIndex: 60,
            minWidth: '180px',
            padding: '4px',
            borderRadius: 'var(--radius-md)',
            backgroundColor: 'var(--bg-primary)',
            boxShadow: 'var(--shadow-lg)',
          }}
        >
          {items.map((item) => (
            <button
              key={item.label}
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                item.onSelect();
              }}
              style={{
                display: 'block',
                width: '100%',
                textAlign: 'left',
                padding: '7px 10px',
                borderRadius: 'var(--radius-sm)',
                fontSize: 'var(--font-size-sm)',
                color: item.danger ? 'var(--danger-text)' : 'var(--text-primary)',
                whiteSpace: 'nowrap',
              }}
              onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = 'var(--bg-hover)')}
              onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = 'transparent')}
            >
              {item.label}
            </button>
          ))}
        </div>
      )}
    </span>
  );
};

/** 五类主分类的简称，用于卡片上的「五类覆盖」 */
export const CATEGORY_SHORT: Record<string, string> = {
  '制度与标准': '制度',
  '方法与工具': '方法',
  '项目案例': '案例',
  '指标数据': '指标',
  '专家经验': '经验',
};

export const formatCoverage = (coverage: Record<string, number>): string => {
  const parts = Object.entries(coverage)
    .filter(([, n]) => n > 0)
    .map(([cat, n]) => `${CATEGORY_SHORT[cat] || cat} ${n}`);
  return parts.length ? parts.join(' · ') : '暂无';
};


/** 文本框输入拆为列表：换行、中英文逗号、顿号、分号均可分隔 */
export const splitList = (text: string): string[] => {
  const seen = new Set<string>();
  const out: string[] = [];
  text
    .split(/[\n,，、;；]+/)
    .map((s) => s.trim())
    .filter(Boolean)
    .forEach((s) => {
      const key = s.replace(/\s+/g, '').toLowerCase();
      if (!seen.has(key)) {
        seen.add(key);
        out.push(s);
      }
    });
  return out;
};

export const errorMessage = (err: unknown, fallback: string): string =>
  err instanceof Error && err.message ? err.message : fallback;

export const ErrorNotice: React.FC<{ message: string; onClose?: () => void }> = ({ message, onClose }) => (
  <div
    role="alert"
    style={{
      display: 'flex',
      alignItems: 'flex-start',
      gap: '8px',
      padding: '8px 12px',
      borderRadius: 'var(--radius-sm)',
      backgroundColor: 'var(--danger-bg)',
      border: '1px solid var(--danger-border)',
      color: 'var(--danger-text)',
      fontSize: 'var(--font-size-sm)',
      lineHeight: 1.6,
    }}
  >
    <span style={{ flex: 1 }}>{message}</span>
    {onClose && (
      <button type="button" className="btn-ghost btn-sm btn-icon" style={{ width: '24px', height: '24px' }} onClick={onClose} aria-label="关闭提示">
        <X size={14} />
      </button>
    )}
  </div>
);

export const fieldLabelStyle: React.CSSProperties = {
  display: 'block',
  fontSize: 'var(--font-size-xs)',
  fontWeight: 600,
  color: 'var(--text-secondary)',
  marginBottom: '6px',
};

export const textAreaStyle: React.CSSProperties = {
  width: '100%',
  padding: '8px 10px',
  borderRadius: 'var(--radius-sm)',
  border: '1px solid var(--border-strong)',
  backgroundColor: 'var(--bg-primary)',
  color: 'var(--text-primary)',
  fontSize: 'var(--font-size-sm)',
  lineHeight: 1.6,
  resize: 'vertical',
  fontFamily: 'inherit',
};

interface SceneFormModalProps {
  isOpen: boolean;
  title: string;
  initial: Partial<SceneFormPayload>;
  submitText: string;
  onSubmit: (payload: SceneFormPayload) => Promise<void>;
  onCancel: () => void;
}

/** 新建或编辑场景：名称、说明、别名、典型问题（PRD 5.2） */
export const SceneFormModal: React.FC<SceneFormModalProps> = ({ isOpen, title, initial, submitText, onSubmit, onCancel }) => {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [aliasesText, setAliasesText] = useState('');
  const [problemsText, setProblemsText] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!isOpen) return;
    setName(initial.name || '');
    setDescription(initial.description || '');
    setAliasesText((initial.aliases || []).join('\n'));
    setProblemsText((initial.typical_problems || []).join('\n'));
    setError(null);
    setSaving(false);
  }, [isOpen, initial]);

  if (!isOpen) return null;

  const handleSubmit = async () => {
    if (!name.trim()) {
      setError('场景名称不能为空');
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await onSubmit({
        name: name.trim(),
        description: description.trim(),
        aliases: splitList(aliasesText),
        typical_problems: problemsText.split('\n').map((s) => s.trim()).filter(Boolean),
        revision_token: initial.revision_token,
      });
    } catch (err) {
      setError(errorMessage(err, '保存失败'));
      setSaving(false);
    }
  };

  return (
    <ModalShell title={title} width={520} onClose={onCancel} busy={saving}
      bodyStyle={{ padding: '20px 24px', display: 'flex', flexDirection: 'column', gap: '14px' }}
      footer={<>
        <button type="button" className="btn-secondary" onClick={onCancel} disabled={saving}>取消</button>
        <button type="button" className="btn-primary" onClick={handleSubmit} disabled={saving}>
          {saving && <Loader2 size={14} className="spin-slow" />}
          {saving ? '正在保存…' : submitText}
        </button>
      </>}>
      {error && <ErrorNotice message={error} />}
      <div>
        <label style={fieldLabelStyle} htmlFor="scene-form-name">场景名称</label>
        <input id="scene-form-name" className="zx-input" value={name} maxLength={30} onChange={(e) => setName(e.target.value)} placeholder="如：防汛管理" />
      </div>
      <div>
        <label style={fieldLabelStyle} htmlFor="scene-form-desc">一句话说明这个场景管什么</label>
        <textarea id="scene-form-desc" style={textAreaStyle} rows={2} maxLength={300} value={description} onChange={(e) => setDescription(e.target.value)} placeholder="如：汛前检查、排水设施与积水处置" />
      </div>
      <div>
        <label style={fieldLabelStyle} htmlFor="scene-form-aliases">包含的标签（知识带有其中任一标签，就归到这个场景；每行一个）</label>
        <textarea id="scene-form-aliases" style={textAreaStyle} rows={3} value={aliasesText} onChange={(e) => setAliasesText(e.target.value)} />
      </div>
      <div>
        <label style={fieldLabelStyle} htmlFor="scene-form-problems">常见问题（选填，帮助系统找到相关知识；每行一个）</label>
        <textarea id="scene-form-problems" style={textAreaStyle} rows={3} value={problemsText} onChange={(e) => setProblemsText(e.target.value)} placeholder="如：汛前要检查哪些设施" />
      </div>
    </ModalShell>
  );
};
