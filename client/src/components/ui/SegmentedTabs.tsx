import React from 'react';

export interface SegmentedOption<T extends string> {
  key: T;
  label: React.ReactNode;
  count?: number;
  icon?: React.ReactNode;
  testId?: string;
  title?: string;
}

interface SegmentedTabsProps<T extends string> {
  options: SegmentedOption<T>[];
  value: T | null;
  onChange: (key: T) => void;
  style?: React.CSSProperties;
}

/** 分段切换：用于状态筛选、视图切换 */
export function SegmentedTabs<T extends string>({ options, value, onChange, style }: SegmentedTabsProps<T>) {
  return (
    <div className="zx-segmented" style={style}>
      {options.map((opt) => (
        <button
          key={opt.key}
          type="button"
          className={value === opt.key ? 'active' : undefined}
          data-testid={opt.testId}
          title={opt.title}
          onClick={() => onChange(opt.key)}
        >
          {opt.icon}
          <span>{opt.label}</span>
          {typeof opt.count === 'number' && <span className="count">{opt.count}</span>}
        </button>
      ))}
    </div>
  );
}
