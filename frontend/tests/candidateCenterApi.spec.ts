import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { beforeEach, describe, expect, it, vi } from 'vitest'

import apiClient from '../src/api/client'

const FORBIDDEN_TYPE_KEYS = [
  'extracted_text',
  'standardized_text',
  'confirmed_content',
  'ai_structured',
  'question_encrypted',
  'raw_output',
  'raw_request',
  'raw_response',
  'result_payload',
  'meeting_password_encrypted',
  'meeting_password',
  'overall_summary',
  'quote',
] as const

function declaresForbiddenField(source: string, key: string): boolean {
  const pattern = new RegExp(`(^|[^a-zA-Z0-9_])${key}(:|\\?)`)
  return pattern.test(source)
}

describe('candidateCenter api client', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('listCandidateCenterApplications defaults assigned true', async () => {
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({
      data: { items: [], total: 0, page: 1, page_size: 20 },
    })
    const { listCandidateCenterApplications } = await import('../src/api/candidateCenter')

    await listCandidateCenterApplications()

    expect(getSpy).toHaveBeenCalledWith('/candidate-center/applications', {
      params: {
        assigned: true,
        page: 1,
        page_size: 20,
      },
    })
  })

  it('listCandidateCenterApplications forwards assigned false', async () => {
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({
      data: { items: [], total: 0, page: 2, page_size: 10 },
    })
    const { listCandidateCenterApplications } = await import('../src/api/candidateCenter')

    await listCandidateCenterApplications({
      assigned: false,
      status: 'in_progress',
      pipeline_status: 'interviewing',
      keyword: 'alice',
      page: 2,
      page_size: 10,
      sort: 'created_at_desc',
    })

    expect(getSpy).toHaveBeenCalledWith('/candidate-center/applications', {
      params: {
        assigned: false,
        status: 'in_progress',
        pipeline_status: 'interviewing',
        keyword: 'alice',
        page: 2,
        page_size: 10,
        sort: 'created_at_desc',
      },
    })
  })

  it('getCandidateCenterApplicationDetail hits nested path', async () => {
    const candidateId = '11111111-1111-1111-1111-111111111111'
    const applicationId = '22222222-2222-2222-2222-222222222222'
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({
      data: {
        application_id: applicationId,
        candidate_id: candidateId,
        name: '张三',
        job_id: '33333333-3333-3333-3333-333333333333',
        job_name: '后端工程师',
        job_code: 'JOB-1',
        job_version_id: '44444444-4444-4444-4444-444444444444',
        status: 'in_progress',
        pipeline_status: 'interviewing',
        interview_started: true,
        rounds: [],
        other_applications: [],
      },
    })
    const { getCandidateCenterApplicationDetail } = await import('../src/api/candidateCenter')

    await getCandidateCenterApplicationDetail(candidateId, applicationId)

    expect(getSpy).toHaveBeenCalledWith(
      `/candidate-center/candidates/${candidateId}/applications/${applicationId}`,
    )
  })

  it('types omit sensitive fields', () => {
    const sourcePath = join(
      dirname(fileURLToPath(import.meta.url)),
      '../src/api/candidateCenter.ts',
    )
    const source = readFileSync(sourcePath, 'utf8')

    for (const key of FORBIDDEN_TYPE_KEYS) {
      expect(declaresForbiddenField(source, key)).toBe(false)
    }
  })
})
