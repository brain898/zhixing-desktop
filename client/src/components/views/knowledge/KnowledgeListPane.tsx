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
  Filter,
  Ban,
} from 'lucide-react';
import {
  KnowledgeItem,
  KnowledgeStats,
  PrimaryCategory,
  DocumentItem,
  SearchKnowledgeResultItem,
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
  const [activeSearchTerm, setActiveSearchTerm] = useState('');
  const [searchResults, setSearchResults] = useState<SearchKnowledgeResultItem[]>([]);
  const [isSearching, setIsSearching] = useState(false);

  // 一级状态筛选：'all' | 'pending' | 'confirmed'
  const [statusFilter, setStatusFilter] = useState<'all' | 'pending' | 'confirmed'>('all');
  // 二级筛选折叠状态
  const [showFilterDrawer, setShowFilterDrawer] = useState(false);
  // 分类筛选：'all' | 'unclassified' | PrimaryCategory
  const [categoryFilter, setCategoryFilter] = useState<string>('all');
  // 管理状态筛选：'all' | 'active' | 'disabled'
  const [lifecycleFilter, setLifecycleFilter] = useState<'all' | 'active' | 'disabled'>('all');

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

      const lifecycleParam =
        lifecycleFilter === 'all' ? undefined : lifecycleFilter;

      const res = await api.getKnowledgeItems({
        document_id: docId,
        category: categoryParam,
        review_status: reviewStatusParam,
        lifecycle_status: lifecycleParam,
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
    if (!activeSearchTerm) {
      fetchKnowledgeData();
    }
  }, [selectedDoc, statusFilter, categoryFilter, lifecycleFilter]);

  // 执行正式检索
  const handleExecuteSearch = async () => {
    const q = searchQuery.trim();
    if (!q) {
      handleClearSearch();
      return;
    }
    try {
      setIsSearching(true);
      setActiveSearchTerm(q);
      const res = await api.searchKnowledge({ q, document_id: selectedDoc?.id });
      setSearchResults(res.items || []);
    } catch (err: any) {
      console.error('Search failed:', err);
      alert(`检索失败: ${err.message}`);
    } finally {
      setIsSearching(false);
    }
  };

  // 清除检索，返回维护列表
  const handleClearSearch = () => {
    setSearchQuery('');
    setActiveSearchTerm('');
    setSearchResults([]);
    fetchKnowledgeData();
  };

  // 快捷停用/恢复
  const handleToggleItemLifecycle = async (e: React.MouseEvent, item: KnowledgeItem) => {
    e.stopPropagation();
    const targetStatus = item.lifecycle_status === 'disabled' ? 'active' : 'disabled';
    const actionText = targetStatus === 'disabled' ? '停用' : '恢复启用';
    if (
      !window.confirm(
        `确认${actionText}知识条目「${item.title}」？${
          targetStatus === 'disabled'
            ? '停用后将退出正式检索，不再对外提供服务。'
            : '恢复后将立即重新恢复正式检索服务。'
        }`
      )
    ) {
      return;
    }
    try {
      await api.updateKnowledgeLifecycle(item.id, targetStatus);
      if (activeSearchTerm) {
        handleExecuteSearch();
      } else {
        fetchKnowledgeData();
      }
    } catch (err: any) {
      alert(`${actionText}失败: ${err.message}`);
    }
  };

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

        {/* 第三行：一级状态筛选 + 未分类待办入口 + 折叠筛选按钮 + 检索输入框 */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '12px', flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
            {/* 一级状态筛选：全部 / 待核对 / 已确认 */}
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

            {/* 若未分类数 > 0，展示独立「需要分类 X 条」快捷待办按钮 */}
            {(stats?.unclassified_count || 0) > 0 && (
              <button
                type="button"
                data-testid="tab-unclassified"
                onClick={() => setCategoryFilter(categoryFilter === 'unclassified' ? 'all' : 'unclassified')}
                title="待分类条目必须由人工核对并指定五类主分类之一方可启用"
                style={{
                  height: '28px',
                  padding: '0 10px',
                  fontSize: '12px',
                  fontWeight: 600,
                  borderRadius: 'var(--radius-sm)',
                  border: categoryFilter === 'unclassified' ? '1px solid #D97706' : '1px solid #FCD34D',
                  backgroundColor: categoryFilter === 'unclassified' ? '#FEF3C7' : '#FFFBEB',
                  color: '#B45309',
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '4px',
                }}
              >
                <AlertTriangle size={13} />
                <span>需要分类 {stats?.unclassified_count} 条</span>
              </button>
            )}

            {/* 筛选面板展开/折叠按钮 */}
            <button
              type="button"
              data-testid="toggle-filters-btn"
              onClick={() => setShowFilterDrawer(!showFilterDrawer)}
              style={{
                height: '28px',
                padding: '0 10px',
                fontSize: '12px',
                fontWeight: (categoryFilter !== 'all' && categoryFilter !== 'unclassified') || lifecycleFilter !== 'all' ? 600 : 400,
                borderRadius: 'var(--radius-sm)',
                border: '1px solid var(--border-color)',
                backgroundColor: showFilterDrawer || (categoryFilter !== 'all' && categoryFilter !== 'unclassified') || lifecycleFilter !== 'all'
                  ? 'var(--bg-secondary)'
                  : '#FFFFFF',
                color: (categoryFilter !== 'all' && categoryFilter !== 'unclassified') || lifecycleFilter !== 'all'
                  ? 'var(--brand-accent)'
                  : 'var(--text-secondary)',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: '4px',
              }}
            >
              <Filter size={13} />
              <span>筛选</span>
              {((categoryFilter !== 'all' && categoryFilter !== 'unclassified' ? 1 : 0) + (lifecycleFilter !== 'all' ? 1 : 0)) > 0 && (
                <span
                  style={{
                    backgroundColor: 'var(--brand-accent)',
                    color: '#FFFFFF',
                    borderRadius: '10px',
                    padding: '0 5px',
                    fontSize: '10px',
                    lineHeight: '14px',
                  }}
                >
                  {(categoryFilter !== 'all' && categoryFilter !== 'unclassified' ? 1 : 0) + (lifecycleFilter !== 'all' ? 1 : 0)}
                </span>
              )}
            </button>
          </div>

          {/* 知识检索输入框 */}
          <div
            style={{
              width: '280px',
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
            <span title="点击或按回车执行正式检索" style={{ display: 'flex', alignItems: 'center' }}>
              <Search
                size={14}
                color="var(--text-muted)"
                style={{ marginRight: '6px', cursor: 'pointer' }}
                onClick={handleExecuteSearch}
              />
            </span>
            <input
              type="text"
              placeholder="检索已生效知识（回车检索）..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleExecuteSearch();
              }}
              style={{
                border: 'none',
                outline: 'none',
                fontSize: '12px',
                width: '100%',
                backgroundColor: 'transparent',
                color: 'var(--text-primary)',
              }}
            />
            {(searchQuery || activeSearchTerm) && (
              <span title="清空检索" style={{ display: 'flex', alignItems: 'center', cursor: 'pointer' }} onClick={handleClearSearch}>
                <X
                  size={13}
                  color="var(--text-muted)"
                />
              </span>
            )}
          </div>
        </div>

        {/* 展开的二级筛选面板 */}
        {showFilterDrawer && (
          <div
            style={{
              padding: '12px 14px',
              backgroundColor: 'var(--bg-secondary)',
              borderRadius: 'var(--radius-sm)',
              border: '1px solid var(--border-color)',
              display: 'flex',
              flexDirection: 'column',
              gap: '10px',
              fontSize: '12px',
            }}
          >
            {/* 主分类行 */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
              <span style={{ color: 'var(--text-muted)', width: '68px', flexShrink: 0 }}>主分类：</span>
              <button
                type="button"
                onClick={() => setCategoryFilter('all')}
                style={{
                  padding: '2px 8px',
                  borderRadius: 'var(--radius-sm)',
                  border: '1px solid var(--border-color)',
                  backgroundColor: categoryFilter === 'all' ? 'var(--brand-accent)' : '#FFFFFF',
                  color: categoryFilter === 'all' ? '#FFFFFF' : 'var(--text-primary)',
                  cursor: 'pointer',
                  fontSize: '12px',
                }}
              >
                全部
              </button>
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
                      padding: '2px 8px',
                      borderRadius: 'var(--radius-sm)',
                      border: isSelected ? `1px solid ${CATEGORY_STYLES[cat].border}` : '1px solid var(--border-color)',
                      backgroundColor: isSelected ? CATEGORY_STYLES[cat].bg : '#FFFFFF',
                      color: isSelected ? CATEGORY_STYLES[cat].text : 'var(--text-secondary)',
                      cursor: 'pointer',
                      fontSize: '12px',
                    }}
                  >
                    {cat} ({count})
                  </button>
                );
              })}
            </div>

            {/* 管理状态行 */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
              <span style={{ color: 'var(--text-muted)', width: '68px', flexShrink: 0 }}>管理状态：</span>
              <button
                type="button"
                onClick={() => setLifecycleFilter('all')}
                style={{
                  padding: '2px 8px',
                  borderRadius: 'var(--radius-sm)',
                  border: '1px solid var(--border-color)',
                  backgroundColor: lifecycleFilter === 'all' ? 'var(--brand-accent)' : '#FFFFFF',
                  color: lifecycleFilter === 'all' ? '#FFFFFF' : 'var(--text-primary)',
                  cursor: 'pointer',
                  fontSize: '12px',
                }}
              >
                全部状态
              </button>
              <button
                type="button"
                onClick={() => setLifecycleFilter('active')}
                style={{
                  padding: '2px 8px',
                  borderRadius: 'var(--radius-sm)',
                  border: '1px solid var(--border-color)',
                  backgroundColor: lifecycleFilter === 'active' ? '#ECFDF5' : '#FFFFFF',
                  color: lifecycleFilter === 'active' ? '#059669' : 'var(--text-secondary)',
                  cursor: 'pointer',
                  fontSize: '12px',
                }}
              >
                正常服务中 ({stats?.active_count !== undefined ? stats.active_count : stats?.confirmed_count || 0})
              </button>
              <button
                type="button"
                onClick={() => setLifecycleFilter('disabled')}
                style={{
                  padding: '2px 8px',
                  borderRadius: 'var(--radius-sm)',
                  border: '1px solid var(--border-color)',
                  backgroundColor: lifecycleFilter === 'disabled' ? '#FEF2F2' : '#FFFFFF',
                  color: lifecycleFilter === 'disabled' ? '#DC2626' : 'var(--text-secondary)',
                  cursor: 'pointer',
                  fontSize: '12px',
                }}
              >
                已停用 ({stats?.disabled_count || 0})
              </button>
            </div>
          </div>
        )}
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
        {/* 正式检索模式结果渲染 */}
        {activeSearchTerm ? (
          isSearching ? (
            <div
              style={{
                padding: '60px 20px',
                textAlign: 'center',
                color: 'var(--text-secondary)',
                fontSize: '13px',
              }}
            >
              正在检索已建立索引的正式知识...
            </div>
          ) : (
            <>
              {/* 检索结果头部提示 */}
              <div
                style={{
                  padding: '10px 16px',
                  backgroundColor: '#FFFFFF',
                  border: '1px solid var(--border-color)',
                  borderRadius: 'var(--radius-sm)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  fontSize: '13px',
                }}
              >
                <div>
                  检索「<strong style={{ color: 'var(--brand-accent)' }}>{activeSearchTerm}</strong>」：
                  共找到 <strong>{searchResults.length}</strong> 条正式服务中的可用知识
                </div>
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={handleClearSearch}
                  style={{ height: '26px', fontSize: '11px', padding: '0 10px' }}
                >
                  返回维护列表
                </button>
              </div>

              {searchResults.length === 0 ? (
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
                  <Search size={28} color="var(--text-muted)" />
                  <div style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)' }}>
                    未找到与「{activeSearchTerm}」相关的已生效知识
                  </div>
                  <div style={{ fontSize: '12px', color: 'var(--text-muted)', maxWidth: '420px', lineHeight: 1.5 }}>
                    正式检索仅匹配已确认启用、建立检索索引且未被停用的知识版本。
                  </div>
                  <button
                    type="button"
                    className="btn-secondary"
                    onClick={handleClearSearch}
                    style={{ height: '28px', fontSize: '12px', marginTop: '6px' }}
                  >
                    返回维护列表
                  </button>
                </div>
              ) : (
                searchResults.map((res) => {
                  const catStyle = res.primary_category
                    ? CATEGORY_STYLES[res.primary_category] || { bg: '#EDF2F7', text: '#4A5568', border: '#CBD5E0' }
                    : { bg: '#FEF3C7', text: '#B45309', border: '#FCD34D' };

                  return (
                    <div
                      key={res.item_id}
                      onClick={() => setActiveItemId(res.item_id)}
                      style={{
                        backgroundColor: '#FFFFFF',
                        borderRadius: 'var(--radius-md)',
                        border: '1px solid var(--border-color)',
                        padding: '16px 20px',
                        display: 'flex',
                        flexDirection: 'column',
                        gap: '10px',
                        cursor: 'pointer',
                        boxShadow: '0 1px 2px rgba(0, 0, 0, 0.03)',
                        transition: 'border-color 0.15s',
                      }}
                      onMouseEnter={(e) => {
                        e.currentTarget.style.borderColor = 'var(--brand-accent)';
                      }}
                      onMouseLeave={(e) => {
                        e.currentTarget.style.borderColor = 'var(--border-color)';
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
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
                            {res.primary_category || '知识'}
                          </span>
                          {res.subject && (
                            <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                              适用：{res.subject}
                            </span>
                          )}
                        </div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                          <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                            相关度：{Math.round(res.score * 100)}%
                          </span>
                          <span
                            style={{
                              fontSize: '11px',
                              fontWeight: 600,
                              padding: '2px 8px',
                              borderRadius: 'var(--radius-sm)',
                              backgroundColor: '#ECFDF5',
                              color: '#059669',
                              border: '1px solid #A7F3D0',
                            }}
                          >
                            服务中 (v{res.version_number})
                          </span>
                        </div>
                      </div>

                      <div style={{ fontSize: '15px', fontWeight: 600, color: 'var(--text-primary)' }}>
                        {res.title}
                      </div>

                      <div
                        style={{
                          fontSize: '13px',
                          color: 'var(--text-primary)',
                          lineHeight: 1.6,
                          backgroundColor: 'var(--bg-secondary)',
                          padding: '8px 12px',
                          borderRadius: 'var(--radius-sm)',
                        }}
                      >
                        {res.matched_snippets && res.matched_snippets.length > 0
                          ? res.matched_snippets.join(' ... ')
                          : res.statement}
                      </div>

                      <div
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'space-between',
                          fontSize: '11px',
                          color: 'var(--text-muted)',
                        }}
                      >
                        <span>来源：{res.document_title}</span>
                        <span style={{ color: 'var(--brand-accent)', fontWeight: 500, display: 'flex', alignItems: 'center', gap: '2px' }}>
                          <span>查看与维护</span>
                          <ArrowRight size={12} />
                        </span>
                      </div>
                    </div>
                  );
                })
              )}
            </>
          )
        ) : (
          /* 常规维护列表视图 */
          <>
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
            ) : items.length === 0 && (statusFilter !== 'all' || categoryFilter !== 'all' || lifecycleFilter !== 'all' || searchQuery.trim()) ? (
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
                const isDisabled = item.lifecycle_status === 'disabled';

                // 区分正常待确认与阻止启用的严重问题
                const blockingIssues = (item.quality_flags || []).filter((f) =>
                  f.includes('伪造来源') || f.includes('不匹配') || f.includes('冲突') || f.includes('无效提取') || f.includes('缺乏有效')
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
                      opacity: isDisabled ? 0.65 : 1,
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
                        {isDisabled ? (
                          <span
                            style={{
                              fontSize: '11px',
                              fontWeight: 600,
                              padding: '2px 8px',
                              borderRadius: 'var(--radius-sm)',
                              backgroundColor: '#FEF2F2',
                              color: '#DC2626',
                              border: '1px solid #FECACA',
                              display: 'flex',
                              alignItems: 'center',
                              gap: '3px',
                            }}
                          >
                            <Ban size={12} />
                            <span>已停用</span>
                          </span>
                        ) : item.has_draft_version ? (
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
                            <span>待核对新草稿 (v{item.draft_version_number})</span>
                          </span>
                        ) : isConfirmed ? (
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
                            <span>已确认启用</span>
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
                        <AlertCircle size={13} style={{ flexShrink: 0 }} />
                        <span>影响启用问题：{blockingIssues.join('；')}（需核对修正）</span>
                      </div>
                    )}

                    {/* 底栏 */}
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
                        {!selectedDoc && (
                          <span style={{ display: 'flex', alignItems: 'center', gap: '4px', color: 'var(--text-secondary)' }}>
                            <FileText size={12} />
                            <span>来源：{item.document_title}</span>
                          </span>
                        )}

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

                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        {/* 快捷停用/恢复按钮 */}
                        {isConfirmed && !isDisabled && (
                          <button
                            type="button"
                            onClick={(e) => handleToggleItemLifecycle(e, item)}
                            style={{
                              background: 'none',
                              border: '1px solid var(--border-color)',
                              borderRadius: 'var(--radius-xs)',
                              padding: '2px 8px',
                              fontSize: '11px',
                              color: 'var(--text-secondary)',
                              cursor: 'pointer',
                            }}
                          >
                            停用
                          </button>
                        )}
                        {isDisabled && (
                          <button
                            type="button"
                            onClick={(e) => handleToggleItemLifecycle(e, item)}
                            style={{
                              background: 'none',
                              border: '1px solid #A7F3D0',
                              backgroundColor: '#ECFDF5',
                              borderRadius: 'var(--radius-xs)',
                              padding: '2px 8px',
                              fontSize: '11px',
                              color: '#059669',
                              cursor: 'pointer',
                            }}
                          >
                            恢复启用
                          </button>
                        )}

                        <span style={{ color: 'var(--brand-accent)', fontWeight: 500, display: 'flex', alignItems: 'center', gap: '2px' }}>
                          <span>{isConfirmed ? '查看/维护' : '核对知识'}</span>
                          <ArrowRight size={12} />
                        </span>
                      </div>
                    </div>
                  </div>
                );
              })
            )}
          </>
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
