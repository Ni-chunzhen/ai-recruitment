import apiClient from './client'

export interface FieldStatus {
  configured: boolean
  enabled: boolean
  status: string
  value?: string
}

export interface MailBlock {
  delivery_provider: 'console'
  queue_name: string
  smtp_enabled: false
  note: string
}

export interface IntegrationsSummary {
  dify: Record<string, FieldStatus | boolean> & {
    live_enabled_env?: boolean
  }
  minio: Record<string, FieldStatus>
  mail: MailBlock
  restart_required: boolean
  message_key: string
}

export interface DifyUpdatePayload {
  api_base_url?: string
  api_key?: string
  jd_parse_api_key?: string
  score_dimension_api_key?: string
  jd_parse_workflow_id?: string
  score_dimension_workflow_id?: string
  resume_parse_api_key?: string
  resume_score_api_key?: string
  resume_parse_workflow_id?: string
  resume_score_workflow_id?: string
  interview_question_generate_api_key?: string
  interview_question_generate_workflow_id?: string
  ai_provider?: 'mock' | 'dify'
}

export interface MinioUpdatePayload {
  endpoint?: string
  access_key?: string
  secret_key?: string
  bucket?: string
  secure?: string | boolean
  presign_seconds?: string | number
}

export interface ConnectivityTestResult {
  ok: boolean
  error_code: string | null
  latency_ms: number
}

export async function getIntegrationsSummary() {
  const { data } = await apiClient.get<IntegrationsSummary>('/admin/integrations')
  return data
}

export async function updateDifyIntegrations(payload: DifyUpdatePayload) {
  const { data } = await apiClient.put<IntegrationsSummary>(
    '/admin/integrations/dify',
    payload,
  )
  return data
}

export async function updateMinioIntegrations(payload: MinioUpdatePayload) {
  const { data } = await apiClient.put<IntegrationsSummary>(
    '/admin/integrations/minio',
    payload,
  )
  return data
}

export async function testIntegrationProvider(provider: 'dify' | 'minio') {
  const { data } = await apiClient.post<ConnectivityTestResult>(
    `/admin/integrations/${provider}/test`,
  )
  return data
}
