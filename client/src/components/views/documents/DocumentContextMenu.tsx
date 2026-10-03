import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Trash2 } from 'lucide-react';
import { DocumentItem } from '../../../types';

interface DocumentContextMenuProps {
  x: number;
  y: number;
  document: DocumentItem;
  onClose: () => void;
  onDelete: (doc: DocumentItem) => void;
}

export const DocumentContextMenu: React.FC<DocumentContextMenuProps> = ({
  x,
  y,
  document: targetDoc,
  onClose,
  onDelete,
}) => {
  const menuRef = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState({ top: y, left: x });

  // 边界防溢出计算：确保菜单在鼠标附近且不超出视口
  useLayoutEffect(() => {
    if (!menuRef.current) return;
    const rect = menuRef.current.getBoundingClientRect();
    const pad = 8;
    const winWidth = window.innerWidth || window.document.documentElement.clientWidth;
    const winHeight = window.innerHeight || window.document.documentElement.clientHeight;

    let nextLeft = x;
    let nextTop = y;

    if (nextLeft + rect.width > winWidth - pad) {
      nextLeft = Math.max(pad, winWidth - rect.width - pad);
    }
    if (nextTop + rect.height > winHeight - pad) {
      nextTop = Math.max(pad, winHeight - rect.height - pad);
    }

    setPos({ top: nextTop, left: nextLeft });
  }, [x, y]);

  // 键盘 Esc 关闭与视口事件监听
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        onClose();
      }
    };

    const handleScrollOrResize = () => {
      onClose();
    };

    window.addEventListener('keydown', handleKeyDown);
    window.addEventListener('scroll', handleScrollOrResize, true);
    window.addEventListener('resize', handleScrollOrResize);

    return () => {
      window.removeEventListener('keydown', handleKeyDown);
      window.removeEventListener('scroll', handleScrollOrResize, true);
      window.removeEventListener('resize', handleScrollOrResize);
    };
  }, [onClose]);

  return (
    <>
      {/* 点击外部透明遮罩，阻断下层点击并关闭菜单 */}
      <div
        data-testid="context-menu-backdrop"
        style={{
          position: 'fixed',
          inset: 0,
          zIndex: 1090,
          background: 'transparent',
        }}
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          onClose();
        }}
        onContextMenu={(e) => {
          e.preventDefault();
          e.stopPropagation();
          onClose();
        }}
      />

      {/* 右键菜单主体 */}
      <div
        ref={menuRef}
        data-testid="document-context-menu"
        style={{
          position: 'fixed',
          top: `${pos.top}px`,
          left: `${pos.left}px`,
          zIndex: 1100,
          backgroundColor: '#FFFFFF',
          borderRadius: 'var(--radius-md)',
          boxShadow: 'var(--shadow-lg)',
          border: '1px solid var(--border-color)',
          padding: '4px',
          minWidth: '136px',
          userSelect: 'none',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <button
          type="button"
          data-testid="context-menu-delete-btn"
          onClick={(e) => {
            e.stopPropagation();
            onDelete(targetDoc);
          }}
          style={{
            width: '100%',
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            padding: '7px 10px',
            border: 'none',
            borderRadius: 'var(--radius-sm)',
            backgroundColor: 'transparent',
            color: 'var(--error-text)',
            fontSize: 'var(--font-size-xs)',
            fontWeight: 500,
            cursor: 'pointer',
            textAlign: 'left',
            transition: 'background-color 100ms ease',
          }}
          onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = 'var(--error-bg)')}
          onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = 'transparent')}
        >
          <Trash2 size={14} />
          <span>删除文件</span>
        </button>
      </div>
    </>
  );
};
