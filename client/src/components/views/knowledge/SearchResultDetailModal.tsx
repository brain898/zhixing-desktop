import React from 'react';
import { Eye, FileText, X } from 'lucide-react';
import { SearchKnowledgeResultItem, SourceLocator } from '../../../types';
import { formatAnchor, formatFieldName, formatVersionLabel } from '../../../utils/formatters';

interface SearchResultDetailModalProps {
  result: SearchKnowledgeResultItem | null;
  onClose: () => void;
  onOpenSource: (documentId: string, versionId: string, locator: SourceLocator | null) => void;
}

const describeLocator = (locator: SourceLocator | null): string => {
  if (!locator) return '查看来源';
  if (locator.page_number !== null) {
    return `第 ${locator.page_number} 页${locator.heading_path ? ` · ${locator.heading_path}` : ''}`;
  }
  if (locator.heading_path && locator.paragraph_anchor) {
    return `${locator.heading_path} · ${formatAnchor(locator.paragraph_anchor)}`;
  }
  if (locator.paragraph_anchor) return formatAnchor(locator.paragraph_anchor);
  if (locator.heading_path) return locator.heading_path;
  return locator.block_index !== null ? `第 ${locator.block_index} 个结构块` : '查看来源';
};

const AtomList: React.FC<{ title: string; items: string[] }> = ({ title, items }) => (
  <section>
    <h3 style={{ fontSize: '13px', fontWeight: 600, marginBottom: '8px', color: 'var(--text-primary)' }}>{title}</h3>    {items.length > 0 ? (
      <ul style={{ margin: 0, paddingLeft: '20px', display: 'flex', flexDirection: 'column', gap: '6px' }}>
        {items.map((item, index) => (
          <li key={`${title}-${index}`} style={{ fontSize: '13px', lineHeight: 1.65, color: 'var(--text-secondary)' }}>
            {item}
          </li>
        ))}
      </ul>
    ) : (
      <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)' }}>无</div>
    )}
  </section>
);

export const SearchResultDetailModal: React.FC<SearchResultDetailModalProps> = ({
  result,
  onClose,
  onOpenSource,
}) => {
  if (!result) return null;

  const primaryLocator = result.evidence?.[0]?.source_locator || null;

  return (
    <div
      data-testid="formal-result-detail"
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 1100,
        backgroundColor: 'rgba(18, 26, 22, 0.42)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
      onClick={onClose}
    >      <div
        style={{
          width: '860px',
          maxWidth: '94vw',
          maxHeight: '86vh',
          backgroundColor: '#FFFFFF',
          borderRadius: 'var(--radius-lg)',
          boxShadow: 'var(--shadow-lg)',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
        }}
        onClick={(event) => event.stopPropagation()}
      >
        <header
          style={{
            padding: '18px 22px',
            borderBottom: '1px solid var(--border-color)',
            display: 'flex',
            alignItems: 'flex-start',
            justifyContent: 'space-between',
            gap: '16px',
            backgroundColor: 'var(--success-bg)',
          }}
        >
          <div style={{ minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '7px' }}>
              <span style={{ fontSize: 'var(--font-size-xs)', color: 'var(--brand-accent)', fontWeight: 600 }}>
                {result.primary_category}
              </span>
              <span style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)' }}>
                知识版本 v{result.version_number}
              </span>
            </div>            <h2 style={{ margin: 0, fontSize: '18px', lineHeight: 1.4, color: 'var(--text-primary)' }}>{result.title}</h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭正式检索结果详情"
            style={{ border: 'none', background: 'transparent', color: 'var(--text-secondary)', cursor: 'pointer', padding: '4px' }}
          >
            <X size={20} />
          </button>
        </header>

        <div style={{ overflowY: 'auto', padding: '20px 22px', display: 'flex', flexDirection: 'column', gap: '20px' }}>
          <section>
            <h3 style={{ fontSize: '13px', fontWeight: 600, marginBottom: '8px', color: 'var(--text-primary)' }}>核心陈述</h3>
            <div
              style={{
                fontSize: '14px',
                lineHeight: 1.7,
                color: 'var(--text-primary)',
                backgroundColor: 'var(--bg-secondary)',
                borderRadius: 'var(--radius-sm)',
                padding: '12px 14px',
              }}
            >
              {result.statement || result.content}
            </div>
          </section>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: '18px' }}>
            <AtomList title="条件" items={result.conditions || []} />
            <AtomList title="动作" items={result.actions || []} />
            <AtomList title="例外" items={result.exceptions || []} />
          </div>          <section>
            <h3 style={{ fontSize: '13px', fontWeight: 600, marginBottom: '8px', color: 'var(--text-primary)' }}>来源与版本</h3>
            <div style={{ fontSize: '13px', color: 'var(--text-secondary)', lineHeight: 1.8 }}>
              <div><FileText size={13} style={{ verticalAlign: '-2px', marginRight: '5px' }} />{result.source.file_name || result.document_title}</div>
              <div>来源文件版本：{formatVersionLabel(result.source.document_version_label)}</div>
              {result.business_scenes?.length > 0 && <div>业务场景：{result.business_scenes.join('、')}</div>}
              {result.customer_types?.length > 0 && <div>客户类型：{result.customer_types.join('、')}</div>}
              {result.problem_tags?.length > 0 && <div>问题：{result.problem_tags.join('、')}</div>}
            </div>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => onOpenSource(result.source.document_id, result.source.document_version_id, primaryLocator)}
              style={{ marginTop: '10px', height: '30px', fontSize: 'var(--font-size-xs)', gap: '5px' }}
            >
              <Eye size={13} />
              <span>{describeLocator(primaryLocator)}</span>
            </button>
          </section>

          <section>
            <h3 style={{ fontSize: '13px', fontWeight: 600, marginBottom: '8px', color: 'var(--text-primary)' }}>
              证据（{result.evidence?.length || 0}）
            </h3>            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              {(result.evidence || []).map((evidence) => (
                <div
                  key={evidence.id}
                  style={{
                    border: '1px solid var(--border-color)',
                    borderRadius: 'var(--radius-sm)',
                    padding: '10px 12px',
                    display: 'flex',
                    alignItems: 'flex-start',
                    justifyContent: 'space-between',
                    gap: '12px',
                  }}
                >
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)', marginBottom: '4px' }}>
                      {formatFieldName(evidence.field_name)}
                    </div>
                    <div style={{ fontSize: '13px', lineHeight: 1.6, color: 'var(--text-secondary)' }}>{evidence.excerpt}</div>
                  </div>
                  <button
                    type="button"
                    onClick={() =>
                      onOpenSource(result.source.document_id, result.source.document_version_id, evidence.source_locator)
                    }
                    style={{
                      flexShrink: 0,
                      border: 'none',
                      background: 'transparent',
                      color: 'var(--brand-accent)',
                      cursor: 'pointer',
                      fontSize: 'var(--font-size-xs)',
                    }}
                  >                    {describeLocator(evidence.source_locator)}
                  </button>
                </div>
              ))}
              {(result.evidence || []).length === 0 && (
                <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)' }}>当前知识版本未返回可展示的证据条目。</div>
              )}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
};
