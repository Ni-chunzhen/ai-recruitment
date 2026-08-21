import apiClient from './client'

export type HiringDecisionType = 'recommend_hire' | 'reject' | 'hold'

export interface HiringDecisionRequest {
  decision: HiringDecisionType
  reason_code: string
  analysis_version_id: string
  lock_version: number
  idempotency_key?: string
}

export interface HiringDecisionOut {
  id: string
  application_id: string
  decision: HiringDecisionType
  reason_code: string
  round_id: string
  analysis_version_id: string
  overall_score: number | null
  analysis_version_no: number | null
  from_pipeline_status: string
  to_pipeline_status: string
  lock_version: number
  created_at: string
  decided_by: string | null
}

export interface HiringReasonCodeItem {
  code: string
  label: string
  allowed_decisions: string[]
}

export async function createHiringDecision(
  applicationId: string,
  body: HiringDecisionRequest,
): Promise<HiringDecisionOut> {
  const { data } = await apiClient.post<HiringDecisionOut>(
    `/applications/${applicationId}/hiring-decisions`,
    body,
  )
  return data
}

export async function listHiringDecisions(
  applicationId: string,
): Promise<{ items: HiringDecisionOut[] }> {
  const { data } = await apiClient.get<{ items: HiringDecisionOut[] }>(
    `/applications/${applicationId}/hiring-decisions`,
  )
  return data
}

export async function listHiringReasonCodes(): Promise<{ items: HiringReasonCodeItem[] }> {
  const { data } = await apiClient.get<{ items: HiringReasonCodeItem[] }>(
    '/hiring-decision-reason-codes',
  )
  return data
}
