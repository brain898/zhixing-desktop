import React, { useState, useEffect } from 'react';
import {
  X,
  CheckCircle2,
  AlertTriangle,
  Clock,
  BookOpen,
  FileText,
  Trash2,
  Save,
  Tag,
  Shield,
  Layers,
  Sparkles,
} from 'lucide-react';
import {
  KnowledgeItemDetail,
  PrimaryCategory,
  AtomType,
  FieldState,
  MetricDefinition,
  CaseDetails,
} from '../../../types';
import { api } from '../../../services/api';
import { UnsavedChangesModal } from './UnsavedChangesModal';
import { formatVersionLabel, formatAnchor, formatFieldName } from '../../../utils/formatters';

interface ProofreadingModalProps {
  itemId: string | null;
  onClose: () => void;
  onSaved: () => void;
  onDeleted: () => void;
}

const CATEGORY_COLORS: Record<string, { bg: string; text: string; border: string }> = {
  制度与标准: { bg: '#EBF4F0', text: '#285C49', border: '#C2DBD0' },
  方法与工具: { bg: '#EBF8FF', text: '#2B6CB0', border: '#BEE3F8' },
  项目案例: { bg: '#FAF5FF', text: '#6B46C1', border: '#E9D8FD' },
  指标数据: { bg: '#FFFAF0', text: '#C05621', border: '#FEEBC8' },
  专家经验: { bg: '#F0FFF4', text: '#22543D', border: '#C6F6D5' },
};

export const ProofreadingModal: React.FC<ProofreadingModalProps> = ({
  itemId,
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

  // 表单受控状态
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

  // 脏状态追踪与未保存拦截
  const [isDirty, setIsDirty] = useState(false);
  const [showUnsavedPrompt, setShowUnsavedPrompt] = useState(false);

  // 标签快速添加
  const [tagInput, setTagInput] = useState('');
  const [tagType, setTagType] = useState<'customer' | 'scene' | 'problem'>('scene');

  useEffect(() => {
    if (!itemId) return;
    let isMounted = true;
    setLoading(true);
    setError(null);

    api
      .getKnowledgeItemDetail(itemId)
      .then((data) => {
        if (!isMounted) return;
        setDetail(data);
        const av = data.active_version;
        setTitle(av.title);
        setStatement(av.statement);
        setContent(av.content || av.statement);
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
        setAccessScope(data.access_scope);
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

  const handleRequestClose = () => {
    if (isDirty) {
      setShowUnsavedPrompt(true);
    } else {
      onClose();
    }
  };

  const buildPayload = () => {
    if (!detail) return {};
    return {
      revision_token: detail.active_version.revision_token,
      title,
      statement,
      content,
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
      // 更新本地 token 与质检标记
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
    } catch (err: any) {
      alert(`保存草稿失败: ${err.message}`);
    } finally {
      setSaving(false);
    }
  };

  // 确认知识版本（严格门槛）
  const handleConfirm = async () => {
    if (!detail) return;
    // 客户端先做基本检查
    if (!title.trim() || !statement.trim()) {
      alert('确认失败：知识标题与核心陈述为必填项');
      return;
    }
    if (!primaryCategory) {
      alert('确认失败：主分类仍为「待分类」，确认前必须明确指定五类主分类之一');
      return;
    }

    try {
      setConfirming(true);
      // 先保存当前草稿修改
      if (isDirty) {
        const draftRes = await api.saveKnowledgeDraft(detail.id, buildPayload());
        detail.active_version.revision_token = draftRes.revision_token;
      }

      // 执行确认
      const confirmRes = await api.confirmKnowledgeItem(detail.id, {
        revision_token: detail.active_version.revision_token,
      });

      // 更新本地状态为已确认
      setDetail((prev) => {
        if (!prev) return null;
        return {
          ...prev,
          active_version: {
            ...prev.active_version,
            review_status: 'confirmed',
            index_status: 'not_indexed',
            revision_token: confirmRes.revision_token,
          },
        };
      });
      setIsDirty(false);
      onSaved();
    } catch (err: any) {
      alert(`确认失败: ${err.message}`);
    } finally {
      setConfirming(false);
    }
  };

  // 逻辑删除
  const handleDelete = async () => {
    if (!detail) return;
    if (!window.confirm(`确认删除知识条目「${detail.active_version.title}」？删除后将撤销检索与日常访问资格。`)) {
      return;
    }
    try {
      setDeleting(true);
      await api.deleteKnowledgeItem(detail.id);
      onDeleted();
      onClose();
    } catch (err: any) {
      alert(`删除失败: ${err.message}`);
    } finally {
      setDeleting(false);
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
          backgroundColor: 'rgba(0, 0, 0, 0.5)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          zIndex: 1000,
          padding: '20px',
        }}
      >
        <div
          style={{
            width: '1280px',
            maxWidth: '96vw',
            height: '860px',
            maxHeight: '94vh',
            backgroundColor: '#FFFFFF',
            borderRadius: 'var(--radius-lg)',
            boxShadow: '0 12px 40px rgba(0, 0, 0, 0.2)',
            display: 'flex',
            flexDirection: 'column',
            overflow: 'hidden',
          }}
        >
          {/* 顶栏：标题与状态播报 */}
          <div
            style={{
              padding: '16px 24px',
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
                  width: '32px',
                  height: '32px',
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
                <div style={{ fontSize: '15px', fontWeight: 600, color: 'var(--text-primary)' }}>
                  知识原子校对工作台
                </div>
                <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '2px' }}>
                  来源资料：{detail?.document_title}（{formatVersionLabel(detail?.document_version_label)}）
                </div>
              </div>
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
              {/* 审核状态徽标 */}
              {detail?.active_version.review_status === 'confirmed' ? (
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
                  <span>已确认，索引未建立</span>
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
                  <span>待校对</span>
                </span>
              )}

              {/* 索引状态真实表达 */}
              <span
                style={{
                  fontSize: '11px',
                  padding: '3px 8px',
                  borderRadius: 'var(--radius-sm)',
                  backgroundColor: 'var(--bg-primary)',
                  color: 'var(--text-muted)',
                  border: '1px solid var(--border-color)',
                }}
              >
                检索索引：未建立（待构建）
              </span>

              {/* 提炼引擎标识 */}
              <span
                style={{
                  fontSize: '11px',
                  padding: '3px 8px',
                  borderRadius: 'var(--radius-sm)',
                  backgroundColor: 'var(--brand-accent-light)',
                  color: 'var(--brand-accent)',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '4px',
                }}
              >
                <Sparkles size={12} />
                <span>
                  {detail?.active_version.extraction_context?.provider === 'deepseek-api'
                    ? 'DeepSeek 官方大模型'
                    : '结构化离线提炼引擎'}
                </span>
              </span>

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

          {/* 主体左右两栏对照区 */}
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
              正在加载知识原子与原文证据...
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
              {/* 左侧：原文证据对照栏 (42% 宽度) */}
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
                    <span>原文证据对照 ({detail?.evidence.length || 0} 处支撑)</span>
                  </div>
                  <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                    不虚构页码，精确到段落/表格
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
                  {detail?.evidence && detail.evidence.length > 0 ? (
                    detail.evidence.map((ev, idx) => (
                      <div
                        key={ev.id || idx}
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
                          <span
                            style={{
                              fontWeight: 600,
                              color: 'var(--brand-accent)',
                              backgroundColor: 'var(--brand-accent-light)',
                              padding: '2px 6px',
                              borderRadius: 'var(--radius-sm)',
                            }}
                          >
                            支撑字段：{formatFieldName(ev.field_name)}
                          </span>
                          <span
                            style={{
                              color: 'var(--text-secondary)',
                              backgroundColor: 'var(--bg-secondary)',
                              padding: '2px 8px',
                              borderRadius: 'var(--radius-sm)',
                              fontSize: '11px',
                            }}
                          >
                            {formatAnchor(ev.paragraph_anchor || `p.${ev.page_number || 1}`)}
                          </span>
                        </div>

                        {ev.heading_path && (
                          <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                            章节路径：<strong>{ev.heading_path}</strong>
                          </div>
                        )}

                        {/* 摘录高亮 */}
                        {ev.excerpt && (
                          <div
                            style={{
                              fontSize: '12px',
                              padding: '8px 10px',
                              backgroundColor: '#F0FFF4',
                              color: '#22543D',
                              borderRadius: 'var(--radius-sm)',
                              border: '1px solid #C6F6D5',
                              lineHeight: 1.5,
                            }}
                          >
                            <span style={{ fontWeight: 600 }}>匹配摘录：</span>「{ev.excerpt}」
                          </div>
                        )}

                        {/* 完整结构块正文 */}
                        <div
                          style={{
                            fontSize: '12px',
                            color: 'var(--text-primary)',
                            lineHeight: 1.6,
                            backgroundColor: 'var(--bg-secondary)',
                            padding: '10px 12px',
                            borderRadius: 'var(--radius-sm)',
                            whiteSpace: 'pre-wrap',
                          }}
                        >
                          {ev.text_content}
                        </div>
                      </div>
                    ))
                  ) : (
                    <div
                      style={{
                        padding: '30px',
                        textAlign: 'center',
                        color: 'var(--text-muted)',
                        fontSize: '13px',
                      }}
                    >
                      该条目暂无绑定的原文证据（请检查抽取日志）
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
                    }}
                  >
                    💡 提示：来源文本匹配仅证明在原文中检索到相应段落或表格，不能代替管理员进行语义校对。
                  </div>
                </div>
              </div>

              {/* 右侧：知识原子详情与编辑表单 (58% 宽度) */}
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
                    gap: '20px',
                  }}
                >
                  {/* 质检问题告警条 */}
                  {qualityFlags.length > 0 && (
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
                        <span>检测到待核验质量问题（需管理员校对确认）：</span>
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
                        {qualityFlags.map((flag, idx) => (
                          <li key={idx}>{flag}</li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {/* 基础信息区：标题与五类分类 */}
                  <div
                    style={{
                      display: 'grid',
                      gridTemplateColumns: '2fr 1fr',
                      gap: '16px',
                    }}
                  >
                    <div>
                      <label
                        style={{
                          fontSize: '12px',
                          fontWeight: 600,
                          color: 'var(--text-secondary)',
                          marginBottom: '6px',
                          display: 'block',
                        }}
                      >
                        知识条目标题 *
                      </label>
                      <input
                        type="text"
                        data-testid="input-title"
                        value={title}
                        onChange={(e) => {
                          setTitle(e.target.value);
                          markDirty();
                        }}
                        style={{
                          width: '100%',
                          height: '36px',
                          padding: '0 12px',
                          fontSize: '13px',
                          borderRadius: 'var(--radius-sm)',
                          border: '1px solid var(--border-color)',
                          outline: 'none',
                        }}
                      />
                    </div>

                    <div>
                      <label
                        style={{
                          fontSize: '12px',
                          fontWeight: 600,
                          color: 'var(--text-secondary)',
                          marginBottom: '6px',
                          display: 'block',
                        }}
                      >
                        五类主分类 *
                      </label>
                      <select
                        data-testid="select-primary-category"
                        value={primaryCategory}
                        onChange={(e) => {
                          setPrimaryCategory(e.target.value as PrimaryCategory);
                          markDirty();
                        }}
                        style={{
                          width: '100%',
                          height: '36px',
                          padding: '0 10px',
                          fontSize: '13px',
                          fontWeight: 500,
                          borderRadius: 'var(--radius-sm)',
                          border: '1px solid var(--border-color)',
                          backgroundColor: primaryCategory ? '#FFFFFF' : 'var(--warning-bg)',
                          color: primaryCategory ? 'var(--text-primary)' : 'var(--warning-text)',
                          outline: 'none',
                          cursor: 'pointer',
                        }}
                      >
                        <option value="">-- 待分类 (需确认) --</option>
                        <option value="制度与标准">制度与标准</option>
                        <option value="方法与工具">方法与工具</option>
                        <option value="项目案例">项目案例</option>
                        <option value="指标数据">指标数据</option>
                        <option value="专家经验">专家经验</option>
                      </select>
                    </div>
                  </div>

                  {/* 核心陈述与正文 */}
                  <div>
                    <label
                      style={{
                        fontSize: '12px',
                        fontWeight: 600,
                        color: 'var(--text-secondary)',
                        marginBottom: '6px',
                        display: 'block',
                      }}
                    >
                      独立可理解核心陈述 *
                    </label>
                    <textarea
                      rows={3}
                      value={statement}
                      onChange={(e) => {
                        setStatement(e.target.value);
                        markDirty();
                      }}
                      placeholder="能独立核对的完整规则或事实，不脱离前提与例外..."
                      style={{
                        width: '100%',
                        padding: '10px 12px',
                        fontSize: '13px',
                        lineHeight: 1.5,
                        borderRadius: 'var(--radius-sm)',
                        border: '1px solid var(--border-color)',
                        outline: 'none',
                        resize: 'vertical',
                      }}
                    />
                  </div>

                  {/* 原子三要素：主体、条件、动作、例外 */}
                  <div
                    style={{
                      backgroundColor: 'var(--bg-secondary)',
                      borderRadius: 'var(--radius-md)',
                      padding: '16px',
                      display: 'flex',
                      flexDirection: 'column',
                      gap: '14px',
                      border: '1px solid var(--border-color)',
                    }}
                  >
                    <div style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)' }}>
                      知识原子结构字段
                    </div>

                    {/* 主体 */}
                    <div>
                      <label style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '4px', display: 'block' }}>
                        业务执行主体
                      </label>
                      <input
                        type="text"
                        value={subject}
                        onChange={(e) => {
                          setSubject(e.target.value);
                          markDirty();
                        }}
                        style={{
                          width: '100%',
                          height: '32px',
                          padding: '0 10px',
                          fontSize: '12px',
                          borderRadius: 'var(--radius-sm)',
                          border: '1px solid var(--border-color)',
                          backgroundColor: '#FFFFFF',
                          outline: 'none',
                        }}
                      />
                    </div>

                    {/* 条件 */}
                    <div>
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '6px' }}>
                        <label style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                          触发条件与前提
                        </label>
                        <button
                          type="button"
                          className="btn-secondary"
                          onClick={() => {
                            setConditions([...conditions, '']);
                            markDirty();
                          }}
                          style={{ height: '24px', fontSize: '11px', padding: '0 8px' }}
                        >
                          + 添加条件
                        </button>
                      </div>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                        {conditions.map((cond, idx) => (
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
                                height: '30px',
                                padding: '0 10px',
                                fontSize: '12px',
                                borderRadius: 'var(--radius-sm)',
                                border: '1px solid var(--border-color)',
                                backgroundColor: '#FFFFFF',
                                outline: 'none',
                              }}
                            />
                            <button
                              type="button"
                              onClick={() => {
                                setConditions(conditions.filter((_, i) => i !== idx));
                                markDirty();
                              }}
                              style={{
                                border: 'none',
                                background: 'none',
                                color: 'var(--error-text)',
                                cursor: 'pointer',
                              }}
                            >
                              <X size={14} />
                            </button>
                          </div>
                        ))}
                      </div>
                    </div>

                    {/* 动作 */}
                    <div>
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '6px' }}>
                        <label style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                          执行动作与标准
                        </label>
                        <button
                          type="button"
                          className="btn-secondary"
                          onClick={() => {
                            setActions([...actions, '']);
                            markDirty();
                          }}
                          style={{ height: '24px', fontSize: '11px', padding: '0 8px' }}
                        >
                          + 添加动作
                        </button>
                      </div>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
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
                                height: '30px',
                                padding: '0 10px',
                                fontSize: '12px',
                                borderRadius: 'var(--radius-sm)',
                                border: '1px solid var(--border-color)',
                                backgroundColor: '#FFFFFF',
                                outline: 'none',
                              }}
                            />
                            <button
                              type="button"
                              onClick={() => {
                                setActions(actions.filter((_, i) => i !== idx));
                                markDirty();
                              }}
                              style={{
                                border: 'none',
                                background: 'none',
                                color: 'var(--error-text)',
                                cursor: 'pointer',
                              }}
                            >
                              <X size={14} />
                            </button>
                          </div>
                        ))}
                      </div>
                    </div>

                    {/* 例外与禁止 */}
                    <div>
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '6px' }}>
                        <label style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                          例外、禁止情形与停止条件
                        </label>
                        <button
                          type="button"
                          className="btn-secondary"
                          onClick={() => {
                            setExceptions([...exceptions, '']);
                            markDirty();
                          }}
                          style={{ height: '24px', fontSize: '11px', padding: '0 8px' }}
                        >
                          + 添加例外
                        </button>
                      </div>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                        {exceptions.map((exc, idx) => (
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
                                height: '30px',
                                padding: '0 10px',
                                fontSize: '12px',
                                borderRadius: 'var(--radius-sm)',
                                border: '1px solid var(--border-color)',
                                backgroundColor: '#FFFFFF',
                                outline: 'none',
                              }}
                            />
                            <button
                              type="button"
                              onClick={() => {
                                setExceptions(exceptions.filter((_, i) => i !== idx));
                                markDirty();
                              }}
                              style={{
                                border: 'none',
                                background: 'none',
                                color: 'var(--error-text)',
                                cursor: 'pointer',
                              }}
                            >
                              <X size={14} />
                            </button>
                          </div>
                        ))}
                      </div>
                    </div>

                    {/* 若为指标数据：展示口径定义 */}
                    {primaryCategory === '指标数据' && (
                      <div
                        style={{
                          backgroundColor: '#FFFFFF',
                          padding: '12px',
                          borderRadius: 'var(--radius-sm)',
                          border: '1px solid var(--border-color)',
                          display: 'flex',
                          flexDirection: 'column',
                          gap: '10px',
                        }}
                      >
                        <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-primary)' }}>
                          指标口径规范
                        </div>
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '10px' }}>
                          <div>
                            <label style={{ fontSize: '11px', color: 'var(--text-muted)', display: 'block' }}>
                              计量单位
                            </label>
                            <input
                              type="text"
                              value={metricDef?.unit || ''}
                              onChange={(e) => {
                                setMetricDef({ ...(metricDef || { name: title, period: '', criteria: '' }), unit: e.target.value });
                                markDirty();
                              }}
                              style={{ width: '100%', height: '28px', fontSize: '12px', padding: '0 8px' }}
                            />
                          </div>
                          <div>
                            <label style={{ fontSize: '11px', color: 'var(--text-muted)', display: 'block' }}>
                              统计期间
                            </label>
                            <input
                              type="text"
                              value={metricDef?.period || ''}
                              onChange={(e) => {
                                setMetricDef({ ...(metricDef || { name: title, unit: '', criteria: '' }), period: e.target.value });
                                markDirty();
                              }}
                              style={{ width: '100%', height: '28px', fontSize: '12px', padding: '0 8px' }}
                            />
                          </div>
                          <div>
                            <label style={{ fontSize: '11px', color: 'var(--text-muted)', display: 'block' }}>
                              达标基准
                            </label>
                            <input
                              type="text"
                              value={metricDef?.criteria || ''}
                              onChange={(e) => {
                                setMetricDef({ ...(metricDef || { name: title, unit: '', period: '' }), criteria: e.target.value });
                                markDirty();
                              }}
                              style={{ width: '100%', height: '28px', fontSize: '12px', padding: '0 8px' }}
                            />
                          </div>
                        </div>
                      </div>
                    )}
                  </div>

                  {/* 业务标签与权限管理 */}
                  <div
                    style={{
                      border: '1px solid var(--border-color)',
                      borderRadius: 'var(--radius-md)',
                      padding: '16px',
                      display: 'flex',
                      flexDirection: 'column',
                      gap: '12px',
                    }}
                  >
                    <div style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)' }}>
                      业务标签与适用权限
                    </div>

                    {/* 标签添加与展示 */}
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', alignItems: 'center' }}>
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
                          <X size={11} cursor="pointer" onClick={() => { setCustomerTypes(customerTypes.filter((x) => x !== t)); markDirty(); }} />
                        </span>
                      ))}
                      {businessScenes.map((t) => (
                        <span
                          key={`s_${t}`}
                          style={{
                            fontSize: '11px',
                            backgroundColor: 'var(--brand-accent-light)',
                            color: 'var(--brand-accent)',
                            padding: '2px 8px',
                            borderRadius: '12px',
                            border: '1px solid var(--border-color)',
                            display: 'flex',
                            alignItems: 'center',
                            gap: '4px',
                          }}
                        >
                          场景: {t}
                          <X size={11} cursor="pointer" onClick={() => { setBusinessScenes(businessScenes.filter((x) => x !== t)); markDirty(); }} />
                        </span>
                      ))}
                      {problemTags.map((t) => (
                        <span
                          key={`p_${t}`}
                          style={{
                            fontSize: '11px',
                            backgroundColor: '#FEF3C7',
                            color: '#92400E',
                            padding: '2px 8px',
                            borderRadius: '12px',
                            border: '1px solid #FCD34D',
                            display: 'flex',
                            alignItems: 'center',
                            gap: '4px',
                          }}
                        >
                          问题: {t}
                          <X size={11} cursor="pointer" onClick={() => { setProblemTags(problemTags.filter((x) => x !== t)); markDirty(); }} />
                        </span>
                      ))}
                    </div>

                    {/* 添加标签输入栏 */}
                    <div style={{ display: 'flex', gap: '8px', alignItems: 'center', marginTop: '4px' }}>
                      <select
                        value={tagType}
                        onChange={(e) => setTagType(e.target.value as any)}
                        style={{ height: '30px', fontSize: '12px', padding: '0 8px' }}
                      >
                        <option value="scene">业务场景</option>
                        <option value="customer">客户类型</option>
                        <option value="problem">问题标签</option>
                      </select>
                      <input
                        type="text"
                        placeholder="输入新标签名称..."
                        value={tagInput}
                        onChange={(e) => setTagInput(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') handleAddTag();
                        }}
                        style={{ flex: 1, height: '30px', fontSize: '12px', padding: '0 10px' }}
                      />
                      <button
                        type="button"
                        className="btn-secondary"
                        onClick={handleAddTag}
                        style={{ height: '30px', fontSize: '12px' }}
                      >
                        添加标签
                      </button>
                    </div>

                    {/* 权限范围与关联业务能力状态 */}
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px', marginTop: '6px' }}>
                      <div>
                        <label style={{ fontSize: '12px', color: 'var(--text-secondary)', display: 'block', marginBottom: '4px' }}>
                          访问权限控制
                        </label>
                        <select
                          value={accessScope}
                          onChange={(e) => {
                            setAccessScope(e.target.value as any);
                            markDirty();
                          }}
                          style={{ width: '100%', height: '32px', fontSize: '12px', padding: '0 8px' }}
                        >
                          <option value="admin_only">仅管理员可见</option>
                          <option value="org_internal">企业内部全员可用</option>
                        </select>
                      </div>

                      <div>
                        <label style={{ fontSize: '12px', color: 'var(--text-secondary)', display: 'block', marginBottom: '4px' }}>
                          关联业务能力组件（真实状态）
                        </label>
                        <div
                          style={{
                            height: '32px',
                            display: 'flex',
                            alignItems: 'center',
                            fontSize: '12px',
                            color: 'var(--text-muted)',
                            backgroundColor: 'var(--bg-secondary)',
                            padding: '0 10px',
                            borderRadius: 'var(--radius-sm)',
                          }}
                        >
                          暂无能力组件引用（待后续阶段联调）
                        </div>
                      </div>
                    </div>
                  </div>
                </div>

                {/* 底部操作区：保存草稿、确认知识、删除 */}
                <div
                  style={{
                    padding: '14px 24px',
                    borderTop: '1px solid var(--border-color)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    backgroundColor: '#FFFFFF',
                  }}
                >
                  <button
                    type="button"
                    onClick={handleDelete}
                    disabled={deleting}
                    className="btn-secondary"
                    style={{
                      height: '34px',
                      fontSize: '12px',
                      color: 'var(--error-text)',
                      borderColor: 'var(--border-color)',
                      gap: '4px',
                    }}
                  >
                    <Trash2 size={13} />
                    <span>{deleting ? '正在删除...' : '删除条目'}</span>
                  </button>

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

                    <button
                      type="button"
                      data-testid="confirm-knowledge-btn"
                      className="btn-primary"
                      onClick={handleConfirm}
                      disabled={confirming || saving}
                      style={{ height: '34px', fontSize: '12px', gap: '4px' }}
                    >
                      <CheckCircle2 size={13} />
                      <span>{confirming ? '正在校验确认...' : '确认知识版本'}</span>
                    </button>
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* 未保存离开确认弹窗 */}
      <UnsavedChangesModal
        isOpen={showUnsavedPrompt}
        onKeepEditing={() => setShowUnsavedPrompt(false)}
        onDiscard={() => {
          setShowUnsavedPrompt(false);
          setIsDirty(false);
          onClose();
        }}
        onSave={handleSaveDraft}
        saving={saving}
      />
    </>
  );
};
