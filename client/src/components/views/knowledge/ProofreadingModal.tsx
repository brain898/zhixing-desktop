import React, { useState, useEffect, useMemo } from 'react';
import {
  X,
  CheckCircle2,
  AlertCircle,
  Info,
  Clock,
  BookOpen,
  FileText,
  Save,
  Tag,
  Shield,
  ChevronLeft,
  ChevronRight,
  ChevronDown,
  Edit3,
  Ban,
  RotateCcw,
  RotateCw,
} from 'lucide-react';
import {
  KnowledgeItem,
  KnowledgeItemDetail,
  PrimaryCategory,
  AtomType,
  MetricDefinition,
  CaseDetails,
} from '../../../types';
import { api } from '../../../services/api';
import { UnsavedChangesModal } from './UnsavedChangesModal';
import { StructuredReview, normalizeCaseDetails } from './StructuredReview';
import { MetricRowsEditor } from './MetricRowsEditor';
import { AppConfirmDialog } from '../../common/AppConfirmDialog';
import { formatVersionLabel, formatAnchor } from '../../../utils/formatters';

export interface ProofreadingModalProps {
  itemId: string | null;
  itemList?: string[];
  items?: KnowledgeItem[];
  onSelectNext?: (nextId: string) => void;
  onClose: () => void;
  onSaved: () => void;
  onDeleted: () => void;
}

import { CATEGORY_STYLES, EditableStringList } from './knowledgeShared';

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
}

export const ProofreadingModal: React.FC<ProofreadingModalProps> = ({
  itemId,
  itemList = [],
  items = [],
  onSelectNext,
  onClose,
  onSaved,
  onDeleted,
}) => {
  const [detail, setDetail] = useState<KnowledgeItemDetail | null>(null);
  const [isDetailReady, setIsDetailReady] = useState(false);
  const isDetailReadyRef = React.useRef(false);
  const currentItemIdRef = React.useRef<string | null>(itemId);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [retryingStructure, setRetryingStructure] = useState(false);
  const [jevBusy, setJevBusy] = useState(false);
  const [jevExpanded, setJevExpanded] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [togglingLifecycle, setTogglingLifecycle] = useState(false);
  const [restoringHistory, setRestoringHistory] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 知识条目横向切换过渡状态与请求序号保护
  const [switchDirection, setSwitchDirection] = useState<'next' | 'prev' | 'fade'>('fade');
  const itemRequestSeqRef = React.useRef(0);
  const isDirtyRef = React.useRef(false);

  // 全局忙碌状态：执行敏感异步操作期间锁定切换与关闭，杜绝目标错位
  const isBusy = saving || confirming || deleting || retryingStructure || togglingLifecycle || restoringHistory || jevBusy;

  const refreshCurrentDetail = async () => {
    const currentId = currentItemIdRef.current;
    if (!currentId) return;
    const data = await api.getKnowledgeItemDetail(currentId);
    if (currentItemIdRef.current !== currentId) return;
    setDetail(data);
    if (!isDirtyRef.current) applyDetailToForm(data);
  };

  const handleRetryJev = async () => {
    if (!itemId || jevBusy) return;
    setJevBusy(true);
    setError(null);
    try {
      await api.retryJevEvaluation(itemId);
      await refreshCurrentDetail();
    } catch (err: any) {
      setError(err.message || 'Jev 质检重试失败');
    } finally {
      setJevBusy(false);
    }
  };

  const handleIgnoreJevQuestion = async (questionId: string) => {
    if (!itemId || jevBusy) return;
    setJevBusy(true);
    setError(null);
    try {
      await api.ignoreJevQuestion(itemId, questionId);
      await refreshCurrentDetail();
    } catch (err: any) {
      setError(err.message || '忽略 Jev 提示失败');
    } finally {
      setJevBusy(false);
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

  // 全量编辑模式与就地微调
  const [fullEditMode, setFullEditMode] = useState(false);
  const [editingSection, setEditingSection] = useState<string | null>(null);
  const [isTagsEditing, setIsTagsEditing] = useState(false);
  const [focusedBlock, setFocusedBlock] = useState<string | null>(null);
  const [focusedExcerpt, setFocusedExcerpt] = useState('');
  const [focusedPrecision, setFocusedPrecision] = useState<'field' | 'paragraph'>('paragraph');
  const canEdit = isDetailReady && !isBusy;
  const titleEditing = canEdit && (fullEditMode || editingSection === 'title');
  const categoryEditing = canEdit && (fullEditMode || editingSection === 'category');
  const statementEditing = canEdit && (fullEditMode || editingSection === 'statement');
  const structureEditing = canEdit && (fullEditMode || editingSection === 'structure');
  const tagsEditing = canEdit && (fullEditMode || isTagsEditing);

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
  const [previewFlags, setPreviewFlags] = useState<string[]>([]);
  const [previewBlocking, setPreviewBlocking] = useState<string[]>([]);
  const [previewStatus, setPreviewStatus] = useState<'idle' | 'waiting' | 'checking' | 'ready' | 'error'>('idle');
  const [previewError, setPreviewError] = useState('');
  const [previewNonce, setPreviewNonce] = useState(0);

  // 脏状态追踪
  const [isDirty, setIsDirty] = useState(false);
  const [showUnsavedPrompt, setShowUnsavedPrompt] = useState(false);
  const [pendingNextId, setPendingNextId] = useState<string | null>(null);

  // 排除（不收录）弹窗状态
  const [showExcludeModal, setShowExcludeModal] = useState(false);
  // M02-E（M01-C4 / R7）：排除前显示被多少个 Skill 引用
  const [excludeSkillRefs, setExcludeSkillRefs] = useState<number | null>(null);
  const [selectedExcludeReason, setSelectedExcludeReason] = useState(EXCLUSION_REASONS[0]);
  const [customExcludeReason, setCustomExcludeReason] = useState('');

  // 标签输入与收拢状态
  const [tagInput, setTagInput] = useState('');
  const [tagType, setTagType] = useState<'customer' | 'scene' | 'problem'>('scene');

  // M01-C3：场景目录存在时，业务场景可从目录中选择；目录为空或加载失败时保持原有手动输入
  const [sceneCatalogNames, setSceneCatalogNames] = useState<string[]>([]);
  useEffect(() => {
    let alive = true;
    api
      .getSceneCatalog()
      .then((res) => {
        if (alive) setSceneCatalogNames(res.scenes.filter((s) => s.status === 'active').map((s) => s.name));
      })
      .catch(() => {
        if (alive) setSceneCatalogNames([]);
      });
    return () => {
      alive = false;
    };
  }, []);

  // 历史版本：只读查看；恢复动作只生成新草稿，不直接修改历史记录
  const [showVersionHistory, setShowVersionHistory] = useState(false);
  const [historyPreview, setHistoryPreview] = useState<any | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);

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
        });
      }
    }
    return Array.from(map.values());
  }, [detail?.evidence]);

  const locateEvidence = (fieldName: string, index = 0) => {
    const evidence = detail?.evidence || [];
    const matches = evidence.filter(ev => ev.field_name === fieldName);
    // 行号并非证据 ID。指标行只有在数值与单位均出现在摘录中时，才把摘录当作该行证据。
    const metricRow = fieldName === 'metric_definition' ? metricDef?.rows?.[index] : undefined;
    const rowMatches = metricRow ? matches.filter(ev =>
      (!metricRow.value || (ev.excerpt || '').includes(metricRow.value)) &&
      (!metricRow.unit || (ev.excerpt || '').includes(metricRow.unit))) : [];
    const ev = metricRow ? (rowMatches[0] || evidence[0]) : (matches[index] || matches[0] || evidence[0]);
    if (!ev?.source_block_id) return;
    setFocusedBlock(ev.source_block_id);
    const fullText = ev.text_content || '';
    const excerpt = ev.excerpt || '';
    const fieldBound = metricRow ? rowMatches.includes(ev) : matches.includes(ev);
    const exact = fieldBound && ev.accuracy_level === 'exact' && excerpt.length > 0 &&
      excerpt !== fullText && fullText.includes(excerpt);
    setFocusedExcerpt(exact ? excerpt : '');
    setFocusedPrecision(exact ? 'field' : 'paragraph');
    window.requestAnimationFrame(() => {
      document.getElementById('proofread-source-' + ev.source_block_id)?.scrollIntoView({
        block: 'center', behavior: 'smooth',
      });
    });
  };

  // 当前条目在列表中的索引
  const currentIndex = useMemo(() => {
    if (!itemId || !itemList || itemList.length === 0) return -1;
    return itemList.indexOf(itemId);
  }, [itemId, itemList]);

  const hasPrev = currentIndex > 0;
  const hasNext = currentIndex >= 0 && currentIndex < itemList.length - 1;

  // 是否为已确认生效的正式知识条目（非待核对新候选，且无待确认草稿）
  const isConfirmedActive = useMemo(() => {
    return Boolean(
      detail &&
      detail.active_version &&
      detail.active_version.review_status === 'confirmed' &&
      !detail.is_draft_version
    );
  }, [detail]);

  const applyDetailToForm = (itemDetail: KnowledgeItemDetail) => {
    const av = itemDetail.active_version;
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
    setCaseDetails(normalizeCaseDetails(av.case_details));
    setCustomerTypes(av.customer_types || []);
    setBusinessScenes(av.business_scenes || []);
    setProblemTags(av.problem_tags || []);
    const docScope = itemDetail.document_access_scope;
    const effectiveScope = docScope === 'admin_only' ? 'admin_only' : (itemDetail.access_scope || 'admin_only');
    setAccessScope(effectiveScope);
    setValidFrom(av.valid_from || '');
    setValidUntil(av.valid_until || '');
    setQualityFlags(av.quality_flags || []);
    setIsDirty(false);
    isDirtyRef.current = false;
    setEditingSection(null);
  };

  useEffect(() => {
    currentItemIdRef.current = itemId;
    if (!itemId) {
      setDetail(null);
      setIsDetailReady(false);
      isDetailReadyRef.current = false;
      setLoading(false);
      setError(null);
      setIsDirty(false);
      isDirtyRef.current = false;
      return;
    }

    const seq = ++itemRequestSeqRef.current;
    setIsDetailReady(false);
    isDetailReadyRef.current = false;
    setLoading(true);
    setError(null);
    setIsDirty(false);
    isDirtyRef.current = false;
    setEditingSection(null);
    setFullEditMode(false);
    setFocusedBlock(null);
    setFocusedExcerpt('');
    setJevExpanded(false);

    // 若 items 中已包含该条目，就地呈现基础摘要预览消除白屏与突兀感；
    // 但明确区分摘要与完整详情：完整详情未返回前不允许编辑、保存、确认等业务操作。
    const targetItem = items?.find((i) => i.id === itemId);
    if (targetItem) {
      setTitle(targetItem.title || '');
      setStatement(targetItem.statement || '');
      setContent(targetItem.statement || '');
      setPrimaryCategory(targetItem.primary_category || '');
      setAtomType(targetItem.atom_type || '规则');
      setSubject(targetItem.subject || '物业责任主体');
      setConditions(targetItem.conditions || []);
      setActions(targetItem.actions || []);
      setExceptions(targetItem.exceptions || []);
      setMetricDef(targetItem.metric_definition || null);
      setCaseDetails(normalizeCaseDetails(targetItem.case_details));
      setCustomerTypes(targetItem.customer_types || []);
      setBusinessScenes(targetItem.business_scenes || []);
      setProblemTags(targetItem.problem_tags || []);
      const effectiveScope = targetItem.access_scope === 'admin_only' ? 'admin_only' : (targetItem.access_scope || 'admin_only');
      setAccessScope(effectiveScope);
      setValidFrom(targetItem.valid_from || '');
      setValidUntil(targetItem.valid_until || '');
      setQualityFlags(targetItem.quality_flags || []);
    } else {
      setTitle('');
      setStatement('');
      setContent('');
      setPrimaryCategory('');
      setConditions([]);
      setActions([]);
      setExceptions([]);
      setMetricDef(null);
      setCaseDetails(null);
      setCustomerTypes([]);
      setBusinessScenes([]);
      setProblemTags([]);
    }

    api
      .getKnowledgeItemDetail(itemId)
      .then((data) => {
        // 请求序号或目标条目不一致，直接丢弃（乱序返回、已切换或弹窗已关闭）
        if (seq !== itemRequestSeqRef.current || currentItemIdRef.current !== itemId) return;
        setDetail(data);
        // 初始详情响应不能覆盖其请求发出后产生的编辑
        if (!isDirtyRef.current) {
          applyDetailToForm(data);
        }
        setIsDetailReady(true);
        isDetailReadyRef.current = true;
      })
      .catch((err: any) => {
        if (seq !== itemRequestSeqRef.current || currentItemIdRef.current !== itemId) return;
        setError(err.message || '加载知识详情失败');
        setIsDetailReady(false);
        isDetailReadyRef.current = false;
      })
      .finally(() => {
        if (seq === itemRequestSeqRef.current && currentItemIdRef.current === itemId) {
          setLoading(false);
        }
      });

    return () => {
      // 切换或卸载使旧请求失效
      itemRequestSeqRef.current++;
    };
  }, [itemId]);

  // Jev 在后台执行，工作台只轮询当前条目的评估状态，不阻塞编辑或复用旧结果。
  useEffect(() => {
    const jevStatus = detail?.jev_evaluation?.status;
    if (!itemId || !['queued', 'running'].includes(jevStatus || '')) return;
    const timer = window.setInterval(() => {
      const currentId = currentItemIdRef.current;
      if (!currentId || currentId !== itemId) return;
      api.getKnowledgeItemDetail(currentId).then((data) => {
        if (currentItemIdRef.current === currentId) setDetail(data);
      }).catch(() => {
        // 后台质检失败会由下一次正常详情加载明确显示，不把轮询错误伪装成检查通过。
      });
    }, 1500);
    return () => window.clearInterval(timer);
  }, [itemId, detail?.jev_evaluation?.status]);

  const markDirty = () => {
    if (!isDetailReady || isBusy) return;
    setIsDirty(true);
    isDirtyRef.current = true;
    setPreviewStatus('waiting');
    setPreviewError('');
  };

  const effectiveQualityFlags = isDirty
    ? (previewStatus === 'ready' ? previewFlags : [])
    : qualityFlags;

  // 质检信息三层分类（阻断启用、建议确认、原文未提及）
  const qualityCheck = useMemo(() => {
    const blocking: string[] = [];
    const suggestions: string[] = [];
    const neutralInfos: string[] = [];

    // 前端保留简单必填提示
    if (!title.trim()) {
      blocking.push('知识条目标题不能为空');
    }
    if (!statement.trim()) {
      blocking.push('核心陈述不能为空且须具备实质业务内容');
    }
    if (!primaryCategory) {
      blocking.push('主分类仍为「待分类」，确认前必须明确指定五类主分类之一');
    }

    // 常规质量标记分类（移除中文正则推断阻断条件，由服务端统一返回阻断项）
    for (const flag of effectiveQualityFlags) {
      if (flag.includes('管理员操作')) continue;

      if (
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

    // 后端统一驱动业务阻断条件
    const serverBlockers = isDirty
      ? (previewStatus === 'ready' ? previewBlocking : [])
      : (detail?.confirmation_blockers || []);
    for (const msg of serverBlockers) {
      if (msg === '该版本已确认或不处于待审核状态') continue;
      if (!blocking.includes(msg)) blocking.push(msg);
    }

    return {
      blocking,
      suggestions,
      neutralInfos,
      isBlocked: blocking.length > 0,
    };
  }, [title, statement, primaryCategory, effectiveQualityFlags, isDirty, previewStatus, previewBlocking, detail]);

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
      access_scope: detail?.document_access_scope === 'admin_only' ? 'admin_only' : accessScope,
      valid_from: validFrom || null,
      valid_until: validUntil || null,
    };
  };

  // 防抖只读预览：每次编辑后重新核对当前字段。乱序响应不会覆盖后续编辑或其他条目。
  useEffect(() => {
    if (!isDirty || !detail || detail.id !== itemId ||
      !['pending_review', 'confirmed'].includes(detail.active_version.review_status)) {
      if (!isDirty) setPreviewStatus('idle');
      return;
    }
    let current = true;
    setPreviewStatus('waiting');
    setPreviewError('');
    const token = detail.active_version.revision_token;
    const payload = {
      revision_token: token, title, statement, primary_category: primaryCategory || null,
      atom_type: atomType, subject, conditions, actions, exceptions,
      metric_definition: metricDef, case_details: caseDetails,
    };
    const timer = window.setTimeout(() => {
      if (!current) return;
      setPreviewStatus('checking');
      api.validateKnowledgeDraft(detail.id, payload)
        .then(result => {
          if (!current || result.revision_token !== token) return;
          setPreviewFlags(result.quality_flags);
          setPreviewBlocking(result.blocking);
          setPreviewStatus('ready');
        })
        .catch((err: any) => {
          if (!current) return;
          setPreviewStatus('error');
          setPreviewError(err?.message || '校验服务暂不可用');
        });
    }, 550);
    return () => {
      current = false;
      window.clearTimeout(timer);
    };
  }, [itemId, isDirty, detail?.id, detail?.active_version.revision_token,
      detail?.active_version.review_status, title, statement, primaryCategory,
      atomType, subject, conditions, actions, exceptions, metricDef, caseDetails, previewNonce]);

  // 仅对仍未被人工处理的候选执行手动重抽；后端再次验证权限、来源版本及乐观锁。
  const handleRetryStructure = async () => {
    if (!detail || !isDetailReady || isDirty || retryingStructure || isBusy) return;
    const opItemId = detail.id;
    const opSeq = itemRequestSeqRef.current;
    try {
      setRetryingStructure(true);
      await api.retryKnowledgeStructure(opItemId, detail.active_version.revision_token);
      if (currentItemIdRef.current !== opItemId || itemRequestSeqRef.current !== opSeq) return;
      const refreshed = await api.getKnowledgeItemDetail(opItemId);
      if (currentItemIdRef.current !== opItemId || itemRequestSeqRef.current !== opSeq) return;
      setDetail(refreshed);
      applyDetailToForm(refreshed);
      setFullEditMode(false);
      onSaved();
    } catch (err: any) {
      if (currentItemIdRef.current === opItemId && itemRequestSeqRef.current === opSeq) {
        alert('单条重抽未完成：' + (err.message || '请直接核对与编辑原文'));
      }
    } finally {
      if (currentItemIdRef.current === opItemId && itemRequestSeqRef.current === opSeq) {
        setRetryingStructure(false);
      }
    }
  };

  // 保存草稿
  const handleSaveDraft = async () => {
    if (!detail || !isDetailReady || isBusy) return;
    const opItemId = detail.id;
    const opSeq = itemRequestSeqRef.current;
    try {
      setSaving(true);
      const payload = buildPayload();
      const res = await api.saveKnowledgeDraft(opItemId, payload);
      if (currentItemIdRef.current !== opItemId || itemRequestSeqRef.current !== opSeq) return;
      setDetail((prev) => {
        if (!prev || prev.id !== opItemId) return prev;
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
      isDirtyRef.current = false;
      setShowUnsavedPrompt(false);
      onSaved();

      if (pendingNextId && onSelectNext) {
        const next = pendingNextId;
        setPendingNextId(null);
        onSelectNext(next);
      }
    } catch (err: any) {
      if (currentItemIdRef.current === opItemId && itemRequestSeqRef.current === opSeq) {
        alert(`保存草稿失败: ${err.message}`);
      }
    } finally {
      if (currentItemIdRef.current === opItemId && itemRequestSeqRef.current === opSeq) {
        setSaving(false);
      }
    }
  };

  // 确认知识并启用
  const handleConfirm = async () => {
    if (!detail || !isDetailReady || isBusy) return;

    // 校验未完成时不能拿旧提示确认；新旧草稿均由后端做最终资格校验。
    if (isDirty && previewStatus !== 'ready') {
      alert('请等待当前内容自动校验完成；若校验失败，可先保存草稿再重试。');
      return;
    }
    if (qualityCheck.isBlocked) {
      alert(`无法确认启用：\n${qualityCheck.blocking.join('\n')}`);
      return;
    }

    const opItemId = detail.id;
    const opSeq = itemRequestSeqRef.current;

    try {
      setConfirming(true);
      if (isDirty) {
        const draftRes = await api.saveKnowledgeDraft(opItemId, buildPayload());
        if (currentItemIdRef.current !== opItemId || itemRequestSeqRef.current !== opSeq) return;
        detail.active_version.revision_token = draftRes.revision_token;
      }

      const confirmRes = await api.confirmKnowledgeItem(opItemId, {
        revision_token: detail.active_version.revision_token,
      });

      if (currentItemIdRef.current !== opItemId || itemRequestSeqRef.current !== opSeq) return;

      const willAdvance = !isConfirmedActive && hasNext && Boolean(onSelectNext);

      if (!willAdvance) {
        setDetail((prev) => {
          if (!prev || prev.id !== opItemId) return prev;
          return {
            ...prev,
            lifecycle_status: 'active',
            is_draft_version: false,
            can_confirm: false,
            confirmation_blockers: [],
            active_version: {
              ...prev.active_version,
              review_status: 'confirmed',
              index_status: confirmRes.index_status as any,
              revision_token: confirmRes.revision_token,
            },
          };
        });
      }
      setIsDirty(false);
      isDirtyRef.current = false;
      onSaved();

      // 确认启用成功后，待核对队列自动流转至下一条；若已是已生效知识条目维护，直接保存生效并关闭弹窗
      if (willAdvance && onSelectNext) {
        setSwitchDirection('next');
        onSelectNext(itemList[currentIndex + 1]);
      } else {
        onClose();
      }
    } catch (err: any) {
      if (currentItemIdRef.current === opItemId && itemRequestSeqRef.current === opSeq) {
        alert(`确认失败: ${err.message}`);
      }
    } finally {
      if (currentItemIdRef.current === opItemId && itemRequestSeqRef.current === opSeq) {
        setConfirming(false);
      }
    }
  };

  // 停用与恢复启用
  const handleToggleLifecycle = async () => {
    if (!detail || !isDetailReady || isBusy) return;
    const targetStatus = detail.lifecycle_status === 'disabled' ? 'active' : 'disabled';
    const actionText = targetStatus === 'disabled' ? '停用' : '恢复启用';
    const opItemId = detail.id;
    const opSeq = itemRequestSeqRef.current;

    setConfirmDialogState({
      isOpen: true,
      title: `确认${actionText}知识条目`,
      variant: targetStatus === 'disabled' ? 'danger' : 'primary',
      confirmText: actionText,
      cancelText: '取消',
      targetName: detail.active_version.title || '当前知识条目',
      description: `即将${actionText}该知识条目。`,
      impactDescription:
        targetStatus === 'disabled'
          ? '停用后将退出正式检索，不再对外提供服务。'
          : '恢复后将立即重新恢复正式检索服务。',
      initialFocus: targetStatus === 'disabled' ? 'cancel' : 'confirm',
      onConfirm: async () => {
        try {
          setTogglingLifecycle(true);
          await api.updateKnowledgeLifecycle(opItemId, targetStatus);
          if (currentItemIdRef.current !== opItemId || itemRequestSeqRef.current !== opSeq) return;
          setDetail((prev) => (prev && prev.id === opItemId ? { ...prev, lifecycle_status: targetStatus } : prev));
          onSaved();
          setConfirmDialogState((prev) => ({ ...prev, isOpen: false }));
        } catch (err: any) {
          if (currentItemIdRef.current === opItemId && itemRequestSeqRef.current === opSeq) {
            alert(`${actionText}失败: ${err.message}`);
          }
        } finally {
          if (currentItemIdRef.current === opItemId && itemRequestSeqRef.current === opSeq) {
            setTogglingLifecycle(false);
          }
        }
      },
    });
  };

  // 排除（不收录）操作
  const handleConfirmExclude = async () => {
    if (!detail || !isDetailReady || isBusy) return;
    const reason =
      selectedExcludeReason === '其他原因'
        ? customExcludeReason.trim() || '其他原因'
        : selectedExcludeReason;
    const opItemId = detail.id;
    const opSeq = itemRequestSeqRef.current;

    try {
      setDeleting(true);
      await api.deleteKnowledgeItem(opItemId, 'exclude', reason);
      if (currentItemIdRef.current !== opItemId || itemRequestSeqRef.current !== opSeq) return;
      setShowExcludeModal(false);
      onDeleted();

      if (hasNext && onSelectNext) {
        setSwitchDirection('next');
        onSelectNext(itemList[currentIndex + 1]);
      } else if (hasPrev && onSelectNext) {
        setSwitchDirection('prev');
        onSelectNext(itemList[currentIndex - 1]);
      } else {
        onClose();
      }
    } catch (err: any) {
      if (currentItemIdRef.current === opItemId && itemRequestSeqRef.current === opSeq) {
        alert(`排除操作失败: ${err.message}`);
      }
    } finally {
      if (currentItemIdRef.current === opItemId && itemRequestSeqRef.current === opSeq) {
        setDeleting(false);
      }
    }
  };

  const handleViewHistory = async (versionId: string) => {
    if (!detail || !isDetailReady || isBusy) return;
    const opItemId = detail.id;
    const opSeq = itemRequestSeqRef.current;
    try {
      setHistoryLoading(true);
      const data = await api.getKnowledgeVersionHistory(opItemId, versionId);
      if (currentItemIdRef.current !== opItemId || itemRequestSeqRef.current !== opSeq) return;
      setHistoryPreview(data);
    } catch (err: any) {
      if (currentItemIdRef.current === opItemId && itemRequestSeqRef.current === opSeq) {
        alert(`历史版本读取失败: ${err.message}`);
      }
    } finally {
      if (currentItemIdRef.current === opItemId && itemRequestSeqRef.current === opSeq) {
        setHistoryLoading(false);
      }
    }
  };

  const handleRestoreHistory = async (versionId: string) => {
    if (!detail || !isDetailReady || isBusy) return;
    const opItemId = detail.id;
    const opSeq = itemRequestSeqRef.current;
    const opToken = detail.active_version.revision_token;

    setConfirmDialogState({
      isOpen: true,
      title: '确认恢复历史版本为草稿',
      variant: 'primary',
      confirmText: '生成新草稿',
      cancelText: '取消',
      targetName: `历史版本 v${versionId.slice(0, 8)}`,
      description: '恢复不会修改历史记录，而是基于该历史内容创建一个新的待确认草稿。',
      impactDescription: '创建后可以在核对窗口中进一步校对并再次确认发布。',
      initialFocus: 'confirm',
      onConfirm: async () => {
        try {
          setRestoringHistory(true);
          const res = await api.restoreKnowledgeHistory(
            opItemId,
            versionId,
            opToken
          );
          if (currentItemIdRef.current !== opItemId || itemRequestSeqRef.current !== opSeq) return;
          alert(`${res.message}（新草稿 v${res.version_number}）`);
          onSaved();
          onClose();
        } catch (err: any) {
          if (currentItemIdRef.current === opItemId && itemRequestSeqRef.current === opSeq) {
            alert(`恢复历史版本失败: ${err.message}`);
          }
        } finally {
          if (currentItemIdRef.current === opItemId && itemRequestSeqRef.current === opSeq) {
            setRestoringHistory(false);
            setConfirmDialogState((prev) => ({ ...prev, isOpen: false }));
          }
        }
      },
    });
  };

  // 切换条目处理
  const navigateTo = (nextId: string, direction?: 'next' | 'prev') => {
    if (isBusy || !onSelectNext) return;
    const nextIdx = itemList.indexOf(nextId);
    setSwitchDirection(direction || (nextIdx >= 0 && nextIdx < currentIndex ? 'prev' : 'next'));
    if (isDirty) {
      setPendingNextId(nextId);
      setShowUnsavedPrompt(true);
    } else {
      onSelectNext(nextId);
    }
  };

  // 放弃修改：将表单数据还原至服务端最新状态并清除脏标记
  const handleResetChanges = () => {
    if (!detail || !isDetailReady || isBusy) return;
    applyDetailToForm(detail);
  };

  const handleRequestClose = () => {
    if (isBusy) return;
    if (isDirty) {
      setPendingNextId(null);
      setShowUnsavedPrompt(true);
    } else {
      onClose();
    }
  };

  // 暂不处理（跳过此条）
  const handleSkip = () => {
    if (isBusy) return;
    if (hasNext && onSelectNext) {
      if (isDirty) {
        setConfirmDialogState({
          isOpen: true,
          title: '放弃未保存修改并跳过',
          variant: 'danger',
          confirmText: '放弃并跳过',
          cancelText: '返回编辑',
          targetName: title || '当前知识条目',
          description: '当前条目已有未保存的修改内容。',
          impactDescription: '跳过后未保存的修改将被丢弃，直接切换到下一条。',
          initialFocus: 'cancel',
          onConfirm: () => {
            setIsDirty(false);
            isDirtyRef.current = false;
            setConfirmDialogState((prev) => ({ ...prev, isOpen: false }));
            setSwitchDirection('next');
            onSelectNext(itemList[currentIndex + 1]);
          },
        });
      } else {
        setSwitchDirection('next');
        onSelectNext(itemList[currentIndex + 1]);
      }
    } else {
      onClose();
    }
  };

  // 添加标签
  const handleAddTag = () => {
    if (!isDetailReady || isBusy) return;
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

  // 键盘快捷操作支持（Escape关闭、Alt+←上一条、Alt+→下一条）
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (isBusy) return;
      const isInput = ['INPUT', 'TEXTAREA', 'SELECT'].includes((e.target as HTMLElement)?.tagName);
      if (e.key === 'Escape' && !showExcludeModal && !showUnsavedPrompt && !confirmDialogState.isOpen) {
        e.preventDefault();
        handleRequestClose();
      } else if (e.altKey && e.key === 'ArrowLeft' && !isInput) {
        e.preventDefault();
        if (hasPrev) navigateTo(itemList[currentIndex - 1], 'prev');
      } else if (e.altKey && e.key === 'ArrowRight' && !isInput) {
        e.preventDefault();
        if (hasNext) navigateTo(itemList[currentIndex + 1], 'next');
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [hasPrev, hasNext, currentIndex, itemList, showExcludeModal, showUnsavedPrompt, confirmDialogState.isOpen, isDirty, isBusy]);

  useEffect(() => {
    const targetId = detail?.id;
    if (!showExcludeModal || !targetId) {
      setExcludeSkillRefs(null);
      return;
    }
    let active = true;
    api.getKnowledgeDeletionImpact(targetId)
      .then((impact) => { if (active) setExcludeSkillRefs(impact.skill_reference_count); })
      .catch(() => { if (active) setExcludeSkillRefs(null); });
    return () => { active = false; };
  }, [showExcludeModal, detail?.id]);

  if (!itemId) return null;

  return (
    <>
      <div
        className="modal-backdrop-animate"
        style={{
          position: 'fixed',
          inset: 0,
          backgroundColor: 'rgba(18, 26, 22, 0.42)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          zIndex: 1000,
          padding: '16px',
        }}
      >
        <div
          className="modal-panel-animate"
          style={{
            width: '1360px',
            maxWidth: '96vw',
            height: '890px',
            maxHeight: '94vh',
            backgroundColor: 'var(--bg-primary)',
            borderRadius: 'var(--radius-lg)',
            boxShadow: 'var(--shadow-lg)',
            display: 'flex',
            flexDirection: 'column',
            overflow: 'hidden',
          }}
        >
          {/* 顶栏：标题、说明与状态表达 */}
          <div
            style={{
              padding: '12px 20px 12px 24px',
              minHeight: '60px',
              borderBottom: '1px solid var(--border-color)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              backgroundColor: 'var(--bg-primary)',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
              <div
                style={{
                  width: '34px',
                  height: '34px',
                  borderRadius: 'var(--radius-md)',
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
                  <span style={{ fontSize: 'var(--font-size-section)', fontWeight: 600, color: 'var(--text-primary)' }}>
                    {isConfirmedActive ? '查看与维护知识' : '核对知识'}
                  </span>
                </div>
                <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-secondary)', marginTop: '2px' }}>
                  来源资料：
                  <strong style={{ color: 'var(--text-primary)' }}>
                    {detail?.document_title || '未知资料'}
                  </strong>
                  （{formatVersionLabel(detail?.document_version_label)}）
                </div>
              </div>
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              {/* 停用状态标识 */}
              {detail?.lifecycle_status === 'disabled' && (
                <span
                  data-testid="disabled-status-badge"
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: '4px',
                    fontSize: 'var(--font-size-xs)',
                    fontWeight: 600,
                    height: '26px',
                    padding: '0 10px',
                    borderRadius: '13px',
                    backgroundColor: 'var(--danger-bg)',
                    color: 'var(--danger-text)',
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
                    data-testid={
                      detail.active_version.review_status === 'confirmed' &&
                      (detail.active_version.index_status === 'not_indexed' || detail.active_version.index_status === 'indexing')
                        ? 'activating-status-badge'
                        : detail.active_version.review_status === 'confirmed' && detail.active_version.index_status === 'failed'
                        ? 'activation-failed-badge'
                        : 'draft-version-badge'
                    }
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: '4px',
                      fontSize: 'var(--font-size-xs)',
                      fontWeight: 600,
                      height: '26px',
                      padding: '0 10px',
                      borderRadius: '13px',
                      backgroundColor:
                        detail.active_version.review_status === 'confirmed' && detail.active_version.index_status === 'failed'
                          ? 'var(--danger-bg)'
                          : detail.active_version.review_status === 'confirmed'
                          ? 'var(--info-bg)'
                          : 'var(--warning-bg)',
                      color:
                        detail.active_version.review_status === 'confirmed' && detail.active_version.index_status === 'failed'
                          ? 'var(--danger-text)'
                          : detail.active_version.review_status === 'confirmed'
                          ? 'var(--info-text)'
                          : 'var(--warning-text)',
                    }}
                  >
                    {detail.active_version.review_status === 'confirmed' &&
                    (detail.active_version.index_status === 'not_indexed' || detail.active_version.index_status === 'indexing') ? (
                      <RotateCw size={13} className="spin-slow" />
                    ) : detail.active_version.review_status === 'confirmed' && detail.active_version.index_status === 'failed' ? (
                      <AlertCircle size={13} />
                    ) : (
                      <Clock size={13} />
                    )}
                    <span>
                      {detail.active_version.review_status === 'confirmed' && detail.active_version.index_status === 'failed'
                        ? '启用失败，旧版本仍在服务'
                        : detail.active_version.review_status === 'confirmed' &&
                          (detail.active_version.index_status === 'not_indexed' || detail.active_version.index_status === 'indexing')
                        ? '正在启用 · 建立索引'
                        : detail.active_version.review_status === 'confirmed'
                        ? '新版本已确认，等待切换'
                        : `待核对新草稿 (v${detail.active_version.version_number})`}
                    </span>
                  </span>
                  {detail.serving_version_number && (
                    <span style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)' }}>
                      （线上服务中：v{detail.serving_version_number}）
                    </span>
                  )}
                </div>
              ) : detail?.lifecycle_status === 'disabled' ? null : detail?.active_version.review_status === 'confirmed' ? (
                <span
                  data-testid="confirmed-status-badge"
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: '4px',
                    fontSize: 'var(--font-size-xs)',
                    fontWeight: 600,
                    height: '26px',
                    padding: '0 10px',
                    borderRadius: '13px',
                    backgroundColor: 'var(--success-bg)',
                    color: 'var(--brand-accent)',
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
                    fontSize: 'var(--font-size-xs)',
                    fontWeight: 600,
                    height: '26px',
                    padding: '0 10px',
                    borderRadius: '13px',
                    backgroundColor: 'var(--warning-bg)',
                    color: 'var(--warning-text)',
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
                  disabled={!isDetailReady || togglingLifecycle || isBusy}
                  className={detail.lifecycle_status === 'disabled' ? 'btn-ghost' : 'btn-danger-ghost'}
                  style={detail.lifecycle_status === 'disabled' ? { color: 'var(--brand-accent)' } : undefined}
                >
                  {detail.lifecycle_status === 'disabled' ? '恢复启用' : '停用知识'}
                </button>
              )}

              <button
                type="button"
                disabled={!isDetailReady || isBusy}
                onClick={() => {
                  setShowVersionHistory((v) => !v);
                  setHistoryPreview(null);
                }}
                className="btn-ghost"
                style={{ backgroundColor: showVersionHistory ? 'var(--bg-hover)' : undefined, color: showVersionHistory ? 'var(--text-primary)' : undefined }}
              >
                <RotateCcw size={14} />
                <span>版本历史</span>
              </button>

              {!isDetailReady && !error && (
                <span
                  data-testid="detail-loading-indicator"
                  style={{
                    fontSize: 'var(--font-size-xs)',
                    color: 'var(--text-muted)',
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: '4px',
                    padding: '4px 8px',
                    backgroundColor: 'var(--bg-secondary)',
                    borderRadius: 'var(--radius-sm)',
                    border: '1px solid var(--border-color)',
                  }}
                >
                  <RotateCw size={12} className="spin-slow" />
                  <span>完整详情加载中...</span>
                </span>
              )}

              {/* 模式切换 */}
              <button
                type="button"
                disabled={!isDetailReady || isBusy}
                onClick={() => setFullEditMode(!fullEditMode)}
                className={fullEditMode ? 'btn-primary' : 'btn-ghost'}
              >
                <Edit3 size={14} />
                <span>{fullEditMode ? '切换阅读模式' : '编辑全部内容'}</span>
              </button>

              <span className="zx-divider-v" />
              <button
                type="button"
                data-testid="close-modal-btn"
                disabled={isBusy}
                onClick={handleRequestClose}
                className="btn-ghost btn-icon"
                title="关闭"
              >
                <X size={18} />
              </button>
            </div>
          </div>

          {/* 主体两栏对照区 */}
          {!detail && loading ? (
            <div style={{ flex: 1, display: 'flex', minHeight: 0, overflow: 'hidden' }}>
              {/* 左栏骨架屏 42% */}
              <div
                style={{
                  width: '42%',
                  borderRight: '1px solid var(--border-color)',
                  backgroundColor: 'var(--bg-secondary)',
                  display: 'flex',
                  flexDirection: 'column',
                  padding: '16px 20px',
                  gap: '14px',
                }}
              >
                <div style={{ height: '20px', width: '140px', backgroundColor: 'var(--border-color)', borderRadius: '4px' }} />
                <div style={{ height: '110px', backgroundColor: '#FFFFFF', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-color)', padding: '12px' }}>
                  <div style={{ height: '14px', width: '40%', backgroundColor: 'var(--bg-sunken)', borderRadius: '4px', marginBottom: '8px' }} />
                  <div style={{ height: '12px', width: '90%', backgroundColor: 'var(--bg-sunken)', borderRadius: '4px', marginBottom: '6px' }} />
                  <div style={{ height: '12px', width: '70%', backgroundColor: 'var(--bg-sunken)', borderRadius: '4px' }} />
                </div>
                <div style={{ height: '130px', backgroundColor: '#FFFFFF', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-color)', padding: '12px' }}>
                  <div style={{ height: '14px', width: '35%', backgroundColor: 'var(--bg-sunken)', borderRadius: '4px', marginBottom: '8px' }} />
                  <div style={{ height: '12px', width: '95%', backgroundColor: 'var(--bg-sunken)', borderRadius: '4px', marginBottom: '6px' }} />
                  <div style={{ height: '12px', width: '80%', backgroundColor: 'var(--bg-sunken)', borderRadius: '4px' }} />
                </div>
              </div>
              {/* 右栏骨架屏 58% */}
              <div
                style={{
                  flex: 1,
                  display: 'flex',
                  flexDirection: 'column',
                  backgroundColor: '#FFFFFF',
                  padding: '20px 24px',
                  gap: '16px',
                }}
              >
                <div style={{ display: 'flex', gap: '8px' }}>
                  <div style={{ height: '28px', width: '72px', backgroundColor: 'var(--bg-sunken)', borderRadius: '14px' }} />
                  <div style={{ height: '28px', width: '72px', backgroundColor: 'var(--bg-sunken)', borderRadius: '14px' }} />
                </div>
                <div style={{ height: '32px', width: '60%', backgroundColor: 'var(--bg-sunken)', borderRadius: '4px' }} />
                <div style={{ height: '110px', width: '100%', backgroundColor: 'var(--bg-sunken)', borderRadius: '6px' }} />
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', flex: 1, color: 'var(--text-secondary)', fontSize: '13px', gap: '8px' }}>
                  <RotateCw size={15} className="spin-slow" />
                  <span>正在加载知识内容与原文比对数据...</span>
                </div>
              </div>
            </div>
          ) : error && !detail ? (
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
            <div
              key={itemId || detail?.id}
              className={
                switchDirection === 'next'
                  ? 'item-animate-next'
                  : switchDirection === 'prev'
                  ? 'item-animate-prev'
                  : 'item-animate-fade'
              }
              style={{
                flex: 1,
                display: 'flex',
                minHeight: 0,
                overflow: 'hidden',
                backgroundColor: 'var(--bg-secondary)',
              }}
            >
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
                    padding: '18px 24px 4px',
                    fontSize: 'var(--font-size-sm)',
                    fontWeight: 600,
                    color: 'var(--text-secondary)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                    <FileText size={15} color="var(--brand-accent)" />
                    <span>原文对照（共 {mergedEvidence.length} 处来源段落）</span>
                  </div>
                </div>

                <div
                  style={{
                    flex: 1,
                    overflowY: 'auto',
                    padding: '12px 24px 24px',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '12px',
                  }}
                >
                  {mergedEvidence && mergedEvidence.length > 0 ? (
                    mergedEvidence.map((ev, idx) => (
                      <div
                        key={ev.source_block_id || idx}
                        id={`proofread-source-${ev.source_block_id}`}
                        style={{
                          backgroundColor: 'var(--bg-primary)',
                          borderRadius: 'var(--radius-md)',
                          padding: '14px 16px 16px',
                          display: 'flex',
                          flexDirection: 'column',
                          gap: '8px',
                          boxShadow: focusedBlock === ev.source_block_id
                            ? '0 0 0 2px var(--brand-500), var(--shadow-md)'
                            : 'var(--shadow-sm)',
                          transition: 'box-shadow 160ms ease',
                        }}
                      >
                        <div
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'space-between',
                            fontSize: 'var(--font-size-xs)',
                          }}
                        >
                          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', alignItems: 'center' }}>
                            <span
                              className="zx-tag neutral"
                            >
                              段落 {ev.block_index !== undefined ? `#${ev.block_index + 1}` : ''}
                            </span>
                          </div>
                          <span
                            style={{
                              color: 'var(--text-muted)',
                              fontSize: 'var(--font-size-xs)',
                            }}
                          >
                            {formatAnchor(ev.paragraph_anchor || (ev.page_number ? `p.${ev.page_number}` : ''))}
                          </span>
                        </div>

                        {ev.heading_path && (
                          <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)' }}>
                            章节路径：
                            <span style={{ color: 'var(--text-secondary)' }}>{ev.heading_path}</span>
                          </div>
                        )}

                        {/* 只展示完整原文，不重复渲染“主要结论/处理动作/指标要求”等字段摘录。 */}
                        {/* 完整原文段落 */}
                        <div
                          style={{
                            fontSize: 'var(--font-size-base)',
                            color: 'var(--text-primary)',
                            lineHeight: 1.85,
                            whiteSpace: 'pre-wrap',
                            maxHeight: '320px',
                            overflowY: 'auto',
                          }}
                        >
                          {focusedBlock === ev.source_block_id && focusedExcerpt && ev.text_content.includes(focusedExcerpt) ? (
                            <>
                              {ev.text_content.slice(0, ev.text_content.indexOf(focusedExcerpt))}
                              <mark style={{ background: 'var(--highlight-bg)' }}>{focusedExcerpt}</mark>
                              {ev.text_content.slice(ev.text_content.indexOf(focusedExcerpt) + focusedExcerpt.length)}
                            </>
                          ) : ev.text_content}
                        </div>
                        {focusedBlock === ev.source_block_id && (
                          <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                            {focusedPrecision === 'field' ? '已定位现有字段证据摘录（仍需核对含义）' : '仅有段落级证据，不能精确定位字段'}
                          </span>
                        )}
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
                      fontSize: 'var(--font-size-xs)',
                      color: 'var(--text-muted)',
                      padding: '0 2px',
                      lineHeight: 1.7,
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
                    padding: '16px 32px 24px',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '0',
                  }}
                >
                  {showVersionHistory && detail && (
                    <div
                      data-testid="version-history-panel"
                      style={{
                        border: '1px solid var(--border-color)',
                        borderRadius: 'var(--radius-md)',
                        backgroundColor: 'var(--bg-secondary)',
                        padding: '14px',
                        display: 'flex',
                        flexDirection: 'column',
                        gap: '12px',
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                        <div>
                          <div style={{ fontSize: '13px', fontWeight: 700, color: 'var(--text-primary)' }}>
                            版本历史（只读）
                          </div>
                          <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)', marginTop: '3px' }}>
                            历史版本不能直接修改；恢复会复制历史内容生成新的待确认草稿。
                          </div>
                        </div>
                        {historyLoading && (
                          <span style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)' }}>
                            正在读取...
                          </span>
                        )}
                      </div>

                      <div style={{ display: 'flex', gap: '12px', minHeight: '180px' }}>
                        <div
                          style={{
                            width: '38%',
                            display: 'flex',
                            flexDirection: 'column',
                            gap: '6px',
                            maxHeight: '260px',
                            overflowY: 'auto',
                          }}
                        >
                          {detail.version_history.map((version) => {
                            const servingVersionNumber = detail.is_draft_version
                              ? detail.serving_version_number
                              : detail.active_version.version_number;
                            const isServing = version.version_number === servingVersionNumber;
                            const isDisplayed = version.id === detail.active_version.id;
                            return (
                              <button
                                key={version.id}
                                type="button"
                                onClick={() => handleViewHistory(version.id)}
                                style={{
                                  textAlign: 'left',
                                  padding: '8px 10px',
                                  borderRadius: 'var(--radius-sm)',
                                  border: '1px solid var(--border-color)',
                                  backgroundColor: historyPreview?.version?.id === version.id ? '#FFFFFF' : 'transparent',
                                  cursor: 'pointer',
                                  color: 'var(--text-primary)',
                                }}
                              >
                                <div style={{ fontSize: '12px', fontWeight: 600 }}>
                                  v{version.version_number} · {version.title}
                                </div>
                                <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '3px' }}>
                                  {isServing ? '当前服务' : isDisplayed && detail.is_draft_version ? '待切换' : '历史'}
                                  {' · '}
                                  {version.review_status === 'confirmed' ? '已确认' : '待确认'}
                                  {' · '}
                                  {version.index_status}
                                </div>
                              </button>
                            );
                          })}
                        </div>

                        <div
                          style={{
                            width: '62%',
                            backgroundColor: '#FFFFFF',
                            border: '1px solid var(--border-color)',
                            borderRadius: 'var(--radius-sm)',
                            padding: '12px',
                            overflowY: 'auto',
                            maxHeight: '260px',
                          }}
                        >
                          {historyPreview ? (
                            <>
                              <div style={{ fontSize: '13px', fontWeight: 700, color: 'var(--text-primary)' }}>
                                v{historyPreview.version.version_number} · {historyPreview.version.title}
                              </div>
                              <div style={{ fontSize: '12px', color: 'var(--text-muted)', margin: '5px 0 10px' }}>
                                来源：{historyPreview.document_file_name} / {historyPreview.document_version_label}
                              </div>
                              <div style={{ fontSize: '12px', color: 'var(--text-primary)', lineHeight: 1.6, whiteSpace: 'pre-wrap' }}>
                                {historyPreview.version.statement || historyPreview.version.content}
                              </div>
                              <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '10px' }}>
                                来源证据 {historyPreview.evidence?.length || 0} 条 · 此视图只读
                              </div>
                              {!historyPreview.is_current && !historyPreview.is_pending && (
                                <button
                                  type="button"
                                  onClick={() => handleRestoreHistory(historyPreview.version.id)}
                                  disabled={restoringHistory}
                                  style={{
                                    marginTop: '10px',
                                    height: '30px',
                                    padding: '0 10px',
                                    borderRadius: 'var(--radius-sm)',
                                    border: '1px solid var(--border-color)',
                                    backgroundColor: '#FFFFFF',
                                    cursor: restoringHistory ? 'not-allowed' : 'pointer',
                                    fontSize: '12px',
                                    fontWeight: 600,
                                  }}
                                >
                                  {restoringHistory ? '正在创建草稿...' : '恢复为新草稿'}
                                </button>
                              )}
                            </>
                          ) : (
                            <div style={{ fontSize: '12px', color: 'var(--text-muted)', lineHeight: 1.6 }}>
                              选择左侧版本查看当时的正文、来源文件版本和证据数量。
                            </div>
                          )}
                        </div>
                      </div>
                    </div>
                  )}

                  {/* 每次字段修改后的无保存校验状态，避免将旧警告误认为当前结果。 */}
                  {isDirty && (
                    <div data-testid="draft-validation-status" style={{
                      padding: '9px 12px', borderRadius: 'var(--radius-sm)',
                      border: '1px solid var(--border-color)', fontSize: 12,
                      background: previewStatus === 'error' ? 'var(--warning-bg)' : 'var(--bg-secondary)',
                      color: 'var(--text-secondary)', display: 'flex', gap: 8, alignItems: 'center',
                    }}>
                      {previewStatus === 'ready'
                        ? (qualityCheck.blocking.length
                          ? '已按当前修改重新校验（未保存），请核对下方阻断问题。'
                          : '已按当前修改重新校验：未发现自动质检问题（未保存）。')
                        : previewStatus === 'error'
                          ? `自动校验失败：${previewError}。当前不显示旧警告；可重试或保存草稿。`
                          : previewStatus === 'checking'
                            ? '正在核对当前修改…此前的旧警告已暂时隐藏。'
                            : '修改后即将自动核对…此前的旧警告已暂时隐藏。'}
                      {previewStatus === 'error' && (
                        <button type="button" className="btn-secondary"
                          onClick={() => setPreviewNonce(v => v + 1)} style={{ marginLeft: 'auto', fontSize: 12 }}>
                          重新校验
                        </button>
                      )}
                    </div>
                  )}

                  {/* 1. 质检提示卡片（清晰区分三级） */}
                  {/* 1.1 阻断启用问题 */}
                  {(!isConfirmedActive || isDirty) && qualityCheck.blocking.length > 0 && (
                    <div
                      style={{
                        backgroundColor: 'var(--danger-bg)',
                        border: '1px solid var(--danger-border)',
                        borderRadius: 'var(--radius-md)',
                        padding: '12px 16px',
                        display: 'flex',
                        flexDirection: 'column',
                        gap: '6px',
                      }}
                    >
                      <div
                        style={{
                          fontSize: 'var(--font-size-xs)',
                          fontWeight: 700,
                          color: 'var(--danger-text)',
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
                          fontSize: 'var(--font-size-xs)',
                          color: 'var(--danger-text)',
                          lineHeight: 1.6,
                        }}
                      >
                        {qualityCheck.blocking.map((msg, idx) => (
                          <li key={idx}>{msg}</li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {detail?.active_version.review_status === 'pending_review' &&
                    detail.active_version.created_by === 'system_extractor' &&
                    !detail.active_version.reviewed_by &&
                    qualityFlags.some(flag => /结构化|指标堆积|指标关联需核对|条件对应需核对|例外对应需核对/.test(flag)) && (
                    <div style={{ display: 'flex', gap: 10, alignItems: 'center', background: 'var(--bg-secondary)',
                      border: '1px solid var(--border-color)', borderRadius: 'var(--radius-sm)', padding: '8px 12px' }}>
                      <span style={{ flex: 1, fontSize: 12, color: 'var(--text-secondary)' }}>
                        当前结果需要核对。可手动重试一次，或点击下方具体字段修改；重抽不会自动确认知识。
                      </span>
                      <button type="button" className="btn-secondary" disabled={isDirty || retryingStructure}
                        onClick={handleRetryStructure} title={isDirty ? '请先保存或放弃当前编辑' : '仅重抽尚未人工编辑的候选'}>
                        {retryingStructure ? '正在重新整理…' : '手动重抽'}
                      </button>
                    </div>
                  )}

                  {/* Jev 独立分类建议与语义质检，不参与自动确认、权限或检索资格判断。 */}
                  {detail?.jev_evaluation && (() => {
                    const jev = detail.jev_evaluation;
                    const stateLabel: Record<string, string> = {
                      not_started: '尚未执行', queued: '等待质检', running: '质检中', completed: '质检完成',
                      failed: '质检失败', not_configured: '未配置 API Key', disabled: '功能已关闭', stale: '评估已过期',
                    };
                    const choiceLabel: Record<string, string> = {
                      no_issue: '未发现问题', suspected_issue: '疑似有问题', insufficient_evidence: '证据不足', not_applicable: '不适用',
                    };
                    const actionable = jev.status === 'failed' || jev.status === 'stale' || jev.status === 'not_started' || jev.status === 'not_configured';
                    const needsAttention = jev.review_priority === 'high' || jev.review_priority === 'medium' || actionable;
                    const attentionCount = (jev.answers || []).filter(a => !a.ignored && ['suspected_issue', 'insufficient_evidence'].includes(a.choice || '')).length;
                    const compactSummary = jev.status === 'completed'
                      ? (needsAttention ? `${attentionCount || jev.priority_reasons?.length || 1} 项需要关注` : '未发现需优先核对的问题')
                      : (jev.error_message || stateLabel[jev.status] || '质检状态待确认');
                    return (
                      <div data-testid="jev-quality-panel" style={{
                        border: `1px solid ${needsAttention ? 'var(--warning-border)' : 'var(--success-border)'}`,
                        borderRadius: 'var(--radius-md)', background: needsAttention ? 'var(--warning-bg)' : 'var(--success-bg)',
                        overflow: 'hidden', flexShrink: 0,
                      }}>
                        <button type="button" data-testid="jev-quality-toggle" aria-expanded={jevExpanded}
                          onClick={() => setJevExpanded(value => !value)} style={{
                            width: '100%', border: 0, background: 'transparent', padding: '11px 14px',
                            display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', textAlign: 'left',
                          }}>
                          <Shield size={15} color={needsAttention ? 'var(--warning-text)' : 'var(--brand-accent)'} />
                          <strong style={{ fontSize: 13 }}>Jev 分类与语义质检</strong>
                          <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>{stateLabel[jev.status] || jev.status}</span>
                          <span style={{ marginLeft: 'auto', fontSize: 12, color: needsAttention ? 'var(--warning-text)' : 'var(--text-secondary)' }}>
                            {compactSummary}
                          </span>
                          {jev.review_priority && jev.review_priority !== 'not_available' && (
                            <span style={{ fontSize: 12, fontWeight: 700, color: jev.review_priority === 'high' ? 'var(--danger-text)' : jev.review_priority === 'medium' ? 'var(--warning-text)' : 'var(--brand-accent)' }}>
                              {jev.review_priority === 'high' ? '优先核对' : jev.review_priority === 'medium' ? '建议核对' : '常规核对'}
                            </span>
                          )}
                          <ChevronDown size={15} color="var(--text-muted)" style={{
                            transform: jevExpanded ? 'rotate(180deg)' : 'rotate(0deg)', transition: 'transform 160ms ease',
                          }} />
                        </button>
                        {jevExpanded && <div data-testid="jev-quality-details" style={{
                          borderTop: '1px solid var(--border-color)', padding: '12px 14px',
                          display: 'flex', flexDirection: 'column', gap: 10,
                        }}>
                        {jev.status === 'completed' ? (<>
                          <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                            分类建议：<strong>{jev.classification_suggestion || '无法确定'}</strong>
                            {jev.classification_disagrees ? `，与当前分类「${primaryCategory || '待分类'}」存在分歧` : '，与当前分类未发现分歧'}。
                            该建议不会覆盖人工分类。
                          </div>
                          <div style={{ display: 'grid', gap: 7 }}>
                            {jev.answers.filter(a => a.question_id !== 'primary_category').map((answer) => {
                              const selectedProbability = answer.choice ? answer.probabilities?.[answer.choice] : undefined;
                              return (
                                <div key={answer.question_id} style={{
                                  padding: '8px 10px', borderRadius: 'var(--radius-sm)', background: answer.ignored ? 'var(--bg-sunken)' : '#FFFFFF',
                                  border: `1px solid ${answer.choice === 'suspected_issue' && !answer.ignored ? 'var(--danger-border)' : 'var(--border-color)'}`,
                                  opacity: answer.ignored ? 0.68 : 1,
                                }}>
                                  <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                                    <span style={{ fontSize: 12, fontWeight: 600, flex: 1 }}>{answer.question_label}</span>
                                    <span style={{ fontSize: 12, color: answer.choice === 'suspected_issue' ? 'var(--danger-text)' : 'var(--text-secondary)' }}>
                                      {choiceLabel[answer.choice || ''] || answer.choice}
                                    </span>
                                  </div>
                                  <div style={{ marginTop: 4, fontSize: 12, color: 'var(--text-muted)', display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                                    <span>涉及字段：{answer.relevant_fields.join('、')}</span>
                                    {selectedProbability !== undefined && <span>选项概率 {(selectedProbability * 100).toFixed(0)}%</span>}
                                    {answer.confidence !== null && answer.confidence !== undefined && <span>分布置信度 {(answer.confidence * 100).toFixed(0)}%</span>}
                                    <button type="button" onClick={() => locateEvidence(answer.relevant_fields[0])} style={{ border: 0, padding: 0, background: 'none', color: 'var(--brand-accent)', cursor: 'pointer', fontSize: 12 }}>查看原文</button>
                                    {!answer.ignored && ['suspected_issue', 'insufficient_evidence'].includes(answer.choice || '') && (
                                      <button type="button" onClick={() => handleIgnoreJevQuestion(answer.question_id)} disabled={jevBusy}
                                        style={{ border: 0, padding: 0, background: 'none', color: 'var(--text-secondary)', cursor: jevBusy ? 'not-allowed' : 'pointer', fontSize: 12 }}>忽略提示</button>
                                    )}
                                    {answer.ignored && <span>已忽略：{answer.ignore_reason}</span>}
                                  </div>
                                </div>
                              );
                            })}
                          </div>
                          <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                            实际模型 {jev.actual_model || jev.requested_model}，问题版本 {jev.question_definition_version}。审核阈值尚未经过业务样本校准，高置信度不等于同等准确率。
                          </div>
                        </>) : (
                          <div style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 12, color: jev.status === 'failed' ? 'var(--danger-text)' : 'var(--text-secondary)' }}>
                            <span>{jev.error_message || (jev.status === 'disabled' ? '关闭时原有抽取和人工审核不受影响。' : '质检未完成，不能视为检查通过。')}</span>
                            {actionable && <button type="button" className="btn-secondary" onClick={handleRetryJev} disabled={jevBusy}>{jevBusy ? '正在提交…' : '重新质检'}</button>}
                          </div>
                        )}
                        </div>}
                      </div>
                    );
                  })()}

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
                        fontSize: 'var(--font-size-xs)',
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
                      padding: '20px 0',
                      borderBottom: '1px solid var(--border-color)',
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
                        <span style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)', display: 'block' }}>
                          知识条目标题
                        </span>
                        {titleEditing ? (
                          <input
                            type="text"
                            data-testid="input-title"
                            disabled={!canEdit}
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
                              backgroundColor: !canEdit ? 'var(--bg-secondary)' : '#FFFFFF',
                              cursor: !canEdit ? 'not-allowed' : 'text',
                            }}
                          />
                        ) : (
                          <div
                            style={{
                              fontSize: '18px',
                              fontWeight: 600,
                              color: 'var(--text-primary)',
                              marginTop: '4px',
                              lineHeight: 1.45,
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
                          disabled={!canEdit}
                          onClick={() =>
                            setEditingSection(editingSection === 'title' ? null : 'title')
                          }
                          style={{
                            fontSize: 'var(--font-size-xs)',
                            color: 'var(--text-secondary)',
                            background: 'none',
                            border: 'none',
                            cursor: !canEdit ? 'not-allowed' : 'pointer',
                            opacity: !canEdit ? 0.6 : 1,
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
                        <span style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-secondary)' }}>
                          所属分类：
                        </span>
                        {categoryEditing ? (
                          <select
                            data-testid="select-primary-category"
                            disabled={!canEdit}
                            value={primaryCategory}
                            onChange={(e) => {
                              setPrimaryCategory(e.target.value as PrimaryCategory);
                              markDirty();
                            }}
                            style={{
                              height: '30px',
                              padding: '0 8px',
                              fontSize: 'var(--font-size-xs)',
                              fontWeight: 600,
                              borderRadius: 'var(--radius-sm)',
                              border: '1px solid var(--border-color)',
                              backgroundColor: !canEdit ? 'var(--bg-secondary)' : (primaryCategory ? '#FFFFFF' : 'var(--warning-bg)'),
                              color: primaryCategory
                                ? 'var(--text-primary)'
                                : 'var(--warning-text)',
                              outline: 'none',
                              cursor: !canEdit ? 'not-allowed' : 'pointer',
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
                              fontSize: 'var(--font-size-xs)',
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
                              fontSize: 'var(--font-size-xs)',
                              fontWeight: 600,
                              padding: '3px 10px',
                              borderRadius: 'var(--radius-sm)',
                              backgroundColor: 'var(--warning-bg)',
                              color: 'var(--warning-text)',
                              border: '1px solid var(--warning-border)',
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
                          disabled={!canEdit}
                          onClick={() =>
                            setEditingSection(editingSection === 'category' ? null : 'category')
                          }
                          style={{
                            fontSize: 'var(--font-size-xs)',
                            color: 'var(--text-secondary)',
                            background: 'none',
                            border: 'none',
                            cursor: !canEdit ? 'not-allowed' : 'pointer',
                            opacity: !canEdit ? 0.6 : 1,
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
                      padding: '20px 0',
                      borderBottom: '1px solid var(--border-color)',
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
                          fontSize: 'var(--font-size-xs)',
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
                          disabled={!canEdit}
                          onClick={() =>
                            setEditingSection(editingSection === 'statement' ? null : 'statement')
                          }
                          style={{
                            fontSize: 'var(--font-size-xs)',
                            color: 'var(--text-secondary)',
                            background: 'none',
                            border: 'none',
                            cursor: !canEdit ? 'not-allowed' : 'pointer',
                            opacity: !canEdit ? 0.6 : 1,
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

                    {statementEditing ? (
                      <textarea
                        rows={3}
                        disabled={!canEdit}
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
                          backgroundColor: !canEdit ? 'var(--bg-secondary)' : '#FFFFFF',
                          cursor: !canEdit ? 'not-allowed' : 'text',
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
                      padding: '20px 0',
                      borderBottom: '1px solid var(--border-color)',
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
                          fontSize: 'var(--font-size-sm)',
                          fontWeight: 600,
                          color: 'var(--text-secondary)',
                        }}
                      >
                        {primaryCategory
                          ? `${primaryCategory}结构要素`
                          : '核心业务结构要素（待分类）'}
                      </span>
                      {!fullEditMode && (
                        <button
                          type="button"
                          disabled={!canEdit}
                          onClick={() =>
                            setEditingSection(editingSection === 'structure' ? null : 'structure')
                          }
                          style={{
                            background: 'none',
                            border: 'none',
                            fontSize: 'var(--font-size-xs)',
                            color: 'var(--brand-accent)',
                            cursor: !canEdit ? 'not-allowed' : 'pointer',
                            opacity: !canEdit ? 0.6 : 1,
                            display: 'flex',
                            alignItems: 'center',
                            gap: '3px',
                          }}
                        >
                          <Edit3 size={12} />
                          <span>{editingSection === 'structure' ? '完成' : '修改'}</span>
                        </button>
                      )}
                    </div>

                    {!structureEditing ? (
                      <StructuredReview item={{ statement, primary_category: primaryCategory || null, subject,
                        conditions, actions, exceptions, metric_definition: metricDef, case_details: caseDetails }}
                        hideStatement onLocate={locateEvidence} onEdit={canEdit ? () => setEditingSection('structure') : undefined}
                        sourcePrecision={detail?.evidence?.some(ev => ev.excerpt && ev.text_content && ev.excerpt !== ev.text_content && ev.accuracy_level === 'exact') ? 'field' : 'paragraph'} />
                    ) : (<>
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
                              fontSize: 'var(--font-size-xs)',
                              fontWeight: 600,
                              color: 'var(--text-secondary)',
                              marginBottom: '6px',
                            }}
                          >
                            谁负责（责任主体）
                          </div>
                          {structureEditing ? (
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
                                fontSize: 'var(--font-size-xs)',
                                borderRadius: 'var(--radius-sm)',
                                border: '1px solid var(--border-color)',
                              }}
                            />
                          ) : (
                            <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-primary)' }}>
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
                                  fontSize: 'var(--font-size-xs)',
                                  fontWeight: 600,
                                  color: 'var(--text-secondary)',
                                }}
                              >
                                什么时候适用（前提条件）
                              </span>
                            </div>
                            {structureEditing ? (
                              <EditableStringList
                                items={conditions}
                                onChange={(next) => {
                                  setConditions(next);
                                  markDirty();
                                }}
                                placeholder="输入前提条件..."
                                emptyNotice="原文未设特殊前置条件"
                              />
                            ) : conditions.length > 0 ? (
                              conditions.map((cond, idx) => (
                                <div
                                  key={idx}
                                  style={{
                                    fontSize: 'var(--font-size-xs)',
                                    color: 'var(--text-primary)',
                                    lineHeight: 1.6,
                                    padding: '2px 0',
                                  }}
                                >
                                  {cond}
                                </div>
                              ))
                            ) : (
                              <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)' }}>
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
                                  fontSize: 'var(--font-size-xs)',
                                  fontWeight: 600,
                                  color: 'var(--text-secondary)',
                                }}
                              >
                                应该怎么做（操作步骤）
                              </span>
                            </div>
                            {structureEditing ? (
                              <EditableStringList
                                items={actions}
                                onChange={(next) => {
                                  setActions(next);
                                  markDirty();
                                }}
                                placeholder="输入操作步骤..."
                                emptyNotice="（按核心陈述执行）"
                              />
                            ) : actions.length > 0 ? (
                              actions.map((act, idx) => (
                                <div
                                  key={idx}
                                  style={{
                                    fontSize: 'var(--font-size-xs)',
                                    color: 'var(--text-primary)',
                                    lineHeight: 1.6,
                                    padding: '2px 0',
                                  }}
                                >
                                  {act}
                                </div>
                              ))
                            ) : (
                              <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)' }}>
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
                                fontSize: 'var(--font-size-xs)',
                                fontWeight: 600,
                                color: 'var(--text-secondary)',
                              }}
                            >
                              特殊情况与例外（免责或禁止）
                            </span>
                          </div>
                          {structureEditing ? (
                            <EditableStringList
                              items={exceptions}
                              onChange={(next) => {
                                setExceptions(next);
                                markDirty();
                              }}
                              placeholder="输入特殊情况或例外..."
                              emptyNotice="原文未提及特殊例外或禁止事项"
                            />
                          ) : exceptions.length > 0 ? (
                            exceptions.map((exc, idx) => (
                              <div
                                key={idx}
                                style={{
                                  fontSize: 'var(--font-size-xs)',
                                  color: 'var(--text-primary)',
                                  lineHeight: 1.6,
                                  padding: '2px 0',
                                }}
                              >
                                {exc}
                              </div>
                            ))
                          ) : (
                            <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)' }}>
                              原文未提及特殊例外或禁止事项
                            </div>
                          )}
                        </div>
                      </div>
                    )}

                    {/* 指标数据 */}
                    {primaryCategory === '指标数据' && structureEditing && (
                      <MetricRowsEditor value={metricDef} onChange={(value) => { setMetricDef(value); markDirty(); }}
                        title={title} />
                    )}

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
                        {!metricDef?.rows?.length && <div
                          style={{
                            display: 'grid',
                            gridTemplateColumns: 'repeat(3, 1fr)',
                            gap: '12px',
                          }}
                        >
                          <div>
                            <label
                              style={{
                                fontSize: 'var(--font-size-xs)',
                                color: 'var(--text-secondary)',
                                display: 'block',
                                marginBottom: '4px',
                              }}
                            >
                              计量单位
                            </label>
                            {structureEditing ? (
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
                                  fontSize: 'var(--font-size-xs)',
                                  padding: '0 8px',
                                  borderRadius: 'var(--radius-sm)',
                                  border: '1px solid var(--border-color)',
                                }}
                              />
                            ) : (
                              <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-primary)', lineHeight: 1.6 }}>
                                {metricDef?.unit || '原文未提供'}
                              </div>
                            )}
                          </div>
                          <div>
                            <label
                              style={{
                                fontSize: 'var(--font-size-xs)',
                                color: 'var(--text-secondary)',
                                display: 'block',
                                marginBottom: '4px',
                              }}
                            >
                              统计期间
                            </label>
                            {structureEditing ? (
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
                                  fontSize: 'var(--font-size-xs)',
                                  padding: '0 8px',
                                  borderRadius: 'var(--radius-sm)',
                                  border: '1px solid var(--border-color)',
                                }}
                              />
                            ) : (
                              <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-primary)', lineHeight: 1.6 }}>
                                {metricDef?.period || '原文未提供'}
                              </div>
                            )}
                          </div>
                          <div>
                            <label
                              style={{
                                fontSize: 'var(--font-size-xs)',
                                color: 'var(--text-secondary)',
                                display: 'block',
                                marginBottom: '4px',
                              }}
                            >
                              达标基准 / 警戒线
                            </label>
                            {structureEditing ? (
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
                                  fontSize: 'var(--font-size-xs)',
                                  padding: '0 8px',
                                  borderRadius: 'var(--radius-sm)',
                                  border: '1px solid var(--border-color)',
                                }}
                              />
                            ) : (
                              <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-primary)', lineHeight: 1.6 }}>
                                {metricDef?.criteria || '原文未提供'}
                              </div>
                            )}
                          </div>
                        </div>}
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
                                fontSize: 'var(--font-size-xs)',
                                fontWeight: 600,
                                color: 'var(--text-secondary)',
                              }}
                            >
                              应该怎么做（具体步骤）
                            </span>
                            {structureEditing && (
                              <button
                                type="button"
                                onClick={() => {
                                  setActions([...actions, '']);
                                  markDirty();
                                }}
                                style={{
                                  background: 'none',
                                  border: 'none',
                                  fontSize: 'var(--font-size-xs)',
                                  color: 'var(--brand-accent)',
                                  cursor: 'pointer',
                                }}
                              >
                                + 增加步骤
                              </button>
                            )}
                          </div>
                          {actions.length > 0 ? (
                            actions.map((act, idx) => (
                              <div key={idx} style={{ marginBottom: '4px' }}>
                                {structureEditing ? (
                                  <div style={{ display: 'flex', gap: '6px' }}>
                                    <span
                                      style={{
                                        fontSize: 'var(--font-size-xs)',
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
                                        fontSize: 'var(--font-size-xs)',
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
                                ) : (
                                  <div
                                    style={{
                                      display: 'flex',
                                      gap: '8px',
                                      fontSize: 'var(--font-size-xs)',
                                      color: 'var(--text-primary)',
                                      lineHeight: 1.6,
                                      padding: '2px 0',
                                    }}
                                  >
                                    <span style={{ color: 'var(--text-muted)', minWidth: '18px' }}>{idx + 1}.</span>
                                    <span>{act}</span>
                                  </div>
                                )}
                              </div>
                            ))
                          ) : (
                            <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)' }}>
                              原文未列出具体步骤
                            </div>
                          )}
                        </div>
                      </div>
                    )}

                    {/* 案例：阅读与章节视图共享，编辑时四项均可修改 */}
                    {primaryCategory === '项目案例' && (
                      <div style={{ padding: 14, background: '#FFFFFF', border: '1px solid var(--border-color)',
                        borderRadius: 'var(--radius-sm)', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                        {([
                          ['background', '项目背景与面临挑战'],
                          ['actions', '采取的措施'],
                          ['results', '实际结果'],
                          ['limitations', '适用限制'],
                        ] as const).map(([key, label]) => (
                          <label key={key} style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-secondary)' }}>
                            {label}
                            <textarea rows={3} value={caseDetails?.[key] || ''} placeholder="原文未提供时可留空"
                              onChange={(event) => {
                                setCaseDetails({ background: '', actions: '', results: '', limitations: '',
                                  ...caseDetails, [key]: event.target.value });
                                markDirty();
                              }}
                              style={{ width: '100%', padding: '6px 8px', marginTop: 4, boxSizing: 'border-box',
                                fontSize: 'var(--font-size-xs)', borderRadius: 'var(--radius-sm)',
                                border: '1px solid var(--border-color)', resize: 'vertical' }} />
                          </label>
                        ))}
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
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                          <div
                            style={{
                              fontSize: 'var(--font-size-xs)',
                              fontWeight: 600,
                              color: 'var(--text-secondary)',
                            }}
                          >
                            应该怎么做（建议要点）
                          </div>
                          {structureEditing && (
                            <button
                              type="button"
                              onClick={() => {
                                setActions([...actions, '']);
                                markDirty();
                              }}
                              style={{
                                background: 'none',
                                border: 'none',
                                fontSize: 'var(--font-size-xs)',
                                color: 'var(--brand-accent)',
                                cursor: 'pointer',
                              }}
                            >
                              + 添加建议
                            </button>
                          )}
                        </div>
                        {actions.length > 0 ? (
                          actions.map((act, idx) => (
                            <div key={idx}>
                              {structureEditing ? (
                                <div style={{ display: 'flex', gap: '6px' }}>
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
                                      fontSize: 'var(--font-size-xs)',
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
                              ) : (
                                <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-primary)', lineHeight: 1.6 }}>
                                  {act}
                                </div>
                              )}
                            </div>
                          ))
                        ) : (
                          <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)' }}>
                            原文未列出建议要点
                          </div>
                        )}
                      </div>
                    )}

                    {/* 待分类提示 */}
                    {!primaryCategory && (
                      <div
                        style={{
                          padding: '16px',
                          backgroundColor: '#FFFFFF',
                          borderRadius: 'var(--radius-sm)',
                          border: '1px dashed var(--warning-border)',
                          fontSize: 'var(--font-size-xs)',
                          color: 'var(--warning-text)',
                          textAlign: 'center',
                        }}
                      >
                        请在上方指定具体分类（如「制度与标准」或「指标数据」），以启用针对性结构要素核对。
                      </div>
                    )}
                    </>)}
                  </div>

                  {/* 5. 业务标签与权限 */}
                  <div
                    style={{
                      padding: '20px 0',
                      borderBottom: '1px solid var(--border-color)',
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
                          fontSize: 'var(--font-size-xs)',
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
                          disabled={!canEdit}
                          onClick={() => setIsTagsEditing(!isTagsEditing)}
                          className="btn-secondary"
                          style={{
                            fontSize: 'var(--font-size-xs)',
                            height: '26px',
                            padding: '0 8px',
                            color: 'var(--text-secondary)',
                            cursor: !canEdit ? 'not-allowed' : 'pointer',
                            opacity: !canEdit ? 0.6 : 1,
                          }}
                        >
                          {isTagsEditing ? '完成收拢' : '修改适用范围与权限'}
                        </button>
                      )}
                    </div>

                    {/* 收拢状态下的紧凑概览 */}
                    {!tagsEditing ? (
                      <div
                        style={{
                          display: 'grid',
                          gridTemplateColumns: 'repeat(2, 1fr)',
                          gap: '8px',
                          fontSize: 'var(--font-size-xs)',
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
                                fontSize: 'var(--font-size-xs)',
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
                                fontSize: 'var(--font-size-xs)',
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
                                fontSize: 'var(--font-size-xs)',
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

                        {/* M01-C3：从场景目录选择业务场景（无目录时不显示） */}
                        {sceneCatalogNames.length > 0 && (
                          <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }} data-testid="scene-catalog-picker">
                            <span style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)', flexShrink: 0 }}>
                              从场景目录选择
                            </span>
                            <select
                              value=""
                              disabled={!canEdit}
                              aria-label="从场景目录选择业务场景"
                              onChange={(e) => {
                                const val = e.target.value;
                                if (val && !businessScenes.includes(val)) {
                                  setBusinessScenes([...businessScenes, val]);
                                  markDirty();
                                }
                              }}
                              style={{
                                flex: 1,
                                height: '28px',
                                fontSize: 'var(--font-size-xs)',
                                padding: '0 6px',
                                cursor: !canEdit ? 'not-allowed' : 'pointer',
                                backgroundColor: !canEdit ? 'var(--bg-secondary)' : '#FFFFFF',
                              }}
                            >
                              <option value="">选择业务场景…</option>
                              {sceneCatalogNames
                                .filter((name) => !businessScenes.includes(name))
                                .map((name) => (
                                  <option key={name} value={name}>
                                    {name}
                                  </option>
                                ))}
                            </select>
                          </div>
                        )}

                        {/* 添加标签输入栏 */}
                        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                          <select
                            value={tagType}
                            disabled={!canEdit}
                            onChange={(e) => setTagType(e.target.value as any)}
                            style={{
                              height: '28px',
                              fontSize: 'var(--font-size-xs)',
                              padding: '0 6px',
                              cursor: !canEdit ? 'not-allowed' : 'pointer',
                              backgroundColor: !canEdit ? 'var(--bg-secondary)' : '#FFFFFF',
                            }}
                          >
                            <option value="scene">业务场景</option>
                            <option value="customer">客户类型</option>
                            <option value="problem">问题标签</option>
                          </select>
                          <input
                            type="text"
                            placeholder={
                              tagType === 'scene' && sceneCatalogNames.length > 0
                                ? '没有合适的场景时，在这里手动输入...'
                                : '输入新标签按回车添加...'
                            }
                            value={tagInput}
                            disabled={!canEdit}
                            onChange={(e) => setTagInput(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter') handleAddTag();
                            }}
                            style={{
                              flex: 1,
                              height: '28px',
                              fontSize: 'var(--font-size-xs)',
                              padding: '0 8px',
                              borderRadius: 'var(--radius-sm)',
                              border: '1px solid var(--border-color)',
                              cursor: !canEdit ? 'not-allowed' : 'text',
                              backgroundColor: !canEdit ? 'var(--bg-secondary)' : '#FFFFFF',
                            }}
                          />
                          <button
                            type="button"
                            className="btn-secondary"
                            disabled={!canEdit || !tagInput.trim()}
                            onClick={handleAddTag}
                            style={{
                              height: '28px',
                              fontSize: 'var(--font-size-xs)',
                              padding: '0 10px',
                              cursor: (!canEdit || !tagInput.trim()) ? 'not-allowed' : 'pointer',
                              opacity: (!canEdit || !tagInput.trim()) ? 0.6 : 1,
                            }}
                          >
                            添加
                          </button>
                        </div>

                        {/* 权限选择 */}
                        <div
                          style={{
                            paddingTop: '8px',
                            borderTop: '1px solid var(--bg-secondary)',
                          }}
                        >
                          <div
                            style={{
                              display: 'flex',
                              alignItems: 'center',
                              justifyContent: 'space-between',
                            }}
                          >
                            <span style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-secondary)' }}>
                              访问权限控制
                            </span>
                            <select
                              value={detail?.document_access_scope === 'admin_only' ? 'admin_only' : accessScope}
                              disabled={!canEdit || detail?.document_access_scope === 'admin_only'}
                              onChange={(e) => {
                                setAccessScope(e.target.value as any);
                                markDirty();
                              }}
                              style={{
                                height: '28px',
                                fontSize: 'var(--font-size-xs)',
                                padding: '0 8px',
                                borderRadius: 'var(--radius-sm)',
                                border: '1px solid var(--border-color)',
                                backgroundColor: (!canEdit || detail?.document_access_scope === 'admin_only') ? 'var(--bg-secondary)' : '#FFFFFF',
                                cursor: (!canEdit || detail?.document_access_scope === 'admin_only') ? 'not-allowed' : 'pointer',
                              }}
                            >
                              <option value="admin_only">仅管理员可见</option>
                              <option value="org_internal" disabled={detail?.document_access_scope === 'admin_only'}>
                                {detail?.document_access_scope === 'admin_only'
                                  ? '企业全员可用（来源文件为专享，不可放宽）'
                                  : '企业内部全员可用'}
                              </option>
                            </select>
                          </div>
                          {detail?.document_access_scope === 'admin_only' && (
                            <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '4px', textAlign: 'right' }}>
                              来源文件为管理员专享，知识条目权限不能比文件更开放
                            </div>
                          )}
                        </div>
                      </>
                    )}
                  </div>
                </div>

                {/* 底部操作栏（支持上一条/下一条与连续核对） */}
                <div
                  style={{
                    padding: '0 24px',
                    minHeight: '64px',
                    borderTop: '1px solid var(--border-color)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    gap: '12px',
                    backgroundColor: 'var(--bg-primary)',
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
                          className="btn-ghost"
                          onClick={() => hasPrev && navigateTo(itemList[currentIndex - 1], 'prev')}
                          disabled={!hasPrev || isBusy}
                          title="上一条 (Alt + ←)"
                          style={{
                            padding: '0 8px',
                            gap: '2px',
                            cursor: (!hasPrev || isBusy) ? 'not-allowed' : 'pointer',
                            opacity: (!hasPrev || isBusy) ? 0.5 : 1,
                          }}
                        >
                          <ChevronLeft size={15} />
                          <span>上一条</span>
                        </button>

                        <span
                          style={{
                            fontSize: 'var(--font-size-sm)',
                            color: 'var(--text-secondary)',
                            padding: '0 4px',
                            whiteSpace: 'nowrap',
                          }}
                        >
                          第 {currentIndex + 1} / {itemList.length} 条
                        </span>

                        <button
                          type="button"
                          className="btn-ghost"
                          onClick={() => hasNext && navigateTo(itemList[currentIndex + 1], 'next')}
                          disabled={!hasNext || isBusy}
                          title="下一条 (Alt + →)"
                          style={{
                            padding: '0 8px',
                            gap: '2px',
                            cursor: (!hasNext || isBusy) ? 'not-allowed' : 'pointer',
                            opacity: (!hasNext || isBusy) ? 0.5 : 1,
                          }}
                        >
                          <span>下一条</span>
                          <ChevronRight size={15} />
                        </button>
                      </div>
                    )}

                    {/* 暂不处理（跳到下一条，仅在待核对模式下显示） */}
                    {!isConfirmedActive && (
                      <button
                        type="button"
                        className="btn-ghost"
                        onClick={handleSkip}
                        disabled={isBusy}
                        title="暂不保存修改，直接看下一条"
                        style={{
                          fontSize: 'var(--font-size-xs)',
                          padding: '0 10px',
                          cursor: isBusy ? 'not-allowed' : 'pointer',
                          opacity: isBusy ? 0.5 : 1,
                        }}
                      >
                        暂不处理
                      </button>
                    )}

                    {/* 不收录（排除，仅在待核对模式下显示） */}
                    {!isConfirmedActive && (
                      <button
                        type="button"
                        onClick={() => setShowExcludeModal(true)}
                        disabled={!isDetailReady || isBusy || deleting}
                        className="btn-danger-ghost"
                        title={!isDetailReady ? '完整详情加载中...' : '排除此条知识候选，不计入待核对队列'}
                        style={{
                          gap: '4px',
                          cursor: (!isDetailReady || isBusy || deleting) ? 'not-allowed' : 'pointer',
                          opacity: (!isDetailReady || isBusy || deleting) ? 0.55 : 1,
                        }}
                      >
                        <Ban size={12} />
                        <span>不收录</span>
                      </button>
                    )}
                  </div>

                  {/* 右侧：操作按钮组 */}
                  {isConfirmedActive ? (
                    isDirty ? (
                      /* 已确认条目 - 已修改内容（维护编辑状态） */
                      <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                        <button
                          type="button"
                          className="btn-secondary"
                          onClick={handleResetChanges}
                          disabled={!isDetailReady || saving || confirming || isBusy}
                          style={{
                            gap: '4px',
                            cursor: (!isDetailReady || saving || confirming || isBusy) ? 'not-allowed' : 'pointer',
                            opacity: (!isDetailReady || saving || confirming || isBusy) ? 0.55 : 1,
                          }}
                          title="还原已修改字段，取消未保存的编辑"
                        >
                          <RotateCcw size={13} />
                          <span>放弃修改</span>
                        </button>

                        <button
                          type="button"
                          data-testid="save-draft-btn"
                          className="btn-secondary"
                          onClick={handleSaveDraft}
                          disabled={!isDetailReady || saving || confirming || isBusy}
                          style={{
                            gap: '4px',
                            cursor: (!isDetailReady || saving || confirming || isBusy) ? 'not-allowed' : 'pointer',
                            opacity: (!isDetailReady || saving || confirming || isBusy) ? 0.55 : 1,
                          }}
                          title={!isDetailReady ? '完整详情加载中...' : '保存为待核对的新版本草稿，旧生效版本保持服务'}
                        >
                          <Save size={13} />
                          <span>{saving ? '保存中...' : '保存为新草稿'}</span>
                        </button>

                        <div style={{ position: 'relative' }}>
                          <button
                            type="button"
                            data-testid="confirm-knowledge-btn"
                            className="btn-primary"
                            onClick={handleConfirm}
                            disabled={!isDetailReady || confirming || saving || isBusy || qualityCheck.isBlocked || previewStatus !== 'ready'}
                            style={{
                              gap: '4px',
                              opacity: (!isDetailReady || qualityCheck.isBlocked || previewStatus !== 'ready' || isBusy) ? 0.55 : 1,
                              cursor: (!isDetailReady || qualityCheck.isBlocked || previewStatus !== 'ready' || isBusy) ? 'not-allowed' : 'pointer',
                            }}
                            title={
                              !isDetailReady
                                ? '完整详情加载中，就绪后可核对与启用'
                                : qualityCheck.isBlocked
                                  ? `无法启用：${qualityCheck.blocking[0]}`
                                  : previewStatus !== 'ready'
                                    ? '等待当前编辑内容自动校验完成'
                                    : '确认修改并立即启用新版本'
                            }
                          >
                            <CheckCircle2 size={13} />
                            <span>{confirming ? '正在启用...' : '确认并启用新版本'}</span>
                          </button>
                        </div>
                      </div>
                    ) : (
                      /* 已确认条目 - 未修改内容（正常查看/维护状态） */
                      <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                        {detail?.lifecycle_status === 'disabled' ? (
                          <span
                            data-testid="disabled-footer-badge"
                            style={{
                              display: 'inline-flex',
                              alignItems: 'center',
                              gap: '4px',
                              fontSize: 'var(--font-size-xs)',
                              color: 'var(--danger-text)',
                              backgroundColor: 'var(--danger-bg)',
                              height: '28px',                              padding: '0 12px',                              borderRadius: '14px',
                              fontWeight: 500,
                            }}
                          >
                            <Ban size={13} />
                            <span>条目已停用（未生效）</span>
                          </span>
                        ) : (
                          <span
                            data-testid="active-version-in-effect-badge"
                            style={{
                              display: 'inline-flex',
                              alignItems: 'center',
                              gap: '4px',
                              fontSize: 'var(--font-size-xs)',
                              color: 'var(--brand-accent)',
                              backgroundColor: 'var(--success-bg)',
                              height: '28px',                              padding: '0 12px',                              borderRadius: '14px',
                              fontWeight: 500,
                            }}
                          >
                            <CheckCircle2 size={13} />
                            <span>当前版本已生效</span>
                          </span>
                        )}
                      </div>
                    )
                  ) : (
                    /* 待核对条目 - 审核流操作 */
                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                      <button
                        type="button"
                        data-testid="save-draft-btn"
                        className="btn-secondary"
                        onClick={handleSaveDraft}
                        disabled={!isDetailReady || saving || confirming || isBusy}
                        style={{
                          gap: '4px',
                          cursor: (!isDetailReady || saving || confirming || isBusy) ? 'not-allowed' : 'pointer',
                          opacity: (!isDetailReady || saving || confirming || isBusy) ? 0.55 : 1,
                        }}
                        title={!isDetailReady ? '完整详情加载中...' : '保存草稿'}
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
                          disabled={!isDetailReady || confirming || saving || isBusy || qualityCheck.isBlocked || (isDirty && previewStatus !== 'ready')}
                          style={{
                            gap: '4px',
                            opacity: (!isDetailReady || qualityCheck.isBlocked || (isDirty && previewStatus !== 'ready') || isBusy) ? 0.55 : 1,
                            cursor: (!isDetailReady || qualityCheck.isBlocked || (isDirty && previewStatus !== 'ready') || isBusy) ? 'not-allowed' : 'pointer',
                          }}
                          title={
                            !isDetailReady
                              ? '完整详情加载中，就绪后可核对与启用'
                              : qualityCheck.isBlocked
                                ? `无法启用：${qualityCheck.blocking[0]}`
                                : isDirty && previewStatus !== 'ready'
                                  ? '等待当前编辑内容自动校验完成'
                                  : (hasNext ? '核对无误，确认启用并进入下一条' : '核对无误，确认并启用知识')
                          }
                        >
                          <CheckCircle2 size={13} />
                          <span>
                            {confirming
                              ? '正在启用...'
                              : isDirty
                                ? '确认修改并启用'
                                : (hasNext ? '确认通过，下一条' : '确认并启用')}
                          </span>
                        </button>
                      </div>
                    </div>
                  )}
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
            backgroundColor: 'rgba(18, 26, 22, 0.42)',
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
              boxShadow: 'var(--shadow-lg)',
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
                  backgroundColor: 'var(--danger-bg)',
                  color: 'var(--danger-text)',
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
                <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-secondary)', marginTop: '2px' }}>
                  排除后将移出待核对队列和分类统计，后续重试不会自动复活。
                </div>
              </div>
            </div>

            {excludeSkillRefs !== null && (
              <div
                data-testid="exclude-skill-refs"
                style={{
                  fontSize: 'var(--font-size-xs)',
                  lineHeight: 1.5,
                  padding: '8px 10px',
                  borderRadius: 'var(--radius-sm)',
                  backgroundColor: excludeSkillRefs > 0 ? 'var(--warning-bg)' : 'var(--bg-secondary)',
                  color: excludeSkillRefs > 0 ? 'var(--warning-text)' : 'var(--text-muted)',
                }}
              >
                {excludeSkillRefs > 0
                  ? `这条知识被 ${excludeSkillRefs} 个 Skill 引用；排除后这些 Skill 需要复核，不能继续引用它。`
                  : '这条知识没有被 Skill 引用。'}
              </div>
            )}

            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              <span style={{ fontSize: 'var(--font-size-xs)', fontWeight: 600, color: 'var(--text-primary)' }}>
                请选择排除原因：
              </span>
              {EXCLUSION_REASONS.map((r) => (
                <label
                  key={r}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '8px',
                    fontSize: 'var(--font-size-xs)',
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
                    fontSize: 'var(--font-size-xs)',
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
                style={{ height: '32px', fontSize: 'var(--font-size-xs)' }}
              >
                取消
              </button>
              <button
                type="button"
                onClick={handleConfirmExclude}
                disabled={deleting}
                style={{
                  height: '32px',
                  fontSize: 'var(--font-size-xs)',
                  padding: '0 14px',
                  borderRadius: 'var(--radius-sm)',
                  backgroundColor: 'var(--danger-text)',
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

      {/* 应用内通用确认弹窗 */}
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
    </>
  );
};
