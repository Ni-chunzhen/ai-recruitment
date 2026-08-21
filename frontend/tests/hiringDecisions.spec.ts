import { beforeEach, describe, expect, it, vi } from 'vitest'

import apiClient from '../src/api/client'
import {
  createHiringDecision,
  listHiringDecisions,
  listHiringReasonCodes,
} from '../src/api/hiringDecisions'

vi.mock('../src/api/client', () => ({
  default: {
    post: vi.fn(),
    get: vi.fn(),
  },
}))

describe('hiringDecisions api', () => {
  beforeEach(() => {
    vi.mocked(apiClient.post).mockReset()
    vi.mocked(apiClient.get).mockReset()
  })

  it('createHiringDecision posts only locked body keys', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({
      data: {
        id: 'd1',
        application_id: 'app-1',
        decision: 'recommend_hire',
        reason_code: 'meets_role_bar',
        round_id: 'r1',
        analysis_version_id: 'v1',
        overall_score: 4,
        analysis_version_no: 1,
        from_pipeline_status: 'interviewing',
        to_pipeline_status: 'pending_offer',
        lock_version: 4,
        created_at: '2026-08-20T00:00:00Z',
        decided_by: 'u1',
      },
    })

    const body = {
      decision: 'recommend_hire' as const,
      reason_code: 'meets_role_bar',
      analysis_version_id: 'v1',
      lock_version: 3,
      idempotency_key: 'idem-1',
    }
    await createHiringDecision('app-1', body)

    expect(apiClient.post).toHaveBeenCalledWith(
      '/applications/app-1/hiring-decisions',
      body,
    )
    const sent = vi.mocked(apiClient.post).mock.calls[0][1] as Record<string, unknown>
    expect(Object.keys(sent).sort()).toEqual(
      [
        'analysis_version_id',
        'decision',
        'idempotency_key',
        'lock_version',
        'reason_code',
      ].sort(),
    )
    expect(sent).not.toHaveProperty('reason')
    expect(sent).not.toHaveProperty('quote')
    expect(sent).not.toHaveProperty('notes')
  })

  it('list helpers hit history and reason-code paths', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: { items: [] } })
    await listHiringDecisions('app-1')
    await listHiringReasonCodes()
    expect(apiClient.get).toHaveBeenCalledWith('/applications/app-1/hiring-decisions')
    expect(apiClient.get).toHaveBeenCalledWith('/hiring-decision-reason-codes')
  })
})
