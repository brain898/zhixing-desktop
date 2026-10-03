import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Layers, Loader2, RotateCcw, Sparkles } from 'lucide-react';
import { api } from '../../../services/api';
import { SceneCard, SceneCardsResponse, SceneRecallResponse } from '../../../types';
import { EmptyState } from '../../ui/EmptyState';
import { CATEGORY_SHORT, ErrorNotice, errorMessage, fieldLabelStyle, formatCoverage } from './sceneShared';
import { ModalShell } from './skillReviewShared';
import { usePolling } from './usePolling';

interface SceneEntryPaneProps {
  onManageCatalog: () => void;
  onOpenBatch: (batchId: string) => void;
}

/** 卡片底部的一行提示：不能生成的原因、正在生成、上次生成结果或操作说明 */
const generateHint = (card: SceneCard, data: SceneCardsResponse | null): { text: string; tone: 'muted' | 'warning' } => {
  if (card.statistics_pending) return { text: '可用知识统计中…', tone: 'muted' };
  if (!card.can_generate) return { text: card.generate_blocked_reason || '', tone: 'warning' };
  if (!data?.generation_available) return { text: data?.generation_unavailable_reason || '暂时不能生成', tone: 'warning' };
  if (card.running_batch_id) return { text: '正在生成，可以随时查看进度。', tone: 'muted' };
  if (card.latest_batch) {
    const b = card.latest_batch;
    return { text: `上次生成：${b.status_label}，${b.stored_count} 个候选进入待审核。`, tone: 'muted' };
  }
  return { text: '系统会先拆分出几个任务，再逐个生成 Skill 候选，完成后进入待审核。', tone: 'muted' };
};

const skillSummary = (card: SceneCard): string => {
  const c = card.skill_counts;
  if (!c.total) return '暂无';
  return `待审核 ${c.pending_review} · 已通过 ${c.approved} · 待复核 ${c.needs_recheck}`;
};

/** FR03 场景入口：每个场景一张卡片；可用知识数以召回结果为准；生成按钮发起 M02-C 生成批次 */
export const SceneEntryPane: React.FC<SceneEntryPaneProps> = ({ onManageCatalog, onOpenBatch }) => {
  const [data, setData] = useState<SceneCardsResponse | null>(() => api.getCachedSceneCards());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [recallScene, setRecallScene] = useState<SceneCard | null>(null);
  const [generateScene, setGenerateScene] = useState<SceneCard | null>(null);
  const requestController = useRef<AbortController | null>(null);

  const load = useCallback(async (forceRefresh = false) => {
    requestController.current?.abort();
    const controller = new AbortController();
    requestController.current = controller;
    setLoading(true);
    setError(null);
    try {
      const value = await api.getSceneCards(forceRefresh, controller.signal);
      if (!controller.signal.aborted) setData(value);
    } catch (err) {
      if (!controller.signal.aborted) setError(errorMessage(err, '场景加载失败，请稍后再试'));
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    return () => requestController.current?.abort();
  }, [load]);
  const statisticsPending = !!data?.cards.some((card) => card.statistics_pending);
  usePolling(statisticsPending, load, 750);

  if (loading && !data) {
    return (
      <div style={{ padding: '32px', display: 'flex', alignItems: 'center', gap: '8px', color: 'var(--text-muted)', fontSize: 'var(--font-size-sm)' }}>
        <Loader2 size={14} className="spin-slow" /> 正在加载场景…
      </div>
    );
  }

  if (error && (!data || !data.cards.length)) {
    return (
      <div style={{ padding: '24px', display: 'flex', flexDirection: 'column', gap: '12px', maxWidth: '560px' }}>
        <ErrorNotice message={error} />
        <div>
          <button type="button" className="btn-secondary" onClick={() => load(true)}><RotateCcw size={14} />重试</button>
        </div>
      </div>
    );
  }

  const cards = data?.cards || [];
  if (!cards.length) {
    return (
      <EmptyState icon={<Layers size={24} />} title="没有可用的场景" description="场景都已停用，可以在「管理场景」里重新启用或新建。">
        <button type="button" className="btn-primary" onClick={onManageCatalog}>去管理场景</button>
      </EmptyState>
    );
  }

  return (
    <div style={{ padding: '24px 32px 40px', overflow: 'auto', flex: 1 }}>
      {error && <ErrorNotice message={error} />}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '16px' }}>
        <div style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-secondary)' }}>
          选择一个业务场景，系统会用这个场景下的知识生成 Skill。
        </div>
        <button type="button" className="btn-ghost btn-sm" onClick={() => load(true)} disabled={loading || statisticsPending} title="重新统计可用知识">
          {loading || statisticsPending ? <Loader2 size={13} className="spin-slow" /> : <RotateCcw size={13} />}{statisticsPending ? '统计中…' : '刷新'}
        </button>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: '16px' }}>
        {cards.map((card) => (
          <div
            key={card.scene_id}
            className="zx-card"
            data-testid="scene-card"
            data-scene-name={card.name}
            data-statistics-pending={!!card.statistics_pending}
            style={{ padding: '18px 20px', display: 'flex', flexDirection: 'column', gap: '14px' }}
          >
            <div>
              <div style={{ fontSize: 'var(--font-size-md)', fontWeight: 600, color: 'var(--text-primary)' }}>{card.name}</div>
              <div
                title={card.description || undefined}
                style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-muted)', marginTop: '4px', minHeight: '20px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}
              >
                {card.description}
              </div>
            </div>

            <div style={{ display: 'flex', alignItems: 'baseline', gap: '6px' }}>
              <span
                data-testid="scene-available-count"
                style={{ fontSize: '28px', fontWeight: 600, lineHeight: 1, color: card.can_generate ? 'var(--text-primary)' : 'var(--warning-text)' }}
              >
                {card.statistics_pending && !card.available_atom_count ? '…' : card.available_atom_count}
              </span>
              <span style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-secondary)' }}>条可用知识</span>
              {card.statistics_pending && <span style={fieldLabelStyle}>统计中</span>}
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr', columnGap: '12px', rowGap: '6px', fontSize: 'var(--font-size-sm)' }}>
              <span style={{ color: 'var(--text-muted)' }}>知识类型</span>
              <span data-testid="scene-coverage">{formatCoverage(card.category_coverage)}</span>
              <span style={{ color: 'var(--text-muted)' }}>已有 Skill</span>
              <span data-testid="scene-skills">{skillSummary(card)}</span>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginTop: 'auto', paddingTop: '12px', borderTop: '1px solid var(--border-color)' }}>
              <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                <button type="button" className="btn-secondary btn-sm" onClick={() => setRecallScene(card)} disabled={!!card.statistics_pending}>查看知识</button>
                {card.running_batch_id ? (
                  <button
                    type="button"
                    className="btn-primary btn-sm"
                    data-testid="scene-view-running"
                    onClick={() => onOpenBatch(card.running_batch_id as string)}
                  >
                    <Loader2 size={13} className="spin-slow" />查看进度
                  </button>
                ) : (
                  <button
                    type="button"
                    className="btn-primary btn-sm"
                    data-testid="scene-generate"
                    disabled={loading || !!card.statistics_pending || !!error || !card.can_generate || !data?.generation_available}
                    title={generateHint(card, data).text || undefined}
                    onClick={() => setGenerateScene(card)}
                  >
                    <Sparkles size={13} />生成 Skill
                  </button>
                )}
                {!card.running_batch_id && card.latest_batch && (
                  <button
                    type="button"
                    className="btn-ghost btn-sm"
                    data-testid="scene-latest-batch"
                    onClick={() => card.latest_batch && onOpenBatch(card.latest_batch.batch_id)}
                  >
                    上次记录
                  </button>
                )}
              </div>
              <div
                data-testid="scene-generate-hint"
                style={{ fontSize: 'var(--font-size-xs)', color: generateHint(card, data).tone === 'warning' ? 'var(--warning-text)' : 'var(--text-muted)', lineHeight: 1.6, minHeight: '38px' }}
              >
                {generateHint(card, data).text}
              </div>
            </div>
          </div>
        ))}
      </div>
      {recallScene && <RecallPoolModal scene={recallScene} onClose={() => setRecallScene(null)} />}
      {generateScene && (
        <GenerateDialog
          scene={generateScene}
          onCancel={() => setGenerateScene(null)}
          onStarted={(batchId) => {
            setGenerateScene(null);
            onOpenBatch(batchId);
          }}
          onConflict={() => {
            setGenerateScene(null);
            load();
          }}
        />
      )}
    </div>
  );
};

/** G1 发起：可选填写一句生成侧重说明 */
const GenerateDialog: React.FC<{
  scene: SceneCard;
  onCancel: () => void;
  onStarted: (batchId: string) => void;
  onConflict: () => void;
}> = ({ scene, onCancel, onStarted, onConflict }) => {
  const [focus, setFocus] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await api.startSkillBatch(scene.scene_id, focus.trim() || undefined);
      onStarted(res.batch_id);
    } catch (err) {
      setError(errorMessage(err, '没有发起成功，请稍后再试'));
      setBusy(false);
    }
  };

  return (
    <ModalShell title={`为「${scene.name}」生成 Skill`} label="生成 Skill" width={500} testId="generate-dialog"
      onClose={onCancel} busy={busy} bodyStyle={{ padding: '18px 24px', display: 'flex', flexDirection: 'column', gap: '14px' }}
      footer={<>
        <button type="button" className="btn-secondary" onClick={onCancel} disabled={busy}>取消</button>
        <button type="button" className="btn-primary" data-testid="generate-confirm" onClick={submit} disabled={busy}>
          {busy ? <Loader2 size={14} className="spin-slow" /> : <Sparkles size={14} />}
          {busy ? '正在发起…' : '开始生成'}
        </button>
      </>}>
      <div style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-secondary)', lineHeight: 1.7 }}>
        系统会用这个场景的 {scene.available_atom_count} 条可用知识，拆分出最多 5 个任务，再逐个生成 Skill 候选并自动检查。整个过程在后台进行，通常需要几分钟。
      </div>
      <div>
        <label style={fieldLabelStyle} htmlFor="generate-focus">这次想侧重什么（选填，一句话）</label>
        <input
          id="generate-focus"
          className="zx-input"
          data-testid="generate-focus"
          value={focus}
          maxLength={100}
          onChange={(e) => setFocus(e.target.value)}
          placeholder="如：侧重交付查验"
        />
      </div>
      {error && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
          <ErrorNotice message={error} />
          {error.includes('正在生成') && (
            <div>
              <button type="button" className="btn-secondary btn-sm" onClick={onConflict}>回到场景列表</button>
            </div>
          )}
        </div>
      )}
    </ModalShell>
  );
};

const RecallPoolModal: React.FC<{ scene: SceneCard; onClose: () => void }> = ({ scene, onClose }) => {
  const [data, setData] = useState<SceneRecallResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .getSceneRecall(scene.scene_id)
      .then((res) => alive && setData(res))
      .catch((err) => alive && setError(errorMessage(err, '知识加载失败，请稍后再试')));
    return () => {
      alive = false;
    };
  }, [scene.scene_id]);

  const byTag = data ? data.atoms.filter((a) => a.recall_source === 'tag').length : 0;
  const byContent = data ? data.atoms.length - byTag : 0;

  return (
    <ModalShell title={`${scene.name} · 可用知识`} label="场景可用知识" width={720} height={620} onClose={onClose}
      bodyStyle={{ padding: '4px 24px 20px' }}
      subtitle={data && (
        <div data-testid="recall-summary" style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)', marginTop: '2px' }}>
          {byContent === 0
            ? `共 ${data.atoms.length} 条，都带有这个场景的标签。`
            : `共 ${data.atoms.length} 条：${byTag} 条带有这个场景的标签，另外 ${byContent} 条内容相关，由系统补充找到（标有「内容相关」）。`}
        </div>
      )}>
      {error && <ErrorNotice message={error} />}
      {!data && !error && (
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: 'var(--text-muted)', fontSize: 'var(--font-size-sm)', padding: '16px 0' }}>
          <Loader2 size={14} className="spin-slow" />正在查找…
        </div>
      )}
      {data && data.semantic_error && (
        <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--warning-text)', padding: '10px 0 0' }}>暂时只能按标签查找，内容相关的知识没有列出。</div>
      )}
      {data && data.atoms.length === 0 && (
        <div style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-muted)', padding: '16px 0' }}>这个场景下还没有可用的知识。</div>
      )}
      {data?.atoms.map((atom) => (
        <div key={atom.version_id} data-testid="recall-atom" style={{ padding: '12px 0', borderBottom: '1px solid var(--border-color)', display: 'flex', flexDirection: 'column', gap: '4px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <span style={{ fontSize: 'var(--font-size-sm)', fontWeight: 600, color: 'var(--text-primary)', flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {atom.title}
            </span>
            {atom.primary_category && <span className="zx-tag neutral">{CATEGORY_SHORT[atom.primary_category] || atom.primary_category}</span>}
            {atom.recall_source === 'semantic' && (
              <span className="zx-tag warning" title="这条知识没有这个场景的标签，是按内容相关补充找到的">内容相关</span>
            )}
          </div>
          {atom.statement && (
            <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-secondary)', lineHeight: 1.6 }}>{atom.statement}</div>
          )}
        </div>
      ))}
    </ModalShell>
  );
};
