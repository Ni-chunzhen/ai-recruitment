import apiClient from './client'

export type ApplicationStatus =
  | 'in_progress'
  | 'rejected'
  | 'transferred'
  | 'terminated'
  | 'hired'

export type CloseAction = 'reject' | 'transfer' | 'terminate'

export interface JobApplication {
  id: string
  candidate_id: string
  candidate_name: string
  candidate_phone?: string | null
  candidate_email?: string | null
  job_id: string
  job_version_id: string
  status: ApplicationStatus
  pipeline_status?: string
  resume_version_id?: string | null
  lock_version?: number
  interview_started: boolean
  interview_task_state: string
  close_action?: string | null
  close_reason?: string | null
  transferred_to_job_id?: string | null
  previous_version_id?: string | null
  migration_reason?: string | null
  migrated_at?: string | null
  timeline_events: Array<Record<string, unknown>>
  created_at: string
  updated_at: string
}

export interface ClosePreviewItem {
  application_id: string
  candidate_id: string
  candidate_name: string
  status: ApplicationStatus
  interview_started: boolean
  job_version_id: string
}

export interface ClosePreview {
  can_close: boolean
  in_flight_count: number
  items: ClosePreviewItem[]
}

export async function listJobCandidates(jobId: string, inFlightOnly = false) {
  const { data } = await apiClient.get<{ items: JobApplication[]; total: number }>(
    `/jobs/${jobId}/candidates`,
    { params: { in_flight_only: inFlightOnly } },
  )
  return data
}

export async function createJobCandidate(
  jobId: string,
  payload: {
    name: string
    phone?: string
    email?: string
    interview_started?: boolean
  },
) {
  const { data } = await apiClient.post<JobApplication>(`/jobs/${jobId}/candidates`, payload)
  return data
}

export async function getClosePreview(jobId: string) {
  const { data } = await apiClient.get<ClosePreview>(`/jobs/${jobId}/close-preview`)
  return data
}

export async function resolveCloseApplication(
  jobId: string,
  applicationId: string,
  payload: { action: CloseAction; reason: string; target_job_id?: string },
) {
  const { data } = await apiClient.post<JobApplication>(
    `/jobs/${jobId}/candidates/${applicationId}/resolve-close`,
    payload,
  )
  return data
}

export async function migrateApplicationVersion(
  jobId: string,
  applicationId: string,
  payload: { to_version_id: string; reason?: string },
) {
  const { data } = await apiClient.post<JobApplication>(
    `/jobs/${jobId}/candidates/${applicationId}/migrate-version`,
    payload,
  )
  return data
}
