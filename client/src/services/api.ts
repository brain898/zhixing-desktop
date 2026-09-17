import {
  User,
  KnowledgeOverview,
  DocumentItem,
  DocumentDetail,
  SourceBlock,
  UploadResult,
  KnowledgeItemsResponse,
  KnowledgeItemDetail,
} from '../types';

const API_BASE_URL = 'http://127.0.0.1:8766/api';

let authToken: string | null = localStorage.getItem('zhixing_token');
let onUnauthorizedCallback: (() => void) | null = null;

export const setAuthToken = (token: string | null) => {
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

async function request<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string>),
  };

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
    const err = await response.json().catch(() => ({ detail: '请求失败' }));
    const error = new Error(err.detail || `请求失败 (${response.status})`);
    (error as any).status = response.status;
    throw error;
  }

  return response.json();
}

export const api = {
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

  async getKnowledgeOverview(): Promise<KnowledgeOverview> {
    return request<KnowledgeOverview>('/knowledge/overview');
  },

  async checkSystemStatus(): Promise<{ status: string }> {
    return request<{ status: string }>('/system/status');
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

    const headers: Record<string, string> = {};
    if (authToken) {
      headers['Authorization'] = `Bearer ${authToken}`;
    }

    const response = await fetch(`${API_BASE_URL}/documents/upload`, {
      method: 'POST',
      headers,
      body: formData,
    });

    if (response.status === 401) {
      setAuthToken(null);
      if (onUnauthorizedCallback) onUnauthorizedCallback();
      throw new Error('登录已失效，请重新登录');
    }

    if (!response.ok) {
      const err = await response.json().catch(() => ({ detail: '上传失败' }));
      throw new Error(err.detail || `上传失败 (${response.status})`);
    }

    return response.json();
  },

  getFileUrl(documentId: string, versionId: string): string {
    return `${API_BASE_URL}/documents/${documentId}/versions/${versionId}/file`;
  },

  async getKnowledgeItems(params?: {
    document_id?: string;
    category?: string;
    review_status?: string;
    search?: string;
  }): Promise<KnowledgeItemsResponse> {
    const query = new URLSearchParams();
    if (params?.document_id) query.append('document_id', params.document_id);
    if (params?.category) query.append('category', params.category);
    if (params?.review_status) query.append('review_status', params.review_status);
    if (params?.search) query.append('search', params.search);
    const qs = query.toString() ? `?${query.toString()}` : '';
    return request<KnowledgeItemsResponse>(`/knowledge/items${qs}`);
  },

  async getKnowledgeItemDetail(itemId: string): Promise<KnowledgeItemDetail> {
    return request<KnowledgeItemDetail>(`/knowledge/items/${itemId}`);
  },

  async saveKnowledgeDraft(
    itemId: string,
    payload: Record<string, any>
  ): Promise<{ message: string; revision_token: string; quality_flags: string[] }> {
    return request<{ message: string; revision_token: string; quality_flags: string[] }>(
      `/knowledge/items/${itemId}/draft`,
      {
        method: 'PUT',
        body: JSON.stringify(payload),
      }
    );
  },

  async confirmKnowledgeItem(
    itemId: string,
    payload: { revision_token: string }
  ): Promise<{ message: string; review_status: string; index_status: string; revision_token: string }> {
    return request<{ message: string; review_status: string; index_status: string; revision_token: string }>(
      `/knowledge/items/${itemId}/confirm`,
      {
        method: 'POST',
        body: JSON.stringify(payload),
      }
    );
  },

  async deleteKnowledgeItem(itemId: string): Promise<{ message: string; id: string }> {
    return request<{ message: string; id: string }>(`/knowledge/items/${itemId}`, {
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

  async triggerExtract(documentId: string, versionId: string): Promise<{ message: string; task_id: string }> {
    return request<{ message: string; task_id: string }>(
      `/documents/${documentId}/versions/${versionId}/extract`,
      {
        method: 'POST',
      }
    );
  },
};
