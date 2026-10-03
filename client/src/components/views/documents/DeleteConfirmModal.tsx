import React, { useEffect, useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import { DocumentDetail } from '../../../types';
import { api } from '../../../services/api';

function skillRefText(count: number, statusCounts?: Record<string, number>): string {
  const detail = Object.entries(statusCounts || {}).map(([k, v]) => `${k} ${v} 个`).join('、');
  return detail ? `${count} 个（${detail}）` : `${count} 个`;
}

interface DeleteConfirmModalProps {
  document: { id: string; title: string } | null;
  onClose: () => void;
  onDeleted: () => void;
}

export const DeleteConfirmModal: React.FC<DeleteConfirmModalProps> = ({
  document,
  onClose,
  onDeleted,
}) => {
  const [deleting, setDeleting] = useState(false);
  const deletingRef = React.useRef(false);
  const [impact, setImpact] = useState<Awaited<ReturnType<typeof api.getDocumentDeletionImpact>> | null>(null);
  const [impactError, setImpactError] = useState<string | null>(null);

  useEffect(() => {
    deletingRef.current = false;
    if (!document) {
      setImpact(null);
      setImpactError(null);
      return;
    }
    let active = true;
    setImpact(null);
    setImpactError(null);
    api.getDocumentDeletionImpact(document.id)
      .then((data) => {
        if (active) setImpact(data);
      })
      .catch((err: any) => {
        if (active) setImpactError(err.message || '删除影响读取失败');
      });
    return () => {
      active = false;
    };
  }, [document?.id]);

  useEffect(() => {
    if (!document) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !deletingRef.current) {
        e.preventDefault();
        e.stopPropagation();
        onClose();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [document, onClose]);

  if (!document) return null;

  const handleConfirm = async () => {
    if (deletingRef.current || deleting) return;
    if (!impact) {
      alert(impactError || '正在读取真实删除影响，请稍后重试');
      return;
    }
    deletingRef.current = true;
    try {
      setDeleting(true);
      await api.deleteDocument(document.id);
      onDeleted();
      onClose();
    } catch (err: any) {
      alert(err.message || '删除资料失败');
    } finally {
      deletingRef.current = false;
      setDeleting(false);
    }
  };

  return (
    <div
      data-testid="delete-confirm-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget && !deletingRef.current) {
          onClose();
        }
      }}
      style={{
        position: 'fixed',
        inset: 0,
        backgroundColor: 'rgba(18, 26, 22, 0.42)',
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
              {impact ? (
                <>
                  历史版本：<strong>{impact.version_count} 个</strong> · 派生知识：
                  <strong>{impact.derived_knowledge_count} 条</strong>
                  <br />
                  <span
                    data-testid="delete-skill-refs"
                    style={{ color: impact.skill_reference_count > 0 ? 'var(--warning-text)' : undefined }}
                  >
                    被 Skill 引用：<strong>{skillRefText(impact.skill_reference_count, impact.skill_reference_status_counts)}</strong>
                    {impact.skill_reference_count > 0 && '，删除后这些 Skill 需要复核'}
                  </span>
                </>
              ) : impactError ? (
                <span style={{ color: 'var(--error-text)' }}>{impactError}</span>
              ) : (
                <span>正在读取真实影响...</span>
              )}
            </div>
          </div>
        </div>

        <div
          style={{
            fontSize: 'var(--font-size-xs)',
            color: 'var(--text-muted)',
            backgroundColor: 'var(--bg-secondary)',
            padding: '10px 12px',
            borderRadius: 'var(--radius-sm)',
            lineHeight: 1.5,
          }}
        >
          <strong>真实影响：</strong>
          <br />
          {impact ? (
            <>
              将撤销 {impact.version_count} 个文件版本、{impact.derived_knowledge_count} 条派生知识的使用资格；
              当前检索派生记录 {impact.retrieval_record_count} 条、运行中任务 {impact.active_task_count} 个。
              <br />
              案例关系引用 {impact.related_case_reference_count} 条；Skill 引用 {impact.skill_reference_count} 条。
              <br />
              本操作采用逻辑删除，原始数据当前不执行未经约定的物理删除；索引清理失败时仍保持停止使用。
            </>
          ) : (
            <>必须成功读取真实影响后才能确认删除。</>
          )}
        </div>

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px', marginTop: '4px' }}>
          <button
            type="button"
            data-testid="cancel-delete-btn"
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
            disabled={deleting || !impact}
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
