import React, { useState } from 'react';
import { Loader2 } from 'lucide-react';
import { SkillEvaluation } from '../../../types';
import { ErrorNotice } from './sceneShared';
import { ModalShell, UNSUPPORTED_RESOLUTION_TEXT, inputStyle, readableValue, smallLabel, textAreaStyle } from './skillReviewShared';

const Footer: React.FC<{ busy: boolean; confirmText: string; disabled?: boolean; danger?: boolean; onCancel: () => void; onConfirm: () => void; testId?: string }> = ({
  busy, confirmText, disabled, danger, onCancel, onConfirm, testId,
}) => (
  <>
    <button type="button" className="btn-secondary" onClick={onCancel} disabled={busy}>取消</button>
    <button type="button" className={danger ? 'btn-danger' : 'btn-primary'} onClick={onConfirm} disabled={busy || disabled} data-testid={testId}>
      {busy && <Loader2 size={14} className="spin-slow" />}{confirmText}
    </button>
  </>
);

export interface ChecklistEntry {
  key: string;
  label: string;
  /** 这条 Skill 对应内容的摘要，对照着确认 */
  summary: string;
}

/** 通过 / 修改后通过：逐项确认审核清单（FR12）；有修改时列出改动，可逐条填写修改理由（FR13，选填） */
export const ApproveDialog: React.FC<{
  evaluation: SkillEvaluation;
  checklist: ChecklistEntry[];
  initialChecked: Record<string, boolean>;
  initialReasons: Record<string, string>;
  busy: boolean;
  error: string | null;
  onCancel: () => void;
  onConfirm: (comment: string, reasons: Record<string, string>, checked: Record<string, boolean>) => void;
}> = ({ evaluation, checklist, initialChecked, initialReasons, busy, error, onCancel, onConfirm }) => {
  const [comment, setComment] = useState('');
  const [reasons, setReasons] = useState<Record<string, string>>(initialReasons);
  const [checked, setChecked] = useState<Record<string, boolean>>(initialChecked);
  const { summary, diffs } = evaluation;
  const res = Object.entries(summary.unsupported_resolutions || {});
  const done = checklist.filter((c) => checked[c.key]).length;
  const left = checklist.length - done;
  return (
    <ModalShell
      title={evaluation.approve_label}
      subtitle={evaluation.has_changes ? '会保存为新版本（专家修订版），AI 原稿保留不变' : '没有修改，当前版本即为通过版本，不产生新版本'}
      width={720}
      onClose={onCancel}
      busy={busy}
      testId="approve-dialog"
      footer={(
        <>
          <span style={{ ...smallLabel, fontSize: 'var(--font-size-sm)', marginRight: 'auto' }} data-testid="approve-check-count">已确认 {done} / {checklist.length}</span>
          <Footer busy={busy} confirmText={left ? `还差 ${left} 项确认` : `确认${evaluation.approve_label}`} disabled={left > 0}
            onCancel={onCancel} onConfirm={() => onConfirm(comment, reasons, checked)} testId="approve-confirm" />
        </>
      )}
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
        {error && <ErrorNotice message={error} />}
        <div>
          <div style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-secondary)', marginBottom: '8px' }}>逐项确认下面几点，每项下面是这条 Skill 的实际内容</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }} data-testid="approve-checklist">
            {checklist.map((c) => (
              <label key={c.key} style={{ display: 'flex', gap: '12px', padding: '10px 12px', borderRadius: 'var(--radius-md)', cursor: 'pointer',
                backgroundColor: checked[c.key] ? 'var(--success-bg)' : undefined }}>
                <input type="checkbox" checked={!!checked[c.key]} data-testid={`check-${c.key}`} style={{ marginTop: '3px', accentColor: 'var(--brand-600)' }}
                  onChange={(e) => setChecked({ ...checked, [c.key]: e.target.checked })} />
                <span style={{ display: 'flex', flexDirection: 'column', gap: '2px', minWidth: 0 }}>
                  <span style={{ fontSize: 'var(--font-size-base)', fontWeight: 600, color: 'var(--text-primary)' }}>{c.label}</span>
                  <span style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-secondary)', lineHeight: 1.6 }}>{c.summary}</span>
                </span>
              </label>
            ))}
          </div>
        </div>
        {evaluation.has_changes && (
          <>
            <div style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-secondary)' }} data-testid="approve-summary">
              共修改 {summary.changed_field_count} 处；新增步骤 {summary.steps_added} 个，删除步骤 {summary.steps_deleted} 个
              {summary.steps_reordered ? '，调整了步骤顺序' : ''}
              {res.length > 0 && `；无依据项：${res.map(([k, v]) => `${UNSUPPORTED_RESOLUTION_TEXT[k] || k} ${v} 项`).join('、')}`}
            </div>
            <div style={{ maxHeight: '320px', overflow: 'auto', border: '1px solid var(--border-color)', borderRadius: 'var(--radius-md)' }}>
              {diffs.map((d) => (
                <div key={d.path + d.op} style={{ padding: '8px 12px', borderBottom: '1px solid var(--bg-sunken)' }} data-testid="approve-diff">
                  <div style={{ display: 'flex', gap: '6px', alignItems: 'center' }}>
                    <span className="zx-tag neutral">{d.op_label}</span>
                    <span style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-primary)' }}>{d.label}</span>
                  </div>
                  {d.op !== 'reorder' && (
                    <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-secondary)', marginTop: '4px', lineHeight: 1.6 }}>
                      {d.op !== 'add' && <div>原来：{readableValue(d.before)}</div>}
                      {d.op !== 'delete' && <div>现在：{readableValue(d.after)}</div>}
                    </div>
                  )}
                  <input style={{ ...inputStyle, height: '28px', marginTop: '6px', fontSize: 'var(--font-size-xs)' }}
                    placeholder="修改理由（选填）" value={reasons[d.path] || ''}
                    onChange={(e) => setReasons({ ...reasons, [d.path]: e.target.value })} />
                </div>
              ))}
            </div>
          </>
        )}
        <div>
          <div style={{ ...smallLabel, marginBottom: '4px' }}>审核意见（选填）</div>
          <textarea style={textAreaStyle} value={comment} onChange={(e) => setComment(e.target.value)} maxLength={1000} data-testid="approve-comment" />
        </div>
      </div>
    </ModalShell>
  );
};

/** 退回重生成（待审核）/ 重新生成（校验未通过） */
export const RegenerateDialog: React.FC<{
  fromFailed: boolean;
  groups: { key: string; label: string }[];
  regenerateCount: number;
  hasEdits: boolean;
  busy: boolean;
  error: string | null;
  onCancel: () => void;
  onConfirm: (comment: string, groups: string[]) => void;
}> = ({ fromFailed, groups, regenerateCount, hasEdits, busy, error, onCancel, onConfirm }) => {
  const [comment, setComment] = useState('');
  const [picked, setPicked] = useState<string[]>([]);
  const missing = !fromFailed && !comment.trim();
  return (
    <ModalShell
      title={fromFailed ? '重新生成' : '退回重生成'}
      subtitle={fromFailed ? '按上次没通过检查的问题重新生成一次' : `新内容会作为新版本回到待审核；已重生成 ${regenerateCount} 次`}
      width={560}
      onClose={onCancel}
      busy={busy}
      testId="regenerate-dialog"
      footer={<Footer busy={busy} confirmText={fromFailed ? '重新生成' : '退回重生成'} disabled={missing} onCancel={onCancel}
        onConfirm={() => onConfirm(comment.trim(), picked)} testId="regenerate-confirm" />}
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
        {error && <ErrorNotice message={error} />}
        <div>
          <div style={{ ...smallLabel, marginBottom: '4px' }}>{fromFailed ? '补充说明（选填）' : '退回意见（必填）：说明哪里不对、希望怎么改'}</div>
          <textarea style={{ ...textAreaStyle, minHeight: '96px' }} value={comment} onChange={(e) => setComment(e.target.value)} maxLength={1000}
            placeholder="例如：不适用范围太笼统，请写明不处理的收费项目" data-testid="regenerate-comment" />
        </div>
        {!fromFailed && (
          <div>
            <div style={{ ...smallLabel, marginBottom: '6px' }}>需要重写的部分（选填，不选表示由系统判断）</div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '12px' }}>
              {groups.map((g) => (
                <label key={g.key} style={{ display: 'inline-flex', alignItems: 'center', gap: '4px', fontSize: 'var(--font-size-sm)' }}>
                  <input type="checkbox" checked={picked.includes(g.key)} data-testid={`regenerate-group-${g.key}`}
                    onChange={(e) => setPicked(e.target.checked ? [...picked, g.key] : picked.filter((k) => k !== g.key))} />
                  {g.key} {g.label}
                </label>
              ))}
            </div>
          </div>
        )}
        {hasEdits && (
          <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--warning-text)' }}>当前还没提交的修改会被放弃。</div>
        )}
      </div>
    </ModalShell>
  );
};

/** 驳回：原因与说明必填（FR12） */
export const RejectDialog: React.FC<{
  reasons: string[];
  busy: boolean;
  error: string | null;
  onCancel: () => void;
  onConfirm: (reason: string, note: string) => void;
}> = ({ reasons, busy, error, onCancel, onConfirm }) => {
  const [reason, setReason] = useState('');
  const [note, setNote] = useState('');
  return (
    <ModalShell
      title="驳回"
      subtitle="驳回的候选会保留，之后可以恢复"
      width={520}
      onClose={onCancel}
      busy={busy}
      testId="reject-dialog"
      footer={<Footer busy={busy} danger confirmText="确认驳回" disabled={!reason || !note.trim()} onCancel={onCancel}
        onConfirm={() => onConfirm(reason, note.trim())} testId="reject-confirm" />}
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
        {error && <ErrorNotice message={error} />}
        <div>
          <div style={{ ...smallLabel, marginBottom: '6px' }}>驳回原因（必选）</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
            {reasons.map((r) => (
              <label key={r} style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', fontSize: 'var(--font-size-sm)' }}>
                <input type="radio" name="reject-reason" checked={reason === r} onChange={() => setReason(r)} data-testid="reject-reason" value={r} />{r}
              </label>
            ))}
          </div>
        </div>
        <div>
          <div style={{ ...smallLabel, marginBottom: '4px' }}>说明（必填）</div>
          <textarea style={textAreaStyle} value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} data-testid="reject-note" />
        </div>
      </div>
    </ModalShell>
  );
};

/** M02-E 提交复核（FR14）：列出每条受影响知识的处理方式与内容改动，意见选填 */
export const RecheckDialog: React.FC<{
  evaluation: SkillEvaluation;
  busy: boolean;
  error: string | null;
  onCancel: () => void;
  onConfirm: (comment: string) => void;
}> = ({ evaluation, busy, error, onCancel, onConfirm }) => {
  const [comment, setComment] = useState('');
  const { summary, diffs, atom_changes: changes } = evaluation;
  return (
    <ModalShell
      title="提交复核"
      subtitle={evaluation.has_changes ? '引用或内容有变化，会保存为新版本（复核处理版），之前的版本保留不变' : '引用和内容都没有变化，不产生新版本，只记录复核结论'}
      width={680}
      onClose={onCancel}
      busy={busy}
      testId="recheck-dialog"
      footer={<Footer busy={busy} confirmText="确认提交复核" onCancel={onCancel} onConfirm={() => onConfirm(comment)} testId="recheck-confirm" />}
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
        {error && <ErrorNotice message={error} />}
        <div style={{ border: '1px solid var(--border-color)', borderRadius: 'var(--radius-md)' }}>
          {changes.map((c) => (
            <div key={c.atom_version_id} style={{ padding: '8px 12px', borderBottom: '1px solid var(--bg-sunken)', fontSize: 'var(--font-size-sm)' }} data-testid="recheck-summary-item">
              <span className="zx-tag neutral" style={{ marginRight: '6px' }}>{c.resolution_label || '未处理'}</span>
              「{c.title}」{c.trigger_labels.join('、')}
              {c.note && <div style={{ ...smallLabel, marginTop: '2px' }}>说明：{c.note}</div>}
            </div>
          ))}
        </div>
        {evaluation.has_changes && (
          <div style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-secondary)' }}>
            共改动 {summary.changed_field_count} 处（含引用改指新版本或移除）：{diffs.slice(0, 6).map((d) => d.label).join('、')}{diffs.length > 6 ? ' 等' : ''}
          </div>
        )}
        <div>
          <div style={{ ...smallLabel, marginBottom: '4px' }}>复核意见（选填）</div>
          <textarea style={textAreaStyle} value={comment} onChange={(e) => setComment(e.target.value)} maxLength={1000} data-testid="recheck-comment" />
        </div>
      </div>
    </ModalShell>
  );
};
