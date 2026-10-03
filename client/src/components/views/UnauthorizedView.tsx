import React from 'react';
import { ShieldAlert } from 'lucide-react';
import { EmptyState } from '../ui/EmptyState';

interface UnauthorizedViewProps {
  onBackToHome: () => void;
}

export const UnauthorizedView: React.FC<UnauthorizedViewProps> = ({ onBackToHome }) => {
  return (
    <EmptyState
      icon={<ShieldAlert size={24} color="var(--danger-text)" />}
      title="当前账号无权访问此页面"
      description="知识资产整理与管理功能仅对企业知识管理员开放。普通成员可通过授权的 AI 咨询入口使用可用知识。"
      style={{ userSelect: 'none' }}
    >
      <button className="btn-secondary" onClick={onBackToHome} style={{ marginTop: '8px' }}>
        返回工作台
      </button>
    </EmptyState>
  );
};
