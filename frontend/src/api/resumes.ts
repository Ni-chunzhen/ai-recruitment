import apiClient from './client'
import type { AITask } from './aiTasks'
import { getAITask, isTerminalStatus } from './aiTasks'

export type ResumeStatus =
  | 'pending_parse'
  | 'parsing'
  | 'pending_review'
  | 'confirmed'
  | 'parse_failed'
  | 'void'

export type PipelineStatus =
  | 'pending_parse'
  | 'pending_hr_screen'
  | 'interviewing'
  | 'pending_offer'
  | 'rejected'
  | 'talent_pool'

export type ScreeningDecisionType =
  | 'enter_interview'
  | 'hold'
  | 'reject'
  | 'talent_pool'

export type ScreeningReasonCode = string

export interface ScreeningReasonCodeItem {
  code: ScreeningReasonCode
  label: string
  allowed_decisions: ScreeningDecisionType[] | string[]
  requires_description: boolean
}

export interface ResumeStructuredContent {
  name: string
  name_pending?: boolean
  phone: string
  email: string
  years_of_experience?: number | null
  education: Array<Record<string, unknown>>
  work_experience: Array<Record<string, unknown>>
  projects: Array<Record<string, unknown>>
  skills: string[]
  standardized_text: string
  field_sources?: Record<string, string>
}

export interface DuplicateCandidateHint {
  id: string
  name: string
  phone?: string | null
  email?: string | null
  match_on: string[]
}

export interface ResumeVersion {
  id: string
  resume_id: string
  candidate_id: string
  kind: string
  version_label: string
  status: ResumeStatus
  original_filename?: string | null
  content_type?: string | null
  file_size?: number | null
  extracted_text?: string | null
  ai_structured?: Record<string, unknown> | null
  draft_content?: ResumeStructuredContent | Record<string, unknown> | null
  confirmed_content?: ResumeStructuredContent | Record<string, unknown> | null
  standardized_text?: string | null
  parse_task_id?: string | null
  confirmed_by?: string | null
  confirmed_at?: string | null
  created_at: string
  updated_at: string
  preview_url?: string | null
  duplicate_hints?: DuplicateCandidateHint[]
}

export interface ResumeUploadItem {
  resume_id: string
  resume_version_id: string
  candidate_id: string
  application_id?: string | null
  parse_task_id?: string | null
  status: ResumeStatus
  original_filename: string
  duplicate_hints: DuplicateCandidateHint[]
}

export interface ResumeListItem {
  resume_id: string
  resume_version_id: string
  candidate_id: string
  candidate_name: string
  phone?: string | null
  email?: string | null
  status: ResumeStatus
  has_application: boolean
  job_names: string[]
  original_filename?: string | null
  updated_at: string
  awaiting_match: boolean
}

export interface ApplicationOut {
  id: string
  candidate_id: string
  candidate_name: string
  job_id: string
  job_name?: string | null
  job_version_id: string
  job_version_label?: string | null
  resume_version_id?: string | null
  pipeline_status: PipelineStatus
  status: string
  lock_version: number
  created_at: string
  updated_at: string
}

export interface DimensionScoreItem {
  name: string
  description: string
  weight: number
  score: number
  weighted_score?: number | null
  evidence: string
  gap: string
  risk: string
}

export interface ScoreReport {
  application_id: string
  result_id: string
  version_label: string
  schema_version?: string
  candidate_id: string
  candidate_name: string
  job_id: string
  job_name: string
  job_version_id: string
  job_version_label: string
  resume_version_id: string
  resume_version_label: string
  total_score: number
  calculated_total_score: number
  model_total_score?: number | null
  score_difference?: number | null
  validation_warnings?: string[]
  recommendation: string
  score_band: string
  summary: string
  information_insufficient: boolean
  dimensions: DimensionScoreItem[]
  risks: string[]
  must_have_check: string[]
  is_current?: boolean
  is_stale: boolean
  requested_by?: string | null
  created_at: string
  ai_disclaimer: string
  lock_version?: number
}

export interface ScreeningDecisionOut {
  id: string
  application_id: string
  decision: ScreeningDecisionType
  reason_code?: ScreeningReasonCode | null
  reason?: string | null
  from_pipeline_status: PipelineStatus
  to_pipeline_status: PipelineStatus
  lock_version: number
  created_at: string
}

export async function uploadResumes(payload: {
  files: File[]
  jobId?: string
  linkCandidateId?: string
}) {
  const form = new FormData()
  for (const file of payload.files) form.append('files', file)
  if (payload.jobId) form.append('job_id', payload.jobId)
  if (payload.linkCandidateId) form.append('link_candidate_id', payload.linkCandidateId)
  const { data } = await apiClient.post<{ items: ResumeUploadItem[] }>('/resumes', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
  return data
}

export async function listResumes(params: {
  keyword?: string
  status?: string
  linked?: boolean | null
  offset?: number
  limit?: number
} = {}) {
  const { data } = await apiClient.get<{ items: ResumeListItem[]; total: number }>('/resumes', {
    params: {
      keyword: params.keyword || undefined,
      status: params.status || undefined,
      linked: params.linked ?? undefined,
      offset: params.offset ?? 0,
      limit: params.limit ?? 50,
    },
  })
  return data
}

export async function listPendingReviews() {
  const { data } = await apiClient.get<{ items: ResumeListItem[]; total: number }>(
    '/workbench/pending-reviews',
  )
  return data
}

export async function getResumeVersion(versionId: string) {
  const { data } = await apiClient.get<ResumeVersion>(`/resume-versions/${versionId}`)
  return data
}

export async function saveResumeDraft(versionId: string, content: ResumeStructuredContent) {
  const { data } = await apiClient.put<ResumeVersion>(`/resume-versions/${versionId}/draft`, {
    content,
  })
  return data
}

export async function confirmResumeVersion(
  versionId: string,
  payload: { content: ResumeStructuredContent; link_candidate_id?: string },
) {
  const { data } = await apiClient.put<ResumeVersion>(
    `/resume-versions/${versionId}/confirmed-content`,
    payload,
  )
  return data
}

export async function reparseResumeVersion(versionId: string, text: string) {
  const form = new FormData()
  form.append('text', text)
  const { data } = await apiClient.post<ResumeVersion>(
    `/resume-versions/${versionId}/reparse`,
    form,
    { headers: { 'Content-Type': 'multipart/form-data' } },
  )
  return data
}

export async function createApplication(payload: {
  candidate_id: string
  job_id: string
  resume_version_id?: string
  idempotency_key?: string
}) {
  const { data } = await apiClient.post<ApplicationOut>('/applications', payload)
  return data
}

export async function getApplication(applicationId: string) {
  const { data } = await apiClient.get<ApplicationOut>(`/applications/${applicationId}`)
  return data
}

export async function listScreeningReasonCodes() {
  const { data } = await apiClient.get<{ items: ScreeningReasonCodeItem[] }>(
    '/screening-reason-codes',
  )
  return data
}

export async function createResumeScoreTask(
  applicationId: string,
  payload: { resume_version_id?: string; idempotency_key?: string } = {},
) {
  const { data } = await apiClient.post<{
    task_id: string
    application_id: string
    status: string
  }>(`/applications/${applicationId}/resume-score-tasks`, payload)
  return data
}

export async function getScoreReport(applicationId: string) {
  const { data } = await apiClient.get<ScoreReport>(
    `/applications/${applicationId}/resume-score-report`,
  )
  return data
}

export async function getScoreHistory(applicationId: string) {
  const { data } = await apiClient.get<{ items: ScoreReport[] }>(
    `/applications/${applicationId}/resume-score-history`,
  )
  return data
}

export async function getScoreHistoryItem(applicationId: string, resultId: string) {
  const { data } = await apiClient.get<ScoreReport>(
    `/applications/${applicationId}/resume-score-history/${resultId}`,
  )
  return data
}

export async function createScreeningDecision(
  applicationId: string,
  payload: {
    decision: ScreeningDecisionType
    reason_code?: ScreeningReasonCode
    reason?: string
    lock_version: number
    idempotency_key?: string
  },
) {
  const { data } = await apiClient.post<ScreeningDecisionOut>(
    `/applications/${applicationId}/screening-decisions`,
    payload,
  )
  return data
}

export async function pollAITaskUntilDone(
  taskId: string,
  options: { intervalMs?: number; timeoutMs?: number; signal?: AbortSignal } = {},
): Promise<AITask> {
  const intervalMs = options.intervalMs ?? 1500
  const timeoutMs = options.timeoutMs ?? 180000
  const started = Date.now()
  while (true) {
    if (options.signal?.aborted) throw new DOMException('Aborted', 'AbortError')
    const task = await getAITask(taskId)
    if (isTerminalStatus(task.status)) {
      return task
    }
    if (Date.now() - started > timeoutMs) throw new Error('AI 任务轮询超时')
    await new Promise((resolve) => setTimeout(resolve, intervalMs))
  }
}

export const resumeStatusLabel: Record<ResumeStatus, string> = {
  pending_parse: '待解析',
  parsing: '解析中',
  pending_review: '待校对',
  confirmed: '已确认',
  parse_failed: '解析失败',
  void: '已作废',
}

export const pipelineStatusLabel: Record<PipelineStatus, string> = {
  pending_parse: '待解析',
  pending_hr_screen: '待HR筛选',
  interviewing: '面试中',
  pending_offer: '录用建议待后续',
  rejected: '已淘汰',
  talent_pool: '人才库',
}
