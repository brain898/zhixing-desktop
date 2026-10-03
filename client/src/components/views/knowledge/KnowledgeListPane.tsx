import React, { useState, useEffect, useRef } from 'react';
import {
  Search,
  CheckCircle2,
  Clock,
  AlertTriangle,
  FileText,
  BookOpen,
  X,
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
  SourceLocator,
} from '../../../types';
import { api } from '../../../services/api';
import { ProofreadingModal } from './ProofreadingModal';
import { SearchResultDetailModal } from './SearchResultDetailModal';
import { AppConfirmDialog } from '../../common/AppConfirmDialog';
import { formatAnchor, formatVersionLabel } from '../../../utils/formatters';

interface KnowledgeListPaneProps {
  selectedDoc: DocumentItem | null;
  refreshKey?: number;
  onClearDocSelection: () => void;
  onOpenDocPreview: (tab?: 'preview' | 'pipeline') => void;
  onOpenSearchSource: (documentId: string, versionId: string, locator: SourceLocator | null) => void;
}

import { CATEGORY_STYLES } from './knowledgeShared';
import { SegmentedTabs } from '../../ui/SegmentedTabs';

const sortItemsWithDisabledLast = (itemList: KnowledgeItem[]): KnowledgeItem[] => {
  return [...itemList].sort((a, b) => {
    const aDisabled = a.lifecycle_status === 'disabled' ? 1 : 0;
    const bDisabled = b.lifecycle_status === 'disabled' ? 1 : 0;
    return aDisabled - bDisabled;
  });
};

type FormalSearchStatus = 'idle' | 'loading' | 'results' | 'empty' | 'error';

interface FormalSearchSnapshot {
  q: string;
  document_id?: string;
  category: string[];
  customer_type: string[];
  business_scene: string[];
  problem_tag: string[];
}

export const KnowledgeListPane: React.FC<KnowledgeListPaneProps> = ({
  selectedDoc,
  refreshKey = 0,
  onClearDocSelection,
  onOpenDocPreview,
  onOpenSearchSource,
}) => {
  const [items, setItems] = useState<KnowledgeItem[]>([]);
  const [stats, setStats] = useState<KnowledgeStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [activeSearchTerm, setActiveSearchTerm] = useState('');
  const [searchResults, setSearchResults] = useState<SearchKnowledgeResultItem[]>([]);
  const [formalSearchStatus, setFormalSearchStatus] = useState<FormalSearchStatus>('idle');
  const [formalSearchError, setFormalSearchError] = useState<string | null>(null);
  const [lastSearchSnapshot, setLastSearchSnapshot] = useState<FormalSearchSnapshot | null>(null);
  const [activeSearchResult, setActiveSearchResult] = useState<SearchKnowledgeResultItem | null>(null);
  const searchAbortRef = useRef<AbortController | null>(null);
  const searchRequestSequenceRef = useRef(0);
  const knowledgeAbortRef = useRef<AbortController | null>(null);
  const knowledgeRequestSequenceRef = useRef(0);
  const lastRenderedDocIdRef = useRef<string | undefined>(selectedDoc?.id);

  const [lifecycleConfirmState, setLifecycleConfirmState] = useState<{
    isOpen: boolean;
    item: KnowledgeItem | null;
    targetStatus: 'active' | 'disabled';
    loading: boolean;
  }>({
    isOpen: false,
    item: null,
    targetStatus: 'disabled',
    loading: false,
  });

  const [showSearchFilterDrawer, setShowSearchFilterDrawer] = useState(false);
  const [formalCategories, setFormalCategories] = useState<string[]>([]);
  const [formalCustomerTypes, setFormalCustomerTypes] = useState<string[]>([]);
  const [formalBusinessScenes, setFormalBusinessScenes] = useState<string[]>([]);
  const [formalProblemTags, setFormalProblemTags] = useState<string[]>([]);
  const [tagOptions, setTagOptions] = useState({
    customer_types: [] as string[],
    business_scenes: [] as string[],
    problem_tags: [] as string[],
  });

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

  const fetchKnowledgeData = async (isSilentBackground = false) => {
    knowledgeAbortRef.current?.abort();
    const controller = new AbortController();
    knowledgeAbortRef.current = controller;
    const requestSeq = ++knowledgeRequestSequenceRef.current;

    if (!isSilentBackground) {
      setLoading(true);
      setListError(null);
    }

    try {
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

      const res = await api.getKnowledgeItems(
        {
          document_id: docId,
          category: categoryParam,
          review_status: reviewStatusParam,
          lifecycle_status: lifecycleParam,
        },
        controller.signal
      );

      if (requestSeq !== knowledgeRequestSequenceRef.current) return;
      setItems(sortItemsWithDisabledLast(res.items));
      setStats(res.stats);
      setListError(null);
      lastRenderedDocIdRef.current = docId;
    } catch (err: any) {
      if (err?.name === 'AbortError' || requestSeq !== knowledgeRequestSequenceRef.current) return;
      console.error('Failed to load knowledge items:', err);
      setListError(err?.message || '加载知识条目失败');
    } finally {
      if (requestSeq === knowledgeRequestSequenceRef.current) {
        setLoading(false);
      }
    }
  };

  const currentDocId = selectedDoc?.id;
  const currentDocStatus = selectedDoc?.processing_status;

  // 切换目标文件时立即重置已有条目，避免旧文件数据在新文件标题下显示或被误操作
  useEffect(() => {
    if (lastRenderedDocIdRef.current !== currentDocId) {
      knowledgeAbortRef.current?.abort();
      searchAbortRef.current?.abort();
      setItems([]);
      setStats(null);
      setListError(null);
      lastRenderedDocIdRef.current = currentDocId;
    }
  }, [currentDocId]);

  useEffect(() => {
    if (!activeSearchTerm) {
      // 若当前已有数据且文件 ID 相同，且只是由于 refreshKey 变化触发，执行平滑后台刷新
      const isSameDoc = lastRenderedDocIdRef.current === currentDocId;
      const isSilent = isSameDoc && items.length > 0;
      fetchKnowledgeData(isSilent);
    } else {
      executeFormalSearch();
    }
  }, [currentDocId, currentDocStatus, statusFilter, categoryFilter, lifecycleFilter, refreshKey]);

  useEffect(() => {
    api.getKnowledgeTags()
      .then(setTagOptions)
      .catch((err) => console.error('Failed to load knowledge tags:', err));
    return () => {
      searchAbortRef.current?.abort();
      knowledgeAbortRef.current?.abort();
    };
  }, []);

  const toggleFormalFilter = (
    value: string,
    selected: string[],
    setter: React.Dispatch<React.SetStateAction<string[]>>
  ) => {
    setter(selected.includes(value) ? selected.filter((item) => item !== value) : [...selected, value]);
  };

  const buildSearchSnapshot = (): FormalSearchSnapshot => ({
    q: searchQuery.trim(),
    document_id: selectedDoc?.id,
    category: [...formalCategories],
    customer_type: [...formalCustomerTypes],
    business_scene: [...formalBusinessScenes],
    problem_tag: [...formalProblemTags],
  });

  const executeFormalSearch = async (snapshotOverride?: FormalSearchSnapshot | null) => {
    const snapshot = snapshotOverride || buildSearchSnapshot();
    if (!snapshot.q) {
      if (activeSearchTerm) handleClearSearch();
      return;
    }

    searchAbortRef.current?.abort();
    const controller = new AbortController();
    searchAbortRef.current = controller;
    const requestSequence = ++searchRequestSequenceRef.current;

    setActiveSearchTerm(snapshot.q);
    setLastSearchSnapshot(snapshot);
    setFormalSearchStatus('loading');
    setFormalSearchError(null);
    setSearchResults([]);

    try {
      const res = await api.searchKnowledge(snapshot, controller.signal);
      if (requestSequence !== searchRequestSequenceRef.current) return;
      const results = res.items || [];
      setSearchResults(results);
      setFormalSearchStatus(results.length > 0 ? 'results' : 'empty');
    } catch (err: any) {
      if (err?.name === 'AbortError' || requestSequence !== searchRequestSequenceRef.current) return;
      console.error('Formal search failed:', err);
      setFormalSearchError(err?.message || '检索失败');
      setFormalSearchStatus('error');
    }
  };

  const handleExecuteSearch = () => {
    void executeFormalSearch();
  };

  const handleRetrySearch = () => {
    if (lastSearchSnapshot) void executeFormalSearch(lastSearchSnapshot);
  };

  // 清除正式查询，仅退出正式检索；维护范围与维护筛选保持原样。
  const handleClearSearch = () => {
    searchAbortRef.current?.abort();
    searchAbortRef.current = null;
    searchRequestSequenceRef.current += 1;
    setSearchQuery('');
    setActiveSearchTerm('');
    setSearchResults([]);
    setFormalSearchStatus('idle');
    setFormalSearchError(null);
    setLastSearchSnapshot(null);
    setActiveSearchResult(null);
    fetchKnowledgeData();
  };

  // 快捷停用/恢复（通过应用内确认弹窗）
  const handleToggleItemLifecycle = (e: React.MouseEvent, item: KnowledgeItem) => {
    e.stopPropagation();
    const targetStatus = item.lifecycle_status === 'disabled' ? 'active' : 'disabled';
    setLifecycleConfirmState({
      isOpen: true,
      item,
      targetStatus,
      loading: false,
    });
  };

  const handleConfirmLifecycleToggle = async () => {
    const { item, targetStatus } = lifecycleConfirmState;
    if (!item) return;

    setLifecycleConfirmState((prev) => ({ ...prev, loading: true }));
    try {
      await api.updateKnowledgeLifecycle(item.id, targetStatus);
      setItems((prev) => {
        const nextList = prev.map((it) => (it.id === item.id ? { ...it, lifecycle_status: targetStatus } : it));
        return sortItemsWithDisabledLast(nextList);
      });
      setLifecycleConfirmState({ isOpen: false, item: null, targetStatus: 'disabled', loading: false });
      if (activeSearchTerm) {
        handleExecuteSearch();
      } else {
        fetchKnowledgeData(true);
      }
    } catch (err: any) {
      alert(`${targetStatus === 'disabled' ? '停用' : '恢复启用'}失败: ${err.message}`);
      setLifecycleConfirmState((prev) => ({ ...prev, loading: false }));
    }
  };

  const handleCancelLifecycleToggle = () => {
    if (lifecycleConfirmState.loading) return;
    setLifecycleConfirmState({ isOpen: false, item: null, targetStatus: 'disabled', loading: false });
  };

  const handleRetryList = () => {
    void fetchKnowledgeData(false);
  };

  // 处理流水线「一句结果 + 对应动作」统一状态条
  const renderStatusStrip = (
    tone: 'progress' | 'danger' | 'warning' | 'pending' | 'success',
    icon: React.ReactNode,
    text: React.ReactNode,
    action: React.ReactNode,
    extra?: React.ReactNode
  ) => {
    const toneColor: Record<typeof tone, string> = {
      progress: 'var(--brand-accent)',
      danger: 'var(--danger-text)',
      warning: 'var(--warning-text)',
      pending: 'var(--warning-text)',
      success: 'var(--brand-accent)',
    };
    return (
      <div
        style={{
          padding: '8px 8px 8px 14px',
          backgroundColor: tone === 'danger' ? 'var(--danger-bg)' : 'var(--bg-primary)',
          borderRadius: 'var(--radius-md)',
          boxShadow: tone === 'danger' ? 'none' : 'var(--shadow-sm)',
          border: tone === 'danger' ? '1px solid var(--danger-border)' : '1px solid var(--border-color)',
          display: 'flex',
          alignItems: 'center',
          gap: '12px',
          fontSize: 'var(--font-size-sm)',
          color: 'var(--text-secondary)',
        }}
      >
        <span style={{ display: 'flex', color: toneColor[tone], flexShrink: 0 }}>{icon}</span>
        <span style={{ color: tone === 'danger' ? 'var(--danger-text)' : 'var(--text-primary)', minWidth: 0 }}>{text}</span>
        {extra}
        <span style={{ flex: 1 }} />
        {action}
      </div>
    );
  };

  const renderPendingProgress = (pending: number, total: number) => {
    const done = Math.max(total - pending, 0);
    const pct = total > 0 ? Math.round((done / total) * 100) : 0;
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px', color: 'var(--text-muted)', fontSize: 'var(--font-size-xs)' }}>
        <div className="zx-progress" style={{ width: '160px' }}>
          <i style={{ width: `${pct}%` }} />
        </div>
        <span>
          已处理 {done} / {total}
        </span>
      </div>
    );
  };

  const startReviewButton = (
    <button
      type="button"
      className="btn-primary btn-sm"
      onClick={() => {
        const firstPending = items.find((i) => i.review_status === 'pending_review') || items[0];
        if (firstPending) setActiveItemId(firstPending.id);
      }}
    >
      <span>开始核对</span>
      <ArrowRight size={13} />
    </button>
  );

  const renderPipelineBanner = () => {
    if (selectedDoc) {
      const status = selectedDoc.processing_status;

      if (status === 'parsing' || status === 'extracting' || status === 'queued') {
        return renderStatusStrip(
          'progress',
          <RotateCw size={15} className="spin-slow" />,
          '正在整理资料，可以离开此页面',
          <button type="button" className="btn-ghost btn-sm" onClick={() => onOpenDocPreview('pipeline')}>
            <span>查看进度</span>
            <ArrowRight size={13} />
          </button>
        );
      }

      if (status === 'failed') {
        return renderStatusStrip(
          'danger',
          <AlertCircle size={15} />,
          '文件已保存，但未能生成知识',
          <button type="button" className="btn-secondary btn-sm" onClick={() => onOpenDocPreview('pipeline')}>
            <span>查看原因与重试</span>
            <ArrowRight size={13} />
          </button>
        );
      }

      if (status === 'partial_failed') {
        return renderStatusStrip(
          'warning',
          <AlertTriangle size={15} />,
          '部分内容未能处理',
          <button type="button" className="btn-ghost btn-sm" onClick={() => onOpenDocPreview('pipeline')}>
            <span>查看未处理内容</span>
            <ArrowRight size={13} />
          </button>
        );
      }

      if (status === 'completed') {
        const pendingCount = stats?.pending_review_count || 0;
        const confirmedCount = stats?.confirmed_count || 0;

        if (pendingCount > 0) {
          return renderStatusStrip(
            'pending',
            <Clock size={15} />,
            <>整理完成，有 {pendingCount} 条知识待核对</>,
            startReviewButton,
            renderPendingProgress(pendingCount, stats?.total || 0)
          );
        }

        if (confirmedCount > 0) {
          return renderStatusStrip(
            'success',
            <CheckCircle2 size={15} />,
            <>已有 {confirmedCount} 条可用知识</>,
            <button
              type="button"
              className="btn-ghost btn-sm"
              onClick={() => {
                setStatusFilter('confirmed');
                setCategoryFilter('all');
              }}
            >
              <span>查看可用知识</span>
              <ArrowRight size={13} />
            </button>
          );
        }
      }
    } else if (stats && stats.pending_review_count > 0) {
      // 全部资料视图下存在待核对条目
      return renderStatusStrip(
        'pending',
        <Clock size={15} />,
        <>全库整理完成，共有 {stats.pending_review_count} 条知识待核对</>,
        startReviewButton,
        renderPendingProgress(stats.pending_review_count, stats.total || 0)
      );
    }

    return null;
  };

  const handleClearFilters = () => {
    setStatusFilter('all');
    setCategoryFilter('all');
    setLifecycleFilter('all');
  };

  const clearFormalFilters = () => {
    setFormalCategories([]);
    setFormalCustomerTypes([]);
    setFormalBusinessScenes([]);
    setFormalProblemTags([]);
  };

  const formalFilterCount =
    formalCategories.length +
    formalCustomerTypes.length +
    formalBusinessScenes.length +
    formalProblemTags.length;

  const getResultLocator = (result: SearchKnowledgeResultItem): SourceLocator | null => {
    const matchedEvidenceIds = new Set(
      (result.matched_fragments || []).flatMap((fragment) => fragment.evidence_ids || [])
    );
    const matchedEvidence = (result.evidence || []).find((item) => matchedEvidenceIds.has(item.id));
    return matchedEvidence?.source_locator || result.evidence?.[0]?.source_locator || null;
  };

  const describeResultLocator = (result: SearchKnowledgeResultItem): string => {
    const locator = getResultLocator(result);
    if (!locator) return '来源位置';
    if (locator.page_number !== null) {
      return `第 ${locator.page_number} 页${locator.heading_path ? ` · ${locator.heading_path}` : ''}`;
    }
    if (locator.heading_path && locator.paragraph_anchor) {
      return `${locator.heading_path} · ${formatAnchor(locator.paragraph_anchor)}`;
    }
    if (locator.paragraph_anchor) return formatAnchor(locator.paragraph_anchor);
    if (locator.heading_path) return locator.heading_path;
    return locator.block_index !== null ? `第 ${locator.block_index} 个结构块` : '来源位置';
  };

  const renderFormalFilterRow = (
    label: string,
    options: string[],
    selected: string[],
    setter: React.Dispatch<React.SetStateAction<string[]>>
  ) => (
    <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap' }}>
      <span style={{ color: 'var(--text-muted)', width: '68px', flexShrink: 0 }}>{label}</span>
      <button type="button" onClick={() => setter([])} style={filterChipStyle(selected.length === 0)}>
        全部
      </button>
      {options.map((option) => {
        const isSelected = selected.includes(option);
        return (
          <button
            key={option}
            type="button"
            onClick={() => toggleFormalFilter(option, selected, setter)}
            style={filterChipStyle(isSelected)}
          >
            {option}
          </button>
        );
      })}
    </div>
  );


  const hasSecondaryFilter = (categoryFilter !== 'all' && categoryFilter !== 'unclassified') || lifecycleFilter !== 'all';

  const filterChipStyle = (active: boolean): React.CSSProperties => ({
    height: '26px',
    padding: '0 10px',
    borderRadius: '13px',
    border: '1px solid transparent',
    backgroundColor: active ? 'var(--brand-600)' : 'var(--bg-primary)',
    color: active ? '#FFFFFF' : 'var(--text-secondary)',
    boxShadow: active ? 'none' : 'inset 0 0 0 1px var(--border-color)',
    cursor: 'pointer',
    fontSize: 'var(--font-size-xs)',
    fontWeight: active ? 500 : 400,
    whiteSpace: 'nowrap',
  });

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', height: '100%', minWidth: 0, backgroundColor: 'var(--bg-secondary)' }}>
      {/* 顶部控制栏 */}
      <div
        style={{
          padding: '14px 24px 4px',
          display: 'flex',
          flexDirection: 'column',
          gap: '12px',
        }}
      >
        {/* 第一行：维护浏览与正式检索使用互斥的状态控制 + 检索框 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', minWidth: 0 }}>
          {!activeSearchTerm ? (
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexShrink: 0 }}>
              {/* 一级状态筛选：全部 / 待核对 / 已确认 */}
              <SegmentedTabs
                value={statusFilter}
                onChange={(key) => setStatusFilter(key)}
                options={[
                  { key: 'all', label: '全部', count: stats?.total || 0, testId: 'status-all' },
                  { key: 'pending', label: '待核对', count: stats?.pending_review_count || 0, testId: 'status-pending' },
                  { key: 'confirmed', label: '已确认', count: stats?.confirmed_count || 0, testId: 'status-confirmed' },
                ]}
              />

              {/* 若未分类数 > 0，展示独立「需要分类 X 条」快捷待办按钮 */}
              {(stats?.unclassified_count || 0) > 0 && (
                <button
                  type="button"
                  data-testid="tab-unclassified"
                  className="zx-pill warning"
                  onClick={() => setCategoryFilter(categoryFilter === 'unclassified' ? 'all' : 'unclassified')}
                  title="待分类条目必须由人工核对并指定五类主分类之一方可启用"
                  style={{
                    cursor: 'pointer',
                    boxShadow: categoryFilter === 'unclassified' ? 'inset 0 0 0 1px var(--warning-text)' : 'none',
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
                className="btn-ghost"
                onClick={() => setShowFilterDrawer(!showFilterDrawer)}
                style={{
                  backgroundColor: showFilterDrawer ? 'var(--bg-hover)' : undefined,
                  color: hasSecondaryFilter ? 'var(--brand-accent)' : undefined,
                  fontWeight: hasSecondaryFilter ? 600 : 500,
                }}
              >
                <Filter size={14} />
                <span>筛选</span>
              </button>
            </div>
          ) : (
            <div
              data-testid="formal-search-mode"
              style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: 'var(--font-size-sm)', color: 'var(--text-primary)', fontWeight: 600 }}
            >
              <Search size={15} color="var(--brand-accent)" />
              <span>正式检索结果</span>
              <span className="zx-tag neutral">{lastSearchSnapshot?.document_id ? '当前文件范围' : '全部资料范围'}</span>
            </div>
          )}

          <span style={{ flex: 1 }} />

          {/* 独立原文与处理详情入口 */}
          {selectedDoc && (
            <>
              <button
                type="button"
                className="btn-ghost"
                onClick={() => onOpenDocPreview('preview')}
                title="查看该文件的原文预览与段落锚点"
              >
                <Eye size={14} />
                <span>查看原文</span>
              </button>
              <button
                type="button"
                className="btn-ghost"
                onClick={() => onOpenDocPreview('pipeline')}
                title="查看结构块拆解、任务耗时与技术信息"
              >
                <FileText size={14} />
                <span>处理详情</span>
              </button>
              <span className="zx-divider-v" />
            </>
          )}

          {/* 知识检索输入框 */}
          <div
            style={{
              flex: '0 1 320px',
              minWidth: '200px',
              height: 'var(--control-md)',
              border: '1px solid var(--border-strong)',
              borderRadius: 'var(--radius-sm)',
              display: 'flex',
              alignItems: 'center',
              padding: '0 3px 0 10px',
              backgroundColor: 'var(--bg-primary)',
              boxShadow: 'var(--shadow-sm)',
            }}
          >
            <Search size={14} color="var(--text-muted)" style={{ marginRight: '6px', flexShrink: 0 }} />
            <input
              type="text"
              data-testid="formal-search-input"
              placeholder="输入关键词，回车进入正式检索"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleExecuteSearch();
              }}
              style={{
                border: 'none',
                outline: 'none',
                boxShadow: 'none',
                fontSize: 'var(--font-size-sm)',
                width: '100%',
                minWidth: 0,
                backgroundColor: 'transparent',
                color: 'var(--text-primary)',
              }}
            />
            {(searchQuery || activeSearchTerm) && (
              <span
                data-testid="clear-formal-search"
                title="清空检索并返回维护浏览"
                style={{ display: 'flex', alignItems: 'center', cursor: 'pointer', padding: '0 4px' }}
                onClick={handleClearSearch}
              >
                <X size={14} color="var(--text-muted)" />
              </span>
            )}
            <button
              type="button"
              data-testid="formal-search-filter-toggle"
              onClick={() => setShowSearchFilterDrawer(!showSearchFilterDrawer)}
              title="设置正式检索条件"
              style={{
                color: formalFilterCount > 0 ? 'var(--brand-accent)' : 'var(--text-muted)',
                display: 'flex',
                alignItems: 'center',
                height: '24px',
                padding: '0 5px',
                gap: '2px',
                borderRadius: 'var(--radius-xs)',
                backgroundColor: showSearchFilterDrawer ? 'var(--bg-hover)' : 'transparent',
              }}
            >
              <Filter size={13} />
              {formalFilterCount > 0 && <span style={{ fontSize: 'var(--font-size-xs)' }}>{formalFilterCount}</span>}
            </button>
            <button
              type="button"
              data-testid="formal-search-submit"
              onClick={handleExecuteSearch}
              disabled={!searchQuery.trim() || formalSearchStatus === 'loading'}
              className={searchQuery.trim() ? 'btn-primary btn-sm' : 'btn-ghost btn-sm'}
              style={{ height: '24px', marginLeft: '2px', opacity: 1 }}
            >
              {formalSearchStatus === 'loading' ? '检索中' : '检索'}
            </button>
          </div>
        </div>

        {/* 第二行：流水线一句结果与动作条 */}
        {renderPipelineBanner()}

        {/* 展开的二级筛选面板 */}
        {showFilterDrawer && !activeSearchTerm && (
          <div
            style={{
              padding: '12px 14px',
              backgroundColor: 'var(--bg-primary)',
              borderRadius: 'var(--radius-md)',
              border: '1px solid var(--border-color)',
              display: 'flex',
              flexDirection: 'column',
              gap: '10px',
              fontSize: 'var(--font-size-xs)',
            }}
          >
            {/* 主分类行 */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap' }}>
              <span style={{ color: 'var(--text-muted)', width: '68px', flexShrink: 0 }}>主分类</span>
              <button type="button" onClick={() => setCategoryFilter('all')} style={filterChipStyle(categoryFilter === 'all')}>
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
                    style={filterChipStyle(isSelected)}
                  >
                    {cat} ({count})
                  </button>
                );
              })}
            </div>

            {/* 管理状态行 */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap' }}>
              <span style={{ color: 'var(--text-muted)', width: '68px', flexShrink: 0 }}>管理状态</span>
              <button type="button" onClick={() => setLifecycleFilter('all')} style={filterChipStyle(lifecycleFilter === 'all')}>
                全部状态
              </button>
              <button type="button" onClick={() => setLifecycleFilter('active')} style={filterChipStyle(lifecycleFilter === 'active')}>
                正常服务中 ({stats?.active_count !== undefined ? stats.active_count : stats?.confirmed_count || 0})
              </button>
              <button type="button" onClick={() => setLifecycleFilter('disabled')} style={filterChipStyle(lifecycleFilter === 'disabled')}>
                已停用 ({stats?.disabled_count || 0})
              </button>
            </div>
          </div>
        )}

        {showSearchFilterDrawer && (
          <div
            data-testid="formal-search-filters"
            style={{
              padding: '12px 14px',
              backgroundColor: 'var(--bg-primary)',
              borderRadius: 'var(--radius-md)',
              border: '1px solid var(--border-color)',
              display: 'flex',
              flexDirection: 'column',
              gap: '10px',
              fontSize: 'var(--font-size-xs)',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <span style={{ color: 'var(--text-primary)', fontWeight: 600 }}>
                正式检索条件
                <span style={{ color: 'var(--text-muted)', fontWeight: 400, marginLeft: '8px' }}>
                  同一项多选为“或”，不同项之间为“且”
                </span>
              </span>
              {formalFilterCount > 0 && (
                <button type="button" className="btn-ghost btn-sm" onClick={clearFormalFilters} style={{ color: 'var(--brand-accent)' }}>
                  清除检索条件
                </button>
              )}
            </div>
            {renderFormalFilterRow(
              '主分类',
              ['制度与标准', '方法与工具', '项目案例', '指标数据', '专家经验'],
              formalCategories,
              setFormalCategories
            )}
            {renderFormalFilterRow('客户类型', tagOptions.customer_types, formalCustomerTypes, setFormalCustomerTypes)}
            {renderFormalFilterRow('业务场景', tagOptions.business_scenes, formalBusinessScenes, setFormalBusinessScenes)}
            {renderFormalFilterRow('问题', tagOptions.problem_tags, formalProblemTags, setFormalProblemTags)}
          </div>
        )}
      </div>

      {/* 知识条目列表展示区 */}
      <div
        style={{
          flex: 1,
          overflowY: 'auto',
          padding: '12px 24px 24px',
          display: 'flex',
          flexDirection: 'column',
          gap: '10px',
          backgroundColor: 'var(--bg-secondary)',
        }}
      >
        {/* 正式检索模式结果渲染 */}
        {activeSearchTerm ? (
          formalSearchStatus === 'loading' ? (
            <div
              style={{
                padding: '60px 20px',
                textAlign: 'center',
                color: 'var(--text-secondary)',
                fontSize: '13px',
              }}
            >
              正在检索可用知识...
            </div>
          ) : (
            <>
              {/* 检索结果头部提示 */}
              <div
                style={{
                  padding: '8px 8px 8px 16px',
                  backgroundColor: 'var(--bg-primary)',
                  border: '1px solid var(--border-color)',
                  borderRadius: 'var(--radius-md)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  fontSize: '13px',
                }}
              >
                <div>
                  {formalSearchStatus === 'error' ? (
                    <>
                      检索「<strong style={{ color: 'var(--brand-accent)' }}>{activeSearchTerm}</strong>」失败
                    </>
                  ) : (
                    <>
                      检索「<strong style={{ color: 'var(--brand-accent)' }}>{activeSearchTerm}</strong>」：
                      共找到 <strong>{searchResults.length}</strong> 条正式服务中的可用知识
                    </>
                  )}
                </div>
                <button
                  type="button"
                  className="btn-ghost btn-sm"
                  onClick={handleClearSearch}
                >
                  返回维护列表
                </button>
              </div>

              {formalSearchStatus === 'error' ? (
                <div
                  data-testid="formal-search-error"
                  style={{
                    backgroundColor: 'var(--bg-primary)',
                    borderRadius: 'var(--radius-md)',
                    boxShadow: 'var(--shadow-md)',
                    padding: '50px 20px',
                    textAlign: 'center',
                    display: 'flex',
                    flexDirection: 'column',
                    alignItems: 'center',
                    gap: '10px',
                  }}
                >
                  <AlertCircle size={28} color="var(--danger-text)" />
                  <div style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)' }}>
                    检索暂时不可用，请重试。
                  </div>
                  {formalSearchError && formalSearchError !== '检索暂时不可用，请重试。' && (
                    <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-secondary)' }}>
                      {formalSearchError}
                    </div>
                  )}
                  <button
                    type="button"
                    data-testid="formal-search-retry"
                    className="btn-secondary"
                    onClick={handleRetrySearch}
                    style={{ height: '30px', fontSize: 'var(--font-size-xs)', marginTop: '4px' }}
                  >
                    重试
                  </button>
                </div>
              ) : searchResults.length === 0 ? (
                <div
                  data-testid="formal-search-empty"
                  style={{
                    backgroundColor: 'var(--bg-primary)',
                    borderRadius: 'var(--radius-md)',
                    boxShadow: 'var(--shadow-md)',
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
                    没有找到符合条件的可用知识，可调整关键词或筛选条件。
                  </div>
                  <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)', maxWidth: '460px', lineHeight: 1.5 }}>
                    正式检索只展示当前范围内符合启用、有效期、权限与索引资格的知识。
                  </div>
                  <button
                    type="button"
                    className="btn-secondary"
                    onClick={handleClearSearch}
                    style={{ height: '28px', fontSize: 'var(--font-size-xs)', marginTop: '6px' }}
                  >
                    返回维护列表
                  </button>
                </div>
              ) : (
                searchResults.map((res) => {
                  const catStyle = res.primary_category
                    ? CATEGORY_STYLES[res.primary_category] || { bg: 'var(--bg-sunken)', text: 'var(--text-secondary)', border: 'var(--border-strong)' }
                    : { bg: 'var(--warning-bg)', text: 'var(--warning-text)', border: 'var(--warning-border)' };

                  return (
                    <div
                      key={res.version_id}
                      data-testid={`formal-search-result-${res.version_id}`}
                      onClick={() => setActiveSearchResult(res)}
                      style={{
                        backgroundColor: 'var(--bg-primary)',
                        borderRadius: 'var(--radius-md)',
                        boxShadow: 'var(--shadow-md)',
                        padding: '16px 20px',
                        display: 'flex',
                        flexDirection: 'column',
                        gap: '10px',
                        flexShrink: 0,
                      }}
                      className="zx-card zx-card-interactive"
                    >
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                          <span className="zx-tag" style={{ backgroundColor: catStyle.bg, color: catStyle.text }}>
                            {res.primary_category || '知识'}
                          </span>
                          {res.subject && (
                            <span style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-secondary)' }}>
                              适用：{res.subject}
                            </span>
                          )}
                        </div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                          <span className="zx-badge success">知识版本 v{res.version_number}</span>
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

                      {res.business_scenes?.length > 0 && (
                        <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap' }}>
                          <span style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)' }}>业务场景：</span>
                          {res.business_scenes.map((scene) => (
                            <span key={scene} className="zx-tag neutral">
                              {scene}
                            </span>
                          ))}
                        </div>
                      )}

                      <div
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'space-between',
                          gap: '12px',
                          fontSize: 'var(--font-size-xs)',
                          color: 'var(--text-muted)',
                        }}
                      >
                        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', minWidth: 0, flexWrap: 'wrap' }}>
                          <span title={res.source.file_name}>来源文件：{res.source.file_name || res.document_title}</span>
                          <span>文件版本：{formatVersionLabel(res.source.document_version_label)}</span>
                        </div>
                        <button
                          type="button"
                          data-testid={`formal-source-${res.version_id}`}
                          onClick={(e) => {
                            e.stopPropagation();
                            onOpenSearchSource(
                              res.source.document_id,
                              res.source.document_version_id,
                              getResultLocator(res)
                            );
                          }}
                          style={{
                            border: 'none',
                            background: 'transparent',
                            color: 'var(--brand-accent)',
                            fontWeight: 600,
                            display: 'flex',
                            alignItems: 'center',
                            gap: '3px',
                            cursor: 'pointer',
                            fontSize: 'var(--font-size-xs)',
                            flexShrink: 0,
                          }}
                        >
                          <Eye size={12} />
                          <span>{describeResultLocator(res)}</span>
                        </button>
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
            {loading && items.length === 0 ? (
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
            ) : listError && items.length === 0 ? (
              <div
                data-testid="knowledge-list-error"
                style={{
                  backgroundColor: 'var(--bg-primary)',
                  borderRadius: 'var(--radius-md)',
                  boxShadow: 'var(--shadow-md)',
                  padding: '50px 20px',
                  textAlign: 'center',
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  gap: '12px',
                }}
              >
                <AlertCircle size={32} color="var(--danger-text)" />
                <div style={{ fontSize: '15px', fontWeight: 600, color: 'var(--danger-text)' }}>
                  知识列表加载失败
                </div>
                <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-secondary)', maxWidth: '420px', lineHeight: 1.5 }}>
                  {listError}
                </div>
                <button
                  type="button"
                  data-testid="knowledge-list-retry"
                  className="btn-secondary"
                  onClick={handleRetryList}
                  style={{ height: '30px', fontSize: 'var(--font-size-xs)', marginTop: '6px' }}
                >
                  重试
                </button>
              </div>
            ) : selectedDoc && ['parsing', 'extracting', 'queued'].includes(selectedDoc.processing_status) ? (
              /* 整理中空状态 */
              <div
                style={{
                  backgroundColor: 'var(--bg-primary)',
                  borderRadius: 'var(--radius-md)',
                  boxShadow: 'var(--shadow-md)',
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
                <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-secondary)', maxWidth: '420px', lineHeight: 1.5 }}>
                  系统正在调用大模型提炼知识原子并校验来源证据，您可以离开此页面处理其他任务，整理完成后将自动呈现。
                </div>
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => onOpenDocPreview('pipeline')}
                  style={{ height: '30px', fontSize: 'var(--font-size-xs)', marginTop: '6px' }}
                >
                  查看后台处理详情
                </button>
              </div>
            ) : selectedDoc && selectedDoc.processing_status === 'failed' ? (
              /* 处理失败空状态 */
              <div
                style={{
                  backgroundColor: 'var(--bg-primary)',
                  borderRadius: 'var(--radius-md)',
                  boxShadow: 'var(--shadow-md)',
                  padding: '50px 20px',
                  textAlign: 'center',
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  gap: '12px',
                }}
              >
                <AlertCircle size={32} color="var(--danger-text)" />
                <div style={{ fontSize: '15px', fontWeight: 600, color: 'var(--danger-text)' }}>
                  文件已保存，但未能生成知识
                </div>
                <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-secondary)', maxWidth: '400px', lineHeight: 1.5 }}>
                  正文解析或知识提炼阶段遇到阻碍。请查看失败原因并针对失败步骤发起重试。
                </div>
                <button
                  type="button"
                  className="btn-primary"
                  onClick={() => onOpenDocPreview('pipeline')}
                  style={{ height: '32px', fontSize: 'var(--font-size-xs)', marginTop: '6px' }}
                >
                  查看失败原因并重试
                </button>
              </div>
            ) : items.length === 0 && (statusFilter !== 'all' || categoryFilter !== 'all' || lifecycleFilter !== 'all') ? (
              /* 筛选无结果空状态 */
              <div
                style={{
                  backgroundColor: 'var(--bg-primary)',
                  borderRadius: 'var(--radius-md)',
                  boxShadow: 'var(--shadow-md)',
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
                <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)' }}>
                  可尝试更换搜索词或清除当前分类与状态筛选条件。
                </div>
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={handleClearFilters}
                  style={{ height: '28px', fontSize: 'var(--font-size-xs)', marginTop: '6px' }}
                >
                  清除全部筛选
                </button>
              </div>
            ) : items.length === 0 ? (
              /* 未发现可提取内容空状态 */
              <div
                style={{
                  backgroundColor: 'var(--bg-primary)',
                  borderRadius: 'var(--radius-md)',
                  boxShadow: 'var(--shadow-md)',
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
                <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)', maxWidth: '400px', lineHeight: 1.5 }}>
                  该资料可能为纯目录、空表格或扫描件，未包含符合标准的规则或方法。您可以查看原文或更换资料。
                </div>
                {selectedDoc && (
                  <button
                    type="button"
                    className="btn-secondary"
                    onClick={() => onOpenDocPreview('preview')}
                    style={{ height: '28px', fontSize: 'var(--font-size-xs)', marginTop: '6px' }}
                  >
                    查看文件原文
                  </button>
                )}
              </div>
            ) : (
              /* 正常知识条目列表渲染：卡片优先展示标题、简短摘要、分类和主要业务状态 */
              <>
                {listError && (
                  <div
                    data-testid="knowledge-list-error-banner"
                    style={{
                      padding: '10px 16px',
                      backgroundColor: 'var(--danger-bg)',
                      border: '1px solid var(--danger-border)',
                      borderRadius: 'var(--radius-sm)',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      fontSize: '13px',
                      color: 'var(--danger-text)',
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                      <AlertCircle size={15} />
                      <span>刷新知识列表失败：{listError}</span>
                    </div>
                    <button
                      type="button"
                      className="btn-secondary"
                      onClick={handleRetryList}
                      style={{ height: '26px', fontSize: 'var(--font-size-xs)', padding: '0 10px' }}
                    >
                      重试
                    </button>
                  </div>
                )}
                {items.map((item) => {
                const catStyle = item.primary_category
                  ? CATEGORY_STYLES[item.primary_category] || { bg: 'var(--bg-sunken)', text: 'var(--text-secondary)', border: 'var(--border-strong)' }
                  : { bg: 'var(--warning-bg)', text: 'var(--warning-text)', border: 'var(--warning-border)' };

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
                    className="zx-card zx-card-interactive"
                    onClick={() => setActiveItemId(item.id)}
                    style={{
                      padding: '16px 20px 14px',
                      display: 'flex',
                      flexDirection: 'column',
                      gap: '6px',
                      opacity: isDisabled ? 0.62 : 1,
                      flexShrink: 0,
                    }}
                  >
                    {/* 顶栏：知识标题 + 主要业务状态（遵守 PRD 状态边界与可用性要求） */}
                    <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '12px' }}>
                      <div style={{ fontSize: 'var(--font-size-md)', fontWeight: 600, color: 'var(--text-primary)', lineHeight: 1.4 }}>
                        {item.title}
                      </div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexShrink: 0, paddingTop: '2px' }}>
                        {isDisabled ? (
                          <span className="zx-badge danger">
                            <span>已停用</span>
                          </span>
                        ) : item.has_draft_version ? (
                          <span className="zx-badge warning">
                            <span>待核对新草稿 (v{item.draft_version_number})</span>
                          </span>
                        ) : isConfirmed ? (
                          <span className="zx-badge success">
                            <span>已确认启用</span>
                          </span>
                        ) : (
                          <span className="zx-badge warning">
                            <span>待核对</span>
                          </span>
                        )}
                      </div>
                    </div>

                    {/* 简短核心陈述摘要（最多两行） */}
                    <div
                      style={{
                        fontSize: 'var(--font-size-sm)',
                        color: 'var(--text-secondary)',
                        lineHeight: 1.6,
                        display: '-webkit-box',
                        WebkitLineClamp: 2,
                        WebkitBoxOrient: 'vertical',
                        overflow: 'hidden',
                      }}
                    >
                      {item.statement}
                    </div>

                    {/* 阻止启用的阻塞性问题明确提示 */}
                    {blockingIssues.length > 0 && (
                      <div
                        style={{
                          fontSize: 'var(--font-size-xs)',
                          color: 'var(--danger-text)',
                          backgroundColor: 'var(--danger-bg)',
                          padding: '5px 10px',
                          borderRadius: 'var(--radius-sm)',
                          display: 'flex',
                          alignItems: 'center',
                          gap: '6px',
                          marginTop: '2px',
                        }}
                      >
                        <AlertCircle size={13} style={{ flexShrink: 0 }} />
                        <span>影响启用问题：{blockingIssues.join('；')}（需核对修正）</span>
                      </div>
                    )}

                    {/* 底栏：分类 · 适用对象 · 来源 · 场景 | 操作 */}
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        gap: '12px',
                        fontSize: 'var(--font-size-xs)',
                        color: 'var(--text-muted)',
                        marginTop: '6px',
                        minHeight: '28px',
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', gap: '10px', minWidth: 0, flexWrap: 'wrap' }}>
                        <span className="zx-tag" style={{ backgroundColor: catStyle.bg, color: catStyle.text }}>
                          {item.primary_category || '待分类'}
                        </span>

                        {item.subject && <span style={{ color: 'var(--text-secondary)' }}>适用：{item.subject}</span>}

                        {!selectedDoc && (
                          <span style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                            <FileText size={12} />
                            <span>来源：{item.document_title}</span>
                          </span>
                        )}

                        {item.business_scenes &&
                          item.business_scenes.slice(0, 2).map((s) => (
                            <span key={s} style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                              <span style={{ color: 'var(--border-strong)' }}>·</span>
                              <span>{s}</span>
                            </span>
                          ))}
                      </div>

                      <div className="zx-card-hover-actions" style={{ display: 'flex', alignItems: 'center', gap: '6px', flexShrink: 0 }}>
                        {/* 快捷停用/恢复按钮 */}
                        {isConfirmed && !isDisabled && (
                          <button type="button" className="btn-ghost btn-sm" onClick={(e) => handleToggleItemLifecycle(e, item)}>
                            停用
                          </button>
                        )}
                        {isDisabled && (
                          <button
                            type="button"
                            className="btn-ghost btn-sm"
                            onClick={(e) => handleToggleItemLifecycle(e, item)}
                            style={{ color: 'var(--brand-accent)' }}
                          >
                            恢复启用
                          </button>
                        )}

                        <span className="btn-secondary btn-sm">
                          <span>{isConfirmed ? '查看/维护' : '核对知识'}</span>
                          <ArrowRight size={13} />
                        </span>
                      </div>
                    </div>
                  </div>
                );
              })}
              </>
            )}
          </>
        )}
      </div>

      {/* 核对知识弹窗（支持上一条/下一条与连续核对） */}
      <ProofreadingModal
        itemId={activeItemId}
        itemList={items.map((i) => i.id)}
        items={items}
        onSelectNext={(nextId) => setActiveItemId(nextId)}
        onClose={() => setActiveItemId(null)}
        onSaved={() => fetchKnowledgeData(true)}
        onDeleted={() => fetchKnowledgeData(true)}
      />

      <SearchResultDetailModal
        result={activeSearchResult}
        onClose={() => setActiveSearchResult(null)}
        onOpenSource={(documentId, versionId, locator) => {
          setActiveSearchResult(null);
          onOpenSearchSource(documentId, versionId, locator);
        }}
      />

      <AppConfirmDialog
        isOpen={lifecycleConfirmState.isOpen}
        title={lifecycleConfirmState.targetStatus === 'disabled' ? '停用知识条目？' : '恢复启用知识条目？'}
        variant={lifecycleConfirmState.targetStatus === 'disabled' ? 'danger' : 'primary'}
        targetName={lifecycleConfirmState.item?.title}
        confirmText={lifecycleConfirmState.targetStatus === 'disabled' ? '停用知识' : '恢复启用'}
        cancelText="取消"
        initialFocus={lifecycleConfirmState.targetStatus === 'disabled' ? 'cancel' : 'confirm'}
        loading={lifecycleConfirmState.loading}
        impactDescription={
          lifecycleConfirmState.targetStatus === 'disabled'
            ? '停用后该条目将退出正式检索，不再对外提供服务。历史版本与证据完整保留，可随时恢复启用。'
            : '恢复后该条目将重新符合正式检索条件，即刻向检索服务生效。'
        }
        onConfirm={handleConfirmLifecycleToggle}
        onCancel={handleCancelLifecycleToggle}
      />
    </div>
  );
};
