export type UserRole = 'admin' | 'member';

export interface User {
  id: string;
  organization_id: string;
  organization_name: string;
  username: string;
  display_name: string;
  role: UserRole;
  account_status: 'active' | 'disabled';
}

export interface KnowledgeOverview {
  document_count: number;
  knowledge_count: number;
  pending_count: number;
  category_counts: Record<string, number>;
  is_empty: boolean;
}

export interface DocumentItem {
  id: string;
  title: string;
  active_version_id: string | null;
  created_at: string;
  updated_at: string;
  version_count: number;
  version_label: string;
  file_name: string;
  file_type: string;
  file_size: number;
  uploaded_at: string;
  processing_status: 'uploading' | 'queued' | 'parsing' | 'extracting' | 'completed' | 'partial_failed' | 'failed' | 'cancelled';
  error_summary: string | null;
  task_id: string | null;
  task_status: string | null;
  attempt_count: number;
  block_count: number;
}

export interface DocumentVersion {
  id: string;
  version_label: string;
  file_name: string;
  file_type: string;
  file_size: number;
  uploaded_at: string;
  processing_status: 'uploading' | 'queued' | 'parsing' | 'extracting' | 'completed' | 'partial_failed' | 'failed' | 'cancelled';
  error_summary: string | null;
  task_id: string | null;
  task_status: string | null;
  attempt_count: number;
  block_count: number;
}

export interface DocumentDetail {
  id: string;
  title: string;
  active_version_id: string | null;
  created_at: string;
  updated_at: string;
  versions: DocumentVersion[];
}

export interface SourceBlock {
  id: string;
  block_index: number;
  block_type: 'heading' | 'paragraph' | 'table' | 'list_item';
  heading_path: string | null;
  page_number: number | null;
  paragraph_anchor: string | null;
  text_content: string;
}

export type PrimaryCategory = '制度与标准' | '方法与工具' | '项目案例' | '指标数据' | '专家经验';
export type AtomType = '规则' | '判断' | '方法' | '案例' | '指标' | '经验';
export type FieldState = 'supported' | 'not_stated' | 'not_applicable' | 'failed';

export interface KnowledgeEvidence {
  id: string;
  source_block_id: string;
  field_name: string;
  excerpt: string;
  accuracy_level: string;
  block_index?: number;
  block_type?: string;
  heading_path?: string | null;
  page_number?: number | null;
  paragraph_anchor?: string | null;
  text_content?: string;
}

export type JevEvaluationStatus = 'not_started' | 'queued' | 'running' | 'completed' | 'failed' | 'not_configured' | 'disabled' | 'stale';

export interface JevEvaluationAnswer {
  question_id: string;
  question_type: 'choice' | 'score' | 'noul';
  question_label: string;
  relevant_fields: string[];
  choice: string | null;
  probabilities: Record<string, number>;
  confidence: number | null;
  display_status: string;
  ignored: boolean;
  ignore_reason: string | null;
}

export interface JevEvaluation {
  id?: string;
  status: JevEvaluationStatus;
  is_stale: boolean;
  requested_model?: string;
  actual_model?: string | null;
  question_definition_version?: string;
  classification_suggestion?: PrimaryCategory | '无法确定' | null;
  classification_disagrees?: boolean;
  review_priority?: 'high' | 'medium' | 'normal' | 'not_available';
  priority_reasons?: string[];
  usage?: Record<string, number>;
  elapsed_ms?: number | null;
  error_code?: string | null;
  error_message?: string | null;
  thresholds_calibrated?: boolean;
  answers: JevEvaluationAnswer[];
}

export interface MetricRow {
  name: string;
  relation: string;
  value: string;
  unit: string;
  condition: string;
  period: string;
  linkage: string;
  note: string;
}

export interface MetricDefinition {
  rows?: MetricRow[];
  name: string;
  unit: string;
  period: string;
  criteria: string;
}

export interface CaseDetails {
  background: string;
  actions: string;
  results: string;
  limitations: string;
}

export interface KnowledgeItem {
  id: string;
  document_id: string;
  document_title: string;
  access_scope: 'admin_only' | 'org_internal';
  lifecycle_status: 'active' | 'disabled' | 'deleted';
  created_at: string;
  updated_at: string;
  active_version_id: string;
  source_document_version_id: string;
  version_number: number;
  title: string;
  content: string;
  primary_category: PrimaryCategory | null;
  atom_type: AtomType;
  subject: string;
  statement: string;
  conditions: string[];
  actions: string[];
  exceptions: string[];
  metric_definition: MetricDefinition | null;
  case_details: CaseDetails | null;
  field_states: Record<string, FieldState>;
  quality_flags: string[];
  customer_types: string[];
  business_scenes: string[];
  problem_tags: string[];
  source_anchors: string[];
  valid_from: string | null;
  valid_until: string | null;
  review_status: 'pending_review' | 'confirmed';
  reviewed_by?: string | null;
  created_by?: string | null;
  index_status: 'not_indexed' | 'indexing' | 'ready' | 'failed' | 'pending_cleanup';
  revision_token: string;
  extraction_context: Record<string, any>;
  related_cases: string[];
  business_importance?: 'critical' | 'normal' | 'informational';
  importance_rationale?: string | null;
  importance_adjusted_by?: string | null;
  evidence_count: number;
  has_draft_version?: boolean;
  draft_version_number?: number | null;
  pending_review_status?: 'pending_review' | 'confirmed' | null;
  pending_index_status?: 'not_indexed' | 'indexing' | 'ready' | 'failed' | 'pending_cleanup' | null;
}

export interface KnowledgeItemDetail {
  id: string;
  document_id: string;
  document_title: string;
  document_version_label: string;
  document_file_name: string;
  access_scope: 'admin_only' | 'org_internal';
  document_access_scope?: 'admin_only' | 'org_internal';
  lifecycle_status: string;
  created_at: string;
  updated_at: string;
  is_draft_version?: boolean;
  serving_version_number?: number | null;
  active_version: KnowledgeItem;
  evidence: KnowledgeEvidence[];
  version_history: Array<{
    id: string;
    version_number: number;
    title: string;
    primary_category: PrimaryCategory | null;
    review_status: string;
    index_status: string;
    created_at: string;
    created_by: string;
  }>;
  can_confirm?: boolean;
  confirmation_blockers?: string[];
  batch_review_reasons?: string[];
  jev_evaluation?: JevEvaluation;
}

export interface KnowledgeStats {
  total: number;
  category_counts: Record<PrimaryCategory, number>;
  unclassified_count: number;
  pending_review_count: number;
  confirmed_count: number;
  active_count?: number;
  disabled_count?: number;
}

export interface KnowledgeItemsResponse {
  items: KnowledgeItem[];
  stats: KnowledgeStats;
}

export interface SourceLocator {
  block_index: number | null;
  block_type: string | null;
  heading_path: string | null;
  page_number: number | null;
  paragraph_anchor: string | null;
}

export interface SearchKnowledgeEvidence {
  id: string;
  source_block_id: string;
  field_name: string;
  excerpt: string;
  accuracy_level: string;
  source_locator: SourceLocator;
}

export interface SearchKnowledgeResultItem {
  item_id: string;
  version_id: string;
  version_number: number;
  title: string;
  primary_category: PrimaryCategory;
  atom_type: AtomType;
  subject: string;
  statement: string;
  content: string;
  document_id: string;
  document_title: string;
  document_version_label: string;
  conditions: string[];
  actions: string[];
  exceptions: string[];
  metric_definition: MetricDefinition | null;
  case_details: CaseDetails | null;
  customer_types: string[];
  business_scenes: string[];
  problem_tags: string[];
  valid_from: string | null;
  valid_until: string | null;
  access_scope: 'admin_only' | 'org_internal';
  lifecycle_status: 'active' | 'disabled';
  source: {
    document_id: string;
    document_title: string;
    document_version_id: string;
    document_version_label: string;
    file_name: string;
    source_anchors: string[];
  };
  evidence: SearchKnowledgeEvidence[];
  evidence_count: number;
  matched_snippets: string[];
  matched_fragments: Array<{
    fragment_key: string;
    fragment_type: string;
    channels: string[];
    rank: number;
    dense_similarity: number | null;
    snippet: string;
    evidence_ids: string[];
  }>;
  score: number;
}

export interface SearchKnowledgeResponse {
  query: string;
  total: number;
  items: SearchKnowledgeResultItem[];
}

export interface UploadResult {
  status: 'success' | 'duplicate_content' | 'conflict_name';
  message: string;
  document_id?: string;
  version_id?: string;
  task_id?: string;
  title?: string;
  version_label?: string;
  existing_document_id?: string;
  existing_title?: string;
  existing_version_id?: string;
  file_name?: string;
}

export type NavItemKey = 'knowledge' | 'skills' | 'agent';

// 审核减负与章节集中核对相关类型
export type CoverageStatus = 'associated_candidate' | 'admin_ignored' | 'model_suggested_ignore' | 'uncovered';

export interface ChapterSourceBlock {
  id: string;
  block_index: number;
  block_type: string;
  heading_path: string | null;
  page_number: number | null;
  paragraph_anchor: string | null;
  text_content: string;
  coverage_status: CoverageStatus;
  coverage_note: string;
  ignore_status: string;
  ignore_reason: string | null;
  ignored_by: string | null;
  ignored_at: string | null;
  associated_items: Array<{ item_id: string; title: string; field_name: string }>;
}

export interface ChapterKnowledgeItem {
  item_id: string;
  version_id: string;
  title: string;
  statement: string;
  content: string;
  primary_category: PrimaryCategory | null;
  atom_type: AtomType;
  subject: string;
  conditions: string[];
  actions: string[];
  exceptions: string[];
  metric_definition: MetricDefinition | null;
  case_details: CaseDetails | null;
  field_states: Record<string, FieldState>;
  quality_flags: string[];
  source_anchors: string[];
  review_status: 'pending_review' | 'confirmed';
  reviewed_by?: string | null;
  created_by?: string | null;
  index_status: 'not_indexed' | 'indexing' | 'ready' | 'failed' | 'pending_cleanup';
  revision_token: string;
  is_draft: boolean;
  business_importance: 'critical' | 'normal' | 'informational';
  importance_rationale: string;
  importance_adjusted_by: string | null;
  issues_summary: {
    deterministic_errors: Array<{ issue_type: string; message: string; blocking: boolean; source_anchor: string | null }>;
    model_doubts: Array<{ issue_type: string; message: string; blocking: boolean; source_anchor: string | null }>;
    conflict_check_status: string;
    unimplemented_capabilities: string[];
  };
  can_confirm: boolean;
  confirmation_blockers: string[];
  batch_review_reasons: string[];
  can_batch_confirm: boolean;
  batch_block_reason: string | null;
  evidence: KnowledgeEvidence[];
  associated_block_ids: string[];
  jev_evaluation?: JevEvaluation;
}

export interface ChapterGroup {
  chapter_key: string;
  chapter_name: string;
  heading_path: string | null;
  is_derived_group: boolean;
  group_description: string;
  blocks: ChapterSourceBlock[];
  items: ChapterKnowledgeItem[];
  stats: {
    total_blocks: number;
    uncovered_blocks: number;
    associated_blocks: number;
    admin_ignored_blocks: number;
    model_suggested_ignore_blocks: number;
    total_items: number;
    pending_items: number;
    confirmed_items: number;
    critical_items: number;
    can_batch_confirm_count: number;
  };
  attention_points: Array<{
    type: string;
    severity: 'blocking' | 'important' | 'warning';
    message: string;
    item_id?: string;
  }>;
  chapter_status: 'has_uncovered' | 'pending_source_review' | 'pending_review' | 'fully_reviewed' | 'empty';
  status_label: string;
}

export interface ChapterReviewResponse {
  document_id: string;
  document_title: string;
  version_id: string;
  version_label: string;
  file_name: string;
  chapters: ChapterGroup[];
  summary: {
    total_chapters: number;
    total_blocks: number;
    uncovered_blocks: number;
    associated_blocks: number;
    admin_ignored_blocks: number;
    model_suggested_ignore_blocks: number;
    total_items: number;
    pending_items: number;
    confirmed_items: number;
    critical_items: number;
  };
}

export interface BatchConfirmResponse {
  message: string;
  confirmed_count: number;
  skipped_count: number;
  confirmed_items: Array<{ item_id: string; version_id: string; title: string }>;
  skipped_items: Array<{ item_id: string; reason: string; status_code?: number }>;
}

// M02 Skill 工厂：场景目录（PRD 第 5 章）与原子召回（FR04）
export type SceneStatus = 'active' | 'disabled';

export interface Scene {
  scene_id: string;
  name: string;
  description: string;
  aliases: string[];
  typical_problems: string[];
  status: SceneStatus;
  origin: string | null;
  revision_token: string;
  created_by: string;
  created_at: string;
  updated_by: string | null;
  updated_at: string;
}

export interface SceneRecallStats {
  tag_hits: number;
  semantic_hits: number;
  merged: number;
  eligible: number;
  returned: number;
  truncated: boolean;
  tag_in_pool: number;
  semantic_in_pool: number;
}

export interface SceneSkillCounts {
  pending_review: number;
  approved: number;
  needs_recheck: number;
  total: number;
}

export interface SceneCard extends Scene {
  statistics_pending?: boolean;
  available_atom_count: number;
  category_coverage: Record<string, number>;
  recall_stats: SceneRecallStats;
  semantic_error: string | null;
  skill_counts: SceneSkillCounts;
  can_generate: boolean;
  generate_blocked_reason: string | null;
  min_atoms_for_generation: number;
  running_batch_id?: string | null;
  latest_batch?: SkillBatchSummary | null;
}

export interface SceneCardsResponse {
  has_catalog: boolean;
  cards: SceneCard[];
  min_atoms_for_generation: number;
  generation_available: boolean;
  generation_unavailable_reason: string | null;
}

// ---------------------------------------------------------------------------
// M02-C 生成批次（PRD 第 6 章）
// ---------------------------------------------------------------------------

export type SkillBatchStatus = 'running' | 'completed' | 'partial' | 'failed' | 'no_tasks';
export type SkillStageStatus = 'waiting' | 'running' | 'done' | 'failed' | 'skipped';
export type SkillTaskResultStatus =
  | 'pending'
  | 'generating'
  | 'validating'
  | 'repairing'
  | 'stored'
  | 'validation_failed'
  | 'call_failed'
  | 'skipped';

export interface SkillBatchSummary {
  batch_id: string;
  scene_id: string;
  scene_name: string | null;
  status: SkillBatchStatus;
  status_label: string;
  status_reason: string | null;
  focus_note: string | null;
  initiated_at: string;
  completed_at: string | null;
  stored_count: number;
  task_count: number;
}

export interface SkillBatchStage {
  key: 'recall' | 'split' | 'generate' | 'validate';
  label: string;
  status: SkillStageStatus;
  detail: string | null;
}

export interface SkillPoolAtom {
  atom_version_id: string;
  atom_item_id: string;
  title: string;
  primary_category: string | null;
  atom_type: string | null;
  recall_source: 'tag' | 'semantic';
  matched_tags: string[];
}

export interface SkillTaskResult {
  task_key: string;
  name?: string;
  status: SkillTaskResultStatus;
  status_label?: string;
  skill_id?: string;
  repair_count?: number;
  skip_reason?: string | null;
  similar_skills?: SkillSimilarRef[];
}

export interface SkillSplitTask {
  task_key: string;
  name: string;
  goal: string;
  task_type: string;
  split_reason: string;
  atom_version_ids: string[];
  atoms: { atom_version_id: string; title: string | null }[];
  status: 'to_generate' | 'skipped';
  skip_reason: string | null;
  hints: string[];
  possible_duplicate_of: { task_key: string; overlap: number }[];
  result?: SkillTaskResult | null;
}

export interface SkillSplitResult {
  tasks: SkillSplitTask[];
  unused_atoms: { atom_version_id: string; title: string; reason: string; source: 'model' | 'program' }[];
  no_task_reason: string | null;
  generate_count: number;
}

export interface SkillSimilarRef {
  skill_id: string;
  name: string | null;
  status: string;
  overlap: number;
}

export interface SkillCandidateSummary {
  skill_id: string;
  batch_id: string | null;
  task_key: string | null;
  name: string;
  status: string;
  status_label: string;
  confidence_level: '高' | '中' | '低' | null;
  unsupported_count: number | null;
  ref_count: number;
  version_number: number | null;
  similar_skills: SkillSimilarRef[];
  error_summary: string[];
  updated_at: string | null;
}

export interface SkillBatchDetail extends SkillBatchSummary {
  stages: SkillBatchStage[];
  atom_pool: {
    atoms: SkillPoolAtom[];
    semantic_error: string | null;
    stats?: { eligible: number; returned: number; tag_truncated?: number; semantic_truncated?: number };
  } | null;
  task_split: SkillSplitResult | null;
  task_results: SkillTaskResult[];
  candidates: SkillCandidateSummary[];
}

export interface SkillIssue {
  level: 'hard_error' | 'hint';
  code: string;
  path: string;
  message: string;
  plain: string;
}

export interface SkillCandidateDetail extends SkillCandidateSummary {
  scene_id: string;
  visibility: string | null;
  version_kind: string | null;
  skill_json: Record<string, unknown> | null;
  issues: SkillIssue[];
  repair_count: number | null;
}

export interface SceneTagStat {
  tag: string;
  count: number;
  atoms: Array<{ item_id: string; version_id: string; title: string }>;
}

export interface SceneMergeGroup {
  name: string;
  description: string;
  typical_problems: string[];
  tags: string[];
  reason?: string;
  /** 整理新标签时：true 表示归入已有场景 */
  existing?: boolean;
  target_scene_id?: string | null;
}

export type SceneMergeSuggestionStatus = 'queued' | 'running' | 'completed' | 'failed' | 'confirmed' | 'discarded';

export interface SceneMergeSuggestion {
  suggestion_id: string;
  /** initial：首次整理；incremental：目录建立后整理新标签 */
  mode: 'initial' | 'incremental';
  status: SceneMergeSuggestionStatus;
  tag_stats: SceneTagStat[];
  groups: SceneMergeGroup[];
  unassigned_tags: string[];
  model_name: string | null;
  prompt_version: string;
  attempt_count: number;
  attempts: Array<{ attempt: number; ok: boolean; errors?: string[]; raw_length?: number; at?: string }>;
  error_message: string | null;
  requested_by: string;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  confirmed_by: string | null;
  confirmed_at: string | null;
}

export interface SceneCatalogResponse {
  has_catalog: boolean;
  scenes: Scene[];
  pending_tag_count: number;
  /** 还没归入任何场景、也没被忽略的标签数（来自已确认知识） */
  unorganized_tag_count: number;
  latest_suggestion: SceneMergeSuggestion | null;
}

export type ScenePendingTagStatus = 'pending' | 'mapped' | 'created' | 'ignored';

export interface ScenePendingTag {
  pending_tag_id: string;
  tag: string;
  status: ScenePendingTagStatus;
  source_atoms: Array<{
    item_id: string | null;
    version_id: string | null;
    title: string | null;
    source: 'extraction' | 'manual' | 'catalog_init';
    at: string;
  }>;
  resolved_scene_id: string | null;
  resolution_note: string | null;
  resolved_by: string | null;
  resolved_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface SceneRecallAtom {
  item_id: string;
  version_id: string;
  title: string;
  primary_category: string | null;
  atom_type: string | null;
  statement: string | null;
  business_scenes: string[];
  matched_by: Array<'tag' | 'semantic'>;
  matched_tags: string[];
  semantic_score: number | null;
  semantic_rank: number | null;
  recall_source: 'tag' | 'semantic';
}

export interface SceneRecallResponse {
  scene_id: string;
  atoms: SceneRecallAtom[];
  stats: SceneRecallStats;
  category_coverage: Record<string, number>;
  semantic_query: string;
  semantic_error: string | null;
  config: { pool_limit: number; semantic_top_n: number };
}

export interface SceneFormPayload {
  name: string;
  description: string;
  aliases: string[];
  typical_problems: string[];
  revision_token?: string;
}

// ---------------------------------------------------------------------------
// M02-D 审核（PRD 第 7、8 章 FR09~FR13）
// ---------------------------------------------------------------------------

export type SkillStatus =
  | 'generating'
  | 'validation_failed'
  | 'pending_review'
  | 'approved'
  | 'rejected'
  | 'needs_recheck';

export type SkillReviewAction = 'approve' | 'regenerate' | 'reject' | 'abandon' | 'restore' | 'save_draft' | 'recheck';
export type SkillSubmitAction = Exclude<SkillReviewAction, 'save_draft'>;

export interface SkillReviewDraft {
  skill_json: SkillContent;
  resolutions: Record<string, { action: string; reason: string }>;
  checklist: Record<string, boolean>;
  change_reasons: Record<string, string>;
  stale_resolutions: Record<string, { action: StaleResolutionAction; note: string }>;
}

export type SkillReviewPayloads = {
  approve: SkillReviewDraft & { comment: string };
  recheck: SkillReviewDraft & { comment: string };
  regenerate: { comment: string; field_groups: string[] };
  reject: { reason: string; note: string };
  abandon: Record<never, never>;
  restore: Record<never, never>;
};

export interface SkillIoField {
  key: string;
  label: string;
  type: 'text' | 'number' | 'boolean' | 'enum' | 'date' | 'period' | 'file';
  required: boolean;
  unit?: string | null;
  allowed_values?: string[] | null;
  source_ref?: string | null;
}

export interface SkillStep {
  step_id: string;
  kind: '输入校验' | '知识检索' | '计算' | '规则判断' | '生成表达' | '人工确认';
  condition?: string | null;
  action: string;
  refs: string[];
  basis: '有原子依据' | '无依据' | '通用操作' | '专家补充';
  expert_reason?: string | null;
  on_fail: '补问' | '暂停' | '转人工' | '返回不可计算';
}

export interface SkillKnowledgeRef {
  atom_item_id: string;
  atom_version_id: string;
  role: string;
  role_reason?: string | null;
  used_in_steps: string[];
}

export interface SkillContent {
  schema_version?: string;
  name: string;
  goal: string;
  trigger_description: string;
  task_type: string;
  scene_id?: string;
  applies_to: { customer_types: string[]; property_types: string[]; conditions: string[] };
  not_applies_to: string[];
  knowledge_refs: SkillKnowledgeRef[];
  inputs: SkillIoField[];
  preconditions: { text: string; ref?: string | null }[];
  outputs: SkillIoField[];
  output_template?: string | null;
  steps: SkillStep[];
  risk_boundary: string[];
  escalation_conditions: string[];
  generation_confidence?: { level: '高' | '中' | '低'; reason: string } | null;
  maintainer?: string | null;
  [key: string]: unknown;
}

export interface SkillListItem extends SkillCandidateSummary {
  scene_id: string;
  scene_name: string | null;
  batch_initiated_at: string | null;
  created_at: string;
  atom_changed: boolean;
  stale_reason: string | null;
  visibility: string;
  regenerate_count: number;
  has_draft: boolean;
}

export interface SkillListResponse {
  items: SkillListItem[];
  total: number;
  status_counts: Record<string, number>;
  filters: {
    scenes: [string, string][];
    batches: { batch_id: string; initiated_at: string | null; scene_name: string | null }[];
  };
}

export interface SkillBlocker {
  code: 'CHECKLIST' | 'TBD_EXPERT' | 'UNSUPPORTED_UNRESOLVED' | 'REF_NOT_ELIGIBLE' | 'STRUCTURE' | 'STEP_ID_REUSED';
  message: string;
  details?: string[];
  paths?: string[];
  item_keys?: string[];
}

export interface SkillFieldDiff {
  path: string;
  field: string;
  group: string;
  group_label: string;
  op: 'add' | 'delete' | 'modify' | 'reorder';
  op_label: string;
  label: string;
  before: unknown;
  after: unknown;
  reason?: string | null;
}

export interface SkillDiffSummary {
  changed_field_count: number;
  steps_added: number;
  steps_deleted: number;
  steps_reordered: boolean;
  by_group: Record<string, number>;
  unsupported_resolutions: Record<string, number>;
}

export interface SkillUnsupportedItem {
  kind: '无依据步骤' | '疑似无依据数值' | '例外未落点';
  path: string;
  item_key: string;
  value?: string | null;
  atom_version_id?: string | null;
  message: string;
  label: string;
  still_present: boolean;
  resolution: 'delete' | 'add_ref' | 'expert' | 'landed' | 'edited' | null;
  resolution_label: string | null;
  reason: string | null;
}

export interface SkillReviewIssue {
  level: 'hard_error' | 'hint';
  code: string;
  path: string;
  message: string;
  plain: string;
  label: string;
  detail?: Record<string, unknown>;
}

export interface SkillEvaluation {
  can_approve: boolean;
  blockers: SkillBlocker[];
  has_changes: boolean;
  approve_label: string;
  diffs: SkillFieldDiff[];
  summary: SkillDiffSummary;
  issues: SkillReviewIssue[];
  hint_count: number;
  hard_error_count: number;
  unsupported_items: SkillUnsupportedItem[];
  visibility: string;
  atom_changes: SkillAtomChange[];
}

/** M02-E（FR14）：原子旧版本与新版本的字段差异 */
export interface SkillAtomFieldDiff {
  field: string;
  label: string;
  change: string;
  list: boolean;
  before: any;
  after: any;
  removed?: string[];
  added?: string[];
}

export type StaleResolutionAction = 'update' | 'no_impact' | 'remove';

export interface SkillAtomVersionState {
  atom_version_id: string;
  atom_item_id?: string;
  version_number?: number;
  title: string;
  statement: string;
  conditions: string[];
  actions: string[];
  exceptions: string[];
  valid_from?: string | null;
  valid_until?: string | null;
}

/** M02-E（FR14）：受影响的依据知识及其处理情况 */
export interface SkillAtomChange {
  atom_version_id: string;
  atom_item_id: string | null;
  title: string;
  role: string | null;
  triggers: string[];
  trigger_labels: string[];
  events: { trigger: string; trigger_label: string; created_at: string; effect: string }[];
  still_eligible: boolean;
  exists: boolean;
  old_version: SkillAtomVersionState | null;
  new_version_id: string | null;
  new_version: SkillAtomVersionState | null;
  diff: SkillAtomFieldDiff[];
  options: StaleResolutionAction[];
  option_labels: Record<string, string>;
  remove_only: boolean;
  affected_paths: string[];
  still_referenced: boolean;
  resolution: StaleResolutionAction | null;
  resolution_label: string | null;
  note: string;
  handled: boolean;
  problem: string | null;
}

export interface SkillAtomPanelItem {
  atom_version_id: string;
  atom_item_id: string;
  role: string;
  used_in_steps: string[];
  title: string;
  statement: string;
  conditions: string[];
  actions: string[];
  exceptions: string[];
  metric_definition: MetricDefinition | null;
  case_details: CaseDetails | null;
  primary_category: string | null;
  atom_type: string | null;
  subject: string | null;
  version_number: number | null;
  default_roles: string[];
  eligible: boolean;
  has_newer_version: boolean;
  exists: boolean;
  recall_source: 'tag' | 'semantic' | 'outside_pool' | null;
  in_pool: boolean;
  exceptions_not_landed: string[];
}

export interface SkillVersionInfo {
  version_id: string;
  version_number: number;
  version_kind: string;
  version_kind_label: string;
  based_on_version_id: string | null;
  review_action: string | null;
  review_action_label: string | null;
  reviewed_by_name: string | null;
  reviewed_at: string | null;
  created_by_name: string;
  created_at: string;
}

export interface SkillReviewRecord {
  record_id: string;
  action: string;
  action_label: string;
  operator_id: string;
  operator_name: string;
  from_version_id: string | null;
  to_version_id: string | null;
  from_version_number: number | null;
  to_version_number: number | null;
  comment: string | null;
  reject_reason: string | null;
  regenerate_groups: string[];
  checklist: Record<string, boolean> | null;
  field_diffs: SkillFieldDiff[];
  detail: Record<string, any>;
  created_at: string;
}

export interface SkillRegenerateTask {
  task_id: string;
  status: string;
  source_status: string;
  created_at: string;
  completed_at: string | null;
  outcome: string | null;
  message: string | null;
}

export interface SkillWorkbench extends SkillCandidateSummary {
  scene_id: string;
  scene_name: string | null;
  status: SkillStatus;
  visibility: string;
  revision_token: string;
  current_version_id: string | null;
  current_version_kind: string | null;
  current_version_kind_label: string | null;
  generation_confidence: { level: string; reason: string } | null;
  base_json: Record<string, any>;
  content: SkillContent;
  has_draft: boolean;
  stale_draft: boolean;
  draft_updated_at: string | null;
  draft_updated_by_name: string | null;
  resolutions: Record<string, { action: string; reason: string }>;
  checklist: Record<string, boolean>;
  change_reasons: Record<string, string>;
  evaluation: SkillEvaluation;
  base_hints: SkillIssue[];
  atoms: SkillAtomPanelItem[];
  atom_changed: boolean;
  stale_reason: string | null;
  atom_changes: SkillAtomChange[];
  stale_resolutions: Record<string, { action: StaleResolutionAction; note: string }>;
  batch_pool: (SkillPoolAtom & { eligible: boolean })[];
  next_step_number: number;
  versions: SkillVersionInfo[];
  review_records: SkillReviewRecord[];
  regenerate_count: number;
  regenerate_task: SkillRegenerateTask | null;
  generation_issues: SkillIssue[];
  checklist_items: { key: string; label: string }[];
  reject_reasons: string[];
  field_groups: { key: string; label: string }[];
  allowed_actions: SkillReviewAction[];
}

export interface SkillCheckResponse {
  evaluation: SkillEvaluation;
  content: SkillContent;
  atoms: SkillAtomPanelItem[];
}

export interface SkillActionResponse {
  status: SkillStatus;
  revision_token: string;
  record_id?: string;
  action?: string;
  action_label?: string;
  version_id?: string;
  version_number?: number | null;
  task_id?: string;
  next_skill_id?: string | null;
}

export interface SkillCompareSide {
  version_id: string;
  version_number: number;
  version_kind: string;
  version_kind_label: string;
  created_at: string;
  created_by_name: string;
  skill_json: Record<string, any>;
}

export interface SkillCompareResponse {
  skill_id: string;
  from: SkillCompareSide;
  to: SkillCompareSide;
  diffs: SkillFieldDiff[];
  summary: SkillDiffSummary;
  unsupported_items: SkillUnsupportedItem[];
}

export interface SkillExperience {
  experience_id: string;
  skill_id: string;
  skill_name: string | null;
  version_number: number | null;
  source_kind: string;
  source_path: string;
  source_label: string;
  content: string;
  reason: string;
  status: string;
  created_by_name: string;
  created_at: string;
}
// M02-F / FR13：空分母返回 null，不显示虚构比例。
export interface SkillStatisticRate { numerator: number; denominator: number; value: number | null }
export interface SkillStatisticMetrics {
  candidate_count: number;
  status_counts: Record<string, number>;
  validation_pass_rate: SkillStatisticRate;
  validation_unrecorded_count: number;
  review_pass_rate: SkillStatisticRate;
  review_pending_count: number;
  average_modified_fields: number | null;
  manual_review_record_count: number;
  modified_field_total: number;
  rejection_reasons: Record<string, number>;
  unsupported_resolutions: Record<string, number>;
}
export interface SkillStatistics {
  summary: SkillStatisticMetrics;
  by_scene: (SkillStatisticMetrics & { scene_id: string; name: string; status: string })[];
  by_batch: (SkillStatisticMetrics & { batch_id: string; scene_id: string; scene_name: string; initiated_at: string; status: string })[];
  status_labels: Record<string, string>;
  resolution_labels: Record<string, string>;
  definitions: Record<string, string>;
}
// M03：企业范围由服务端会话确定，前端不传企业标识。
export type ConsultStatus = 'routing' | 'awaiting_input' | 'running' | 'completed' | 'failed';
export interface ConsultSkill {
  id: string;
  name: string;
  goal?: string;
  trigger_description?: string;
  skill_version_id?: string;
  current_version_id?: string;
  version_number?: number;
  published: boolean;
  consult_status: string;
  available: boolean;
  pause_reason?: string;
}
export interface ConsultFormField {
  skill_id: string;
  skill_name?: string;
  key: string;
  label: string;
  type: 'text' | 'number' | 'enum' | 'boolean' | 'date' | 'period' | 'file';
  required?: boolean;
  unit?: string;
  allowed_values?: unknown[];
  description?: string;
}
export interface ConsultCalculation {
  label: string;
  expression: string;
  model_result?: unknown;
  program_result?: unknown;
  unit?: string;
  status?: string;
  note?: string;
  consistent?: boolean;
}
export interface ConsultEvidence {
  atom_version_id?: string;
  title: string;
  statement?: string;
  source_file_name?: string;
  source_version?: string;
  excerpts?: unknown[];
  evidence?: unknown[];
}
export interface ConsultRun {
  id?: string;
  skill_id: string;
  skill_name?: string;
  name?: string;
  skill_version_id: string;
  version_number?: number;
  order_index: number;
  status: string;
  inputs?: Record<string, unknown>;
  output?: Record<string, unknown>;
  recompute?: ConsultCalculation[];
  validation?: unknown;
}
export interface ConsultReport {
  summary?: string;
  skill_results?: unknown[];
  recompute?: ConsultCalculation[];
  actions?: string | string[];
  risk_boundaries?: {skill_id: string; name: string; items: string[]}[];
  manual_items?: unknown[];
  evidence?: (ConsultEvidence & {file_name?: string; version_label?: string})[];
  disclaimer?: string;
  mode?: string;
  [key: string]: unknown;
}
export interface ConsultSession {
  id: string;
  question: string;
  status: ConsultStatus;
  created_at: string;
  updated_at?: string;
  error?: string;
  failure?: {stage: string; error_code: string; http_status?: number | null; retryable: boolean};
  progress?: {message: string; current: number; total: number};
  route?: Record<string, unknown>;
  form?: {fields: ConsultFormField[]};
  inputs?: Record<string, Record<string, unknown>>;
  runs?: ConsultRun[];
  report?: ConsultReport | null;
  handoffs?: ConsultHandoff[];
}
export interface ConsultHandoff {
  id: string;
  session_id: string;
  question?: string;
  source: 'user' | 'system';
  reasons: unknown[];
  snapshot?: Record<string, unknown>;
  status: 'open' | 'resolved';
  created_at?: string;
  resolved_at?: string;
  note?: string;
}
