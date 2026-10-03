import React from 'react';
import { AlertCircle } from 'lucide-react';

interface UnsavedChangesModalProps {
  isOpen: boolean;
  onSave: () => void;
  onDiscard: () => void;
  onKeepEditing: () => void;
  saving?: boolean;
}

export const UnsavedChangesModal: React.FC<UnsavedChangesModalProps> = ({
  isOpen,
  onSave,
  onDiscard,
  onKeepEditing,
  saving = false,
}) => {
  if (!isOpen) return null;

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        backgroundColor: 'rgba(18, 26, 22, 0.42)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 1100,
        userSelect: 'none',
      }}
    >
      <div
        style={{
          width: '420px',
          backgroundColor: '#FFFFFF',
          borderRadius: 'var(--radius-lg)',
          boxShadow: 'var(--shadow-lg)',
          padding: '24px',
          display: 'flex',
          flexDirection: 'column',
          gap: '16px',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: '14px' }}>
          <div
            style={{
              width: '36px',
              height: '36px',
              borderRadius: '50%',
              backgroundColor: 'var(--warning-bg)',
              color: 'var(--warning-text)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              flexShrink: 0,
            }}
          >
            <AlertCircle size={20} />
          </div>

          <div>
            <div style={{ fontSize: '15px', fontWeight: 600, color: 'var(--text-primary)' }}>
              未保存离开确认
            </div>
            <div style={{ fontSize: '13px', color: 'var(--text-secondary)', marginTop: '6px', lineHeight: 1.5 }}>
              当前知识条目存在未保存的修改。若直接离开，未保存的校对内容将会丢失。请选择如何处理：
            </div>
          </div>
        </div>

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px', marginTop: '8px' }}>
          <button
            type="button"
            className="btn-secondary"
            onClick={onKeepEditing}
            disabled={saving}
            style={{ height: '34px', fontSize: '13px' }}
          >
            继续编辑
          </button>
          <button
            type="button"
            onClick={onDiscard}
            disabled={saving}
            style={{
              height: '34px',
              fontSize: '13px',
              padding: '0 12px',
              borderRadius: 'var(--radius-md)',
              backgroundColor: 'var(--bg-secondary)',
              color: 'var(--error-text)',
              border: '1px solid var(--border-color)',
              cursor: 'pointer',
            }}
          >
            放弃修改
          </button>
          <button
            type="button"
            className="btn-primary"
            onClick={onSave}
            disabled={saving}
            style={{ height: '34px', fontSize: '13px' }}
          >
            {saving ? '正在保存...' : '保存草稿'}
          </button>
        </div>
      </div>
    </div>
  );
};
