import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AlertTriangle, ArrowLeft, Check, Loader2, RotateCcw, Save } from 'lucide-react';
import { api } from '../../../services/api';
import {
  SkillAtomPanelItem,
  SkillContent,
  SkillEvaluation,
  SkillReviewDraft,
  SkillReviewPayloads,
  SkillStep,
  SkillSubmitAction,
  SkillUnsupportedItem,
  SkillWorkbench,
  StaleResolutionAction,
} from '../../../types';
import { AppConfirmDialog } from '../../common/AppConfirmDialog';
import { SegmentedTabs } from '../../ui/SegmentedTabs';
import { ErrorNotice, errorMessage } from './sceneShared';
import { AtomColumn, AtomPicker, PickedAtom, defaultRole } from './SkillReviewAtoms';
import {
  IdentityEditor,
  IdentityView,
  IoSectionEditor,
  IoView,
  MaintainerEditor,
  RefsView,
  RiskEditor,
  RiskView,
  SECTIONS,
  ScopeEditor,
  ScopeView,
  SectionCard,
  SectionKey,
  StepsEditor,
  StepsView,
  sectionOfPath,
  stepOfPath,
} from './SkillReviewForm';
import { ApproveDialog, ChecklistEntry, RecheckDialog, RegenerateDialog, RejectDialog } from './SkillReviewDialogs';
import { AtomChangesCard } from './SkillAtomChanges';
import { SkillDiffPane } from './SkillDiffPane';
import { createLatestCheck } from './asyncTasks';
import { usePolling } from './usePolling';
import {
  GENERIC_STEP_KINDS, PROBLEM_KIND_TEXT, STATUS_TONE, TBD, formatTime, humanizeSteps, inputStyle, normalizeContent, plainText, smallLabel, stepLabel,
} from './skillReviewShared';

interface Props {
  skillId: string;
  queue: string[];
  flash: string | null;
  onBack: () => void;
  /** 审核动作提交后：跳到下一条待审核候选（nextId 为空时回到列表） */
  onNavigate: (nextId: string | null, message: string) => void;
}

type Tab = 'review' | 'history';
type Dialog = null | 'approve' | 'recheck' | 'regenerate' | 'reject' | 'abandon' | 'restore' | 'discard' | 'leave';
const CHECK_DEBOUNCE_MS = 500;

/** M02-D 审核工作台（FR10~FR12）：左侧内容与步骤流、右侧依据面板对照；编辑、通过门槛、四种审核动作；修改记录（FR13） */
export const SkillReviewWorkbench: React.FC<Props> = ({ skillId, queue, flash, onBack, onNavigate }) => {
  const [wb, setWb] = useState<SkillWorkbench | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [draft, setDraft] = useState<SkillReviewDraft | null>(null);
  const draftRef = useRef(draft);
  const content = draft?.skill_json || null;
  const resolutions = draft?.resolutions || {};
  const checklist = draft?.checklist || {};
  const changeReasons = draft?.change_reasons || {};
  const staleRes = draft?.stale_resolutions || {};
  const [token, setToken] = useState('');
  const [evaluation, setEvaluation] = useState<SkillEvaluation | null>(null);
  const [atoms, setAtoms] = useState<SkillAtomPanelItem[]>([]);
  const [dirty, setDirty] = useState(false);
  const [checking, setChecking] = useState(false);
  const [selectedStep, setSelectedStep] = useState<string | null>(null);
  const [selectedAtom, setSelectedAtom] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>('review');
  const [dialog, setDialog] = useState<Dialog>(null);
  const [picker, setPicker] = useState<{ step: string | null } | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [conflict, setConflict] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(flash);
  const [editing, setEditing] = useState<Set<SectionKey>>(new Set());
  const nextStep = useRef(1);
  const editSeq = useRef(0);

  const checks = useMemo(() => createLatestCheck(
    (value: SkillReviewDraft, signal: AbortSignal) => api.checkSkillReview(skillId, value, signal),
    {
      result: (res) => {
        const current = draftRef.current;
        if (!current) return;
        const next = { ...current, skill_json: normalizeContent(res.content) };
        draftRef.current = next;
        setDraft(next);
        setEvaluation(res.evaluation);
        setAtoms(res.atoms);
      },
      error: (err) => {
        setActionError(errorMessage(err, '检查失败，请稍后再试'));
        setEvaluation((prev) => prev && { ...prev, can_approve: false });
      },
      busy: setChecking,
    },
  ), [skillId]);
  useEffect(() => () => checks.cancel(), [checks]);

  const load = useCallback(async () => {
    checks.cancel();
    try {
      const data = await api.getSkillWorkbench(skillId);
      setWb(data);
      const next: SkillReviewDraft = {
        skill_json: normalizeContent(data.content),
        resolutions: data.resolutions || {},
        checklist: data.checklist || {},
        change_reasons: data.change_reasons || {},
        stale_resolutions: data.stale_resolutions || {},
      };
      draftRef.current = next;
      setDraft(next);
      setToken(data.revision_token);
      setEvaluation(data.evaluation);
      setAtoms(data.atoms);
      setDirty(false);
      setConflict(null);
      nextStep.current = data.next_step_number;
      editSeq.current = 0;
      setLoadError(null);
    } catch (err) {
      setLoadError(errorMessage(err, '审核内容加载失败，请稍后再试'));
    }
  }, [skillId, checks]);

  useEffect(() => {
    load();
  }, [load]);

  const status = wb?.status;
  // 待审核与待复核都可编辑；待复核还要处理原子变更（FR14）
  const editable = (status === 'pending_review' || status === 'needs_recheck') && !conflict;
  const isRecheck = status === 'needs_recheck';

  usePolling(status === 'generating', load);

  // Every edit invalidates the previous check immediately. Applying its result never schedules another check.
  const updateDraft = (patch: Partial<SkillReviewDraft>, immediate = false) => {
    if (!draftRef.current) return;
    const next = { ...draftRef.current, ...patch };
    draftRef.current = next;
    setDraft(next);
    editSeq.current += 1;
    setDirty(true);
    setNotice(null);
    setActionError(null);
    checks.schedule(next, immediate ? 0 : CHECK_DEBOUNCE_MS);
  };
  const updateContent = (next: SkillContent) => updateDraft({ skill_json: next });
  const updateResolutions = (next: typeof resolutions) => updateDraft({ resolutions: next });
  // 知识变更的处理（FR14）：更新引用、移除引用由后端改写引用位置，立即取回改写后的内容
  const resolveStale = (vid: string, action: StaleResolutionAction, note: string) => {
    const current = draftRef.current?.stale_resolutions || {};
    updateDraft({ stale_resolutions: { ...current, [vid]: { action, note } } }, current[vid]?.action !== action);
  };

  // 步骤与原子双向高亮
  const highlightAtoms = useMemo(() => {
    const step = content?.steps.find((s) => s.step_id === selectedStep);
    return new Set(step?.refs || []);
  }, [content, selectedStep]);
  const highlightSteps = useMemo(() => {
    if (!selectedAtom || !content) return new Set<string>();
    return new Set(content.steps.filter((s) => s.refs.includes(selectedAtom)).map((s) => s.step_id));
  }, [content, selectedAtom]);
  useEffect(() => {
    const first = [...highlightAtoms][0];
    if (first) document.getElementById(`atom-${first}`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }, [highlightAtoms]);
  useEffect(() => {
    const first = [...highlightSteps][0];
    if (first) document.getElementById(`step-${first}`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }, [highlightSteps]);

  const flaggedPaths = useMemo(() => {
    const set = new Set<string>();
    (evaluation?.unsupported_items || []).filter((i) => i.still_present && !i.resolution).forEach((i) => set.add(i.path));
    return set;
  }, [evaluation]);

  const baseKeys = useMemo(() => ({
    inputs: new Set<string>((wb?.base_json?.inputs || []).map((f: { key: string }) => f.key)),
    outputs: new Set<string>((wb?.base_json?.outputs || []).map((f: { key: string }) => f.key)),
  }), [wb]);

  const titleOf = useCallback((vid: string) => {
    const hit = atoms.find((a) => a.atom_version_id === vid) || wb?.atoms.find((a) => a.atom_version_id === vid)
      || wb?.batch_pool.find((a) => a.atom_version_id === vid);
    return hit?.title || vid;
  }, [atoms, wb]);

  if (!wb || !draft || !content || !evaluation) {
    return (
      <div style={{ padding: '24px 32px', display: 'flex', flexDirection: 'column', gap: '12px', maxWidth: '560px' }}>
        {loadError ? (
          <>
            <ErrorNotice message={loadError} />
            <div style={{ display: 'flex', gap: '8px' }}>
              <button type="button" className="btn-secondary" onClick={onBack}><ArrowLeft size={14} />返回</button>
              <button type="button" className="btn-secondary" onClick={load}><RotateCcw size={14} />重试</button>
            </div>
          </>
        ) : (
          <div style={{ ...smallLabel, fontSize: 'var(--font-size-sm)' }}><Loader2 size={14} className="spin-slow" /> 正在加载审核内容…</div>
        )}
      </div>
    );
  }

  // ------------------------------------------------------------------ 编辑操作
  const removeRef = (vid: string) => {
    updateContent({
      ...content,
      knowledge_refs: content.knowledge_refs.filter((r) => r.atom_version_id !== vid),
      steps: content.steps.map((s) => ({ ...s, refs: s.refs.filter((r) => r !== vid) })),
      preconditions: content.preconditions.map((p) => (p.ref === vid ? { ...p, ref: null } : p)),
      inputs: content.inputs.map((f) => (f.source_ref === vid ? { ...f, source_ref: null } : f)),
      outputs: content.outputs.map((f) => (f.source_ref === vid ? { ...f, source_ref: null } : f)),
    });
    if (selectedAtom === vid) setSelectedAtom(null);
  };
  const changeRole = (vid: string, role: string) =>
    updateContent({ ...content, knowledge_refs: content.knowledge_refs.map((r) => (r.atom_version_id === vid ? { ...r, role } : r)) });
  const pickAtom = (atom: PickedAtom) => {
    const exists = content.knowledge_refs.some((r) => r.atom_version_id === atom.atom_version_id);
    const refs = exists ? content.knowledge_refs : [...content.knowledge_refs, {
      atom_item_id: atom.atom_item_id, atom_version_id: atom.atom_version_id, role: defaultRole(atom), used_in_steps: [],
    }];
    const target = picker?.step;
    const steps = target ? content.steps.map((s) => (s.step_id === target && !s.refs.includes(atom.atom_version_id)
      ? { ...s, refs: [...s.refs, atom.atom_version_id], basis: s.basis === '无依据' || s.basis === '通用操作' ? '有原子依据' as const : s.basis }
      : s)) : content.steps;
    updateContent({ ...content, knowledge_refs: refs, steps });
    setPicker(null);
  };
  const allocateStep = () => {
    const sid = `s${nextStep.current}`;
    nextStep.current += 1;
    return sid;
  };

  // 界面用「第 N 步」，系统编号只用于版本对齐
  const stepOrder = new Map(content.steps.map((s, i) => [s.step_id, i + 1] as [string, number]));
  const human = (text: unknown) => humanizeSteps(plainText(text), stepOrder);

  // 待处理问题：待专家补充、未处理的无依据项、格式问题（通过门槛 FR12），集中列出并能跳到对应段落
  const tbdBlocker = evaluation.blockers.find((b) => b.code === 'TBD_EXPERT');
  const problems: Problem[] = [];
  (tbdBlocker?.paths || []).forEach((path, i) => problems.push({
    key: `tbd:${path}`, section: sectionOfPath(path), step: stepOfPath(path), kind: '待专家补充',
    title: `还没填：${human(tbdBlocker?.details?.[i] || path)}`, detail: '这里是「待专家补充」，请写上具体内容',
  }));
  evaluation.unsupported_items.filter((i) => i.still_present && !i.resolution).forEach((item) => {
    const sid = stepOfPath(item.path);
    const step = sid ? content.steps.find((s) => s.step_id === sid) : undefined;
    if (item.kind === '无依据步骤' && sid) {
      problems.push({ key: item.item_key, section: 'steps', step: sid, kind: item.kind, item,
        title: `${stepLabel(stepOrder, sid)}没有找到出处`, detail: step ? plainText(step.action) : human(item.message) });
    } else if (item.kind === '例外未落点') {
      const owner = item.atom_version_id ? titleOf(item.atom_version_id) : '';
      const covered = item.value ? content.steps.find((s) => typeof s.action === 'string' && s.action.includes(item.value as string)) : undefined;
      problems.push({ key: item.item_key, section: sectionOfPath(item.path), step: null, kind: item.kind, item,
        coveredBy: covered ? stepLabel(stepOrder, covered.step_id) : undefined,
        title: `${owner ? `「${owner}」` : '一条知识'}写了特殊情况「${plainText(item.value)}」`,
        detail: covered
          ? `${stepLabel(stepOrder, covered.step_id)}的正文已经写到这句话，但转人工、分支条件或不适用范围里没有对应`
          : '转人工、分支条件或不适用范围里还没有对应' });
    } else {
      problems.push({ key: item.item_key, section: sectionOfPath(item.path), step: sid, kind: item.kind, item,
        title: human(item.label), detail: human(item.message) });
    }
  });
  const changes = evaluation.atom_changes || [];
  changes.filter((c) => !c.handled).forEach((c) => problems.push({
    key: `stale:${c.atom_version_id}`, section: 'refs', step: null, kind: '知识变更',
    title: c.title, detail: `${c.trigger_labels.join('、')}：${c.problem || '请选择处理方式'}`,
  }));
  const changedVids = changes.filter((c) => !c.handled).map((c) => c.atom_version_id);
  evaluation.issues.filter((i) => i.level === 'hard_error'
    && !changedVids.some((v) => (i.path || '').includes(v) || (i.message || '').includes(v))).forEach((issue) => problems.push({
    key: `issue:${issue.code}:${issue.path}`, section: sectionOfPath(issue.path), step: stepOfPath(issue.path),
    kind: issue.code.startsWith('REF_NOT') || issue.code === 'REF_ATOM_NOT_FOUND' ? '知识失效' : '格式问题',
    title: human(issue.label), detail: `${issue.plain}：${human(issue.message)}`,
  }));
  const reused = evaluation.blockers.find((b) => b.code === 'STEP_ID_REUSED');
  if (reused) problems.push({ key: 'step-id-reused', section: 'steps', step: null, kind: '格式问题',
    title: '步骤编号', detail: `${reused.message}：${(reused.details || []).join('、')}` });
  const problemsBySection = problems.reduce<Record<string, number>>((acc, p) => ({ ...acc, [p.section]: (acc[p.section] || 0) + 1 }), {});
  // 审核清单不再逐段勾选，集中到「通过」对话框；其余门槛都满足才能打开对话框。复核不要求审核清单（FR14）
  const otherBlockers = evaluation.blockers.filter((b) => b.code !== 'CHECKLIST');
  const gateReady = isRecheck ? evaluation.can_approve : otherBlockers.length === 0;

  const flaggedStepItem = (sid: string) => problems.find((p) => p.step === sid && p.item && p.item.kind !== '例外未落点');
  const checklistEntries: ChecklistEntry[] = (() => {
    const pick = (xs: string[], n = 3) => {
      const list = xs.filter((x) => x && x !== TBD).map((x) => plainText(x));
      return list.slice(0, n).join('、') + (list.length > n ? ' 等' : '');
    };
    const applies = [...content.applies_to.customer_types, ...content.applies_to.property_types, ...content.applies_to.conditions];
    const invalid = atoms.filter((a) => !a.eligible).length;
    const handled = evaluation.unsupported_items.filter((i) => i.resolution).length;
    const summaries: Record<string, string> = {
      goal: plainText(content.goal) || '（未填写）',
      scope: `适用：${pick(applies) || '未填写'}；不适用：${pick(content.not_applies_to) || '未填写'}`,
      refs: `${atoms.length} 条知识${invalid ? `，其中 ${invalid} 条已失效` : '，都还有效'}`,
      steps: `${content.steps.length} 步${handled ? `；已处理 ${handled} 处没有出处或没对应的内容` : ''}`,
      outputs: `${content.outputs.length} 项：${pick(content.outputs.map((o) => o.label || o.key), 4) || '未填写'}`,
      escalation: `${content.escalation_conditions.length} 种：${pick(content.escalation_conditions) || '未填写'}`,
    };
    return wb.checklist_items.map((c) => ({ key: c.key, label: c.label, summary: summaries[c.key] || '' }));
  })();

  const scrollToChanges = () => document.getElementById('section-atom-changes')?.scrollIntoView({ block: 'start', behavior: 'smooth' });
  const scrollToSection = (key: SectionKey) => document.getElementById(`section-${key}`)?.scrollIntoView({ block: 'start', behavior: 'smooth' });
  const openSection = (key: SectionKey, sid?: string | null) => {
    setEditing((prev) => new Set(prev).add(key));
    if (sid) setSelectedStep(sid);
    window.setTimeout(() => {
      const target = (sid && document.getElementById(`step-${sid}`)) || document.getElementById(`section-${key}`);
      target?.scrollIntoView({ block: 'start', behavior: 'smooth' });
    }, 60);
  };
  const toggleSection = (key: SectionKey) => setEditing((prev) => {
    const next = new Set(prev);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  });
  const markStepExpert = (sid: string) => {
    updateContent({ ...content, steps: content.steps.map((s) => (s.step_id === sid ? { ...s, basis: '专家补充' as const } : s)) });
    openSection('steps', sid);
    window.setTimeout(() => (document.querySelector(`#step-${sid} [data-testid="step-expert-reason"]`) as HTMLInputElement | null)?.focus(), 120);
  };
  const markStepGeneric = (sid: string) =>
    updateContent({ ...content, steps: content.steps.map((s) => (s.step_id === sid ? { ...s, basis: '通用操作' as const } : s)) });
  const deleteStep = (sid: string) => updateContent({ ...content, steps: content.steps.filter((s) => s.step_id !== sid) });
  const landException = (text: string) =>
    updateContent({ ...content, escalation_conditions: [...content.escalation_conditions.filter((t) => t !== 'TBD_EXPERT'), text] });
  const selectStep = (sid: string | null) => { setSelectedStep(sid); setSelectedAtom(null); };

  // ------------------------------------------------------------------ 提交
  const handleError = (err: unknown, fallback: string) => {
    const code = (err as { code?: string }).code;
    const message = errorMessage(err, fallback);
    if (code === 'REVISION_CONFLICT') {
      checks.cancel();
      setConflict(message);
      setDialog(null);
    } else {
      setActionError(message);
    }
  };

  const saveDraft = async () => {
    checks.cancel();
    const savedEdit = editSeq.current;
    setBusy(true);
    setActionError(null);
    try {
      const res = await api.saveSkillDraft(skillId, { ...draft, revision_token: token });
      setToken(res.revision_token);
      if (editSeq.current === savedEdit) {
        const next = { ...draft, skill_json: normalizeContent(res.content) };
        draftRef.current = next;
        setDraft(next);
        setEvaluation(res.evaluation);
        setDirty(false);
      }
      setWb({ ...wb, has_draft: true, draft_updated_at: res.saved_at });
      setNotice(`已保存（${formatTime(res.saved_at)}）。保存的是草稿，${isRecheck ? '提交复核' : '点「通过」'}后才生成正式版本`);
    } catch (err) {
      handleError(err, '保存失败，请稍后再试');
      if ((err as { code?: string }).code !== 'REVISION_CONFLICT' && draftRef.current && editSeq.current === savedEdit) {
        checks.schedule(draftRef.current, 0);
      }
    } finally {
      setBusy(false);
    }
  };

  const discardDraft = async () => {
    setBusy(true);
    try {
      await api.discardSkillDraft(skillId, token);
      setDialog(null);
      setEditing(new Set());
      await load();
      setNotice('已放弃修改，内容恢复为当前版本');
    } catch (err) {
      handleError(err, '操作失败，请稍后再试');
    } finally {
      setBusy(false);
    }
  };

  const submit = async <A extends SkillSubmitAction,>(action: A, body: SkillReviewPayloads[A], message: string) => {
    setBusy(true);
    setActionError(null);
    try {
      const res = await api.submitSkillReview(skillId, action, { revision_token: token, queue, ...body });
      setDialog(null);
      if (action === 'abandon' || action === 'restore' || action === 'recheck') {
        setEditing(new Set());
        await load();
        setNotice(message);
        return;
      }
      onNavigate(res.next_skill_id || null, message);
    } catch (err) {
      handleError(err, '提交失败，请稍后再试');
    } finally {
      setBusy(false);
    }
  };

  const refOptions = content.knowledge_refs.map((r) => ({ value: r.atom_version_id, label: titleOf(r.atom_version_id) }));
  const viewProps = { content, titleOf };
  const stepQuickActions = (step: SkillStep) => {
    const p = flaggedStepItem(step.step_id);
    if (!p?.item || !editable) return null;
    const sid = step.step_id;
    if (p.item.kind === '无依据步骤') {
      return (
        <>
          <button type="button" className="btn-primary btn-sm" onClick={() => setPicker({ step: sid })} data-testid="step-quick-add-ref">补一条依据</button>
          {GENERIC_STEP_KINDS.includes(step.kind) && (
            <button type="button" className="btn-secondary btn-sm" onClick={() => markStepGeneric(sid)} data-testid="step-quick-generic">保留，这是常规操作</button>
          )}
          <button type="button" className="btn-secondary btn-sm" onClick={() => markStepExpert(sid)} data-testid="step-quick-expert">这是我们的经验，写理由</button>
          <button type="button" className="btn-danger-ghost btn-sm" onClick={() => deleteStep(sid)} data-testid="step-quick-delete">删掉这一步</button>
        </>
      );
    }
    return <button type="button" className="btn-secondary btn-sm" onClick={() => setPicker({ step: sid })}>补一条依据</button>;
  };
  const sectionBody = (key: SectionKey) => {
    const on = editable && editing.has(key);
    switch (key) {
      case 'identity':
        return on ? <IdentityEditor content={content} onChange={updateContent} /> : <IdentityView {...viewProps} confidence={wb.generation_confidence} />;
      case 'scope':
        return on ? <ScopeEditor content={content} onChange={updateContent} /> : <ScopeView {...viewProps} />;
      case 'refs':
        return <RefsView atoms={atoms} editing={on} />;
      case 'io':
        return on ? (
          <IoSectionEditor content={content} onChange={updateContent} baseKeys={baseKeys} refOptions={refOptions}
            selectedAtom={selectedAtom} flaggedPaths={flaggedPaths} />
        ) : <IoView {...viewProps} />;
      case 'steps':
        return on ? (
          <StepsEditor content={content} onChange={updateContent} refOptions={refOptions} titleOf={titleOf}
            selectedStep={selectedStep} highlightSteps={highlightSteps} flaggedPaths={flaggedPaths}
            onSelectStep={selectStep} onAllocateStepId={allocateStep} onPickForStep={(sid) => setPicker({ step: sid })} />
        ) : (
          <StepsView content={content} selectedStep={selectedStep} highlightSteps={highlightSteps} flaggedPaths={flaggedPaths}
            onSelectStep={selectStep} renderQuickActions={stepQuickActions} />
        );
      case 'risk':
        return on ? <RiskEditor content={content} onChange={updateContent} /> : <RiskView {...viewProps} />;
      default:
        return <MaintainerEditor content={content} onChange={updateContent} editing={on} />;
    }
  };
  const navCount: Partial<Record<SectionKey, number>> = {
    steps: content.steps.length, risk: content.escalation_conditions.length, refs: atoms.length,
  };
  const focusStep = selectedStep && stepOrder.has(selectedStep) ? {
    sid: selectedStep,
    label: stepLabel(stepOrder, selectedStep),
    refs: content.steps.find((s) => s.step_id === selectedStep)?.refs || [],
  } : null;
  const actions = new Set(wb.allowed_actions);
  const gateText = checking ? null
    : gateReady ? `都处理好了，可以${isRecheck ? '提交复核' : evaluation.approve_label}`
      : problems.length ? `还有 ${problems.length} 处要处理` : plainText(otherBlockers.map((b) => b.message).join('；'));
  const banner = (tone: 'success' | 'danger' | 'warning' | 'neutral'): React.CSSProperties => ({
    fontSize: 'var(--font-size-sm)', borderRadius: 'var(--radius-sm)', padding: '6px 10px', lineHeight: 1.6,
    color: tone === 'neutral' ? 'var(--text-secondary)' : `var(--${tone}-text)`,
    backgroundColor: tone === 'neutral' ? 'var(--bg-sunken)' : `var(--${tone}-bg)`,
  });

  // ------------------------------------------------------------------ 渲染
  return (
    <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', backgroundColor: 'var(--bg-secondary)' }} data-testid="skill-workbench" data-status={wb.status}>
      {/* 顶部：名称与状态，审核动作 */}
      <div style={{ height: '56px', flexShrink: 0, padding: '0 20px', borderBottom: '1px solid var(--border-color)', backgroundColor: 'var(--bg-primary)', display: 'flex', alignItems: 'center', gap: '10px' }}>
        <button type="button" className="btn-ghost btn-sm" onClick={() => (dirty ? setDialog('leave') : onBack())} data-testid="workbench-back" aria-label="返回列表">
          <ArrowLeft size={15} />返回
        </button>
        <span style={{ fontSize: '17px', fontWeight: 700, color: 'var(--text-primary)', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} data-testid="workbench-name">{content.name || wb.name}</span>
        <span className={`zx-tag ${STATUS_TONE[wb.status] ?? 'neutral'}`} data-testid="workbench-status">{wb.status_label}</span>
        {wb.version_number && <span style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-muted)', whiteSpace: 'nowrap' }} data-testid="workbench-version">第 {wb.version_number} 版 · {wb.current_version_kind_label}</span>}
        {wb.regenerate_count > 0 && <span className="zx-tag neutral" data-testid="regenerate-count">已重生成 {wb.regenerate_count} 次</span>}
        {wb.similar_skills.length > 0 && <span className="zx-tag warning" title={wb.similar_skills.map((s) => s.name).join('、')}>与已有 Skill 相似</span>}
        <span style={{ flex: 1 }} />
        <SegmentedTabs<Tab>
          value={tab}
          onChange={setTab}
          options={[
            { key: 'review', label: '审核', testId: 'workbench-tab-review' },
            { key: 'history', label: '修改记录', count: wb.versions.length > 1 ? wb.versions.length : undefined, testId: 'workbench-tab-history', title: '数字为版本数' },
          ]}
        />
        {(actions.size > 0) && <span style={{ width: '1px', height: '22px', backgroundColor: 'var(--border-color)', margin: '0 2px' }} />}
        {actions.has('reject') && <button type="button" className="btn-danger-ghost btn-sm" onClick={() => { setActionError(null); setDialog('reject'); }} disabled={busy} data-testid="action-reject">驳回</button>}
        {actions.has('abandon') && <button type="button" className="btn-danger-ghost btn-sm" onClick={() => { setActionError(null); setDialog('abandon'); }} disabled={busy} data-testid="action-abandon">放弃</button>}
        {actions.has('regenerate') && (
          <button type="button" className="btn-secondary btn-sm" onClick={() => { setActionError(null); setDialog('regenerate'); }} disabled={busy} data-testid="action-regenerate">
            {wb.status === 'validation_failed' ? '重新生成' : '退回重生成'}
          </button>
        )}
        {actions.has('restore') && <button type="button" className="btn-primary btn-sm" onClick={() => { setActionError(null); setDialog('restore'); }} disabled={busy} data-testid="action-restore">恢复</button>}
        {actions.has('recheck') && (
          <button type="button" className="btn-primary btn-sm" onClick={() => { setActionError(null); setDialog('recheck'); }} disabled={busy || checking || !gateReady || !editable}
            title={gateReady ? undefined : gateText || undefined} data-testid="action-recheck">
            <Check size={14} />提交复核
          </button>
        )}
        {actions.has('approve') && (
          <button type="button" className="btn-primary btn-sm" onClick={() => { setActionError(null); setDialog('approve'); }} disabled={busy || checking || !gateReady || !editable}
            title={gateReady ? '下一步逐项确认审核清单' : gateText || undefined} data-testid="action-approve">
            <Check size={14} />{evaluation.approve_label}
          </button>
        )}
      </div>

      {/* 第二行：段落导航、门槛状态、草稿保存 */}
      {tab === 'review' && (
        <div style={{ height: '46px', flexShrink: 0, padding: '0 20px', borderBottom: '1px solid var(--border-color)', backgroundColor: 'var(--bg-primary)', display: 'flex', alignItems: 'center', gap: '12px' }}>
          <nav style={{ display: 'flex', gap: '2px', minWidth: 0, overflow: 'hidden' }} aria-label="段落导航" data-testid="section-nav">
            {SECTIONS.map((s) => (
              <button key={s.key} type="button" onClick={() => scrollToSection(s.key)}
                style={{ border: 0, background: 'none', cursor: 'pointer', padding: '6px 10px', borderRadius: 'var(--radius-sm)', whiteSpace: 'nowrap',
                  fontSize: 'var(--font-size-sm)', color: 'var(--text-secondary)', display: 'inline-flex', alignItems: 'center', gap: '5px' }}>
                {s.nav}{navCount[s.key] !== undefined && <span style={{ color: 'var(--text-muted)' }}>{navCount[s.key]}</span>}
                {problemsBySection[s.key] > 0 && <span style={{ width: '6px', height: '6px', borderRadius: '50%', backgroundColor: 'var(--warning-text)' }} />}
              </button>
            ))}
          </nav>
          <span style={{ flex: 1 }} />
          {editable && (
            <span data-testid="gate-blockers" style={{ fontSize: 'var(--font-size-sm)', whiteSpace: 'nowrap', display: 'inline-flex', alignItems: 'center', gap: '6px',
              color: gateReady ? 'var(--success-text)' : problems.length ? 'var(--warning-text)' : 'var(--text-secondary)' }}>
              {checking ? <><Loader2 size={13} className="spin-slow" />正在检查…</>
                : gateReady ? <><Check size={14} />{gateText}</>
                : problems.length ? (
                  <button type="button" onClick={() => document.getElementById('problems-card')?.scrollIntoView({ block: 'start', behavior: 'smooth' })}
                    style={{ border: 0, cursor: 'pointer', font: 'inherit', color: 'var(--warning-text)', backgroundColor: 'var(--warning-bg)', borderRadius: '999px', padding: '4px 12px', display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
                    <AlertTriangle size={13} />{gateText}
                  </button>
                ) : gateText}
            </span>
          )}
          {wb.status === 'approved' && <span style={{ fontSize: 'var(--font-size-sm)', color: 'var(--success-text)' }}>已通过，等待后续测试。可在「修改记录」查看差异与导出</span>}
          {wb.status === 'rejected' && <span style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-secondary)' }}>这条候选已驳回</span>}
          {editable && (dirty || wb.has_draft) && (
            <button type="button" className="btn-ghost btn-sm" onClick={() => setDialog('discard')} disabled={busy} data-testid="action-discard">放弃修改</button>
          )}
          {editable && dirty && (
            <button type="button" className="btn-secondary btn-sm" onClick={saveDraft} disabled={busy} data-testid="action-save">
              <Save size={13} />保存修改
            </button>
          )}
        </div>
      )}

      {(wb.atom_changed || notice || conflict || actionError || wb.status === 'generating' || wb.regenerate_task?.status === 'failed') && (
        <div style={{ padding: '8px 20px', display: 'flex', flexDirection: 'column', gap: '6px', backgroundColor: 'var(--bg-primary)', borderBottom: '1px solid var(--border-color)' }}>
          {wb.atom_changed && (
            <div style={banner('danger')} data-testid="atom-changed-banner">
              <AlertTriangle size={13} style={{ verticalAlign: '-2px', marginRight: '6px' }} />
              {isRecheck
                ? <>这条 Skill 已通过，但依据的知识发生了变化，需要复核：{wb.stale_reason || '见下方「知识变更」'}。</>
                : wb.status === 'pending_review'
                  ? <>依据的知识发生了变化（停用、失效、出了新版本或权限收紧）。在下方「知识变更」中处理后才能通过。</>
                  : <>依据的知识发生了变化。</>}
              {wb.visibility === 'admin_only' && changes.some((c) => c.triggers.includes('scope_tightened')) && <> 可见范围已随知识权限收紧为「仅管理员」。</>}
            </div>
          )}
          {notice && <div style={banner('success')} data-testid="workbench-notice"><Check size={13} style={{ verticalAlign: '-2px', marginRight: '6px' }} />{notice}</div>}
          {actionError && <ErrorNotice message={plainText(actionError)} />}
          {conflict && (
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }} data-testid="conflict-banner">
              <div style={{ flex: 1 }}><ErrorNotice message={conflict} /></div>
              <button type="button" className="btn-secondary btn-sm" onClick={load}><RotateCcw size={13} />重新加载</button>
            </div>
          )}
          {wb.status === 'generating' && (
            <div style={banner('neutral')} data-testid="generating-banner">
              <Loader2 size={13} className="spin-slow" style={{ verticalAlign: '-2px', marginRight: '6px' }} />正在按意见重新生成，完成后会作为新版本回到待审核。
            </div>
          )}
          {wb.regenerate_task && wb.regenerate_task.status === 'failed' && wb.status !== 'generating' && (
            <div style={banner('warning')} data-testid="regenerate-failed">上次重新生成没有成功：{wb.regenerate_task.message}</div>
          )}
        </div>
      )}

      {tab === 'history' ? (
        <SkillDiffPane skillId={skillId} skillName={content.name || wb.name} versions={wb.versions} records={wb.review_records}
          currentVersionId={wb.current_version_id} titleOf={titleOf} />
      ) : (
        <div style={{ flex: 1, minHeight: 0, display: 'flex' }}>
          {/* 左：Skill 内容，先处理问题，再看步骤流和其余段落 */}
          <div style={{ flex: 1, minWidth: 0, overflow: 'auto', padding: '20px 32px 40px' }} data-testid="workbench-right">
            <div style={{ maxWidth: '920px', margin: '0 auto' }}>
              {(wb.status === 'validation_failed' || (wb.status === 'rejected' && !wb.current_version_id)) && (
                <div className="zx-card" style={{ padding: '12px 16px', marginBottom: '16px', backgroundColor: 'var(--danger-bg)' }} data-testid="generation-issues">
                  <div style={{ fontWeight: 600, marginBottom: '6px' }}>这条候选没有通过检查，不能进入审核</div>
                  <div style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-secondary)', lineHeight: 1.7 }}>
                    {Array.from(new Set(wb.generation_issues.map((i) => i.plain))).join('；') || '格式不完整'}。可以重新生成，或者放弃这条候选。
                  </div>
                </div>
              )}
              <AtomChangesCard
                changes={changes}
                editable={editable}
                resolutions={staleRes}
                selectedAtom={selectedAtom}
                onSelectAtom={(vid) => { setSelectedAtom(vid); setSelectedStep(null); }}
                onGoPath={(path) => openSection(sectionOfPath(path), stepOfPath(path))}
                onResolve={resolveStale}
              />
              {editable && (
                <ProblemsCard
                  problems={problems}
                  resolved={evaluation.unsupported_items.filter((i) => i.resolution)}
                  resolutions={resolutions}
                  isRecheck={isRecheck}
                  onGo={(p) => (p.key.startsWith('stale:') ? scrollToChanges() : openSection(p.section, p.step))}
                  onResolve={(entries) => updateResolutions({ ...resolutions, ...Object.fromEntries(entries.map(([key, reason]) => [key, { action: 'expert', reason }])) })}
                  onClear={(key) => {
                    const next = { ...resolutions };
                    delete next[key];
                    updateResolutions(next);
                  }}
                  onDeleteStep={deleteStep}
                  onMarkStepExpert={markStepExpert}
                  onPickForStep={(sid) => setPicker({ step: sid })}
                  onLand={landException}
                />
              )}
              {SECTIONS.map((s) => (
                <SectionCard
                  key={s.key}
                  sectionKey={s.key}
                  canEdit={editable}
                  editing={editable && editing.has(s.key)}
                  problems={problemsBySection[s.key] || 0}
                  onToggleEdit={() => toggleSection(s.key)}
                >
                  {sectionBody(s.key)}
                </SectionCard>
              ))}
            </div>
          </div>
          {/* 右：依据面板，跟随选中的步骤 */}
          <aside style={{ width: '420px', flexShrink: 0, borderLeft: '1px solid var(--border-color)', overflow: 'auto', padding: '20px 20px 32px', backgroundColor: 'var(--bg-primary)', boxSizing: 'border-box' }}>
            <AtomColumn
              atoms={atoms}
              editable={editable && editing.has('refs')}
              selectedAtom={selectedAtom}
              highlightAtoms={highlightAtoms}
              focusStep={focusStep}
              stepOrder={stepOrder}
              onSelectAtom={(vid) => { setSelectedAtom(vid); setSelectedStep(null); }}
              onSelectStep={selectStep}
              onClearStep={() => setSelectedStep(null)}
              onRoleChange={changeRole}
              onRemove={removeRef}
              onAdd={() => setPicker({ step: null })}
              onLand={editable ? landException : undefined}
            />
          </aside>
        </div>
      )}

      {picker && (
        <AtomPicker
          pool={wb.batch_pool}
          existing={new Set(content.knowledge_refs.map((r) => r.atom_version_id))}
          targetStep={picker.step}
          targetLabel={picker.step ? stepLabel(stepOrder, picker.step) : null}
          onPick={pickAtom}
          onClose={() => setPicker(null)}
        />
      )}
      {dialog === 'approve' && (
        <ApproveDialog evaluation={evaluation} checklist={checklistEntries} initialChecked={checklist} initialReasons={changeReasons} busy={busy} error={actionError}
          onCancel={() => setDialog(null)}
          onConfirm={(comment, reasons, checked) => {
            const next = { ...draft, change_reasons: reasons, checklist: checked };
            draftRef.current = next;
            setDraft(next);
            submit('approve', { ...next, comment },
              `「${content.name}」已${evaluation.approve_label}`);
          }} />
      )}
      {dialog === 'recheck' && (
        <RecheckDialog evaluation={evaluation} busy={busy} error={actionError} onCancel={() => setDialog(null)}
          onConfirm={(comment) => submit('recheck', { ...draft, comment },
            `「${content.name}」已完成复核，回到已通过`)} />
      )}
      {dialog === 'regenerate' && (
        <RegenerateDialog fromFailed={wb.status === 'validation_failed'} groups={wb.field_groups} regenerateCount={wb.regenerate_count}
          hasEdits={dirty || wb.has_draft} busy={busy} error={actionError} onCancel={() => setDialog(null)}
          onConfirm={(comment, groups) => submit('regenerate', { comment, field_groups: groups },
            `「${content.name}」已${wb.status === 'validation_failed' ? '开始重新生成' : '退回重生成'}，完成后回到待审核`)} />
      )}
      {dialog === 'reject' && (
        <RejectDialog reasons={wb.reject_reasons} busy={busy} error={actionError} onCancel={() => setDialog(null)}
          onConfirm={(reason, note) => submit('reject', { reason, note }, `「${content.name}」已驳回`)} />
      )}
      <AppConfirmDialog
        isOpen={dialog === 'abandon'}
        title="放弃这条候选？"
        variant="danger"
        description="放弃后进入「已驳回」，不产生版本；之后可以恢复。"
        confirmText="放弃"
        loading={busy}
        onConfirm={() => submit('abandon', {}, '已放弃这条候选')}
        onCancel={() => setDialog(null)}
      />
      <AppConfirmDialog
        isOpen={dialog === 'restore'}
        title="恢复这条候选？"
        description={wb.current_version_id ? '恢复后回到「待审核」。' : '这条候选没有通过检查，恢复后回到「校验未通过」。'}
        confirmText="恢复"
        loading={busy}
        onConfirm={() => submit('restore', {}, '已恢复')}
        onCancel={() => setDialog(null)}
      />
      <AppConfirmDialog
        isOpen={dialog === 'discard'}
        title="放弃所有未提交的修改？"
        variant="danger"
        description="内容会恢复为当前版本，已保存的草稿也会删除。"
        confirmText="放弃修改"
        loading={busy}
        onConfirm={discardDraft}
        onCancel={() => setDialog(null)}
      />
      <AppConfirmDialog
        isOpen={dialog === 'leave'}
        title="还有修改没有保存"
        variant="danger"
        description="离开后这些修改会丢失。需要保留的话，请先点「保存修改」。"
        confirmText="仍然离开"
        onConfirm={() => { setDialog(null); onBack(); }}
        onCancel={() => setDialog(null)}
      />
    </div>
  );
};

interface Problem {
  key: string;
  section: SectionKey;
  step: string | null;
  kind: string;
  title: string;
  detail: string;
  item?: SkillUnsupportedItem;
  /** 特殊情况已在某一步正文中写到时，给出那一步 */
  coveredBy?: string;
}

const ProblemsCard: React.FC<{
  problems: Problem[];
  resolved: SkillUnsupportedItem[];
  resolutions: Record<string, { action: string; reason: string }>;
  isRecheck: boolean;
  onGo: (p: Problem) => void;
  onResolve: (entries: [string, string][]) => void;
  onClear: (key: string) => void;
  onDeleteStep: (sid: string) => void;
  onMarkStepExpert: (sid: string) => void;
  onPickForStep: (sid: string) => void;
  onLand: (text: string) => void;
}> = ({ problems, resolved, resolutions, isRecheck, onGo, onResolve, onClear, onDeleteStep, onMarkStepExpert, onPickForStep, onLand }) => {
  if (problems.length === 0) {
    return (
      <div id="problems-card" style={{ padding: '12px 16px', marginBottom: '24px', fontSize: 'var(--font-size-sm)', color: 'var(--success-text)', backgroundColor: 'var(--success-bg)', borderRadius: 'var(--radius-lg)' }} data-testid="problems-card" data-count="0">
        <Check size={14} style={{ verticalAlign: '-2px', marginRight: '6px' }} />
        没有需要处理的问题。看完内容后点右上角「{isRecheck ? '提交复核' : '通过'}」{isRecheck ? '' : '，在弹出的清单里逐项确认'}。
        {resolved.length > 0 && <span style={{ color: 'var(--text-muted)' }}>（已处理 {resolved.length} 处：{resolved.map((i) => i.resolution_label).join('、')}）</span>}
      </div>
    );
  }
  const covered = problems.filter((p) => p.coveredBy && p.item);
  return (
    <div id="problems-card" style={{ marginBottom: '28px', scrollMarginTop: '16px' }} data-testid="problems-card" data-count={problems.length}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: '10px', marginBottom: '10px' }}>
        <h2 style={{ margin: 0, fontSize: 'var(--font-size-section)', fontWeight: 700 }}>先处理这 {problems.length} 处</h2>
        <span style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-muted)' }}>处理完才能{isRecheck ? '提交复核' : '通过'}</span>
        <span style={{ flex: 1 }} />
        {covered.length > 1 && (
          <button type="button" className="btn-secondary btn-sm" data-testid="resolve-covered-all"
            onClick={() => onResolve(covered.map((p) => [p.item!.item_key, `已在${p.coveredBy}中写明`]))}>
            确认都已写在步骤里（{covered.length} 处）
          </button>
        )}
      </div>
      <div style={{ backgroundColor: 'var(--bg-primary)', borderRadius: 'var(--radius-lg)', boxShadow: 'var(--shadow-sm)', overflow: 'hidden' }}>
        {problems.map((p, idx) => {
          const item = p.item;
          const sid = p.step;
          const expertInput = item && item.kind !== '无依据步骤';
          return (
            <div key={p.key} data-testid="problem-item" data-kind={p.kind} style={{ padding: '14px 18px', borderTop: idx ? '1px solid var(--bg-sunken)' : undefined, display: 'flex', flexDirection: 'column', gap: '6px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                <span className="zx-tag warning" style={{ flexShrink: 0 }}>{PROBLEM_KIND_TEXT[p.kind] || p.kind}</span>
                <span style={{ fontSize: 'var(--font-size-base)', fontWeight: 600, color: 'var(--text-primary)', minWidth: 0 }}>{p.title}</span>
                <span style={{ flex: 1 }} />
                <button type="button" className="btn-ghost btn-sm" onClick={() => onGo(p)} data-testid="problem-go">去看看</button>
              </div>
              <div style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-secondary)', lineHeight: 1.7 }}>{p.detail}</div>
              {item && (
                <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center', marginTop: '2px' }}>
                  {item.kind === '无依据步骤' && sid && (
                    <>
                      <button type="button" className="btn-secondary btn-sm" onClick={() => onPickForStep(sid)} data-testid="resolve-add-ref">补一条依据</button>
                      <button type="button" className="btn-secondary btn-sm" onClick={() => onMarkStepExpert(sid)} data-testid="resolve-expert-step">这是我们的经验，写理由</button>
                      <button type="button" className="btn-danger-ghost btn-sm" onClick={() => onDeleteStep(sid)} data-testid="resolve-delete">删掉这一步</button>
                    </>
                  )}
                  {item.kind === '例外未落点' && p.coveredBy && (
                    <button type="button" className="btn-primary btn-sm" data-testid="resolve-covered"
                      onClick={() => onResolve([[item.item_key, `已在${p.coveredBy}中写明`]])}>确认{p.coveredBy}已写到</button>
                  )}
                  {item.kind === '例外未落点' && item.value && (
                    <button type="button" className="btn-secondary btn-sm" onClick={() => onLand(item.value as string)} data-testid="resolve-land">加入转人工的情形</button>
                  )}
                  {item.kind === '疑似无依据数值' && sid && (
                    <button type="button" className="btn-secondary btn-sm" onClick={() => onPickForStep(sid)}>补一条依据</button>
                  )}
                </div>
              )}
              {expertInput && (
                <input style={{ ...inputStyle, height: '30px', fontSize: 'var(--font-size-sm)' }} data-testid="resolve-expert-reason"
                  placeholder="或者写一句为什么不用改（例如：该特殊情况不适用本任务、数值来自项目惯例）"
                  value={resolutions[item!.item_key]?.reason || ''}
                  onChange={(e) => (e.target.value ? onResolve([[item!.item_key, e.target.value]]) : onClear(item!.item_key))} />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};
