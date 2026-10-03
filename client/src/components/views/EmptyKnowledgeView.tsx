import React from 'react';
import { Plus, FolderOpen } from 'lucide-react';

interface EmptyKnowledgeViewProps {
  onImportClick: () => void;
}

// AC01：全新无资料时主工作区仅显示一句引导语与导入按钮
export const EmptyKnowledgeView: React.FC<EmptyKnowledgeViewProps> = ({ onImportClick }) => {
  return (
    <div className="zx-empty" style={{ userSelect: 'none', gap: '20px' }}>
      <div className="zx-empty-icon">
        <FolderOpen size={24} />
      </div>
      <p style={{ fontSize: 'var(--font-size-md)', color: 'var(--text-secondary)', lineHeight: 1.6 }}>
        导入第一份物业资料，开始建立企业知识库。
      </p>
      <button className="btn-primary btn-lg" onClick={onImportClick}>
        <Plus size={16} />
        <span>导入文件</span>
      </button>
    </div>
  );
};
