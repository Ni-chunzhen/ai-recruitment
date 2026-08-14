import apiClient from './client'

export type JobStatus = 'draft' | 'open' | 'paused' | 'closed'

export interface StructuredJd {
  responsibilities: string[]
  requirements: string[]
  must_have: string[]
  nice_to_have: string[]
  skills: string[]
}

export interface ScoreDimension {
  name: string
  weight: number
  description: string
  anchors: string[]
  custom?: boolean
}

export interface JobVersion {
  id: string
  version_label: string | null
  major: number
  minor: number
  status: string
  upgrade_type: string | null
  change_summary: string | null
  raw_jd_text: string
  structured_jd: StructuredJd
  score_dimensions: ScoreDimension[]
  job_snapshot?: Record<string, unknown> | null
  base_version_id: string | null
  published_at: string | null
  published_by: string | null
  created_at: string
  updated_at: string
  is_current?: boolean
  bound_candidates?: number
}

export interface JobVersionListItem {
  id: string
  version_label: string | null
  major: number
  minor: number
  status: string
  status_label: string
  upgrade_type: string | null
  upgrade_type_label: string | null
  change_summary: string | null
  published_at: string | null
  published_by: string | null
  created_at: string
  updated_at: string
  is_current: boolean
  is_draft: boolean
  bound_candidates: number
}

export interface VersionDiffChange {
  field: string
  label: string
  before: unknown
  after: unknown
}

export interface VersionDiffResponse {
  from_version: JobVersionListItem
  to_version: JobVersionListItem
  changes: VersionDiffChange[]
  has_changes: boolean
}

export interface JobListItem {
  id: string
  code: string
  status: JobStatus
  status_label: string
  name: string
  department: string
  level: string | null
  headcount: number | null
  location: string
  owner_user_id: string | null
  owner_name: string
  urgency: string | null
  current_version_label: string | null
  updated_at: string
  created_at: string
}

export interface JobListResponse {
  items: JobListItem[]
  total: number
  page: number
  page_size: number
}

export interface JobDetail {
  id: string
  code: string
  status: JobStatus
  status_label: string
  name: string
  department: string
  level: string | null
  headcount: number | null
  location: string
  owner_user_id: string | null
  owner_name: string
  urgency: string | null
  source_job_id: string | null
  current_version_id: string | null
  draft_version_id: string | null
  current_version: JobVersion | null
  draft_version: JobVersion | null
  closed_at: string | null
  close_reason: string | null
  pause_reason: string | null
  created_by: string | null
  updated_by: string | null
  created_at: string
  updated_at: string
}

export interface CreateJobPayload {
  name?: string
  department?: string
  level?: string | null
  headcount?: number | null
  location?: string
  owner_name?: string
  urgency?: string | null
  raw_jd_text?: string
  structured_jd?: StructuredJd
  score_dimensions?: ScoreDimension[]
}

export interface SaveDraftPayload extends CreateJobPayload {
  change_summary?: string | null
}

export interface JobListQuery {
  page?: number
  page_size?: number
  code?: string
  name?: string
  keyword?: string
  department?: string
  owner?: string
  status?: JobStatus | ''
}

export function emptyStructuredJd(): StructuredJd {
  return {
    responsibilities: [],
    requirements: [],
    must_have: [],
    nice_to_have: [],
    skills: [],
  }
}

export async function listJobs(params: JobListQuery = {}) {
  const { data } = await apiClient.get<JobListResponse>('/jobs', { params })
  return data
}

export async function getJob(jobId: string) {
  const { data } = await apiClient.get<JobDetail>(`/jobs/${jobId}`)
  return data
}

export async function createJob(payload: CreateJobPayload) {
  const { data } = await apiClient.post<JobDetail>('/jobs', payload)
  return data
}

export async function saveJobDraft(jobId: string, payload: SaveDraftPayload) {
  const { data } = await apiClient.patch<JobDetail>(`/jobs/${jobId}/draft`, payload)
  return data
}

export async function publishJob(
  jobId: string,
  payload: { reason?: string; force_major?: boolean; change_summary?: string } = {},
) {
  const { data } = await apiClient.post<JobDetail>(`/jobs/${jobId}/publish`, payload)
  return data
}

export async function pauseJob(jobId: string, reason: string) {
  const { data } = await apiClient.post<JobDetail>(`/jobs/${jobId}/pause`, { reason })
  return data
}

export async function resumeJob(jobId: string, reason: string) {
  const { data } = await apiClient.post<JobDetail>(`/jobs/${jobId}/resume`, { reason })
  return data
}

export async function closeJob(jobId: string, reason: string) {
  const { data } = await apiClient.post<JobDetail>(`/jobs/${jobId}/close`, { reason })
  return data
}

export async function copyJob(jobId: string) {
  const { data } = await apiClient.post<JobDetail>(`/jobs/${jobId}/copy`)
  return data
}

export async function deleteJob(jobId: string) {
  await apiClient.delete(`/jobs/${jobId}`)
}

export async function listJobVersions(jobId: string) {
  const { data } = await apiClient.get<{ items: JobVersionListItem[]; total: number }>(
    `/jobs/${jobId}/versions`,
  )
  return data
}

export async function getJobVersion(jobId: string, versionId: string) {
  const { data } = await apiClient.get<JobVersion>(`/jobs/${jobId}/versions/${versionId}`)
  return data
}

export async function diffJobVersions(
  jobId: string,
  fromVersionId: string,
  toVersionId: string,
) {
  const { data } = await apiClient.get<VersionDiffResponse>(`/jobs/${jobId}/versions/diff`, {
    params: { from: fromVersionId, to: toVersionId },
  })
  return data
}

export async function copyVersionToDraft(jobId: string, versionId: string) {
  const { data } = await apiClient.post<JobDetail>(
    `/jobs/${jobId}/versions/${versionId}/copy-to-draft`,
  )
  return data
}
