import type {
  ConversationDetail, Conversation, Dataset, DatasetDetail, Health, Insights, Message, PreviewPage,
} from './types'

// Same-origin "/api": proxied to FastAPI in dev, served by FastAPI in production.
const BASE = (import.meta.env.VITE_API_URL as string | undefined) ?? '/api'

export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${BASE}${path}`, init)
  } catch {
    throw new ApiError('Cannot reach the DataSense backend. Is it running on port 8000?', 0)
  }
  if (!res.ok) {
    let detail = `Request failed (${res.status})`
    try {
      const body = await res.json()
      if (typeof body.detail === 'string') detail = body.detail
    } catch { /* non-JSON error body */ }
    throw new ApiError(detail, res.status)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

const json = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export interface ChatResponse {
  conversation_id: string
  title: string
  user_message: Message
  assistant_message: Message
}

export const api = {
  health: () => request<Health>('/health'),
  listDatasets: () => request<Dataset[]>('/datasets'),
  getDataset: (id: string) => request<DatasetDetail>(`/datasets/${id}`),
  uploadDataset: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<DatasetDetail>('/datasets', { method: 'POST', body: form })
  },
  createSample: () => request<DatasetDetail>('/datasets/sample', { method: 'POST' }),
  deleteDataset: (id: string) => request<void>(`/datasets/${id}`, { method: 'DELETE' }),
  preview: (id: string, p: { page: number; pageSize: number; sortBy?: string; sortDir: string; search?: string }) => {
    const q = new URLSearchParams({ page: String(p.page), page_size: String(p.pageSize), sort_dir: p.sortDir })
    if (p.sortBy) q.set('sort_by', p.sortBy)
    if (p.search) q.set('search', p.search)
    return request<PreviewPage>(`/datasets/${id}/preview?${q}`)
  },
  insights: (id: string, refresh = false) => request<Insights>(`/datasets/${id}/insights${refresh ? '?refresh=true' : ''}`),
  aiSummary: (id: string) => request<{ ai_summary: string }>(`/datasets/${id}/insights/ai-summary`, { method: 'POST' }),
  chat: (body: { dataset_id: string; message: string; conversation_id?: string }) => request<ChatResponse>('/chat', json(body)),
  conversations: (datasetId: string) => request<Conversation[]>(`/datasets/${datasetId}/conversations`),
  conversation: (id: string) => request<ConversationDetail>(`/conversations/${id}`),
  deleteConversation: (id: string) => request<void>(`/conversations/${id}`, { method: 'DELETE' }),
  reportUrl: (datasetId: string, conversationId?: string) =>
    `${BASE}/datasets/${datasetId}/report${conversationId ? `?conversation_id=${conversationId}` : ''}`,
  csvUrl: (messageId: number) => `${BASE}/messages/${messageId}/csv`,
}
