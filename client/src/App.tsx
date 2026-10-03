import React, { useState, useEffect } from 'react';
import { useAuth } from './context/AuthContext';
import { GlobalSidebar } from './components/layout/GlobalSidebar';
import { LoginView } from './components/views/LoginView';
import { KnowledgeWorkspaceView } from './components/views/KnowledgeWorkspaceView';
import { MemberHomeView } from './components/views/MemberHomeView';
import { UnauthorizedView } from './components/views/UnauthorizedView';
import { NavItemKey } from './types';
import { SkillFactoryView } from './components/views/skills/SkillFactoryView';
import { ConsultView } from './components/views/consult/ConsultView';
import { LogoMark } from './components/common/Logo';

export const AppContent: React.FC = () => {
  const { user, isLoading } = useAuth();
  const [currentNav, setCurrentNav] = useState<NavItemKey>('knowledge');

  // 当用户角色变化时，设置合适的默认页面 (AC33 登录分流)
  useEffect(() => {
    if (user) {
      if (user.role === 'admin') {
        setCurrentNav('knowledge');
      } else {
        setCurrentNav('agent');
      }
    }
  }, [user]);

  if (isLoading) {
    return (
      <div className="zx-boot">
        <span className="zx-boot-mark">
          <LogoMark size={48} />
        </span>
        知行有策 桌面工作台启动中...
      </div>
    );
  }

  // 未登录状态展示登录页面
  if (!user) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
        <LoginView />
      </div>
    );
  }

  // 渲染主工作区视图
  const renderContent = () => {
    // 权限守卫：普通成员仅允许访问 Agent 咨询，其余管理和生产模块均被拦截 (AC02, AC34)
    if (user.role !== 'admin' && currentNav !== 'agent') {
      return <UnauthorizedView onBackToHome={() => setCurrentNav('agent')} />;
    }

    switch (currentNav) {
      case 'knowledge':
        return <KnowledgeWorkspaceView />;
      case 'skills':
        return <SkillFactoryView />;
      case 'agent':
        if (user.role === 'member') {
          return <MemberHomeView />;
        }
        return <ConsultView />;
      default:
        return null;
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
        <GlobalSidebar currentNav={currentNav} onSelectNav={setCurrentNav} />
        <main style={{ flex: 1, minWidth: 0, height: '100%', overflow: 'hidden', display: 'flex', flexDirection: 'column', backgroundColor: 'var(--bg-secondary)' }}>
          {renderContent()}
        </main>
      </div>
    </div>
  );
};

export const App: React.FC = () => {
  return <AppContent />;
};
