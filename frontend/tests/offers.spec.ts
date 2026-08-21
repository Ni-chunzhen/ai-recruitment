import { beforeEach, describe, expect, it, vi } from 'vitest'

import apiClient from '../src/api/client'
import {
  confirmOfferSend,
  createOffer,
  getOffer,
  listOfferAttempts,
  listOffers,
  markOfferReady,
  retryOfferSend,
  updateOfferDraft,
  voidOffer,
} from '../src/api/offers'

vi.mock('../src/api/client', () => ({
  default: {
    post: vi.fn(),
    get: vi.fn(),
    patch: vi.fn(),
  },
}))

describe('offers api', () => {
  beforeEach(() => {
    vi.mocked(apiClient.post).mockReset()
    vi.mocked(apiClient.get).mockReset()
    vi.mocked(apiClient.patch).mockReset()
  })

  it('create and list hit application-scoped paths', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({ data: { id: 'o1' } })
    vi.mocked(apiClient.get).mockResolvedValue({ data: { items: [] } })
    await createOffer('app-1', { idempotency_key: 'c1' })
    await listOffers('app-1')
    expect(apiClient.post).toHaveBeenCalledWith('/applications/app-1/offers', {
      idempotency_key: 'c1',
    })
    expect(apiClient.get).toHaveBeenCalledWith('/applications/app-1/offers')
  })

  it('send and retry require idempotency and lock fields', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({
      data: { attempt_id: 'a1', status: 'sending' },
    })
    await confirmOfferSend('o1', {
      offer_version_id: 'v1',
      lock_version: 2,
      idempotency_key: 's1',
    })
    await retryOfferSend('o1', { lock_version: 3, idempotency_key: 'r1' })
    expect(apiClient.post).toHaveBeenCalledWith('/offers/o1/send', {
      offer_version_id: 'v1',
      lock_version: 2,
      idempotency_key: 's1',
    })
    expect(apiClient.post).toHaveBeenCalledWith('/offers/o1/retry', {
      lock_version: 3,
      idempotency_key: 'r1',
    })
  })

  it('detail preview and attempts use offer paths without extra body fields', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: { id: 'o1', subject: 's' } })
    vi.mocked(apiClient.patch).mockResolvedValue({ data: { id: 'o1' } })
    vi.mocked(apiClient.post).mockResolvedValue({ data: { id: 'o1' } })
    await getOffer('o1')
    await listOfferAttempts('o1')
    await updateOfferDraft('o1', {
      subject: 's',
      body_html: 'h',
      body_text: 't',
      lock_version: 1,
      idempotency_key: 'u1',
    })
    await markOfferReady('o1', { lock_version: 1, idempotency_key: 'rdy' })
    await voidOffer('o1', {
      lock_version: 1,
      void_reason_code: 'withdrawn',
      idempotency_key: 'v1',
    })
    expect(apiClient.get).toHaveBeenCalledWith('/offers/o1')
    expect(apiClient.get).toHaveBeenCalledWith('/offers/o1/attempts')
    expect(apiClient.patch).toHaveBeenCalledWith('/offers/o1', expect.any(Object))
    const patchBody = vi.mocked(apiClient.patch).mock.calls[0][1] as Record<string, unknown>
    expect(patchBody).not.toHaveProperty('recipient_email')
    expect(patchBody).not.toHaveProperty('smtp')
  })
})
