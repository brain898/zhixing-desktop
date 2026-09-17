import React from 'react';
import { Minus, Square, X } from 'lucide-react';

export const WindowHeader: React.FC = () => {
  const handleMinimize = () => {
    window.electronAPI?.minimize();
  };

  const handleMaximize = () => {
    window.electronAPI?.maximize();
  };

  const handleClose = () => {
    window.electronAPI?.close();
  };

  return (
    <header
      className="titlebar-drag"
      style={{
        height: 'var(--header-height)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        backgroundColor: 'var(--bg-primary)',
        borderBottom: '1px solid var(--border-color)',
        userSelect: 'none',
        paddingLeft: '16px',
        zIndex: 100,
      }}
    >
      <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)' }}>
        知行有策 · 企业物业咨询数字化平台
      </div>

      <div
        className="titlebar-no-drag"
        style={{
          display: 'flex',
          alignItems: 'center',
          height: '100%',
        }}
      >
        <button
          onClick={handleMinimize}
          title="最小化"
          style={{
            width: '44px',
            height: '100%',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: 'var(--text-secondary)',
          }}
          onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = 'var(--bg-secondary)')}
          onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = 'transparent')}
        >
          <Minus size={14} />
        </button>

        <button
          onClick={handleMaximize}
          title="最大化"
          style={{
            width: '44px',
            height: '100%',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: 'var(--text-secondary)',
          }}
          onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = 'var(--bg-secondary)')}
          onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = 'transparent')}
        >
          <Square size={12} />
        </button>

        <button
          onClick={handleClose}
          title="关闭"
          style={{
            width: '44px',
            height: '100%',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: 'var(--text-secondary)',
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.backgroundColor = 'var(--error-text)';
            e.currentTarget.style.color = '#FFFFFF';
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.backgroundColor = 'transparent';
            e.currentTarget.style.color = 'var(--text-secondary)';
          }}
        >
          <X size={14} />
        </button>
      </div>
    </header>
  );
};
