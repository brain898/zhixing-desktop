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

export interface MetricDefinition {
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
  index_status: 'not_indexed' | 'indexing' | 'ready' | 'failed' | 'pending_cleanup';
  revision_token: string;
  extraction_context: Record<string, any>;
  related_cases: string[];
  evidence_count: number;
}

export interface KnowledgeItemDetail {
  id: string;
  document_id: string;
  document_title: string;
  document_version_label: string;
  document_file_name: string;
  access_scope: 'admin_only' | 'org_internal';
  lifecycle_status: string;
  created_at: string;
  updated_at: string;
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
}

export interface KnowledgeStats {
  total: number;
  category_counts: Record<PrimaryCategory, number>;
  unclassified_count: number;
  pending_review_count: number;
  confirmed_count: number;
}

export interface KnowledgeItemsResponse {
  items: KnowledgeItem[];
  stats: KnowledgeStats;
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

declare global {
  interface Window {
    electronAPI?: {
      minimize: () => void;
      maximize: () => void;
      close: () => void;
      isMaximized: () => Promise<boolean>;
    };
  }
}
