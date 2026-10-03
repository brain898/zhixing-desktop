import React from 'react';
import { ConsultCalculation, ConsultReport as Report, ConsultRun } from '../../../types';

export const valueText = (value: unknown): string => {
  if (value === undefined || value === null || value === '') return '暂无';
  if (typeof value === 'boolean') return value ? '是' : '否';
  if (Array.isArray(value)) return value.map(valueText).join('；');
  if (typeof value === 'object') return Object.values(value as Record<string, unknown>).map(valueText).join('；');
  return String(value);
};

const sourceLocation = (location: Record<string, unknown>): string => [
  location.page_number ? `第 ${location.page_number} 页` : '',
  location.heading_path ? valueText(location.heading_path) : '',
  location.paragraph_anchor ? `段落 ${location.paragraph_anchor}` : '',
  location.block_index !== undefined && location.block_index !== null ? `第 ${Number(location.block_index) + 1} 个原文片段` : '',
].filter(Boolean).join(' · ');

const CalculationTable: React.FC<{ rows: ConsultCalculation[] }> = ({ rows }) => rows.length ? <div className="zx-consult-table-wrap"><table>
  <thead><tr><th>项目与算式</th><th>模型结果</th><th>程序结果</th><th>核对情况</th></tr></thead>
  <tbody>{rows.map((row, i) => <tr key={i}><td>{row.label}<code>{row.expression}</code></td><td>{valueText(row.model_result)} {row.unit}</td><td>{valueText(row.program_result)} {row.unit}</td><td>{row.status || (row.consistent ? '一致' : '未经复算')}{row.note && <small>{row.note}</small>}</td></tr>)}</tbody>
</table></div> : <p className="zx-consult-muted">本次没有需要复算的算式。</p>;

export const ConsultReport: React.FC<{ report: Report; runs: ConsultRun[] }> = ({ report, runs }) => <article className="zx-consult-report" data-testid="consult-report">
  <h2>咨询报告</h2>
  {report.mode === 'fallback' && <p className="zx-consult-notice">未匹配到可用 Skill，以下为知识条目整理。</p>}
  <section><h3>1. 结论摘要</h3><p>{report.summary || '本次没有可形成确定结论的结果。'}</p></section>
  <section><h3>2. 各 Skill 结果</h3>
    {(report.skill_results || []).length ? (report.skill_results || []).map((raw, index) => {
      const result = raw as Record<string, any>;
      const output = result.output || result;
      const labels = Object.fromEntries((result.output_fields || []).map((f: any) => [f.key, f.label]));
      return <div className="zx-consult-result" key={index}>
        <h4>{index + 1}. {result.name || result.skill_name || runs[index]?.name || '咨询能力'} <span>{({failed: '执行失败', skipped: '未执行', uncomputable: '不可计算', needs_human: '需人工'} as Record<string, string>)[result.status] || result.status || output.status}</span></h4>
        <p>{output.summary}</p>
        <table><thead><tr><th>输出字段</th><th>结果</th></tr></thead><tbody>{Object.entries(output.outputs || result.outputs || {}).map(([key, value], i) => <tr key={key}><td>{labels[key] || `结果 ${i + 1}`}</td><td>{valueText(value)}{(result.unverified_outputs || []).includes(key) && '（未经复算）'}</td></tr>)}</tbody></table>
        {(output.step_results || result.step_results || []).map((step: any, i: number) => <div className="zx-consult-step" key={i}><strong>第 {i + 1} 步 · {step.state}</strong><p>{step.action || step.label}{step.action || step.label ? '：' : ''}{step.conclusion}</p><small>引用依据 {Array.isArray(step.refs) ? step.refs.length : 0} 条，详见下方依据清单。</small></div>)}
        {(result.validation || []).length > 0 && <p className="zx-consult-notice">{valueText(result.validation)}</p>}
      </div>;
    }) : <p className="zx-consult-muted">本次使用知识检索整理，没有执行 Skill。</p>}
  </section>
  <section><h3>3. 计算复算表</h3><p className="zx-consult-muted">复算只核对算术，代入数值的业务口径仍需人工确认。</p><CalculationTable rows={report.recompute || []} /></section>
  <section><h3>4. 行动建议</h3>{Array.isArray(report.actions) ? <ul>{report.actions.map((action, i) => <li key={i}>{valueText(action)}</li>)}</ul> : <p>{valueText(report.actions)}</p>}</section>
  <section><h3>5. 风险边界</h3>{report.risk_boundaries?.length ? report.risk_boundaries.map((group, i) => <div key={i}><h4>{group.name}</h4>{group.items.map((item, j) => <p key={j}>{item}</p>)}</div>) : <p className="zx-consult-muted">本次没有执行 Skill，请人工核实知识条目适用范围。</p>}</section>
  <section><h3>6. 需人工确认项</h3>{report.manual_items?.length ? <ul>{report.manual_items.map((item, i) => <li key={i}>{valueText(item)}</li>)}</ul> : <p>暂无额外确认项，报告结论仍需人工确认。</p>}</section>
  <section><h3>7. 依据清单</h3>{report.evidence?.length ? report.evidence.map((item, i) => <details className="zx-consult-evidence" data-testid="consult-evidence" key={item.atom_version_id || i}>
    <summary>{item.title || `依据 ${i + 1}`}</summary>
    <p>{item.statement}</p><p className="zx-consult-muted">来源：{item.file_name || item.source_file_name || '暂无文件名'} · {item.version_label || item.source_version || '暂无版本'}</p>
    {(item.evidence || item.excerpts || []).map((raw, j) => { const evidence = raw as any; return <blockquote key={j}>{typeof evidence === 'string' ? evidence : evidence.excerpt || evidence.text || valueText(evidence)}{evidence.source_locator && <small>{sourceLocation(evidence.source_locator)}</small>}</blockquote>; })}
  </details>) : <p className="zx-consult-muted">未找到可追溯的知识依据。</p>}</section>
  <section><h3>8. 使用提示</h3><p className="zx-consult-notice">{report.disclaimer || '本报告由 AI 按已审核 Skill 生成，结论需人工确认。'}</p></section>
</article>;
