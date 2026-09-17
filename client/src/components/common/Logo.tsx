import React from 'react';

export const Logo: React.FC<{ size?: number }> = ({ size = 26 }) => {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
      <svg
        width={size}
        height={size}
        viewBox="0 0 32 32"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
        style={{ flexShrink: 0 }}
      >
        {/* 左上折叠几何页 */}
        <path
          d="M6 6L20 4L14 18L6 16Z"
          fill="#285C49"
        />
        {/* 右下折叠几何页 */}
        <path
          d="M14 18L26 14L22 28L10 26Z"
          fill="#3B7D66"
        />
        {/* 中心微反光面 */}
        <path
          d="M14 18L20 4L26 14Z"
          fill="#1C4537"
          opacity="0.9"
        />
      </svg>
      <span
        style={{
          fontSize: '17px',
          fontWeight: 600,
          letterSpacing: '0.5px',
          color: 'var(--text-primary)',
          userSelect: 'none',
        }}
      >
        知行有策
      </span>
    </div>
  );
};
