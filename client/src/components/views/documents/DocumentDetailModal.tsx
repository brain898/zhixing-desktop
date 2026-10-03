import React, { useState, useEffect } from 'react';
import {
  X,
  FileText,
  RotateCw,
  Download,
  AlertCircle,
  CheckCircle2,
  Clock,
  ChevronDown,
  ChevronRight,
  Trash2,
} from 'lucide-react';
import { DocumentDetail, DocumentVersion, SourceBlock, SourceLocator } from '../../../types';
import { api } from '../../../services/api';
import { formatVersionLabel, formatAnchor, formatFileType } from '../../../utils/formatters';

interface DocumentDetailModalProps {
  documentId: string | null;
  isOpen: boolean;
  defaultTab?: 'preview' | 'pipeline';
  initialVersionId?: string | null;
  focusLocator?: SourceLocator | null;
  onClose: () => void;
  onRefreshList: () => void;
  onDeleteRequested: (document: DocumentDetail) => void;
}

export const DocumentDetailModal: React.FC<DocumentDetailModalProps> = ({
  documentId,
  isOpen,
  defaultTab = 'preview',
  initialVersionId = null,
  focusLocator = null,
  onClose,
  onRefreshList,
  onDeleteRequested,
}) => {
  const [activeTab, setActiveTab] = useState<'preview' | 'pipeline'>(defaultTab);
  const [docDetail, setDocDetail] = useState<DocumentDetail | null>(null);
  const [selectedVersionId, setSelectedVersionId] = useState<string | null>(null);
  const [blocks, setBlocks] = useState<SourceBlock[]>([]);
  const [loading, setLoading] = useState(true);
  const [blocksLoading, setBlocksLoading] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [showRawBlocks, setShowRawBlocks] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (isOpen) {
      setActiveTab(defaultTab);
    }
  }, [isOpen, defaultTab]);

  const fetchDetail = async (preserveVersion = true) => {
    if (!documentId) return;
    try {
      const data = await api.getDocument(documentId);
      setDocDetail(data);

      const hasInitialVersion =
        initialVersionId && data.versions.some((v: DocumentVersion) => v.id === initialVersionId);
      const targetVerId = hasInitialVersion
        ? initialVersionId
        : preserveVersion && selectedVersionId && data.versions.some((v: DocumentVersion) => v.id === selectedVersionId)
        ? selectedVersionId
        : data.active_version_id || (data.versions[0] ? data.versions[0].id : null);

      setSelectedVersionId(targetVerId);
    } catch (err: any) {
      setError(err.message || '获取资料详情失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!isOpen || !documentId) return;
    setLoading(true);
    setError(null);
    fetchDetail(false);
  }, [isOpen, documentId, initialVersionId]);

  // 当处于正在解析或提取中时轮询状态（避免重叠轮询与无变化的重复全量刷新）
  const isFetchingDetailRef = React.useRef(false);
  const currentVerStatus = docDetail?.versions.find((v) => v.id === selectedVersionId)?.processing_status;
  const prevVerStatusRef = React.useRef(currentVerStatus);

  useEffect(() => {
    prevVerStatusRef.current = currentVerStatus;
  }, [currentVerStatus]);

  useEffect(() => {
    if (!isOpen || !selectedVersionId || !documentId) return;

    const isActive =
      currentVerStatus === 'parsing' ||
      currentVerStatus === 'queued' ||
      currentVerStatus === 'extracting';

    if (!isActive) return;

    let isSubscribed = true;
    const timer = setInterval(async () => {
      if (isFetchingDetailRef.current) return;
      isFetchingDetailRef.current = true;
      try {
        const data = await api.getDocument(documentId);
        if (isSubscribed) {
          setDocDetail(data);
          const updatedVer = data.versions.find((v: DocumentVersion) => v.id === selectedVersionId);
          const newStatus = updatedVer?.processing_status;

          // 仅在状态产生真实变化或任务结束时，才通知主页面刷新列表与统计
          if (newStatus && newStatus !== prevVerStatusRef.current) {
            prevVerStatusRef.current = newStatus;
            onRefreshList();
          }
        }
      } catch (err: any) {
        console.error('Failed to poll document detail:', err);
      } finally {
        isFetchingDetailRef.current = false;
      }
    }, 2500);

    return () => {
      isSubscribed = false;
      clearInterval(timer);
    };
  }, [isOpen, selectedVersionId, documentId, currentVerStatus, onRefreshList]);

  // 支持 Escape 键关闭抽屉
  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose]);

  // 加载原文结构块
  useEffect(() => {
    if (!isOpen || !documentId || !selectedVersionId) return;

    const currentVer = docDetail?.versions.find((v) => v.id === selectedVersionId);
    if (!currentVer || !['completed', 'extracting', 'partial_failed'].includes(currentVer.processing_status)) {
      setBlocks([]);
      return;
    }

    const loadBlocks = async () => {
      try {
        setBlocksLoading(true);
        const data = await api.getSourceBlocks(documentId, selectedVersionId);
        setBlocks(Array.isArray(data) ? data : []);
      } catch (err: any) {
        console.error('Failed to load blocks:', err);
      } finally {
        setBlocksLoading(false);
      }
    };

    loadBlocks();
  }, [isOpen, documentId, selectedVersionId, docDetail]);

  // 正式检索来源定位：在指定历史文件版本中定位真实页码/标题/段落/行锚点。
  useEffect(() => {
    if (!isOpen || activeTab !== 'preview' || blocksLoading || blocks.length === 0 || !focusLocator) return;

    const target = blocks.find((block) => {
      if (focusLocator.paragraph_anchor && block.paragraph_anchor === focusLocator.paragraph_anchor) return true;
      if (focusLocator.block_index !== null && block.block_index === focusLocator.block_index) return true;
      if (
        focusLocator.page_number !== null &&
        block.page_number === focusLocator.page_number &&
        (!focusLocator.heading_path || block.heading_path === focusLocator.heading_path)
      ) return true;
      return false;
    });
    if (!target) return;

    const timer = window.setTimeout(() => {
      const element = document.querySelector(`[data-source-block-id="${target.id}"]`) as HTMLElement | null;
      if (!element) return;
      element.scrollIntoView({ block: 'center', behavior: 'smooth' });
      element.animate(
        [
          { backgroundColor: 'rgba(40, 92, 73, 0.16)' },
          { backgroundColor: 'rgba(40, 92, 73, 0)' },
        ],
        { duration: 1800, easing: 'ease-out' }
      );
    }, 80);

    return () => window.clearTimeout(timer);
  }, [isOpen, activeTab, blocksLoading, blocks, focusLocator]);

  if (!isOpen || !documentId) return null;

  const currentVersion: DocumentVersion | undefined =
    docDetail?.versions.find((v) => v.id === selectedVersionId) || docDetail?.versions[0];

  const formatSize = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
  };

  const formatDate = (iso: string) => {
    if (!iso) return '-';
    return iso.replace('T', ' ').substring(0, 19);
  };

  const handleRetry = async () => {
    if (!documentId || !selectedVersionId) return;
    try {
      setRetrying(true);
      await api.retryTask(documentId, selectedVersionId);
      await fetchDetail(true);
      onRefreshList();
    } catch (err: any) {
      alert(err.message || '重试提交失败');
    } finally {
      setRetrying(false);
    }
  };

  const handleDownload = async () => {
    if (!documentId || !selectedVersionId || !currentVersion) return;
    try {
      setDownloading(true);
      const blob = await api.downloadOriginalFile(documentId, selectedVersionId);
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = currentVersion.file_name || '原文文件';
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (err: any) {
      alert(err.message || '下载原文文件失败');
    } finally {
      setDownloading(false);
    }
  };

  return (
    <div
      style={{
        position: 'fixed',
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        backgroundColor: 'rgba(18, 26, 22, 0.42)',
        zIndex: 1000,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
      onClick={onClose}
    >
      <div
        style={{
          width: '1080px',
          height: '86vh',
          maxWidth: '95vw',
          backgroundColor: 'var(--bg-primary)',
          borderRadius: 'var(--radius-lg)',
          boxShadow: 'var(--shadow-lg)',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* 顶部标题栏与 Tab */}
        <div
          style={{
            padding: '12px 20px 12px 24px',
            minHeight: '64px',
            gap: '16px',
            borderBottom: '1px solid var(--border-color)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            backgroundColor: 'var(--bg-primary)',
            flexShrink: 0,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px', minWidth: 0, flex: 1 }}>
            <div
              style={{
                width: '36px',
                height: '36px',
                borderRadius: 'var(--radius-md)',
                backgroundColor: 'var(--brand-accent-light)',
                color: 'var(--brand-accent)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                flexShrink: 0,
              }}
            >
              <FileText size={18} />
            </div>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div
                style={{
                  fontSize: 'var(--font-size-section)',
                  fontWeight: 600,
                  color: 'var(--text-primary)',
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                }}
              >
                {docDetail?.title || '资料详情'}
              </div>
              <div
                style={{
                  fontSize: 'var(--font-size-xs)',
                  color: 'var(--text-muted)',
                  marginTop: '2px',
                  display: 'flex',
                  gap: '4px 14px',
                  flexWrap: 'wrap',
                  whiteSpace: 'nowrap',
                }}
              >
                <span>版本：{formatVersionLabel(currentVersion?.version_label)}</span>
                <span>格式：{formatFileType(currentVersion?.file_type)}</span>
                <span>大小：{formatSize(currentVersion?.file_size || 0)}</span>
                <span>导入时间：{formatDate(currentVersion?.uploaded_at || '')}</span>
              </div>
            </div>
          </div>

          {/* 切换 Tab：原文预览 VS 处理详情 */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexShrink: 0 }}>
            <div className="zx-segmented" style={{ marginRight: '6px' }}>
              <button
                type="button"
                onClick={() => setActiveTab('preview')}
                className={activeTab === 'preview' ? 'active' : undefined}
              >
                原文预览
              </button>
              <button
                type="button"
                onClick={() => setActiveTab('pipeline')}
                className={activeTab === 'pipeline' ? 'active' : undefined}
              >
                处理详情
              </button>
            </div>

            <button
              type="button"
              className="btn-ghost"
              onClick={handleDownload}
              disabled={downloading || !currentVersion}
              title="下载保存到本地的文件原件"
            >
              <Download size={13} />
              <span>{downloading ? '下载中' : '下载原件'}</span>
            </button>

            <button
              type="button"
              data-testid="delete-doc-btn"
              onClick={() => {
                if (!docDetail) return;
                onDeleteRequested(docDetail);
                onClose();
              }}
              className="btn-danger-ghost"
              disabled={!docDetail}
            >
              <Trash2 size={13} />
              <span>删除资料</span>
            </button>

            <button
              type="button"
              data-testid="close-detail-modal-btn"
              onClick={onClose}
              style={{
                background: 'none',
                border: 'none',
                color: 'var(--text-secondary)',
                cursor: 'pointer',
                padding: '4px',
                display: 'flex',
                alignItems: 'center',
              }}
            >
              <X size={20} />
            </button>
          </div>
        </div>

        {/* 内容区 */}
        <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: '24px' }}>
          {loading ? (
            <div style={{ padding: '60px', textAlign: 'center', color: 'var(--text-secondary)', fontSize: '13px' }}>
              正在加载资料与解析详情...
            </div>
          ) : error ? (
            <div style={{ padding: '40px', textAlign: 'center', color: 'var(--error-text)', fontSize: '13px' }}>
              {error}
            </div>
          ) : activeTab === 'preview' ? (
            /* 原文预览视图 */
            <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  padding: '10px 14px',
                  backgroundColor: 'var(--bg-secondary)',
                  borderRadius: 'var(--radius-sm)',
                  fontSize: 'var(--font-size-xs)',
                  color: 'var(--text-secondary)',
                }}
              >
                <span>忠实反映原始上传文件的内容与段落结构，供核对时溯源参考。</span>
                <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                  <button
                    type="button"
                    className="btn-secondary"
                    onClick={() => setShowRawBlocks(!showRawBlocks)}
                    style={{ height: '26px', fontSize: 'var(--font-size-xs)', padding: '0 10px' }}
                  >
                    {showRawBlocks ? '切换文章阅读视图' : '查看结构信息'}
                  </button>
                  <span style={{ color: 'var(--brand-accent)', fontWeight: 500 }}>
                    共 {blocks.length} 处正文段落与表格
                  </span>
                </div>
              </div>

              {blocksLoading ? (
                <div style={{ padding: '40px', textAlign: 'center', color: 'var(--text-muted)', fontSize: '13px' }}>
                  正在渲染文件正文...
                </div>
              ) : blocks.length === 0 ? (
                <div
                  style={{
                    padding: '60px',
                    textAlign: 'center',
                    color: 'var(--text-muted)',
                    fontSize: '13px',
                    backgroundColor: 'var(--bg-secondary)',
                    borderRadius: 'var(--radius-md)',
                  }}
                >
                  {currentVersion?.processing_status === 'parsing'
                    ? '正文解析进行中，请稍候...'
                    : '未提取到正文文本，若为扫描件暂不支持文字层识别'}
                </div>
              ) : !showRawBlocks ? (
                /* 默认整洁文章排版 */
                <div
                  style={{
                    border: '1px solid var(--border-color)',
                    borderRadius: 'var(--radius-md)',
                    backgroundColor: '#FFFFFF',
                    padding: '28px 36px',
                    maxWidth: '840px',
                    margin: '0 auto',
                    width: '100%',
                    lineHeight: 1.8,
                    fontSize: '14px',
                    color: 'var(--text-primary)',
                    boxShadow: 'var(--shadow-sm)',
                  }}
                >
                  {blocks.map((block) => {
                    if (block.block_type === 'heading') {
                      return (
                        <h3
                          key={block.id}
                          id={`anchor-${block.paragraph_anchor}`}
                          data-source-block-id={block.id}
                          style={{
                            fontSize: '16px',
                            fontWeight: 700,
                            color: 'var(--text-primary)',
                            marginTop: '22px',
                            marginBottom: '10px',
                            paddingBottom: '6px',
                            borderBottom: '1px solid var(--border-color)',
                          }}
                        >
                          {block.text_content}
                        </h3>
                      );
                    }
                    if (block.block_type === 'table') {
                      return (
                        <div
                          key={block.id}
                          id={`anchor-${block.paragraph_anchor}`}
                          data-source-block-id={block.id}
                          style={{
                            backgroundColor: 'var(--bg-secondary)',
                            border: '1px solid var(--border-color)',
                            borderRadius: 'var(--radius-sm)',
                            padding: '12px',
                            margin: '14px 0',
                            overflowX: 'auto',
                            fontFamily: 'monospace',
                            fontSize: 'var(--font-size-xs)',
                            whiteSpace: 'pre',
                          }}
                        >
                          {block.text_content}
                        </div>
                      );
                    }
                    return (
                      <p
                        key={block.id}
                        id={`anchor-${block.paragraph_anchor}`}
                        data-source-block-id={block.id}
                        style={{
                          margin: '0 0 14px 0',
                          lineHeight: 1.8,
                          whiteSpace: 'pre-wrap',
                        }}
                      >
                        {block.text_content}
                      </p>
                    );
                  })}
                </div>
              ) : (
                /* 结构块拆解视图 */
                <div
                  style={{
                    border: '1px solid var(--border-color)',
                    borderRadius: 'var(--radius-md)',
                    backgroundColor: '#FFFFFF',
                    padding: '16px 20px',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '12px',
                  }}
                >
                  {blocks.map((block) => (
                    <div
                      key={block.id}
                      id={`anchor-${block.paragraph_anchor}`}
                      style={{
                        padding: '10px 14px',
                        borderRadius: 'var(--radius-sm)',
                        backgroundColor: block.block_type === 'heading' ? 'var(--bg-secondary)' : '#FFFFFF',
                        borderLeft: block.block_type === 'heading' ? '3px solid var(--brand-accent)' : '1px solid var(--border-color)',
                        display: 'flex',
                        flexDirection: 'column',
                        gap: '6px',
                        transition: 'background-color 0.12s',
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)' }}>
                        <span style={{ fontWeight: 500, color: 'var(--text-secondary)' }}>
                          {block.heading_path || '正文'}
                        </span>
                        <span style={{ backgroundColor: 'var(--bg-secondary)', padding: '1px 6px', borderRadius: '4px', border: '1px solid var(--border-color)' }}>
                          {formatAnchor(block.paragraph_anchor || `p.${block.page_number || 1}`)}
                        </span>
                      </div>
                      <div
                        style={{
                          fontSize: block.block_type === 'heading' ? '14px' : '13px',
                          fontWeight: block.block_type === 'heading' ? 600 : 400,
                          color: 'var(--text-primary)',
                          lineHeight: 1.6,
                          whiteSpace: 'pre-wrap',
                        }}
                      >
                        {block.text_content}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ) : (
            /* 处理详情视图 */
            <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
              {/* 处理流水线概览 */}
              <div
                style={{
                  border: '1px solid var(--border-color)',
                  borderRadius: 'var(--radius-md)',
                  padding: '16px 20px',
                  backgroundColor: 'var(--bg-secondary)',
                }}
              >
                <div style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)', marginBottom: '14px' }}>
                  处理流水线当前进度
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '14px' }}>
                  {/* 步骤 1 */}
                  <div style={{ backgroundColor: '#FFFFFF', padding: '12px 14px', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border-color)' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: 'var(--font-size-xs)', fontWeight: 600, color: 'var(--success-text)' }}>
                      <CheckCircle2 size={14} />
                      <span>1. 文件安全保存</span>
                    </div>
                    <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)', marginTop: '4px' }}>
                      原始文件已安全持久化并生成指纹，未发生篡改。
                    </div>
                  </div>

                  {/* 步骤 2 */}
                  <div style={{ backgroundColor: '#FFFFFF', padding: '12px 14px', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border-color)' }}>
                    {['completed', 'extracting', 'partial_failed'].includes(currentVersion?.processing_status || '') ? (
                      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: 'var(--font-size-xs)', fontWeight: 600, color: 'var(--success-text)' }}>
                        <CheckCircle2 size={14} />
                        <span>2. 正文结构解析完成</span>
                      </div>
                    ) : currentVersion?.processing_status === 'parsing' ? (
                      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: 'var(--font-size-xs)', fontWeight: 600, color: 'var(--pending-text)' }}>
                        <RotateCw size={14} className="spin-slow" />
                        <span>2. 正文结构解析中</span>
                      </div>
                    ) : currentVersion?.processing_status === 'failed' ? (
                      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: 'var(--font-size-xs)', fontWeight: 600, color: 'var(--error-text)' }}>
                        <AlertCircle size={14} />
                        <span>2. 正文解析失败</span>
                      </div>
                    ) : (
                      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: 'var(--font-size-xs)', fontWeight: 600, color: 'var(--text-muted)' }}>
                        <Clock size={14} />
                        <span>2. 解析排队中</span>
                      </div>
                    )}
                    <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)', marginTop: '4px' }}>
                      {['completed', 'extracting', 'partial_failed'].includes(currentVersion?.processing_status || '')
                        ? `成功提取 ${blocks.length} 处结构块与定位锚点。`
                        : currentVersion?.error_summary || '等待后台线程解析正文'}
                    </div>
                  </div>

                  {/* 步骤 3 */}
                  <div style={{ backgroundColor: '#FFFFFF', padding: '12px 14px', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border-color)' }}>
                    {currentVersion?.processing_status === 'completed' ? (
                      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: 'var(--font-size-xs)', fontWeight: 600, color: 'var(--success-text)' }}>
                        <CheckCircle2 size={14} />
                        <span>3. 知识整理完成</span>
                      </div>
                    ) : currentVersion?.processing_status === 'extracting' ? (
                      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: 'var(--font-size-xs)', fontWeight: 600, color: 'var(--brand-accent)' }}>
                        <RotateCw size={14} className="spin-slow" />
                        <span>3. 知识抽取整理中</span>
                      </div>
                    ) : currentVersion?.processing_status === 'partial_failed' ? (
                      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: 'var(--font-size-xs)', fontWeight: 600, color: 'var(--error-text)' }}>
                        <AlertCircle size={14} />
                        <span>3. 知识提炼部分异常</span>
                      </div>
                    ) : (
                      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: 'var(--font-size-xs)', fontWeight: 600, color: 'var(--text-muted)' }}>
                        <Clock size={14} />
                        <span>3. 知识提炼等待中</span>
                      </div>
                    )}
                    <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)', marginTop: '4px' }}>
                      {currentVersion?.processing_status === 'completed'
                        ? '知识整理已生成候选，请在右栏逐项核对。'
                        : currentVersion?.error_summary || 'DeepSeek 大模型语义提炼'}
                    </div>
                  </div>
                </div>

                {/* 失败重试按钮 */}
                {(currentVersion?.processing_status === 'failed' || currentVersion?.processing_status === 'partial_failed') && (
                  <div style={{ marginTop: '14px', display: 'flex', alignItems: 'center', gap: '12px' }}>
                    <button
                      type="button"
                      className="btn-primary"
                      onClick={handleRetry}
                      disabled={retrying}
                      style={{ height: '32px', fontSize: 'var(--font-size-xs)' }}
                    >
                      <RotateCw size={13} className={retrying ? 'spin-slow' : ''} />
                      <span>针对失败步骤重试</span>
                    </button>
                    <span style={{ fontSize: 'var(--font-size-xs)', color: 'var(--error-text)' }}>
                      重试仅针对失败步骤，不覆盖已有的人工修改，不复活已删除条目。
                    </span>
                  </div>
                )}
              </div>

              {/* 结构块与解析技术信息 (按需展开，默认折叠) */}
              <div
                style={{
                  border: '1px solid var(--border-color)',
                  borderRadius: 'var(--radius-md)',
                  backgroundColor: '#FFFFFF',
                  overflow: 'hidden',
                }}
              >
                <div
                  onClick={() => setShowRawBlocks(!showRawBlocks)}
                  style={{
                    padding: '12px 18px',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    cursor: 'pointer',
                    backgroundColor: 'var(--bg-secondary)',
                    userSelect: 'none',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)' }}>
                    {showRawBlocks ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                    <span>解析结构块与技术记录 ({blocks.length})</span>
                  </div>
                  <span style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)' }}>
                    {showRawBlocks ? '点击折叠' : '点击展开查看原始拆解结构块'}
                  </span>
                </div>

                {showRawBlocks && (
                  <div style={{ padding: '16px', display: 'flex', flexDirection: 'column', gap: '10px', maxHeight: '360px', overflowY: 'auto' }}>
                    {blocks.map((b) => (
                      <div
                        key={b.id}
                        style={{
                          padding: '8px 12px',
                          borderRadius: 'var(--radius-sm)',
                          backgroundColor: 'var(--bg-secondary)',
                          fontSize: 'var(--font-size-xs)',
                          display: 'flex',
                          flexDirection: 'column',
                          gap: '4px',
                        }}
                      >
                        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)' }}>
                          <span>[#{b.block_index}] {b.block_type} | {b.heading_path || 'root'}</span>
                          <span>{b.paragraph_anchor}</span>
                        </div>
                        <div style={{ color: 'var(--text-primary)', lineHeight: 1.5 }}>
                          {b.text_content}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
