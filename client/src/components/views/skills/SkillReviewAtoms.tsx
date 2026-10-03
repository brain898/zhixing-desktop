import React, { useEffect, useState } from 'react';
import { AlertTriangle, ChevronDown, ChevronRight, FileText, Loader2, Plus, Search, X } from 'lucide-react';
import { api } from '../../../services/api';
import { PrimaryCategory, SkillAtomPanelItem, SkillPoolAtom } from '../../../types';
import { StructuredReview } from '../knowledge/StructuredReview';
import { errorMessage } from './sceneShared';
import { ModalShell, REF_ROLES, iconButton, inputStyle, plainText, smallLabel, stepLabel } from './skillReviewShared';

export interface PickedAtom {
  atom_item_id: string;
  atom_version_id: string;
  title: string;
  primary_category: string | null;
  atom_type: string | null;
}

// PRD 4.3 五类主分类到默认引用角色（atom_type 更细时以 atom_type 为准），与服务端 skill_validation 一致
const CATEGORY_ROLE: Record<string, string> = {
  制度与标准: '判断规则', 方法与工具: '执行动作', 指标数据: '指标口径', 项目案例: '案例参考', 专家经验: '例外处理',
};
const TYPE_ROLE: Record<string, string> = { 判断: '判断规则', 规则: '判断规则', 方法: '执行动作', 案例: '案例参考', 指标: '指标口径', 经验: '例外处理' };
export const defaultRole = (atom: { primary_category: string | null; atom_type: string | null }) =>
  (atom.atom_type === '判断' ? TYPE_ROLE['判断'] : CATEGORY_ROLE[atom.primary_category || '']) || TYPE_ROLE[atom.atom_type || ''] || '判断规则';

const SOURCE_LABEL: Record<string, string> = { semantic: '内容相关', outside_pool: '审核时补充' };

interface AtomColumnProps {
  atoms: SkillAtomPanelItem[];
  editable: boolean;
  selectedAtom: string | null;
  highlightAtoms: Set<string>;
  /** 当前选中的步骤：面板先列它依据的知识，其余收在下方 */
  focusStep: { sid: string; label: string; refs: string[] } | null;
  stepOrder: Map<string, number>;
  onSelectAtom: (vid: string | null) => void;
  onSelectStep: (sid: string) => void;
  onClearStep: () => void;
  onRoleChange: (vid: string, role: string) => void;
  onRemove: (vid: string) => void;
  onAdd: () => void;
  onLand?: (text: string) => void;
}

/** 右侧依据面板：选中步骤时先列该步依据，否则按用途分组列全部；可展开结构字段与原文证据（FR10） */
export const AtomColumn: React.FC<AtomColumnProps> = ({
  atoms, editable, selectedAtom, highlightAtoms, focusStep, stepOrder, onSelectAtom, onSelectStep, onClearStep, onRoleChange, onRemove, onAdd, onLand,
}) => {
  const card = (atom: SkillAtomPanelItem, showRole: boolean) => (
    <AtomCard
      key={atom.atom_version_id}
      atom={atom}
      editable={editable}
      showRole={showRole}
      stepOrder={stepOrder}
      selected={selectedAtom === atom.atom_version_id}
      highlighted={highlightAtoms.has(atom.atom_version_id)}
      onSelect={() => onSelectAtom(selectedAtom === atom.atom_version_id ? null : atom.atom_version_id)}
      onSelectStep={onSelectStep}
      onRoleChange={(role) => onRoleChange(atom.atom_version_id, role)}
      onRemove={() => onRemove(atom.atom_version_id)}
      onLand={onLand}
    />
  );
  const heading = (eyebrow: string, title: string) => (
    <div style={{ flex: 1, minWidth: 0 }}>
      <div style={{ fontSize: 'var(--font-size-xs)', fontWeight: 600, color: 'var(--text-muted)', letterSpacing: '1px' }}>{eyebrow}</div>
      <div style={{ fontSize: 'var(--font-size-section)', fontWeight: 700, color: 'var(--text-primary)', marginTop: '2px' }}>{title}</div>
    </div>
  );
  const addButton = editable && (
    <button type="button" className="btn-secondary btn-sm" onClick={onAdd} data-testid="atom-add">
      <Plus size={13} />添加依据
    </button>
  );

  if (focusStep) {
    const own = focusStep.refs.map((vid) => atoms.find((a) => a.atom_version_id === vid)).filter(Boolean) as SkillAtomPanelItem[];
    const rest = atoms.filter((a) => !focusStep.refs.includes(a.atom_version_id));
    const invalid = own.filter((a) => !a.eligible).length;
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }} data-testid="atom-column" data-focus-step={focusStep.sid}>
        <div style={{ display: 'flex', alignItems: 'flex-end', gap: '8px' }}>
          {heading(`${focusStep.label}依据的知识`, own.length === 0 ? '这一步没有依据' : `${own.length} 条${invalid ? `，${invalid} 条已失效` : '，都还有效'}`)}
          <button type="button" className="btn-ghost btn-sm" onClick={onClearStep} data-testid="atom-show-all">看全部 {atoms.length} 条</button>
        </div>
        {own.length === 0 && (
          <div style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-secondary)', lineHeight: 1.7, backgroundColor: 'var(--warning-bg)', borderRadius: 'var(--radius-md)', padding: '10px 12px' }}>
            知识库里没有找到这一步的出处。可以在左侧这一步下面补一条依据，或说明这是常规操作、你的经验。
          </div>
        )}
        {own.map((a) => card(a, true))}
        {rest.length > 0 && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginTop: '8px' }}>
            <span style={{ fontSize: 'var(--font-size-xs)', fontWeight: 600, color: 'var(--text-muted)', letterSpacing: '1px', flex: 1 }}>其他依据知识 {rest.length} 条</span>
            {addButton}
          </div>
        )}
        {rest.map((a) => card(a, true))}
      </div>
    );
  }

  const groups = REF_ROLES.map((role) => ({ role, items: atoms.filter((a) => a.role === role) }))
    .filter((g) => g.items.length > 0);
  const others = atoms.filter((a) => !REF_ROLES.includes(a.role));
  if (others.length) groups.push({ role: '其他', items: others });
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }} data-testid="atom-column">
      <div style={{ display: 'flex', alignItems: 'flex-end', gap: '8px' }}>
        {heading('全部依据知识', `${atoms.length} 条`)}
        {addButton}
      </div>
      {atoms.length > 0 && <div style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-muted)', marginTop: '-8px' }}>点左侧某一步，这里只看它用到的知识</div>}
      {groups.map((g) => (
        <div key={g.role} data-testid="atom-group" data-role={g.role}>
          <div style={{ fontSize: 'var(--font-size-xs)', fontWeight: 600, color: 'var(--text-muted)', letterSpacing: '1px', marginBottom: '8px' }}>
            用作{g.role} · {g.items.length}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            {g.items.map((a) => card(a, false))}
          </div>
        </div>
      ))}
      {atoms.length === 0 && <div style={smallLabel}>还没有引用任何知识。</div>}
    </div>
  );
};

const AtomCard: React.FC<{
  atom: SkillAtomPanelItem;
  editable: boolean;
  showRole: boolean;
  stepOrder: Map<string, number>;
  selected: boolean;
  highlighted: boolean;
  onSelect: () => void;
  onSelectStep: (sid: string) => void;
  onRoleChange: (role: string) => void;
  onRemove: () => void;
  onLand?: (text: string) => void;
}> = ({ atom, editable, showRole, stepOrder, selected, highlighted, onSelect, onSelectStep, onRoleChange, onRemove, onLand }) => {
  const [open, setOpen] = useState(false);
  const roleMismatch = atom.default_roles.length > 0 && !atom.default_roles.includes(atom.role);
  const steps = [...atom.used_in_steps].sort((a, b) => (stepOrder.get(a) ?? 99) - (stepOrder.get(b) ?? 99));
  const active = highlighted || selected;
  return (
    <div
      id={`atom-${atom.atom_version_id}`}
      data-testid="atom-card"
      data-vid={atom.atom_version_id}
      data-highlight={active ? 'true' : undefined}
      style={{
        padding: '12px 14px',
        borderRadius: 'var(--radius-md)',
        backgroundColor: active ? 'var(--bg-primary)' : 'var(--bg-secondary)',
        boxShadow: selected ? '0 0 0 2px var(--brand-500)' : highlighted ? '0 0 0 1px var(--brand-300), var(--shadow-sm)' : undefined,
        scrollMarginTop: '12px',
        transition: 'box-shadow 160ms ease, background-color 160ms ease',
      }}
    >
      <div style={{ display: 'flex', gap: '8px', alignItems: 'flex-start' }}>
        <button type="button" onClick={onSelect} data-testid="atom-select" title="点一下，左侧标出用到它的步骤"
          style={{ flex: 1, minWidth: 0, textAlign: 'left', border: 0, background: 'none', padding: 0, cursor: 'pointer', display: 'flex', flexDirection: 'column', gap: '4px' }}>
          <span style={{ display: 'flex', alignItems: 'baseline', gap: '8px' }}>
            <span style={{ flex: 1, fontSize: 'var(--font-size-base)', fontWeight: 600, color: 'var(--text-primary)', lineHeight: 1.5 }}>{atom.title}</span>
            {atom.primary_category && <span style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)', flexShrink: 0 }}>{atom.primary_category}</span>}
          </span>
          {atom.statement && (
            <span style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-secondary)', lineHeight: 1.65,
              ...(open || active ? {} : { display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' as const, overflow: 'hidden' }) }}>
              {plainText(atom.statement)}
            </span>
          )}
        </button>
        {editable && (
          <button type="button" style={iconButton} onClick={onRemove} title="移除这条依据" aria-label="移除这条依据" data-testid="atom-remove">
            <X size={14} />
          </button>
        )}
      </div>
      {(atom.recall_source && SOURCE_LABEL[atom.recall_source]) || !atom.eligible ? (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px', marginTop: '6px' }}>
          {atom.recall_source && SOURCE_LABEL[atom.recall_source] && (
            <span className="zx-tag warning" data-testid="atom-source">{SOURCE_LABEL[atom.recall_source]}</span>
          )}
          {!atom.eligible && <span className="zx-tag danger" data-testid="atom-ineligible">{atom.exists ? '已停用或失效' : '已不存在'}</span>}
          {atom.eligible === false && atom.has_newer_version && <span className="zx-tag danger">已有新版本</span>}
        </div>
      ) : null}
      {atom.exceptions_not_landed.length > 0 && (
        <div data-testid="atom-exception-not-landed" style={{ marginTop: '8px', fontSize: 'var(--font-size-xs)', color: 'var(--warning-text)', lineHeight: 1.6, display: 'flex', alignItems: 'flex-start', gap: '6px', flexWrap: 'wrap' }}>
          <AlertTriangle size={12} style={{ marginTop: '3px', flexShrink: 0 }} />
          <span style={{ flex: 1, minWidth: '160px' }}>写了特殊情况「{atom.exceptions_not_landed.map(plainText).join('」「')}」，转人工或不适用范围里还没有对应</span>
          {onLand && atom.exceptions_not_landed.length === 1 && (
            <button type="button" className="btn-ghost btn-sm" onClick={() => onLand(atom.exceptions_not_landed[0])}>加入转人工</button>
          )}
        </div>
      )}
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap', marginTop: '8px', fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)' }}>
        {editable ? (
          <select value={atom.role} onChange={(e) => onRoleChange(e.target.value)} data-testid="atom-role" aria-label="用途"
            style={{ ...inputStyle, width: 'auto', height: '26px', fontSize: 'var(--font-size-xs)' }}>
            {REF_ROLES.map((r) => <option key={r} value={r}>用作{r}</option>)}
          </select>
        ) : showRole && <span>用作{atom.role}</span>}
        {roleMismatch && <span style={{ color: 'var(--warning-text)' }}>（这类知识一般用作{atom.default_roles.join('或')}）</span>}
        {steps.length === 0 ? <span>没有步骤用到</span> : <span>用在</span>}
        {steps.map((sid) => (
          <button key={sid} type="button" className="zx-tag neutral" onClick={() => onSelectStep(sid)}
            style={{ border: 0, cursor: 'pointer' }} data-testid="atom-step-link">{stepLabel(stepOrder, sid)}</button>
        ))}
        <span style={{ flex: 1 }} />
        <button type="button" style={{ ...iconButton, gap: '2px', fontSize: 'var(--font-size-xs)' }} onClick={() => setOpen(!open)} aria-label={open ? '收起' : '展开'}>
          {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}{open ? '收起原文' : '看原文'}
        </button>
      </div>
      {open && <AtomDetail atom={atom} />}
    </div>
  );
};

/** 展开：结构字段（复用 M01 StructuredReview）与原文证据（M01 知识版本接口） */
const AtomDetail: React.FC<{ atom: SkillAtomPanelItem }> = ({ atom }) => {
  const [evidence, setEvidence] = useState<any[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    api.getKnowledgeVersionHistory(atom.atom_item_id, atom.atom_version_id)
      .then((res) => alive && setEvidence(res.evidence || []))
      .catch((err) => alive && setError(errorMessage(err, '原文暂时无法读取')));
    return () => { alive = false; };
  }, [atom.atom_item_id, atom.atom_version_id]);
  return (
    <div style={{ marginTop: '10px', display: 'flex', flexDirection: 'column', gap: '10px' }} data-testid="atom-detail">
      <StructuredReview
        hideStatement
        item={{
          statement: atom.statement,
          primary_category: (atom.primary_category || null) as PrimaryCategory | null,
          subject: atom.subject || '',
          conditions: atom.conditions,
          actions: atom.actions,
          exceptions: atom.exceptions,
          metric_definition: atom.metric_definition,
          case_details: atom.case_details,
        }}
      />
      <div>
        <div style={{ ...smallLabel, display: 'flex', alignItems: 'center', gap: '4px', marginBottom: '6px' }}>
          <FileText size={13} color="var(--brand-accent)" />原文证据
        </div>
        {error && <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--warning-text)' }}>{error}（下方为引用时保存的内容）</div>}
        {!evidence && !error && <div style={smallLabel}><Loader2 size={12} className="spin-slow" /> 正在读取…</div>}
        {evidence && evidence.length === 0 && <div style={smallLabel}>这条知识没有记录原文位置。</div>}
        {evidence && evidence.map((ev) => (
          <div key={ev.id} data-testid="atom-evidence" style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border-color)', borderRadius: 'var(--radius-md)', padding: '10px 12px', marginBottom: '6px' }}>
            <div style={{ display: 'flex', gap: '6px', alignItems: 'center', fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)', marginBottom: '4px' }}>
              <span className="zx-tag neutral">段落 {ev.block_index !== undefined && ev.block_index !== null ? `#${ev.block_index + 1}` : ''}</span>
              {ev.heading_path && <span>{ev.heading_path}</span>}
            </div>
            <div style={{ fontSize: 'var(--font-size-sm)', lineHeight: 1.75, color: 'var(--text-primary)', whiteSpace: 'pre-wrap', maxHeight: '200px', overflowY: 'auto' }}>
              {ev.excerpt && ev.text_content && ev.text_content.includes(ev.excerpt) ? (
                <>
                  {ev.text_content.slice(0, ev.text_content.indexOf(ev.excerpt))}
                  <mark style={{ background: 'var(--highlight-bg)' }}>{ev.excerpt}</mark>
                  {ev.text_content.slice(ev.text_content.indexOf(ev.excerpt) + ev.excerpt.length)}
                </>
              ) : (ev.text_content || ev.excerpt)}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

/** 新增依据：本批次找到的知识，或在全部正式知识中查找（M01 正式检索，只返回可用知识） */
export const AtomPicker: React.FC<{
  pool: (SkillPoolAtom & { eligible: boolean })[];
  existing: Set<string>;
  targetStep: string | null;
  targetLabel?: string | null;
  onPick: (atom: PickedAtom) => void;
  onClose: () => void;
}> = ({ pool, existing, targetStep, targetLabel, onPick, onClose }) => {
  const [tab, setTab] = useState<'pool' | 'search'>('pool');
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<PickedAtom[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const search = async () => {
    if (!query.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.searchKnowledge({ q: query.trim() });
      setResults(res.items.map((r) => ({
        atom_item_id: r.item_id, atom_version_id: r.version_id, title: r.title,
        primary_category: r.primary_category, atom_type: r.atom_type,
      })));
    } catch (err) {
      setError(errorMessage(err, '查找失败，请稍后再试'));
    } finally {
      setBusy(false);
    }
  };
  const row = (a: PickedAtom, disabledReason?: string) => {
    const used = existing.has(a.atom_version_id);
    return (
      <div key={a.atom_version_id} data-testid="picker-row" style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '8px 0', borderBottom: '1px solid var(--bg-sunken)' }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-primary)' }}>{a.title}</div>
          <div style={smallLabel}>{a.primary_category || '待分类'}</div>
        </div>
        {disabledReason ? <span style={{ fontSize: 'var(--font-size-xs)', color: 'var(--danger-text)' }}>{disabledReason}</span> : (
          <button type="button" className="btn-secondary btn-sm" disabled={used && !targetStep} onClick={() => onPick(a)} data-testid="picker-add">
            {used ? (targetStep ? '用于该步骤' : '已引用') : '添加'}
          </button>
        )}
      </div>
    );
  };
  return (
    <ModalShell title="添加依据知识" subtitle={targetStep ? `添加后用于${targetLabel || `步骤 ${targetStep}`}` : '只能选择当前有效、已确认的知识'} width={620} onClose={onClose} testId="atom-picker">
      <div style={{ display: 'flex', gap: '8px', marginBottom: '12px' }}>
        <button type="button" className={tab === 'pool' ? 'btn-primary btn-sm' : 'btn-secondary btn-sm'} onClick={() => setTab('pool')} data-testid="picker-tab-pool">本次找到的知识</button>
        <button type="button" className={tab === 'search' ? 'btn-primary btn-sm' : 'btn-secondary btn-sm'} onClick={() => setTab('search')} data-testid="picker-tab-search">在全部知识中查找</button>
      </div>
      {tab === 'pool' ? (
        <div style={{ maxHeight: '420px', overflow: 'auto' }}>
          {pool.length === 0 && <div style={smallLabel}>这条 Skill 没有生成记录，请在全部知识中查找。</div>}
          {pool.map((a) => row({ atom_item_id: a.atom_item_id, atom_version_id: a.atom_version_id, title: a.title, primary_category: a.primary_category, atom_type: a.atom_type },
            a.eligible ? undefined : '已停用或失效'))}
        </div>
      ) : (
        <div>
          <div style={{ display: 'flex', gap: '8px', marginBottom: '8px' }}>
            <input style={inputStyle} value={query} placeholder="输入要找的内容，例如「收缴率口径」" onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && search()} data-testid="picker-query" />
            <button type="button" className="btn-primary btn-sm" onClick={search} disabled={busy || !query.trim()} data-testid="picker-search">
              {busy ? <Loader2 size={13} className="spin-slow" /> : <Search size={13} />}查找
            </button>
          </div>
          {error && <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--danger-text)' }}>{error}</div>}
          <div style={{ maxHeight: '380px', overflow: 'auto' }}>
            {results && results.length === 0 && <div style={smallLabel}>没有找到相关的知识。</div>}
            {results && results.map((a) => row(a))}
          </div>
        </div>
      )}
    </ModalShell>
  );
};
