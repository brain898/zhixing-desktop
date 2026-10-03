import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Loader2, MoreHorizontal, Plus } from 'lucide-react';
import { api } from '../../../services/api';
import { Scene, SceneFormPayload, ScenePendingTag } from '../../../types';
import { AppConfirmDialog } from '../../common/AppConfirmDialog';
import { ActionMenu, ErrorNotice, SceneFormModal, errorMessage } from './sceneShared';

interface SceneCatalogManagerProps {
  scenes: Scene[];
  onChanged: () => void;
}

const EMPTY_FORM: Partial<SceneFormPayload> = {};

type FormState =
  | { mode: 'create'; initial: Partial<SceneFormPayload> }
  | { mode: 'edit'; scene: Scene; initial: Partial<SceneFormPayload> }
  | { mode: 'from_pending'; pending: ScenePendingTag; initial: Partial<SceneFormPayload> };

const sectionHint: React.CSSProperties = { fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)', marginTop: '2px' };
const metaLabel: React.CSSProperties = { fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)', flexShrink: 0 };

/** 管理场景（FR01/FR02）：场景增删改与停用、待处理标签 */
export const SceneCatalogManager: React.FC<SceneCatalogManagerProps> = ({ scenes, onChanged }) => {
  const [pending, setPending] = useState<ScenePendingTag[]>([]);
  const [pendingLoading, setPendingLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState<FormState | null>(null);
  const [deleting, setDeleting] = useState<Scene | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [mapTarget, setMapTarget] = useState<Record<string, string>>({});

  const activeScenes = useMemo(() => scenes.filter((s) => s.status === 'active'), [scenes]);

  const loadPending = useCallback(async () => {
    setPendingLoading(true);
    try {
      const res = await api.getScenePendingTags('pending');
      setPending(res.items);
    } catch (err) {
      setError(errorMessage(err, '待处理标签加载失败'));
    } finally {
      setPendingLoading(false);
    }
  }, []);

  useEffect(() => {
    loadPending();
  }, [loadPending, scenes]);

  const run = async (id: string, action: () => Promise<unknown>) => {
    setBusyId(id);
    setError(null);
    try {
      await action();
      onChanged();
    } catch (err) {
      setError(errorMessage(err, '操作失败，请稍后再试'));
    } finally {
      setBusyId(null);
    }
  };

  const submitForm = async (payload: SceneFormPayload) => {
    if (!form) return;
    if (form.mode === 'create') {
      await api.createScene(payload);
    } else if (form.mode === 'edit') {
      await api.updateScene(form.scene.scene_id, payload);
    } else {
      await api.resolveScenePendingTag(form.pending.pending_tag_id, { action: 'create', new_scene: payload });
    }
    setForm(null);
    onChanged();
  };

  const formTitle = form?.mode === 'edit' ? '编辑场景' : form?.mode === 'from_pending' ? '用这个标签新建场景' : '新建场景';

  return (
    <div
      style={{ padding: '24px 32px 40px', overflow: 'auto', flex: 1, display: 'flex', flexDirection: 'column', gap: '20px', maxWidth: '1040px', width: '100%', margin: '0 auto' }}
      data-testid="scene-catalog-manager"
    >
      {error && <ErrorNotice message={error} onClose={() => setError(null)} />}

      {pending.length > 0 && (
        <section className="zx-card" style={{ padding: '16px 20px' }} data-testid="pending-tags">
          <div style={{ marginBottom: '8px' }}>
            <div style={{ fontSize: 'var(--font-size-base)', fontWeight: 600 }}>待处理标签 · {pending.length}</div>
            <div style={sectionHint}>这些标签还不属于任何场景，带有它们的知识暂时进不了场景。可以归入已有场景、新建场景，或忽略。</div>
          </div>
          {pending.map((p) => (
            <div key={p.pending_tag_id} data-testid="pending-tag-row" style={{ display: 'flex', gap: '16px', padding: '12px 0', borderTop: '1px solid var(--border-color)', alignItems: 'center' }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 'var(--font-size-base)', fontWeight: 600 }}>{p.tag}</div>
                <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)', marginTop: '4px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  来自 {p.source_atoms.length} 条知识：{p.source_atoms.slice(0, 3).map((s) => s.title || '未命名知识').join('、')}
                  {p.source_atoms.length > 3 ? ' 等' : ''}
                </div>
              </div>
              <div style={{ display: 'flex', gap: '6px', alignItems: 'center', flexShrink: 0 }}>
                <select
                  aria-label={`把 ${p.tag} 归入场景`}
                  value={mapTarget[p.pending_tag_id] || ''}
                  onChange={(e) => setMapTarget((prev) => ({ ...prev, [p.pending_tag_id]: e.target.value }))}
                  style={{ height: 'var(--control-sm)', fontSize: 'var(--font-size-sm)', padding: '0 6px', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border-strong)', maxWidth: '180px' }}
                >
                  <option value="">选择场景…</option>
                  {activeScenes.map((s) => (
                    <option key={s.scene_id} value={s.scene_id}>{s.name}</option>
                  ))}
                </select>
                <button
                  type="button"
                  className="btn-secondary btn-sm"
                  disabled={busyId !== null || !mapTarget[p.pending_tag_id]}
                  onClick={() => run(p.pending_tag_id, () => api.resolveScenePendingTag(p.pending_tag_id, { action: 'map', scene_id: mapTarget[p.pending_tag_id] }))}
                >
                  {busyId === p.pending_tag_id && <Loader2 size={13} className="spin-slow" />}归入
                </button>
                <ActionMenu
                  label={`${p.tag} 的更多操作`}
                  align="right"
                  disabled={busyId !== null}
                  triggerClassName="btn-ghost btn-sm btn-icon"
                  triggerStyle={{ width: '28px' }}
                  trigger={<MoreHorizontal size={16} />}
                  items={[
                    { label: '用这个标签新建场景', onSelect: () => setForm({ mode: 'from_pending', pending: p, initial: { name: p.tag, aliases: [p.tag] } }) },
                    { label: '忽略这个标签', onSelect: () => run(p.pending_tag_id, () => api.resolveScenePendingTag(p.pending_tag_id, { action: 'ignore' })) },
                  ]}
                />
              </div>
            </div>
          ))}
        </section>
      )}

      <section className="zx-card" style={{ padding: '16px 20px' }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '12px', marginBottom: '8px' }}>
          <div>
            <div style={{ fontSize: 'var(--font-size-base)', fontWeight: 600 }}>全部场景 · {scenes.length}</div>
            <div style={sectionHint}>知识带有场景「包含的标签」中的任意一个，就会归到这个场景。</div>
          </div>
          <button type="button" className="btn-secondary btn-sm" onClick={() => setForm({ mode: 'create', initial: EMPTY_FORM })}>
            <Plus size={14} />新建场景
          </button>
        </div>
        {scenes.map((scene) => (
          <div
            key={scene.scene_id}
            data-testid="scene-row"
            style={{ display: 'flex', gap: '16px', padding: '14px 0', borderTop: '1px solid var(--border-color)', alignItems: 'flex-start', opacity: scene.status === 'active' ? 1 : 0.6 }}
          >
            <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: '6px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <span style={{ fontSize: 'var(--font-size-base)', fontWeight: 600 }}>{scene.name}</span>
                {scene.status !== 'active' && <span className="zx-tag neutral">已停用</span>}
              </div>
              {scene.description && <div style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-secondary)' }}>{scene.description}</div>}
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', alignItems: 'center' }}>
                <span style={metaLabel}>包含的标签</span>
                {scene.aliases.length ? (
                  scene.aliases.map((a) => <span key={a} className="zx-tag neutral">{a}</span>)
                ) : (
                  <span style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)' }}>无</span>
                )}
              </div>
            </div>
            <div style={{ display: 'flex', gap: '4px', flexShrink: 0, alignItems: 'center' }}>
              <button
                type="button"
                className="btn-ghost btn-sm"
                disabled={busyId !== null}
                onClick={() => setForm({ mode: 'edit', scene, initial: { ...scene } })}
              >
                {busyId === scene.scene_id && <Loader2 size={13} className="spin-slow" />}编辑
              </button>
              <ActionMenu
                label={`${scene.name} 的更多操作`}
                align="right"
                disabled={busyId !== null}
                triggerClassName="btn-ghost btn-sm btn-icon"
                triggerStyle={{ width: '28px' }}
                trigger={<MoreHorizontal size={16} />}
                items={[
                  {
                    label: scene.status === 'active' ? '停用（不再出现在选择场景中）' : '重新启用',
                    onSelect: () => run(scene.scene_id, () => api.setSceneStatus(scene.scene_id, scene.status === 'active' ? 'disabled' : 'active', scene.revision_token)),
                  },
                  { label: '删除场景', danger: true, onSelect: () => setDeleting(scene) },
                ]}
              />
            </div>
          </div>
        ))}
      </section>

      {!pendingLoading && pending.length === 0 && (
        <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)' }}>目前没有待处理的标签。</div>
      )}

      <SceneFormModal
        isOpen={form !== null}
        title={formTitle}
        initial={form?.initial || EMPTY_FORM}
        submitText={form?.mode === 'edit' ? '保存' : '创建场景'}
        onSubmit={submitForm}
        onCancel={() => setForm(null)}
      />

      <AppConfirmDialog
        isOpen={deleting !== null}
        title="删除场景"
        variant="danger"
        targetName={deleting?.name}
        description="删除后这个场景会从列表中移除，知识本身不受影响。已经生成过 Skill 的场景不能删除，只能停用。"
        confirmText="删除"
        loading={busyId === deleting?.scene_id}
        onCancel={() => setDeleting(null)}
        onConfirm={async () => {
          if (!deleting) return;
          const target = deleting;
          await run(target.scene_id, () => api.deleteScene(target.scene_id));
          setDeleting(null);
        }}
      />
    </div>
  );
};
