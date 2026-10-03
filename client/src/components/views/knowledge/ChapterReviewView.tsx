import { StructuredReview } from './StructuredReview';
import React, { useState, useEffect, useRef } from 'react';
import {
  FileText,
  AlertTriangle,
  AlertCircle,
  HelpCircle,
  ShieldAlert,
  Check,
  X,
  ArrowRight,
  RotateCcw,
} from 'lucide-react';
import {
  ChapterReviewResponse,
  ChapterGroup,
  ChapterSourceBlock,
  ChapterKnowledgeItem,
  DocumentItem,
} from '../../../types';
import { api } from '../../../services/api';
import { AppConfirmDialog } from '../../common/AppConfirmDialog';

interface ChapterReviewViewProps {
  documentId: string;
  versionId: string;
  refreshKey?: number;
  selectedDoc: DocumentItem | null;
  onOpenProofreadModal: (itemId: string) => void;
  onBackToCardsView?: () => void;
  onRefreshData?: () => void;
}

export const ChapterReviewView: React.FC<ChapterReviewViewProps> = ({
  documentId,
  versionId,
  refreshKey = 0,
  selectedDoc: _selectedDoc,
  onOpenProofreadModal,
  onBackToCardsView,
  onRefreshData,
}) => {
  const [data, setData] = useState<ChapterReviewResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // 当前展开的章节 key
  const [activeChapterKey, setActiveChapterKey] = useState<string | null>(null);

  // 原文高亮选中的 block id
  const [highlightedBlockId, setHighlightedBlockId] = useState<string | null>(null);
  const blockRefs = useRef<Record<string, HTMLDivElement | null>>({});

  // 批量确认弹窗状态
  const [batchConfirmModalChapter, setBatchConfirmModalChapter] = useState<ChapterGroup | null>(null);
  const [batchConfirming, setBatchConfirming] = useState(false);
  const [batchConfirmResult, setBatchConfirmResult] = useState<string | null>(null);

  // 标记忽略弹窗状态
  const [ignoreTargetBlock, setIgnoreTargetBlock] = useState<ChapterSourceBlock | null>(null);
  const [ignoreReason, setIgnoreReason] = useState('非实质业务规范或属于文档格式噪音');
  const [ignoring, setIgnoring] = useState(false);

  // 手工补充知识弹窗状态
  const [supplementTargetBlock, setSupplementTargetBlock] = useState<ChapterSourceBlock | null>(null);
  const [supplementTitle, setSupplementTitle] = useState('');
  const [supplementCategory, setSupplementCategory] = useState('');
  const [supplementAtomType, setSupplementAtomType] = useState('方法');
  const [supplementImportance, setSupplementImportance] = useState<'critical' | 'normal' | 'informational'>('normal');
  const [supplementStatement, setSupplementStatement] = useState('');
  const [supplementContent, setSupplementContent] = useState('');
  const [supplementing, setSupplementing] = useState(false);

  // 调整业务重要程度弹窗/内联修改状态
  const [adjustingItemId, setAdjustingItemId] = useState<string | null>(null);
  const [adjustingLevel, setAdjustingLevel] = useState<'critical' | 'normal' | 'informational'>('normal');
  const [adjustingRationale, setAdjustingRationale] = useState('');

  // 筛选章节模式：'all' | 'uncovered' | 'pending'
  const [chapterFilter, setChapterFilter] = useState<'all' | 'uncovered' | 'pending'>('all');
  const reviewRequestSequenceRef = useRef(0);

  const fetchReviewData = async (preserveActiveChapter = true) => {
    const requestSequence = ++reviewRequestSequenceRef.current;
    try {
      setLoading(true);
      setError(null);
      const res = await api.getChapterReview(documentId, versionId);
      if (requestSequence !== reviewRequestSequenceRef.current || res.document_id !== documentId || res.version_id !== versionId) return;
      setData(res);

      if (res.chapters.length > 0) {
        setActiveChapterKey((currentKey) => {
          if (preserveActiveChapter && currentKey && res.chapters.some((c) => c.chapter_key === currentKey)) return currentKey;
          const firstPending = res.chapters.find(
            (c) => c.stats.pending_items > 0 || c.stats.uncovered_blocks > 0 || c.stats.model_suggested_ignore_blocks > 0
          );
          return firstPending ? firstPending.chapter_key : res.chapters[0].chapter_key;
        });
      } else {
        setActiveChapterKey(null);
      }
    } catch (err: any) {
      console.error('Failed to load chapter review data:', err);
      if (requestSequence === reviewRequestSequenceRef.current) setError(err.message || '加载章节审核数据失败');
    } finally {
      if (requestSequence === reviewRequestSequenceRef.current) setLoading(false);
    }
  };

  useEffect(() => {
    setData(null);
    setActiveChapterKey(null);
    setHighlightedBlockId(null);
    fetchReviewData(false);
    return () => {
      reviewRequestSequenceRef.current += 1;
    };
  }, [documentId, versionId]);

  useEffect(() => {
    if (refreshKey > 0) void fetchReviewData(true);
  }, [refreshKey]);

  const activeChapter = data?.chapters.find((c) => c.chapter_key === activeChapterKey) || null;

  // 定位并高亮原文段落
  const handleScrollToBlock = (blockId: string) => {
    setHighlightedBlockId(blockId);
    const el = blockRefs.current[blockId];
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  };

  // 忽略段落
  const handleConfirmIgnore = async () => {
    if (!ignoreTargetBlock || loading) return;
    try {
      setIgnoring(true);
      await api.ignoreSourceBlock(documentId, versionId, ignoreTargetBlock.id, {
        ignore_reason: ignoreReason.trim() || '管理员手工标记忽略',
      });
      setIgnoreTargetBlock(null);
      await fetchReviewData(true);
      onRefreshData?.();
    } catch (err: any) {
      alert('标记忽略失败：' + (err.message || '网络错误'));
    } finally {
      setIgnoring(false);
    }
  };

  // 取消忽略段落
  const handleUnignore = async (blockId: string) => {
    if (loading) return;
    try {
      await api.unignoreSourceBlock(documentId, versionId, blockId);
      await fetchReviewData(true);
      onRefreshData?.();
    } catch (err: any) {
      alert('取消忽略失败：' + (err.message || '网络错误'));
    }
  };

  // 手工补充知识
  const handleConfirmSupplement = async () => {
    if (!supplementTargetBlock || loading) return;
    if (!supplementTitle.trim()) {
      alert('请输入补充知识条目的标题');
      return;
    }
    if (!supplementStatement.trim() || !supplementContent.trim()) {
      alert('核心陈述和知识正文均不能为空');
      return;
    }
    try {
      setSupplementing(true);
      await api.supplementKnowledgeFromBlock(documentId, versionId, supplementTargetBlock.id, {
        title: supplementTitle.trim(),
        atom_type: supplementAtomType,
        primary_category: supplementCategory || null,
        statement: supplementStatement.trim(),
        content: supplementContent.trim(),
        business_importance: supplementImportance,
      });
      setSupplementTargetBlock(null);
      setSupplementTitle('');
      setSupplementStatement('');
      setSupplementContent('');
      await fetchReviewData(true);
      onRefreshData?.();
    } catch (err: any) {
      alert('补充知识失败：' + (err.message || '网络错误'));
    } finally {
      setSupplementing(false);
    }
  };

  // 调整业务重要程度
  const handleSaveImportance = async (itemId: string) => {
    if (loading) return;
    const item = data?.chapters.flatMap((chapter) => chapter.items).find((entry) => entry.item_id === itemId);
    if (!item) return;
    try {
      await api.updateBusinessImportance(itemId, {
        business_importance: adjustingLevel,
        importance_rationale: adjustingRationale.trim(),
        version_id: item.version_id,
        revision_token: item.revision_token,
      });
      setAdjustingItemId(null);
      await fetchReviewData(true);
      onRefreshData?.();
    } catch (err: any) {
      alert('调整重要程度失败：' + (err.message || '网络错误'));
    }
  };

  // 应用内确认弹窗状态
  const [confirmDialogState, setConfirmDialogState] = useState<{
    isOpen: boolean;
    title: string;
    variant: 'danger' | 'primary';
    confirmText: string;
    cancelText?: string;
    targetName?: string;
    description?: React.ReactNode;
    impactDescription?: React.ReactNode;
    loading?: boolean;
    initialFocus?: 'cancel' | 'confirm';
    onConfirm: () => void | Promise<void>;
  }>({
    isOpen: false,
    title: '',
    variant: 'primary',
    confirmText: '确定',
    onConfirm: () => {},
  });

  // 快捷确认单条知识
  const handleConfirmSingleItem = async (item: ChapterKnowledgeItem) => {
    if (loading) return;

    const doConfirm = async () => {
      try {
        await api.confirmKnowledgeItem(item.item_id, { revision_token: item.revision_token });
        await fetchReviewData(true);
        onRefreshData?.();
        setConfirmDialogState((prev) => ({ ...prev, isOpen: false }));
      } catch (err: any) {
        alert('确认条目失败：' + (err.message || '网络错误'));
      }
    };

    if (item.business_importance === 'critical') {
      setConfirmDialogState({
        isOpen: true,
        title: '重要操作核验确认',
        variant: 'primary',
        confirmText: '核实无误，确认通过',
        cancelText: '取消',
        targetName: item.title,
        description: '该条目被标定为重要操作或关键安全规范。',
        impactDescription: `核验依据：${item.importance_rationale || '涉及安全、应急或关键参数'}。确认后将正式发布该条目并生效检索。`,
        initialFocus: 'confirm',
        onConfirm: doConfirm,
      });
      return;
    } else if (item.quality_flags.length > 0) {
      setConfirmDialogState({
        isOpen: true,
        title: '核对提示确认',
        variant: 'primary',
        confirmText: '已核实，确认启用',
        cancelText: '取消',
        targetName: item.title,
        description: `该条目仍有 ${item.quality_flags.length} 项自动核对提示。`,
        impactDescription: '请对照原文确认实际含义、数值、单位和适用条件。确认后将正式启用。',
        initialFocus: 'confirm',
        onConfirm: doConfirm,
      });
      return;
    }

    await doConfirm();
  };

  // 执行章节内普通条目批量确认
  const handleExecuteBatchConfirm = async () => {
    if (!batchConfirmModalChapter || loading) return;
    const canBatchItems = batchConfirmModalChapter.items.filter((item) => item.can_batch_confirm && item.review_status === 'pending_review');
    if (canBatchItems.length === 0) return;

    try {
      setBatchConfirming(true);
      const res = await api.batchConfirmKnowledgeItems({
        items: canBatchItems.map((item) => ({ item_id: item.item_id, revision_token: item.revision_token })),
      });

      setBatchConfirmResult(`批量确认完成！成功确认 ${res.confirmed_count} 条，安全跳过 ${res.skipped_count} 条。已写入审计记录。`);
      setTimeout(() => {
        setBatchConfirmModalChapter(null);
        setBatchConfirmResult(null);
      }, 1800);

      await fetchReviewData(true);
      onRefreshData?.();
    } catch (err: any) {
      alert('批量确认失败：' + (err.message || '网络错误'));
    } finally {
      setBatchConfirming(false);
    }
  };

  if (loading && !data) {
    return (
      <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--text-secondary)' }}>
        正在分析文档章节结构并加载集中核对视图...
      </div>
    );
  }

  if (error || !data) {
    return (
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', gap: '12px' }}>
        <div style={{ color: 'var(--error-text)', fontSize: 'var(--font-size-sm)' }}>{error || '未获取到审核数据'}</div>
        <button className="btn-secondary" onClick={() => fetchReviewData(false)}>重试</button>
      </div>
    );
  }

  // 章节列表按筛选条件过滤
  const filteredChapters = data.chapters.filter((chap) => {
    if (chapterFilter === 'uncovered') return chap.stats.uncovered_blocks > 0 || chap.stats.model_suggested_ignore_blocks > 0;
    if (chapterFilter === 'pending') return chap.stats.pending_items > 0;
    return true;
  });

  // 当前选中章节中的知识条目：解耦区分重点审核与普通审核
  const criticalOrDoubtItems = activeChapter?.items.filter(
    (item) => item.business_importance === 'critical' || item.quality_flags.length > 0 || (item.issues_summary && (item.issues_summary.deterministic_errors.length > 0 || item.issues_summary.model_doubts.length > 0))
  ) || [];

  const normalItems = activeChapter?.items.filter(
    (item) => !criticalOrDoubtItems.some((ci) => ci.item_id === item.item_id)
  ) || [];

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0, backgroundColor: '#FFFFFF' }}>
      {/* 顶部总览卡片栏 */}
      <div
        style={{
          padding: '16px 24px',
          borderBottom: '1px solid var(--border-color)',
          backgroundColor: 'var(--bg-secondary)',
          display: 'flex',
          flexDirection: 'column',
          gap: '12px',
          flexShrink: 0,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <span style={{ fontSize: 'var(--font-size-title)', fontWeight: 600, color: 'var(--text-primary)' }}>
                章节集中核对
              </span>
              <span
                style={{
                  fontSize: 'var(--font-size-xs)',
                  padding: '2px 8px',
                  borderRadius: '10px',
                  backgroundColor: 'var(--info-bg)',
                  color: 'var(--info-text)',
                  fontWeight: 500,
                }}
              >
                {data.document_title} · {data.version_label}
              </span>
              {onBackToCardsView && (
                <button
                  type="button"
                  onClick={onBackToCardsView}
                  style={{
                    border: 'none',
                    background: 'transparent',
                    color: 'var(--brand-accent)',
                    cursor: 'pointer',
                    fontSize: 'var(--font-size-xs)',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '4px',
                    marginLeft: '8px',
                  }}
                >
                  <ArrowRight size={13} />
                  <span>切换回卡片平铺视图</span>
                </button>
              )}
            </div>
            <p style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-secondary)', marginTop: '4px' }}>
              按原文顺序组织审核，保留知识条目独立性。严格将重点操作/模型疑点与普通条目解耦核对。
            </p>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <button
              className="btn-secondary"
              onClick={() => fetchReviewData(true)}
              disabled={loading}
              style={{ fontSize: 'var(--font-size-xs)', height: '30px' }}
            >
              <RotateCcw size={13} />
              <span>刷新核对</span>
            </button>
          </div>
        </div>

        {/* 概览统计指标药丸 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '16px', flexWrap: 'wrap', fontSize: 'var(--font-size-xs)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <span style={{ color: 'var(--text-muted)' }}>总章节数：</span>
            <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{data.summary.total_chapters} 节</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <span style={{ color: 'var(--text-muted)' }}>正文段落：</span>
            <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{data.summary.total_blocks} 段</span>
          </div>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              backgroundColor: data.summary.uncovered_blocks > 0 ? 'var(--warning-bg)' : 'var(--bg-sunken)',
              padding: '2px 8px',
              borderRadius: '4px',
              border: data.summary.uncovered_blocks > 0 ? '1px solid var(--warning-border)' : '1px solid var(--border-color)',
            }}
          >
            <AlertCircle size={13} color={data.summary.uncovered_blocks > 0 ? 'var(--warning-text)' : 'var(--text-secondary)'} />
            <span style={{ color: data.summary.uncovered_blocks > 0 ? 'var(--warning-text)' : 'var(--text-secondary)' }}>
              尚未覆盖待检查段落：
            </span>
            <strong style={{ color: data.summary.uncovered_blocks > 0 ? 'var(--warning-text)' : 'var(--text-secondary)' }}>
              {data.summary.uncovered_blocks}
            </strong>
          </div>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              backgroundColor: data.summary.model_suggested_ignore_blocks > 0 ? 'var(--bg-neutral-tag)' : 'var(--bg-sunken)',
              padding: '2px 8px',
              borderRadius: '4px',
              border: data.summary.model_suggested_ignore_blocks > 0 ? '1px solid var(--border-strong)' : '1px solid var(--border-color)',
            }}
          >
            <HelpCircle size={13} color={data.summary.model_suggested_ignore_blocks > 0 ? 'var(--text-secondary)' : 'var(--text-secondary)'} />
            <span style={{ color: data.summary.model_suggested_ignore_blocks > 0 ? 'var(--text-secondary)' : 'var(--text-secondary)' }}>
              模型建议忽略待人工检查：
            </span>
            <strong>{data.summary.model_suggested_ignore_blocks}</strong>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <span style={{ color: 'var(--text-muted)' }}>待核对知识：</span>
            <span style={{ fontWeight: 600, color: 'var(--info-text)' }}>{data.summary.pending_items} 条</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <span style={{ color: 'var(--text-muted)' }}>已确认：</span>
            <span style={{ fontWeight: 600, color: 'var(--brand-accent)' }}>{data.summary.confirmed_items} 条</span>
          </div>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              backgroundColor: data.summary.critical_items > 0 ? 'var(--danger-bg)' : 'var(--bg-sunken)',
              padding: '2px 8px',
              borderRadius: '4px',
              border: data.summary.critical_items > 0 ? '1px solid var(--danger-border)' : '1px solid var(--border-color)',
            }}
          >
            <ShieldAlert size={13} color={data.summary.critical_items > 0 ? 'var(--danger-text)' : 'var(--text-secondary)'} />
            <span style={{ color: data.summary.critical_items > 0 ? 'var(--danger-text)' : 'var(--text-secondary)' }}>重要操作条目：</span>
            <strong style={{ color: data.summary.critical_items > 0 ? 'var(--danger-text)' : 'var(--text-secondary)' }}>
              {data.summary.critical_items} 条
            </strong>
          </div>
        </div>
      </div>

      {/* 主工作区：左侧章节目录导航 + 右侧双栏集中核对区 */}
      <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
        {/* 左侧章节目录列 (260px) */}
        <div
          style={{
            width: '280px',
            borderRight: '1px solid var(--border-color)',
            backgroundColor: 'var(--bg-secondary)',
            display: 'flex',
            flexDirection: 'column',
            flexShrink: 0,
          }}
        >
          {/* 筛选章节状态 */}
          <div
            style={{
              padding: '10px 12px',
              borderBottom: '1px solid var(--border-color)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              backgroundColor: '#FFFFFF',
            }}
          >
            <span style={{ fontSize: 'var(--font-size-xs)', fontWeight: 600, color: 'var(--text-secondary)' }}>
              章节导航 ({filteredChapters.length})
            </span>
            <select
              value={chapterFilter}
              onChange={(e) => setChapterFilter(e.target.value as any)}
              style={{
                fontSize: '12px',
                border: '1px solid var(--border-color)',
                borderRadius: '4px',
                padding: '2px 4px',
                color: 'var(--text-secondary)',
                backgroundColor: '#FFFFFF',
                outline: 'none',
              }}
            >
              <option value="all">全部章节</option>
              <option value="uncovered">有待检查原文</option>
              <option value="pending">有待核对条目</option>
            </select>
          </div>

          {/* 章节列表项 */}
          <div style={{ flex: 1, overflowY: 'auto', padding: '8px' }}>
            {filteredChapters.map((chap) => {
              const isSelected = chap.chapter_key === activeChapterKey;
              return (
                <div
                  key={chap.chapter_key}
                  onClick={() => setActiveChapterKey(chap.chapter_key)}
                  style={{
                    padding: '10px 12px',
                    borderRadius: 'var(--radius-sm)',
                    backgroundColor: isSelected ? '#FFFFFF' : 'transparent',
                    border: isSelected ? '1px solid var(--brand-accent)' : '1px solid transparent',
                    boxShadow: isSelected ? '0 1px 3px rgba(0,0,0,0.06)' : 'none',
                    marginBottom: '6px',
                    cursor: 'pointer',
                    transition: 'all 0.15s ease',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '6px' }}>
                    <div
                      style={{
                        fontSize: 'var(--font-size-sm)',
                        fontWeight: isSelected ? 600 : 500,
                        color: isSelected ? 'var(--brand-accent)' : 'var(--text-primary)',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                      title={chap.chapter_name}
                    >
                      {chap.chapter_name}
                    </div>
                  </div>

                  {chap.is_derived_group && (
                    <div style={{ fontSize: '12px', color: 'var(--warning-text)', marginTop: '2px', backgroundColor: 'var(--warning-bg)', padding: '1px 4px', borderRadius: '2px', display: 'inline-block' }}>
                      {chap.group_description}
                    </div>
                  )}

                  {/* 状态指示与数量 */}
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: '6px', fontSize: '12px' }}>
                    <span
                      style={{
                        padding: '1px 6px',
                        borderRadius: '3px',
                        backgroundColor:
                          chap.chapter_status === 'has_uncovered'
                            ? 'var(--warning-bg)'
                            : chap.chapter_status === 'pending_review' || chap.chapter_status === 'pending_source_review'
                            ? 'var(--info-bg)'
                            : chap.chapter_status === 'fully_reviewed'
                            ? 'var(--success-bg)'
                            : 'var(--bg-sunken)',
                        color:
                          chap.chapter_status === 'has_uncovered'
                            ? 'var(--warning-text)'
                            : chap.chapter_status === 'pending_review' || chap.chapter_status === 'pending_source_review'
                            ? 'var(--info-text)'
                            : chap.chapter_status === 'fully_reviewed'
                            ? 'var(--brand-accent)'
                            : 'var(--text-secondary)',
                        fontWeight: 500,
                      }}
                    >
                      {chap.status_label}
                    </span>

                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px', color: 'var(--text-muted)' }}>
                      <span>段落:{chap.stats.total_blocks}</span>
                      <span>知识:{chap.stats.total_items}</span>
                    </div>
                  </div>

                  {/* 包含重要操作提示 */}
                  {chap.stats.critical_items > 0 && (
                    <div style={{ marginTop: '4px', fontSize: '12px', color: 'var(--danger-text)', display: 'flex', alignItems: 'center', gap: '3px' }}>
                      <ShieldAlert size={11} />
                      <span>{chap.stats.critical_items} 条重要安全/应急操作</span>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>

        {/* 右侧：双栏沉浸式集中核对主区域 */}
        {activeChapter ? (
          <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
            {/* 左半区 (45%)：只读原文流水 */}
            <div
              style={{
                flex: '0 0 45%',
                borderRight: '1px solid var(--border-color)',
                display: 'flex',
                flexDirection: 'column',
                minHeight: 0,
                backgroundColor: '#FFFFFF',
              }}
            >
              {/* 原文列标题 */}
              <div
                style={{
                  padding: '12px 16px',
                  borderBottom: '1px solid var(--border-color)',
                  backgroundColor: 'var(--bg-secondary)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  flexShrink: 0,
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <FileText size={15} color="var(--text-secondary)" />
                  <span style={{ fontSize: 'var(--font-size-sm)', fontWeight: 600, color: 'var(--text-primary)' }}>
                    原始文本流水 (只读)
                  </span>
                  <span style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)' }}>
                    {activeChapter.blocks.length} 段
                  </span>
                </div>
                <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                  严格保持原文，不改写、不去符号
                </div>
              </div>

              {/* 原文段落列表 */}
              <div style={{ flex: 1, overflowY: 'auto', padding: '16px' }}>
                {activeChapter.blocks.map((block) => {
                  const isHighlighted = highlightedBlockId === block.id;
                  const isUncovered = block.coverage_status === 'uncovered';
                  const isAdminIgnored = block.coverage_status === 'admin_ignored';
                  const isModelSuggestedIgnore = block.coverage_status === 'model_suggested_ignore';
                  const isAssociated = block.coverage_status === 'associated_candidate';

                  return (
                    <div
                      key={block.id}
                      ref={(el) => (blockRefs.current[block.id] = el)}
                      style={{
                        padding: '12px 14px',
                        borderRadius: 'var(--radius-sm)',
                        backgroundColor: isHighlighted
                          ? 'var(--warning-bg)'
                          : isUncovered
                          ? 'var(--warning-bg)'
                          : isAdminIgnored
                          ? 'var(--bg-secondary)'
                          : '#FFFFFF',
                        border: isHighlighted
                          ? '2px solid var(--warning-text)'
                          : isUncovered
                          ? '1px dashed var(--warning-text)'
                          : isAdminIgnored
                          ? '1px dashed var(--border-color)'
                          : '1px solid var(--border-color)',
                        marginBottom: '12px',
                        transition: 'background-color 0.2s ease, border-color 0.2s ease',
                      }}
                    >
                      {/* 段落顶栏：锚点与状态标签 */}
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                          <span
                            style={{
                              fontSize: '12px',
                              fontFamily: 'monospace',
                              color: 'var(--text-muted)',
                              backgroundColor: 'var(--bg-secondary)',
                              padding: '1px 4px',
                              borderRadius: '3px',
                            }}
                          >
                            #{block.block_index}
                          </span>
                          {block.page_number && (
                            <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                              P.{block.page_number}
                            </span>
                          )}
                          <span
                            style={{
                              fontSize: '12px',
                              padding: '1px 6px',
                              borderRadius: '3px',
                              fontWeight: 500,
                              backgroundColor: isUncovered
                                ? 'var(--warning-bg)'
                                : isAdminIgnored
                                ? 'var(--border-color)'
                                : isModelSuggestedIgnore
                                ? 'var(--bg-neutral-tag)'
                                : 'var(--success-bg)',
                              color: isUncovered
                                ? 'var(--warning-text)'
                                : isAdminIgnored
                                ? 'var(--text-secondary)'
                                : isModelSuggestedIgnore
                                ? 'var(--text-secondary)'
                                : 'var(--brand-accent)',
                            }}
                            title={block.coverage_note}
                          >
                            {isUncovered && '尚未覆盖，待检查'}
                            {isAdminIgnored && `管理员已忽略 (${block.ignore_reason || '已人工核验'})`}
                            {isModelSuggestedIgnore && '模型建议忽略'}
                            {isAssociated && '已关联候选条目'}
                          </span>
                        </div>

                        {/* 操作按钮：忽略 / 补充 */}
                        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                          {(isUncovered || isModelSuggestedIgnore) && (
                            <>
                              <button
                                type="button"
                                onClick={() => {
                                  setIgnoreTargetBlock(block);
                                  setIgnoreReason(
                                    isModelSuggestedIgnore
                                      ? block.ignore_reason || '已人工核对模型建议，确认无需提取'
                                      : '非实质业务规范或属于文档格式噪音'
                                  );
                                }}
                                style={{
                                  fontSize: '12px',
                                  padding: '2px 6px',
                                  borderRadius: '3px',
                                  border: '1px solid var(--border-color)',
                                  backgroundColor: '#FFFFFF',
                                  color: 'var(--text-secondary)',
                                  cursor: 'pointer',
                                }}
                                title="标记该段落为非知识正文并记录理由"
                              >
                                {isModelSuggestedIgnore ? '人工确认忽略' : '标记忽略'}
                              </button>
                              <button
                                type="button"
                                onClick={() => {
                                  setSupplementTargetBlock(block);
                                  setSupplementTitle('');
                                  setSupplementCategory('');
                                  setSupplementAtomType('方法');
                                  setSupplementStatement(block.text_content.slice(0, 100));
                                  setSupplementContent(block.text_content);
                                }}
                                style={{
                                  fontSize: '12px',
                                  padding: '2px 6px',
                                  borderRadius: '3px',
                                  border: '1px solid var(--info-border)',
                                  backgroundColor: 'var(--info-bg)',
                                  color: 'var(--info-text)',
                                  cursor: 'pointer',
                                  fontWeight: 500,
                                }}
                                title="由此未覆盖段落手工补充一条候选知识"
                              >
                                补充知识
                              </button>
                            </>
                          )}
                          {isAdminIgnored && (
                            <button
                              type="button"
                              onClick={() => handleUnignore(block.id)}
                              style={{
                                fontSize: '12px',
                                padding: '2px 6px',
                                borderRadius: '3px',
                                border: '1px solid var(--border-color)',
                                backgroundColor: '#FFFFFF',
                                color: 'var(--text-secondary)',
                                cursor: 'pointer',
                              }}
                            >
                              取消忽略
                            </button>
                          )}
                        </div>
                      </div>

                      {/* 段落正文：严格保持原文 */}
                      <div
                        style={{
                          fontSize: '13px',
                          lineHeight: '1.6',
                          color: 'var(--text-primary)',
                          whiteSpace: 'pre-wrap',
                          wordBreak: 'break-word',
                        }}
                      >
                        {block.text_content}
                      </div>

                      {/* 已关联知识条目快捷入口 */}
                      {block.associated_items.length > 0 && (
                        <div style={{ marginTop: '8px', paddingTop: '6px', borderTop: '1px dashed var(--border-color)', display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap' }}>
                          <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>关联条目：</span>
                          {block.associated_items.map((it) => (
                            <span
                              key={it.item_id}
                              style={{
                                fontSize: '12px',
                                color: 'var(--brand-accent)',
                                backgroundColor: 'var(--info-bg)',
                                padding: '1px 6px',
                                borderRadius: '3px',
                                border: '1px solid var(--info-border)',
                              }}
                            >
                              {it.title} ({it.field_name})
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>

            {/* 右半区 (55%)：知识条目集中核对 */}
            <div
              style={{
                flex: '0 0 55%',
                display: 'flex',
                flexDirection: 'column',
                minHeight: 0,
                backgroundColor: 'var(--bg-secondary)',
              }}
            >
              {/* 右半区标题与本章批量确认条 */}
              <div
                style={{
                  padding: '12px 16px',
                  borderBottom: '1px solid var(--border-color)',
                  backgroundColor: '#FFFFFF',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  flexShrink: 0,
                }}
              >
                <div>
                  <span style={{ fontSize: 'var(--font-size-sm)', fontWeight: 600, color: 'var(--text-primary)' }}>
                    本章候选知识核对 ({activeChapter.items.length} 条)
                  </span>
                  <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '2px' }}>
                    待核对 {activeChapter.stats.pending_items} · 已确认 {activeChapter.stats.confirmed_items} · 可批量 {activeChapter.stats.can_batch_confirm_count}
                  </div>
                </div>

                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <button
                    type="button"
                    className="btn-primary"
                    disabled={activeChapter.stats.can_batch_confirm_count === 0}
                    onClick={() => setBatchConfirmModalChapter(activeChapter)}
                    style={{
                      height: '30px',
                      fontSize: 'var(--font-size-xs)',
                      gap: '4px',
                      backgroundColor: activeChapter.stats.can_batch_confirm_count > 0 ? 'var(--brand-accent)' : 'var(--text-muted)',
                    }}
                    title={
                      activeChapter.stats.can_batch_confirm_count > 0
                        ? `批量确认本章 ${activeChapter.stats.can_batch_confirm_count} 条普通无疑点条目（需二次确认）`
                        : '本章无符合条件的普通待核对条目（重要操作与疑点条目必须逐条确认）'
                    }
                  >
                    <Check size={14} />
                    <span>批量确认普通条目 ({activeChapter.stats.can_batch_confirm_count})</span>
                  </button>
                </div>
              </div>

              {/* 知识条目列表滚动区 */}
              <div style={{ flex: 1, overflowY: 'auto', padding: '16px', display: 'flex', flexDirection: 'column', gap: '16px' }}>
                {/* 疑点/关注点警示条（若有） */}
                {activeChapter.attention_points.length > 0 && (
                  <div
                    style={{
                      padding: '10px 14px',
                      borderRadius: 'var(--radius-sm)',
                      backgroundColor: 'var(--warning-bg)',
                      border: '1px solid var(--warning-border)',
                      display: 'flex',
                      flexDirection: 'column',
                      gap: '6px',
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: 'var(--font-size-xs)', fontWeight: 600, color: 'var(--warning-text)' }}>
                      <AlertTriangle size={14} />
                      <span>本章重点核查关注点 ({activeChapter.attention_points.length})</span>
                    </div>
                    {activeChapter.attention_points.map((pt, idx) => (
                      <div key={idx} style={{ fontSize: '12px', color: 'var(--warning-text)', paddingLeft: '20px' }}>
                        • {pt.message}
                      </div>
                    ))}
                  </div>
                )}

                {/* 1. 重点审核专区 (重要操作 critical / 存在疑点) */}
                {criticalOrDoubtItems.length > 0 && (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', borderLeft: '4px solid var(--danger-text)', paddingLeft: '8px' }}>
                      <span style={{ fontSize: 'var(--font-size-sm)', fontWeight: 600, color: 'var(--danger-text)' }}>
                        重点审核（重要操作 / 模型疑点 / 阻断项）
                      </span>
                      <span style={{ fontSize: '12px', color: 'var(--danger-text)', backgroundColor: 'var(--danger-bg)', padding: '1px 6px', borderRadius: '3px' }}>
                        {criticalOrDoubtItems.length} 条 · 禁止普通批量确认，须人工逐条复核
                      </span>
                    </div>

                    {criticalOrDoubtItems.map((item) => (
                      <KnowledgeItemCard
                        key={item.item_id}
                        item={item}
                        isCriticalZone
                        onLocateBlock={handleScrollToBlock}
                        onProofread={() => onOpenProofreadModal(item.item_id)}
                        onQuickConfirm={() => handleConfirmSingleItem(item)}
                        onAdjustImportance={() => {
                          setAdjustingItemId(item.item_id);
                          setAdjustingLevel(item.business_importance);
                          setAdjustingRationale(item.importance_rationale || '');
                        }}
                      />
                    ))}
                  </div>
                )}

                {/* 2. 普通审核专区 (常规流程/标准) */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px', borderLeft: '4px solid var(--info-text)', paddingLeft: '8px' }}>
                    <span style={{ fontSize: 'var(--font-size-sm)', fontWeight: 600, color: 'var(--info-text)' }}>
                      普通审核（常规业务知识）
                    </span>
                    <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                      {normalItems.length} 条
                    </span>
                  </div>

                  {normalItems.length === 0 && (
                    <div style={{ padding: '16px', textAlign: 'center', color: 'var(--text-muted)', fontSize: 'var(--font-size-xs)' }}>
                      本章节无普通审核条目
                    </div>
                  )}

                  {normalItems.map((item) => (
                    <KnowledgeItemCard
                      key={item.item_id}
                      item={item}
                      isCriticalZone={false}
                      onLocateBlock={handleScrollToBlock}
                      onProofread={() => onOpenProofreadModal(item.item_id)}
                      onQuickConfirm={() => handleConfirmSingleItem(item)}
                      onAdjustImportance={() => {
                        setAdjustingItemId(item.item_id);
                        setAdjustingLevel(item.business_importance);
                        setAdjustingRationale(item.importance_rationale || '');
                      }}
                    />
                  ))}
                </div>
              </div>
            </div>
          </div>
        ) : (
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-muted)' }}>
            请在左侧选择一个章节进行集中核对
          </div>
        )}
      </div>

      {/* 模态弹窗 1：批量确认二次确认与跳过清单弹窗 */}
      {batchConfirmModalChapter && (
        <BatchConfirmModal
          chapter={batchConfirmModalChapter}
          isSubmitting={batchConfirming}
          resultMessage={batchConfirmResult}
          onConfirm={handleExecuteBatchConfirm}
          onClose={() => {
            setBatchConfirmModalChapter(null);
            setBatchConfirmResult(null);
          }}
        />
      )}

      {/* 模态弹窗 2：标记忽略段落弹窗 */}
      {ignoreTargetBlock && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            backgroundColor: 'rgba(18, 26, 22, 0.42)',
            zIndex: 1000,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <div
            style={{
              width: '460px',
              backgroundColor: '#FFFFFF',
              borderRadius: 'var(--radius-md)',
              boxShadow: 'var(--shadow-lg)',
              padding: '20px',
              display: 'flex',
              flexDirection: 'column',
              gap: '14px',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <span style={{ fontSize: 'var(--font-size-md)', fontWeight: 600, color: 'var(--text-primary)' }}>
                标记正文段落为忽略
              </span>
              <button
                type="button"
                onClick={() => setIgnoreTargetBlock(null)}
                style={{ border: 'none', background: 'transparent', cursor: 'pointer' }}
              >
                <X size={16} />
              </button>
            </div>

            <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-secondary)' }}>
              目标段落 #{ignoreTargetBlock.block_index}：
              <div
                style={{
                  marginTop: '4px',
                  padding: '8px',
                  backgroundColor: 'var(--bg-secondary)',
                  borderRadius: '4px',
                  maxHeight: '80px',
                  overflowY: 'auto',
                  border: '1px solid var(--border-color)',
                }}
              >
                {ignoreTargetBlock.text_content}
              </div>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
              <label style={{ fontSize: 'var(--font-size-xs)', fontWeight: 500, color: 'var(--text-secondary)' }}>
                忽略原因或核查说明：
              </label>
              <textarea
                value={ignoreReason}
                onChange={(e) => setIgnoreReason(e.target.value)}
                rows={3}
                style={{
                  border: '1px solid var(--border-color)',
                  borderRadius: '4px',
                  padding: '8px',
                  fontSize: 'var(--font-size-xs)',
                  outline: 'none',
                }}
                placeholder="例如：非实质业务正文、纯排版目录、封面或格式噪音"
              />
            </div>

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px', marginTop: '6px' }}>
              <button className="btn-secondary" onClick={() => setIgnoreTargetBlock(null)} disabled={ignoring}>
                取消
              </button>
              <button className="btn-primary" onClick={handleConfirmIgnore} disabled={ignoring}>
                {ignoring ? '保存中...' : '确认忽略'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 模态弹窗 3：从未覆盖段落补充知识弹窗 */}
      {supplementTargetBlock && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            backgroundColor: 'rgba(18, 26, 22, 0.42)',
            zIndex: 1000,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <div
            style={{
              width: '560px',
              backgroundColor: '#FFFFFF',
              borderRadius: 'var(--radius-md)',
              boxShadow: 'var(--shadow-lg)',
              padding: '20px',
              display: 'flex',
              flexDirection: 'column',
              gap: '14px',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <span style={{ fontSize: 'var(--font-size-md)', fontWeight: 600, color: 'var(--text-primary)' }}>
                从未覆盖段落补充候选知识
              </span>
              <button
                type="button"
                onClick={() => setSupplementTargetBlock(null)}
                style={{ border: 'none', background: 'transparent', cursor: 'pointer' }}
              >
                <X size={16} />
              </button>
            </div>

            <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-secondary)' }}>
              来源原文段落 #{supplementTargetBlock.block_index}：
              <div
                style={{
                  marginTop: '4px',
                  padding: '8px',
                  backgroundColor: 'var(--bg-secondary)',
                  borderRadius: '4px',
                  maxHeight: '70px',
                  overflowY: 'auto',
                  border: '1px solid var(--border-color)',
                }}
              >
                {supplementTargetBlock.text_content}
              </div>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
              <div>
                <label style={{ fontSize: 'var(--font-size-xs)', fontWeight: 500, color: 'var(--text-secondary)' }}>
                  条目标题：<span style={{ color: 'red' }}>*</span>
                </label>
                <input
                  type="text"
                  value={supplementTitle}
                  onChange={(e) => setSupplementTitle(e.target.value)}
                  placeholder="例如：紧急停机操作规程与响应时限"
                  style={{
                    width: '100%',
                    marginTop: '4px',
                    border: '1px solid var(--border-color)',
                    borderRadius: '4px',
                    padding: '6px 8px',
                    fontSize: 'var(--font-size-xs)',
                    outline: 'none',
                  }}
                />
              </div>

              <div style={{ display: 'flex', gap: '12px' }}>
                <div style={{ flex: 1 }}>
                  <label style={{ fontSize: 'var(--font-size-xs)', fontWeight: 500, color: 'var(--text-secondary)' }}>
                    主分类：
                  </label>
                  <select
                    value={supplementCategory}
                    onChange={(e) => setSupplementCategory(e.target.value)}
                    style={{
                      width: '100%',
                      marginTop: '4px',
                      border: '1px solid var(--border-color)',
                      borderRadius: '4px',
                      padding: '6px 8px',
                      fontSize: 'var(--font-size-xs)',
                    }}
                  >
                    <option value="">待分类</option>
                    <option value="制度与标准">制度与标准</option>
                    <option value="方法与工具">方法与工具</option>
                    <option value="项目案例">项目案例</option>
                    <option value="指标数据">指标数据</option>
                    <option value="专家经验">专家经验</option>
                  </select>
                </div>
                <div style={{ flex: 1 }}>
                  <label style={{ fontSize: 'var(--font-size-xs)', fontWeight: 500, color: 'var(--text-secondary)' }}>
                    原子类型：
                  </label>
                  <select
                    value={supplementAtomType}
                    onChange={(e) => setSupplementAtomType(e.target.value)}
                    style={{
                      width: '100%',
                      marginTop: '4px',
                      border: '1px solid var(--border-color)',
                      borderRadius: '4px',
                      padding: '6px 8px',
                      fontSize: 'var(--font-size-xs)',
                      outline: 'none',
                    }}
                  >
                    <option value="规则">规则</option>
                    <option value="判断">判断</option>
                    <option value="方法">方法</option>
                    <option value="案例">案例</option>
                    <option value="指标">指标</option>
                    <option value="经验">经验</option>
                  </select>
                </div>

                <div style={{ flex: 1 }}>
                  <label style={{ fontSize: 'var(--font-size-xs)', fontWeight: 500, color: 'var(--text-secondary)' }}>
                    业务重要程度：
                  </label>
                  <select
                    value={supplementImportance}
                    onChange={(e) => setSupplementImportance(e.target.value as any)}
                    style={{
                      width: '100%',
                      marginTop: '4px',
                      border: '1px solid var(--border-color)',
                      borderRadius: '4px',
                      padding: '6px 8px',
                      fontSize: 'var(--font-size-xs)',
                      outline: 'none',
                    }}
                  >
                    <option value="critical">⚠️ 重要操作 (critical) - 安全/应急/关键参数</option>
                    <option value="normal">常规流程 (normal)</option>
                    <option value="informational">参考信息 (informational)</option>
                  </select>
                </div>
              </div>

              <div>
                <label style={{ fontSize: 'var(--font-size-xs)', fontWeight: 500, color: 'var(--text-secondary)' }}>
                  规范陈述 (Statement)：
                </label>
                <textarea
                  value={supplementStatement}
                  onChange={(e) => setSupplementStatement(e.target.value)}
                  rows={2}
                  style={{
                    width: '100%',
                    marginTop: '4px',
                    border: '1px solid var(--border-color)',
                    borderRadius: '4px',
                    padding: '6px 8px',
                    fontSize: 'var(--font-size-xs)',
                    outline: 'none',
                  }}
                  placeholder="抽取提炼出的标准陈述"
                />
              </div>

              <div>
                <label style={{ fontSize: 'var(--font-size-xs)', fontWeight: 500, color: 'var(--text-secondary)' }}>
                  知识正文详情 (Content)：
                </label>
                <textarea
                  value={supplementContent}
                  onChange={(e) => setSupplementContent(e.target.value)}
                  rows={3}
                  style={{
                    width: '100%',
                    marginTop: '4px',
                    border: '1px solid var(--border-color)',
                    borderRadius: '4px',
                    padding: '6px 8px',
                    fontSize: 'var(--font-size-xs)',
                    outline: 'none',
                  }}
                  placeholder="完整的操作说明或规范正文"
                />
              </div>
            </div>

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px', marginTop: '6px' }}>
              <button className="btn-secondary" onClick={() => setSupplementTargetBlock(null)} disabled={supplementing}>
                取消
              </button>
              <button className="btn-primary" onClick={handleConfirmSupplement} disabled={supplementing}>
                {supplementing ? '补充中...' : '提交补充知识'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 模态弹窗 4：调整业务重要程度弹窗 */}
      {adjustingItemId && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            backgroundColor: 'rgba(18, 26, 22, 0.42)',
            zIndex: 1000,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <div
            style={{
              width: '460px',
              backgroundColor: '#FFFFFF',
              borderRadius: 'var(--radius-md)',
              boxShadow: 'var(--shadow-lg)',
              padding: '20px',
              display: 'flex',
              flexDirection: 'column',
              gap: '14px',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <span style={{ fontSize: 'var(--font-size-md)', fontWeight: 600, color: 'var(--text-primary)' }}>
                调整业务重要程度
              </span>
              <button
                type="button"
                onClick={() => setAdjustingItemId(null)}
                style={{ border: 'none', background: 'transparent', cursor: 'pointer' }}
              >
                <X size={16} />
              </button>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
              <label style={{ fontSize: 'var(--font-size-xs)', fontWeight: 500, color: 'var(--text-secondary)' }}>
                选择重要程度等级：
              </label>
              <select
                value={adjustingLevel}
                onChange={(e) => setAdjustingLevel(e.target.value as any)}
                style={{
                  border: '1px solid var(--border-color)',
                  borderRadius: '4px',
                  padding: '6px 8px',
                  fontSize: 'var(--font-size-xs)',
                  outline: 'none',
                }}
              >
                <option value="critical">⚠️ 重要操作 (critical) - 涉及安全、应急、火警或重大事故规程</option>
                <option value="normal">常规业务 (normal) - 标准日常操作流程与规范</option>
                <option value="informational">参考信息 (informational) - 概念介绍、辅助背景说明</option>
              </select>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
              <label style={{ fontSize: 'var(--font-size-xs)', fontWeight: 500, color: 'var(--text-secondary)' }}>
                调整依据 / 理由：
              </label>
              <textarea
                value={adjustingRationale}
                onChange={(e) => setAdjustingRationale(e.target.value)}
                rows={3}
                style={{
                  border: '1px solid var(--border-color)',
                  borderRadius: '4px',
                  padding: '8px',
                  fontSize: 'var(--font-size-xs)',
                  outline: 'none',
                }}
                placeholder="说明调整为该等级的业务原因"
              />
            </div>

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px', marginTop: '6px' }}>
              <button className="btn-secondary" onClick={() => setAdjustingItemId(null)}>
                取消
              </button>
              <button
                className="btn-primary"
                onClick={() => handleSaveImportance(adjustingItemId)}
                disabled={loading || !adjustingRationale.trim()}
              >
                保存调整
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 应用内确认弹窗 */}
      <AppConfirmDialog
        isOpen={confirmDialogState.isOpen}
        title={confirmDialogState.title}
        variant={confirmDialogState.variant}
        confirmText={confirmDialogState.confirmText}
        cancelText={confirmDialogState.cancelText}
        targetName={confirmDialogState.targetName}
        description={confirmDialogState.description}
        impactDescription={confirmDialogState.impactDescription}
        loading={confirmDialogState.loading}
        initialFocus={confirmDialogState.initialFocus}
        onConfirm={confirmDialogState.onConfirm}
        onCancel={() => setConfirmDialogState((prev) => ({ ...prev, isOpen: false }))}
      />
    </div>
  );
};

// 知识卡片子组件
interface KnowledgeItemCardProps {
  item: ChapterKnowledgeItem;
  isCriticalZone: boolean;
  onLocateBlock: (blockId: string) => void;
  onProofread: () => void;
  onQuickConfirm: () => void;
  onAdjustImportance: () => void;
}

const KnowledgeItemCard: React.FC<KnowledgeItemCardProps> = ({
  item,
  isCriticalZone,
  onLocateBlock,
  onProofread,
  onQuickConfirm,
  onAdjustImportance,
}) => {
  const isCritical = item.business_importance === 'critical';
  const isConfirmed = item.review_status === 'confirmed';

  return (
    <div
      data-testid={`chapter-item-${item.item_id}`}
      style={{
        backgroundColor: '#FFFFFF',
        borderRadius: 'var(--radius-sm)',
        border: isCriticalZone
          ? '1px solid var(--danger-border)'
          : '1px solid var(--border-color)',
        boxShadow: 'var(--shadow-sm)',
        padding: '14px',
        display: 'flex',
        flexDirection: 'column',
        gap: '10px',
      }}
    >
      {/* 头部：标题、分类、重要程度、状态 */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '10px' }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap' }}>
            <span style={{ fontSize: 'var(--font-size-sm)', fontWeight: 600, color: 'var(--text-primary)' }}>
              {item.title}
            </span>

            {/* 重要程度药丸 */}
            <span
              onClick={onAdjustImportance}
              style={{
                fontSize: '12px',
                padding: '1px 6px',
                borderRadius: '3px',
                fontWeight: 500,
                cursor: 'pointer',
                backgroundColor:
                  item.business_importance === 'critical'
                    ? 'var(--danger-bg)'
                    : item.business_importance === 'informational'
                    ? 'var(--bg-sunken)'
                    : 'var(--info-bg)',
                color:
                  item.business_importance === 'critical'
                    ? 'var(--danger-text)'
                    : item.business_importance === 'informational'
                    ? 'var(--text-secondary)'
                    : 'var(--info-text)',
                border:
                  item.business_importance === 'critical'
                    ? '1px solid var(--danger-text)'
                    : '1px solid transparent',
              }}
              title="点击调整重要程度与依据"
            >
              {item.business_importance === 'critical' && '⚠️ 重要操作'}
              {item.business_importance === 'normal' && '常规'}
              {item.business_importance === 'informational' && '参考信息'}
            </span>

            {item.primary_category && (
              <span
                style={{
                  fontSize: '12px',
                  padding: '1px 6px',
                  borderRadius: '3px',
                  backgroundColor: 'var(--bg-secondary)',
                  color: 'var(--text-secondary)',
                }}
              >
                {item.primary_category}
              </span>
            )}
            <span
              style={{
                fontSize: '12px',
                padding: '1px 6px',
                borderRadius: '3px',
                backgroundColor: 'var(--bg-tertiary)',
                color: 'var(--text-muted)',
              }}
            >
              {item.atom_type}
            </span>
          </div>

          {/* 重要性说明 */}
          {item.importance_rationale && (
            <div style={{ fontSize: '12px', color: isCritical ? 'var(--danger-text)' : 'var(--text-muted)', marginTop: '3px' }}>
              依据：{item.importance_rationale}
            </div>
          )}
        </div>

        {/* 状态徽标 */}
        <div>
          <span
            style={{
              fontSize: '12px',
              padding: '2px 8px',
              borderRadius: '4px',
              fontWeight: 500,
              backgroundColor: isConfirmed ? 'var(--success-bg)' : 'var(--info-bg)',
              color: isConfirmed ? 'var(--brand-accent)' : 'var(--info-text)',
            }}
          >
            {isConfirmed ? '已确认' : '待核对'}
          </span>
        </div>
      </div>

      {/* 疑点与阻断提示 */}
      {item.issues_summary && (item.issues_summary.deterministic_errors.length > 0 || item.issues_summary.model_doubts.length > 0) && (
        <div style={{ backgroundColor: 'var(--danger-bg)', padding: '8px 10px', borderRadius: '4px', border: '1px solid var(--danger-border)', display: 'flex', flexDirection: 'column', gap: '4px' }}>
          {item.issues_summary.deterministic_errors.map((err, idx) => (
            <div key={idx} style={{ fontSize: '12px', color: 'var(--danger-text)', display: 'flex', alignItems: 'center', gap: '4px' }}>
              <AlertCircle size={12} />
              <span>[确定性错误] {err.message}</span>
            </div>
          ))}
          {item.issues_summary.model_doubts.map((dbt, idx) => (
            <div key={idx} style={{ fontSize: '12px', color: 'var(--warning-text)', display: 'flex', alignItems: 'center', gap: '4px' }}>
              <HelpCircle size={12} />
              <span>[模型疑点待复核] {dbt.message}</span>
            </div>
          ))}
        </div>
      )}

      {/* 规则冲突检测状态明确声明 */}
      {item.issues_summary?.conflict_check_status && (
        <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
          规则冲突状态：{item.issues_summary.conflict_check_status}
        </div>
      )}

      {/* 与详细校对共用的可读结果；章节只有段落级关联，不伪装字段证据。 */}
      <StructuredReview item={item} sourcePrecision="paragraph"
        onLocate={() => { if (item.associated_block_ids?.[0]) onLocateBlock(item.associated_block_ids[0]); }}
        onEdit={onProofread} />

      {/* 关联来源段落锚点与联动高亮 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap', paddingTop: '6px', borderTop: '1px solid var(--border-color)' }}>
        <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>定位原文：</span>
        {item.associated_block_ids && item.associated_block_ids.length > 0 ? (
          item.associated_block_ids.map((bid) => (
            <button
              key={bid}
              type="button"
              onClick={() => onLocateBlock(bid)}
              style={{
                fontSize: '12px',
                border: '1px solid var(--info-border)',
                backgroundColor: 'var(--info-bg)',
                color: 'var(--info-text)',
                padding: '1px 6px',
                borderRadius: '3px',
                cursor: 'pointer',
              }}
              title="点击在左侧原文流水中高亮并滚动到该段落"
            >
              定位段落
            </button>
          ))
        ) : (
          <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>暂无关联段落</span>
        )}

        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: '8px' }}>
          <button
            type="button"
            className="btn-secondary"
            onClick={onProofread}
            style={{ height: '26px', fontSize: 'var(--font-size-xs)', padding: '0 8px' }}
          >
            详细校对
          </button>

          {!isConfirmed && (
            <button
              type="button"
              className="btn-primary"
              onClick={onQuickConfirm}
              disabled={!item.can_confirm}
              title={!item.can_confirm ? item.confirmation_blockers.join('；') : undefined}
              style={{
                height: '26px',
                fontSize: 'var(--font-size-xs)',
                padding: '0 8px',
                backgroundColor: isCritical ? 'var(--danger-text)' : 'var(--brand-accent)',
                opacity: item.can_confirm ? 1 : 0.55,
              }}
            >
              {isCritical ? '重点审核确认' : '确认通过'}
            </button>
          )}
        </div>
      </div>
    </div>
  );
};

// 批量确认二次确认弹窗
interface BatchConfirmModalProps {
  chapter: ChapterGroup;
  isSubmitting: boolean;
  resultMessage: string | null;
  onConfirm: () => void;
  onClose: () => void;
}

const BatchConfirmModal: React.FC<BatchConfirmModalProps> = ({
  chapter,
  isSubmitting,
  resultMessage,
  onConfirm,
  onClose,
}) => {
  const canBatchItems = chapter.items.filter(
    (item) => item.can_batch_confirm && item.review_status === 'pending_review'
  );
  const skippedItems = chapter.items.filter(
    (item) => !item.can_batch_confirm && item.review_status === 'pending_review'
  );

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        backgroundColor: 'rgba(18, 26, 22, 0.42)',
        zIndex: 1000,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      <div
        style={{
          width: '620px',
          maxHeight: '85vh',
          backgroundColor: '#FFFFFF',
          borderRadius: 'var(--radius-md)',
          boxShadow: 'var(--shadow-lg)',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
        }}
      >
        <div
          style={{
            padding: '16px 20px',
            borderBottom: '1px solid var(--border-color)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            backgroundColor: 'var(--bg-secondary)',
          }}
        >
          <div>
            <h3 style={{ fontSize: 'var(--font-size-md)', fontWeight: 600, color: 'var(--text-primary)' }}>
              批量确认核对清单（防误操作保护）
            </h3>
            <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '2px' }}>
              所属章节：{chapter.chapter_name}
            </div>
          </div>
          <button type="button" onClick={onClose} style={{ border: 'none', background: 'transparent', cursor: 'pointer' }}>
            <X size={16} />
          </button>
        </div>

        <div style={{ padding: '16px 20px', flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '14px' }}>
          {resultMessage ? (
            <div style={{ padding: '16px', backgroundColor: 'var(--success-bg)', border: '1px solid var(--success-border)', borderRadius: '6px', color: 'var(--brand-accent)', textAlign: 'center', fontSize: 'var(--font-size-sm)' }}>
              {resultMessage}
            </div>
          ) : (
            <>
              {/* 即将确认的普通条目清单 */}
              <div>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px' }}>
                  <span style={{ fontSize: 'var(--font-size-sm)', fontWeight: 600, color: 'var(--brand-accent)' }}>
                    即将确认的普通条目 ({canBatchItems.length} 条)
                  </span>
                  <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                    经核验无阻断性错误与模型疑点
                  </span>
                </div>
                <div style={{ border: '1px solid var(--success-border)', borderRadius: '4px', backgroundColor: 'var(--success-bg)', maxHeight: '160px', overflowY: 'auto' }}>
                  {canBatchItems.map((item) => (
                    <div
                      key={item.item_id}
                      style={{
                        padding: '8px 12px',
                        borderBottom: '1px solid var(--border-color)',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        fontSize: '12px',
                      }}
                    >
                      <span style={{ color: 'var(--text-primary)', fontWeight: 500 }}>{item.title}</span>
                      <span style={{ color: 'var(--text-muted)', fontSize: '12px' }}>{item.primary_category || '待分类'}</span>
                    </div>
                  ))}
                </div>
              </div>

              {/* 跳过不确认的条目清单 */}
              <div>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px' }}>
                  <span style={{ fontSize: 'var(--font-size-sm)', fontWeight: 600, color: 'var(--danger-text)' }}>
                    安全跳过的重点/阻断条目 ({skippedItems.length} 条)
                  </span>
                  <span style={{ fontSize: '12px', color: 'var(--danger-text)' }}>
                    刚性拦截，必须人工逐条逐项核实
                  </span>
                </div>
                <div style={{ border: '1px solid var(--danger-border)', borderRadius: '4px', backgroundColor: 'var(--danger-bg)', maxHeight: '160px', overflowY: 'auto' }}>
                  {skippedItems.length === 0 ? (
                    <div style={{ padding: '10px 12px', fontSize: '12px', color: 'var(--text-muted)' }}>无跳过条目</div>
                  ) : (
                    skippedItems.map((item) => (
                      <div
                        key={item.item_id}
                        style={{
                          padding: '8px 12px',
                          borderBottom: '1px solid var(--danger-bg)',
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'space-between',
                          fontSize: '12px',
                        }}
                      >
                        <div>
                          <div style={{ color: 'var(--danger-text)', fontWeight: 500 }}>{item.title}</div>
                          <div style={{ color: 'var(--danger-text)', fontSize: '12px' }}>
                            原因：{item.batch_block_reason || (item.business_importance === 'critical' ? '重要操作规程/安全规范，禁止批量通过' : '存在未复核疑点')}
                          </div>
                        </div>
                        <span style={{ padding: '1px 6px', borderRadius: '3px', backgroundColor: 'var(--danger-bg)', color: 'var(--danger-text)', fontSize: '12px' }}>
                          保留逐条核验
                        </span>
                      </div>
                    ))
                  )}
                </div>
              </div>

              <div style={{ fontSize: '12px', color: 'var(--text-muted)', lineHeight: 1.5 }}>
                提示：批量操作将调用后端对各条目的版本令牌进行并发冲突校验。确认后将写入不可篡改的管理员操作审计日志。
              </div>
            </>
          )}
        </div>

        <div
          style={{
            padding: '12px 20px',
            borderTop: '1px solid var(--border-color)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'flex-end',
            gap: '10px',
            backgroundColor: 'var(--bg-secondary)',
          }}
        >
          <button className="btn-secondary" onClick={onClose} disabled={isSubmitting}>
            取消
          </button>
          {!resultMessage && (
            <button
              className="btn-primary"
              onClick={onConfirm}
              disabled={isSubmitting || canBatchItems.length === 0}
              style={{ backgroundColor: 'var(--brand-accent)' }}
            >
              {isSubmitting ? '正在校验并批量确认...' : `确认批量通过 (${canBatchItems.length}条)`}
            </button>
          )}
        </div>
      </div>
    </div>
  );
};
