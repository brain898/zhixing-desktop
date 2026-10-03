import React, { useMemo, useState } from 'react';
import { Loader2, RotateCcw, Check, MoreHorizontal } from 'lucide-react';
import { api } from '../../../services/api';
import { Scene, SceneMergeGroup, SceneMergeSuggestion } from '../../../types';
import { ActionMenu, ActionMenuItem, ErrorNotice, errorMessage, fieldLabelStyle, textAreaStyle } from './sceneShared';

interface DraftGroup {
  key: string;
  accepted: boolean;
  name: string;
  description: string;
  problemsText: string;
  tags: string[];
  reason?: string;
  editing: boolean;
  /** 整理新标签时，归入的已有场景 */
  targetSceneId?: string;
  /** 由拖拽或菜单临时创建；标签被移空后自动移除 */
  autoCreated?: boolean;
}

const UNASSIGNED = '__unassigned__';

let groupSeq = 0;
const nextKey = () => `g${++groupSeq}`;

interface SceneMergeReviewProps {
  suggestion: SceneMergeSuggestion;
  /** 整理新标签时可归入的已有场景（启用中） */
  existingScenes?: Scene[];
  onChanged: () => void;
}

const centerCard: React.CSSProperties = {
  width: '520px',
  padding: '28px',
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  gap: '10px',
  textAlign: 'center',
};

/**
 * 场景整理结果确认：进行中、失败、逐组确认三种状态。
 * 首次整理：确认后生成场景；整理新标签：新标签归入已有场景（只追加标签）或组成新场景。
 */
export const SceneMergeReview: React.FC<SceneMergeReviewProps> = ({ suggestion, existingScenes = [], onChanged }) => {
  const incremental = suggestion.mode === 'incremental';
  const [groups, setGroups] = useState<DraftGroup[]>(() =>
    suggestion.groups.map((g) => ({
      key: nextKey(),
      accepted: true,
      name: g.name,
      description: g.description,
      problemsText: g.typical_problems.join('\n'),
      tags: [...g.tags],
      reason: g.reason,
      editing: false,
      targetSceneId: g.existing && g.target_scene_id ? g.target_scene_id : undefined,
    }))
  );
  const [unassigned, setUnassigned] = useState<string[]>(() => [...suggestion.unassigned_tags]);
  const [busy, setBusy] = useState<null | 'confirm' | 'discard' | 'retry'>(null);
  const [error, setError] = useState<string | null>(null);
  // 拖拽中的标签及其来源分组；overKey 为当前悬停的放置目标（分组 key、'new' 或 UNASSIGNED）
  const [dragging, setDragging] = useState<{ tag: string; from: string } | null>(null);
  const [overKey, setOverKey] = useState<string | null>(null);

  // 每个标签对应的知识，用于显示「涉及几条知识」
  const atomsByTag = useMemo(() => {
    const map: Record<string, string[]> = {};
    suggestion.tag_stats.forEach((t) => {
      map[t.tag] = t.atoms.map((a) => a.version_id);
    });
    return map;
  }, [suggestion.tag_stats]);

  const knowledgeCount = (tags: string[]) => new Set(tags.flatMap((t) => atomsByTag[t] || [])).size;

  const retry = async () => {
    setBusy('retry');
    setError(null);
    try {
      await api.requestSceneMergeSuggestion();
      onChanged();
    } catch (err) {
      setError(errorMessage(err, '暂时无法重新整理，请稍后再试'));
    } finally {
      setBusy(null);
    }
  };

  const discard = async () => {
    setBusy('discard');
    setError(null);
    try {
      await api.discardSceneMergeSuggestion(suggestion.suggestion_id);
      onChanged();
    } catch (err) {
      setError(errorMessage(err, '操作失败，请稍后再试'));
      setBusy(null);
    }
  };

  if (suggestion.status === 'queued' || suggestion.status === 'running') {
    return (
      <div style={{ padding: '64px 32px', display: 'flex', justifyContent: 'center' }}>
        <div className="zx-card" data-testid="merge-suggestion-running" style={centerCard}>
          <Loader2 size={22} className="spin-slow" color="var(--brand-600)" />
          <div style={{ fontSize: 'var(--font-size-md)', fontWeight: 600 }}>{incremental ? '正在整理新标签' : '正在整理业务场景'}</div>
          <div style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-secondary)', lineHeight: 1.7 }}>
            {incremental
              ? `正在为 ${suggestion.tag_stats.length} 个新标签找合适的场景，通常十几秒就好。`
              : `正在把 ${suggestion.tag_stats.length} 个场景标签归成几个场景，通常十几秒就好。`}
          </div>
        </div>
      </div>
    );
  }

  if (suggestion.status === 'failed') {
    return (
      <div style={{ padding: '64px 32px', display: 'flex', justifyContent: 'center' }}>
        <div className="zx-card" data-testid="merge-suggestion-failed" style={centerCard}>
          <div style={{ fontSize: 'var(--font-size-md)', fontWeight: 600 }}>这次没有整理成功</div>
          <div style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-secondary)' }}>可以再试一次，已有数据不受影响。</div>
          {error && <ErrorNotice message={error} />}
          <div style={{ display: 'flex', gap: '8px', marginTop: '6px' }}>
            {incremental && (
              <button type="button" className="btn-ghost" onClick={discard} disabled={busy !== null}>返回</button>
            )}
            <button type="button" className="btn-primary" onClick={retry} disabled={busy !== null}>
              {busy === 'retry' ? <Loader2 size={14} className="spin-slow" /> : <RotateCcw size={14} />}
              重新整理
            </button>
          </div>
        </div>
      </div>
    );
  }

  const updateGroup = (key: string, patch: Partial<DraftGroup>) =>
    setGroups((prev) => prev.map((g) => (g.key === key ? { ...g, ...patch } : g)));

  /** 把标签移到目标：已有分组 key、'new'（单独成新场景）、'scene:<id>'（归入尚未出现的已有场景）或 UNASSIGNED */
  const moveTag = (tag: string, fromKey: string, target: string) => {
    setGroups((prev) => {
      let next = prev.map((g) => (g.key === fromKey ? { ...g, tags: g.tags.filter((t) => t !== tag) } : g));
      if (target === 'new') {
        next = [...next, { key: nextKey(), accepted: true, name: tag, description: '', problemsText: '', tags: [tag], editing: false, autoCreated: true }];
      } else if (target.startsWith('scene:')) {
        const scene = existingScenes.find((s) => s.scene_id === target.slice(6));
        if (scene) {
          next = [
            ...next,
            { key: nextKey(), accepted: true, name: scene.name, description: scene.description, problemsText: '', tags: [tag], editing: false, targetSceneId: scene.scene_id, autoCreated: true },
          ];
        }
      } else if (target !== UNASSIGNED) {
        next = next.map((g) => (g.key === target ? { ...g, tags: [...g.tags, tag] } : g));
      }
      return next.filter((g) => !(g.autoCreated && g.tags.length === 0));
    });
    if (fromKey === UNASSIGNED) setUnassigned((prev) => prev.filter((t) => t !== tag));
    if (target === UNASSIGNED) setUnassigned((prev) => [...prev, tag]);
  };

  const mergeInto = (fromKey: string, toKey: string) => {
    setGroups((prev) => {
      const from = prev.find((g) => g.key === fromKey);
      if (!from) return prev;
      return prev
        .filter((g) => g.key !== fromKey)
        .map((g) => (g.key === toKey ? { ...g, tags: [...g.tags, ...from.tags.filter((t) => !g.tags.includes(t))] } : g));
    });
  };

  const accepted = groups.filter((g) => g.accepted);
  const acceptedNew = accepted.filter((g) => !g.targetSceneId);
  const acceptedExisting = accepted.filter((g) => g.targetSceneId && g.tags.length > 0);
  const validationErrors: string[] = [];
  const nameSeen = new Set<string>(
    incremental ? existingScenes.flatMap((s) => [s.name, ...s.aliases]).map((n) => n.replace(/\s+/g, '').toLowerCase()) : []
  );
  acceptedNew.forEach((g) => {
    const norm = g.name.replace(/\s+/g, '').toLowerCase();
    if (!norm) validationErrors.push('有场景还没填名称');
    else if (nameSeen.has(norm)) validationErrors.push(`场景名称「${g.name}」和已有场景或标签重复了`);
    nameSeen.add(norm);
  });
  if (acceptedNew.length + acceptedExisting.length === 0) validationErrors.push('至少保留一个场景');
  const leftoverCount =
    unassigned.length + groups.filter((g) => !g.accepted).reduce((sum, g) => sum + g.tags.length, 0);

  const confirm = async () => {
    if (validationErrors.length) return;
    setBusy('confirm');
    setError(null);
    const payload: SceneMergeGroup[] = [...acceptedExisting, ...acceptedNew].map((g) => ({
      name: g.name.trim(),
      description: g.description.trim(),
      typical_problems: g.problemsText.split('\n').map((s) => s.trim()).filter(Boolean),
      tags: g.tags,
      reason: g.reason,
      target_scene_id: g.targetSceneId || null,
    }));
    try {
      await api.confirmSceneMergeSuggestion(suggestion.suggestion_id, payload);
      onChanged();
    } catch (err) {
      setError(errorMessage(err, '确认失败，请稍后再试'));
      setBusy(null);
    }
  };

  const presentSceneIds = new Set(groups.map((g) => g.targetSceneId).filter(Boolean));
  const otherScenes = incremental ? existingScenes.filter((sc) => !presentSceneIds.has(sc.scene_id)) : [];

  /** 放置目标的拖拽事件：分组 key、'new'（单独成新场景）或 UNASSIGNED（先不分组） */
  const dropHandlers = (targetKey: string) => ({
    onDragOver: (e: React.DragEvent) => {
      if (!dragging) return;
      e.preventDefault();
      e.stopPropagation();
      e.dataTransfer.dropEffect = 'move';
      if (overKey !== targetKey) setOverKey(targetKey);
    },
    onDragLeave: (e: React.DragEvent) => {
      if (!e.currentTarget.contains(e.relatedTarget as Node | null)) {
        setOverKey((k) => (k === targetKey ? null : k));
      }
    },
    onDrop: (e: React.DragEvent) => {
      if (!dragging) return;
      e.preventDefault();
      e.stopPropagation();
      const d = dragging;
      setDragging(null);
      setOverKey(null);
      if (d.from !== targetKey) moveTag(d.tag, d.from, targetKey);
    },
  });

  const dropHighlight = (targetKey: string): React.CSSProperties =>
    overKey === targetKey ? { boxShadow: '0 0 0 2px var(--brand-500)', backgroundColor: 'var(--brand-50)' } : {};

  const tagChip = (tag: string, fromKey: string) => {
    const count = (atomsByTag[tag] || []).length;
    return (
      <span
        key={tag}
        draggable={busy === null}
        data-testid="tag-chip"
        data-tag={tag}
        title="按住拖到其他场景；拖到空白处就单独成为新场景"
        className="zx-tag neutral"
        onDragStart={(e) => {
          e.dataTransfer.effectAllowed = 'move';
          e.dataTransfer.setData('text/plain', tag);
          setDragging({ tag, from: fromKey });
        }}
        onDragEnd={() => {
          setDragging(null);
          setOverKey(null);
        }}
        style={{
          cursor: busy === null ? 'grab' : 'default',
          gap: '4px',
          height: '26px',
          padding: '0 10px',
          fontSize: 'var(--font-size-sm)',
          userSelect: 'none',
          opacity: dragging?.tag === tag ? 0.4 : 1,
        }}
      >
        <span>{tag}</span>
        {count > 0 && <span style={{ color: 'var(--text-muted)', fontSize: 'var(--font-size-xs)' }}>{count}</span>}
      </span>
    );
  };

  const steps = incremental
    ? ['检查每个新标签的去处', '放错了就按住标签，拖到对的场景', '确认']
    : ['检查分组是否合理', '名称不合适直接改，标签放错了按住拖到对的场景', '确认生成场景'];

  const confirmLabel = incremental
    ? `确认（归入 ${acceptedExisting.length} 个已有场景，新建 ${acceptedNew.length} 个）`
    : `确认，生成 ${accepted.length} 个场景`;

  return (
    <div style={{ minHeight: '100%' }} {...dropHandlers('new')}>
    <div
      style={{ padding: '24px 32px 40px', display: 'flex', flexDirection: 'column', gap: '14px', maxWidth: '920px', width: '100%', margin: '0 auto' }}
      data-testid="merge-suggestion-review"
      data-mode={suggestion.mode}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '16px' }}>
        <div>
          <div style={{ fontSize: 'var(--font-size-title)', fontWeight: 600 }}>{incremental ? '整理新标签' : '确认场景分组'}</div>
          <div style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-secondary)', marginTop: '4px' }}>
            {incremental
              ? `有 ${suggestion.tag_stats.length} 个新标签还没归入场景。系统建议了去处：归入已有场景，或组成新场景。已有场景只会多出新标签，名称和说明不变。`
              : '系统已把知识库里的场景标签分好了组，检查无误后确认，就可以按场景生产 Skill。'}
          </div>
        </div>
        <div style={{ display: 'flex', gap: '8px', flexShrink: 0 }}>
          <button type="button" className="btn-ghost" onClick={discard} disabled={busy !== null}>放弃</button>
          <button type="button" className="btn-primary" data-testid="merge-confirm" onClick={confirm} disabled={busy !== null || validationErrors.length > 0}>
            {busy === 'confirm' ? <Loader2 size={14} className="spin-slow" /> : <Check size={14} />}
            {confirmLabel}
          </button>
        </div>
      </div>

      <ol
        data-testid="merge-steps"
        style={{ display: 'flex', gap: '20px', margin: 0, padding: '10px 14px', listStyle: 'none', borderRadius: 'var(--radius-md)', backgroundColor: 'var(--brand-50)', fontSize: 'var(--font-size-sm)', color: 'var(--brand-700)' }}
      >
        {steps.map((step, i) => (
          <li key={step} style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <span style={{ width: '18px', height: '18px', borderRadius: '50%', backgroundColor: 'var(--brand-600)', color: '#fff', fontSize: 'var(--font-size-xs)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
              {i + 1}
            </span>
            {step}
          </li>
        ))}
      </ol>

      {error && <ErrorNotice message={error} onClose={() => setError(null)} />}
      {validationErrors.length > 0 && <ErrorNotice message={validationErrors.join('；')} />}

      {groups.map((g) => {
        const isExisting = !!g.targetSceneId;
        const moreItems: ActionMenuItem[] = isExisting
          ? []
          : [
              { label: g.editing ? '收起说明' : '编辑说明和常见问题', onSelect: () => updateGroup(g.key, { editing: !g.editing }) },
              ...groups
                .filter((o) => o.key !== g.key && o.accepted)
                .map((o) => ({ label: `并入「${o.name || '未命名场景'}」`, onSelect: () => mergeInto(g.key, o.key) })),
            ];
        return (
          <div
            key={g.key}
            className="zx-card"
            data-testid="merge-group"
            data-existing={isExisting ? 'true' : 'false'}
            {...dropHandlers(g.key)}
            style={{
              padding: '14px 18px',
              display: 'flex',
              flexDirection: 'column',
              gap: '10px',
              opacity: g.accepted ? 1 : 0.55,
              transition: 'box-shadow 120ms ease, background-color 120ms ease',
              ...dropHighlight(g.key),
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
              <input
                type="checkbox"
                aria-label={isExisting ? '把这些新标签归入该场景' : '保留这个场景'}
                title={g.accepted ? '取消勾选则不处理这一组' : '勾选后处理这一组'}
                checked={g.accepted}
                onChange={(e) => updateGroup(g.key, { accepted: e.target.checked })}
                style={{ width: '16px', height: '16px', cursor: 'pointer' }}
              />
              {isExisting ? (
                <>
                  <span style={{ fontSize: 'var(--font-size-base)', fontWeight: 600, padding: '0 2px' }}>{g.name}</span>
                  <span className="zx-tag">已有场景</span>
                </>
              ) : (
                <>
                  <input
                    className="zx-input"
                    aria-label="场景名称"
                    value={g.name}
                    maxLength={30}
                    onChange={(e) => updateGroup(g.key, { name: e.target.value })}
                    style={{ maxWidth: '260px', fontWeight: 600, fontSize: 'var(--font-size-base)' }}
                  />
                  {incremental && <span className="zx-tag warning">新场景</span>}
                </>
              )}
              <span style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-muted)' }}>
                {!g.accepted ? '不处理' : isExisting ? `新增 ${knowledgeCount(g.tags)} 条知识` : `涉及 ${knowledgeCount(g.tags)} 条知识`}
              </span>
              <div style={{ flex: 1 }} />
              {moreItems.length > 0 && (
                <ActionMenu
                  label="更多操作"
                  align="right"
                  triggerClassName="btn-ghost btn-sm btn-icon"
                  triggerStyle={{ width: '28px' }}
                  trigger={<MoreHorizontal size={16} />}
                  items={moreItems}
                />
              )}
            </div>

            {!g.editing && g.description && (
              <div style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-secondary)', paddingLeft: '26px' }}>{g.description}</div>
            )}

            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', alignItems: 'center', paddingLeft: '26px' }}>
              {isExisting && g.tags.length > 0 && (
                <span style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)' }}>将加入：</span>
              )}
              {g.tags.length ? g.tags.map((t) => tagChip(t, g.key)) : (
                <span style={{ fontSize: 'var(--font-size-xs)', color: isExisting ? 'var(--text-muted)' : 'var(--warning-text)' }}>
                  {isExisting ? '没有要加入的新标签' : '这个场景还没有标签'}
                </span>
              )}
            </div>

            {g.editing && !isExisting && (
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px', paddingLeft: '26px' }}>
                <div>
                  <label style={fieldLabelStyle}>一句话说明这个场景管什么</label>
                  <textarea style={textAreaStyle} rows={2} value={g.description} onChange={(e) => updateGroup(g.key, { description: e.target.value })} />
                </div>
                <div>
                  <label style={fieldLabelStyle}>常见问题（选填，每行一个）</label>
                  <textarea style={textAreaStyle} rows={2} value={g.problemsText} onChange={(e) => updateGroup(g.key, { problemsText: e.target.value })} />
                </div>
              </div>
            )}
          </div>
        );
      })}

      {otherScenes.length > 0 && (
        <div data-testid="other-scenes-zone" style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '8px' }}>
          <span style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)' }}>也可以拖到其他已有场景：</span>
          {otherScenes.map((sc) => {
            const key = `scene:${sc.scene_id}`;
            return (
              <span
                key={sc.scene_id}
                data-testid="other-scene-target"
                data-scene-name={sc.name}
                {...dropHandlers(key)}
                style={{
                  padding: '4px 12px',
                  borderRadius: '13px',
                  border: `1.5px dashed ${overKey === key ? 'var(--brand-500)' : 'var(--border-strong)'}`,
                  backgroundColor: overKey === key ? 'var(--brand-50)' : 'var(--bg-primary)',
                  color: overKey === key ? 'var(--brand-700)' : 'var(--text-secondary)',
                  fontSize: 'var(--font-size-sm)',
                }}
              >
                {sc.name}
              </span>
            );
          })}
        </div>
      )}

      <div
        data-testid="new-scene-dropzone"
        {...dropHandlers('new')}
        style={{
          padding: '16px',
          borderRadius: 'var(--radius-md)',
          border: `1.5px dashed ${overKey === 'new' ? 'var(--brand-500)' : 'var(--border-strong)'}`,
          backgroundColor: overKey === 'new' ? 'var(--brand-50)' : 'transparent',
          color: overKey === 'new' ? 'var(--brand-700)' : 'var(--text-muted)',
          fontSize: 'var(--font-size-sm)',
          textAlign: 'center',
          transition: 'background-color 120ms ease, border-color 120ms ease',
        }}
      >
        {overKey === 'new' ? '松开鼠标，新建一个场景' : '把标签拖到这里或任意空白处，就会单独成为一个新场景'}
      </div>

      {(unassigned.length > 0 || dragging) && (
        <div
          className="zx-card"
          data-testid="unassigned-zone"
          {...dropHandlers(UNASSIGNED)}
          style={{ padding: '14px 18px', display: 'flex', flexDirection: 'column', gap: '10px', ...dropHighlight(UNASSIGNED) }}
        >
          <div style={{ fontSize: 'var(--font-size-sm)', fontWeight: 600 }}>暂未分组的标签</div>
          {unassigned.length > 0 ? (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>{unassigned.map((t) => tagChip(t, UNASSIGNED))}</div>
          ) : (
            <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)' }}>拖到这里表示先不分组，以后再处理</div>
          )}
        </div>
      )}

      {leftoverCount > 0 && (
        <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)' }}>
          没有放进场景的 {leftoverCount} 个标签，确认后会放到「管理场景」的待处理标签里，之后再归类。
        </div>
      )}
    </div>
    </div>
  );
};
