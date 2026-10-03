import React, { useState } from 'react';
import { Logo } from '../common/Logo';
import { useAuth } from '../../context/AuthContext';
import { Lock, User, AlertCircle, CircleCheck } from 'lucide-react';


const inputStyle: React.CSSProperties = {
  border: 'none',
  background: 'transparent',
  width: '100%',
  fontSize: 'var(--font-size-base)',
  color: 'var(--text-primary)',
  boxShadow: 'none',
};

const labelStyle: React.CSSProperties = {
  display: 'block',
  fontSize: 'var(--font-size-sm)',
  fontWeight: 500,
  color: 'var(--text-secondary)',
  marginBottom: '6px',
};

const highlights = [
  '导入制度、预案与规程，自动整理为知识条目',
  '每条知识都能回到原文段落逐项核对',
  '确认后的知识用于 AI Skill 与咨询服务',
];

export const LoginView: React.FC = () => {
  const { login } = useAuth();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const demoAdminUsername = import.meta.env.VITE_DEMO_ADMIN_USERNAME || '';
  const demoAdminPassword = import.meta.env.VITE_DEMO_ADMIN_PASSWORD || '';
  const demoMemberUsername = import.meta.env.VITE_DEMO_MEMBER_USERNAME || '';
  const demoMemberPassword = import.meta.env.VITE_DEMO_MEMBER_PASSWORD || '';
  const demoEnabled =
    import.meta.env.VITE_ENABLE_DEMO_ACCOUNTS === 'true' &&
    Boolean(demoAdminUsername && demoAdminPassword && demoMemberUsername && demoMemberPassword);

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
    setUsername(demoAdminUsername);
    setPassword(demoAdminPassword);
    setErrorMsg(null);
  };

  const setMemberDemo = () => {
    setUsername(demoMemberUsername);
    setPassword(demoMemberPassword);
    setErrorMsg(null);
  };

  return (
    <div style={{ flex: 1, height: '100%', display: 'flex', userSelect: 'none' }}>
      {/* 左侧品牌区 */}
      <div
        style={{
          width: '42%',
          maxWidth: '560px',
          minWidth: '360px',
          background: 'linear-gradient(160deg, #22513F 0%, #17382D 48%, #0F271F 100%)',
          color: 'var(--brand-100)',
          padding: '48px 56px',
          display: 'flex',
          flexDirection: 'column',
          position: 'relative',
          overflow: 'hidden',
        }}
      >
        <div aria-hidden className="zx-login-texture" />
        <div
          aria-hidden
          style={{
            position: 'absolute',
            right: '-160px',
            bottom: '-160px',
            width: '480px',
            height: '480px',
            borderRadius: '50%',
            border: '80px solid rgba(255, 255, 255, 0.04)',
          }}
        />
        <div
          aria-hidden
          style={{
            position: 'absolute',
            right: '-40px',
            bottom: '-40px',
            width: '240px',
            height: '240px',
            borderRadius: '50%',
            border: '1px solid rgba(232, 182, 76, 0.18)',
          }}
        />
        <div style={{ position: 'relative' }}>
          <Logo size={34} inverse />
        </div>
        <div style={{ margin: 'auto 0', position: 'relative' }}>
          <div style={{ width: '32px', height: '3px', borderRadius: '2px', backgroundColor: '#E8B64C', marginBottom: '20px' }} />
          <h1 style={{ fontSize: '30px', lineHeight: 1.45, fontWeight: 600, color: '#FFFFFF', letterSpacing: '0.5px' }}>
            把企业资料
            <br />
            整理成可追溯的知识资产
          </h1>
          <ul style={{ listStyle: 'none', marginTop: '28px', display: 'flex', flexDirection: 'column', gap: '14px' }}>
            {highlights.map((text) => (
              <li key={text} style={{ display: 'flex', alignItems: 'center', gap: '10px', fontSize: 'var(--font-size-base)' }}>
                <CircleCheck size={16} color="var(--brand-300)" style={{ flexShrink: 0 }} />
                {text}
              </li>
            ))}
          </ul>
        </div>
        <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--brand-300)', position: 'relative' }}>
          面向物业咨询的知识资产与 AI Skill 生产平台
        </div>
      </div>

      {/* 右侧登录表单 */}
      <div
        style={{
          flex: 1,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          backgroundColor: 'var(--bg-primary)',
          padding: '32px',
          overflow: 'auto',
        }}
      >
        <div style={{ width: '340px' }}>
          <h2 style={{ fontSize: '24px', fontWeight: 600, color: 'var(--text-primary)' }}>登录</h2>
          <div style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-muted)', marginTop: '4px', marginBottom: '28px' }}>
            使用企业分配的账号进入工作空间
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
                fontSize: 'var(--font-size-sm)',
                marginBottom: '16px',
              }}
            >
              <AlertCircle size={14} style={{ flexShrink: 0 }} />
              <span>{errorMsg}</span>
            </div>
          )}

          {/* 登录表单 */}
          <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '18px' }}>
            <div>
              <label htmlFor="login-username" style={labelStyle}>
                账号
              </label>
              <div className="zx-login-field">
                <User size={16} color="var(--text-muted)" />
                <input
                  id="login-username"
                  type="text"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="请输入用户名"
                  style={inputStyle}
                />
              </div>
            </div>

            <div>
              <label htmlFor="login-password" style={labelStyle}>
                密码
              </label>
              <div className="zx-login-field">
                <Lock size={16} color="var(--text-muted)" />
                <input
                  id="login-password"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="请输入密码"
                  style={inputStyle}
                />
              </div>
            </div>

            <button type="submit" className="btn-primary btn-lg" disabled={loading} style={{ width: '100%', marginTop: '10px' }}>
              {loading ? '正在验证身份...' : '登录'}
            </button>
          </form>

          {/* 快速填入测试账号（仅在本地开发/测试模式开放受控凭据，生产环境自动剔除） */}
          {demoEnabled && (
            <div
              style={{
                marginTop: '28px',
                paddingTop: '16px',
                borderTop: '1px solid var(--border-color)',
                fontSize: 'var(--font-size-xs)',
                color: 'var(--text-muted)',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px' }}>
                <span>快捷填充验证账号：</span>
                <span className="zx-tag">仅限开发演示环境</span>
              </div>
              <div style={{ display: 'flex', gap: '8px' }}>
                <button type="button" onClick={setAdminDemo} className="btn-secondary btn-sm" style={{ flex: 1 }}>
                  管理员 (文哲)
                </button>
                <button type="button" onClick={setMemberDemo} className="btn-secondary btn-sm" style={{ flex: 1 }}>
                  普通成员 (李景研)
                </button>
              </div>
              <div style={{ marginTop: '8px', lineHeight: 1.5 }}>
                注意：正式生产环境需对接统一身份鉴权（SSO/OAuth），严禁直接连接真实商业资产。
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
