import React, { useState, useEffect } from 'react';
import { useAuth } from './context/AuthContext';
import { WindowHeader } from './components/layout/WindowHeader';
import { GlobalSidebar } from './components/layout/GlobalSidebar';
import { LoginView } from './components/views/LoginView';
import { KnowledgeWorkspaceView } from './components/views/KnowledgeWorkspaceView';
import { MemberHomeView } from './components/views/MemberHomeView';
import { UnauthorizedView } from './components/views/UnauthorizedView';
import { NavItemKey } from './types';
import { Wrench, Bot } from 'lucide-react';

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
      <div
        style={{
          height: '100%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: 'var(--text-secondary)',
          backgroundColor: 'var(--bg-primary)',
        }}
      >
        知行有策 桌面工作台启动中...
      </div>
    );
  }

  // 未登录状态展示登录页面
  if (!user) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
        <WindowHeader />
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
        return (
          <div
            style={{
              flex: 1,
              height: '100%',
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              color: 'var(--text-secondary)',
              backgroundColor: 'var(--bg-primary)',
              gap: '12px',
            }}
          >
            <div
              style={{
                width: '44px',
                height: '44px',
                borderRadius: '50%',
                backgroundColor: 'var(--bg-secondary)',
                color: 'var(--text-primary)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <Wrench size={22} />
            </div>
            <h3 style={{ fontSize: '16px', fontWeight: 600, color: 'var(--text-primary)' }}>
              Skill 工厂模块（准备中）
            </h3>
            <p style={{ fontSize: '13px', color: 'var(--text-secondary)', maxWidth: '420px', textAlign: 'center' }}>
              遵循产品契约：Skill 生产依赖已确认并索引完成的知识原子。当前阶段集中跑通知识资产整理。
            </p>
          </div>
        );
      case 'agent':
        if (user.role === 'member') {
          return <MemberHomeView />;
        }
        return (
          <div
            style={{
              flex: 1,
              height: '100%',
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              color: 'var(--text-secondary)',
              backgroundColor: 'var(--bg-primary)',
              gap: '12px',
            }}
          >
            <div
              style={{
                width: '44px',
                height: '44px',
                borderRadius: '50%',
                backgroundColor: 'var(--bg-secondary)',
                color: 'var(--brand-accent)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <Bot size={22} />
            </div>
            <h3 style={{ fontSize: '16px', fontWeight: 600, color: 'var(--text-primary)' }}>
              Agent 咨询管理（准备中）
            </h3>
            <p style={{ fontSize: '13px', color: 'var(--text-secondary)', maxWidth: '420px', textAlign: 'center' }}>
              管理员视角：用于配置向普通成员或客户开放的受控知识咨询边界与审计。
            </p>
          </div>
        );
      default:
        return null;
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      <WindowHeader />
      <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
        <GlobalSidebar currentNav={currentNav} onSelectNav={setCurrentNav} />
        <main style={{ flex: 1, minWidth: 0, height: '100%', overflow: 'hidden' }}>
          {renderContent()}
        </main>
      </div>
    </div>
  );
};

export const App: React.FC = () => {
  return <AppContent />;
};
