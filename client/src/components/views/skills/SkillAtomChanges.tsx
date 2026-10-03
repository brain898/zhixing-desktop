import React, { useEffect, useState } from 'react';
import { AlertTriangle, Check } from 'lucide-react';
import { SkillAtomChange, SkillAtomFieldDiff, StaleResolutionAction } from '../../../types';
import { describePathText, formatTime, inputStyle, readableValue, smallLabel } from './skillReviewShared';

/**
 * M02-E 知识变更（PRD FR14 复核视图）：受影响的依据知识、旧版本与新版本差异（M01 知识版本记录）、
 * 引用它的步骤与字段；逐条选择「更新引用 / 确认无影响（须填说明）/ 移除引用」。
 */
export const AtomChangesCard: React.FC<{
  changes: SkillAtomChange[];
  editable: boolean;
  resolutions: Record<string, { action: StaleResolutionAction; note: string }>;
  selectedAtom: string | null;
  onSelectAtom: (vid: string) => void;
  onGoPath: (path: string) => void;
  onResolve: (vid: string, action: StaleResolutionAction, note: string) => void;
}> = ({ changes, editable, resolutions, selectedAtom, onSelectAtom, onGoPath, onResolve }) => {
  if (changes.length === 0) return null;
  const pending = changes.filter((c) => !c.handled).length;
  return (
    <div className="zx-card" style={{ padding: 0, marginBottom: '16px', borderLeft: `3px solid ${pending ? 'var(--danger-text)' : 'var(--success-text)'}`, overflow: 'hidden' }}
      id="section-atom-changes" data-testid="atom-changes-card" data-pending={pending}>
      <div style={{ padding: '12px 16px 8px' }}>
        <div style={{ fontSize: 'var(--font-size-section)', fontWeight: 600 }}>
          {pending ? `依据知识有 ${changes.length} 条发生了变化，还有 ${pending} 条没有处理` : `依据知识的 ${changes.length} 条变化都已处理`}
        </div>
        <div style={smallLabel}>
          下面对照知识的旧版本与新版本，并标出这条 Skill 中用到它的步骤和字段。停用、删除或排除的知识不能继续引用。
          系统不会自动改写 Skill 内容。
        </div>
      </div>
      {changes.map((c) => (
        <ChangeItem key={c.atom_version_id} change={c} editable={editable} resolution={resolutions[c.atom_version_id]}
          selected={selectedAtom === c.atom_version_id} onSelect={() => onSelectAtom(c.atom_version_id)}
          onGoPath={onGoPath} onResolve={onResolve} />
      ))}
    </div>
  );
};

const ChangeItem: React.FC<{
  change: SkillAtomChange;
  editable: boolean;
  resolution?: { action: StaleResolutionAction; note: string };
  selected: boolean;
  onSelect: () => void;
  onGoPath: (path: string) => void;
  onResolve: (vid: string, action: StaleResolutionAction, note: string) => void;
}> = ({ change: c, editable, resolution, selected, onSelect, onGoPath, onResolve }) => {
  const [note, setNote] = useState(resolution?.note ?? c.note ?? '');
  useEffect(() => setNote(resolution?.note ?? c.note ?? ''), [resolution?.note, c.note]);
  const chosen = resolution?.action || c.resolution;
  const hasNew = !!c.new_version;
  return (
    <div data-testid="atom-change-item" data-atom={c.atom_version_id} data-handled={c.handled ? 'true' : 'false'}
      onClick={onSelect}
      style={{ padding: '12px 16px', borderTop: '1px solid var(--bg-sunken)', backgroundColor: selected ? 'var(--brand-50)' : undefined, cursor: 'pointer' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
        {c.handled
          ? <span className="zx-tag"><Check size={12} style={{ verticalAlign: '-2px' }} />已处理：{c.resolution_label}</span>
          : <span className="zx-tag danger"><AlertTriangle size={12} style={{ verticalAlign: '-2px' }} />待处理</span>}
        <span style={{ fontSize: 'var(--font-size-sm)', fontWeight: 600 }}>{c.title}</span>
        {c.trigger_labels.map((t) => <span key={t} className="zx-tag warning" data-testid="atom-change-trigger">{t}</span>)}
        {c.events[0] && <span style={smallLabel}>{formatTime(c.events[0].created_at)}</span>}
      </div>

      {/* 引用它的步骤与字段 */}
      {c.affected_paths.length > 0 && (
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap', marginTop: '8px' }} data-testid="atom-change-affected">
          <span style={smallLabel}>用到它的地方：</span>
          {c.affected_paths.map((p) => (
            <button key={p} type="button" className="zx-tag warning" style={{ cursor: 'pointer', border: 'none' }}
              onClick={(e) => { e.stopPropagation(); onSelect(); onGoPath(p); }}>
              {describePathText(p)}
            </button>
          ))}
        </div>
      )}

      {/* 旧版本与新版本差异 */}
      {hasNew ? (
        <div style={{ marginTop: '10px' }} data-testid="atom-version-diff">
          <div style={{ ...smallLabel, marginBottom: '4px' }}>
            知识内容对照：引用的旧版本（第 {c.old_version?.version_number ?? '?'} 版） → 当前生效的新版本（第 {c.new_version?.version_number ?? '?'} 版）
          </div>
          {c.diff.length === 0 ? (
            <div style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-secondary)' }}>新版本在标题、陈述、条件、动作、例外、口径与有效期上没有变化。</div>
          ) : (
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 'var(--font-size-sm)' }}>
              <thead>
                <tr style={{ color: 'var(--text-muted)', textAlign: 'left' }}>
                  <th style={th}>字段</th><th style={th}>旧版本</th><th style={th}>新版本</th>
                </tr>
              </thead>
              <tbody>{c.diff.map((d) => <DiffRow key={d.field} diff={d} />)}</tbody>
            </table>
          )}
        </div>
      ) : c.old_version && (
        <div style={{ marginTop: '10px', fontSize: 'var(--font-size-sm)', color: 'var(--text-secondary)', lineHeight: 1.6 }} data-testid="atom-old-snapshot">
          <div style={smallLabel}>{c.exists ? '引用时的知识内容' : '这条知识已删除，以下是引用时保存的内容'}</div>
          <div>{c.old_version.statement}</div>
        </div>
      )}

      {/* 处理 */}
      {editable && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', marginTop: '10px' }} onClick={(e) => e.stopPropagation()}>
          <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', alignItems: 'center' }}>
            <span style={smallLabel}>处理方式：</span>
            {c.options.includes('update') && (
              <button type="button" className={chosen === 'update' ? 'btn-primary btn-sm' : 'btn-secondary btn-sm'}
                onClick={() => onResolve(c.atom_version_id, 'update', note)} data-testid="stale-update">更新引用（改用新版本）</button>
            )}
            {c.options.includes('no_impact') && (
              <button type="button" className={chosen === 'no_impact' ? 'btn-primary btn-sm' : 'btn-secondary btn-sm'}
                onClick={() => onResolve(c.atom_version_id, 'no_impact', note)} data-testid="stale-no-impact">确认无影响</button>
            )}
            <button type="button" className={chosen === 'remove' ? 'btn-danger btn-sm' : 'btn-danger-ghost btn-sm'}
              onClick={() => onResolve(c.atom_version_id, 'remove', note)} data-testid="stale-remove">移除引用</button>
            {c.remove_only && <span style={{ ...smallLabel, color: 'var(--danger-text)' }}>这条知识已不能继续引用，只能移除，或者退回重生成、驳回</span>}
          </div>
          {c.options.includes('no_impact') && (
            <input style={{ ...inputStyle, height: '30px', fontSize: 'var(--font-size-sm)' }} data-testid="stale-note"
              placeholder="确认无影响时必须写明理由，例如：新版本只补充了登记要求，不影响本 Skill 的判断"
              value={note} maxLength={500}
              onChange={(e) => { setNote(e.target.value); if (chosen === 'no_impact') onResolve(c.atom_version_id, 'no_impact', e.target.value); }} />
          )}
          {chosen === 'remove' && <span style={smallLabel}>已移除引用；只依据这条知识的步骤变成「无依据」，请在上方问题中删除、补依据或标为专家补充。撤销请点「放弃修改」。</span>}
          {!c.handled && c.problem && <span style={{ fontSize: 'var(--font-size-xs)', color: 'var(--danger-text)' }} data-testid="stale-problem">{c.problem}</span>}
        </div>
      )}
    </div>
  );
};

const th: React.CSSProperties = { padding: '4px 8px', borderBottom: '1px solid var(--border-color)', fontWeight: 500, fontSize: 'var(--font-size-xs)' };
const td: React.CSSProperties = { padding: '6px 8px', borderBottom: '1px solid var(--bg-sunken)', verticalAlign: 'top', lineHeight: 1.6 };

const DiffRow: React.FC<{ diff: SkillAtomFieldDiff }> = ({ diff: d }) => {
  if (d.list) {
    const removed = new Set(d.removed || []);
    const added = new Set(d.added || []);
    return (
      <tr data-testid="atom-diff-row" data-field={d.field}>
        <td style={{ ...td, width: '88px', color: 'var(--text-muted)' }}>{d.label}</td>
        <td style={td}>{(d.before as string[]).map((x) => <div key={x} style={removed.has(x) ? delStyle : undefined}>{x}</div>)}{(d.before as string[]).length === 0 && '—'}</td>
        <td style={td}>{(d.after as string[]).map((x) => <div key={x} style={added.has(x) ? addStyle : undefined}>{x}</div>)}{(d.after as string[]).length === 0 && '—'}</td>
      </tr>
    );
  }
  return (
    <tr data-testid="atom-diff-row" data-field={d.field}>
      <td style={{ ...td, width: '88px', color: 'var(--text-muted)' }}>{d.label}</td>
      <td style={td}><span style={delStyle}>{readableValue(d.before) || '—'}</span></td>
      <td style={td}><span style={addStyle}>{readableValue(d.after) || '—'}</span></td>
    </tr>
  );
};

const delStyle: React.CSSProperties = { backgroundColor: 'var(--danger-bg)', color: 'var(--danger-text)', textDecoration: 'line-through', borderRadius: '2px', padding: '0 2px' };
const addStyle: React.CSSProperties = { backgroundColor: 'var(--success-bg)', color: 'var(--success-text)', borderRadius: '2px', padding: '0 2px' };
