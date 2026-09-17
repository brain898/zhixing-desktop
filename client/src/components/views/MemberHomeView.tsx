import React from 'react';
import { Bot, AlertCircle } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';

export const MemberHomeView: React.FC = () => {
  const { user } = useAuth();

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
        padding: '32px',
        userSelect: 'none',
      }}
    >
      <div
        style={{
          maxWidth: '520px',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          textAlign: 'center',
          gap: '16px',
        }}
      >
        <div
          style={{
            width: '48px',
            height: '48px',
            borderRadius: '50%',
            backgroundColor: 'var(--bg-secondary)',
            color: 'var(--brand-accent)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <Bot size={24} />
        </div>

        <h2 style={{ fontSize: '18px', fontWeight: 600, color: 'var(--text-primary)' }}>
          物业 AI 咨询服务（准备中）
        </h2>

        <p style={{ fontSize: '14px', color: 'var(--text-secondary)', lineHeight: 1.6 }}>
          你好，{user?.display_name}。你当前以「普通成员」身份登录。
          <br />
          知识资产整理与 Skill 生产目前由企业知识管理员维护中。当企业内部可用知识确认并发布后，你将在此通过受控 AI 咨询检索与使用专业规范。
        </p>

        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            padding: '10px 16px',
            borderRadius: 'var(--radius-md)',
            backgroundColor: 'var(--bg-secondary)',
            border: '1px solid var(--border-color)',
            fontSize: 'var(--font-size-xs)',
            color: 'var(--text-secondary)',
            marginTop: '8px',
          }}
        >
          <AlertCircle size={14} color="var(--pending-text)" />
          <span>所属企业：{user?.organization_name}</span>
        </div>
      </div>
    </div>
  );
};
