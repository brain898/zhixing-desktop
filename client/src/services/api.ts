import {
  User,
  DocumentItem,
  DocumentDetail,
  SourceBlock,
  UploadResult,
  KnowledgeItemsResponse,
  KnowledgeItemDetail,
  SearchKnowledgeResponse,
  ChapterReviewResponse,
  BatchConfirmResponse,
  Scene,
  SceneCardsResponse,
  SceneCatalogResponse,
  SceneFormPayload,
  SceneMergeGroup,
  SceneMergeSuggestion,
  ScenePendingTag,
  SceneRecallResponse,
  SkillBatchDetail,
  SkillCandidateDetail,
  SkillActionResponse,
  SkillCheckResponse,
  SkillCompareResponse,
  SkillReviewDraft,
  SkillReviewPayloads,
  SkillSubmitAction,
  SkillExperience,
  SkillListResponse,
  SkillWorkbench,
  SkillStatistics,
  ConsultSkill,
  ConsultSession,
  ConsultHandoff,
} from '../types';

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8766/api').replace(/\/$/, '');

let authToken: string | null = localStorage.getItem('zhixing_token');
let onUnauthorizedCallback: (() => void) | null = null;
let sceneCardsSnapshot: SceneCardsResponse | null = null;
let sceneCardsRequest = 0;

export const setAuthToken = (token: string | null) => {
  if (authToken !== token) {
    sceneCardsSnapshot = null;
    sceneCardsRequest += 1;
  }
  authToken = token;
  if (token) {
    localStorage.setItem('zhixing_token', token);
  } else {
    localStorage.removeItem('zhixing_token');
  }
};

export const getAuthToken = () => authToken;

export const setOnUnauthorized = (cb: () => void) => {
  onUnauthorizedCallback = cb;
};

interface RequestOptions extends RequestInit {
  responseType?: 'json' | 'blob';
}

async function request<T>(endpoint: string, options: RequestOptions = {}): Promise<T> {
  const isFormData = typeof FormData !== 'undefined' && options.body instanceof FormData;
  const headers: Record<string, string> = {
    ...(options.headers as Record<string, string>),
  };

  if (!isFormData && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json';
  }

  if (authToken) {
    headers['Authorization'] = `Bearer ${authToken}`;
  }

  const response = await fetch(`${API_BASE_URL}${endpoint}`, {
    ...options,
    headers,
  });

  if (response.status === 401) {
    setAuthToken(null);
    if (onUnauthorizedCallback) {
      onUnauthorizedCallback();
    }
    const err = await response.json().catch(() => ({ detail: '登录已失效' }));
    throw new Error(err.detail || '登录已失效，请重新登录');
  }

  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: `请求失败 (${response.status})` }));
    const detail = err?.detail;
    const message =
      typeof detail === 'object' && detail
        ? detail.message || `请求失败 (${response.status})`
        : detail || `请求失败 (${response.status})`;
    const error = new Error(message);
    (error as any).status = response.status;
    if (typeof detail === 'object' && detail?.code) {
      (error as any).code = detail.code;
    }
    throw error;
  }

  if (options.responseType === 'blob') {
    return (await response.blob()) as unknown as T;
  }

  return response.json();
}

export const api = {
  async getConsultSkills(): Promise<{ items: ConsultSkill[]; total: number }> {
    return request('/consult/skills');
  },
  async publishConsultSkill(skillId: string): Promise<unknown> {
    return request(`/consult/skills/${skillId}/publish`, { method: 'POST' });
  },
  async unpublishConsultSkill(skillId: string, reason = ''): Promise<unknown> {
    return request(`/consult/skills/${skillId}/unpublish`, { method: 'POST', body: JSON.stringify({ reason }) });
  },
  async createConsultSession(question: string): Promise<ConsultSession> {
    return request('/consult/sessions', { method: 'POST', body: JSON.stringify({ question }) });
  },
  async getConsultSessions(): Promise<{ items: ConsultSession[]; total: number }> {
    return request('/consult/sessions');
  },
  async getConsultSession(id: string): Promise<ConsultSession> {
    return request(`/consult/sessions/${id}`);
  },
  async submitConsultInputs(id: string, inputs: Record<string, Record<string, unknown>>): Promise<unknown> {
    return request(`/consult/sessions/${id}/inputs`, { method: 'POST', body: JSON.stringify({ inputs }) });
  },
  async retryConsultSession(id: string): Promise<unknown> {
    return request(`/consult/sessions/${id}/retry`, { method: 'POST' });
  },
  async handoffConsultSession(id: string, reason: string): Promise<ConsultHandoff> {
    return request(`/consult/sessions/${id}/handoff`, { method: 'POST', body: JSON.stringify({ reason }) });
  },
  async getConsultHandoffs(): Promise<{ items: ConsultHandoff[]; total: number }> {
    return request('/consult/handoffs');
  },
  async resolveConsultHandoff(id: string, note: string): Promise<unknown> {
    return request(`/consult/handoffs/${id}/resolve`, { method: 'POST', body: JSON.stringify({ note }) });
  },
  async login(username: string, password: string): Promise<{ token: string; user: User }> {
    const data = await request<{ token: string; user: User }>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    });
    setAuthToken(data.token);
    return data;
  },

  async logout(): Promise<void> {
    try {
      if (authToken) {
        await request('/auth/logout', { method: 'POST' });
      }
    } finally {
      setAuthToken(null);
    }
  },

  async getMe(): Promise<User> {
    return request<User>('/auth/me');
  },

  async getDocuments(): Promise<DocumentItem[]> {
    return request<DocumentItem[]>('/documents');
  },

  async getDocument(documentId: string): Promise<DocumentDetail> {
    return request<DocumentDetail>(`/documents/${documentId}`);
  },

  async getSourceBlocks(documentId: string, versionId: string): Promise<SourceBlock[]> {
    return request<SourceBlock[]>(`/documents/${documentId}/versions/${versionId}/source-blocks`);
  },

  async retryTask(documentId: string, versionId: string): Promise<{ message: string; task_id: string }> {
    return request<{ message: string; task_id: string }>(`/documents/${documentId}/versions/${versionId}/retry`, {
      method: 'POST',
    });
  },

  async getDocumentDeletionImpact(documentId: string): Promise<{
    document_id: string;
    title: string;
    version_count: number;
    derived_knowledge_count: number;
    retrieval_record_count: number;
    active_task_count: number;
    related_case_reference_count: number;
    skill_reference_count: number;
    skill_reference_status_counts?: Record<string, number>;
    skill_references?: { skill_id: string; name: string | null; status: string; status_label: string }[];
    physical_delete_scheduled: boolean;
  }> {
    return request(`/documents/${documentId}/deletion-impact`);
  },

  async deleteDocument(documentId: string): Promise<{ message: string; document_id: string }> {
    return request<{ message: string; document_id: string }>(`/documents/${documentId}`, {
      method: 'DELETE',
    });
  },

  async uploadDocument(
    file: File,
    duplicateMode: string = 'ask',
    targetDocumentId?: string
  ): Promise<UploadResult> {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('duplicate_mode', duplicateMode);
    if (targetDocumentId) {
      formData.append('target_document_id', targetDocumentId);
    }
    return request<UploadResult>('/documents/upload', {
      method: 'POST',
      body: formData,
    });
  },

  async downloadOriginalFile(documentId: string, versionId: string): Promise<Blob> {
    return request<Blob>(`/documents/${documentId}/versions/${versionId}/file`, {
      responseType: 'blob',
    });
  },

  async getKnowledgeItems(
    params?: {
      document_id?: string;
      category?: string;
      review_status?: string;
      lifecycle_status?: string;
      search?: string;
    },
    signal?: AbortSignal
  ): Promise<KnowledgeItemsResponse> {
    const query = new URLSearchParams();
    if (params?.document_id) query.append('document_id', params.document_id);
    if (params?.category) query.append('category', params.category);
    if (params?.review_status) query.append('review_status', params.review_status);
    if (params?.lifecycle_status) query.append('lifecycle_status', params.lifecycle_status);
    if (params?.search) query.append('search', params.search);
    const qs = query.toString() ? `?${query.toString()}` : '';
    return request<KnowledgeItemsResponse>(`/knowledge/items${qs}`, { signal });
  },

  async getKnowledgeItemDetail(itemId: string): Promise<KnowledgeItemDetail> {
    return request<KnowledgeItemDetail>(`/knowledge/items/${itemId}`);
  },

  async retryJevEvaluation(itemId: string): Promise<{ message: string; evaluation_id?: string; status: string }> {
    return request(`/knowledge/items/${itemId}/jev/retry`, { method: 'POST' });
  },

  async ignoreJevQuestion(itemId: string, questionId: string, reason?: string): Promise<{ message: string }> {
    return request(`/knowledge/items/${itemId}/jev/questions/${questionId}/ignore`, {
      method: 'POST',
      body: JSON.stringify({ reason: reason || '管理员已人工核对并忽略该模型提示' }),
    });
  },

  async getKnowledgeDeletionImpact(itemId: string): Promise<{
    item_id: string;
    title: string;
    version_count: number;
    retrieval_record_count: number;
    active_task_count: number;
    related_case_reference_count: number;
    skill_reference_count: number;
    skill_reference_status_counts?: Record<string, number>;
    skill_references?: { skill_id: string; name: string | null; status: string; status_label: string }[];
    source_document_preserved: boolean;
  }> {
    return request(`/knowledge/items/${itemId}/deletion-impact`);
  },

  async getKnowledgeVersionHistory(itemId: string, versionId: string): Promise<any> {
    return request(`/knowledge/items/${itemId}/versions/${versionId}`);
  },

  async restoreKnowledgeHistory(
    itemId: string,
    versionId: string,
    revisionToken: string
  ): Promise<{ message: string; draft_version_id: string; version_number: number; revision_token: string }> {
    return request(`/knowledge/items/${itemId}/versions/${versionId}/restore`, {
      method: 'POST',
      body: JSON.stringify({ revision_token: revisionToken }),
    });
  },

  async validateKnowledgeDraft(
    itemId: string,
    payload: Record<string, any>
  ): Promise<{ quality_flags: string[]; blocking: string[]; can_confirm: boolean; revision_token: string }> {
    return request(`/knowledge/items/${itemId}/draft/validate`, {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  },

  async saveKnowledgeDraft(
    itemId: string,
    payload: Record<string, any>
  ): Promise<{ message: string; revision_token: string; quality_flags: string[]; is_new_version_draft?: boolean }> {
    return request<{ message: string; revision_token: string; quality_flags: string[]; is_new_version_draft?: boolean }>(
      `/knowledge/items/${itemId}/draft`,
      {
        method: 'PUT',
        body: JSON.stringify(payload),
      }
    );
  },

  async retryKnowledgeStructure(itemId: string, revisionToken: string): Promise<{ message: string; quality_flags: string[]; revision_token: string }> {
    return request<{ message: string; quality_flags: string[]; revision_token: string }>(
      `/knowledge/items/${itemId}/structure/retry`,
      { method: 'POST', body: JSON.stringify({ revision_token: revisionToken }) }
    );
  },

  async confirmKnowledgeItem(
    itemId: string,
    payload: { revision_token: string }
  ): Promise<{ message: string; review_status: string; index_status: 'not_indexed' | 'indexing' | 'ready' | 'failed' | 'pending_cleanup'; revision_token: string; index_task_id: string; index_task_status: string }> {
    return request<{ message: string; review_status: string; index_status: 'not_indexed' | 'indexing' | 'ready' | 'failed' | 'pending_cleanup'; revision_token: string; index_task_id: string; index_task_status: string }>(
      `/knowledge/items/${itemId}/confirm`,
      {
        method: 'POST',
        body: JSON.stringify(payload),
      }
    );
  },

  async updateKnowledgeLifecycle(
    itemId: string,
    lifecycleStatus: 'active' | 'disabled'
  ): Promise<{ message: string; item_id: string; lifecycle_status: string }> {
    return request<{ message: string; item_id: string; lifecycle_status: string }>(
      `/knowledge/items/${itemId}/lifecycle`,
      {
        method: 'PUT',
        body: JSON.stringify({ lifecycle_status: lifecycleStatus }),
      }
    );
  },

  async searchKnowledge(
    params: {
      q: string;
      document_id?: string;
      category?: string[];
      customer_type?: string[];
      business_scene?: string[];
      problem_tag?: string[];
    },
    signal?: AbortSignal
  ): Promise<SearchKnowledgeResponse> {
    const query = new URLSearchParams();
    query.append('q', params.q);
    if (params.document_id) query.append('document_id', params.document_id);
    params.category?.forEach((value) => query.append('category', value));
    params.customer_type?.forEach((value) => query.append('customer_type', value));
    params.business_scene?.forEach((value) => query.append('business_scene', value));
    params.problem_tag?.forEach((value) => query.append('problem_tag', value));
    return request<SearchKnowledgeResponse>(`/knowledge/search?${query.toString()}`, { signal });
  },

  async deleteKnowledgeItem(
    itemId: string,
    actionType: 'delete' | 'exclude' = 'delete',
    reason?: string
  ): Promise<{ message: string; id: string }> {
    const params = new URLSearchParams();
    if (actionType) params.append('action_type', actionType);
    if (reason) params.append('reason', reason);
    const query = params.toString() ? `?${params.toString()}` : '';
    return request<{ message: string; id: string }>(`/knowledge/items/${itemId}${query}`, {
      method: 'DELETE',
    });
  },

  async getKnowledgeTags(): Promise<{
    customer_types: string[];
    business_scenes: string[];
    problem_tags: string[];
  }> {
    return request<{
      customer_types: string[];
      business_scenes: string[];
      problem_tags: string[];
    }>('/knowledge/tags');
  },

  async getChapterReview(documentId: string, versionId: string): Promise<ChapterReviewResponse> {
    return request<ChapterReviewResponse>(`/documents/${documentId}/versions/${versionId}/chapters/review`);
  },

  async ignoreSourceBlock(
    documentId: string,
    versionId: string,
    blockId: string,
    payload: { ignore_reason?: string } = {}
  ): Promise<{ message: string; block_id: string; ignore_status: string }> {
    return request<{ message: string; block_id: string; ignore_status: string }>(
      `/documents/${documentId}/versions/${versionId}/source-blocks/${blockId}/ignore`,
      {
        method: 'POST',
        body: JSON.stringify(payload),
      }
    );
  },

  async unignoreSourceBlock(
    documentId: string,
    versionId: string,
    blockId: string
  ): Promise<{ message: string; block_id: string; ignore_status: string }> {
    return request<{ message: string; block_id: string; ignore_status: string }>(
      `/documents/${documentId}/versions/${versionId}/source-blocks/${blockId}/ignore`,
      {
        method: 'DELETE',
      }
    );
  },

  async updateBusinessImportance(
    itemId: string,
    payload: {
      business_importance: 'critical' | 'normal' | 'informational';
      importance_rationale: string;
      version_id: string;
      revision_token: string;
    }
  ): Promise<{ message: string; item_id: string; business_importance: string; revision_token: string }> {
    return request<{ message: string; item_id: string; business_importance: string; revision_token: string }>(
      `/knowledge/items/${itemId}/importance`,
      {
        method: 'PUT',
        body: JSON.stringify(payload),
      }
    );
  },

  async supplementKnowledgeFromBlock(
    documentId: string,
    versionId: string,
    blockId: string,
    payload: {
      title: string;
      primary_category: string | null;
      atom_type: string;
      statement: string;
      content: string;
      business_importance: 'critical' | 'normal' | 'informational';
    }
  ): Promise<{ message: string; item_id: string; title: string }> {
    return request<{ message: string; item_id: string; title: string }>(
      `/documents/${documentId}/versions/${versionId}/source-blocks/${blockId}/supplement`,
      {
        method: 'POST',
        body: JSON.stringify(payload),
      }
    );
  },

  async batchConfirmKnowledgeItems(payload: {
    items: Array<{ item_id: string; revision_token: string }>;
  }): Promise<BatchConfirmResponse> {
    return request<BatchConfirmResponse>('/knowledge/items/batch-confirm', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  },

  // ---------- M02 Skill 工厂：场景目录 ----------
  async getSceneCatalog(): Promise<SceneCatalogResponse> {
    return request<SceneCatalogResponse>('/skill-factory/scene-catalog');
  },

  async requestSceneMergeSuggestion(): Promise<SceneMergeSuggestion> {
    return request<SceneMergeSuggestion>('/skill-factory/scene-catalog/suggestions', { method: 'POST' });
  },

  async confirmSceneMergeSuggestion(
    suggestionId: string,
    groups: SceneMergeGroup[]
  ): Promise<{ scenes: Scene[]; extended_scenes: Scene[]; pending_tags: string[] }> {
    return request<{ scenes: Scene[]; extended_scenes: Scene[]; pending_tags: string[] }>(
      `/skill-factory/scene-catalog/suggestions/${suggestionId}/confirm`,
      { method: 'POST', body: JSON.stringify({ groups }) }
    );
  },

  async discardSceneMergeSuggestion(suggestionId: string): Promise<SceneMergeSuggestion> {
    return request<SceneMergeSuggestion>(`/skill-factory/scene-catalog/suggestions/${suggestionId}/discard`, {
      method: 'POST',
    });
  },

  async getScenePendingTags(
    status: 'pending' | 'all' = 'pending'
  ): Promise<{ items: ScenePendingTag[]; total: number }> {
    return request<{ items: ScenePendingTag[]; total: number }>(
      `/skill-factory/scene-catalog/pending-tags?status=${status}`
    );
  },

  async resolveScenePendingTag(
    pendingTagId: string,
    payload:
      | { action: 'map'; scene_id: string }
      | { action: 'create'; new_scene: Partial<SceneFormPayload> }
      | { action: 'ignore'; note?: string }
  ): Promise<ScenePendingTag & { scene: Scene | null }> {
    return request<ScenePendingTag & { scene: Scene | null }>(
      `/skill-factory/scene-catalog/pending-tags/${pendingTagId}/resolve`,
      { method: 'POST', body: JSON.stringify(payload) }
    );
  },

  async createScene(payload: SceneFormPayload): Promise<Scene> {
    return request<Scene>('/skill-factory/scenes', { method: 'POST', body: JSON.stringify(payload) });
  },

  async updateScene(sceneId: string, payload: Partial<SceneFormPayload>): Promise<Scene> {
    return request<Scene>(`/skill-factory/scenes/${sceneId}`, { method: 'PUT', body: JSON.stringify(payload) });
  },

  async setSceneStatus(sceneId: string, status: 'active' | 'disabled', revisionToken?: string): Promise<Scene> {
    return request<Scene>(`/skill-factory/scenes/${sceneId}/${status === 'active' ? 'enable' : 'disable'}`, {
      method: 'POST',
      body: JSON.stringify(revisionToken ? { revision_token: revisionToken } : {}),
    });
  },

  async deleteScene(sceneId: string): Promise<{ message: string; scene_id: string }> {
    return request<{ message: string; scene_id: string }>(`/skill-factory/scenes/${sceneId}`, { method: 'DELETE' });
  },

  getCachedSceneCards(): SceneCardsResponse | null {
    return sceneCardsSnapshot;
  },

  async getSceneCards(forceRefresh = false, signal?: AbortSignal): Promise<SceneCardsResponse> {
    const session = authToken;
    const currentRequest = ++sceneCardsRequest;
    const query = `?background_refresh=true${forceRefresh ? '&force_refresh=true' : ''}`;
    const value = await request<SceneCardsResponse>(`/skill-factory/scenes/cards${query}`, { signal });
    if (session === authToken && currentRequest === sceneCardsRequest && !signal?.aborted) {
      sceneCardsSnapshot = value;
    }
    return value;
  },

  async getSkillStatistics(params: { scene_id?: string; batch_id?: string } = {}): Promise<SkillStatistics> {
    const query = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => { if (value) query.set(key, value); });
    return request<SkillStatistics>(`/skill-factory/statistics${query.size ? `?${query}` : ''}`);
  },

  async getSceneRecall(sceneId: string): Promise<SceneRecallResponse> {
    return request<SceneRecallResponse>(`/skill-factory/scenes/${sceneId}/recall`);
  },

  async startSkillBatch(sceneId: string, focusNote?: string): Promise<{ batch_id: string; scene_id: string; status: string }> {
    return request<{ batch_id: string; scene_id: string; status: string }>(`/skill-factory/scenes/${sceneId}/batches`, {
      method: 'POST',
      body: JSON.stringify(focusNote ? { focus_note: focusNote } : {}),
    });
  },

  async getSkillBatch(batchId: string): Promise<SkillBatchDetail> {
    return request<SkillBatchDetail>(`/skill-factory/batches/${batchId}`);
  },

  async getSkillCandidate(skillId: string): Promise<SkillCandidateDetail> {
    return request<SkillCandidateDetail>(`/skill-factory/skills/${skillId}`);
  },

  // ---------- M02-D 审核（FR09~FR13） ----------
  async listSkills(params: {
    scene_id?: string;
    status?: string;
    batch_id?: string;
    has_unsupported?: 'yes' | 'no' | '';
    needs_recheck?: boolean;
  } = {}): Promise<SkillListResponse> {
    const query = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== '' && value !== false) query.append(key, String(value));
    });
    const qs = query.toString();
    return request<SkillListResponse>(`/skill-factory/skills${qs ? `?${qs}` : ''}`);
  },

  async getSkillWorkbench(skillId: string): Promise<SkillWorkbench> {
    return request<SkillWorkbench>(`/skill-factory/skills/${skillId}/workbench`);
  },

  async checkSkillReview(
    skillId: string,
    body: SkillReviewDraft,
    signal?: AbortSignal
  ): Promise<SkillCheckResponse> {
    return request<SkillCheckResponse>(`/skill-factory/skills/${skillId}/review/check`, {
      method: 'POST',
      body: JSON.stringify(body),
      signal,
    });
  },

  async saveSkillDraft(
    skillId: string,
    body: SkillReviewDraft & { revision_token: string }
  ): Promise<Omit<SkillCheckResponse, 'atoms'> & { revision_token: string; saved_at: string }> {
    return request(`/skill-factory/skills/${skillId}/draft`, { method: 'PUT', body: JSON.stringify(body) });
  },

  async discardSkillDraft(skillId: string, revisionToken: string): Promise<{ revision_token: string }> {
    return request(`/skill-factory/skills/${skillId}/draft/discard`, {
      method: 'POST',
      body: JSON.stringify({ revision_token: revisionToken }),
    });
  },

  async submitSkillReview<A extends SkillSubmitAction>(
    skillId: string,
    action: A,
    body: SkillReviewPayloads[A] & { revision_token: string; queue: string[] }
  ): Promise<SkillActionResponse> {
    return request<SkillActionResponse>(`/skill-factory/skills/${skillId}/review/${action}`, {
      method: 'POST',
      body: JSON.stringify(body),
    });
  },

  async compareSkillVersions(skillId: string, fromId?: string, toId?: string): Promise<SkillCompareResponse> {
    const query = new URLSearchParams();
    if (fromId) query.append('from', fromId);
    if (toId) query.append('to', toId);
    const qs = query.toString();
    return request<SkillCompareResponse>(`/skill-factory/skills/${skillId}/compare${qs ? `?${qs}` : ''}`);
  },

  async exportSkillDiff(skillId: string, format: 'markdown' | 'json', fromId?: string, toId?: string): Promise<Blob> {
    const query = new URLSearchParams({ format });
    if (fromId) query.append('from', fromId);
    if (toId) query.append('to', toId);
    return request<Blob>(`/skill-factory/skills/${skillId}/export?${query.toString()}`, { responseType: 'blob' });
  },

  async listSkillExperiences(): Promise<{ items: SkillExperience[]; total: number }> {
    return request<{ items: SkillExperience[]; total: number }>('/skill-factory/experiences');
  },
};
