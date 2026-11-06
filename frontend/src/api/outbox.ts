import api from '@/api'

export interface OutboxDeadLetter {
  id: number
  aggregate_type: string
  aggregate_id: number
  event_kind: string
  status: string
  attempt_count: number
  max_attempts: number
  last_error: string
  created_at: string | null
  updated_at: string | null
}

export interface OutboxDeadLetterPage {
  items: OutboxDeadLetter[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

export function listOutboxDeadLetters(page = 1, pageSize = 20) {
  return api.get('/admin/outbox/dead-letters', {
    params: { page, page_size: pageSize },
  }) as Promise<{ data: OutboxDeadLetterPage }>
}

export function replayOutboxDeadLetter(id: number, reason: string) {
  return api.post(`/admin/outbox/dead-letters/${id}/replay`, { reason })
}
