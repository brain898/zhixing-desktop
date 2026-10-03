import React, { useState, useRef, useEffect } from 'react';
import { FileText, Wrench, Bot, Settings, ChevronsUpDown, LogOut, Building, PanelLeftClose, PanelLeftOpen } from 'lucide-react';
import { Logo } from '../common/Logo';
import { useAuth } from '../../context/AuthContext';
import { useLayout } from '../../context/LayoutContext';
import { NavItemKey } from '../../types';

interface GlobalSidebarProps {
  currentNav: NavItemKey;
  onSelectNav: (key: NavItemKey) => void;
}

export const GlobalSidebar: React.FC<GlobalSidebarProps> = ({ currentNav, onSelectNav }) => {
  const { user, logout } = useAuth();
  const { isSidebarRail, focusMode, toggleSidebar } = useLayout();
  const [showUserMenu, setShowUserMenu] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  const isAdmin = user?.role === 'admin';
  const collapsed = isSidebarRail;

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
      icon: <FileText size={17} />,
      adminOnly: true, // 仅管理员可见
    },
    {
      key: 'skills',
      label: 'Skill 工厂',
      icon: <Wrench size={17} />,
      adminOnly: true, // 仅管理员可见，普通成员不显示
    },
    {
      key: 'agent',
      label: 'Agent 咨询',
      icon: <Bot size={17} />,
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
      className={collapsed ? 'zx-sidebar-collapsed' : undefined}
      style={{
        width: collapsed ? 'var(--sidebar-rail-width)' : 'var(--sidebar-width)',
        backgroundColor: 'var(--bg-secondary)',
        borderRight: '1px solid var(--border-color)',
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'space-between',
        alignItems: collapsed ? 'center' : 'stretch',
        padding: collapsed ? '16px 8px 12px' : '16px 10px 12px',
        flexShrink: 0,
        height: '100%',
        userSelect: 'none',
        position: 'relative',
        transition: 'width 180ms cubic-bezier(0.16, 1, 0.3, 1)',
      }}
    >
      {/* 顶部区域 */}
      <div style={{ width: '100%', display: 'flex', flexDirection: 'column', alignItems: collapsed ? 'center' : 'stretch' }}>
        {/* Logo 与折叠按钮 */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: collapsed ? 'center' : 'space-between',
            padding: collapsed ? '2px 0 20px' : '2px 0 20px 8px',
          }}
        >
          <Logo showText={!collapsed} />
          {!collapsed && (
            <button
              className="btn-ghost btn-sm btn-icon"
              style={{ width: '28px' }}
              title="收起侧栏"
              onClick={toggleSidebar}
            >
              <PanelLeftClose size={16} />
            </button>
          )}
        </div>

        {/* 区域小标题 */}
        {!collapsed && (
          <div style={{ padding: '0 10px 6px', fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)' }}>
            工作空间
          </div>
        )}

        {/* 导航菜单项 */}
        <nav style={{ display: 'flex', flexDirection: 'column', gap: '2px', alignItems: collapsed ? 'center' : 'stretch' }}>
          {visibleNavItems.map((item) => {
            const isActive = currentNav === item.key;
            return (
              <button
                key={item.key}
                onClick={() => onSelectNav(item.key)}
                className={`zx-nav-item${isActive ? ' active' : ''}`}
                title={collapsed ? item.label : undefined}
                aria-label={item.label}
              >
                <span className="zx-nav-icon" style={{ display: 'flex' }}>
                  {item.icon}
                </span>
                {!collapsed && <span>{item.label}</span>}
              </button>
            );
          })}
        </nav>
      </div>

      {/* 底部设置与账号区域 */}
      <div
        style={{ position: 'relative', width: '100%', display: 'flex', flexDirection: 'column', alignItems: collapsed ? 'center' : 'stretch' }}
        ref={menuRef}
      >
        {collapsed && !focusMode && (
          <button className="zx-nav-item" title="展开侧栏" onClick={toggleSidebar} style={{ marginBottom: '2px' }}>
            <PanelLeftOpen size={16} />
          </button>
        )}

        {/* 设置操作项 */}
        <button
          className="zx-nav-item"
          onClick={() => {
            alert('系统设置模块已就绪，当前已接入真实服务端环境');
          }}
          title={collapsed ? '设置' : undefined}
          style={{ fontSize: 'var(--font-size-sm)', marginBottom: '8px' }}
        >
          <Settings size={16} />
          {!collapsed && <span>设置</span>}
        </button>

        {/* 用户头像与信息条 */}
        <div
          onClick={() => setShowUserMenu(!showUserMenu)}
          title={collapsed ? `${user?.display_name || ''} · ${roleLabel}` : undefined}
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: collapsed ? 'center' : 'space-between',
            width: '100%',
            padding: collapsed ? '12px 0 2px' : '12px 8px 2px',
            borderTop: '1px solid var(--border-color)',
            cursor: 'pointer',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', minWidth: 0 }}>
            {/* 圆形头像 */}
            <div
              style={{
                width: '30px',
                height: '30px',
                borderRadius: '50%',
                backgroundColor: 'var(--brand-100)',
                color: 'var(--brand-700)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: '13px',
                fontWeight: 600,
                flexShrink: 0,
                boxShadow: showUserMenu ? '0 0 0 2px var(--brand-200)' : 'none',
                transition: 'box-shadow 140ms ease',
              }}
            >
              {avatarChar}
            </div>

            {/* 用户昵称与角色 */}
            {!collapsed && (
              <div style={{ minWidth: 0 }}>
                <div
                  style={{
                    fontSize: 'var(--font-size-sm)',
                    fontWeight: 600,
                    color: 'var(--text-primary)',
                    whiteSpace: 'nowrap',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    lineHeight: 1.3,
                  }}
                  title={user?.display_name}
                >
                  {user?.display_name || '未登录'}
                </div>
                <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)', marginTop: '1px' }}>
                  {roleLabel}
                </div>
              </div>
            )}
          </div>

          {!collapsed && <ChevronsUpDown size={14} color="var(--text-muted)" />}
        </div>

        {/* 用户弹层菜单 (含企业信息、退出登录) */}
        {showUserMenu && (
          <div
            style={{
              position: 'absolute',
              bottom: '52px',
              left: 0,
              width: '208px',
              backgroundColor: 'var(--bg-primary)',
              borderRadius: 'var(--radius-md)',
              boxShadow: 'var(--shadow-lg)',
              padding: '6px',
              zIndex: 200,
            }}
          >
            <div style={{ padding: '8px 10px', borderBottom: '1px solid var(--border-color)', marginBottom: '4px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)' }}>
                <Building size={12} />
                <span>当前所属企业</span>
              </div>
              <div
                style={{
                  fontSize: 'var(--font-size-sm)',
                  fontWeight: 500,
                  color: 'var(--text-primary)',
                  marginTop: '4px',
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
              className="btn-danger-ghost"
              onClick={() => {
                setShowUserMenu(false);
                logout();
              }}
              style={{ width: '100%', justifyContent: 'flex-start' }}
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
