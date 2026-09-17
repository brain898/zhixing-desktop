import React from 'react';
import { Plus } from 'lucide-react';

interface EmptyKnowledgeViewProps {
  onImportClick: () => void;
}

export const EmptyKnowledgeView: React.FC<EmptyKnowledgeViewProps> = ({ onImportClick }) => {
  return (
    <div
      style={{
        flex: 1,
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        backgroundColor: 'var(--bg-primary)',
        padding: '24px',
        userSelect: 'none',
      }}
    >
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          gap: '20px',
          maxWidth: '480px',
          textAlign: 'center',
        }}
      >
        <p
          style={{
            fontSize: '15px',
            color: 'var(--text-secondary)',
            lineHeight: 1.6,
          }}
        >
          导入第一份物业资料，开始建立企业知识库。
        </p>

        <button
          className="btn-primary"
          onClick={onImportClick}
          style={{
            padding: '0 20px',
            height: '38px',
            fontSize: '14px',
          }}
        >
          <Plus size={16} />
          <span>导入文件</span>
        </button>
      </div>
    </div>
  );
};
