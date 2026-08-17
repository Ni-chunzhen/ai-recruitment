import apiClient from './client'

export type InvitationEventType = 'INITIAL' | 'RESCHEDULE' | 'CANCELLATION'
export type InvitationAudienceType = 'CANDIDATE' | 'INTERVIEWER'
export type InvitationStatus = 'DRAFT' | 'READY' | 'RECORDED_SENT' | 'VOIDED'
export type InvitationCopyType = 'SUBJECT' | 'HTML_BODY' | 'FULL_TEXT'
export type InvitationChannelType = 'CORPORATE_EMAIL' | 'WORK_EMAIL' | 'OTHER'

export interface InvitationMessageSummary {
  id: string
  interview_round_id: string
  schedule_id: string
  schedule_version: number
  event_type: InvitationEventType | string
  audience_type: InvitationAudienceType | string
  recipient_user_id?: string | null
  recipient_key: string
  recipient_name: string
  recipient_email_masked?: string | null
  status: InvitationStatus | string
  current_version_id?: string | null
  current_version_no?: number | null
  template_code?: string | null
  version: number
  missing_fields: string[]
  created_at: string
  updated_at: string
}

export interface InvitationMessageDetail extends InvitationMessageSummary {
  subject: string
  body_html: string
  body_text?: string | null
  template_code: string
  template_version: string
  content_hash: string
  current_version_no: number
}

export interface InvitationStatusCounts {
  generated: number
  ready: number
  recorded_sent: number
  voided: number
}

export interface InvitationListResponse {
  items: InvitationMessageSummary[]
  counts: InvitationStatusCounts
}

export interface RecordSentResponse {
  id: string
  message_id: string
  message_version_id: string
  sent_at: string
  channel_type: string
  channel_note?: string | null
  recipient_email_masked?: string | null
  status: string
}

export interface ConfirmInvitationResponse {
  round_id: string
  status: string
  schedule_version: number
  version: number
  confirmed_at?: string | null
  confirmed_by_name?: string | null
}

export async function generateInvitations(
  roundId: string,
  payload: { event_type?: InvitationEventType; idempotency_key: string },
) {
  const { data } = await apiClient.post<{ items: InvitationMessageSummary[] }>(
    `/interview-rounds/${roundId}/invitations/generate`,
    payload,
  )
  return data
}

export async function listInvitations(roundId: string) {
  const { data } = await apiClient.get<InvitationListResponse>(
    `/interview-rounds/${roundId}/invitations`,
  )
  return data
}

export async function getInvitationDetail(messageId: string) {
  const { data } = await apiClient.get<InvitationMessageDetail>(
    `/interview-invitations/${messageId}`,
  )
  return data
}

export async function updateInvitation(
  messageId: string,
  payload: {
    version: number
    subject: string
    body_html: string
    body_text?: string | null
  },
) {
  const { data } = await apiClient.put<InvitationMessageDetail>(
    `/interview-invitations/${messageId}`,
    payload,
  )
  return data
}

export async function auditInvitationCopy(
  messageId: string,
  payload: { copy_type: InvitationCopyType },
) {
  const { data } = await apiClient.post<InvitationMessageSummary>(
    `/interview-invitations/${messageId}/copy-audit`,
    payload,
  )
  return data
}

export async function recordInvitationSent(
  messageId: string,
  payload: {
    sent_at: string
    message_version_id: string
    channel_type: InvitationChannelType
    channel_note?: string
    idempotency_key: string
  },
) {
  const { data } = await apiClient.post<RecordSentResponse>(
    `/interview-invitations/${messageId}/record-sent`,
    payload,
  )
  return data
}

export async function confirmInvitation(
  roundId: string,
  payload: {
    schedule_version: number
    version: number
    send_summary?: string | null
    idempotency_key: string
  },
) {
  const { data } = await apiClient.post<ConfirmInvitationResponse>(
    `/interview-rounds/${roundId}/confirm-invitation`,
    payload,
  )
  return data
}
