import React, { useState } from 'react';
import { Search, Plus, RotateCw, FileText, AlertCircle, Clock, CheckCircle2, Layers } from 'lucide-react';
import { DocumentItem } from '../../../types';
import { formatVersionLabel } from '../../../utils/formatters';

interface DocumentSidebarProps {
  documents: DocumentItem[];
  selectedDocId: string | null;
  onSelectDoc: (docId: string | null) => void;
  onOpenUpload: () => void;
  onOpenPreview?: (docId: string) => void;
}

export const DocumentSidebar: React.FC<DocumentSidebarProps> = ({
  documents,
  selectedDocId,
  onSelectDoc,
  onOpenUpload,
  onOpenPreview,
}) => {
  const [searchQuery, setSearchQuery] = useState('');

  const filteredDocs = documents.filter((doc) =>
    doc.title.toLowerCase().includes(searchQuery.trim().toLowerCase())
  );

  const getFormatBadge = (fileType: string) => {
    const type = fileType.toLowerCase();
    let bg = '#718096';
    let text = '文档';

    if (type === 'pdf') {
      bg = '#E53E3E';
      text = 'PDF';
    } else if (type === 'docx' || type === 'doc') {
      bg = '#2B6CB0';
      text = '文档';
    } else if (type === 'md' || type === 'markdown') {
      bg = '#285C49';
      text = '文稿';
    } else if (type === 'txt') {
      bg = '#4A5568';
      text = '文本';
    }

    return (
      <div
        style={{
          width: '32px',
          height: '32px',
          borderRadius: '4px',
          backgroundColor: bg,
          color: '#FFFFFF',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontSize: '11px',
          fontWeight: 700,
          flexShrink: 0,
        }}
      >
        {text}
      </div>
    );
  };

  const getStatusDisplay = (status: string) => {
    switch (status) {
      case 'completed':
        return (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px', color: 'var(--success-text)', fontSize: '11px' }}>
            <CheckCircle2 size={12} />
            整理完成
          </span>
        );
      case 'parsing':
      case 'extracting':
        return (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px', color: 'var(--brand-accent)', fontSize: '11px' }}>
            <RotateCw size={12} className="spin-slow" />
            正在整理
          </span>
        );
      case 'partial_failed':
      case 'failed':
        return (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px', color: 'var(--error-text)', fontSize: '11px' }}>
            <AlertCircle size={12} />
            整理失败
          </span>
        );
      case 'queued':
      default:
        return (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px', color: 'var(--text-muted)', fontSize: '11px' }}>
            <Clock size={12} />
            排队中
          </span>
        );
    }
  };

  return (
    <section
      style={{
        width: '300px',
        borderRight: '1px solid var(--border-color)',
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'space-between',
        padding: '16px',
        flexShrink: 0,
        backgroundColor: '#FFFFFF',
      }}
    >
      <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
        {/* 顶部标题与快速导入 */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '12px' }}>
          <h2 style={{ fontSize: 'var(--font-size-section)', fontWeight: 600, color: 'var(--text-primary)' }}>
            原始文件 ({documents.length})
          </h2>
          <button
            onClick={onOpenUpload}
            title="导入新文件"
            style={{
              padding: '4px 8px',
              display: 'flex',
              alignItems: 'center',
              gap: '4px',
              fontSize: '12px',
              color: 'var(--brand-accent)',
              border: '1px solid var(--brand-accent)',
              borderRadius: 'var(--radius-sm)',
              backgroundColor: 'transparent',
            }}
          >
            <Plus size={13} />
            <span>添加</span>
          </button>
        </div>

        {/* 搜索过滤框 */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            padding: '6px 10px',
            border: '1px solid var(--border-color)',
            borderRadius: 'var(--radius-md)',
            backgroundColor: 'var(--bg-secondary)',
            marginBottom: '12px',
            flexShrink: 0,
          }}
        >
          <Search size={14} color="var(--text-muted)" />
          <input
            type="text"
            placeholder="搜索资料文件名..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            style={{
              border: 'none',
              background: 'transparent',
              fontSize: 'var(--font-size-sm)',
              width: '100%',
              color: 'var(--text-primary)',
            }}
          />
        </div>

        {/* 资料列表 */}
        <div
          style={{
            flex: 1,
            overflowY: 'auto',
            display: 'flex',
            flexDirection: 'column',
            gap: '6px',
            paddingRight: '2px',
          }}
        >
          {!searchQuery.trim() && (
            <div
              data-testid="doc-item-all"
              onClick={() => onSelectDoc(null)}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '10px',
                padding: '8px 10px',
                borderRadius: 'var(--radius-md)',
                backgroundColor: selectedDocId === null ? 'var(--bg-selected)' : '#FFFFFF',
                border: `1px solid ${selectedDocId === null ? 'var(--brand-accent)' : 'var(--border-color)'}`,
                cursor: 'pointer',
                transition: 'all 120ms ease',
                marginBottom: '2px',
              }}
            >
              <div
                style={{
                  width: '32px',
                  height: '32px',
                  borderRadius: '4px',
                  backgroundColor: 'var(--brand-accent-light)',
                  color: 'var(--brand-accent)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: '11px',
                  fontWeight: 700,
                  flexShrink: 0,
                }}
              >
                <Layers size={16} />
              </div>
              <div style={{ minWidth: 0, flex: 1 }}>
                <div
                  style={{
                    fontSize: 'var(--font-size-sm)',
                    fontWeight: 600,
                    color: 'var(--text-primary)',
                  }}
                >
                  全部资料
                </div>
                <div
                  style={{
                    fontSize: '11px',
                    color: 'var(--text-muted)',
                    marginTop: '2px',
                  }}
                >
                  汇总查看全部已导入资料
                </div>
              </div>
            </div>
          )}

          {filteredDocs.length === 0 ? (
            <div
              style={{
                textAlign: 'center',
                padding: '32px 12px',
                color: 'var(--text-muted)',
                fontSize: 'var(--font-size-xs)',
              }}
            >
              {searchQuery.trim() ? '未匹配到相关资料' : '暂无资料'}
            </div>
          ) : (
            filteredDocs.map((doc) => {
              const isSelected = doc.id === selectedDocId;
              return (
                <div
                  key={doc.id}
                  data-testid={`doc-item-${doc.title}`}
                  onClick={() => onSelectDoc(doc.id)}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '10px',
                    padding: '8px 10px',
                    borderRadius: 'var(--radius-md)',
                    backgroundColor: isSelected ? 'var(--bg-selected)' : '#FFFFFF',
                    border: `1px solid ${isSelected ? 'var(--brand-accent)' : 'var(--border-color)'}`,
                    cursor: 'pointer',
                    transition: 'all 120ms ease',
                    position: 'relative',
                  }}
                >
                  {getFormatBadge(doc.file_type)}
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                      }}
                    >
                      <div
                        title={doc.title}
                        style={{
                          fontSize: 'var(--font-size-sm)',
                          fontWeight: 500,
                          color: 'var(--text-primary)',
                          whiteSpace: 'nowrap',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          maxWidth: '170px',
                        }}
                      >
                        {doc.title}
                      </div>

                      {/* 独立查看原文入口 */}
                      {onOpenPreview && (
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            onOpenPreview(doc.id);
                          }}
                          title="查看文件原文预览与处理详情"
                          style={{
                            border: 'none',
                            background: 'none',
                            color: 'var(--brand-accent)',
                            fontSize: '11px',
                            cursor: 'pointer',
                            padding: '2px 4px',
                            borderRadius: '3px',
                          }}
                        >
                          原文
                        </button>
                      )}
                    </div>
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        marginTop: '3px',
                      }}
                    >
                      {getStatusDisplay(doc.processing_status)}
                      <span
                        style={{
                          fontSize: '11px',
                          color: 'var(--text-muted)',
                          backgroundColor: 'var(--bg-secondary)',
                          padding: '1px 5px',
                          borderRadius: '3px',
                        }}
                      >
                        {formatVersionLabel(doc.version_label)}
                      </span>
                    </div>
                  </div>
                </div>
              );
            })
          )}
        </div>
      </div>

      <div
        style={{
          fontSize: 'var(--font-size-xs)',
          color: 'var(--text-muted)',
          paddingTop: '12px',
          borderTop: '1px solid var(--border-color)',
          marginTop: '8px',
        }}
      >
        原始文件已持久化保存，随时可查看
      </div>
    </section>
  );
};
