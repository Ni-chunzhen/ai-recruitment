import apiClient from './client'

export type InterviewAIDispatchStatus = 'queued' | 'pending_dispatch'
export type InterviewQuestionSourceType = 'AI_GENERATED' | 'MANUAL_EDIT' | string
export type InterviewQuestionSetStatus = 'DRAFT' | 'READY' | string
export type InterviewQuestionEvidenceSource =
  | 'JOB_REQUIREMENT'
  | 'RESUME_EXPERIENCE'
  | 'GENERAL'

export interface InterviewAIGenerateRequest {
  idempotency_key: string
}

export interface InterviewAIGenerateOut {
  task_id: string
  task_type: string
  status: string
  round_id: string
  dispatch_status: InterviewAIDispatchStatus
}

export interface InterviewQuestionVersionSummary {
  id: string
  question_set_id: string
  round_id: string
  version_no: number
  version_label: string
  source_type: InterviewQuestionSourceType
  job_version_id: string
  resume_version_id: string
  question_count: number
  is_current: boolean
  created_at: string
  created_by: string | null
  ai_task_id: string | null
}

export interface InterviewQuestionSet {
  id: string | null
  round_id: string
  status: InterviewQuestionSetStatus | null
  current_version_id: string | null
  confirmed_by: string | null
  confirmed_at: string | null
  versions: InterviewQuestionVersionSummary[]
}

export interface InterviewQuestionItem {
  id: string
  dimension_key: string
  question: string
  purpose: string
  evidence_source: InterviewQuestionEvidenceSource | string
  resume_evidence: string | null
  follow_up_prompts: string[]
  risk_flags: string[]
  display_order: number
}

export interface InterviewQuestionVersionDetail extends InterviewQuestionVersionSummary {
  items: InterviewQuestionItem[]
}

export interface InterviewQuestionItemWrite {
  dimension_key: string
  question: string
  purpose: string
  evidence_source: InterviewQuestionEvidenceSource
  resume_evidence?: string | null
  follow_up_prompts?: string[]
  risk_flags?: string[]
  display_order: number
}

export interface InterviewQuestionEditRequest {
  idempotency_key: string
  expected_current_version_id: string
  questions: InterviewQuestionItemWrite[]
}

export interface InterviewQuestionConfirmRequest {
  idempotency_key: string
  expected_current_version_id: string
}

export interface InterviewAnalysisVersionSummary {
  analysis_id: string
  version_id: string
  version_no: number
  version_label: string
  transcript_version_id: string
  transcript_version_label?: string | null
  job_version_id: string
  ai_task_id: string
  overall_score: string | number | null
  dimension_count: number
  evidence_count: number
  created_by: string | null
  created_at: string
  is_current: boolean
  is_stale: boolean
}

export interface InterviewAnalysisSet {
  analysis_id: string | null
  round_id: string
  current_version_id: string | null
  versions: InterviewAnalysisVersionSummary[]
}

export interface InterviewAnalysisEvidence {
  id: string
  transcript_segment_id: string
  segment_no: number
  quote: string
}

export interface InterviewAnalysisDimension {
  id: string
  dimension_key: string
  dimension_name: string
  weight: string | number
  score: number | null
  analysis: string
  strengths: string[]
  risks: string[]
  insufficient_information: string | null
  suggested_follow_ups: string[]
  display_order: number
  evidence: InterviewAnalysisEvidence[]
}

export interface InterviewAnalysisVersionDetail extends InterviewAnalysisVersionSummary {
  overall_summary: string
  dimensions: InterviewAnalysisDimension[]
}

export async function generateQuestionSet(
  roundId: string,
  body: InterviewAIGenerateRequest,
) {
  const { data } = await apiClient.post<InterviewAIGenerateOut>(
    `/interview-rounds/${roundId}/question-set/generate`,
    body,
  )
  return data
}

export async function getQuestionSet(roundId: string) {
  const { data } = await apiClient.get<InterviewQuestionSet>(
    `/interview-rounds/${roundId}/question-set`,
  )
  return data
}

export async function getQuestionSetVersion(roundId: string, versionId: string) {
  const { data } = await apiClient.get<InterviewQuestionVersionDetail>(
    `/interview-rounds/${roundId}/question-set/versions/${versionId}`,
  )
  return data
}

export async function createQuestionSetVersion(
  roundId: string,
  body: InterviewQuestionEditRequest,
) {
  const { data } = await apiClient.post<InterviewQuestionVersionDetail>(
    `/interview-rounds/${roundId}/question-set/versions`,
    body,
  )
  return data
}

export async function confirmQuestionSet(
  roundId: string,
  body: InterviewQuestionConfirmRequest,
) {
  const { data } = await apiClient.post<InterviewQuestionSet>(
    `/interview-rounds/${roundId}/question-set/confirm`,
    body,
  )
  return data
}

export async function generateRoundAnalysis(
  roundId: string,
  body: InterviewAIGenerateRequest,
) {
  const { data } = await apiClient.post<InterviewAIGenerateOut>(
    `/interview-rounds/${roundId}/analysis/generate`,
    body,
  )
  return data
}

export async function getRoundAnalysis(roundId: string) {
  const { data } = await apiClient.get<InterviewAnalysisSet>(
    `/interview-rounds/${roundId}/analysis`,
  )
  return data
}

export async function getRoundAnalysisVersion(roundId: string, versionId: string) {
  const { data } = await apiClient.get<InterviewAnalysisVersionDetail>(
    `/interview-rounds/${roundId}/analysis/versions/${versionId}`,
  )
  return data
}
