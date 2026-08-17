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
  | 'generate_invitation'
  | 'view_invitation'
  | 'confirm_invitation'
  | 'generate_cancellation'

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
  invitation_confirmed_at?: string | null
  invitation_confirmed_by?: string | null
  invitation_confirmed_by_name?: string | null
  invitation_confirmed_schedule_version?: number | null
  invitation_confirmation_summary?: string | null
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

export type TranscriptSourceMethod = 'PASTE' | 'TXT' | 'MD'
export type TranscriptSpeakerRole =
  | 'CANDIDATE'
  | 'INTERVIEWER'
  | 'OTHER'
  | 'UNKNOWN'
export type TranscriptVersionType = 'ORIGINAL' | 'DRAFT' | 'CONFIRMED'
export type TranscriptVersionStatus = 'EDITING' | 'IMMUTABLE'
export type TranscriptSegmentSourceType =
  | 'ORIGINAL'
  | 'CORRECTED'
  | 'MANUAL_ADDITION'

export interface TranscriptPreviewSegment {
  segment_no: number
  speaker_key: string
  speaker_name: string
  speaker_role: string
  start_time_ms: number | null
  end_time_ms: number | null
  text: string
  matched_rule: string
}

export interface TranscriptPreview {
  encoding: string
  sha256: string
  char_count: number
  segment_count: number
  matched_rules: string[]
  source_method: string
  filename: string | null
  size: number
  mime: string | null
  segments: TranscriptPreviewSegment[]
}

export interface TranscriptImportSegment {
  speaker_key: string
  speaker_name: string
  speaker_role: TranscriptSpeakerRole | string
  text: string
  start_time_ms?: number | null
  end_time_ms?: number | null
  is_unclear?: boolean
  is_included_in_analysis?: boolean
  source_segment_refs?: number[]
}

export interface TranscriptImportRequest {
  idempotency_key: string
  source_method?: TranscriptSourceMethod | string | null
  raw_text?: string | null
  filename?: string | null
  source_sha256: string
  segments: TranscriptImportSegment[]
}

export interface TranscriptSegment {
  id: string
  segment_no: number
  speaker_key: string
  speaker_name: string
  speaker_role: string
  start_time_ms: number | null
  end_time_ms: number | null
  text: string
  source_type: string
  source_segment_refs: number[]
  is_included_in_analysis: boolean
  is_unclear: boolean
}

export interface TranscriptSummary {
  id: string
  interview_round_id: string
  original_version_id: string | null
  current_draft_version_id: string | null
  current_confirmed_version_id: string | null
  version: number
  created_at: string
  updated_at: string
}

export interface TranscriptVersionSummary {
  id: string
  transcript_id: string
  version_type: string
  version_no: number
  version_label: string
  status: string
  source_method: string
  source_filename: string | null
  source_size: number | null
  source_mime: string | null
  source_encoding: string | null
  based_on_version_id: string | null
  segment_count: number
  confirmed_by: string | null
  confirmed_at: string | null
  version: number
  created_at: string
  updated_at: string
}

export interface TranscriptList {
  transcript: TranscriptSummary | null
  versions: TranscriptVersionSummary[]
}

export interface TranscriptVersionDetail {
  id: string
  transcript_id: string
  interview_round_id: string
  version_type: string
  version_no: number
  version_label: string
  status: string
  source_method: string
  source_filename: string | null
  source_size: number | null
  source_mime: string | null
  source_encoding: string | null
  source_sha256: string
  based_on_version_id: string | null
  confirmed_by: string | null
  confirmed_at: string | null
  version: number
  created_at: string
  updated_at: string
  segments: TranscriptSegment[]
  raw_text: string
}

export interface DraftSegmentPayload {
  speaker_key: string
  speaker_name: string
  speaker_role: TranscriptSpeakerRole | string
  text: string
  start_time_ms?: number | null
  end_time_ms?: number | null
  is_unclear?: boolean
  is_included_in_analysis?: boolean
  source_segment_refs?: number[]
}

export interface TranscriptChangeCounts {
  speaker_changes: number
  text_corrections: number
  merge_split_count: number
  deleted_count: number
  manual_addition_count: number
  excluded_from_analysis_count: number
  reorder_count: number
}

export interface DraftSaveResponse {
  version: TranscriptVersionDetail
  change_counts: TranscriptChangeCounts
}

export interface CompleteWithoutTranscriptRequest {
  reason_code: string
  description?: string | null
  version: number
  idempotency_key: string
}

export interface CompleteWithoutTranscriptResult {
  round_id: string
  status: string
  version: number
  transcript_completion_mode: string
  transcript_completion_reason_code: string
  transcript_completion_reason_description: string | null
  transcript_completed_by: string
  transcript_completed_at: string
}

export interface TranscriptReasonCode {
  code: string
  label: string
  requires_description: boolean
}

export async function previewTranscript(
  roundId: string,
  payload: { text: string } | FormData,
) {
  const body =
    payload instanceof FormData
      ? payload
      : (() => {
          const form = new FormData()
          form.append('text', payload.text)
          return form
        })()
  const { data } = await apiClient.post<TranscriptPreview>(
    `/interview-rounds/${roundId}/transcripts/preview`,
    body,
  )
  return data
}

export async function importTranscript(
  roundId: string,
  body: TranscriptImportRequest,
) {
  const { data } = await apiClient.post<TranscriptVersionDetail>(
    `/interview-rounds/${roundId}/transcripts`,
    body,
  )
  return data
}

export async function getRoundTranscripts(roundId: string) {
  const { data } = await apiClient.get<TranscriptList>(
    `/interview-rounds/${roundId}/transcripts`,
  )
  return data
}

export async function getTranscriptVersion(versionId: string) {
  const { data } = await apiClient.get<TranscriptVersionDetail>(
    `/transcript-versions/${versionId}`,
  )
  return data
}

export async function createTranscriptDraft(
  transcriptId: string,
  body: { idempotency_key: string },
) {
  const { data } = await apiClient.post<TranscriptVersionDetail>(
    `/interview-transcripts/${transcriptId}/draft`,
    body,
  )
  return data
}

export async function saveTranscriptDraft(
  draftId: string,
  body: {
    draft_version_id: string
    version: number
    idempotency_key: string
    segments: DraftSegmentPayload[]
  },
) {
  const { data } = await apiClient.put<DraftSaveResponse>(
    `/transcript-versions/${draftId}/draft`,
    body,
  )
  return data
}

export async function confirmTranscriptDraft(
  draftId: string,
  body: { idempotency_key: string; version: number },
) {
  const { data } = await apiClient.post<TranscriptVersionDetail>(
    `/transcript-versions/${draftId}/confirm`,
    body,
  )
  return data
}

export async function completeWithoutTranscript(
  roundId: string,
  body: CompleteWithoutTranscriptRequest,
) {
  const { data } = await apiClient.post<CompleteWithoutTranscriptResult>(
    `/interview-rounds/${roundId}/complete-without-transcript`,
    body,
  )
  return data
}

export async function getTranscriptReasonCodes() {
  const { data } = await apiClient.get<{ items: TranscriptReasonCode[] }>(
    '/interview-transcript-reason-codes',
  )
  return data
}
