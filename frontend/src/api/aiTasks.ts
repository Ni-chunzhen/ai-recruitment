import apiClient from './client'
import type { JobDetail } from './jobs'

export type AITaskType =
  | 'JD_PARSE'
  | 'SCORE_DIMENSION_RECOMMEND'
  | 'RESUME_PARSE'
  | 'RESUME_SCORE'
export type AITaskStatus =
  | 'pending'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'output_invalid'
  | 'cancelled'

export interface AITaskAttempt {
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
  created_at: string
}

export interface AITask {
  id: string
  task_type: AITaskType
  status: AITaskStatus
  business_type: string
  business_id: string
  version_id: string | null
  created_by: string | null
  error_code: string | null
  error_message: string | null
  error_category: 'retryable' | 'non_retryable' | null
  attempt_count: number
  retry_cycle_no: number
  cycle_attempt_count: number
  started_at: string | null
  finished_at: string | null
  created_at: string
  updated_at: string
  attempts: AITaskAttempt[]
}

export async function createAITask(payload: {
  task_type: AITaskType
  business_type?: string
  business_id: string
  input?: Record<string, unknown>
}) {
  const { data } = await apiClient.post<AITask>('/ai-tasks', {
    business_type: 'job',
    ...payload,
  })
  return data
}

export async function getAITask(taskId: string) {
  const { data } = await apiClient.get<AITask>(`/ai-tasks/${taskId}`)
  return data
}

export async function listAITasks(businessType: string, businessId: string) {
  const { data } = await apiClient.get<{ items: AITask[]; total: number }>('/ai-tasks', {
    params: { business_type: businessType, business_id: businessId },
  })
  return data
}

export async function retryAITask(taskId: string) {
  const { data } = await apiClient.post<AITask>(`/ai-tasks/${taskId}/retry`)
  return data
}

export async function cancelAITask(taskId: string, reason?: string) {
  const { data } = await apiClient.post<AITask>(`/ai-tasks/${taskId}/cancel`, {
    reason: reason || undefined,
  })
  return data
}

export async function applyAITask(taskId: string) {
  const { data } = await apiClient.post<JobDetail>(`/ai-tasks/${taskId}/apply`)
  return data
}

export function isTerminalStatus(status: AITaskStatus) {
  return (
    status === 'succeeded' ||
    status === 'failed' ||
    status === 'output_invalid' ||
    status === 'cancelled'
  )
}

export async function pollAITask(
  taskId: string,
  options: { intervalMs?: number; timeoutMs?: number; signal?: AbortSignal } = {},
) {
  const intervalMs = options.intervalMs ?? 1500
  const timeoutMs = options.timeoutMs ?? 120000
  const started = Date.now()

  while (true) {
    if (options.signal?.aborted) {
      throw new DOMException('Aborted', 'AbortError')
    }
    const task = await getAITask(taskId)
    if (isTerminalStatus(task.status)) return task
    if (Date.now() - started > timeoutMs) {
      throw new Error('AI 任务轮询超时')
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs))
  }
}
