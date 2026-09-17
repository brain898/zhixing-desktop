/**
 * 知行有策 - 中文自然呈现格式化工具
 * 专门去除界面中程序化英文、生硬代码标识与括号杂质
 */

/**
 * 格式化文档版本号为自然的中文表达，如 "v1" -> "第 1 版"
 */
export function formatVersionLabel(versionLabel?: string | null): string {
  if (!versionLabel) return '第 1 版';
  const clean = versionLabel.trim();
  if (/^第\s*\d+\s*版$/.test(clean)) return clean;
  const match = clean.match(/\d+/);
  if (match) {
    return `第 ${match[0]} 版`;
  }
  return clean;
}

/**
 * 格式化原文定位锚点为直观的中文描述，消除纯英文和程序化代码串
 * 例如:
 * "line_1" -> "第 1 行"
 * "page_1_p_2" -> "第 1 页第 2 段"
 * "page_1_tbl_1" -> "第 1 页表格 1"
 * "page_1" -> "第 1 页"
 * "p_3" -> "第 3 段"
 * "tbl_2" -> "表格 2"
 * "p.1" -> "第 1 页"
 */
export function formatAnchor(anchor?: string | null): string {
  if (!anchor) return '第 1 段';
  const raw = anchor.trim().replace(/^\[+|\]+$/g, '');

  // page_1_p_2
  let match = raw.match(/^page_(\d+)_p_(\d+)$/i);
  if (match) return `第 ${match[1]} 页第 ${match[2]} 段`;

  // page_1_tbl_2
  match = raw.match(/^page_(\d+)_tbl_(\d+)$/i);
  if (match) return `第 ${match[1]} 页表格 ${match[2]}`;

  // line_12
  match = raw.match(/^line_(\d+)$/i);
  if (match) return `第 ${match[1]} 行`;

  // p_3
  match = raw.match(/^p_(\d+)$/i);
  if (match) return `第 ${match[1]} 段`;

  // tbl_2
  match = raw.match(/^tbl_(\d+)$/i);
  if (match) return `表格 ${match[1]}`;

  // page_1
  match = raw.match(/^page_(\d+)$/i);
  if (match) return `第 ${match[1]} 页`;

  // p.1
  match = raw.match(/^p\.(\d+)$/i);
  if (match) return `第 ${match[1]} 页`;

  // head_1 / heading_1
  match = raw.match(/^head(?:ing|er)?_(\d+)$/i);
  if (match) return `第 ${match[1]} 级标题`;

  return raw;
}

/**
 * 知识原子结构字段映射为中文展示
 */
export function formatFieldName(fieldName?: string | null): string {
  if (!fieldName) return '核心陈述';
  const map: Record<string, string> = {
    statement: '核心陈述',
    subject: '业务执行主体',
    conditions: '触发前提与条件',
    actions: '执行动作与标准',
    exceptions: '例外与禁止情形',
    metric_definition: '指标口径规范',
    case_details: '案例详情',
    title: '知识条目标题',
    content: '正文内容',
    customer_types: '客户类型',
    business_scenes: '业务场景',
    problem_tags: '问题标签',
  };
  return map[fieldName.toLowerCase()] || fieldName;
}

/**
 * 文件类型中文表达
 */
export function formatFileType(fileType?: string | null): string {
  if (!fileType) return '通用文档';
  const lower = fileType.toLowerCase();
  if (lower === 'md' || lower === 'markdown') return 'Markdown 文档';
  if (lower === 'docx' || lower === 'doc') return 'Word 文档';
  if (lower === 'pdf') return 'PDF 文档';
  if (lower === 'txt') return '纯文本文档';
  return fileType.toUpperCase();
}
