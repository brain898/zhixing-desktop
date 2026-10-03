import React from 'react';

interface EmptyStateProps {
  icon: React.ReactNode;
  title: React.ReactNode;
  description?: React.ReactNode;
  children?: React.ReactNode;
  style?: React.CSSProperties;
}

/** 统一空状态：图标 + 标题 + 说明 + 可选操作 */
export const EmptyState: React.FC<EmptyStateProps> = ({ icon, title, description, children, style }) => (
  <div className="zx-empty" style={style}>
    <div className="zx-empty-icon">{icon}</div>
    <h3 className="zx-empty-title">{title}</h3>
    {description && <div className="zx-empty-desc">{description}</div>}
    {children}
  </div>
);
