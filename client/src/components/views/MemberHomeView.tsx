import React from 'react';
import { Bot, Building } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { EmptyState } from '../ui/EmptyState';

export const MemberHomeView: React.FC = () => {
  const { user } = useAuth();

  return (
    <EmptyState
      icon={<Bot size={24} />}
      title="物业 AI 咨询服务（准备中）"
      style={{ userSelect: 'none' }}
      description={
        <>
          你好，{user?.display_name}。你当前以「普通成员」身份登录。
          <br />
          知识资产整理目前由企业知识管理员维护。普通成员知识问答属于第三模块，本阶段不提供入口或生成回答。
        </>
      }
    >
      <div className="zx-pill" style={{ marginTop: '8px', backgroundColor: 'var(--bg-primary)', boxShadow: 'var(--shadow-sm)' }}>
        <Building size={13} />
        <span>所属企业：{user?.organization_name}</span>
      </div>
    </EmptyState>
  );
};
