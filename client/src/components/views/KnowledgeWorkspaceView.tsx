import React, { useState, useEffect } from 'react';
import { Plus, PanelLeftClose, PanelLeftOpen } from 'lucide-react';
import { useLayout } from '../../context/LayoutContext';
import { api } from '../../services/api';
import { DocumentItem, DocumentDetail, SourceLocator } from '../../types';
import { EmptyKnowledgeView } from './EmptyKnowledgeView';
import { DocumentSidebar } from './documents/DocumentSidebar';
import { KnowledgeListPane } from './knowledge/KnowledgeListPane';
import { DocumentDetailModal } from './documents/DocumentDetailModal';
import { UploadModal } from './documents/UploadModal';
import { DeleteConfirmModal } from './documents/DeleteConfirmModal';
import { AppConfirmDialog } from '../common/AppConfirmDialog';

export const KnowledgeWorkspaceView: React.FC = () => {
  const { focusMode } = useLayout();
  const [isDocSidebarOpen, setIsDocSidebarOpen] = useState(true);
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [selectedDocId, setSelectedDocId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [knowledgeRefreshKey, setKnowledgeRefreshKey] = useState(0);

  const [isUploadOpen, setIsUploadOpen] = useState(false);
  const [docToDelete, setDocToDelete] = useState<{ id: string; title: string } | null>(null);

  // 批量删除应用内确认弹窗状态
  const [batchDeleteState, setBatchDeleteState] = useState<{
    isOpen: boolean;
    docIds: string[];
    targetNames: string[];
    versionCount: number;
    knowledgeCount: number;
    skillRefCount: number;
    loading: boolean;
  }>({
    isOpen: false,
    docIds: [],
    targetNames: [],
    versionCount: 0,
    knowledgeCount: 0,
    skillRefCount: 0,
    loading: false,
  });
  const batchDeleteResolverRef = React.useRef<((val: boolean) => void) | null>(null);
  const batchDeleteExecutingRef = React.useRef(false);

  // 原文预览与处理详情模态抽屉
  const [detailDocId, setDetailDocId] = useState<string | null>(null);
  const [detailDefaultTab, setDetailDefaultTab] = useState<'preview' | 'pipeline'>('preview');
  const [detailVersionId, setDetailVersionId] = useState<string | null>(null);
  const [detailFocusLocator, setDetailFocusLocator] = useState<SourceLocator | null>(null);
  const [isDetailModalOpen, setIsDetailModalOpen] = useState(false);

  // 轮询互斥与稳定指纹
  const isFetchingDocsRef = React.useRef(false);

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

  // 提取稳定的活跃任务签名，避免每次文档更新生成新引用导致死循环轮询
  const activeTaskSignature = documents
    .filter(
      (d) => d.processing_status === 'parsing' || d.processing_status === 'queued' || d.processing_status === 'extracting'
    )
    .map((d) => `${d.id}:${d.processing_status}`)
    .sort()
    .join(';');

  // 当存在正在解析中的任务时，自动轮询列表状态（同一轮询请求未完成时不发起下一次）
  useEffect(() => {
    if (!activeTaskSignature) return;

    let isSubscribed = true;
    const timer = setInterval(async () => {
      if (isFetchingDocsRef.current) return;
      isFetchingDocsRef.current = true;
      try {
        const docs = await api.getDocuments();
        if (isSubscribed) {
          setDocuments((prev) => {
            const prevSig = prev.map((d) => `${d.id}:${d.processing_status}:${d.updated_at}`).join('|');
            const newSig = docs.map((d) => `${d.id}:${d.processing_status}:${d.updated_at}`).join('|');
            return prevSig === newSig ? prev : docs;
          });
        }
      } catch (err: any) {
        console.error('Failed to poll documents:', err);
      } finally {
        isFetchingDocsRef.current = false;
      }
    }, 2500);

    return () => {
      isSubscribed = false;
      clearInterval(timer);
    };
  }, [activeTaskSignature]);

  const handleOpenDetailModal = (
    docId: string,
    tab: 'preview' | 'pipeline' = 'preview',
    versionId: string | null = null,
    locator: SourceLocator | null = null
  ) => {
    setDetailDocId(docId);
    setDetailDefaultTab(tab);
    setDetailVersionId(versionId);
    setDetailFocusLocator(locator);
    setIsDetailModalOpen(true);
  };

  const handleBatchDeleteDocuments = async (docIds: string[]): Promise<boolean> => {
    if (docIds.length === 0) return false;
    if (batchDeleteState.isOpen || batchDeleteState.loading) return false;

    const targets = documents.filter((doc) => docIds.includes(doc.id));
    const targetNames = targets.map((doc) => doc.title);
    try {
      const impacts = await Promise.all(docIds.map((id) => api.getDocumentDeletionImpact(id)));
      const versionCount = impacts.reduce((sum, item) => sum + item.version_count, 0);
      const knowledgeCount = impacts.reduce((sum, item) => sum + item.derived_knowledge_count, 0);
      const skillRefCount = impacts.reduce((sum, item) => sum + (item.skill_reference_count || 0), 0);

      return new Promise<boolean>((resolve) => {
        batchDeleteResolverRef.current = resolve;
        setBatchDeleteState({
          isOpen: true,
          docIds,
          targetNames,
          versionCount,
          knowledgeCount,
          skillRefCount,
          loading: false,
        });
      });
    } catch (err: any) {
      alert(err.message || '读取删除影响失败');
      return false;
    }
  };

  const handleExecuteBatchDelete = async () => {
    if (batchDeleteExecutingRef.current || batchDeleteState.loading) return;
    const { docIds, targetNames } = batchDeleteState;
    if (docIds.length === 0) return;

    batchDeleteExecutingRef.current = true;
    setBatchDeleteState((prev) => ({ ...prev, loading: true }));
    try {
      const results = await Promise.allSettled(docIds.map((id) => api.deleteDocument(id)));
      const failedIds = results
        .map((result, index) => (result.status === 'rejected' ? docIds[index] : null))
        .filter((id): id is string => Boolean(id));

      if (selectedDocId && docIds.includes(selectedDocId)) {
        setSelectedDocId(null);
      }
      if (detailDocId && docIds.includes(detailDocId)) {
        setIsDetailModalOpen(false);
        setDetailDocId(null);
        setDetailVersionId(null);
        setDetailFocusLocator(null);
      }

      await fetchDocuments(true);
      setKnowledgeRefreshKey((prev) => prev + 1);

      if (failedIds.length > 0) {
        const failedNames = targetNames.filter((_, idx) => failedIds.includes(docIds[idx])).join('、');
        alert(`部分文件删除失败：${failedNames || failedIds.join('、')}。其余文件已按现有逻辑删除规则处理。`);
        batchDeleteResolverRef.current?.(false);
      } else {
        batchDeleteResolverRef.current?.(true);
      }
    } catch (err: any) {
      alert(err.message || '批量删除失败');
      batchDeleteResolverRef.current?.(false);
    } finally {
      batchDeleteExecutingRef.current = false;
      batchDeleteResolverRef.current = null;
      setBatchDeleteState({
        isOpen: false,
        docIds: [],
        targetNames: [],
        versionCount: 0,
        knowledgeCount: 0,
        skillRefCount: 0,
        loading: false,
      });
    }
  };

  const handleCancelBatchDelete = () => {
    if (batchDeleteState.loading) return;
    batchDeleteResolverRef.current?.(false);
    batchDeleteResolverRef.current = null;
    setBatchDeleteState({
      isOpen: false,
      docIds: [],
      targetNames: [],
      versionCount: 0,
      knowledgeCount: 0,
      skillRefCount: 0,
      loading: false,
    });
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
  const showDocSidebar = isDocSidebarOpen && !focusMode;

  // 有资料时呈现两栏工作区
  return (
    <div style={{ flex: 1, height: '100%', display: 'flex', flexDirection: 'column', backgroundColor: 'var(--bg-secondary)', minWidth: 0 }}>
      {/* 顶部工具栏：面包屑 + 全局导入动作 */}
      <div className="zx-topbar">
        <div className="zx-breadcrumb">
          {!focusMode && (
            <button
              type="button"
              className="btn-ghost btn-sm btn-icon"
              style={{ width: '28px', marginLeft: '-6px' }}
              title={isDocSidebarOpen ? '收起资料栏' : '展开资料栏'}
              onClick={() => setIsDocSidebarOpen((prev) => !prev)}
            >
              {isDocSidebarOpen ? <PanelLeftClose size={16} /> : <PanelLeftOpen size={16} />}
            </button>
          )}
          <h1
            onClick={() => selectedDoc && setSelectedDocId(null)}
            title={selectedDoc ? '返回全部资料' : undefined}
            style={{
              cursor: selectedDoc ? 'pointer' : 'default',
              fontSize: 'var(--font-size-base)',
              fontWeight: selectedDoc ? 400 : 600,
              color: selectedDoc ? 'var(--text-muted)' : 'var(--text-primary)',
              whiteSpace: 'nowrap',
            }}
          >
            知识管理
          </h1>
          <span style={{ color: 'var(--border-strong)' }}>/</span>
          <span className="current" title={selectedDoc?.title}>
            {selectedDoc ? selectedDoc.title : '全部资料'}
          </span>
        </div>

        <button className="btn-primary" onClick={() => setIsUploadOpen(true)}>
          <Plus size={16} />
          <span>导入文件</span>
        </button>
      </div>

      {/* 主体两栏工作区：左栏原始资料，右栏知识整理结果 */}
      <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
        {/* 左栏：原始资料列表（专注模式或手动收起时隐藏） */}
        {showDocSidebar && (
          <DocumentSidebar
            documents={documents}
            selectedDocId={selectedDocId}
            onSelectDoc={(id) => setSelectedDocId(id)}
            onOpenUpload={() => setIsUploadOpen(true)}
            onOpenPreview={(id) => handleOpenDetailModal(id, 'preview')}
            onDeleteDocuments={handleBatchDeleteDocuments}
            onRequestDeleteDoc={(doc) => setDocToDelete(doc)}
          />
        )}

        {/* 右栏：知识整理结果与核对列表 */}
        <KnowledgeListPane
          selectedDoc={selectedDoc}
          refreshKey={knowledgeRefreshKey}
          onClearDocSelection={() => setSelectedDocId(null)}
          onOpenDocPreview={(tab) => {
            if (selectedDocId) {
              handleOpenDetailModal(selectedDocId, tab || 'preview');
            } else if (documents.length > 0) {
              handleOpenDetailModal(documents[0].id, tab || 'preview');
            }
          }}
          onOpenSearchSource={(documentId, versionId, locator) =>
            handleOpenDetailModal(documentId, 'preview', versionId, locator)
          }
        />
      </div>

      {/* 原文预览与处理详情模态弹窗 (按需呼出，不占领两栏主视图) */}
      <DocumentDetailModal
        documentId={detailDocId}
        isOpen={isDetailModalOpen}
        defaultTab={detailDefaultTab}
        initialVersionId={detailVersionId}
        focusLocator={detailFocusLocator}
        onClose={() => {
          setIsDetailModalOpen(false);
          setDetailVersionId(null);
          setDetailFocusLocator(null);
        }}
        onRefreshList={() => {
          fetchDocuments(false);
          setKnowledgeRefreshKey((prev) => prev + 1);
        }}
        onDeleteRequested={(document) => setDocToDelete(document)}
      />

      {/* 模态对话框 */}
      <UploadModal
        isOpen={isUploadOpen}
        onClose={() => setIsUploadOpen(false)}
        onUploadSuccess={() => {
          fetchDocuments(true);
          setKnowledgeRefreshKey((prev) => prev + 1);
          setIsUploadOpen(false);
        }}
      />

      <DeleteConfirmModal
        document={docToDelete}
        onClose={() => setDocToDelete(null)}
        onDeleted={() => {
          const deletedId = docToDelete?.id;
          if (deletedId) {
            if (selectedDocId === deletedId) {
              setSelectedDocId(null);
            }
            if (detailDocId === deletedId) {
              setIsDetailModalOpen(false);
              setDetailDocId(null);
              setDetailVersionId(null);
              setDetailFocusLocator(null);
            }
          }
          setDocToDelete(null);
          fetchDocuments(true);
          setKnowledgeRefreshKey((prev) => prev + 1);
        }}
      />

      <AppConfirmDialog
        isOpen={batchDeleteState.isOpen}
        title="删除所选文件？"
        variant="danger"
        confirmText="删除文件"
        cancelText="取消"
        initialFocus="cancel"
        loading={batchDeleteState.loading}
        targetList={batchDeleteState.targetNames}
        description="请核对需要删除的文件及对应影响范围："
        impactDescription={
          <>
            <div>
              将停止使用相关的 <strong>{batchDeleteState.versionCount} 个</strong>文件版本和{' '}
              <strong>{batchDeleteState.knowledgeCount} 条</strong>派生知识，并清理对应检索索引。
            </div>
            <div data-testid="batch-delete-skill-refs" style={{ marginTop: '4px', color: batchDeleteState.skillRefCount > 0 ? 'var(--warning-text)' : undefined }}>
              被 Skill 引用：<strong>{batchDeleteState.skillRefCount} 个</strong>
              {batchDeleteState.skillRefCount > 0 && '（各文件分别统计后相加），删除后这些 Skill 需要复核'}
            </div>
            <div style={{ marginTop: '4px', color: 'var(--text-muted)' }}>
              原始业务记录采用现有逻辑删除机制保留审计痕迹，不执行物理删除。
            </div>
          </>
        }
        onConfirm={handleExecuteBatchDelete}
        onCancel={handleCancelBatchDelete}
      />
    </div>
  );
};
