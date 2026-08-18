import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as interviewAiApi from '../src/api/interviewAi'
import type { InterviewRound } from '../src/api/interviews'
import InterviewAnalysisDrawer from '../src/components/interviews/InterviewAnalysisDrawer.vue'

vi.mock('element-plus', async () => {
  const actual = await vi.importActual<typeof import('element-plus')>('element-plus')
  return {
    ...actual,
    ElMessage: {
      success: vi.fn(),
      error: vi.fn(),
      warning: vi.fn(),
      info: vi.fn(),
    },
  }
})

const { ElMessage } = await import('element-plus')

const ROUND_ID = '55555555-5555-5555-5555-555555555555'
const VERSION_ID = 'cccccccc-cccc-cccc-cccc-cccccccccccc'
const SECRET_ANALYSIS = 'SECRET_ANALYSIS_BODY'
const SECRET_QUOTE = 'SECRET_EVIDENCE_QUOTE'
const CIPHER = 'enc:v1:not-for-http'

function makeRound(overrides: Partial<InterviewRound> = {}): InterviewRound {
  return {
    id: ROUND_ID,
    application_id: 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
    job_version_id: 'jv-1',
    name: '技术一面',
    sequence_no: 1,
    status: 'COMPLETED',
    format: 'ONLINE',
    owner_id: 'owner-1',
    owner_name: '王磊',
    interviewers: [],
    current_schedule: null,
    schedule_history: [],
    version: 3,
    allowed_actions: [],
    created_at: '2026-08-14T00:00:00Z',
    updated_at: '2026-08-17T00:00:00Z',
    ...overrides,
  }
}

const analysisSummary = {
  analysis_id: 'an-1',
  version_id: VERSION_ID,
  version_no: 1,
  version_label: 'A1',
  transcript_version_id: 'tv-1',
  transcript_version_label: 'C1',
  job_version_id: 'jv-1',
  ai_task_id: 'task-a',
  overall_score: '4.60',
  dimension_count: 1,
  evidence_count: 1,
  created_by: 'user-hr',
  created_at: '2026-08-18T03:00:00Z',
  is_current: true,
  is_stale: true,
}

const analysisSet = {
  analysis_id: 'an-1',
  round_id: ROUND_ID,
  current_version_id: VERSION_ID,
  versions: [analysisSummary],
}

const analysisDetail = {
  ...analysisSummary,
  overall_summary: '整体稳定',
  dimensions: [
    {
      id: 'dim-1',
      dimension_key: 'D001',
      dimension_name: '协作',
      weight: '40.00',
      score: 4,
      analysis: SECRET_ANALYSIS,
      strengths: ['目标对齐'],
      risks: ['细节偏少'],
      insufficient_information: '缺少结果数据',
      suggested_follow_ups: ['请补充结果'],
      display_order: 1,
      evidence: [
        {
          id: 'ev-1',
          transcript_segment_id: 'seg-1',
          segment_no: 1,
          quote: SECRET_QUOTE,
        },
      ],
    },
  ],
}

vi.mock('../src/api/interviewAi', () => ({
  generateRoundAnalysis: vi.fn(),
  getRoundAnalysis: vi.fn(),
  getRoundAnalysisVersion: vi.fn(),
}))

const stubs = {
  'el-drawer': {
    props: ['modelValue', 'title'],
    template:
      '<div v-if="modelValue" data-test="analysis-drawer">{{ title }}<slot /></div>',
  },
  'el-button': {
    props: ['disabled', 'loading'],
    template: '<button :disabled="disabled || loading"><slot /></button>',
  },
  'el-tag': { template: '<span class="el-tag"><slot /></span>' },
  'el-empty': {
    props: ['description'],
    template: '<div class="el-empty">{{ description }}</div>',
  },
  'el-alert': {
    props: ['title'],
    template: '<div class="el-alert">{{ title }}<slot /></div>',
  },
}

function mountDrawer(
  props: {
    canManage?: boolean
    hasConfirmedTranscript?: boolean
    round?: InterviewRound | null
  } = {},
) {
  return mount(InterviewAnalysisDrawer, {
    props: {
      open: true,
      round: props.round ?? makeRound(),
      canManage: props.canManage ?? true,
      hasConfirmedTranscript: props.hasConfirmedTranscript ?? true,
    },
    global: {
      stubs,
      directives: { loading: {} },
    },
  })
}

describe('InterviewAnalysisDrawer', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(interviewAiApi.getRoundAnalysis).mockResolvedValue(analysisSet)
    vi.mocked(interviewAiApi.getRoundAnalysisVersion).mockResolvedValue(analysisDetail)
    vi.mocked(interviewAiApi.generateRoundAnalysis).mockResolvedValue({
      task_id: 'task-a',
      task_type: 'INTERVIEW_ROUND_ANALYZE',
      status: 'pending',
      round_id: ROUND_ID,
      dispatch_status: 'queued',
    })
  })

  it('lists metadata without analysis body or quotes until detail is opened', async () => {
    const wrapper = mountDrawer()
    await flushPromises()
    expect(interviewAiApi.getRoundAnalysis).toHaveBeenCalledWith(ROUND_ID)
    expect(interviewAiApi.getRoundAnalysisVersion).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('A1')
    expect(wrapper.text()).toContain('4.60')
    expect(wrapper.text()).toContain('STALE')
    expect(wrapper.text()).not.toContain(SECRET_ANALYSIS)
    expect(wrapper.text()).not.toContain(SECRET_QUOTE)
    await wrapper.get(`[data-test="analysis-version-${VERSION_ID}"]`).trigger('click')
    await flushPromises()
    expect(interviewAiApi.getRoundAnalysisVersion).toHaveBeenCalledWith(ROUND_ID, VERSION_ID)
    expect(wrapper.text()).toContain(SECRET_ANALYSIS)
    expect(wrapper.text()).toContain(SECRET_QUOTE)
    expect(wrapper.text()).toContain('缺少结果数据')
    expect(wrapper.get('[data-test="analysis-stale-hint"]').text()).toContain(
      '转写已更新，此分析基于旧确认转写',
    )
    expect(wrapper.text()).not.toContain(CIPHER)
    expect(wrapper.find('[data-test="delete-analysis"]').exists()).toBe(false)
  })

  it('generate sends idempotency_key and does not fake success', async () => {
    const wrapper = mountDrawer()
    await flushPromises()
    await wrapper.get('[data-test="generate-round-analysis"]').trigger('click')
    await flushPromises()
    expect(interviewAiApi.generateRoundAnalysis).toHaveBeenCalledWith(
      ROUND_ID,
      expect.objectContaining({ idempotency_key: expect.stringMatching(/\S+/) }),
    )
    expect(wrapper.text()).toContain('任务已创建；待投递/处理中')
    expect(ElMessage.success).not.toHaveBeenCalled()
  })

  it('hides generate when backend would not allow it', async () => {
    const wrapper = mountDrawer({
      hasConfirmedTranscript: false,
      round: makeRound({ status: 'COMPLETED' }),
    })
    await flushPromises()
    expect(wrapper.find('[data-test="generate-round-analysis"]').exists()).toBe(false)
  })

  it('hides generate for execute-only users even when a confirmed transcript exists', async () => {
    const wrapper = mountDrawer({ canManage: false, hasConfirmedTranscript: true })
    await flushPromises()
    expect(wrapper.find('[data-test="generate-round-analysis"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('A1')
  })

  it('surfaces WITHOUT_TRANSCRIPT and missing-anchor 400 messages', async () => {
    vi.mocked(interviewAiApi.generateRoundAnalysis).mockRejectedValue({
      response: { status: 400, data: { detail: '该轮无转写，不能生成单轮分析' } },
    })
    const wrapper = mountDrawer()
    await flushPromises()
    await wrapper.get('[data-test="generate-round-analysis"]').trigger('click')
    await flushPromises()
    expect(ElMessage.error).toHaveBeenCalledWith(
      expect.stringContaining('该轮无转写，不能生成单轮分析'),
    )
  })

  it('does not add hire/offer/multi-round or raw payload copy', async () => {
    const wrapper = mountDrawer()
    await flushPromises()
    await wrapper.get(`[data-test="analysis-version-${VERSION_ID}"]`).trigger('click')
    await flushPromises()
    const text = wrapper.text()
    expect(text).not.toContain('录用')
    expect(text).not.toContain('Offer')
    expect(text).not.toContain('多轮综合')
    expect(text).not.toContain('自动决策')
    expect(text).not.toContain('Dify')
    expect(text).not.toContain('SMTP')
    expect(text).not.toContain('raw_response')
  })
})
