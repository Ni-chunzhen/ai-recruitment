import apiClient from './client'

export interface ComprehensiveGenerateRequest {
  idempotency_key: string
}

export type ComprehensiveDispatchStatus = 'queued' | 'pending_dispatch'

export interface ComprehensiveGenerateOut {
  task_id: string
  task_type: string
  status: string
  application_id: string
  dispatch_status: ComprehensiveDispatchStatus
}

export interface CoverageGap {
  round_id: string
  sequence_no: number | null
  reason_code: string
  status?: string | null
}

export interface CoverageReport {
  eligible_round_count: number
  total_round_count: number
  included_rounds: Array<{
    round_id: string
    sequence_no: number
    analysis_version_id: string
    overall_score?: number | string | null
  }>
  gaps: CoverageGap[]
  coverage_insufficient: boolean
  single_round_only: boolean
  missing_round_count: number
}

export interface ComprehensiveRoundRefDimension {
  dimension_key: string
  dimension_name?: string
  weight?: number
  score?: number | null
  insufficient_information?: boolean
}

export interface ComprehensiveRoundRef {
  round_id: string
  sequence_no: number
  analysis_version_id: string
  analysis_version_no?: number
  overall_score?: number | string | null
  dimensions?: ComprehensiveRoundRefDimension[]
  evidence_refs?: Array<{
    dimension_key: string
    segment_no: number
    transcript_segment_id: string
  }>
}

export interface ComprehensiveVersionSummary {
  analysis_id: string
  version_id: string
  version_no: number
  version_label: string
  ai_task_id: string
  overall_score?: number | string | null
  coverage_report: CoverageReport | Record<string, unknown>
  created_by: string | null
  created_at: string
  is_current: boolean
  is_stale: boolean
}

export interface ComprehensiveSet {
  analysis_id: string | null
  application_id: string
  current_version_id: string | null
  versions: ComprehensiveVersionSummary[]
}

export interface ComprehensiveVersionDetail extends ComprehensiveVersionSummary {
  overall_summary: string
  round_refs: ComprehensiveRoundRef[]
}

export async function generateComprehensiveAnalysis(
  applicationId: string,
  body: ComprehensiveGenerateRequest,
) {
  const { data } = await apiClient.post<ComprehensiveGenerateOut>(
    `/applications/${applicationId}/comprehensive-analysis/generate`,
    body,
  )
  return data
}

export async function getComprehensiveAnalysis(applicationId: string) {
  const { data } = await apiClient.get<ComprehensiveSet>(
    `/applications/${applicationId}/comprehensive-analysis`,
  )
  return data
}

export async function getComprehensiveAnalysisVersion(
  applicationId: string,
  versionId: string,
) {
  const { data } = await apiClient.get<ComprehensiveVersionDetail>(
    `/applications/${applicationId}/comprehensive-analysis/versions/${versionId}`,
  )
  return data
}
