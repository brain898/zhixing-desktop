import React, { useId } from 'react';

interface LogoProps {
  size?: number;
  /** 是否显示「知行有策」文字 */
  showText?: boolean;
  /** 深色背景下使用浅色版本 */
  inverse?: boolean;
}

/** 品牌标识：墨绿圆角底 + 展开的书页（知识资产）+ 金色星芒（AI 洞察），与 public/icon.svg 保持一致 */
export const LogoMark: React.FC<{ size?: number; inverse?: boolean }> = ({ size = 24, inverse = false }) => {
  const gradientId = `zx-logo-${useId().replace(/:/g, '')}`;

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      style={{ flexShrink: 0, display: 'block' }}
      aria-hidden
    >
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#3D7A62" />
          <stop offset="1" stopColor="#17382D" />
        </linearGradient>
      </defs>
      <rect
        width="64"
        height="64"
        rx="15"
        fill={inverse ? 'rgba(255, 255, 255, 0.10)' : `url(#${gradientId})`}
        stroke={inverse ? 'rgba(255, 255, 255, 0.16)' : 'none'}
      />
      <path d="M10.5 26C18.5 22.8 26.3 23.2 31 27.8V48.5C26.3 44.4 18.5 43.9 10.5 46.5Z" fill="#FFFFFF" />
      <path d="M53.5 26C45.5 22.8 37.7 23.2 33 27.8V48.5C37.7 44.4 45.5 43.9 53.5 46.5Z" fill="#FFFFFF" fillOpacity="0.8" />
      <path d="M32 5.5Q33.5 13.2 40.5 14.8Q33.5 16.4 32 24.1Q30.5 16.4 23.5 14.8Q30.5 13.2 32 5.5Z" fill="#E8B64C" />
    </svg>
  );
};

export const Logo: React.FC<LogoProps> = ({ size = 24, showText = true, inverse = false }) => {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
      <LogoMark size={size} inverse={inverse} />
      {showText && (
        <span
          style={{
            fontSize: '16px',
            fontWeight: 700,
            letterSpacing: '1px',
            color: inverse ? '#FFFFFF' : 'var(--text-primary)',
            userSelect: 'none',
            whiteSpace: 'nowrap',
          }}
        >
          知行有策
        </span>
      )}
    </div>
  );
};
