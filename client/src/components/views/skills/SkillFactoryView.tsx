import React, { useCallback, useEffect, useState } from 'react';
import { Layers, Loader2, RotateCcw, Sparkles } from 'lucide-react';
import { api } from '../../../services/api';
import { SceneCatalogResponse, SceneFormPayload } from '../../../types';
import { EmptyState } from '../../ui/EmptyState';
import { SegmentedTabs } from '../../ui/SegmentedTabs';
import { SceneEntryPane } from './SceneEntryPane';
import { SceneCatalogManager } from './SceneCatalogManager';
import { SceneMergeReview } from './SceneMergeReview';
import { BatchDetailPane } from './BatchDetailPane';
import { SkillCandidateListPane } from './SkillCandidateListPane';
import { SkillStatisticsPane } from './SkillStatisticsPane';
import { SkillReviewWorkbench } from './SkillReviewWorkbench';
import { ReviewErrorBoundary } from './skillReviewShared';
import { ErrorNotice, SceneFormModal, errorMessage } from './sceneShared';
import { usePolling } from './usePolling';

type FactoryTab = 'entry' | 'review' | 'catalog' | 'statistics';

const POLL_INTERVAL_MS = 1500;
const EMPTY_FORM: Partial<SceneFormPayload> = {};

/** M02 Skill 工厂（管理员）：场景入口与场景目录管理 */
export const SkillFactoryView: React.FC = () => {
  const [catalog, setCatalog] = useState<SceneCatalogResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<FactoryTab>('entry');
  const [initBusy, setInitBusy] = useState(false);
  const [initError, setInitError] = useState<string | null>(null);
  const [manualCreateOpen, setManualCreateOpen] = useState(false);
  // M02-C：打开的生成记录（批次详情）
  const [batchId, setBatchId] = useState<string | null>(null);
  // M02-D：打开的审核工作台；queue 为打开时列表的顺序，提交后按它跳到下一条待审核候选
  const [review, setReview] = useState<{ skillId: string; queue: string[]; flash: string | null } | null>(null);
  const [listKey, setListKey] = useState(0);

  const load = useCallback(async () => {
    setError(null);
    try {
      setCatalog(await api.getSceneCatalog());
    } catch (err) {
      setError(errorMessage(err, '场景加载失败，请稍后再试'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const reload = useCallback(() => {
    load();
  }, [load]);

  // 归并建议在后台生成：进行中时轮询，完成后刷新目录
  const suggestion = catalog?.latest_suggestion || null;
  const suggestionRunning = !!suggestion && (suggestion.status === 'queued' || suggestion.status === 'running');
  usePolling(suggestionRunning, load, POLL_INTERVAL_MS);

  const startInit = async () => {
    setInitBusy(true);
    setInitError(null);
    try {
      await api.requestSceneMergeSuggestion();
      await load();
    } catch (err) {
      setInitError(errorMessage(err, '暂时无法自动整理，请稍后再试'));
    } finally {
      setInitBusy(false);
    }
  };

  const hasCatalog = !!catalog?.has_catalog;
  const suggestionActive = !!suggestion && ['queued', 'running', 'completed', 'failed'].includes(suggestion.status);
  const showSuggestion = !hasCatalog && suggestionActive;
  // 目录建立后整理新标签：进行中或待确认时占用主区域
  const showIncremental = hasCatalog && suggestionActive && suggestion?.mode === 'incremental';
  const newTagCount = catalog?.unorganized_tag_count || 0;

  const renderBody = () => {
    if (loading) {
      return (
        <div style={{ padding: '32px', display: 'flex', alignItems: 'center', gap: '8px', color: 'var(--text-muted)', fontSize: 'var(--font-size-sm)' }}>
          <Loader2 size={14} className="spin-slow" />正在加载…
        </div>
      );
    }
    if (error) {
      return (
        <div style={{ padding: '24px', display: 'flex', flexDirection: 'column', gap: '12px', maxWidth: '560px' }}>
          <ErrorNotice message={error} />
          <div>
            <button type="button" className="btn-secondary" onClick={load}><RotateCcw size={14} />重试</button>
          </div>
        </div>
      );
    }
    if (tab === 'statistics' && !review && !batchId) return <SkillStatisticsPane />;
    if (!hasCatalog) {
      if (showSuggestion && suggestion) {
        return (
          <div style={{ flex: 1, overflow: 'auto' }}>
            <SceneMergeReview key={`${suggestion.suggestion_id}:${suggestion.status}`} suggestion={suggestion} onChanged={reload} />
          </div>
        );
      }
      // AC01：还没有场景时只显示一句引导文案和一个开始按钮（PRD 按钮名「初始化场景目录」，按用户要求改为大白话）
      return (
        <EmptyState
          icon={<Layers size={24} />}
          title="先把知识整理成业务场景"
          description="Skill 按业务场景生产。点击下方按钮，系统会把知识库里零散的场景标签整理成几个场景，你检查确认后就能使用。"
        >
          <button type="button" className="btn-primary" data-testid="init-scene-catalog" onClick={startInit} disabled={initBusy}>
            {initBusy && <Loader2 size={14} className="spin-slow" />}
            {initBusy ? '正在开始…' : '开始整理场景'}
          </button>
          {initError && (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '8px', maxWidth: '440px' }}>
              <ErrorNotice message={initError} />
              <button type="button" className="btn-secondary btn-sm" onClick={() => setManualCreateOpen(true)}>手动新建场景</button>
            </div>
          )}
        </EmptyState>
      );
    }
    if (showIncremental && suggestion) {
      return (
        <div style={{ flex: 1, overflow: 'auto' }}>
          <SceneMergeReview
            key={`${suggestion.suggestion_id}:${suggestion.status}`}
            suggestion={suggestion}
            existingScenes={(catalog?.scenes || []).filter((s) => s.status === 'active')}
            onChanged={reload}
          />
        </div>
      );
    }
    if (review) {
      const closeReview = () => {
        setReview(null);
      };
      return (
        <ReviewErrorBoundary key={review.skillId} onBack={closeReview}>
        <SkillReviewWorkbench
          skillId={review.skillId}
          queue={review.queue}
          flash={review.flash}
          onBack={closeReview}
          onNavigate={(nextId, message) => {
            if (nextId) {
              setReview({ skillId: nextId, queue: review.queue, flash: `${message}，已跳到下一条待审核候选` });
            } else {
              closeReview();
            }
          }}
        />
        </ReviewErrorBoundary>
      );
    }
    if (batchId) {
      return (
        <BatchDetailPane
          key={batchId}
          batchId={batchId}
          onBack={() => {
            setBatchId(null);
          }}
          onOpenSkill={(skillId, queue) => setReview({ skillId, queue, flash: null })}
        />
      );
    }
    const newTagsBar = newTagCount > 0 && (
      <div
        data-testid="new-tags-bar"
        style={{
          margin: '16px 32px 0',
          padding: '10px 14px',
          display: 'flex',
          alignItems: 'center',
          gap: '12px',
          borderRadius: 'var(--radius-md)',
          backgroundColor: 'var(--warning-bg)',
          border: '1px solid var(--warning-border)',
          fontSize: 'var(--font-size-sm)',
          color: 'var(--warning-text)',
        }}
      >
        <span style={{ flex: 1 }}>
          有 {newTagCount} 个新标签还没归入场景，带这些标签的知识暂时用不上。可以让系统自动整理，你确认后生效。
        </span>
        <button type="button" className="btn-primary btn-sm" data-testid="organize-new-tags" onClick={startInit} disabled={initBusy}>
          {initBusy ? <Loader2 size={13} className="spin-slow" /> : <Sparkles size={13} />}
          自动整理
        </button>
      </div>
    );
    return (
      <>
        {newTagsBar}
        {initError && (
          <div style={{ margin: '12px 32px 0' }}>
            <ErrorNotice message={initError} onClose={() => setInitError(null)} />
          </div>
        )}
        {tab === 'catalog' ? (
          <SceneCatalogManager scenes={catalog?.scenes || []} onChanged={reload} />
        ) : tab === 'review' ? (
          <ReviewErrorBoundary key={listKey} onBack={() => setListKey((k) => k + 1)}>
            <SkillCandidateListPane onOpenSkill={(skillId, queue) => setReview({ skillId, queue, flash: null })} />
          </ReviewErrorBoundary>
        ) : (
          <SceneEntryPane onManageCatalog={() => setTab('catalog')} onOpenBatch={setBatchId} />
        )}
      </>
    );
  };

  const currentLabel = tab === 'statistics' && !review && !batchId ? '统计' : !hasCatalog
    ? '整理业务场景'
    : review
      ? '审核'
      : batchId
      ? '生成记录'
      : showIncremental
      ? '整理新标签'
      : tab === 'catalog'
        ? '管理场景'
        : tab === 'review'
          ? '审核候选'
          : '选择场景';

  return (
    <div style={{ flex: 1, height: '100%', display: 'flex', flexDirection: 'column', backgroundColor: 'var(--bg-secondary)', minWidth: 0 }} data-testid="skill-factory-view">
      <div className="zx-topbar">
        <div className="zx-breadcrumb">
          <span>Skill 工厂</span>
          <span>/</span>
          <span className="current">{currentLabel}</span>
        </div>
        {!showIncremental && !batchId && !review && (
          <SegmentedTabs<FactoryTab>
            value={tab}
            onChange={setTab}
            options={[
              { key: 'entry', label: '选择场景', testId: 'tab-scene-entry' },
              { key: 'review', label: '审核候选', testId: 'tab-skill-review' },
              { key: 'catalog', label: '管理场景', count: catalog?.pending_tag_count || undefined, testId: 'tab-scene-catalog', title: '数字为待处理标签数' },
              { key: 'statistics', label: '统计', testId: 'tab-skill-statistics' },
            ]}
          />
        )}
      </div>
      <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'auto' }}>{renderBody()}</div>
      <SceneFormModal
        isOpen={manualCreateOpen}
        title="新建场景"
        initial={EMPTY_FORM}
        submitText="创建场景"
        onSubmit={async (payload) => {
          await api.createScene(payload);
          setManualCreateOpen(false);
          reload();
        }}
        onCancel={() => setManualCreateOpen(false)}
      />
    </div>
  );
};
