import React, { useState, useEffect, useMemo } from 'react';
import {
  X,
  CheckCircle2,
  AlertTriangle,
  AlertCircle,
  Info,
  Clock,
  BookOpen,
  FileText,
  Trash2,
  Save,
  Tag,
  Shield,
  Sparkles,
  ChevronLeft,
  ChevronRight,
  Edit3,
  Plus,
  Ban,
  Check,
  RotateCcw,
} from 'lucide-react';
import {
  KnowledgeItemDetail,
  PrimaryCategory,
  AtomType,
  MetricDefinition,
  CaseDetails,
} from '../../../types';
import { api } from '../../../services/api';
import { UnsavedChangesModal } from './UnsavedChangesModal';
import { formatVersionLabel, formatAnchor, formatFieldName } from '../../../utils/formatters';

export interface ProofreadingModalProps {
  itemId: string | null;
  itemList?: string[];
  onSelectNext?: (nextId: string) => void;
  onClose: () => void;
  onSaved: () => void;
  onDeleted: () => void;
}

const CATEGORY_STYLES: Record<string, { bg: string; text: string; border: string }> = {
  制度与标准: { bg: '#EBF4F0', text: '#285C49', border: '#C2DBD0' },
  方法与工具: { bg: '#EBF8FF', text: '#2B6CB0', border: '#BEE3F8' },
  项目案例: { bg: '#FAF5FF', text: '#6B46C1', border: '#E9D8FD' },
  指标数据: { bg: '#FFFAF0', text: '#C05621', border: '#FEEBC8' },
  专家经验: { bg: '#F0FFF4', text: '#22543D', border: '#C6F6D5' },
};

const EXCLUSION_REASONS = [
  '非实质业务知识，属于无关泛文本',
  '排版分割线、纯符号或格式噪音',
  '与已有收录的知识内容重复',
  '其他原因',
];

interface MergedEvidence {
  source_block_id: string;
  block_index?: number;
  block_type?: string;
  heading_path?: string | null;
  page_number?: number | null;
  paragraph_anchor?: string | null;
  text_content: string;
  usages: Array<{ field_name: string; label: string; excerpt: string }>;
}

const FIELD_LABEL_MAP: Record<string, string> = {
  statement: '主要结论',
  title: '知识标题',
  subject: '责任主体',
  conditions: '适用条件',
  actions: '处理动作',
  exceptions: '特殊情况/例外',
  metric_definition: '指标要求',
  case_details: '案例信息',
};

export const ProofreadingModal: React.FC<ProofreadingModalProps> = ({
  itemId,
  itemList = [],
  onSelectNext,
  onClose,
  onSaved,
  onDeleted,
}) => {
  const [detail, setDetail] = useState<KnowledgeItemDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 全量编辑模式与就地微调
  const [fullEditMode, setFullEditMode] = useState(false);
  const [editingSection, setEditingSection] = useState<string | null>(null);

  // 表单受控数据
  const [title, setTitle] = useState('');
  const [statement, setStatement] = useState('');
  const [content, setContent] = useState('');
  const [primaryCategory, setPrimaryCategory] = useState<PrimaryCategory | ''>('');
  const [atomType, setAtomType] = useState<AtomType>('规则');
  const [subject, setSubject] = useState('');
  const [conditions, setConditions] = useState<string[]>([]);
  const [actions, setActions] = useState<string[]>([]);
  const [exceptions, setExceptions] = useState<string[]>([]);
  const [metricDef, setMetricDef] = useState<MetricDefinition | null>(null);
  const [caseDetails, setCaseDetails] = useState<CaseDetails | null>(null);
  const [customerTypes, setCustomerTypes] = useState<string[]>([]);
  const [businessScenes, setBusinessScenes] = useState<string[]>([]);
  const [problemTags, setProblemTags] = useState<string[]>([]);
  const [accessScope, setAccessScope] = useState<'admin_only' | 'org_internal'>('admin_only');
  const [validFrom, setValidFrom] = useState('');
  const [validUntil, setValidUntil] = useState('');
  const [qualityFlags, setQualityFlags] = useState<string[]>([]);

  // 脏状态追踪
  const [isDirty, setIsDirty] = useState(false);
  const [showUnsavedPrompt, setShowUnsavedPrompt] = useState(false);
  const [pendingNextId, setPendingNextId] = useState<string | null>(null);

  // 排除（不收录）弹窗状态
  const [showExcludeModal, setShowExcludeModal] = useState(false);
  const [selectedExcludeReason, setSelectedExcludeReason] = useState(EXCLUSION_REASONS[0]);
  const [customExcludeReason, setCustomExcludeReason] = useState('');

  // 标签输入与收拢状态
  const [tagInput, setTagInput] = useState('');
  const [tagType, setTagType] = useState<'customer' | 'scene' | 'problem'>('scene');
  const [isTagsEditing, setIsTagsEditing] = useState(false);
  const [availableTags, setAvailableTags] = useState<{
    customer_types: string[];
    business_scenes: string[];
    problem_tags: string[];
  } | null>(null);
  const [togglingLifecycle, setTogglingLifecycle] = useState(false);

  // 原文证据按 source_block_id 分组聚合
  const mergedEvidence = useMemo(() => {
    if (!detail?.evidence) return [];
    const map = new Map<string, MergedEvidence>();
    for (const ev of detail.evidence) {
      const key = ev.source_block_id || `block_${ev.block_index ?? Math.random()}`;
      if (!map.has(key)) {
        map.set(key, {
          source_block_id: ev.source_block_id,
          block_index: ev.block_index,
          block_type: ev.block_type,
          heading_path: ev.heading_path,
          page_number: ev.page_number,
          paragraph_anchor: ev.paragraph_anchor,
          text_content: ev.text_content || '',
          usages: [],
        });
      }
      const item = map.get(key)!;
      const label = FIELD_LABEL_MAP[ev.field_name] || formatFieldName(ev.field_name);
      if (!item.usages.some((u) => u.field_name === ev.field_name && u.excerpt === ev.excerpt)) {
        item.usages.push({
          field_name: ev.field_name,
          label,
          excerpt: ev.excerpt,
        });
      }
    }
    return Array.from(map.values());
  }, [detail?.evidence]);

  // 当前条目在列表中的索引
  const currentIndex = useMemo(() => {
    if (!itemId || !itemList || itemList.length === 0) return -1;
    return itemList.indexOf(itemId);
  }, [itemId, itemList]);

  const hasPrev = currentIndex > 0;
  const hasNext = currentIndex >= 0 && currentIndex < itemList.length - 1;

  useEffect(() => {
    if (!itemId) return;
    let isMounted = true;
    setLoading(true);
    setError(null);
    setEditingSection(null);

    api
      .getKnowledgeItemDetail(itemId)
      .then((data) => {
        if (!isMounted) return;
        setDetail(data);
        const av = data.active_version;
        setTitle(av.title || '');
        setStatement(av.statement || '');
        setContent(av.content || av.statement || '');
        setPrimaryCategory(av.primary_category || '');
        setAtomType(av.atom_type || '规则');
        setSubject(av.subject || '物业责任主体');
        setConditions(av.conditions || []);
        setActions(av.actions || []);
        setExceptions(av.exceptions || []);
        setMetricDef(av.metric_definition || null);
        setCaseDetails(av.case_details || null);
        setCustomerTypes(av.customer_types || []);
        setBusinessScenes(av.business_scenes || []);
        setProblemTags(av.problem_tags || []);
        setAccessScope(data.access_scope || 'admin_only');
        setValidFrom(av.valid_from || '');
        setValidUntil(av.valid_until || '');
        setQualityFlags(av.quality_flags || []);
        setIsDirty(false);
      })
      .catch((err: any) => {
        if (!isMounted) return;
        setError(err.message || '加载知识详情失败');
      })
      .finally(() => {
        if (isMounted) setLoading(false);
      });

    return () => {
      isMounted = false;
    };
  }, [itemId]);

  const markDirty = () => {
    if (!isDirty) setIsDirty(true);
  };

  // 质检信息三层分类（阻断启用、建议确认、原文未提及）
  const qualityCheck = useMemo(() => {
    const blocking: string[] = [];
    const suggestions: string[] = [];
    const neutralInfos: string[] = [];

    if (!title.trim()) {
      blocking.push('知识条目标题不能为空');
    }
    if (!statement.trim() || statement.trim() === '---' || statement.trim().length < 4) {
      blocking.push('核心陈述不能为空且须具备实质业务内容');
    }
    if (!primaryCategory) {
      blocking.push('主分类仍为「待分类」，确认前必须明确指定五类主分类之一');
    }

    // 分析现有 quality_flags
    for (const flag of qualityFlags) {
      if (flag.includes('管理员操作')) continue;

      if (
        flag.includes('缺少') ||
        flag.includes('无效') ||
        flag.includes('纯符号') ||
        flag.includes('横线') ||
        flag.includes('无有效原文证据') ||
        flag.includes('未指定五类主分类')
      ) {
        if (!blocking.includes(flag)) blocking.push(flag);
      } else if (
        flag.includes('未提供') ||
        flag.includes('未提及') ||
        flag.includes('未明确') ||
        flag.includes('未包含') ||
        flag.includes('允许留空')
      ) {
        if (!neutralInfos.includes(flag)) neutralInfos.push(flag);
      } else {
        if (!suggestions.includes(flag)) suggestions.push(flag);
      }
    }

    return {
      blocking,
      suggestions,
      neutralInfos,
      isBlocked: blocking.length > 0,
    };
  }, [title, statement, primaryCategory, qualityFlags]);

  const buildPayload = () => {
    if (!detail) return {};
    return {
      revision_token: detail.active_version.revision_token,
      title,
      statement,
      content: content || statement,
      primary_category: primaryCategory || null,
      atom_type: atomType,
      subject,
      conditions,
      actions,
      exceptions,
      metric_definition: metricDef,
      case_details: caseDetails,
      customer_types: customerTypes,
      business_scenes: businessScenes,
      problem_tags: problemTags,
      access_scope: accessScope,
      valid_from: validFrom || null,
      valid_until: validUntil || null,
    };
  };

  // 保存草稿
  const handleSaveDraft = async () => {
    if (!detail) return;
    try {
      setSaving(true);
      const payload = buildPayload();
      const res = await api.saveKnowledgeDraft(detail.id, payload);
      setDetail((prev) => {
        if (!prev) return null;
        return {
          ...prev,
          active_version: {
            ...prev.active_version,
            revision_token: res.revision_token,
            quality_flags: res.quality_flags,
          },
        };
      });
      setQualityFlags(res.quality_flags);
      setIsDirty(false);
      setShowUnsavedPrompt(false);
      onSaved();

      if (pendingNextId && onSelectNext) {
        const next = pendingNextId;
        setPendingNextId(null);
        onSelectNext(next);
      }
    } catch (err: any) {
      alert(`保存草稿失败: ${err.message}`);
    } finally {
      setSaving(false);
    }
  };

  // 确认知识并启用
  const handleConfirm = async () => {
    if (!detail) return;

    if (qualityCheck.isBlocked) {
      alert(`无法确认启用：\n${qualityCheck.blocking.join('\n')}`);
      return;
    }

    try {
      setConfirming(true);
      if (isDirty) {
        const draftRes = await api.saveKnowledgeDraft(detail.id, buildPayload());
        detail.active_version.revision_token = draftRes.revision_token;
      }

      const confirmRes = await api.confirmKnowledgeItem(detail.id, {
        revision_token: detail.active_version.revision_token,
      });

      setDetail((prev) => {
        if (!prev) return null;
        return {
          ...prev,
          lifecycle_status: 'active',
          is_draft_version: false,
          active_version: {
            ...prev.active_version,
            review_status: 'confirmed',
            index_status: 'ready',
            revision_token: confirmRes.revision_token,
          },
        };
      });
      setIsDirty(false);
      onSaved();

      // 如果有下一条，自动跳入下一条连续核对
      if (hasNext && onSelectNext) {
        onSelectNext(itemList[currentIndex + 1]);
      }
    } catch (err: any) {
      alert(`确认失败: ${err.message}`);
    } finally {
      setConfirming(false);
    }
  };

  // 停用与恢复启用
  const handleToggleLifecycle = async () => {
    if (!detail) return;
    const targetStatus = detail.lifecycle_status === 'disabled' ? 'active' : 'disabled';
    const actionText = targetStatus === 'disabled' ? '停用' : '恢复启用';
    if (
      !window.confirm(
        `确认${actionText}该知识条目？${
          targetStatus === 'disabled'
            ? '停用后将退出正式检索，不再对外提供服务。'
            : '恢复后将立即重新恢复正式检索服务。'
        }`
      )
    ) {
      return;
    }
    try {
      setTogglingLifecycle(true);
      await api.updateKnowledgeLifecycle(detail.id, targetStatus);
      setDetail((prev) => (prev ? { ...prev, lifecycle_status: targetStatus } : null));
      onSaved();
    } catch (err: any) {
      alert(`${actionText}失败: ${err.message}`);
    } finally {
      setTogglingLifecycle(false);
    }
  };

  // 排除（不收录）操作
  const handleConfirmExclude = async () => {
    if (!detail) return;
    const reason =
      selectedExcludeReason === '其他原因'
        ? customExcludeReason.trim() || '其他原因'
        : selectedExcludeReason;

    try {
      setDeleting(true);
      await api.deleteKnowledgeItem(detail.id, 'exclude', reason);
      setShowExcludeModal(false);
      onDeleted();

      if (hasNext && onSelectNext) {
        onSelectNext(itemList[currentIndex + 1]);
      } else if (hasPrev && onSelectNext) {
        onSelectNext(itemList[currentIndex - 1]);
      } else {
        onClose();
      }
    } catch (err: any) {
      alert(`排除操作失败: ${err.message}`);
    } finally {
      setDeleting(false);
    }
  };

  // 逻辑彻底删除
  const handleDelete = async () => {
    if (!detail) return;
    if (
      !window.confirm(
        `确认彻底删除知识条目「${detail.active_version.title}」？删除后将撤销检索与日常访问资格。`
      )
    ) {
      return;
    }
    try {
      setDeleting(true);
      await api.deleteKnowledgeItem(detail.id, 'delete');
      onDeleted();
      if (hasNext && onSelectNext) {
        onSelectNext(itemList[currentIndex + 1]);
      } else {
        onClose();
      }
    } catch (err: any) {
      alert(`删除失败: ${err.message}`);
    } finally {
      setDeleting(false);
    }
  };

  // 切换条目处理
  const navigateTo = (nextId: string) => {
    if (!onSelectNext) return;
    if (isDirty) {
      setPendingNextId(nextId);
      setShowUnsavedPrompt(true);
    } else {
      onSelectNext(nextId);
    }
  };

  const handleRequestClose = () => {
    if (isDirty) {
      setPendingNextId(null);
      setShowUnsavedPrompt(true);
    } else {
      onClose();
    }
  };

  // 暂不处理（跳过此条）
  const handleSkip = () => {
    if (hasNext && onSelectNext) {
      if (isDirty) {
        if (window.confirm('当前条目已修改，确定不保存直接跳过吗？')) {
          setIsDirty(false);
          onSelectNext(itemList[currentIndex + 1]);
        }
      } else {
        onSelectNext(itemList[currentIndex + 1]);
      }
    } else {
      onClose();
    }
  };

  // 添加标签
  const handleAddTag = () => {
    const val = tagInput.trim();
    if (!val) return;
    if (tagType === 'customer' && !customerTypes.includes(val)) {
      setCustomerTypes([...customerTypes, val]);
      markDirty();
    } else if (tagType === 'scene' && !businessScenes.includes(val)) {
      setBusinessScenes([...businessScenes, val]);
      markDirty();
    } else if (tagType === 'problem' && !problemTags.includes(val)) {
      setProblemTags([...problemTags, val]);
      markDirty();
    }
    setTagInput('');
  };

  if (!itemId) return null;

  return (
    <>
      <div
        style={{
          position: 'fixed',
          inset: 0,
          backgroundColor: 'rgba(0, 0, 0, 0.55)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          zIndex: 1000,
          padding: '16px',
        }}
      >
        <div
          style={{
            width: '1360px',
            maxWidth: '96vw',
            height: '890px',
            maxHeight: '94vh',
            backgroundColor: '#FFFFFF',
            borderRadius: 'var(--radius-lg)',
            boxShadow: '0 16px 48px rgba(0, 0, 0, 0.22)',
            display: 'flex',
            flexDirection: 'column',
            overflow: 'hidden',
          }}
        >
          {/* 顶栏：标题、说明与状态表达 */}
          <div
            style={{
              padding: '14px 24px',
              borderBottom: '1px solid var(--border-color)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              backgroundColor: 'var(--bg-secondary)',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
              <div
                style={{
                  width: '34px',
                  height: '34px',
                  borderRadius: 'var(--radius-sm)',
                  backgroundColor: 'var(--brand-accent-light)',
                  color: 'var(--brand-accent)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
              >
                <BookOpen size={18} />
              </div>
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <span style={{ fontSize: '16px', fontWeight: 700, color: 'var(--text-primary)' }}>
                    核对知识
                  </span>
                  <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                    核对系统整理的内容是否忠于原文，重点检查条件、动作和例外
                  </span>
                </div>
                <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '2px' }}>
                  来源资料：
                  <strong style={{ color: 'var(--text-primary)' }}>
                    {detail?.document_title || '未知资料'}
                  </strong>
                  （{formatVersionLabel(detail?.document_version_label)}）
                </div>
              </div>
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
              {/* 停用状态标识 */}
              {detail?.lifecycle_status === 'disabled' && (
                <span
                  data-testid="disabled-status-badge"
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: '4px',
                    fontSize: '12px',
                    fontWeight: 600,
                    padding: '3px 10px',
                    borderRadius: 'var(--radius-sm)',
                    backgroundColor: '#FEF2F2',
                    color: '#DC2626',
                    border: '1px solid #FECACA',
                  }}
                >
                  <Ban size={13} />
                  <span>已停用</span>
                </span>
              )}

              {/* 草稿 / 审核状态 */}
              {detail?.is_draft_version ? (
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <span
                    data-testid="draft-version-badge"
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: '4px',
                      fontSize: '12px',
                      fontWeight: 600,
                      padding: '3px 10px',
                      borderRadius: 'var(--radius-sm)',
                      backgroundColor: 'var(--warning-bg)',
                      color: 'var(--warning-text)',
                      border: '1px solid #FCD34D',
                    }}
                  >
                    <Clock size={13} />
                    <span>待核对新草稿 (v{detail.active_version.version_number})</span>
                  </span>
                  {detail.serving_version_number && (
                    <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                      （线上服务中：v{detail.serving_version_number}）
                    </span>
                  )}
                </div>
              ) : detail?.active_version.review_status === 'confirmed' ? (
                <span
                  data-testid="confirmed-status-badge"
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: '4px',
                    fontSize: '12px',
                    fontWeight: 600,
                    padding: '3px 10px',
                    borderRadius: 'var(--radius-sm)',
                    backgroundColor: '#ECFDF5',
                    color: '#059669',
                    border: '1px solid #A7F3D0',
                  }}
                >
                  <CheckCircle2 size={13} />
                  <span>已确认启用</span>
                </span>
              ) : (
                <span
                  data-testid="pending-status-badge"
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: '4px',
                    fontSize: '12px',
                    fontWeight: 600,
                    padding: '3px 10px',
                    borderRadius: 'var(--radius-sm)',
                    backgroundColor: 'var(--warning-bg)',
                    color: 'var(--warning-text)',
                    border: '1px solid #FCD34D',
                  }}
                >
                  <Clock size={13} />
                  <span>待核对</span>
                </span>
              )}

              {/* 停用/恢复按钮 */}
              {detail && (detail.active_version.review_status === 'confirmed' || detail.lifecycle_status === 'disabled') && (
                <button
                  type="button"
                  data-testid="toggle-lifecycle-btn"
                  onClick={handleToggleLifecycle}
                  disabled={togglingLifecycle}
                  style={{
                    fontSize: '12px',
                    padding: '4px 10px',
                    borderRadius: 'var(--radius-sm)',
                    backgroundColor: detail.lifecycle_status === 'disabled' ? '#ECFDF5' : '#FEF2F2',
                    color: detail.lifecycle_status === 'disabled' ? '#059669' : '#DC2626',
                    border: `1px solid ${detail.lifecycle_status === 'disabled' ? '#A7F3D0' : '#FECACA'}`,
                    cursor: 'pointer',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '4px',
                  }}
                >
                  {detail.lifecycle_status === 'disabled' ? '恢复启用' : '停用知识'}
                </button>
              )}

              {/* 提炼引擎标识 */}
              <span
                style={{
                  fontSize: '11px',
                  padding: '3px 8px',
                  borderRadius: 'var(--radius-sm)',
                  backgroundColor: 'var(--bg-primary)',
                  color: 'var(--text-secondary)',
                  border: '1px solid var(--border-color)',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '4px',
                }}
              >
                <Sparkles size={11} color="var(--brand-accent)" />
                <span>
                  {detail?.active_version.extraction_context?.provider === 'deepseek-api'
                    ? 'DeepSeek 大模型整理'
                    : '离线规则整理'}
                </span>
              </span>

              {/* 模式切换 */}
              <button
                type="button"
                onClick={() => setFullEditMode(!fullEditMode)}
                style={{
                  fontSize: '12px',
                  padding: '4px 10px',
                  borderRadius: 'var(--radius-sm)',
                  backgroundColor: fullEditMode ? 'var(--brand-accent)' : '#FFFFFF',
                  color: fullEditMode ? '#FFFFFF' : 'var(--text-primary)',
                  border: '1px solid var(--border-color)',
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '4px',
                }}
              >
                <Edit3 size={12} />
                <span>{fullEditMode ? '切换阅读模式' : '编辑全部内容'}</span>
              </button>

              <button
                type="button"
                data-testid="close-modal-btn"
                onClick={handleRequestClose}
                style={{
                  background: 'none',
                  border: 'none',
                  color: 'var(--text-secondary)',
                  cursor: 'pointer',
                  padding: '4px',
                  borderRadius: '4px',
                  display: 'flex',
                  alignItems: 'center',
                }}
              >
                <X size={20} />
              </button>
            </div>
          </div>

          {/* 主体两栏对照区 */}
          {loading ? (
            <div
              style={{
                flex: 1,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                color: 'var(--text-secondary)',
                fontSize: '13px',
              }}
            >
              正在加载知识内容与原文比对数据...
            </div>
          ) : error ? (
            <div
              style={{
                flex: 1,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                color: 'var(--error-text)',
                fontSize: '13px',
              }}
            >
              {error}
            </div>
          ) : (
            <div style={{ flex: 1, display: 'flex', minHeight: 0, overflow: 'hidden' }}>
              {/* 左栏：42% 原文证据对照栏 */}
              <div
                style={{
                  width: '42%',
                  borderRight: '1px solid var(--border-color)',
                  backgroundColor: 'var(--bg-secondary)',
                  display: 'flex',
                  flexDirection: 'column',
                  minHeight: 0,
                }}
              >
                <div
                  style={{
                    padding: '12px 20px',
                    borderBottom: '1px solid var(--border-color)',
                    fontSize: '13px',
                    fontWeight: 600,
                    color: 'var(--text-primary)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                    <FileText size={15} color="var(--brand-accent)" />
                    <span>原文证据对照（共 {mergedEvidence.length} 处段落支撑）</span>
                  </div>
                  <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                    不虚构页码，精确到段落与表格
                  </span>
                </div>

                <div
                  style={{
                    flex: 1,
                    overflowY: 'auto',
                    padding: '16px 20px',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '16px',
                  }}
                >
                  {mergedEvidence && mergedEvidence.length > 0 ? (
                    mergedEvidence.map((ev, idx) => (
                      <div
                        key={ev.source_block_id || idx}
                        style={{
                          backgroundColor: '#FFFFFF',
                          border: '1px solid var(--border-color)',
                          borderRadius: 'var(--radius-md)',
                          padding: '14px',
                          display: 'flex',
                          flexDirection: 'column',
                          gap: '10px',
                          boxShadow: '0 1px 3px rgba(0,0,0,0.02)',
                        }}
                      >
                        <div
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'space-between',
                            fontSize: '11px',
                          }}
                        >
                          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', alignItems: 'center' }}>
                            <span
                              style={{
                                fontWeight: 600,
                                color: 'var(--text-secondary)',
                                backgroundColor: 'var(--bg-secondary)',
                                padding: '2px 8px',
                                borderRadius: 'var(--radius-sm)',
                                border: '1px solid var(--border-color)',
                              }}
                            >
                              段落 {ev.block_index !== undefined ? `#${ev.block_index + 1}` : ''}
                            </span>
                            {ev.usages.map((u, uIdx) => (
                              <span
                                key={uIdx}
                                style={{
                                  fontWeight: 500,
                                  color: 'var(--text-primary)',
                                  backgroundColor: 'var(--bg-secondary)',
                                  padding: '2px 6px',
                                  borderRadius: 'var(--radius-sm)',
                                  border: '1px solid var(--border-color)',
                                  fontSize: '11px',
                                }}
                              >
                                [{u.label}]
                              </span>
                            ))}
                          </div>
                          <span
                            style={{
                              color: 'var(--text-secondary)',
                              backgroundColor: 'var(--bg-secondary)',
                              padding: '2px 8px',
                              borderRadius: 'var(--radius-sm)',
                              fontSize: '11px',
                            }}
                          >
                            {formatAnchor(ev.paragraph_anchor || (ev.page_number ? `p.${ev.page_number}` : ''))}
                          </span>
                        </div>

                        {ev.heading_path && (
                          <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                            章节路径：
                            <strong style={{ color: 'var(--text-primary)' }}>{ev.heading_path}</strong>
                          </div>
                        )}

                        {/* 引用摘录列表（采用中性灰白底，不使用误导性成功绿） */}
                        {ev.usages.filter((u) => u.excerpt && u.excerpt.trim()).length > 0 && (
                          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                            {ev.usages
                              .filter((u) => u.excerpt && u.excerpt.trim())
                              .map((u, uIdx) => (
                                <div
                                  key={uIdx}
                                  style={{
                                    fontSize: '12px',
                                    padding: '6px 10px',
                                    backgroundColor: 'var(--bg-secondary)',
                                    color: 'var(--text-primary)',
                                    borderRadius: 'var(--radius-sm)',
                                    border: '1px solid var(--border-color)',
                                    lineHeight: 1.5,
                                  }}
                                >
                                  <span style={{ fontWeight: 600, color: 'var(--text-secondary)' }}>
                                    「{u.label}」引用摘录：
                                  </span>
                                  <span>{u.excerpt}</span>
                                </div>
                              ))}
                          </div>
                        )}

                        {/* 完整原文段落 */}
                        <div
                          style={{
                            fontSize: '12px',
                            color: 'var(--text-primary)',
                            lineHeight: 1.6,
                            backgroundColor: 'var(--bg-secondary)',
                            padding: '10px 12px',
                            borderRadius: 'var(--radius-sm)',
                            whiteSpace: 'pre-wrap',
                            maxHeight: '260px',
                            overflowY: 'auto',
                          }}
                        >
                          {ev.text_content}
                        </div>
                      </div>
                    ))
                  ) : (
                    <div
                      style={{
                        padding: '36px 20px',
                        textAlign: 'center',
                        color: 'var(--text-muted)',
                        fontSize: '13px',
                        backgroundColor: '#FFFFFF',
                        borderRadius: 'var(--radius-md)',
                        border: '1px dashed var(--border-color)',
                      }}
                    >
                      该条目暂无关联的原文段落（可能是手动添加或直接导入）
                    </div>
                  )}

                  <div
                    style={{
                      fontSize: '11px',
                      color: 'var(--text-muted)',
                      backgroundColor: 'var(--bg-primary)',
                      padding: '10px 12px',
                      borderRadius: 'var(--radius-sm)',
                      lineHeight: 1.5,
                      border: '1px dashed var(--border-color)',
                      display: 'flex',
                      alignItems: 'flex-start',
                      gap: '6px',
                    }}
                  >
                    <Info size={14} style={{ flexShrink: 0, marginTop: '2px' }} />
                    <span>
                      注：来源文本匹配仅证明在原文中检索到相应段落或表格，仍需人工核对语义是否完整准确。
                    </span>
                  </div>
                </div>
              </div>

              {/* 右栏：58% 可读知识展示与就地修改 */}
              <div
                style={{
                  width: '58%',
                  display: 'flex',
                  flexDirection: 'column',
                  minHeight: 0,
                  backgroundColor: '#FFFFFF',
                }}
              >
                <div
                  style={{
                    flex: 1,
                    overflowY: 'auto',
                    padding: '20px 24px',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '18px',
                  }}
                >
                  {/* 1. 质检提示卡片（清晰区分三级） */}
                  {/* 1.1 阻断启用问题 */}
                  {qualityCheck.blocking.length > 0 && (
                    <div
                      style={{
                        backgroundColor: '#FEF2F2',
                        border: '1px solid #FCA5A5',
                        borderRadius: 'var(--radius-md)',
                        padding: '12px 16px',
                        display: 'flex',
                        flexDirection: 'column',
                        gap: '6px',
                      }}
                    >
                      <div
                        style={{
                          fontSize: '12px',
                          fontWeight: 700,
                          color: '#DC2626',
                          display: 'flex',
                          alignItems: 'center',
                          gap: '6px',
                        }}
                      >
                        <AlertCircle size={15} />
                        <span>阻断启用问题（需修复后方可确认启用）：</span>
                      </div>
                      <ul
                        style={{
                          margin: 0,
                          paddingLeft: '20px',
                          fontSize: '12px',
                          color: '#B91C1C',
                          lineHeight: 1.6,
                        }}
                      >
                        {qualityCheck.blocking.map((msg, idx) => (
                          <li key={idx}>{msg}</li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {/* 1.2 待核对建议 */}
                  {qualityCheck.suggestions.length > 0 && (
                    <div
                      style={{
                        backgroundColor: 'var(--warning-bg)',
                        border: '1px solid #FCD34D',
                        borderRadius: 'var(--radius-md)',
                        padding: '12px 16px',
                        display: 'flex',
                        flexDirection: 'column',
                        gap: '6px',
                      }}
                    >
                      <div
                        style={{
                          fontSize: '12px',
                          fontWeight: 600,
                          color: 'var(--warning-text)',
                          display: 'flex',
                          alignItems: 'center',
                          gap: '6px',
                        }}
                      >
                        <AlertTriangle size={15} />
                        <span>待核对建议（请比对左侧原文）：</span>
                      </div>
                      <ul
                        style={{
                          margin: 0,
                          paddingLeft: '20px',
                          fontSize: '12px',
                          color: 'var(--warning-text)',
                          lineHeight: 1.6,
                        }}
                      >
                        {qualityCheck.suggestions.map((msg, idx) => (
                          <li key={idx}>{msg}</li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {/* 1.3 原文未提及说明 */}
                  {qualityCheck.neutralInfos.length > 0 && (
                    <div
                      style={{
                        backgroundColor: 'var(--bg-secondary)',
                        border: '1px solid var(--border-color)',
                        borderRadius: 'var(--radius-md)',
                        padding: '10px 14px',
                        display: 'flex',
                        alignItems: 'center',
                        gap: '8px',
                        fontSize: '12px',
                        color: 'var(--text-secondary)',
                      }}
                    >
                      <Info size={14} color="var(--text-muted)" />
                      <span>
                        原文未特别说明：{qualityCheck.neutralInfos.join('、')}（允许留空，不影响启用）
                      </span>
                    </div>
                  )}

                  {/* 2. 标题与所属分类 */}
                  <div
                    style={{
                      border: '1px solid var(--border-color)',
                      borderRadius: 'var(--radius-md)',
                      padding: '16px 20px',
                      backgroundColor: '#FFFFFF',
                      boxShadow: '0 1px 3px rgba(0,0,0,0.02)',
                    }}
                  >
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'flex-start',
                        justifyContent: 'space-between',
                        gap: '16px',
                        marginBottom: '10px',
                      }}
                    >
                      <div style={{ flex: 1 }}>
                        <span style={{ fontSize: '11px', color: 'var(--text-muted)', display: 'block' }}>
                          知识条目标题
                        </span>
                        {fullEditMode || editingSection === 'title' ? (
                          <input
                            type="text"
                            data-testid="input-title"
                            value={title}
                            onChange={(e) => {
                              setTitle(e.target.value);
                              markDirty();
                            }}
                            placeholder="输入简明扼要的知识标题..."
                            style={{
                              width: '100%',
                              height: '36px',
                              padding: '0 10px',
                              fontSize: '14px',
                              fontWeight: 600,
                              borderRadius: 'var(--radius-sm)',
                              border: '1px solid var(--brand-accent)',
                              outline: 'none',
                              marginTop: '4px',
                            }}
                          />
                        ) : (
                          <div
                            style={{
                              fontSize: '17px',
                              fontWeight: 700,
                              color: 'var(--text-primary)',
                              marginTop: '2px',
                              lineHeight: 1.4,
                            }}
                          >
                            {title || '（未命名知识条目）'}
                          </div>
                        )}
                      </div>

                      {/* 编辑按钮 */}
                      {!fullEditMode && (
                        <button
                          type="button"
                          onClick={() =>
                            setEditingSection(editingSection === 'title' ? null : 'title')
                          }
                          style={{
                            fontSize: '11px',
                            color: 'var(--text-secondary)',
                            background: 'none',
                            border: 'none',
                            cursor: 'pointer',
                            display: 'flex',
                            alignItems: 'center',
                            gap: '3px',
                            padding: '4px 6px',
                            borderRadius: 'var(--radius-sm)',
                          }}
                        >
                          <Edit3 size={12} />
                          <span>{editingSection === 'title' ? '完成' : '修改'}</span>
                        </button>
                      )}
                    </div>

                    {/* 主分类切换 */}
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        paddingTop: '10px',
                        borderTop: '1px solid var(--bg-secondary)',
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                          所属五类主分类：
                        </span>
                        {fullEditMode || editingSection === 'category' ? (
                          <select
                            data-testid="select-primary-category"
                            value={primaryCategory}
                            onChange={(e) => {
                              setPrimaryCategory(e.target.value as PrimaryCategory);
                              markDirty();
                            }}
                            style={{
                              height: '30px',
                              padding: '0 8px',
                              fontSize: '12px',
                              fontWeight: 600,
                              borderRadius: 'var(--radius-sm)',
                              border: '1px solid var(--border-color)',
                              backgroundColor: primaryCategory ? '#FFFFFF' : 'var(--warning-bg)',
                              color: primaryCategory
                                ? 'var(--text-primary)'
                                : 'var(--warning-text)',
                              outline: 'none',
                            }}
                          >
                            <option value="">-- 待分类（必须指定五类之一） --</option>
                            <option value="制度与标准">制度与标准</option>
                            <option value="方法与工具">方法与工具</option>
                            <option value="项目案例">项目案例</option>
                            <option value="指标数据">指标数据</option>
                            <option value="专家经验">专家经验</option>
                          </select>
                        ) : primaryCategory ? (
                          <span
                            style={{
                              fontSize: '12px',
                              fontWeight: 600,
                              padding: '3px 10px',
                              borderRadius: 'var(--radius-sm)',
                              backgroundColor:
                                CATEGORY_STYLES[primaryCategory]?.bg || 'var(--bg-secondary)',
                              color:
                                CATEGORY_STYLES[primaryCategory]?.text || 'var(--text-primary)',
                              border: `1px solid ${
                                CATEGORY_STYLES[primaryCategory]?.border || 'var(--border-color)'
                              }`,
                            }}
                          >
                            {primaryCategory}
                          </span>
                        ) : (
                          <span
                            style={{
                              fontSize: '12px',
                              fontWeight: 600,
                              padding: '3px 10px',
                              borderRadius: 'var(--radius-sm)',
                              backgroundColor: 'var(--warning-bg)',
                              color: 'var(--warning-text)',
                              border: '1px solid #FCD34D',
                              display: 'flex',
                              alignItems: 'center',
                              gap: '4px',
                            }}
                          >
                            <AlertCircle size={12} />
                            <span>待分类（需在确认前选定）</span>
                          </span>
                        )}
                      </div>

                      {!fullEditMode && (
                        <button
                          type="button"
                          onClick={() =>
                            setEditingSection(editingSection === 'category' ? null : 'category')
                          }
                          style={{
                            fontSize: '11px',
                            color: 'var(--text-secondary)',
                            background: 'none',
                            border: 'none',
                            cursor: 'pointer',
                            padding: '4px 6px',
                          }}
                        >
                          {editingSection === 'category' ? '完成' : '变更分类'}
                        </button>
                      )}
                    </div>
                  </div>

                  {/* 3. 核心陈述（排版舒适，非普通整屏表单输入框） */}
                  <div
                    style={{
                      border: '1px solid var(--border-color)',
                      borderRadius: 'var(--radius-md)',
                      padding: '16px 20px',
                      backgroundColor: '#FFFFFF',
                      boxShadow: '0 1px 3px rgba(0,0,0,0.02)',
                    }}
                  >
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        marginBottom: '8px',
                      }}
                    >
                      <span
                        style={{
                          fontSize: '12px',
                          fontWeight: 600,
                          color: 'var(--text-secondary)',
                          display: 'flex',
                          alignItems: 'center',
                          gap: '6px',
                        }}
                      >
                        <Shield size={14} color="var(--brand-accent)" />
                        <span>主要讲什么（核心结论） *</span>
                      </span>

                      {!fullEditMode && (
                        <button
                          type="button"
                          onClick={() =>
                            setEditingSection(editingSection === 'statement' ? null : 'statement')
                          }
                          style={{
                            fontSize: '11px',
                            color: 'var(--text-secondary)',
                            background: 'none',
                            border: 'none',
                            cursor: 'pointer',
                            display: 'flex',
                            alignItems: 'center',
                            gap: '3px',
                          }}
                        >
                          <Edit3 size={12} />
                          <span>{editingSection === 'statement' ? '完成' : '修改'}</span>
                        </button>
                      )}
                    </div>

                    {fullEditMode || editingSection === 'statement' ? (
                      <textarea
                        rows={3}
                        value={statement}
                        onChange={(e) => {
                          setStatement(e.target.value);
                          markDirty();
                        }}
                        placeholder="清晰陈述该知识的核心论断、规则或事实..."
                        style={{
                          width: '100%',
                          padding: '10px 12px',
                          fontSize: '13px',
                          lineHeight: 1.6,
                          borderRadius: 'var(--radius-sm)',
                          border: '1px solid var(--brand-accent)',
                          outline: 'none',
                          resize: 'vertical',
                        }}
                      />
                    ) : (
                      <div
                        style={{
                          fontSize: '13px',
                          lineHeight: 1.7,
                          color: 'var(--text-primary)',
                          backgroundColor: 'var(--bg-secondary)',
                          padding: '12px 16px',
                          borderRadius: 'var(--radius-sm)',
                          borderLeft: '3px solid var(--brand-accent)',
                          whiteSpace: 'pre-wrap',
                        }}
                      >
                        {statement || '（暂无核心陈述）'}
                      </div>
                    )}
                  </div>

                  {/* 4. 五类差异化结构要素卡片 */}
                  <div
                    style={{
                      border: '1px solid var(--border-color)',
                      borderRadius: 'var(--radius-md)',
                      backgroundColor: 'var(--bg-secondary)',
                      padding: '16px 20px',
                      display: 'flex',
                      flexDirection: 'column',
                      gap: '14px',
                    }}
                  >
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                      }}
                    >
                      <span
                        style={{
                          fontSize: '13px',
                          fontWeight: 700,
                          color: 'var(--text-primary)',
                        }}
                      >
                        {primaryCategory
                          ? `${primaryCategory}结构要素`
                          : '核心业务结构要素（待分类）'}
                      </span>
                      <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                        按业务类别适配结构化展示，可直接点击修改
                      </span>
                    </div>

                    {/* 制度与标准 */}
                    {primaryCategory === '制度与标准' && (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                        {/* 适用对象 */}
                        <div
                          style={{
                            backgroundColor: '#FFFFFF',
                            padding: '12px 14px',
                            borderRadius: 'var(--radius-sm)',
                            border: '1px solid var(--border-color)',
                          }}
                        >
                          <div
                            style={{
                              fontSize: '11px',
                              fontWeight: 600,
                              color: 'var(--text-secondary)',
                              marginBottom: '6px',
                            }}
                          >
                            谁负责（责任主体）
                          </div>
                          {fullEditMode || editingSection === 'subject' ? (
                            <input
                              type="text"
                              value={subject}
                              onChange={(e) => {
                                setSubject(e.target.value);
                                markDirty();
                              }}
                              placeholder="如：物业服务企业、业主大会、街道办事处..."
                              style={{
                                width: '100%',
                                height: '30px',
                                padding: '0 8px',
                                fontSize: '12px',
                                borderRadius: 'var(--radius-sm)',
                                border: '1px solid var(--border-color)',
                              }}
                            />
                          ) : (
                            <div style={{ fontSize: '12px', color: 'var(--text-primary)' }}>
                              {subject || '（未指定，默认为相关物业管理主体）'}
                            </div>
                          )}
                        </div>

                        {/* 条件与动作与例外 */}
                        <div
                          style={{
                            display: 'grid',
                            gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
                            gap: '12px',
                          }}
                        >
                          {/* 触发条件与适用范围 */}
                          <div
                            style={{
                              backgroundColor: '#FFFFFF',
                              padding: '12px 14px',
                              borderRadius: 'var(--radius-sm)',
                              border: '1px solid var(--border-color)',
                              display: 'flex',
                              flexDirection: 'column',
                              gap: '6px',
                            }}
                          >
                            <div
                              style={{
                                display: 'flex',
                                alignItems: 'center',
                                justifyContent: 'space-between',
                              }}
                            >
                              <span
                                style={{
                                  fontSize: '11px',
                                  fontWeight: 600,
                                  color: 'var(--text-secondary)',
                                }}
                              >
                                什么时候适用（前提条件） ({conditions.length})
                              </span>
                              <button
                                type="button"
                                onClick={() => {
                                  setConditions([...conditions, '']);
                                  markDirty();
                                }}
                                style={{
                                  background: 'none',
                                  border: 'none',
                                  fontSize: '11px',
                                  color: 'var(--brand-accent)',
                                  cursor: 'pointer',
                                }}
                              >
                                + 添加
                              </button>
                            </div>
                            {conditions.length > 0 ? (
                              conditions.map((cond, idx) => (
                                <div key={idx} style={{ display: 'flex', gap: '6px' }}>
                                  <input
                                    type="text"
                                    value={cond}
                                    onChange={(e) => {
                                      const copy = [...conditions];
                                      copy[idx] = e.target.value;
                                      setConditions(copy);
                                      markDirty();
                                    }}
                                    style={{
                                      flex: 1,
                                      height: '28px',
                                      padding: '0 8px',
                                      fontSize: '12px',
                                      borderRadius: 'var(--radius-sm)',
                                      border: '1px solid var(--border-color)',
                                    }}
                                  />
                                  <button
                                    type="button"
                                    onClick={() => {
                                      setConditions(conditions.filter((_, i) => i !== idx));
                                      markDirty();
                                    }}
                                    style={{
                                      background: 'none',
                                      border: 'none',
                                      color: 'var(--error-text)',
                                      cursor: 'pointer',
                                    }}
                                  >
                                    <X size={13} />
                                  </button>
                                </div>
                              ))
                            ) : (
                              <div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                                原文未设特殊前置条件
                              </div>
                            )}
                          </div>

                          {/* 执行要求与规范动作 */}
                          <div
                            style={{
                              backgroundColor: '#FFFFFF',
                              padding: '12px 14px',
                              borderRadius: 'var(--radius-sm)',
                              border: '1px solid var(--border-color)',
                              display: 'flex',
                              flexDirection: 'column',
                              gap: '6px',
                            }}
                          >
                            <div
                              style={{
                                display: 'flex',
                                alignItems: 'center',
                                justifyContent: 'space-between',
                              }}
                            >
                              <span
                                style={{
                                  fontSize: '11px',
                                  fontWeight: 600,
                                  color: 'var(--text-secondary)',
                                }}
                              >
                                应该怎么做（操作步骤） ({actions.length})
                              </span>
                              <button
                                type="button"
                                onClick={() => {
                                  setActions([...actions, '']);
                                  markDirty();
                                }}
                                style={{
                                  background: 'none',
                                  border: 'none',
                                  fontSize: '11px',
                                  color: 'var(--brand-accent)',
                                  cursor: 'pointer',
                                }}
                              >
                                + 添加
                              </button>
                            </div>
                            {actions.length > 0 ? (
                              actions.map((act, idx) => (
                                <div key={idx} style={{ display: 'flex', gap: '6px' }}>
                                  <input
                                    type="text"
                                    value={act}
                                    onChange={(e) => {
                                      const copy = [...actions];
                                      copy[idx] = e.target.value;
                                      setActions(copy);
                                      markDirty();
                                    }}
                                    style={{
                                      flex: 1,
                                      height: '28px',
                                      padding: '0 8px',
                                      fontSize: '12px',
                                      borderRadius: 'var(--radius-sm)',
                                      border: '1px solid var(--border-color)',
                                    }}
                                  />
                                  <button
                                    type="button"
                                    onClick={() => {
                                      setActions(actions.filter((_, i) => i !== idx));
                                      markDirty();
                                    }}
                                    style={{
                                      background: 'none',
                                      border: 'none',
                                      color: 'var(--error-text)',
                                      cursor: 'pointer',
                                    }}
                                  >
                                    <X size={13} />
                                  </button>
                                </div>
                              ))
                            ) : (
                              <div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                                （按核心陈述执行）
                              </div>
                            )}
                          </div>
                        </div>

                        {/* 例外与豁免 */}
                        <div
                          style={{
                            backgroundColor: '#FFFFFF',
                            padding: '12px 14px',
                            borderRadius: 'var(--radius-sm)',
                            border: '1px solid var(--border-color)',
                            display: 'flex',
                            flexDirection: 'column',
                            gap: '6px',
                          }}
                        >
                          <div
                            style={{
                              display: 'flex',
                              alignItems: 'center',
                              justifyContent: 'space-between',
                            }}
                          >
                            <span
                              style={{
                                fontSize: '11px',
                                fontWeight: 600,
                                color: 'var(--text-secondary)',
                              }}
                            >
                              特殊情况与例外（免责或禁止） ({exceptions.length})
                            </span>
                            <button
                              type="button"
                              onClick={() => {
                                setExceptions([...exceptions, '']);
                                markDirty();
                              }}
                              style={{
                                background: 'none',
                                border: 'none',
                                fontSize: '11px',
                                color: 'var(--brand-accent)',
                                cursor: 'pointer',
                              }}
                            >
                              + 添加例外
                            </button>
                          </div>
                          {exceptions.length > 0 ? (
                            exceptions.map((exc, idx) => (
                              <div key={idx} style={{ display: 'flex', gap: '6px' }}>
                                <input
                                  type="text"
                                  value={exc}
                                  onChange={(e) => {
                                    const copy = [...exceptions];
                                    copy[idx] = e.target.value;
                                    setExceptions(copy);
                                    markDirty();
                                  }}
                                  style={{
                                    flex: 1,
                                    height: '28px',
                                    padding: '0 8px',
                                    fontSize: '12px',
                                    borderRadius: 'var(--radius-sm)',
                                    border: '1px solid var(--border-color)',
                                  }}
                                />
                                <button
                                  type="button"
                                  onClick={() => {
                                    setExceptions(exceptions.filter((_, i) => i !== idx));
                                    markDirty();
                                  }}
                                  style={{
                                    background: 'none',
                                    border: 'none',
                                    color: 'var(--error-text)',
                                    cursor: 'pointer',
                                  }}
                                >
                                  <X size={13} />
                                </button>
                              </div>
                            ))
                          ) : (
                            <div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                              原文未特别说明例外情形（允许留空，不影响启用）
                            </div>
                          )}
                        </div>
                      </div>
                    )}

                    {/* 指标数据 */}
                    {primaryCategory === '指标数据' && (
                      <div
                        style={{
                          backgroundColor: '#FFFFFF',
                          padding: '14px',
                          borderRadius: 'var(--radius-sm)',
                          border: '1px solid var(--border-color)',
                          display: 'flex',
                          flexDirection: 'column',
                          gap: '12px',
                        }}
                      >
                        <div
                          style={{
                            display: 'grid',
                            gridTemplateColumns: 'repeat(3, 1fr)',
                            gap: '12px',
                          }}
                        >
                          <div>
                            <label
                              style={{
                                fontSize: '11px',
                                color: 'var(--text-secondary)',
                                display: 'block',
                                marginBottom: '4px',
                              }}
                            >
                              计量单位
                            </label>
                            <input
                              type="text"
                              value={metricDef?.unit || ''}
                              onChange={(e) => {
                                setMetricDef({
                                  ...(metricDef || { name: title, period: '', criteria: '' }),
                                  unit: e.target.value,
                                });
                                markDirty();
                              }}
                              placeholder="如：%、元/㎡·月、次"
                              style={{
                                width: '100%',
                                height: '30px',
                                fontSize: '12px',
                                padding: '0 8px',
                                borderRadius: 'var(--radius-sm)',
                                border: '1px solid var(--border-color)',
                              }}
                            />
                          </div>
                          <div>
                            <label
                              style={{
                                fontSize: '11px',
                                color: 'var(--text-secondary)',
                                display: 'block',
                                marginBottom: '4px',
                              }}
                            >
                              统计期间
                            </label>
                            <input
                              type="text"
                              value={metricDef?.period || ''}
                              onChange={(e) => {
                                setMetricDef({
                                  ...(metricDef || { name: title, unit: '', criteria: '' }),
                                  period: e.target.value,
                                });
                                markDirty();
                              }}
                              placeholder="如：月度、季度、年度"
                              style={{
                                width: '100%',
                                height: '30px',
                                fontSize: '12px',
                                padding: '0 8px',
                                borderRadius: 'var(--radius-sm)',
                                border: '1px solid var(--border-color)',
                              }}
                            />
                          </div>
                          <div>
                            <label
                              style={{
                                fontSize: '11px',
                                color: 'var(--text-secondary)',
                                display: 'block',
                                marginBottom: '4px',
                              }}
                            >
                              达标基准 / 警戒线
                            </label>
                            <input
                              type="text"
                              value={metricDef?.criteria || ''}
                              onChange={(e) => {
                                setMetricDef({
                                  ...(metricDef || { name: title, unit: '', period: '' }),
                                  criteria: e.target.value,
                                });
                                markDirty();
                              }}
                              placeholder="如：≥ 95%、小于 24 小时"
                              style={{
                                width: '100%',
                                height: '30px',
                                fontSize: '12px',
                                padding: '0 8px',
                                borderRadius: 'var(--radius-sm)',
                                border: '1px solid var(--border-color)',
                              }}
                            />
                          </div>
                        </div>
                      </div>
                    )}

                    {/* 方法与工具 */}
                    {primaryCategory === '方法与工具' && (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                        <div
                          style={{
                            backgroundColor: '#FFFFFF',
                            padding: '12px 14px',
                            borderRadius: 'var(--radius-sm)',
                            border: '1px solid var(--border-color)',
                          }}
                        >
                          <div
                            style={{
                              display: 'flex',
                              alignItems: 'center',
                              justifyContent: 'space-between',
                              marginBottom: '6px',
                            }}
                          >
                            <span
                              style={{
                                fontSize: '11px',
                                fontWeight: 600,
                                color: 'var(--text-secondary)',
                              }}
                            >
                              应该怎么做（具体步骤） ({actions.length})
                            </span>
                            <button
                              type="button"
                              onClick={() => {
                                setActions([...actions, '']);
                                markDirty();
                              }}
                              style={{
                                background: 'none',
                                border: 'none',
                                fontSize: '11px',
                                color: 'var(--brand-accent)',
                                cursor: 'pointer',
                              }}
                            >
                              + 增加步骤
                            </button>
                          </div>
                          {actions.map((act, idx) => (
                            <div
                              key={idx}
                              style={{ display: 'flex', gap: '6px', marginBottom: '4px' }}
                            >
                              <span
                                style={{
                                  fontSize: '11px',
                                  color: 'var(--text-muted)',
                                  lineHeight: '28px',
                                  width: '18px',
                                }}
                              >
                                {idx + 1}.
                              </span>
                              <input
                                type="text"
                                value={act}
                                onChange={(e) => {
                                  const copy = [...actions];
                                  copy[idx] = e.target.value;
                                  setActions(copy);
                                  markDirty();
                                }}
                                style={{
                                  flex: 1,
                                  height: '28px',
                                  padding: '0 8px',
                                  fontSize: '12px',
                                  borderRadius: 'var(--radius-sm)',
                                  border: '1px solid var(--border-color)',
                                }}
                              />
                              <button
                                type="button"
                                onClick={() => {
                                  setActions(actions.filter((_, i) => i !== idx));
                                  markDirty();
                                }}
                                style={{
                                  background: 'none',
                                  border: 'none',
                                  color: 'var(--error-text)',
                                  cursor: 'pointer',
                                }}
                              >
                                <X size={13} />
                              </button>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* 项目案例 */}
                    {primaryCategory === '项目案例' && (
                      <div
                        style={{
                          backgroundColor: '#FFFFFF',
                          padding: '14px',
                          borderRadius: 'var(--radius-sm)',
                          border: '1px solid var(--border-color)',
                          display: 'flex',
                          flexDirection: 'column',
                          gap: '10px',
                        }}
                      >
                        <div
                          style={{
                            display: 'grid',
                            gridTemplateColumns: '1fr 1fr',
                            gap: '12px',
                          }}
                        >
                          <div>
                            <label
                              style={{
                                fontSize: '11px',
                                color: 'var(--text-secondary)',
                                display: 'block',
                                marginBottom: '4px',
                              }}
                            >
                              项目背景与面临挑战
                            </label>
                            <textarea
                              rows={2}
                              value={caseDetails?.background || ''}
                              onChange={(e) => {
                                setCaseDetails({
                                  ...(caseDetails || {
                                    actions: '',
                                    results: '',
                                    limitations: '',
                                    background: '',
                                  }),
                                  background: e.target.value,
                                });
                                markDirty();
                              }}
                              style={{
                                width: '100%',
                                padding: '6px 8px',
                                fontSize: '12px',
                                borderRadius: 'var(--radius-sm)',
                                border: '1px solid var(--border-color)',
                              }}
                            />
                          </div>
                          <div>
                            <label
                              style={{
                                fontSize: '11px',
                                color: 'var(--text-secondary)',
                                display: 'block',
                                marginBottom: '4px',
                              }}
                            >
                              成效与经验教训
                            </label>
                            <textarea
                              rows={2}
                              value={caseDetails?.results || ''}
                              onChange={(e) => {
                                setCaseDetails({
                                  ...(caseDetails || {
                                    background: '',
                                    actions: '',
                                    limitations: '',
                                    results: '',
                                  }),
                                  results: e.target.value,
                                });
                                markDirty();
                              }}
                              style={{
                                width: '100%',
                                padding: '6px 8px',
                                fontSize: '12px',
                                borderRadius: 'var(--radius-sm)',
                                border: '1px solid var(--border-color)',
                              }}
                            />
                          </div>
                        </div>
                      </div>
                    )}

                    {/* 专家经验 */}
                    {primaryCategory === '专家经验' && (
                      <div
                        style={{
                          backgroundColor: '#FFFFFF',
                          padding: '14px',
                          borderRadius: 'var(--radius-sm)',
                          border: '1px solid var(--border-color)',
                          display: 'flex',
                          flexDirection: 'column',
                          gap: '10px',
                        }}
                      >
                        <div
                          style={{
                            fontSize: '11px',
                            fontWeight: 600,
                            color: 'var(--text-secondary)',
                          }}
                        >
                          应该怎么做（建议要点）
                        </div>
                        {actions.map((act, idx) => (
                          <div key={idx} style={{ display: 'flex', gap: '6px' }}>
                            <input
                              type="text"
                              value={act}
                              onChange={(e) => {
                                const copy = [...actions];
                                copy[idx] = e.target.value;
                                setActions(copy);
                                markDirty();
                              }}
                              style={{
                                flex: 1,
                                height: '28px',
                                padding: '0 8px',
                                fontSize: '12px',
                                borderRadius: 'var(--radius-sm)',
                                border: '1px solid var(--border-color)',
                              }}
                            />
                            <button
                              type="button"
                              onClick={() => {
                                setActions(actions.filter((_, i) => i !== idx));
                                markDirty();
                              }}
                              style={{
                                background: 'none',
                                border: 'none',
                                color: 'var(--error-text)',
                                cursor: 'pointer',
                              }}
                            >
                              <X size={13} />
                            </button>
                          </div>
                        ))}
                      </div>
                    )}

                    {/* 待分类提示 */}
                    {!primaryCategory && (
                      <div
                        style={{
                          padding: '16px',
                          backgroundColor: '#FFFFFF',
                          borderRadius: 'var(--radius-sm)',
                          border: '1px dashed #FCD34D',
                          fontSize: '12px',
                          color: 'var(--warning-text)',
                          textAlign: 'center',
                        }}
                      >
                        请在上方指定具体分类（如「制度与标准」或「指标数据」），以启用针对性结构要素核对。
                      </div>
                    )}
                  </div>

                  {/* 5. 业务标签与权限 */}
                  <div
                    style={{
                      border: '1px solid var(--border-color)',
                      borderRadius: 'var(--radius-md)',
                      padding: '14px 18px',
                      backgroundColor: '#FFFFFF',
                      display: 'flex',
                      flexDirection: 'column',
                      gap: '10px',
                    }}
                  >
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                      }}
                    >
                      <span
                        style={{
                          fontSize: '12px',
                          fontWeight: 600,
                          color: 'var(--text-secondary)',
                          display: 'flex',
                          alignItems: 'center',
                          gap: '6px',
                        }}
                      >
                        <Tag size={13} />
                        <span>业务标签与权限管理</span>
                      </span>

                      {!fullEditMode && (
                        <button
                          type="button"
                          onClick={() => setIsTagsEditing(!isTagsEditing)}
                          className="btn-secondary"
                          style={{
                            fontSize: '11px',
                            height: '26px',
                            padding: '0 8px',
                            color: 'var(--text-secondary)',
                          }}
                        >
                          {isTagsEditing ? '完成收拢' : '修改适用范围与权限'}
                        </button>
                      )}
                    </div>

                    {/* 收拢状态下的紧凑概览 */}
                    {!isTagsEditing && !fullEditMode ? (
                      <div
                        style={{
                          display: 'grid',
                          gridTemplateColumns: 'repeat(2, 1fr)',
                          gap: '8px',
                          fontSize: '12px',
                          color: 'var(--text-secondary)',
                          backgroundColor: 'var(--bg-secondary)',
                          padding: '10px 12px',
                          borderRadius: 'var(--radius-sm)',
                        }}
                      >
                        <div>
                          <span style={{ color: 'var(--text-muted)' }}>适用客户：</span>
                          <strong style={{ color: 'var(--text-primary)', fontWeight: 500 }}>
                            {customerTypes.length > 0 ? customerTypes.join('、') : '通用客户'}
                          </strong>
                        </div>
                        <div>
                          <span style={{ color: 'var(--text-muted)' }}>业务场景：</span>
                          <strong style={{ color: 'var(--text-primary)', fontWeight: 500 }}>
                            {businessScenes.length > 0 ? businessScenes.join('、') : '通用场景'}
                          </strong>
                        </div>
                        <div>
                          <span style={{ color: 'var(--text-muted)' }}>问题标签：</span>
                          <strong style={{ color: 'var(--text-primary)', fontWeight: 500 }}>
                            {problemTags.length > 0 ? problemTags.join('、') : '未标记'}
                          </strong>
                        </div>
                        <div>
                          <span style={{ color: 'var(--text-muted)' }}>可见权限：</span>
                          <strong style={{ color: 'var(--text-primary)', fontWeight: 500 }}>
                            {accessScope === 'org_internal' ? '企业全员可用' : '仅管理员可见'}
                          </strong>
                        </div>
                      </div>
                    ) : (
                      <>
                        {/* 标签列表 */}
                        <div
                          style={{
                            display: 'flex',
                            flexWrap: 'wrap',
                            gap: '6px',
                            alignItems: 'center',
                          }}
                        >
                          {customerTypes.map((t) => (
                            <span
                              key={`c_${t}`}
                              style={{
                                fontSize: '11px',
                                backgroundColor: 'var(--bg-secondary)',
                                color: 'var(--text-secondary)',
                                padding: '2px 8px',
                                borderRadius: '12px',
                                border: '1px solid var(--border-color)',
                                display: 'flex',
                                alignItems: 'center',
                                gap: '4px',
                              }}
                            >
                              客户: {t}
                              <X
                                size={11}
                                cursor="pointer"
                                onClick={() => {
                                  setCustomerTypes(customerTypes.filter((x) => x !== t));
                                  markDirty();
                                }}
                              />
                            </span>
                          ))}
                          {businessScenes.map((t) => (
                            <span
                              key={`s_${t}`}
                              style={{
                                fontSize: '11px',
                                backgroundColor: 'var(--bg-secondary)',
                                color: 'var(--text-secondary)',
                                padding: '2px 8px',
                                borderRadius: '12px',
                                border: '1px solid var(--border-color)',
                                display: 'flex',
                                alignItems: 'center',
                                gap: '4px',
                              }}
                            >
                              场景: {t}
                              <X
                                size={11}
                                cursor="pointer"
                                onClick={() => {
                                  setBusinessScenes(businessScenes.filter((x) => x !== t));
                                  markDirty();
                                }}
                              />
                            </span>
                          ))}
                          {problemTags.map((t) => (
                            <span
                              key={`p_${t}`}
                              style={{
                                fontSize: '11px',
                                backgroundColor: 'var(--bg-secondary)',
                                color: 'var(--text-secondary)',
                                padding: '2px 8px',
                                borderRadius: '12px',
                                border: '1px solid var(--border-color)',
                                display: 'flex',
                                alignItems: 'center',
                                gap: '4px',
                              }}
                            >
                              问题: {t}
                              <X
                                size={11}
                                cursor="pointer"
                                onClick={() => {
                                  setProblemTags(problemTags.filter((x) => x !== t));
                                  markDirty();
                                }}
                              />
                            </span>
                          ))}
                        </div>

                        {/* 添加标签输入栏 */}
                        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                          <select
                            value={tagType}
                            onChange={(e) => setTagType(e.target.value as any)}
                            style={{ height: '28px', fontSize: '11px', padding: '0 6px' }}
                          >
                            <option value="scene">业务场景</option>
                            <option value="customer">客户类型</option>
                            <option value="problem">问题标签</option>
                          </select>
                          <input
                            type="text"
                            placeholder="输入新标签按回车添加..."
                            value={tagInput}
                            onChange={(e) => setTagInput(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter') handleAddTag();
                            }}
                            style={{
                              flex: 1,
                              height: '28px',
                              fontSize: '12px',
                              padding: '0 8px',
                              borderRadius: 'var(--radius-sm)',
                              border: '1px solid var(--border-color)',
                            }}
                          />
                          <button
                            type="button"
                            className="btn-secondary"
                            onClick={handleAddTag}
                            style={{ height: '28px', fontSize: '11px', padding: '0 10px' }}
                          >
                            添加
                          </button>
                        </div>

                        {/* 权限选择 */}
                        <div
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'space-between',
                            paddingTop: '8px',
                            borderTop: '1px solid var(--bg-secondary)',
                          }}
                        >
                          <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                            访问权限控制
                          </span>
                          <select
                            value={accessScope}
                            onChange={(e) => {
                              setAccessScope(e.target.value as any);
                              markDirty();
                            }}
                            style={{
                              height: '28px',
                              fontSize: '11px',
                              padding: '0 8px',
                              borderRadius: 'var(--radius-sm)',
                              border: '1px solid var(--border-color)',
                            }}
                          >
                            <option value="admin_only">仅管理员可见</option>
                            <option value="org_internal">企业内部全员可用</option>
                          </select>
                        </div>
                      </>
                    )}
                  </div>
                </div>

                {/* 底部操作栏（支持上一条/下一条与连续核对） */}
                <div
                  style={{
                    padding: '12px 24px',
                    borderTop: '1px solid var(--border-color)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    backgroundColor: '#FFFFFF',
                  }}
                >
                  {/* 左侧：连续核对导航与排除 */}
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    {itemList && itemList.length > 0 && (
                      <div
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: '6px',
                          paddingRight: '12px',
                          borderRight: '1px solid var(--border-color)',
                        }}
                      >
                        <button
                          type="button"
                          className="btn-secondary"
                          onClick={() => hasPrev && navigateTo(itemList[currentIndex - 1])}
                          disabled={!hasPrev}
                          title="上一条 (Alt + ←)"
                          style={{
                            height: '32px',
                            padding: '0 8px',
                            fontSize: '11px',
                            gap: '2px',
                          }}
                        >
                          <ChevronLeft size={14} />
                          <span>上一条</span>
                        </button>

                        <span
                          style={{
                            fontSize: '12px',
                            color: 'var(--text-secondary)',
                            fontWeight: 500,
                            padding: '0 4px',
                          }}
                        >
                          第 {currentIndex + 1} / {itemList.length} 条
                        </span>

                        <button
                          type="button"
                          className="btn-secondary"
                          onClick={() => hasNext && navigateTo(itemList[currentIndex + 1])}
                          disabled={!hasNext}
                          title="下一条 (Alt + →)"
                          style={{
                            height: '32px',
                            padding: '0 8px',
                            fontSize: '11px',
                            gap: '2px',
                          }}
                        >
                          <span>下一条</span>
                          <ChevronRight size={14} />
                        </button>
                      </div>
                    )}

                    {/* 暂不处理（跳到下一条） */}
                    <button
                      type="button"
                      className="btn-secondary"
                      onClick={handleSkip}
                      title="暂不保存修改，直接看下一条"
                      style={{
                        height: '32px',
                        fontSize: '11px',
                        padding: '0 10px',
                        color: 'var(--text-secondary)',
                      }}
                    >
                      暂不处理
                    </button>

                    {/* 不收录（排除） */}
                    <button
                      type="button"
                      onClick={() => setShowExcludeModal(true)}
                      className="btn-secondary"
                      title="排除此条知识候选，不计入待核对队列"
                      style={{
                        height: '32px',
                        fontSize: '11px',
                        padding: '0 10px',
                        color: '#B91C1C',
                        borderColor: '#FECACA',
                        backgroundColor: '#FEF2F2',
                        gap: '4px',
                      }}
                    >
                      <Ban size={12} />
                      <span>不收录</span>
                    </button>
                  </div>

                  {/* 右侧：保存草稿、确认启用 */}
                  <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                    <button
                      type="button"
                      data-testid="save-draft-btn"
                      className="btn-secondary"
                      onClick={handleSaveDraft}
                      disabled={saving || confirming}
                      style={{ height: '34px', fontSize: '12px', gap: '4px' }}
                    >
                      <Save size={13} />
                      <span>{saving ? '保存中...' : '保存草稿'}</span>
                    </button>

                    <div style={{ position: 'relative' }}>
                      <button
                        type="button"
                        data-testid="confirm-knowledge-btn"
                        className="btn-primary"
                        onClick={handleConfirm}
                        disabled={confirming || saving || qualityCheck.isBlocked}
                        style={{
                          height: '34px',
                          fontSize: '12px',
                          gap: '4px',
                          opacity: qualityCheck.isBlocked ? 0.55 : 1,
                          cursor: qualityCheck.isBlocked ? 'not-allowed' : 'pointer',
                        }}
                        title={
                          qualityCheck.isBlocked
                            ? `无法启用：${qualityCheck.blocking[0]}`
                            : '核对无误，确认并启用知识'
                        }
                      >
                        <CheckCircle2 size={13} />
                        <span>{confirming ? '正在确认启用...' : '确认内容并启用'}</span>
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* 不收录确认弹窗 */}
      {showExcludeModal && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            backgroundColor: 'rgba(0, 0, 0, 0.55)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 1100,
          }}
        >
          <div
            style={{
              width: '440px',
              backgroundColor: '#FFFFFF',
              borderRadius: 'var(--radius-lg)',
              boxShadow: '0 12px 36px rgba(0, 0, 0, 0.2)',
              padding: '24px',
              display: 'flex',
              flexDirection: 'column',
              gap: '16px',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
              <div
                style={{
                  width: '36px',
                  height: '36px',
                  borderRadius: '50%',
                  backgroundColor: '#FEF2F2',
                  color: '#DC2626',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  flexShrink: 0,
                }}
              >
                <Ban size={18} />
              </div>
              <div>
                <div style={{ fontSize: '15px', fontWeight: 700, color: 'var(--text-primary)' }}>
                  排除此条知识（不收录）
                </div>
                <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '2px' }}>
                  排除后将移出待核对队列和分类统计，后续重试不会自动复活。
                </div>
              </div>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              <span style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-primary)' }}>
                请选择排除原因：
              </span>
              {EXCLUSION_REASONS.map((r) => (
                <label
                  key={r}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '8px',
                    fontSize: '12px',
                    color: 'var(--text-primary)',
                    cursor: 'pointer',
                  }}
                >
                  <input
                    type="radio"
                    name="exclude_reason"
                    checked={selectedExcludeReason === r}
                    onChange={() => setSelectedExcludeReason(r)}
                  />
                  <span>{r}</span>
                </label>
              ))}

              {selectedExcludeReason === '其他原因' && (
                <input
                  type="text"
                  placeholder="请简要说明不收录的原因..."
                  value={customExcludeReason}
                  onChange={(e) => setCustomExcludeReason(e.target.value)}
                  style={{
                    height: '30px',
                    padding: '0 8px',
                    fontSize: '12px',
                    borderRadius: 'var(--radius-sm)',
                    border: '1px solid var(--border-color)',
                    marginTop: '4px',
                  }}
                />
              )}
            </div>

            <div
              style={{
                display: 'flex',
                justifyContent: 'flex-end',
                gap: '10px',
                marginTop: '8px',
              }}
            >
              <button
                type="button"
                className="btn-secondary"
                onClick={() => setShowExcludeModal(false)}
                disabled={deleting}
                style={{ height: '32px', fontSize: '12px' }}
              >
                取消
              </button>
              <button
                type="button"
                onClick={handleConfirmExclude}
                disabled={deleting}
                style={{
                  height: '32px',
                  fontSize: '12px',
                  padding: '0 14px',
                  borderRadius: 'var(--radius-sm)',
                  backgroundColor: '#DC2626',
                  color: '#FFFFFF',
                  border: 'none',
                  cursor: 'pointer',
                  fontWeight: 600,
                }}
              >
                {deleting ? '正在排除...' : '确认不收录'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 未保存离开确认弹窗 */}
      <UnsavedChangesModal
        isOpen={showUnsavedPrompt}
        onKeepEditing={() => {
          setShowUnsavedPrompt(false);
          setPendingNextId(null);
        }}
        onDiscard={() => {
          setShowUnsavedPrompt(false);
          setIsDirty(false);
          if (pendingNextId && onSelectNext) {
            const next = pendingNextId;
            setPendingNextId(null);
            onSelectNext(next);
          } else {
            onClose();
          }
        }}
        onSave={handleSaveDraft}
        saving={saving}
      />
    </>
  );
};
