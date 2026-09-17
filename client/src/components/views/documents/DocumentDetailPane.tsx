import React, { useState, useEffect } from 'react';
import {
  FileText,
  RotateCw,
  Trash2,
  Download,
  AlertCircle,
  CheckCircle2,
  Clock,
  ExternalLink,
  ChevronDown,
  Layers,
  FileCode,
  Tag,
} from 'lucide-react';
import { DocumentDetail, DocumentVersion, SourceBlock } from '../../../types';
import { api } from '../../../services/api';
import { formatVersionLabel, formatAnchor, formatFileType } from '../../../utils/formatters';

interface DocumentDetailPaneProps {
  documentId: string;
  onDeleteRequested: (doc: DocumentDetail) => void;
  onRefreshList: () => void;
}

export const DocumentDetailPane: React.FC<DocumentDetailPaneProps> = ({
  documentId,
  onDeleteRequested,
  onRefreshList,
}) => {
  const [docDetail, setDocDetail] = useState<DocumentDetail | null>(null);
  const [selectedVersionId, setSelectedVersionId] = useState<string | null>(null);
  const [blocks, setBlocks] = useState<SourceBlock[]>([]);
  const [loading, setLoading] = useState(true);
  const [blocksLoading, setBlocksLoading] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchDetail = async (preserveVersion = true) => {
    try {
      const data = await api.getDocument(documentId);
      setDocDetail(data);

      const targetVerId =
        preserveVersion && selectedVersionId && data.versions.some((v: DocumentVersion) => v.id === selectedVersionId)
          ? selectedVersionId
          : data.active_version_id || (data.versions[0] ? data.versions[0].id : null);

      setSelectedVersionId(targetVerId);
    } catch (err: any) {
      setError(err.message || '获取文件详情失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetchDetail(false);
  }, [documentId]);

  // 当选中的版本变化或处理状态为 parsing / queued 时，轮询刷新
  useEffect(() => {
    if (!selectedVersionId || !documentId) return;

    let timer: NodeJS.Timeout | null = null;
    const currentVer = docDetail?.versions.find((v) => v.id === selectedVersionId);

    if (
      currentVer &&
      (currentVer.processing_status === 'parsing' ||
        currentVer.processing_status === 'queued' ||
        currentVer.processing_status === 'extracting')
    ) {
      timer = setInterval(() => {
        fetchDetail(true);
        onRefreshList();
      }, 2000);
    }

    return () => {
      if (timer) clearInterval(timer);
    };
  }, [selectedVersionId, docDetail]);

  // 加载结构块
  useEffect(() => {
    if (!documentId || !selectedVersionId) return;

    const currentVer = docDetail?.versions.find((v) => v.id === selectedVersionId);
    if (!currentVer || currentVer.processing_status !== 'completed') {
      setBlocks([]);
      return;
    }

    const loadBlocks = async () => {
      try {
        setBlocksLoading(true);
        const data = await api.getSourceBlocks(documentId, selectedVersionId);
        setBlocks(data);
      } catch (err: any) {
        console.error('Failed to load blocks:', err);
      } finally {
        setBlocksLoading(false);
      }
    };

    loadBlocks();
  }, [documentId, selectedVersionId, docDetail]);

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

  if (loading) {
    return (
      <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-secondary)' }}>
        正在读取资料详情与解析产物...
      </div>
    );
  }

  if (error || !docDetail) {
    return (
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: '12px' }}>
        <div style={{ color: 'var(--error-text)' }}>{error || '资料不存在或已被删除'}</div>
        <button className="btn-secondary" onClick={() => fetchDetail(false)}>
          重试
        </button>
      </div>
    );
  }

  const currentVersion: DocumentVersion | undefined =
    docDetail.versions.find((v) => v.id === selectedVersionId) || docDetail.versions[0];

  const formatSize = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
  };

  const formatDate = (iso: string) => {
    if (!iso) return '-';
    return iso.replace('T', ' ').substring(0, 19);
  };

  return (
    <section
      style={{
        flex: 1,
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        backgroundColor: '#FFFFFF',
        overflow: 'hidden',
      }}
    >
      {/* 头部元数据栏 */}
      <div
        style={{
          padding: '16px 24px',
          borderBottom: '1px solid var(--border-color)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          backgroundColor: '#FFFFFF',
          flexShrink: 0,
        }}
      >
        <div style={{ minWidth: 0, flex: 1, marginRight: '16px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <h2
              style={{
                fontSize: 'var(--font-size-title)',
                fontWeight: 600,
                color: 'var(--text-primary)',
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
              }}
            >
              {docDetail.title}
            </h2>

            {/* 版本切换下拉 */}
            {docDetail.versions.length > 1 ? (
              <select
                value={selectedVersionId || ''}
                onChange={(e) => setSelectedVersionId(e.target.value)}
                style={{
                  fontSize: '12px',
                  fontWeight: 600,
                  color: 'var(--brand-accent)',
                  backgroundColor: 'var(--brand-accent-light)',
                  border: '1px solid var(--border-color)',
                  borderRadius: 'var(--radius-sm)',
                  padding: '2px 8px',
                  cursor: 'pointer',
                }}
              >
                {docDetail.versions.map((v) => (
                  <option key={v.id} value={v.id}>
                    {formatVersionLabel(v.version_label)}（{formatDate(v.uploaded_at)}）
                  </option>
                ))}
              </select>
            ) : (
              <span
                style={{
                  fontSize: '12px',
                  fontWeight: 600,
                  color: 'var(--brand-accent)',
                  backgroundColor: 'var(--brand-accent-light)',
                  padding: '2px 6px',
                  borderRadius: 'var(--radius-sm)',
                }}
              >
                {formatVersionLabel(currentVersion?.version_label)}
              </span>
            )}
          </div>

          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '16px',
              fontSize: 'var(--font-size-xs)',
              color: 'var(--text-secondary)',
              marginTop: '6px',
            }}
          >
            <span>格式：{formatFileType(currentVersion?.file_type)}</span>
            <span>大小：{formatSize(currentVersion?.file_size || 0)}</span>
            <span>导入时间：{formatDate(currentVersion?.uploaded_at || '')}</span>
            <span>历史版本数：{docDetail.versions.length}</span>
          </div>
        </div>

        {/* 顶部操作区 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <button
            onClick={handleRetry}
            disabled={retrying || currentVersion?.processing_status === 'parsing'}
            className="btn-secondary"
            title="重新触发后台结构化解析"
            style={{ height: '32px', fontSize: '12px', gap: '4px' }}
          >
            <RotateCw size={13} className={retrying ? 'spin-slow' : ''} />
            <span>重新解析</span>
          </button>

          <a
            href={api.getFileUrl(docDetail.id, currentVersion?.id || '')}
            target="_blank"
            rel="noreferrer"
            className="btn-secondary"
            style={{ height: '32px', fontSize: '12px', gap: '4px', textDecoration: 'none' }}
          >
            <Download size={13} />
            <span>查看原件</span>
          </a>

          <button
            data-testid="delete-doc-btn"
            onClick={() => onDeleteRequested(docDetail)}
            className="btn-secondary"
            style={{
              height: '32px',
              fontSize: '12px',
              gap: '4px',
              color: 'var(--error-text)',
              borderColor: 'var(--border-color)',
            }}
          >
            <Trash2 size={13} />
            <span>删除</span>
          </button>
        </div>
      </div>

      {/* 主体滚动区 */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '20px 24px', display: 'flex', flexDirection: 'column', gap: '20px' }}>
        {/* 处理阶段卡片 */}
        <div
          style={{
            border: '1px solid var(--border-color)',
            borderRadius: 'var(--radius-md)',
            backgroundColor: 'var(--bg-secondary)',
            padding: '16px',
          }}
        >
          <div style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)', marginBottom: '12px' }}>
            处理流水线阶段状态
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '12px' }}>
            {/* 阶段 1 */}
            <div
              style={{
                backgroundColor: '#FFFFFF',
                padding: '12px',
                borderRadius: 'var(--radius-sm)',
                border: '1px solid var(--border-color)',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '6px', color: 'var(--success-text)', fontSize: '12px', fontWeight: 600 }}>
                <CheckCircle2 size={14} />
                <span>1. 原始文件保存与校验</span>
              </div>
              <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '4px', lineHeight: 1.4 }}>
                原始文件已安全持久化到存储目录，文件指纹校验通过。
              </div>
            </div>

            {/* 阶段 2 */}
            <div
              style={{
                backgroundColor: '#FFFFFF',
                padding: '12px',
                borderRadius: 'var(--radius-sm)',
                border: '1px solid var(--border-color)',
              }}
            >
              {(currentVersion?.processing_status === 'completed' || currentVersion?.processing_status === 'extracting') && (
                <>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px', color: 'var(--success-text)', fontSize: '12px', fontWeight: 600 }}>
                    <CheckCircle2 size={14} />
                    <span>2. 正文结构解析完成</span>
                  </div>
                  <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '4px', lineHeight: 1.4 }}>
                    已成功拆解并提取 {currentVersion.block_count || blocks.length} 个原文结构块及定位锚点。
                  </div>
                </>
              )}
              {currentVersion?.processing_status === 'parsing' && (
                <>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px', color: 'var(--pending-text)', fontSize: '12px', fontWeight: 600 }}>
                    <RotateCw size={14} className="spin-slow" />
                    <span>2. 后台正文解析中</span>
                  </div>
                  <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '4px', lineHeight: 1.4 }}>
                    正在提取标题层级、段落与表格，请稍候...
                  </div>
                </>
              )}
              {currentVersion?.processing_status === 'queued' && (
                <>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px', color: 'var(--text-muted)', fontSize: '12px', fontWeight: 600 }}>
                    <Clock size={14} />
                    <span>2. 解析任务排队中</span>
                  </div>
                  <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '4px', lineHeight: 1.4 }}>
                    任务已录入持久化队列，等待后台工作线程执行。
                  </div>
                </>
              )}
              {currentVersion?.processing_status === 'failed' && (
                <>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px', color: 'var(--error-text)', fontSize: '12px', fontWeight: 600 }}>
                    <AlertCircle size={14} />
                    <span>2. 正文结构解析失败</span>
                  </div>
                  <div style={{ fontSize: '11px', color: 'var(--error-text)', marginTop: '4px', lineHeight: 1.4 }}>
                    原因：{currentVersion.error_summary || '解析过程异常'}
                  </div>
                </>
              )}
            </div>

            {/* 阶段 3: 知识原子抽取与校对状态 */}
            <div
              style={{
                backgroundColor: '#FFFFFF',
                padding: '12px',
                borderRadius: 'var(--radius-sm)',
                border: '1px solid var(--border-color)',
              }}
            >
              {currentVersion?.processing_status === 'completed' ? (
                <>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px', color: 'var(--success-text)', fontSize: '12px', fontWeight: 600 }}>
                    <CheckCircle2 size={14} />
                    <span>3. 知识原子提炼完成</span>
                  </div>
                  <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '4px', lineHeight: 1.4 }}>
                    已提炼多维知识原子候选并关联确切原文证据，可切换至「知识资产与校对」查看并审核。
                  </div>
                </>
              ) : currentVersion?.processing_status === 'extracting' ? (
                <>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px', color: 'var(--brand-accent)', fontSize: '12px', fontWeight: 600 }}>
                    <RotateCw size={14} className="spin-slow" />
                    <span>3. 知识原子提炼中</span>
                  </div>
                  <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '4px', lineHeight: 1.4 }}>
                    正在调用 DeepSeek 大模型提炼多维知识候选与事实证据，请稍候...
                  </div>
                </>
              ) : (
                <>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px', color: 'var(--text-muted)', fontSize: '12px', fontWeight: 600 }}>
                    <Clock size={14} />
                    <span>3. 知识原子提炼排队</span>
                  </div>
                  <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '4px', lineHeight: 1.4 }}>
                    正文结构解析完成后，系统将自动触发知识原子提炼流水线。
                  </div>
                </>
              )}
            </div>
          </div>
        </div>

        {/* 原文结构化解析块预览 */}
        <div>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '12px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <span style={{ fontSize: 'var(--font-size-base)', fontWeight: 600, color: 'var(--text-primary)' }}>
                原文结构解析结果
              </span>
              <span
                style={{
                  fontSize: '11px',
                  color: 'var(--text-secondary)',
                  backgroundColor: 'var(--bg-secondary)',
                  padding: '2px 8px',
                  borderRadius: '10px',
                }}
              >
                共 {blocks.length} 块
              </span>
            </div>

            <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)' }}>
              按原文出现次序保留上下文与锚点，作为后续知识抽取的事实证据
            </div>
          </div>

          {blocksLoading ? (
            <div style={{ textAlign: 'center', padding: '40px', color: 'var(--text-secondary)', fontSize: '13px' }}>
              正在加载原文结构块...
            </div>
          ) : currentVersion?.processing_status === 'failed' ? (
            <div
              style={{
                padding: '32px',
                borderRadius: 'var(--radius-md)',
                border: '1px dashed var(--error-border)',
                backgroundColor: 'var(--error-bg)',
                textAlign: 'center',
                color: 'var(--error-text)',
              }}
            >
              <AlertCircle size={28} style={{ margin: '0 auto 8px auto' }} />
              <div style={{ fontWeight: 600, fontSize: '14px' }}>文件解析未能成功完成</div>
              <div style={{ fontSize: '12px', marginTop: '6px' }}>{currentVersion.error_summary}</div>
              <button
                onClick={handleRetry}
                disabled={retrying}
                className="btn-primary"
                style={{ marginTop: '16px', height: '32px', fontSize: '12px' }}
              >
                重新尝试解析
              </button>
            </div>
          ) : blocks.length === 0 ? (
            <div
              style={{
                padding: '32px',
                borderRadius: 'var(--radius-md)',
                border: '1px dashed var(--border-color)',
                textAlign: 'center',
                color: 'var(--text-muted)',
                fontSize: '13px',
              }}
            >
              {currentVersion?.processing_status === 'parsing'
                ? '文件正在后台解析中，解析完成后将自动展示结构块...'
                : '暂未提取到原文结构块'}
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              {blocks.map((block) => {
                const isHeading = block.block_type === 'heading';
                const isTable = block.block_type === 'table';

                return (
                  <div
                    key={block.id}
                    style={{
                      padding: '12px 16px',
                      borderRadius: 'var(--radius-md)',
                      border: '1px solid var(--border-color)',
                      backgroundColor: isHeading ? 'var(--bg-secondary)' : '#FFFFFF',
                      transition: 'border-color 120ms ease',
                    }}
                  >
                    {/* 块元信息与锚点 */}
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        fontSize: '11px',
                        color: 'var(--text-muted)',
                        marginBottom: '6px',
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <span style={{ fontWeight: 600, color: 'var(--brand-accent)' }}>
                          #{block.block_index + 1}
                        </span>
                        <span
                          style={{
                            backgroundColor: isHeading ? 'var(--brand-accent-light)' : 'var(--bg-secondary)',
                            color: isHeading ? 'var(--brand-accent)' : 'var(--text-secondary)',
                            padding: '1px 6px',
                            borderRadius: '3px',
                            fontWeight: 500,
                          }}
                        >
                          {isHeading ? '标题' : isTable ? '表格' : '正文段落'}
                        </span>
                        {block.heading_path && (
                          <span style={{ color: 'var(--text-secondary)' }}>
                            路径：{block.heading_path}
                          </span>
                        )}
                      </div>

                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        {block.page_number && <span>第 {block.page_number} 页</span>}
                        {block.paragraph_anchor && (
                          <span style={{ color: 'var(--text-muted)' }}>
                            {formatAnchor(block.paragraph_anchor)}
                          </span>
                        )}
                      </div>
                    </div>

                    {/* 正文或表格内容 */}
                    {isTable ? (
                      <pre
                        style={{
                          fontSize: '12px',
                          color: 'var(--text-primary)',
                          fontFamily: 'monospace',
                          backgroundColor: 'var(--bg-secondary)',
                          padding: '8px',
                          borderRadius: '4px',
                          overflowX: 'auto',
                          margin: 0,
                          whiteSpace: 'pre-wrap',
                        }}
                      >
                        {block.text_content}
                      </pre>
                    ) : (
                      <div
                        style={{
                          fontSize: isHeading ? '14px' : '13px',
                          fontWeight: isHeading ? 600 : 400,
                          color: 'var(--text-primary)',
                          lineHeight: 1.6,
                        }}
                      >
                        {block.text_content}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </section>
  );
};
