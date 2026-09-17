import React, { useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import { DocumentDetail } from '../../../types';
import { api } from '../../../services/api';

interface DeleteConfirmModalProps {
  document: DocumentDetail | null;
  onClose: () => void;
  onDeleted: () => void;
}

export const DeleteConfirmModal: React.FC<DeleteConfirmModalProps> = ({
  document,
  onClose,
  onDeleted,
}) => {
  const [deleting, setDeleting] = useState(false);

  if (!document) return null;

  const handleConfirm = async () => {
    try {
      setDeleting(true);
      await api.deleteDocument(document.id);
      onDeleted();
      onClose();
    } catch (err: any) {
      alert(err.message || '删除资料失败');
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        backgroundColor: 'rgba(0, 0, 0, 0.45)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 1000,
        userSelect: 'none',
      }}
    >
      <div
        style={{
          width: '440px',
          backgroundColor: '#FFFFFF',
          borderRadius: 'var(--radius-lg)',
          boxShadow: '0 8px 30px rgba(0, 0, 0, 0.15)',
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
              backgroundColor: 'var(--error-bg)',
              color: 'var(--error-text)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              flexShrink: 0,
            }}
          >
            <AlertTriangle size={20} />
          </div>

          <div>
            <div style={{ fontSize: '15px', fontWeight: 600, color: 'var(--text-primary)' }}>
              确认删除资料？
            </div>
            <div style={{ fontSize: '13px', color: 'var(--text-secondary)', marginTop: '6px', lineHeight: 1.5 }}>
              资料名称：<strong>{document.title}</strong>
              <br />
              关联历史版本：<strong>{document.versions.length} 个版本</strong>
            </div>
          </div>
        </div>

        <div
          style={{
            fontSize: '12px',
            color: 'var(--text-muted)',
            backgroundColor: 'var(--bg-secondary)',
            padding: '10px 12px',
            borderRadius: 'var(--radius-sm)',
            lineHeight: 1.5,
          }}
        >
          <strong>影响说明：</strong>
          <br />
          1. 删除后将撤销对该文件、各历史版本及原文结构块的日常访问。
          <br />
          2. 该文件当前正在排队或执行的后台解析任务将被立即取消，迟到任务产物拒绝写回。
          <br />
          3. 本操作采用逻辑删除，物理原始数据当前尚未物理清除。
        </div>

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px', marginTop: '4px' }}>
          <button
            type="button"
            className="btn-secondary"
            onClick={onClose}
            disabled={deleting}
            style={{ height: '34px', fontSize: '13px' }}
          >
            取消
          </button>
          <button
            type="button"
            data-testid="confirm-delete-btn"
            onClick={handleConfirm}
            disabled={deleting}
            style={{
              height: '34px',
              fontSize: '13px',
              padding: '0 16px',
              borderRadius: 'var(--radius-md)',
              backgroundColor: 'var(--error-text)',
              color: '#FFFFFF',
              fontWeight: 500,
              cursor: 'pointer',
            }}
          >
            {deleting ? '正在删除...' : '确认删除'}
          </button>
        </div>
      </div>
    </div>
  );
};
