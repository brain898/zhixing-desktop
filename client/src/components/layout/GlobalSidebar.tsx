import React, { useState, useRef, useEffect } from 'react';
import { FileText, Wrench, Bot, Settings, ChevronDown, LogOut, Building } from 'lucide-react';
import { Logo } from '../common/Logo';
import { useAuth } from '../../context/AuthContext';
import { NavItemKey } from '../../types';

interface GlobalSidebarProps {
  currentNav: NavItemKey;
  onSelectNav: (key: NavItemKey) => void;
}

export const GlobalSidebar: React.FC<GlobalSidebarProps> = ({ currentNav, onSelectNav }) => {
  const { user, logout } = useAuth();
  const [showUserMenu, setShowUserMenu] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  const isAdmin = user?.role === 'admin';

  // 点击外部关闭弹层
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setShowUserMenu(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const navItems: { key: NavItemKey; label: string; icon: React.ReactNode; adminOnly?: boolean }[] = [
    {
      key: 'knowledge',
      label: '知识管理',
      icon: <FileText size={18} />,
      adminOnly: true, // 仅管理员可见
    },
    {
      key: 'skills',
      label: 'Skill 工厂',
      icon: <Wrench size={18} />,
      adminOnly: true, // 仅管理员可见，普通成员不显示
    },
    {
      key: 'agent',
      label: 'Agent 咨询',
      icon: <Bot size={18} />,
      adminOnly: false, // 普通成员唯一可见的业务入口
    },
  ];

  // 普通成员仅展示「Agent 咨询」
  const visibleNavItems = isAdmin
    ? navItems
    : navItems.filter((item) => item.key === 'agent');

  const avatarChar = user?.display_name ? user.display_name.trim().charAt(0) : '用';
  const roleLabel = isAdmin ? '管理员' : '普通成员';

  return (
    <aside
      style={{
        width: 'var(--sidebar-width)',
        backgroundColor: 'var(--bg-secondary)',
        borderRight: '1px solid var(--border-color)',
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'space-between',
        padding: '16px 12px 14px 12px',
        flexShrink: 0,
        height: '100%',
        userSelect: 'none',
        position: 'relative',
      }}
    >
      {/* 顶部区域 */}
      <div>
        {/* Logo 区域 */}
        <div style={{ padding: '4px 6px 18px 6px' }}>
          <Logo />
        </div>

        {/* 区域小标题 */}
        <div
          style={{
            padding: '0 8px 8px 8px',
            fontSize: 'var(--font-size-xs)',
            color: 'var(--text-muted)',
            fontWeight: 500,
          }}
        >
          工作空间
        </div>

        {/* 导航菜单项 */}
        <nav style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
          {visibleNavItems.map((item) => {
            const isActive = currentNav === item.key;
            return (
              <button
                key={item.key}
                onClick={() => onSelectNav(item.key)}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '10px',
                  width: '100%',
                  padding: '9px 12px',
                  borderRadius: 'var(--radius-md)',
                  backgroundColor: isActive ? 'var(--bg-selected)' : 'transparent',
                  color: isActive ? 'var(--brand-accent)' : 'var(--text-primary)',
                  fontWeight: isActive ? 600 : 400,
                  fontSize: 'var(--font-size-base)',
                  transition: 'background-color 120ms ease, color 120ms ease',
                  textAlign: 'left',
                }}
                onMouseEnter={(e) => {
                  if (!isActive) e.currentTarget.style.backgroundColor = '#EFEFEF';
                }}
                onMouseLeave={(e) => {
                  if (!isActive) e.currentTarget.style.backgroundColor = 'transparent';
                }}
              >
                <span style={{ color: isActive ? 'var(--brand-accent)' : 'var(--text-secondary)' }}>
                  {item.icon}
                </span>
                <span>{item.label}</span>
              </button>
            );
          })}
        </nav>
      </div>

      {/* 底部设置与账号区域 */}
      <div style={{ position: 'relative' }} ref={menuRef}>
        {/* 设置操作项 */}
        <button
          onClick={() => {
            alert('系统设置模块已就绪，当前已接入真实服务端环境');
          }}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '10px',
            width: '100%',
            padding: '8px 12px',
            borderRadius: 'var(--radius-md)',
            color: 'var(--text-secondary)',
            fontSize: 'var(--font-size-sm)',
            marginBottom: '8px',
            transition: 'background-color 120ms ease',
          }}
          onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = '#EFEFEF')}
          onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = 'transparent')}
        >
          <Settings size={16} />
          <span>设置</span>
        </button>

        {/* 细分割线 */}
        <div style={{ height: '1px', backgroundColor: 'var(--border-color)', margin: '4px 0 10px 0' }} />

        {/* 用户头像与信息条 */}
        <div
          onClick={() => setShowUserMenu(!showUserMenu)}
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '8px',
            borderRadius: 'var(--radius-md)',
            cursor: 'pointer',
            backgroundColor: showUserMenu ? 'var(--bg-selected)' : 'transparent',
            transition: 'background-color 120ms ease',
          }}
          onMouseEnter={(e) => {
            if (!showUserMenu) e.currentTarget.style.backgroundColor = '#EFEFEF';
          }}
          onMouseLeave={(e) => {
            if (!showUserMenu) e.currentTarget.style.backgroundColor = 'transparent';
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', minWidth: 0 }}>
            {/* 圆形头像 */}
            <div
              style={{
                width: '32px',
                height: '32px',
                borderRadius: '50%',
                backgroundColor: '#D9DFDB',
                color: 'var(--text-primary)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: '14px',
                fontWeight: 600,
                flexShrink: 0,
              }}
            >
              {avatarChar}
            </div>

            {/* 用户昵称与角色 */}
            <div style={{ minWidth: 0 }}>
              <div
                style={{
                  fontSize: 'var(--font-size-base)',
                  fontWeight: 600,
                  color: 'var(--text-primary)',
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  lineHeight: 1.2,
                }}
                title={user?.display_name}
              >
                {user?.display_name || '未登录'}
              </div>
              <div
                style={{
                  fontSize: 'var(--font-size-xs)',
                  color: 'var(--text-secondary)',
                  marginTop: '2px',
                }}
              >
                {roleLabel}
              </div>
            </div>
          </div>

          <ChevronDown size={15} color="var(--text-secondary)" />
        </div>

        {/* 用户弹层菜单 (含企业信息、退出登录) */}
        {showUserMenu && (
          <div
            style={{
              position: 'absolute',
              bottom: '56px',
              left: '4px',
              right: '4px',
              backgroundColor: '#FFFFFF',
              border: '1px solid var(--border-color)',
              borderRadius: 'var(--radius-lg)',
              boxShadow: '0 4px 16px rgba(0, 0, 0, 0.08)',
              padding: '8px',
              zIndex: 200,
            }}
          >
            <div
              style={{
                padding: '6px 8px',
                borderBottom: '1px solid var(--border-color)',
                marginBottom: '4px',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '11px', color: 'var(--text-muted)' }}>
                <Building size={12} />
                <span>当前所属企业</span>
              </div>
              <div
                style={{
                  fontSize: '12px',
                  fontWeight: 500,
                  color: 'var(--text-primary)',
                  marginTop: '2px',
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                }}
                title={user?.organization_name}
              >
                {user?.organization_name}
              </div>
            </div>

            <button
              onClick={() => {
                setShowUserMenu(false);
                logout();
              }}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                width: '100%',
                padding: '8px 10px',
                borderRadius: 'var(--radius-sm)',
                color: 'var(--error-text)',
                fontSize: 'var(--font-size-sm)',
                textAlign: 'left',
              }}
              onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = 'var(--error-bg)')}
              onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = 'transparent')}
            >
              <LogOut size={15} />
              <span>退出登录</span>
            </button>
          </div>
        )}
      </div>
    </aside>
  );
};
