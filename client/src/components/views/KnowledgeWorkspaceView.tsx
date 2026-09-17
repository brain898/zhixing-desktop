import React, { useState, useEffect } from 'react';
import { Plus, BookOpen, FileCode } from 'lucide-react';
import { api } from '../../services/api';
import { DocumentItem, DocumentDetail } from '../../types';
import { EmptyKnowledgeView } from './EmptyKnowledgeView';
import { DocumentSidebar } from './documents/DocumentSidebar';
import { DocumentDetailPane } from './documents/DocumentDetailPane';
import { KnowledgeListPane } from './knowledge/KnowledgeListPane';
import { UploadModal } from './documents/UploadModal';
import { DeleteConfirmModal } from './documents/DeleteConfirmModal';

export const KnowledgeWorkspaceView: React.FC = () => {
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [selectedDocId, setSelectedDocId] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<'knowledge' | 'pipeline'>('knowledge');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [isUploadOpen, setIsUploadOpen] = useState(false);
  const [docToDelete, setDocToDelete] = useState<DocumentDetail | null>(null);

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
        <button className="btn-secondary" onClick={() => { setLoading(true); setError(null); fetchDocuments(true); }}>
          重试
        </button>
      </div>
    );
  }

  // 严格遵守 AC01：无资料时主工作区仅显示引导语与导入按钮
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

  // 有真实资料时展示两栏工作区
  return (
    <div style={{ flex: 1, height: '100%', display: 'flex', flexDirection: 'column', backgroundColor: '#FFFFFF' }}>
      {/* 顶部全局页头 */}
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

        {/* 视图切换器：知识资产与校对 VS 原件与结构块 */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            backgroundColor: 'var(--bg-secondary)',
            padding: '3px',
            borderRadius: 'var(--radius-md)',
            border: '1px solid var(--border-color)',
            gap: '4px',
          }}
        >
          <button
            type="button"
            data-testid="mode-tab-knowledge"
            onClick={() => setViewMode('knowledge')}
            style={{
              padding: '6px 14px',
              fontSize: '13px',
              fontWeight: viewMode === 'knowledge' ? 600 : 400,
              borderRadius: 'var(--radius-sm)',
              border: 'none',
              backgroundColor: viewMode === 'knowledge' ? '#FFFFFF' : 'transparent',
              color: viewMode === 'knowledge' ? 'var(--brand-accent)' : 'var(--text-secondary)',
              boxShadow: viewMode === 'knowledge' ? '0 1px 3px rgba(0,0,0,0.08)' : 'none',
              cursor: 'pointer',
              transition: 'all 120ms ease',
            }}
          >
            知识资产与校对
          </button>
          <button
            type="button"
            data-testid="mode-tab-pipeline"
            onClick={() => {
              if (!selectedDocId && documents.length > 0) {
                setSelectedDocId(documents[0].id);
              }
              setViewMode('pipeline');
            }}
            style={{
              padding: '6px 14px',
              fontSize: '13px',
              fontWeight: viewMode === 'pipeline' ? 600 : 400,
              borderRadius: 'var(--radius-sm)',
              border: 'none',
              backgroundColor: viewMode === 'pipeline' ? '#FFFFFF' : 'transparent',
              color: viewMode === 'pipeline' ? 'var(--brand-accent)' : 'var(--text-secondary)',
              boxShadow: viewMode === 'pipeline' ? '0 1px 3px rgba(0,0,0,0.08)' : 'none',
              cursor: 'pointer',
              transition: 'all 120ms ease',
            }}
          >
            原件与结构块
          </button>
        </div>

        <button className="btn-primary" onClick={() => setIsUploadOpen(true)}>
          <Plus size={16} />
          <span>导入文件</span>
        </button>
      </div>

      {/* 主体两栏工作区 */}
      <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
        {/* 左栏：真实原始文件列表 */}
        <DocumentSidebar
          documents={documents}
          selectedDocId={selectedDocId}
          onSelectDoc={(id) => setSelectedDocId(id)}
          onOpenUpload={() => setIsUploadOpen(true)}
        />

        {/* 右栏：根据模式展示知识资产卡片流或原始文档结构块 */}
        {viewMode === 'knowledge' ? (
          <KnowledgeListPane
            selectedDoc={selectedDoc}
            onClearDocSelection={() => setSelectedDocId(null)}
            onSwitchToDocPipeline={() => {
              if (!selectedDocId && documents.length > 0) {
                setSelectedDocId(documents[0].id);
              }
              setViewMode('pipeline');
            }}
          />
        ) : selectedDocId ? (
          <DocumentDetailPane
            key={selectedDocId}
            documentId={selectedDocId}
            onDeleteRequested={(doc) => setDocToDelete(doc)}
            onRefreshList={() => fetchDocuments(false)}
          />
        ) : (
          <div
            style={{
              flex: 1,
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              color: 'var(--text-muted)',
              fontSize: '13px',
              gap: '12px',
            }}
          >
            <div>请在左侧选择文件以查看原始文件与结构块详情</div>
            {documents.length > 0 && (
              <button
                type="button"
                className="btn-secondary"
                onClick={() => setSelectedDocId(documents[0].id)}
              >
                查看首个文件
              </button>
            )}
          </div>
        )}
      </div>

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
