import React, { useState, useEffect } from 'react';
import { Plus } from 'lucide-react';
import { api } from '../../services/api';
import { DocumentItem, DocumentDetail } from '../../types';
import { EmptyKnowledgeView } from './EmptyKnowledgeView';
import { DocumentSidebar } from './documents/DocumentSidebar';
import { KnowledgeListPane } from './knowledge/KnowledgeListPane';
import { DocumentDetailModal } from './documents/DocumentDetailModal';
import { UploadModal } from './documents/UploadModal';
import { DeleteConfirmModal } from './documents/DeleteConfirmModal';

export const KnowledgeWorkspaceView: React.FC = () => {
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [selectedDocId, setSelectedDocId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [isUploadOpen, setIsUploadOpen] = useState(false);
  const [docToDelete, setDocToDelete] = useState<DocumentDetail | null>(null);

  // 原文预览与处理详情模态抽屉
  const [detailDocId, setDetailDocId] = useState<string | null>(null);
  const [detailDefaultTab, setDetailDefaultTab] = useState<'preview' | 'pipeline'>('preview');
  const [isDetailModalOpen, setIsDetailModalOpen] = useState(false);

  const fetchDocuments = async (autoSelectFirst = false) => {
    try {
      const docs = await api.getDocuments();
      setDocuments(docs);

      if (autoSelectFirst && docs.length > 0 && selectedDocId && !docs.some((d) => d.id === selectedDocId)) {
        setSelectedDocId(null);
      }
    } catch (err: any) {
      setError(err.message || '获取资料列表失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchDocuments(true);
  }, []);

  // 当存在正在解析中的任务时，自动轮询列表状态
  useEffect(() => {
    const hasActiveTasks = documents.some(
      (d) => d.processing_status === 'parsing' || d.processing_status === 'queued' || d.processing_status === 'extracting'
    );
    if (!hasActiveTasks) return;

    const timer = setInterval(() => {
      fetchDocuments(false);
    }, 2500);

    return () => clearInterval(timer);
  }, [documents]);

  const handleOpenDetailModal = (docId: string, tab: 'preview' | 'pipeline' = 'preview') => {
    setDetailDocId(docId);
    setDetailDefaultTab(tab);
    setIsDetailModalOpen(true);
  };

  if (loading) {
    return (
      <div
        style={{
          flex: 1,
          height: '100%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: 'var(--text-secondary)',
          fontSize: 'var(--font-size-sm)',
        }}
      >
        正在连接服务端并读取资料库...
      </div>
    );
  }

  if (error) {
    return (
      <div
        style={{
          flex: 1,
          height: '100%',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: '12px',
        }}
      >
        <div style={{ color: 'var(--error-text)', fontSize: 'var(--font-size-sm)' }}>{error}</div>
        <button
          className="btn-secondary"
          onClick={() => {
            setLoading(true);
            setError(null);
            fetchDocuments(true);
          }}
        >
          重试
        </button>
      </div>
    );
  }

  // 严格遵守 AC01：全新无资料时主工作区仅显示一句引导语与导入按钮
  if (documents.length === 0) {
    return (
      <>
        <EmptyKnowledgeView onImportClick={() => setIsUploadOpen(true)} />
        <UploadModal
          isOpen={isUploadOpen}
          onClose={() => setIsUploadOpen(false)}
          onUploadSuccess={() => {
            fetchDocuments(true);
            setIsUploadOpen(false);
          }}
        />
      </>
    );
  }

  const selectedDoc = documents.find((d) => d.id === selectedDocId) || null;

  // 有资料时呈现两栏工作区
  return (
    <div style={{ flex: 1, height: '100%', display: 'flex', flexDirection: 'column', backgroundColor: '#FFFFFF' }}>
      {/* 顶部统一页头：无多余的技术标签切换，直接提供全局导入动作 */}
      <div
        style={{
          height: '76px',
          padding: '16px 24px',
          borderBottom: '1px solid var(--border-color)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          flexShrink: 0,
        }}
      >
        <div>
          <h1 style={{ fontSize: 'var(--font-size-title)', fontWeight: 600, color: 'var(--text-primary)', lineHeight: 1.2 }}>
            知识管理
          </h1>
          <p style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-secondary)', marginTop: '4px' }}>
            将企业资料整理为可分类、可检索、可追溯的知识资产
          </p>
        </div>

        <button className="btn-primary" onClick={() => setIsUploadOpen(true)}>
          <Plus size={16} />
          <span>导入文件</span>
        </button>
      </div>

      {/* 主体两栏工作区：左栏原始资料，右栏知识整理结果 */}
      <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
        {/* 左栏：原始资料列表 */}
        <DocumentSidebar
          documents={documents}
          selectedDocId={selectedDocId}
          onSelectDoc={(id) => setSelectedDocId(id)}
          onOpenUpload={() => setIsUploadOpen(true)}
          onOpenPreview={(id) => handleOpenDetailModal(id, 'preview')}
        />

        {/* 右栏：知识整理结果与核对列表 */}
        <KnowledgeListPane
          selectedDoc={selectedDoc}
          onClearDocSelection={() => setSelectedDocId(null)}
          onOpenDocPreview={(tab) => {
            if (selectedDocId) {
              handleOpenDetailModal(selectedDocId, tab || 'preview');
            } else if (documents.length > 0) {
              handleOpenDetailModal(documents[0].id, tab || 'preview');
            }
          }}
        />
      </div>

      {/* 原文预览与处理详情模态弹窗 (按需呼出，不占领两栏主视图) */}
      <DocumentDetailModal
        documentId={detailDocId}
        isOpen={isDetailModalOpen}
        defaultTab={detailDefaultTab}
        onClose={() => setIsDetailModalOpen(false)}
        onRefreshList={() => fetchDocuments(false)}
      />

      {/* 模态对话框 */}
      <UploadModal
        isOpen={isUploadOpen}
        onClose={() => setIsUploadOpen(false)}
        onUploadSuccess={() => {
          fetchDocuments(true);
          setIsUploadOpen(false);
        }}
      />

      <DeleteConfirmModal
        document={docToDelete}
        onClose={() => setDocToDelete(null)}
        onDeleted={() => {
          setDocToDelete(null);
          fetchDocuments(true);
        }}
      />
    </div>
  );
};
