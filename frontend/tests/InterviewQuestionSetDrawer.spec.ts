import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'

import * as interviewAiApi from '../src/api/interviewAi'
import type { InterviewRound } from '../src/api/interviews'
import InterviewQuestionSetDrawer from '../src/components/interviews/InterviewQuestionSetDrawer.vue'

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

const ROUND_ID = '22222222-2222-2222-2222-222222222222'
const VERSION_ID = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
const SET_ID = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'
const SECRET_QUESTION = 'SECRET_QUESTION_BODY'
const CIPHER = 'enc:v1:not-for-http'

function makeRound(overrides: Partial<InterviewRound> = {}): InterviewRound {
  return {
    id: ROUND_ID,
    application_id: 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
    job_version_id: 'jv-1',
    name: '技术一面',
    sequence_no: 1,
    status: 'SCHEDULED',
    format: 'ONLINE',
    owner_id: 'owner-1',
    owner_name: '王磊',
    interviewers: [],
    current_schedule: null,
    schedule_history: [],
    version: 1,
    allowed_actions: [],
    created_at: '2026-08-14T00:00:00Z',
    updated_at: '2026-08-14T00:00:00Z',
    ...overrides,
  }
}

const versionSummary = {
  id: VERSION_ID,
  question_set_id: SET_ID,
  round_id: ROUND_ID,
  version_no: 1,
  version_label: 'Q1',
  source_type: 'AI_GENERATED',
  job_version_id: 'jv-1',
  resume_version_id: 'rv-1',
  question_count: 1,
  is_current: true,
  created_at: '2026-08-18T01:00:00Z',
  created_by: 'user-hr',
  ai_task_id: 'task-1',
}

const questionSet = {
  id: SET_ID,
  round_id: ROUND_ID,
  status: 'DRAFT',
  current_version_id: VERSION_ID,
  confirmed_by: null,
  confirmed_at: null,
  versions: [versionSummary],
}

const questionDetail = {
  ...versionSummary,
  source_type: 'MANUAL_EDIT',
  items: [
    {
      id: 'item-1',
      dimension_key: 'D001',
      question: SECRET_QUESTION,
      purpose: '考察协作',
      evidence_source: 'JOB_REQUIREMENT',
      resume_evidence: null,
      follow_up_prompts: ['请补充结果'],
      risk_flags: ['细节不足'],
      display_order: 1,
    },
  ],
}

vi.mock('../src/api/interviewAi', () => ({
  generateQuestionSet: vi.fn(),
  getQuestionSet: vi.fn(),
  getQuestionSetVersion: vi.fn(),
  createQuestionSetVersion: vi.fn(),
  confirmQuestionSet: vi.fn(),
}))

const stubs = {
  'el-drawer': {
    props: ['modelValue', 'title'],
    template:
      '<div v-if="modelValue" data-test="question-set-drawer">{{ title }}<slot /><slot name="footer" /></div>',
  },
  'el-dialog': {
    props: ['modelValue', 'title'],
    template:
      '<div v-if="modelValue" class="dialog">{{ title }}<slot /><slot name="footer" /></div>',
  },
  'el-button': {
    props: ['disabled', 'loading', 'type'],
    template: '<button :disabled="disabled || loading"><slot /></button>',
  },
  'el-tag': { template: '<span class="el-tag"><slot /></span>' },
  'el-empty': {
    props: ['description'],
    template: '<div class="el-empty">{{ description }}</div>',
  },
  'el-input': {
    props: ['modelValue', 'type'],
    emits: ['update:modelValue'],
    template:
      '<textarea :value="modelValue" @input="$emit(\'update:modelValue\', ($event.target).value)"></textarea>',
  },
  'el-select': {
    props: ['modelValue'],
    emits: ['update:modelValue'],
    template:
      '<select :value="modelValue" @change="$emit(\'update:modelValue\', ($event.target).value)"><slot /></select>',
  },
  'el-option': {
    props: ['label', 'value'],
    template: '<option :value="value">{{ label }}</option>',
  },
  'el-alert': {
    props: ['title'],
    template: '<div class="el-alert">{{ title }}<slot /></div>',
  },
}

function mountDrawer(
  props: { canManage?: boolean; round?: InterviewRound | null } = {},
) {
  return mount(InterviewQuestionSetDrawer, {
    props: {
      open: true,
      round: props.round ?? makeRound(),
      canManage: props.canManage ?? true,
    },
    global: {
      stubs,
      directives: { loading: {} },
    },
  })
}

describe('InterviewQuestionSetDrawer', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(interviewAiApi.getQuestionSet).mockResolvedValue(questionSet)
    vi.mocked(interviewAiApi.getQuestionSetVersion).mockResolvedValue(questionDetail)
    vi.mocked(interviewAiApi.generateQuestionSet).mockResolvedValue({
      task_id: 'task-1',
      task_type: 'INTERVIEW_QUESTION_GENERATE',
      status: 'pending',
      round_id: ROUND_ID,
      dispatch_status: 'queued',
    })
    vi.mocked(interviewAiApi.createQuestionSetVersion).mockResolvedValue(questionDetail)
    vi.mocked(interviewAiApi.confirmQuestionSet).mockResolvedValue({
      ...questionSet,
      status: 'READY',
      confirmed_by: 'user-hr',
      confirmed_at: '2026-08-18T02:00:00Z',
    })
  })

  it('loads version summaries without question bodies until a version is opened', async () => {
    const wrapper = mountDrawer()
    await flushPromises()
    expect(interviewAiApi.getQuestionSet).toHaveBeenCalledWith(ROUND_ID)
    expect(interviewAiApi.getQuestionSetVersion).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('Q1')
    expect(wrapper.text()).toContain('DRAFT')
    expect(wrapper.text()).not.toContain(SECRET_QUESTION)
    expect(wrapper.text()).not.toContain(CIPHER)
    await wrapper.get(`[data-test="question-version-${VERSION_ID}"]`).trigger('click')
    await flushPromises()
    expect(interviewAiApi.getQuestionSetVersion).toHaveBeenCalledWith(ROUND_ID, VERSION_ID)
    expect(wrapper.text()).toContain(SECRET_QUESTION)
    expect(wrapper.text()).toContain('考察协作')
    expect(wrapper.text()).not.toContain(CIPHER)
    expect(wrapper.text()).not.toContain('_encrypted')
  })

  it('sends idempotency_key on generate and shows queued as in-progress, not success', async () => {
    const wrapper = mountDrawer()
    await flushPromises()
    await wrapper.get('[data-test="generate-question-set"]').trigger('click')
    await flushPromises()
    expect(interviewAiApi.generateQuestionSet).toHaveBeenCalledWith(
      ROUND_ID,
      expect.objectContaining({ idempotency_key: expect.stringMatching(/\S+/) }),
    )
    expect(wrapper.text()).toContain('任务已创建；待投递/处理中')
    expect(ElMessage.success).not.toHaveBeenCalled()
    expect(ElMessage.info).toHaveBeenCalledWith(
      expect.stringContaining('任务已创建；待投递/处理中'),
    )
  })

  it('shows the same in-progress copy for pending_dispatch', async () => {
    vi.mocked(interviewAiApi.generateQuestionSet).mockResolvedValue({
      task_id: 'task-2',
      task_type: 'INTERVIEW_QUESTION_GENERATE',
      status: 'pending',
      round_id: ROUND_ID,
      dispatch_status: 'pending_dispatch',
    })
    const wrapper = mountDrawer()
    await flushPromises()
    await wrapper.get('[data-test="generate-question-set"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('任务已创建；待投递/处理中')
    expect(ElMessage.success).not.toHaveBeenCalled()
  })

  it('hides generate/edit/confirm for execute-only users', async () => {
    const wrapper = mountDrawer({ canManage: false })
    await flushPromises()
    expect(wrapper.find('[data-test="generate-question-set"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="edit-question-set"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="confirm-question-set"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('Q1')
  })

  it('edit and confirm send expected_current_version_id and idempotency_key', async () => {
    const wrapper = mountDrawer()
    await flushPromises()
    await wrapper.get(`[data-test="question-version-${VERSION_ID}"]`).trigger('click')
    await flushPromises()
    await wrapper.get('[data-test="edit-question-set"]').trigger('click')
    await flushPromises()
    await wrapper.get('[data-test="submit-question-edit"]').trigger('click')
    await flushPromises()
    expect(interviewAiApi.createQuestionSetVersion).toHaveBeenCalledWith(
      ROUND_ID,
      expect.objectContaining({
        expected_current_version_id: VERSION_ID,
        idempotency_key: expect.stringMatching(/\S+/),
        questions: expect.arrayContaining([
          expect.objectContaining({
            dimension_key: 'D001',
            question: SECRET_QUESTION,
          }),
        ]),
      }),
    )
    await wrapper.get('[data-test="confirm-question-set"]').trigger('click')
    await flushPromises()
    expect(interviewAiApi.confirmQuestionSet).toHaveBeenCalledWith(
      ROUND_ID,
      expect.objectContaining({
        expected_current_version_id: VERSION_ID,
        idempotency_key: expect.stringMatching(/\S+/),
      }),
    )
  })

  it('shows refresh hint on optimistic lock 409', async () => {
    vi.mocked(interviewAiApi.confirmQuestionSet).mockRejectedValue({
      response: {
        status: 409,
        data: { detail: '面试题纲已被其他人员更新，请刷新后重新编辑' },
      },
    })
    const wrapper = mountDrawer()
    await flushPromises()
    await wrapper.get('[data-test="confirm-question-set"]').trigger('click')
    await flushPromises()
    expect(ElMessage.error).toHaveBeenCalledWith(expect.stringContaining('刷新'))
    expect(wrapper.find('[data-test="refresh-question-set"]').exists()).toBe(true)
  })

  it('shows backend 400/404 detail via existing error hint', async () => {
    vi.mocked(interviewAiApi.generateQuestionSet).mockRejectedValue({
      response: { status: 400, data: { detail: '请先确认候选人简历版本后再生成面试题纲' } },
    })
    const wrapper = mountDrawer()
    await flushPromises()
    await wrapper.get('[data-test="generate-question-set"]').trigger('click')
    await flushPromises()
    expect(ElMessage.error).toHaveBeenCalledWith(
      expect.stringContaining('请先确认候选人简历版本'),
    )
  })

  it('does not add hire/offer/multi-round or raw payload copy', async () => {
    const wrapper = mountDrawer()
    await flushPromises()
    const text = wrapper.text()
    expect(text).not.toContain('录用')
    expect(text).not.toContain('淘汰')
    expect(text).not.toContain('Offer')
    expect(text).not.toContain('多轮综合')
    expect(text).not.toContain('自动决策')
    expect(text).not.toContain('Dify')
    expect(text).not.toContain('SMTP')
    expect(text).not.toContain('raw_request')
    expect(text).not.toContain('sensitive_request')
  })

  it('keeps READY confirm metadata after confirm', async () => {
    vi.mocked(interviewAiApi.getQuestionSet)
      .mockResolvedValueOnce(questionSet)
      .mockResolvedValueOnce({
        ...questionSet,
        status: 'READY',
        confirmed_by: 'user-hr',
        confirmed_at: '2026-08-18T02:00:00Z',
      })
    const wrapper = mountDrawer()
    await flushPromises()
    await wrapper.get('[data-test="confirm-question-set"]').trigger('click')
    await flushPromises()
    await nextTick()
    expect(wrapper.text()).toContain('READY')
    expect(wrapper.text()).toMatch(/确认/)
  })
})
