import apiClient from './client'
import type { AITaskStatus, AITaskType } from './aiTasks'

export type { AITaskStatus, AITaskType }

export interface AITaskAdminListItem {
  id: string
  task_type: AITaskType | string
  business_type: string
  business_id: string
  status: AITaskStatus
  attempt_count: number
  retry_cycle_no: number
  cycle_attempt_count: number
  error_category: 'retryable' | 'non_retryable' | null
  error_code: string | null
  error_message: string | null
  created_by: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  duration_ms: number | null
}

export interface AITaskAttemptAdmin {
  id: string
  attempt_no: number
  retry_cycle_no: number
  cycle_attempt_no: number
  status: string
  started_at: string
  finished_at: string | null
  duration_ms: number | null
  http_status: number | null
  error_category: string | null
  error_message: string | null
  provider_run_id: string | null
  request_id: string | null
}

export interface AITaskAdminDetail extends AITaskAdminListItem {
  attempts: AITaskAttemptAdmin[]
}

export interface AITaskAdminListResponse {
  items: AITaskAdminListItem[]
  total: number
  page: number
  page_size: number
}

export interface AdminAITaskListQuery {
  task_type?: string
  status?: string
  business_type?: string
  business_id?: string
  created_from?: string
  created_to?: string
  keyword?: string
  page?: number
  page_size?: number
}

export async function listAdminAITasks(query: AdminAITaskListQuery = {}) {
  const { data } = await apiClient.get<AITaskAdminListResponse>('/admin/ai-tasks', {
    params: query,
  })
  return data
}

export async function getAdminAITask(taskId: string) {
  const { data } = await apiClient.get<AITaskAdminDetail>(`/admin/ai-tasks/${taskId}`)
  return data
}

export async function retryAdminAITask(taskId: string) {
  const { data } = await apiClient.post<AITaskAdminDetail>(`/admin/ai-tasks/${taskId}/retry`)
  return data
}

export async function cancelAdminAITask(taskId: string, reason?: string) {
  const { data } = await apiClient.post<AITaskAdminDetail>(
    `/admin/ai-tasks/${taskId}/cancel`,
    { reason: reason || undefined },
  )
  return data
}
