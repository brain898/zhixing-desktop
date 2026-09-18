import React, { useState, useEffect } from 'react';
import {
  Search,
  CheckCircle2,
  Clock,
  AlertTriangle,
  FileText,
  BookOpen,
  X,
  Sparkles,
  ArrowRight,
  RotateCw,
  AlertCircle,
  Eye,
} from 'lucide-react';
import {
  KnowledgeItem,
  KnowledgeStats,
  PrimaryCategory,
  DocumentItem,
} from '../../../types';
import { api } from '../../../services/api';
import { ProofreadingModal } from './ProofreadingModal';

interface KnowledgeListPaneProps {
  selectedDoc: DocumentItem | null;
  onClearDocSelection: () => void;
  onOpenDocPreview: (tab?: 'preview' | 'pipeline') => void;
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
  onOpenDocPreview,
}) => {
  const [items, setItems] = useState<KnowledgeItem[]>([]);
  const [stats, setStats] = useState<KnowledgeStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');

  // 状态筛选：'all' | 'pending' | 'confirmed'
  const [statusFilter, setStatusFilter] = useState<'all' | 'pending' | 'confirmed'>('all');
  // 分类筛选：'all' | 'unclassified' | PrimaryCategory
  const [categoryFilter, setCategoryFilter] = useState<string>('all');

  // 当前核对弹窗对应的条目 ID
  const [activeItemId, setActiveItemId] = useState<string | null>(null);

  const fetchKnowledgeData = async () => {
    try {
      setLoading(true);
      const docId = selectedDoc ? selectedDoc.id : undefined;

      const categoryParam =
        categoryFilter === 'all'
          ? undefined
          : categoryFilter === 'unclassified'
          ? 'unclassified'
          : categoryFilter;

      const reviewStatusParam =
        statusFilter === 'pending'
          ? 'pending_review'
          : statusFilter === 'confirmed'
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
  }, [selectedDoc, statusFilter, categoryFilter, searchQuery]);

  // 处理流水线「一句结果 + 对应动作」计算
  const renderPipelineBanner = () => {
    if (selectedDoc) {
      const status = selectedDoc.processing_status;

      if (status === 'parsing' || status === 'extracting' || status === 'queued') {
        return (
          <div
            style={{
              padding: '10px 16px',
              backgroundColor: 'var(--brand-accent-light)',
              border: '1px solid #C2DBD0',
              borderRadius: 'var(--radius-sm)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              fontSize: '13px',
              color: 'var(--brand-accent)',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <RotateCw size={14} className="spin-slow" />
              <span>正在整理资料，可以离开此页面</span>
            </div>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => onOpenDocPreview('pipeline')}
              style={{ height: '26px', fontSize: '11px', padding: '0 10px', gap: '4px' }}
            >
              <span>查看进度</span>
              <ArrowRight size={12} />
            </button>
          </div>
        );
      }

      if (status === 'failed') {
        return (
          <div
            style={{
              padding: '10px 16px',
              backgroundColor: '#FEF2F2',
              border: '1px solid #FECACA',
              borderRadius: 'var(--radius-sm)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              fontSize: '13px',
              color: '#B91C1C',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <AlertCircle size={15} />
              <span>文件已保存，但未能生成知识</span>
            </div>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => onOpenDocPreview('pipeline')}
              style={{ height: '26px', fontSize: '11px', padding: '0 10px', gap: '4px' }}
            >
              <span>查看原因与重试</span>
              <ArrowRight size={12} />
            </button>
          </div>
        );
      }

      if (status === 'partial_failed') {
        return (
          <div
            style={{
              padding: '10px 16px',
              backgroundColor: '#FFFBEB',
              border: '1px solid #FDE68A',
              borderRadius: 'var(--radius-sm)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              fontSize: '13px',
              color: '#B45309',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <AlertTriangle size={15} />
              <span>部分内容未能处理</span>
            </div>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => onOpenDocPreview('pipeline')}
              style={{ height: '26px', fontSize: '11px', padding: '0 10px', gap: '4px' }}
            >
              <span>查看未处理内容</span>
              <ArrowRight size={12} />
            </button>
          </div>
        );
      }

      if (status === 'completed') {
        const pendingCount = stats?.pending_review_count || 0;
        const confirmedCount = stats?.confirmed_count || 0;

        if (pendingCount > 0) {
          return (
            <div
              style={{
                padding: '10px 16px',
                backgroundColor: '#EFF6FF',
                border: '1px solid #BFDBFE',
                borderRadius: 'var(--radius-sm)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                fontSize: '13px',
                color: '#1D4ED8',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <Clock size={15} />
                <span>整理完成，有 {pendingCount} 条知识待核对</span>
              </div>
              <button
                type="button"
                className="btn-primary"
                onClick={() => {
                  const firstPending = items.find((i) => i.review_status === 'pending_review') || items[0];
                  if (firstPending) setActiveItemId(firstPending.id);
                }}
                style={{ height: '26px', fontSize: '11px', padding: '0 12px', gap: '4px' }}
              >
                <span>开始核对</span>
                <ArrowRight size={12} />
              </button>
            </div>
          );
        }

        if (confirmedCount > 0) {
          return (
            <div
              style={{
                padding: '10px 16px',
                backgroundColor: '#ECFDF5',
                border: '1px solid #A7F3D0',
                borderRadius: 'var(--radius-sm)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                fontSize: '13px',
                color: '#047857',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <CheckCircle2 size={15} />
                <span>已有 {confirmedCount} 条可用知识</span>
              </div>
              <button
                type="button"
                className="btn-secondary"
                onClick={() => {
                  setStatusFilter('confirmed');
                  setCategoryFilter('all');
                }}
                style={{ height: '26px', fontSize: '11px', padding: '0 10px', gap: '4px' }}
              >
                <span>查看可用知识</span>
                <ArrowRight size={12} />
              </button>
            </div>
          );
        }
      }
    } else if (stats && stats.pending_review_count > 0) {
      // 全部资料视图下存在待核对条目
      return (
        <div
          style={{
            padding: '10px 16px',
            backgroundColor: '#EFF6FF',
            border: '1px solid #BFDBFE',
            borderRadius: 'var(--radius-sm)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            fontSize: '13px',
            color: '#1D4ED8',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <Clock size={15} />
            <span>全库整理完成，共有 {stats.pending_review_count} 条知识待核对</span>
          </div>
          <button
            type="button"
            className="btn-primary"
            onClick={() => {
              const firstPending = items.find((i) => i.review_status === 'pending_review') || items[0];
              if (firstPending) setActiveItemId(firstPending.id);
            }}
            style={{ height: '26px', fontSize: '11px', padding: '0 12px', gap: '4px' }}
          >
            <span>开始核对</span>
            <ArrowRight size={12} />
          </button>
        </div>
      );
    }

    return null;
  };

  const handleClearFilters = () => {
    setStatusFilter('all');
    setCategoryFilter('all');
    setSearchQuery('');
  };

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
        {/* 第一行：当前资料名称或查询范围 + 查看原文入口 */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <span style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)' }}>
              {selectedDoc ? (
                <>
                  资料：<strong style={{ color: 'var(--brand-accent)' }}>{selectedDoc.title}</strong>
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
                title="清除单文件筛选，汇总查看全部资料"
              >
                <X size={12} />
                <span>查看全部资料</span>
              </button>
            )}
          </div>

          {/* 独立原文与处理详情入口 */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            {selectedDoc && (
              <button
                type="button"
                className="btn-secondary"
                onClick={() => onOpenDocPreview('preview')}
                style={{ height: '28px', fontSize: '12px', gap: '4px' }}
                title="查看该文件的原文预览与段落锚点"
              >
                <Eye size={13} />
                <span>查看原文</span>
              </button>
            )}
            {selectedDoc && (
              <button
                type="button"
                className="btn-secondary"
                onClick={() => onOpenDocPreview('pipeline')}
                style={{ height: '28px', fontSize: '12px', gap: '4px' }}
                title="查看结构块拆解、任务耗时与技术信息"
              >
                <FileText size={13} />
                <span>处理详情</span>
              </button>
            )}
          </div>
        </div>

        {/* 第二行：流水线一句结果与动作条 */}
        {renderPipelineBanner()}

        {/* 第三行：状态筛选 + 五类主分类切换 + 搜索框 */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '16px', flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
            {/* 状态筛选：全部 / 待核对 / 可用 */}
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                backgroundColor: 'var(--bg-secondary)',
                padding: '2px',
                borderRadius: 'var(--radius-sm)',
                border: '1px solid var(--border-color)',
              }}
            >
              <button
                type="button"
                data-testid="status-all"
                onClick={() => setStatusFilter('all')}
                style={{
                  padding: '4px 10px',
                  fontSize: '12px',
                  fontWeight: statusFilter === 'all' ? 600 : 400,
                  border: 'none',
                  borderRadius: 'var(--radius-xs)',
                  backgroundColor: statusFilter === 'all' ? '#FFFFFF' : 'transparent',
                  color: statusFilter === 'all' ? 'var(--text-primary)' : 'var(--text-secondary)',
                  boxShadow: statusFilter === 'all' ? '0 1px 2px rgba(0,0,0,0.05)' : 'none',
                  cursor: 'pointer',
                }}
              >
                全部 ({stats?.total || 0})
              </button>
              <button
                type="button"
                data-testid="status-pending"
                onClick={() => setStatusFilter('pending')}
                style={{
                  padding: '4px 10px',
                  fontSize: '12px',
                  fontWeight: statusFilter === 'pending' ? 600 : 400,
                  border: 'none',
                  borderRadius: 'var(--radius-xs)',
                  backgroundColor: statusFilter === 'pending' ? '#EFF6FF' : 'transparent',
                  color: statusFilter === 'pending' ? '#1D4ED8' : 'var(--text-secondary)',
                  boxShadow: statusFilter === 'pending' ? '0 1px 2px rgba(0,0,0,0.05)' : 'none',
                  cursor: 'pointer',
                }}
              >
                待核对 ({stats?.pending_review_count || 0})
              </button>
              <button
                type="button"
                data-testid="status-confirmed"
                onClick={() => setStatusFilter('confirmed')}
                style={{
                  padding: '4px 10px',
                  fontSize: '12px',
                  fontWeight: statusFilter === 'confirmed' ? 600 : 400,
                  border: 'none',
                  borderRadius: 'var(--radius-xs)',
                  backgroundColor: statusFilter === 'confirmed' ? '#ECFDF5' : 'transparent',
                  color: statusFilter === 'confirmed' ? '#047857' : 'var(--text-secondary)',
                  boxShadow: statusFilter === 'confirmed' ? '0 1px 2px rgba(0,0,0,0.05)' : 'none',
                  cursor: 'pointer',
                }}
              >
                已确认 ({stats?.confirmed_count || 0})
              </button>
            </div>

            <span style={{ width: '1px', height: '16px', backgroundColor: 'var(--border-color)', margin: '0 2px' }} />

            {/* 五类主分类切换 */}
            {(['制度与标准', '方法与工具', '项目案例', '指标数据', '专家经验'] as PrimaryCategory[]).map((cat) => {
              const count = stats?.category_counts[cat] || 0;
              const isSelected = categoryFilter === cat;
              return (
                <button
                  key={cat}
                  type="button"
                  data-testid={`tab-${cat}`}
                  onClick={() => setCategoryFilter(isSelected ? 'all' : cat)}
                  style={{
                    height: '28px',
                    padding: '0 10px',
                    fontSize: '12px',
                    fontWeight: isSelected ? 600 : 400,
                    borderRadius: 'var(--radius-sm)',
                    border: isSelected ? `1px solid ${CATEGORY_STYLES[cat].border}` : '1px solid var(--border-color)',
                    backgroundColor: isSelected ? CATEGORY_STYLES[cat].bg : '#FFFFFF',
                    color: isSelected ? CATEGORY_STYLES[cat].text : 'var(--text-secondary)',
                    cursor: 'pointer',
                    transition: 'all 120ms',
                  }}
                >
                  {cat} ({count})
                </button>
              );
            })}

            {/* 「待分类」独立待处理入口：明确其不是第六类，而是必须核对归类的待办入口 */}
            <button
              type="button"
              data-testid="tab-unclassified"
              onClick={() => setCategoryFilter(categoryFilter === 'unclassified' ? 'all' : 'unclassified')}
              title="待分类属于待处理入口，必须由人工核对并指定五类主分类方可启用"
              style={{
                height: '28px',
                padding: '0 10px',
                fontSize: '12px',
                fontWeight: categoryFilter === 'unclassified' ? 600 : 400,
                borderRadius: 'var(--radius-sm)',
                border: categoryFilter === 'unclassified' ? '1px solid #D97706' : '1px solid var(--border-color)',
                backgroundColor: categoryFilter === 'unclassified' ? '#FEF3C7' : '#FFFFFF',
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
          </div>

          {/* 知识搜索框：明确区分于文件搜索 */}
          <div
            style={{
              width: '260px',
              height: '32px',
              border: '1px solid var(--border-color)',
              borderRadius: 'var(--radius-sm)',
              display: 'flex',
              alignItems: 'center',
              padding: '0 10px',
              backgroundColor: '#FFFFFF',
              flexShrink: 0,
            }}
          >
            <Search size={14} color="var(--text-muted)" style={{ marginRight: '6px' }} />
            <input
              type="text"
              placeholder="检索知识标题与陈述..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              style={{
                border: 'none',
                outline: 'none',
                fontSize: '12px',
                width: '100%',
                backgroundColor: 'transparent',
                color: 'var(--text-primary)',
              }}
            />
            {searchQuery && (
              <X size={13} color="var(--text-muted)" cursor="pointer" onClick={() => setSearchQuery('')} />
            )}
          </div>
        </div>
      </div>

      {/* 知识条目列表展示区 */}
      <div
        style={{
          flex: 1,
          overflowY: 'auto',
          padding: '20px 24px',
          display: 'flex',
          flexDirection: 'column',
          gap: '12px',
          backgroundColor: 'var(--bg-secondary)',
        }}
      >
        {/* 五种空状态精细呈现 */}
        {loading ? (
          <div
            style={{
              padding: '60px 20px',
              textAlign: 'center',
              color: 'var(--text-secondary)',
              fontSize: '13px',
            }}
          >
            正在检索知识整理结果...
          </div>
        ) : selectedDoc && ['parsing', 'extracting', 'queued'].includes(selectedDoc.processing_status) ? (
          /* 整理中空状态 */
          <div
            style={{
              backgroundColor: '#FFFFFF',
              borderRadius: 'var(--radius-md)',
              border: '1px solid var(--border-color)',
              padding: '50px 20px',
              textAlign: 'center',
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              gap: '12px',
            }}
          >
            <RotateCw size={28} className="spin-slow" color="var(--brand-accent)" />
            <div style={{ fontSize: '15px', fontWeight: 600, color: 'var(--text-primary)' }}>
              资料正在整理中
            </div>
            <div style={{ fontSize: '12px', color: 'var(--text-secondary)', maxWidth: '420px', lineHeight: 1.5 }}>
              系统正在调用大模型提炼知识原子并校验来源证据，您可以离开此页面处理其他任务，整理完成后将自动呈现。
            </div>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => onOpenDocPreview('pipeline')}
              style={{ height: '30px', fontSize: '12px', marginTop: '6px' }}
            >
              查看后台处理详情
            </button>
          </div>
        ) : selectedDoc && selectedDoc.processing_status === 'failed' ? (
          /* 处理失败空状态 */
          <div
            style={{
              backgroundColor: '#FFFFFF',
              borderRadius: 'var(--radius-md)',
              border: '1px solid #FECACA',
              padding: '50px 20px',
              textAlign: 'center',
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              gap: '12px',
            }}
          >
            <AlertCircle size={32} color="#B91C1C" />
            <div style={{ fontSize: '15px', fontWeight: 600, color: '#B91C1C' }}>
              文件已保存，但未能生成知识
            </div>
            <div style={{ fontSize: '12px', color: 'var(--text-secondary)', maxWidth: '400px', lineHeight: 1.5 }}>
              正文解析或知识提炼阶段遇到阻碍。请查看失败原因并针对失败步骤发起重试。
            </div>
            <button
              type="button"
              className="btn-primary"
              onClick={() => onOpenDocPreview('pipeline')}
              style={{ height: '32px', fontSize: '12px', marginTop: '6px' }}
            >
              查看失败原因并重试
            </button>
          </div>
        ) : items.length === 0 && (statusFilter !== 'all' || categoryFilter !== 'all' || searchQuery.trim()) ? (
          /* 筛选无结果空状态 */
          <div
            style={{
              backgroundColor: '#FFFFFF',
              borderRadius: 'var(--radius-md)',
              border: '1px solid var(--border-color)',
              padding: '50px 20px',
              textAlign: 'center',
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              gap: '10px',
            }}
          >
            <Search size={26} color="var(--text-muted)" />
            <div style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)' }}>
              未找到符合条件的知识条目
            </div>
            <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
              可尝试更换搜索词或清除当前分类与状态筛选条件。
            </div>
            <button
              type="button"
              className="btn-secondary"
              onClick={handleClearFilters}
              style={{ height: '28px', fontSize: '12px', marginTop: '6px' }}
            >
              清除全部筛选
            </button>
          </div>
        ) : items.length === 0 ? (
          /* 未发现可提取内容空状态 */
          <div
            style={{
              backgroundColor: '#FFFFFF',
              borderRadius: 'var(--radius-md)',
              border: '1px solid var(--border-color)',
              padding: '50px 20px',
              textAlign: 'center',
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              gap: '10px',
            }}
          >
            <BookOpen size={28} color="var(--text-muted)" />
            <div style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)' }}>
              未发现可提取的知识条目
            </div>
            <div style={{ fontSize: '12px', color: 'var(--text-muted)', maxWidth: '400px', lineHeight: 1.5 }}>
              该资料可能为纯目录、空表格或扫描件，未包含符合标准的规则或方法。您可以查看原文或更换资料。
            </div>
            {selectedDoc && (
              <button
                type="button"
                className="btn-secondary"
                onClick={() => onOpenDocPreview('preview')}
                style={{ height: '28px', fontSize: '12px', marginTop: '6px' }}
              >
                查看文件原文
              </button>
            )}
          </div>
        ) : (
          /* 正常知识条目列表渲染：卡片优先展示标题、简短摘要、分类和主要业务状态 */
          items.map((item) => {
            const catStyle = item.primary_category
              ? CATEGORY_STYLES[item.primary_category] || { bg: '#EDF2F7', text: '#4A5568', border: '#CBD5E0' }
              : { bg: '#FEF3C7', text: '#B45309', border: '#FCD34D' };

            const isConfirmed = item.review_status === 'confirmed';

            // 区分正常待确认与阻止启用的严重问题
            const blockingIssues = (item.quality_flags || []).filter((f) =>
              f.includes('伪造来源') || f.includes('不匹配') || f.includes('冲突') || f.includes('无效提取') || f.includes('缺乏有效')
            );
            const advisoryIssues = (item.quality_flags || []).filter(
              (f) => !blockingIssues.includes(f) && f !== '待管理员确认主分类'
            );

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
                {/* 顶栏：分类标签、业务主体、审核与可用性状态 */}
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    {/* 主分类 */}
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

                    {/* 业务执行主体 */}
                    {item.subject && (
                      <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                        适用：{item.subject}
                      </span>
                    )}
                  </div>

                  {/* 主要业务状态表达（遵守 PRD 状态边界与可用性要求） */}
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
                        <span>已确认（索引准备中）</span>
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
                        <span>待核对</span>
                      </span>
                    )}
                  </div>
                </div>

                {/* 知识标题 */}
                <div style={{ fontSize: '15px', fontWeight: 600, color: 'var(--text-primary)', lineHeight: 1.35 }}>
                  {item.title}
                </div>

                {/* 简短核心陈述摘要 */}
                <div
                  style={{
                    fontSize: '13px',
                    color: 'var(--text-secondary)',
                    lineHeight: 1.55,
                    backgroundColor: 'var(--bg-secondary)',
                    padding: '8px 12px',
                    borderRadius: 'var(--radius-sm)',
                  }}
                >
                  {item.statement}
                </div>

                {/* 阻止启用的阻塞性问题明确提示 */}
                {blockingIssues.length > 0 && (
                  <div
                    style={{
                      fontSize: '12px',
                      color: '#B91C1C',
                      backgroundColor: '#FEF2F2',
                      border: '1px solid #FECACA',
                      padding: '5px 10px',
                      borderRadius: 'var(--radius-sm)',
                      display: 'flex',
                      alignItems: 'center',
                      gap: '6px',
                    }}
                  >
                    <AlertCircle size={13} flex-shrink="0" />
                    <span>影响启用问题：{blockingIssues.join('；')}（需核对修正）</span>
                  </div>
                )}

                {/* 底栏：仅在未选文件（全部资料）时显示来源文件名，选中文件后不重复显示文件名 */}
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
                  <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                    {/* 仅在全部资料视图下才显示来源资料名称 */}
                    {!selectedDoc && (
                      <span style={{ display: 'flex', alignItems: 'center', gap: '4px', color: 'var(--text-secondary)' }}>
                        <FileText size={12} />
                        <span>来源：{item.document_title}</span>
                      </span>
                    )}

                    {/* 业务场景标签 */}
                    {item.business_scenes && item.business_scenes.slice(0, 2).map((s) => (
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
                  </div>

                  <span style={{ color: 'var(--brand-accent)', fontWeight: 500, display: 'flex', alignItems: 'center', gap: '2px' }}>
                    <span>核对知识</span>
                    <ArrowRight size={12} />
                  </span>
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* 核对知识弹窗（支持上一条/下一条与连续核对） */}
      <ProofreadingModal
        itemId={activeItemId}
        itemList={items.map((i) => i.id)}
        onSelectNext={(nextId) => setActiveItemId(nextId)}
        onClose={() => setActiveItemId(null)}
        onSaved={fetchKnowledgeData}
        onDeleted={fetchKnowledgeData}
      />
    </div>
  );
};
