import { beforeEach, describe, expect, it, vi } from 'vitest'

import apiClient from '../src/api/client'
import {
  generateComprehensiveAnalysis,
  getComprehensiveAnalysis,
  getComprehensiveAnalysisVersion,
} from '../src/api/comprehensiveAnalysis'

vi.mock('../src/api/client', () => ({
  default: {
    post: vi.fn(),
    get: vi.fn(),
  },
}))

describe('comprehensiveAnalysis api', () => {
  beforeEach(() => {
    vi.mocked(apiClient.post).mockReset()
    vi.mocked(apiClient.get).mockReset()
  })

  it('generate posts only idempotency_key', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({
      data: {
        task_id: 't1',
        task_type: 'INTERVIEW_COMPREHENSIVE_ANALYZE',
        status: 'pending',
        application_id: 'app-1',
        dispatch_status: 'queued',
      },
    })
    await generateComprehensiveAnalysis('app-1', { idempotency_key: 'idem-1' })
    expect(apiClient.post).toHaveBeenCalledWith(
      '/applications/app-1/comprehensive-analysis/generate',
      { idempotency_key: 'idem-1' },
    )
    const sent = vi.mocked(apiClient.post).mock.calls[0][1] as Record<string, unknown>
    expect(Object.keys(sent)).toEqual(['idempotency_key'])
  })

  it('get helpers hit set and version paths', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: {
        analysis_id: 'a1',
        application_id: 'app-1',
        current_version_id: 'v1',
        versions: [],
      },
    })
    await getComprehensiveAnalysis('app-1')
    await getComprehensiveAnalysisVersion('app-1', 'v1')
    expect(apiClient.get).toHaveBeenCalledWith(
      '/applications/app-1/comprehensive-analysis',
    )
    expect(apiClient.get).toHaveBeenCalledWith(
      '/applications/app-1/comprehensive-analysis/versions/v1',
    )
  })
})
