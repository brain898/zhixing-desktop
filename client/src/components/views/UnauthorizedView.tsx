import React from 'react';
import { ShieldAlert } from 'lucide-react';

interface UnauthorizedViewProps {
  onBackToHome: () => void;
}

export const UnauthorizedView: React.FC<UnauthorizedViewProps> = ({ onBackToHome }) => {
  return (
    <div
      style={{
        flex: 1,
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        backgroundColor: 'var(--bg-primary)',
        padding: '24px',
        userSelect: 'none',
      }}
    >
      <div
        style={{
          maxWidth: '420px',
          textAlign: 'center',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          gap: '16px',
        }}
      >
        <div
          style={{
            width: '48px',
            height: '48px',
            borderRadius: '50%',
            backgroundColor: 'var(--error-bg)',
            color: 'var(--error-text)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <ShieldAlert size={24} />
        </div>

        <h2 style={{ fontSize: '18px', fontWeight: 600, color: 'var(--text-primary)' }}>
          当前账号无权访问此页面
        </h2>

        <p style={{ fontSize: '14px', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
          知识资产整理与管理功能仅对企业知识管理员开放。普通成员可通过授权的 AI 咨询入口使用可用知识。
        </p>

        <button className="btn-secondary" onClick={onBackToHome} style={{ marginTop: '8px' }}>
          返回工作台
        </button>
      </div>
    </div>
  );
};
