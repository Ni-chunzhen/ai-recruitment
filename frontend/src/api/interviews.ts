import apiClient from './client'

export type InterviewStatus =
  | 'DRAFT'
  | 'SCHEDULED'
  | 'CONFIRMED'
  | 'IN_PROGRESS'
  | 'PENDING_TRANSCRIPT'
  | 'COMPLETED'
  | 'CANCELLED'
  | 'ENDED_ABNORMALLY'

export type InterviewFormat = 'ONLINE' | 'OFFLINE'
export type InterviewAction =
  | 'edit'
  | 'schedule'
  | 'reschedule'
  | 'cancel'
  | 'start'
  | 'finish'
  | 'complete'
  | 'end_abnormally'

export interface InterviewerAssignment {
  interviewer_id: string
  display_name?: string
  is_primary: boolean
}

export interface InterviewScheduleSummary {
  id: string
  schedule_version: number
  status: string
  start_at_utc: string
  end_at_utc: string
  timezone: string
  format: InterviewFormat | string
  meeting_mode?: string | null
  meeting_provider?: string | null
  meeting_url?: string | null
  meeting_no?: string | null
  has_meeting_password: boolean
  location?: string | null
  contact_name?: string | null
  contact_phone_masked?: string | null
  reschedule_reason?: string | null
  created_at: string
}

export interface InterviewRound {
  id: string
  application_id: string
  job_version_id: string
  name: string
  sequence_no: number
  status: InterviewStatus | string
  format: InterviewFormat | string
  owner_id: string
  owner_name: string
  interviewers: InterviewerAssignment[]
  current_schedule: InterviewScheduleSummary | null
  schedule_history: InterviewScheduleSummary[]
  version: number
  allowed_actions: InterviewAction[] | string[]
  cancellation_reason_code?: string | null
  cancellation_description?: string | null
  abnormal_reason_code?: string | null
  abnormal_description?: string | null
  started_at?: string | null
  finished_at?: string | null
  cancelled_at?: string | null
  created_at: string
  updated_at: string
}

export interface InterviewTimeline {
  application_id: string
  candidate_id: string
  candidate_name: string
  job_id: string
  job_name?: string | null
  job_version_id: string
  job_version_label?: string | null
  pipeline_status: string
  application_status: string
  completed_round_count: number
  total_round_count: number
  rounds: InterviewRound[]
}

export interface InterviewReasonCode {
  code: string
  label: string
  category: string
  requires_description: boolean
}

export interface InterviewStaffItem {
  id: string
  display_name: string
  username: string
}

export interface InterviewConflict {
  has_candidate_conflict: boolean
  has_interviewer_conflict: boolean
  candidate_conflicts: Array<{
    round_id: string
    round_name: string
    start_at_utc: string
    end_at_utc: string
  }>
  interviewer_conflicts: Array<{
    interviewer_id?: string | null
    interviewer_name?: string | null
    round_id: string
    round_name: string
    start_at_utc: string
    end_at_utc: string
  }>
}

export interface InterviewSchedulePayload {
  start_at_utc: string
  end_at_utc: string
  timezone: string
  format: InterviewFormat
  meeting_mode?: string | null
  meeting_url?: string | null
  meeting_no?: string | null
  meeting_password?: string | null
  clear_meeting_password?: boolean
  location?: string | null
  contact_name?: string | null
  contact_phone?: string | null
  version?: number
  idempotency_key?: string
}

export async function getInterviewTimeline(applicationId: string) {
  const { data } = await apiClient.get<InterviewTimeline>(
    `/applications/${applicationId}/interview-rounds`,
  )
  return data
}

export async function createInterviewRound(
  applicationId: string,
  payload: {
    name: string
    sequence_no?: number
    format: InterviewFormat
    owner_id: string
    interviewers: Array<{ interviewer_id: string; is_primary: boolean }>
    schedule?: InterviewSchedulePayload
    idempotency_key?: string
  },
) {
  const { data } = await apiClient.post<InterviewRound>(
    `/applications/${applicationId}/interview-rounds`,
    payload,
  )
  return data
}

export async function updateInterviewRound(
  roundId: string,
  payload: {
    name?: string
    format?: InterviewFormat
    owner_id?: string
    interviewers?: Array<{ interviewer_id: string; is_primary: boolean }>
    version: number
  },
) {
  const { data } = await apiClient.put<InterviewRound>(
    `/interview-rounds/${roundId}`,
    payload,
  )
  return data
}

export async function scheduleInterviewRound(
  roundId: string,
  payload: InterviewSchedulePayload,
) {
  const { data } = await apiClient.post<InterviewRound>(
    `/interview-rounds/${roundId}/schedule`,
    payload,
  )
  return data
}

export async function rescheduleInterviewRound(
  roundId: string,
  payload: InterviewSchedulePayload & {
    reschedule_reason: string
    version: number
    override_interviewer_conflict?: boolean
    override_reason?: string
    clear_meeting_password?: boolean
    idempotency_key?: string
  },
) {
  const { data } = await apiClient.post<InterviewRound>(
    `/interview-rounds/${roundId}/reschedule`,
    payload,
  )
  return data
}

export async function cancelInterviewRound(
  roundId: string,
  payload: {
    reason_code: string
    description?: string
    version: number
    idempotency_key?: string
  },
) {
  const { data } = await apiClient.post<InterviewRound>(
    `/interview-rounds/${roundId}/cancel`,
    payload,
  )
  return data
}

export async function startInterviewRound(
  roundId: string,
  payload: { version: number; idempotency_key?: string },
) {
  const { data } = await apiClient.post<InterviewRound>(
    `/interview-rounds/${roundId}/start`,
    payload,
  )
  return data
}

export async function finishInterviewRound(
  roundId: string,
  payload: { version: number; idempotency_key?: string },
) {
  const { data } = await apiClient.post<InterviewRound>(
    `/interview-rounds/${roundId}/finish`,
    payload,
  )
  return data
}

export async function completeInterviewRound(
  roundId: string,
  payload: { version: number; idempotency_key?: string },
) {
  const { data } = await apiClient.post<InterviewRound>(
    `/interview-rounds/${roundId}/complete`,
    payload,
  )
  return data
}

export async function endInterviewAbnormally(
  roundId: string,
  payload: {
    reason_code: string
    description?: string
    version: number
    idempotency_key?: string
  },
) {
  const { data } = await apiClient.post<InterviewRound>(
    `/interview-rounds/${roundId}/end-abnormally`,
    payload,
  )
  return data
}

export async function reorderInterviewRounds(
  applicationId: string,
  payload: { round_ids: string[] },
) {
  const { data } = await apiClient.post<InterviewTimeline>(
    `/applications/${applicationId}/interview-rounds/reorder`,
    payload,
  )
  return data
}

export async function checkInterviewConflicts(payload: {
  application_id: string
  interviewer_ids: string[]
  start_at_utc: string
  end_at_utc: string
  timezone: string
  exclude_round_id?: string
  override_interviewer_conflict?: boolean
  override_reason?: string
}) {
  const { data } = await apiClient.post<InterviewConflict>(
    '/interview-rounds/conflicts/check',
    payload,
  )
  return data
}

export async function listInterviewReasonCodes() {
  const { data } = await apiClient.get<{ items: InterviewReasonCode[] }>(
    '/interview-reason-codes',
  )
  return data
}

export async function listInterviewStaff() {
  const { data } = await apiClient.get<{ items: InterviewStaffItem[] }>(
    '/interview-staff',
  )
  return data
}
