import React, { useEffect, useState } from 'react';
import { Search, RotateCw, Layers, Settings2, Trash2 } from 'lucide-react';
import { DocumentItem } from '../../../types';
import { formatVersionLabel } from '../../../utils/formatters';
import { DocumentContextMenu } from './DocumentContextMenu';

interface DocumentSidebarProps {
  documents: DocumentItem[];
  selectedDocId: string | null;
  onSelectDoc: (docId: string | null) => void;
  onOpenUpload: () => void;
  onOpenPreview?: (docId: string) => void;
  onDeleteDocuments?: (docIds: string[]) => Promise<boolean>;
  onRequestDeleteDoc?: (doc: DocumentItem) => void;
}

export const DocumentSidebar: React.FC<DocumentSidebarProps> = ({
  documents,
  selectedDocId,
  onSelectDoc,
  onOpenPreview,
  onDeleteDocuments,
  onRequestDeleteDoc,
}) => {
  const [searchQuery, setSearchQuery] = useState('');
  const [manageMode, setManageMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [deleting, setDeleting] = useState(false);
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; doc: DocumentItem } | null>(null);

  useEffect(() => {
    const validIds = new Set(documents.map((doc) => doc.id));
    setSelectedIds((prev) => new Set(Array.from(prev).filter((id) => validIds.has(id))));
    if (contextMenu && !documents.some((d) => d.id === contextMenu.doc.id)) {
      setContextMenu(null);
    }
  }, [documents]);

  const filteredDocs = documents.filter((doc) =>
    doc.title.toLowerCase().includes(searchQuery.trim().toLowerCase())
  );

  const getFormatBadge = (fileType: string) => {
    const type = fileType.toLowerCase();
    let bg = 'var(--text-secondary)';
    let text = '文档';

    if (type === 'pdf') {
      bg = '#B5473A';
      text = 'PDF';
    } else if (type === 'docx' || type === 'doc') {
      bg = '#3E6A8A';
      text = '文档';
    } else if (type === 'md' || type === 'markdown') {
      bg = 'var(--brand-accent)';
      text = '文稿';
    } else if (type === 'txt') {
      bg = 'var(--text-secondary)';
      text = '文本';
    }

    return (
      <div
        style={{
          width: '32px',
          height: '32px',
          borderRadius: 'var(--radius-sm)',
          backgroundColor: bg,
          color: '#FFFFFF',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontSize: 'var(--font-size-xs)',
          fontWeight: 600,
          flexShrink: 0,
        }}
      >
        {text}
      </div>
    );
  };

  const toggleManageMode = () => {
    if (deleting) return;
    setManageMode((current) => {
      if (current) setSelectedIds(new Set());
      return !current;
    });
  };

  const toggleDocSelection = (docId: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(docId)) next.delete(docId);
      else next.add(docId);
      return next;
    });
  };

  const toggleSelectAll = () => {
    const visibleIds = filteredDocs.map((doc) => doc.id);
    const allVisibleSelected = visibleIds.length > 0 && visibleIds.every((id) => selectedIds.has(id));
    setSelectedIds((prev) => {
      const next = new Set(prev);
      visibleIds.forEach((id) => {
        if (allVisibleSelected) next.delete(id);
        else next.add(id);
      });
      return next;
    });
  };

  const handleDeleteSelected = async () => {
    if (!onDeleteDocuments || selectedIds.size === 0 || deleting) return;
    setDeleting(true);
    try {
      const completed = await onDeleteDocuments(Array.from(selectedIds));
      if (completed) {
        setSelectedIds(new Set());
        setManageMode(false);
      }
    } finally {
      setDeleting(false);
    }
  };

  const getStatusDisplay = (status: string) => {
    switch (status) {
      case 'completed':
        return <span className="zx-badge success">整理完成</span>;
      case 'parsing':
      case 'extracting':
        return (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px', color: 'var(--brand-accent)', fontSize: 'var(--font-size-xs)', fontWeight: 500 }}>
            <RotateCw size={12} className="spin-slow" />
            正在整理
          </span>
        );
      case 'partial_failed':
      case 'failed':
        return <span className="zx-badge danger">整理失败</span>;
      case 'queued':
      default:
        return <span className="zx-badge muted">排队中</span>;
    }
  };

  const rowStyle = (highlighted: boolean): React.CSSProperties => ({
    display: 'flex',
    alignItems: 'center',
    gap: '10px',
    padding: '9px 8px',
    borderRadius: 'var(--radius-sm)',
    backgroundColor: highlighted ? 'var(--bg-selected)' : 'transparent',
    cursor: 'pointer',
    transition: 'background-color 120ms ease',
    position: 'relative',
  });

  return (
    <section
      style={{
        width: '280px',
        borderRight: '1px solid var(--border-color)',
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'space-between',
        flexShrink: 0,
        backgroundColor: 'var(--bg-primary)',
        minHeight: 0,
      }}
    >
      <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
        {/* 顶部标题与批量管理 */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '14px 12px 10px 16px',
            gap: '8px',
          }}
        >
          <h2 style={{ fontSize: 'var(--font-size-base)', fontWeight: 600, color: 'var(--text-primary)', whiteSpace: 'nowrap' }}>
            原始文件
            <span style={{ fontWeight: 400, color: 'var(--text-muted)' }}> ({documents.length})</span>
          </h2>
          <button
            type="button"
            className={manageMode ? 'btn-secondary btn-sm' : 'btn-ghost btn-sm'}
            onClick={toggleManageMode}
            title={manageMode ? '退出文件管理' : '批量管理文件'}
            disabled={deleting}
          >
            {!manageMode && <Settings2 size={13} />}
            <span>{manageMode ? '完成' : '管理'}</span>
          </button>
        </div>

        {/* 搜索过滤框 */}
        <div className="zx-search" style={{ margin: '0 16px 10px', flexShrink: 0 }}>
          <Search size={14} />
          <input
            type="text"
            placeholder="搜索资料文件名..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
        </div>

        {/* 资料列表 */}
        <div
          style={{
            flex: 1,
            overflowY: 'auto',
            display: 'flex',
            flexDirection: 'column',
            gap: '2px',
            padding: '0 8px 8px',
          }}
        >
          {!manageMode && !searchQuery.trim() && (
            <div
              data-testid="doc-item-all"
              onClick={() => onSelectDoc(null)}
              style={rowStyle(selectedDocId === null)}
              onMouseEnter={(e) => {
                if (selectedDocId !== null) e.currentTarget.style.backgroundColor = 'var(--bg-secondary)';
              }}
              onMouseLeave={(e) => {
                if (selectedDocId !== null) e.currentTarget.style.backgroundColor = 'transparent';
              }}
            >
              <div
                style={{
                  width: '32px',
                  height: '32px',
                  borderRadius: 'var(--radius-sm)',
                  backgroundColor: 'var(--bg-sunken)',
                  color: 'var(--text-secondary)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  flexShrink: 0,
                }}
              >
                <Layers size={16} />
              </div>
              <div style={{ minWidth: 0, flex: 1 }}>
                <div style={{ fontSize: 'var(--font-size-sm)', fontWeight: 500, color: 'var(--text-primary)' }}>全部资料</div>
                <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)', marginTop: '2px' }}>
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
                fontSize: 'var(--font-size-sm)',
              }}
            >
              {searchQuery.trim() ? '未匹配到相关资料' : '暂无资料'}
            </div>
          ) : (
            filteredDocs.map((doc) => {
              const isSelected = doc.id === selectedDocId;
              const isChecked = selectedIds.has(doc.id);
              const isHighlighted = manageMode ? isChecked : isSelected;
              return (
                <div
                  key={doc.id}
                  className="zx-hover-row"
                  data-testid={`doc-item-${doc.title}`}
                  onClick={() => (manageMode ? toggleDocSelection(doc.id) : onSelectDoc(doc.id))}
                  onContextMenu={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    setContextMenu({
                      x: e.clientX,
                      y: e.clientY,
                      doc,
                    });
                  }}
                  onMouseEnter={(e) => {
                    if (!isHighlighted) e.currentTarget.style.backgroundColor = 'var(--bg-secondary)';
                  }}
                  onMouseLeave={(e) => {
                    if (!isHighlighted) e.currentTarget.style.backgroundColor = 'transparent';
                  }}
                  style={rowStyle(isHighlighted)}
                >
                  {manageMode && (
                    <input
                      type="checkbox"
                      checked={isChecked}
                      aria-label={`选择 ${doc.title}`}
                      onChange={() => toggleDocSelection(doc.id)}
                      onClick={(e) => e.stopPropagation()}
                      style={{
                        width: '15px',
                        height: '15px',
                        margin: 0,
                        accentColor: 'var(--brand-accent)',
                        flexShrink: 0,
                        cursor: 'pointer',
                      }}
                    />
                  )}
                  {getFormatBadge(doc.file_type)}
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '6px' }}>
                      <div
                        title={doc.title}
                        style={{
                          fontSize: 'var(--font-size-sm)',
                          fontWeight: isSelected ? 600 : 500,
                          color: 'var(--text-primary)',
                          whiteSpace: 'nowrap',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          flex: 1,
                          minWidth: 0,
                        }}
                      >
                        {doc.title}
                      </div>

                      {/* 独立查看原文入口（悬停显示） */}
                      {!manageMode && onOpenPreview && (
                        <button
                          type="button"
                          className="zx-hover-reveal"
                          onClick={(e) => {
                            e.stopPropagation();
                            onOpenPreview(doc.id);
                          }}
                          title="查看文件原文预览与处理详情"
                          style={{
                            color: 'var(--brand-accent)',
                            fontSize: 'var(--font-size-xs)',
                            padding: '2px 6px',
                            borderRadius: 'var(--radius-xs)',
                            whiteSpace: 'nowrap',
                            flexShrink: 0,
                            backgroundColor: 'var(--bg-primary)',
                          }}
                        >
                          原文
                        </button>
                      )}
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginTop: '3px' }}>
                      {getStatusDisplay(doc.processing_status)}
                      <span style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)' }}>
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

      {manageMode && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: '8px',
            padding: '10px 16px 0',
            borderTop: '1px solid var(--border-color)',
          }}
        >
          <button
            type="button"
            className="btn-ghost btn-sm"
            onClick={toggleSelectAll}
            disabled={filteredDocs.length === 0 || deleting}
            style={{ color: 'var(--brand-accent)', paddingLeft: 0 }}
          >
            {filteredDocs.length > 0 && filteredDocs.every((doc) => selectedIds.has(doc.id)) ? '取消全选' : '全选'}
          </button>
          <button
            type="button"
            className="btn-danger btn-sm"
            onClick={handleDeleteSelected}
            disabled={selectedIds.size === 0 || deleting || !onDeleteDocuments}
          >
            <Trash2 size={13} />
            <span>{deleting ? '删除中...' : `删除 (${selectedIds.size})`}</span>
          </button>
        </div>
      )}

      <div
        style={{
          fontSize: 'var(--font-size-xs)',
          color: 'var(--text-muted)',
          padding: manageMode ? '8px 16px 12px' : '12px 16px',
          borderTop: manageMode ? 'none' : '1px solid var(--border-color)',
        }}
      >
        {manageMode ? `已选择 ${selectedIds.size} 个文件，可批量删除` : '原始文件已持久化保存，随时可查看'}
      </div>

      {contextMenu && (
        <DocumentContextMenu
          x={contextMenu.x}
          y={contextMenu.y}
          document={contextMenu.doc}
          onClose={() => setContextMenu(null)}
          onDelete={(targetDoc) => {
            setContextMenu(null);
            onRequestDeleteDoc?.(targetDoc);
          }}
        />
      )}
    </section>
  );
};
