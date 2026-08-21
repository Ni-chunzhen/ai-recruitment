import apiClient from './client'

export interface OfferCreateRequest {
  idempotency_key?: string
}

export interface OfferUpdateRequest {
  subject: string
  body_html: string
  body_text: string
  lock_version: number
  idempotency_key?: string
}

export interface OfferReadyRequest {
  lock_version: number
  idempotency_key?: string
}

export interface OfferSendRequest {
  offer_version_id: string
  lock_version: number
  idempotency_key: string
}

export interface OfferRetryRequest {
  lock_version: number
  idempotency_key: string
}

export interface OfferVoidRequest {
  lock_version: number
  void_reason_code: string
  idempotency_key?: string
}

export interface OfferSummaryOut {
  id: string
  application_id: string
  status: string
  hiring_decision_id: string
  recipient_email_masked: string | null
  recipient_name: string
  lock_version: number
  version_no: number | null
  content_hash: string | null
  frozen: boolean | null
  created_at: string
  updated_at: string
}

export interface OfferDetailOut extends OfferSummaryOut {
  version_id: string | null
  subject: string
  body_html: string
  body_text: string
  template_code: string | null
  template_version: string | null
}

export interface OfferSendOut {
  offer_id: string
  attempt_id: string
  status: string
  attempt_status: string
  attempt_no: number
  lock_version: number
  version_id: string
  provider: string
}

export interface OfferAttemptOut {
  id: string
  offer_id: string
  offer_version_id: string
  provider: string
  status: string
  attempt_no: number
  error_code: string | null
  error_message_safe: string | null
  started_at: string | null
  finished_at: string | null
  next_retry_at: string | null
  created_at: string
}

export async function createOffer(
  applicationId: string,
  body: OfferCreateRequest = {},
): Promise<OfferSummaryOut> {
  const { data } = await apiClient.post<OfferSummaryOut>(
    `/applications/${applicationId}/offers`,
    body,
  )
  return data
}

export async function listOffers(
  applicationId: string,
): Promise<{ items: OfferSummaryOut[] }> {
  const { data } = await apiClient.get<{ items: OfferSummaryOut[] }>(
    `/applications/${applicationId}/offers`,
  )
  return data
}

export async function getOffer(offerId: string): Promise<OfferDetailOut> {
  const { data } = await apiClient.get<OfferDetailOut>(`/offers/${offerId}`)
  return data
}

export async function updateOfferDraft(
  offerId: string,
  body: OfferUpdateRequest,
): Promise<OfferSummaryOut> {
  const { data } = await apiClient.patch<OfferSummaryOut>(`/offers/${offerId}`, body)
  return data
}

export async function markOfferReady(
  offerId: string,
  body: OfferReadyRequest,
): Promise<OfferSummaryOut> {
  const { data } = await apiClient.post<OfferSummaryOut>(`/offers/${offerId}/ready`, body)
  return data
}

export async function confirmOfferSend(
  offerId: string,
  body: OfferSendRequest,
): Promise<OfferSendOut> {
  const { data } = await apiClient.post<OfferSendOut>(`/offers/${offerId}/send`, body)
  return data
}

export async function retryOfferSend(
  offerId: string,
  body: OfferRetryRequest,
): Promise<OfferSendOut> {
  const { data } = await apiClient.post<OfferSendOut>(`/offers/${offerId}/retry`, body)
  return data
}

export async function voidOffer(
  offerId: string,
  body: OfferVoidRequest,
): Promise<OfferSummaryOut> {
  const { data } = await apiClient.post<OfferSummaryOut>(`/offers/${offerId}/void`, body)
  return data
}

export async function listOfferAttempts(
  offerId: string,
): Promise<{ items: OfferAttemptOut[] }> {
  const { data } = await apiClient.get<{ items: OfferAttemptOut[] }>(
    `/offers/${offerId}/attempts`,
  )
  return data
}
