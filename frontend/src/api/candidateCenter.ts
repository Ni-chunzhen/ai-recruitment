import apiClient from './client'

export type CandidateCenterSort = 'updated_at_desc' | 'created_at_desc'

export interface CandidateCenterListQuery {
  assigned?: boolean
  status?: string
  pipeline_status?: string
  job_id?: string
  keyword?: string
  page?: number
  page_size?: number
  sort?: CandidateCenterSort
}

export interface CandidateCenterListItem {
  application_id: string
  candidate_id: string
  name: string
  phone?: string | null
  email?: string | null
  job_id: string
  job_name: string
  job_code: string
  job_version_id: string
  job_version_label?: string | null
  status: string
  pipeline_status: string
  round_id?: string | null
  round_name?: string | null
  sequence_no?: number | null
  round_status?: string | null
  schedule_status: string
  invitation_status: string
  transcript_status: string
  question_status: string
  analysis_status: string
  analysis_overall_score?: number | string | null
}

export interface CandidateCenterListResponse {
  items: CandidateCenterListItem[]
  total: number
  page: number
  page_size: number
}

export interface ScoreDimensionSummary {
  name: string
  weight: number
  score: number
}

export interface ScoreSummary {
  result_id: string
  version_label: string
  total_score: number
  calculated_total_score: number
  score_band: string
  recommendation: string
  summary: string
  information_insufficient: boolean
  is_stale: boolean
  is_current: boolean
  dimensions: ScoreDimensionSummary[]
}

export interface ResumeSummary {
  resume_id: string
  resume_version_id: string
  version_label: string
  kind: string
  status: string
  original_filename?: string | null
  confirmed_at?: string | null
}

export interface CandidateCenterRound {
  application_id: string
  round_id: string
  name: string
  sequence_no: number
  status: string
  schedule_status: string
  has_meeting_password: boolean
  invitation_status: string
  transcript_status: string
  question_status: string
  analysis_status: string
  analysis_overall_score?: number | string | null
}

export interface OtherApplicationSummary {
  application_id: string
  job_id: string
  job_name: string
  job_code: string
  status: string
  pipeline_status: string
  created_at: string
}

export interface CandidateCenterDetail {
  application_id: string
  candidate_id: string
  name: string
  phone?: string | null
  email?: string | null
  job_id: string
  job_name: string
  job_code: string
  job_version_id: string
  job_version_label?: string | null
  status: string
  pipeline_status: string
  close_action?: string | null
  interview_started: boolean
  resume_summary?: ResumeSummary | null
  score_summary?: ScoreSummary | null
  rounds: CandidateCenterRound[]
  other_applications: OtherApplicationSummary[]
}

const DEFAULT_LIST_QUERY = {
  assigned: true,
  page: 1,
  page_size: 20,
} as const

export async function listCandidateCenterApplications(
  params: CandidateCenterListQuery = {},
) {
  const { data } = await apiClient.get<CandidateCenterListResponse>(
    '/candidate-center/applications',
    {
      params: {
        ...DEFAULT_LIST_QUERY,
        ...params,
      },
    },
  )
  return data
}

export async function getCandidateCenterApplicationDetail(
  candidateId: string,
  applicationId: string,
) {
  const { data } = await apiClient.get<CandidateCenterDetail>(
    `/candidate-center/candidates/${candidateId}/applications/${applicationId}`,
  )
  return data
}
