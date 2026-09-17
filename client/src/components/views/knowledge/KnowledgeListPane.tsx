import React, { useState, useEffect } from 'react';
import {
  Search,
  CheckCircle2,
  Clock,
  AlertTriangle,
  FileText,
  Tag,
  BookOpen,
  Filter,
  X,
  ExternalLink,
  Layers,
} from 'lucide-react';
import {
  KnowledgeItem,
  KnowledgeStats,
  PrimaryCategory,
  DocumentItem,
} from '../../../types';
import { api } from '../../../services/api';
import { ProofreadingModal } from './ProofreadingModal';
import { formatAnchor } from '../../../utils/formatters';

interface KnowledgeListPaneProps {
  selectedDoc: DocumentItem | null;
  onClearDocSelection: () => void;
  onSwitchToDocPipeline: () => void;
}

const CATEGORY_STYLES: Record<string, { bg: string; text: string; border: string }> = {
  制度与标准: { bg: '#EBF4F0', text: '#285C49', border: '#C2DBD0' },
  方法与工具: { bg: '#EBF8FF', text: '#2B6CB0', border: '#BEE3F8' },
  项目案例: { bg: '#FAF5FF', text: '#6B46C1', border: '#E9D8FD' },
  指标数据: { bg: '#FFFAF0', text: '#C05621', border: '#FEEBC8' },
  专家经验: { bg: '#F0FFF4', text: '#22543D', border: '#C6F6D5' },
};

export const KnowledgeListPane: React.FC<KnowledgeListPaneProps> = ({
  selectedDoc,
  onClearDocSelection,
  onSwitchToDocPipeline,
}) => {
  const [items, setItems] = useState<KnowledgeItem[]>([]);
  const [stats, setStats] = useState<KnowledgeStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [activeTab, setActiveTab] = useState<string>('all'); // 'all' | 'unclassified' | 'pending' | 'confirmed' | PrimaryCategory
  const [activeItemId, setActiveItemId] = useState<string | null>(null);

  const fetchKnowledgeData = async () => {
    try {
      setLoading(true);
      const docId = selectedDoc ? selectedDoc.id : undefined;
      const categoryParam =
        activeTab === 'all' || activeTab === 'unclassified' || activeTab === 'pending' || activeTab === 'confirmed'
          ? activeTab === 'unclassified'
            ? 'unclassified'
            : undefined
          : activeTab;
      const reviewStatusParam =
        activeTab === 'pending'
          ? 'pending_review'
          : activeTab === 'confirmed'
          ? 'confirmed'
          : undefined;

      const res = await api.getKnowledgeItems({
        document_id: docId,
        category: categoryParam,
        review_status: reviewStatusParam,
        search: searchQuery.trim() || undefined,
      });

      setItems(res.items);
      setStats(res.stats);
    } catch (err) {
      console.error('Failed to load knowledge items:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchKnowledgeData();
  }, [selectedDoc, activeTab, searchQuery]);

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', height: '100%', minWidth: 0, backgroundColor: '#FFFFFF' }}>
      {/* 顶部控制栏 */}
      <div
        style={{
          padding: '16px 24px',
          borderBottom: '1px solid var(--border-color)',
          display: 'flex',
          flexDirection: 'column',
          gap: '12px',
          backgroundColor: '#FFFFFF',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          {/* 当前浏览范围说明 */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <span style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)' }}>
              {selectedDoc ? (
                <>
                  范围：所选文件 <strong style={{ color: 'var(--brand-accent)' }}>「{selectedDoc.title}」</strong>
                </>
              ) : (
                <>范围：全部资料 ({stats?.total || 0} 条知识)</>
              )}
            </span>

            {selectedDoc && (
              <button
                type="button"
                className="btn-secondary"
                onClick={onClearDocSelection}
                style={{ height: '24px', fontSize: '11px', padding: '0 8px', gap: '2px' }}
                title="清除单文件筛选，查看全部资料"
              >
                <X size={12} />
                <span>查看全部资料</span>
              </button>
            )}
          </div>

          {/* 切换至原始文件结构块透视 */}
          {selectedDoc && (
            <button
              type="button"
              className="btn-secondary"
              onClick={onSwitchToDocPipeline}
              style={{ height: '28px', fontSize: '12px', gap: '4px' }}
              title="查看原始文档正文拆解块与流水线状态"
            >
              <FileText size={13} />
              <span>查看文件原件与结构块</span>
            </button>
          )}
        </div>

        {/* 搜索与分类 Tab */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '16px' }}>
          {/* 五类知识 + 待分类 + 待确认 Tab */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap' }}>
            <button
              type="button"
              data-testid="tab-all"
              onClick={() => setActiveTab('all')}
              style={{
                height: '30px',
                padding: '0 12px',
                fontSize: '12px',
                fontWeight: activeTab === 'all' ? 600 : 400,
                borderRadius: 'var(--radius-sm)',
                border: activeTab === 'all' ? '1px solid var(--brand-accent)' : '1px solid var(--border-color)',
                backgroundColor: activeTab === 'all' ? 'var(--brand-accent-light)' : 'var(--bg-secondary)',
                color: activeTab === 'all' ? 'var(--brand-accent)' : 'var(--text-secondary)',
                cursor: 'pointer',
              }}
            >
              全部 ({stats?.total || 0})
            </button>

            {/* 待分类入口（重要待办） */}
            <button
              type="button"
              data-testid="tab-unclassified"
              onClick={() => setActiveTab('unclassified')}
              style={{
                height: '30px',
                padding: '0 10px',
                fontSize: '12px',
                fontWeight: activeTab === 'unclassified' ? 600 : 400,
                borderRadius: 'var(--radius-sm)',
                border: activeTab === 'unclassified' ? '1px solid #D97706' : '1px solid var(--border-color)',
                backgroundColor: activeTab === 'unclassified' ? '#FEF3C7' : 'var(--bg-secondary)',
                color: (stats?.unclassified_count || 0) > 0 ? '#B45309' : 'var(--text-secondary)',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: '4px',
              }}
            >
              <AlertTriangle size={12} />
              <span>待分类 ({stats?.unclassified_count || 0})</span>
            </button>

            {/* 待确认入口 */}
            <button
              type="button"
              data-testid="tab-pending"
              onClick={() => setActiveTab('pending')}
              style={{
                height: '30px',
                padding: '0 10px',
                fontSize: '12px',
                fontWeight: activeTab === 'pending' ? 600 : 400,
                borderRadius: 'var(--radius-sm)',
                border: activeTab === 'pending' ? '1px solid #3B82F6' : '1px solid var(--border-color)',
                backgroundColor: activeTab === 'pending' ? '#EFF6FF' : 'var(--bg-secondary)',
                color: activeTab === 'pending' ? '#1D4ED8' : 'var(--text-secondary)',
                cursor: 'pointer',
              }}
            >
              待校对 ({stats?.pending_review_count || 0})
            </button>

            <span style={{ width: '1px', height: '18px', backgroundColor: 'var(--border-color)', margin: '0 4px' }} />

            {/* 五类主分类固定入口 */}
            {(['制度与标准', '方法与工具', '项目案例', '指标数据', '专家经验'] as PrimaryCategory[]).map((cat) => {
              const count = stats?.category_counts[cat] || 0;
              const isSelected = activeTab === cat;
              return (
                <button
                  key={cat}
                  type="button"
                  data-testid={`tab-${cat}`}
                  onClick={() => setActiveTab(cat)}
                  style={{
                    height: '30px',
                    padding: '0 10px',
                    fontSize: '12px',
                    fontWeight: isSelected ? 600 : 400,
                    borderRadius: 'var(--radius-sm)',
                    border: isSelected ? `1px solid ${CATEGORY_STYLES[cat].border}` : '1px solid var(--border-color)',
                    backgroundColor: isSelected ? CATEGORY_STYLES[cat].bg : '#FFFFFF',
                    color: isSelected ? CATEGORY_STYLES[cat].text : 'var(--text-primary)',
                    cursor: 'pointer',
                  }}
                >
                  {cat} ({count})
                </button>
              );
            })}
          </div>

          {/* 搜索框 */}
          <div
            style={{
              width: '240px',
              height: '32px',
              border: '1px solid var(--border-color)',
              borderRadius: 'var(--radius-sm)',
              display: 'flex',
              alignItems: 'center',
              padding: '0 8px',
              backgroundColor: '#FFFFFF',
            }}
          >
            <Search size={14} color="var(--text-muted)" style={{ marginRight: '6px' }} />
            <input
              type="text"
              placeholder="搜索知识标题与陈述..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              style={{
                border: 'none',
                outline: 'none',
                fontSize: '12px',
                width: '100%',
                backgroundColor: 'transparent',
              }}
            />
            {searchQuery && (
              <X size={13} color="var(--text-muted)" cursor="pointer" onClick={() => setSearchQuery('')} />
            )}
          </div>
        </div>
      </div>

      {/* 知识条目卡片流 */}
      <div
        style={{
          flex: 1,
          overflowY: 'auto',
          padding: '20px 24px',
          display: 'flex',
          flexDirection: 'column',
          gap: '14px',
          backgroundColor: 'var(--bg-secondary)',
        }}
      >
        {loading ? (
          <div
            style={{
              padding: '40px',
              textAlign: 'center',
              color: 'var(--text-secondary)',
              fontSize: '13px',
            }}
          >
            正在检索当前范围内的知识原子资产...
          </div>
        ) : items.length === 0 ? (
          <div
            style={{
              backgroundColor: '#FFFFFF',
              borderRadius: 'var(--radius-md)',
              border: '1px solid var(--border-color)',
              padding: '60px 20px',
              textAlign: 'center',
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              gap: '12px',
            }}
          >
            <div
              style={{
                width: '44px',
                height: '44px',
                borderRadius: '50%',
                backgroundColor: 'var(--bg-secondary)',
                color: 'var(--text-muted)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <BookOpen size={22} />
            </div>
            <div style={{ fontSize: '14px', fontWeight: 500, color: 'var(--text-primary)' }}>
              当前筛选范围暂无知识条目
            </div>
            <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
              您可以清除筛选条件，或在左侧重新触发资料的后台提炼任务。
            </div>
          </div>
        ) : (
          items.map((item) => {
            const catStyle = item.primary_category
              ? CATEGORY_STYLES[item.primary_category] || { bg: '#EDF2F7', text: '#4A5568', border: '#CBD5E0' }
              : { bg: '#FEF3C7', text: '#B45309', border: '#FCD34D' };

            const isConfirmed = item.review_status === 'confirmed';

            return (
              <div
                key={item.id}
                data-testid={`knowledge-item-${item.id}`}
                onClick={() => setActiveItemId(item.id)}
                style={{
                  backgroundColor: '#FFFFFF',
                  borderRadius: 'var(--radius-md)',
                  border: '1px solid var(--border-color)',
                  padding: '16px 20px',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '10px',
                  cursor: 'pointer',
                  transition: 'border-color 0.15s, box-shadow 0.15s',
                  boxShadow: '0 1px 2px rgba(0, 0, 0, 0.03)',
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.borderColor = 'var(--brand-accent)';
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.borderColor = 'var(--border-color)';
                }}
              >
                {/* 顶栏：分类、形态、审核状态 */}
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    {/* 主分类标签 */}
                    <span
                      style={{
                        fontSize: '11px',
                        fontWeight: 600,
                        padding: '2px 8px',
                        borderRadius: 'var(--radius-sm)',
                        backgroundColor: catStyle.bg,
                        color: catStyle.text,
                        border: `1px solid ${catStyle.border}`,
                      }}
                    >
                      {item.primary_category || '待分类'}
                    </span>

                    {/* 形态标签 */}
                    <span
                      style={{
                        fontSize: '11px',
                        color: 'var(--text-secondary)',
                        backgroundColor: 'var(--bg-secondary)',
                        padding: '2px 6px',
                        borderRadius: 'var(--radius-sm)',
                        border: '1px solid var(--border-color)',
                      }}
                    >
                      形态：{item.atom_type}
                    </span>

                    {/* 主体 */}
                    {item.subject && (
                      <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                        主体：{item.subject}
                      </span>
                    )}
                  </div>

                  {/* 审核状态角标 */}
                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                    {isConfirmed ? (
                      <span
                        style={{
                          fontSize: '11px',
                          fontWeight: 600,
                          padding: '2px 8px',
                          borderRadius: 'var(--radius-sm)',
                          backgroundColor: '#ECFDF5',
                          color: '#059669',
                          border: '1px solid #A7F3D0',
                          display: 'flex',
                          alignItems: 'center',
                          gap: '3px',
                        }}
                      >
                        <CheckCircle2 size={12} />
                        <span>已确认，索引未建立</span>
                      </span>
                    ) : (
                      <span
                        style={{
                          fontSize: '11px',
                          fontWeight: 600,
                          padding: '2px 8px',
                          borderRadius: 'var(--radius-sm)',
                          backgroundColor: 'var(--warning-bg)',
                          color: 'var(--warning-text)',
                          border: '1px solid #FCD34D',
                          display: 'flex',
                          alignItems: 'center',
                          gap: '3px',
                        }}
                      >
                        <Clock size={12} />
                        <span>待校对</span>
                      </span>
                    )}
                  </div>
                </div>

                {/* 标题 */}
                <div style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)', lineHeight: 1.4 }}>
                  {item.title}
                </div>

                {/* 核心陈述正文 */}
                <div
                  style={{
                    fontSize: '13px',
                    color: 'var(--text-secondary)',
                    lineHeight: 1.6,
                    backgroundColor: 'var(--bg-secondary)',
                    padding: '8px 12px',
                    borderRadius: 'var(--radius-sm)',
                  }}
                >
                  {item.statement}
                </div>

                {/* 质检告警 */}
                {item.quality_flags && item.quality_flags.length > 0 && (
                  <div
                    style={{
                      fontSize: '11px',
                      color: 'var(--warning-text)',
                      backgroundColor: 'var(--warning-bg)',
                      padding: '4px 8px',
                      borderRadius: 'var(--radius-sm)',
                      display: 'flex',
                      alignItems: 'center',
                      gap: '4px',
                    }}
                  >
                    <AlertTriangle size={12} />
                    <span>质检关注：{item.quality_flags.join('；')}</span>
                  </div>
                )}

                {/* 底栏：来源文件、锚点支撑、业务标签 */}
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    fontSize: '11px',
                    color: 'var(--text-muted)',
                    marginTop: '2px',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                    <span style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                      <FileText size={12} />
                      <span>{item.document_title}</span>
                    </span>

                    {item.source_anchors && item.source_anchors.length > 0 && (
                      <span style={{ color: 'var(--brand-accent)' }}>
                        锚点：{item.source_anchors.map(formatAnchor).join('、')}
                      </span>
                    )}

                    <span>证据：{item.evidence_count} 处</span>
                  </div>

                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                    {item.business_scenes.slice(0, 2).map((s) => (
                      <span
                        key={s}
                        style={{
                          backgroundColor: 'var(--bg-secondary)',
                          padding: '1px 6px',
                          borderRadius: '10px',
                          border: '1px solid var(--border-color)',
                        }}
                      >
                        {s}
                      </span>
                    ))}
                    <span style={{ color: 'var(--brand-accent)', fontWeight: 500 }}>
                      对照校对 &gt;
                    </span>
                  </div>
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* 校对抽屉弹窗 */}
      <ProofreadingModal
        itemId={activeItemId}
        onClose={() => setActiveItemId(null)}
        onSaved={fetchKnowledgeData}
        onDeleted={fetchKnowledgeData}
      />
    </div>
  );
};
