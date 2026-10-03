import React, { useEffect, useRef } from 'react';
import { AlertTriangle, HelpCircle, Loader2 } from 'lucide-react';

export interface AppConfirmDialogProps {
  isOpen: boolean;
  title: string;
  variant?: 'danger' | 'primary';
  targetName?: string;
  targetList?: string[];
  impactDescription?: React.ReactNode;
  description?: React.ReactNode;
  confirmText?: string;
  cancelText?: string;
  loading?: boolean;
  initialFocus?: 'cancel' | 'confirm';
  onConfirm: () => void | Promise<void>;
  onCancel: () => void;
}

export const AppConfirmDialog: React.FC<AppConfirmDialogProps> = ({
  isOpen,
  title,
  variant = 'primary',
  targetName,
  targetList,
  impactDescription,
  description,
  confirmText,
  cancelText = '取消',
  loading = false,
  initialFocus = variant === 'danger' ? 'cancel' : 'confirm',
  onConfirm,
  onCancel,
}) => {
  const dialogPanelRef = useRef<HTMLDivElement>(null);
  const cancelBtnRef = useRef<HTMLButtonElement>(null);
  const confirmBtnRef = useRef<HTMLButtonElement>(null);
  const previouslyFocusedElementRef = useRef<HTMLElement | null>(null);
  const confirmLockRef = useRef(false);

  useEffect(() => {
    if (!isOpen) {
      confirmLockRef.current = false;
    }
  }, [isOpen]);

  const handleConfirmClick = async () => {
    if (loading || confirmLockRef.current) return;
    confirmLockRef.current = true;
    try {
      await onConfirm();
    } finally {
      confirmLockRef.current = false;
    }
  };

  const getFocusableElements = (): HTMLElement[] => {
    if (!dialogPanelRef.current) return [];
    const selector = [
      'button:not([disabled])',
      '[href]',
      'input:not([disabled])',
      'select:not([disabled])',
      'textarea:not([disabled])',
      '[tabindex]:not([tabindex="-1"])',
    ].join(', ');
    return Array.from(dialogPanelRef.current.querySelectorAll<HTMLElement>(selector)).filter(
      (el) => el.offsetParent !== null || el.offsetWidth > 0 || el.offsetHeight > 0
    );
  };

  // 记录先前的焦点元素，关闭后安全恢复
  useEffect(() => {
    if (!isOpen) return;

    previouslyFocusedElementRef.current = document.activeElement as HTMLElement | null;

    const timer = window.setTimeout(() => {
      if (loading) {
        dialogPanelRef.current?.focus();
        return;
      }
      const targetBtn = initialFocus === 'cancel' ? cancelBtnRef.current : confirmBtnRef.current;
      if (targetBtn && !targetBtn.disabled) {
        targetBtn.focus();
      } else {
        const focusables = getFocusableElements();
        if (focusables.length > 0) {
          focusables[0].focus();
        } else {
          dialogPanelRef.current?.focus();
        }
      }
    }, 30);

    return () => {
      window.clearTimeout(timer);
      const prev = previouslyFocusedElementRef.current;
      if (prev && typeof prev.focus === 'function' && document.contains(prev)) {
        try {
          prev.focus();
        } catch (_) {}
      }
    };
  }, [isOpen, initialFocus, loading]);

  // 键盘支持：Tab 焦点循环与最顶层 Escape 消费拦截
  useEffect(() => {
    if (!isOpen) return;

    const handleKeyDownCapture = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        e.stopImmediatePropagation();
        if (!loading) {
          onCancel();
        }
        return;
      }

      if (e.key === 'Tab') {
        e.stopPropagation();
        const focusables = getFocusableElements();

        if (focusables.length === 0 || loading) {
          e.preventDefault();
          dialogPanelRef.current?.focus();
          return;
        }

        const first = focusables[0];
        const last = focusables[focusables.length - 1];
        const active = document.activeElement as HTMLElement;

        if (e.shiftKey) {
          if (active === first || !focusables.includes(active)) {
            e.preventDefault();
            last.focus();
          }
        } else {
          if (active === last || !focusables.includes(active)) {
            e.preventDefault();
            first.focus();
          }
        }
      }
    };

    window.addEventListener('keydown', handleKeyDownCapture, true);
    return () => window.removeEventListener('keydown', handleKeyDownCapture, true);
  }, [isOpen, loading, onCancel]);

  if (!isOpen) return null;

  const isDanger = variant === 'danger';
  const resolvedConfirmText = confirmText || (isDanger ? '删除文件' : '确定');

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={title}
      className="modal-backdrop-animate"
      style={{
        position: 'fixed',
        inset: 0,
        backgroundColor: 'rgba(18, 26, 22, 0.42)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 2000,
        padding: '16px',
        userSelect: 'none',
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget && !loading) {
          onCancel();
        }
      }}
    >
      <div
        ref={dialogPanelRef}
        tabIndex={-1}
        className="modal-panel-animate"
        style={{
          width: '460px',
          maxWidth: '92vw',
          backgroundColor: '#FFFFFF',
          borderRadius: 'var(--radius-lg)',
          boxShadow: 'var(--shadow-lg)',
          border: '1px solid var(--border-color)',
          padding: '24px',
          display: 'flex',
          flexDirection: 'column',
          gap: '16px',
          outline: 'none',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* 头部：图标与标题 */}
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: '14px' }}>
          <div
            style={{
              width: '38px',
              height: '38px',
              borderRadius: '50%',
              backgroundColor: isDanger ? 'var(--error-bg)' : 'var(--bg-selected)',
              color: isDanger ? 'var(--error-text)' : 'var(--brand-accent)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              flexShrink: 0,
            }}
          >
            {isDanger ? <AlertTriangle size={20} /> : <HelpCircle size={20} />}
          </div>

          <div style={{ flex: 1, minWidth: 0 }}>
            <h3
              style={{
                fontSize: '16px',
                fontWeight: 600,
                color: 'var(--text-primary)',
                lineHeight: 1.3,
                margin: 0,
              }}
            >
              {title}
            </h3>

            {/* 描述说明 */}
            {description && (
              <div
                style={{
                  fontSize: '13px',
                  color: 'var(--text-secondary)',
                  marginTop: '6px',
                  lineHeight: 1.5,
                }}
              >
                {description}
              </div>
            )}
          </div>
        </div>

        {/* 操作对象展示 */}
        {(targetName || (targetList && targetList.length > 0)) && (
          <div
            style={{
              backgroundColor: 'var(--bg-secondary)',
              border: '1px solid var(--border-color)',
              borderRadius: 'var(--radius-sm)',
              padding: '10px 12px',
              fontSize: '13px',
              color: 'var(--text-primary)',
              lineHeight: 1.5,
              wordBreak: 'break-word',
              maxHeight: '130px',
              overflowY: 'auto',
            }}
          >
            {targetName && <div>操作对象：<strong>{targetName}</strong></div>}
            {targetList && targetList.length > 0 && (
              <div>
                <div style={{ fontWeight: 600, marginBottom: '4px', color: 'var(--text-secondary)' }}>
                  已选文件 ({targetList.length})：
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '3px' }}>
                  {targetList.slice(0, 5).map((name, i) => (
                    <div key={i} style={{ color: 'var(--text-primary)' }}>
                      • {name}
                    </div>
                  ))}
                  {targetList.length > 5 && (
                    <div style={{ color: 'var(--text-muted)', fontSize: '12px' }}>
                      … 以及另外 {targetList.length - 5} 个文件
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        )}

        {/* 真实影响提示 */}
        {impactDescription && (
          <div
            style={{
              fontSize: 'var(--font-size-xs)',
              color: 'var(--text-secondary)',
              backgroundColor: isDanger ? 'var(--warning-bg)' : 'var(--bg-secondary)',
              border: isDanger ? '1px solid var(--warning-border)' : '1px solid var(--border-color)',
              padding: '10px 12px',
              borderRadius: 'var(--radius-sm)',
              lineHeight: 1.55,
            }}
          >
            {impactDescription}
          </div>
        )}

        {/* 操作按钮组 */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'flex-end',
            gap: '10px',
            marginTop: '4px',
          }}
        >
          <button
            ref={cancelBtnRef}
            type="button"
            className="btn-secondary"
            disabled={loading}
            onClick={onCancel}
            style={{
              height: '34px',
              padding: '0 16px',
              fontSize: '13px',
            }}
          >
            {cancelText}
          </button>
          <button
            ref={confirmBtnRef}
            type="button"
            disabled={loading}
            onClick={handleConfirmClick}
            style={{
              height: '34px',
              padding: '0 16px',
              fontSize: '13px',
              fontWeight: 500,
              borderRadius: 'var(--radius-md)',
              border: 'none',
              backgroundColor: isDanger ? 'var(--danger-text)' : 'var(--brand-accent)',
              color: '#FFFFFF',
              cursor: loading ? 'not-allowed' : 'pointer',
              display: 'inline-flex',
              alignItems: 'center',
              gap: '6px',
              transition: 'background-color 140ms ease',
            }}
            onMouseEnter={(e) => {
              if (!loading) {
                e.currentTarget.style.backgroundColor = isDanger ? 'var(--danger-text)' : 'var(--brand-hover)';
              }
            }}
            onMouseLeave={(e) => {
              if (!loading) {
                e.currentTarget.style.backgroundColor = isDanger ? 'var(--danger-text)' : 'var(--brand-accent)';
              }
            }}
          >
            {loading && <Loader2 size={14} className="spin-slow" />}
            <span>{resolvedConfirmText}</span>
          </button>
        </div>
      </div>
    </div>
  );
};
