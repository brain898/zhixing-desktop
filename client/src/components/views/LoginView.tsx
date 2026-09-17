import React, { useState } from 'react';
import { Logo } from '../common/Logo';
import { useAuth } from '../../context/AuthContext';
import { Lock, User, AlertCircle } from 'lucide-react';

export const LoginView: React.FC = () => {
  const { login } = useAuth();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const isDev = import.meta.env.DEV || import.meta.env.VITE_ENABLE_DEMO_ACCOUNTS === 'true';

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username.trim() || !password) {
      setErrorMsg('请输入账号和密码');
      return;
    }

    try {
      setLoading(true);
      setErrorMsg(null);
      await login(username.trim(), password);
    } catch (err: any) {
      setErrorMsg(err.message || '登录失败，请检查网络或账号');
    } finally {
      setLoading(false);
    }
  };

  const setAdminDemo = () => {
    setUsername('admin');
    setPassword('Admin@Zhixing2026');
    setErrorMsg(null);
  };

  const setMemberDemo = () => {
    setUsername('member');
    setPassword('Member@Zhixing2026');
    setErrorMsg(null);
  };

  return (
    <div
      style={{
        flex: 1,
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        backgroundColor: 'var(--bg-secondary)',
        padding: '24px',
        userSelect: 'none',
      }}
    >
      <div
        style={{
          width: '380px',
          backgroundColor: '#FFFFFF',
          borderRadius: 'var(--radius-lg)',
          border: '1px solid var(--border-color)',
          boxShadow: '0 4px 20px rgba(0, 0, 0, 0.04)',
          padding: '36px 32px',
        }}
      >
        {/* 顶部 Logo 与系统名 */}
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', marginBottom: '28px' }}>
          <Logo size={32} />
          <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-secondary)', marginTop: '8px' }}>
            面向物业咨询的知识资产与 AI Skill 生产平台
          </div>
        </div>

        {/* 错误提示 */}
        {errorMsg && (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              padding: '8px 12px',
              borderRadius: 'var(--radius-sm)',
              backgroundColor: 'var(--error-bg)',
              color: 'var(--error-text)',
              fontSize: 'var(--font-size-xs)',
              marginBottom: '16px',
            }}
          >
            <AlertCircle size={14} style={{ flexShrink: 0 }} />
            <span>{errorMsg}</span>
          </div>
        )}

        {/* 登录表单 */}
        <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          <div>
            <label style={{ display: 'block', fontSize: 'var(--font-size-xs)', fontWeight: 500, color: 'var(--text-secondary)', marginBottom: '6px' }}>
              账号
            </label>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                padding: '0 12px',
                border: '1px solid var(--border-color)',
                borderRadius: 'var(--radius-md)',
                height: '38px',
                backgroundColor: '#FFFFFF',
              }}
            >
              <User size={15} color="var(--text-muted)" />
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="请输入用户名"
                style={{
                  border: 'none',
                  background: 'transparent',
                  width: '100%',
                  fontSize: 'var(--font-size-base)',
                  color: 'var(--text-primary)',
                }}
              />
            </div>
          </div>

          <div>
            <label style={{ display: 'block', fontSize: 'var(--font-size-xs)', fontWeight: 500, color: 'var(--text-secondary)', marginBottom: '6px' }}>
              密码
            </label>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                padding: '0 12px',
                border: '1px solid var(--border-color)',
                borderRadius: 'var(--radius-md)',
                height: '38px',
                backgroundColor: '#FFFFFF',
              }}
            >
              <Lock size={15} color="var(--text-muted)" />
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="请输入密码"
                style={{
                  border: 'none',
                  background: 'transparent',
                  width: '100%',
                  fontSize: 'var(--font-size-base)',
                  color: 'var(--text-primary)',
                }}
              />
            </div>
          </div>

          <button
            type="submit"
            className="btn-primary"
            disabled={loading}
            style={{ width: '100%', height: '38px', marginTop: '8px' }}
          >
            {loading ? '正在验证身份...' : '登录'}
          </button>
        </form>

        {/* 快速填入测试账号（仅在本地开发/测试模式开放受控凭据，生产环境自动剔除） */}
        {isDev && (
          <div
            style={{
              marginTop: '24px',
              paddingTop: '16px',
              borderTop: '1px solid var(--border-color)',
              fontSize: 'var(--font-size-xs)',
              color: 'var(--text-muted)',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px' }}>
              <span>快捷填充验证账号：</span>
              <span style={{ fontSize: '11px', color: 'var(--brand-accent)', backgroundColor: 'var(--brand-accent-light)', padding: '2px 6px', borderRadius: '4px' }}>
                仅限开发演示环境
              </span>
            </div>
            <div style={{ display: 'flex', gap: '8px' }}>
              <button
                type="button"
                onClick={setAdminDemo}
                className="btn-secondary"
                style={{ flex: 1, height: '28px', fontSize: '12px' }}
              >
                管理员 (文哲)
              </button>
              <button
                type="button"
                onClick={setMemberDemo}
                className="btn-secondary"
                style={{ flex: 1, height: '28px', fontSize: '12px' }}
              >
                普通成员 (李景研)
              </button>
            </div>
            <div style={{ marginTop: '8px', fontSize: '11px', color: 'var(--text-muted)', lineHeight: '1.4' }}>
              注意：正式生产环境需对接统一身份鉴权（SSO/OAuth），严禁直接连接真实商业资产。
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
