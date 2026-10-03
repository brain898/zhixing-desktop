import React from 'react';
import { X } from 'lucide-react';

// 五类主分类的低饱和标签色：浅底、无描边，与状态徽标形态区分
export const CATEGORY_COLORS: Record<string, { bg: string; text: string; border: string }> = {
  制度与标准: { bg: 'var(--brand-50)', text: 'var(--brand-700)', border: 'var(--brand-50)' },
  方法与工具: { bg: '#EDF2F6', text: '#3E5A73', border: '#EDF2F6' },
  项目案例: { bg: '#F1F3F2', text: '#4F5A54', border: '#F1F3F2' },
  指标数据: { bg: '#F7F1E4', text: '#7A5B1E', border: '#F7F1E4' },
  专家经验: { bg: '#EAF3F3', text: '#2F6464', border: '#EAF3F3' },
};
export const CATEGORY_STYLES = CATEGORY_COLORS;

export interface EditableStringListProps {
  items: string[];
  onChange: (items: string[]) => void;
  placeholder?: string;
  addButtonText?: string;
  emptyNotice?: string;
}

export const EditableStringList: React.FC<EditableStringListProps> = ({
  items,
  onChange,
  placeholder = '请输入内容...',
  addButtonText = '+ 添加',
  emptyNotice,
}) => {
  const handleItemChange = (index: number, val: string) => {
    const next = [...items];
    next[index] = val;
    onChange(next);
  };

  const handleRemove = (index: number) => {
    onChange(items.filter((_, i) => i !== index));
  };

  const handleAdd = () => {
    onChange([...items, '']);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
      <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
        <button
          type="button"
          onClick={handleAdd}
          style={{
            background: 'none',
            border: 'none',
            color: 'var(--brand-accent)',
            fontSize: 'var(--font-size-xs)',
            cursor: 'pointer',
            padding: '2px 6px',
          }}
        >
          {addButtonText}
        </button>
      </div>
      {items.length === 0 && emptyNotice && (
        <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)' }}>
          {emptyNotice}
        </div>
      )}
      {items.map((item, idx) => (
        <div key={idx} style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          <input
            type="text"
            value={item}
            onChange={(e) => handleItemChange(idx, e.target.value)}
            placeholder={placeholder}
            style={{
              flex: 1,
              height: '28px',
              padding: '0 8px',
              fontSize: 'var(--font-size-xs)',
              border: '1px solid var(--border-color)',
              borderRadius: 'var(--radius-sm)',
            }}
          />
          <button
            type="button"
            onClick={() => handleRemove(idx)}
            style={{
              background: 'none',
              border: 'none',
              color: 'var(--text-muted)',
              cursor: 'pointer',
              padding: '4px',
            }}
            title="删除"
          >
            <X size={13} />
          </button>
        </div>
      ))}
    </div>
  );
};
