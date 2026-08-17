import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'

import * as interviewsApi from '../src/api/interviews'
import type { InterviewRound, InterviewTimeline } from '../src/api/interviews'
import { useAuthStore } from '../src/stores/auth'
import InterviewTimelineView from '../src/views/InterviewTimelineView.vue'

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
    ElMessageBox: {
      confirm: vi.fn().mockResolvedValue(true),
    },
  }
})

const { ElMessage, ElMessageBox } = await import('element-plus')

const APP_ID = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
const ROUND_DRAFT = '11111111-1111-1111-1111-111111111111'
const ROUND_SCHEDULED = '22222222-2222-2222-2222-222222222222'
const ROUND_PROGRESS = '33333333-3333-3333-3333-333333333333'
const ROUND_PENDING = '44444444-4444-4444-4444-444444444444'
const ROUND_DONE = '55555555-5555-5555-5555-555555555555'
const ROUND_CONFIRMED = '66666666-6666-6666-6666-666666666666'
const ROUND_CANCELLED = '77777777-7777-7777-7777-777777777777'
const ROUND_ABNORMAL = '88888888-8888-8888-8888-888888888888'

function makeUser(permissions: string[]) {
  return {
    id: 'user-hr',
    username: 'hr',
    display_name: 'HR',
    is_active: true,
    must_change_password: false,
    roles: permissions.includes('recruitment.manage')
      ? ['recruiter_admin']
      : ['interviewer'],
    permissions,
  }
}

function makeRound(overrides: Partial<InterviewRound> = {}): InterviewRound {
  return {
    id: ROUND_SCHEDULED,
    application_id: APP_ID,
    job_version_id: 'jv-1',
    name: '第一轮专业面',
    sequence_no: 1,
    status: 'SCHEDULED',
    format: 'ONLINE',
    owner_id: 'owner-1',
    owner_name: '王磊',
    interviewers: [
      { interviewer_id: 'user-hr', display_name: '王磊', is_primary: true },
    ],
    current_schedule: {
      id: 'sch-1',
      schedule_version: 1,
      status: 'ACTIVE',
      start_at_utc: '2026-08-14T02:00:00Z',
      end_at_utc: '2026-08-14T03:00:00Z',
      timezone: 'Asia/Shanghai',
      format: 'ONLINE',
      meeting_mode: 'MANUAL',
      meeting_url: 'https://meet.example.com/abc',
      meeting_no: '123',
      has_meeting_password: false,
      location: null,
      contact_name: null,
      contact_phone_masked: null,
      reschedule_reason: null,
      created_at: '2026-08-14T01:00:00Z',
    },
    schedule_history: [],
    version: 1,
    allowed_actions: [
      'edit',
      'reschedule',
      'cancel',
      'start',
      'generate_invitation',
      'view_invitation',
      'confirm_invitation',
    ],
    created_at: '2026-08-14T00:00:00Z',
    updated_at: '2026-08-14T00:00:00Z',
    ...overrides,
  }
}

function makeTimeline(
  rounds: InterviewRound[] = [makeRound()],
  overrides: Partial<InterviewTimeline> = {},
): InterviewTimeline {
  return {
    application_id: APP_ID,
    candidate_id: 'cand-1',
    candidate_name: '张三',
    job_id: 'job-1',
    job_name: '前端工程师',
    job_version_id: 'jv-1',
    job_version_label: 'V1.0',
    pipeline_status: 'interviewing',
    application_status: 'in_progress',
    completed_round_count: rounds.filter((item) => item.status === 'COMPLETED').length,
    total_round_count: rounds.length,
    rounds,
    ...overrides,
  }
}

const reasonCodes = {
  items: [
    {
      code: 'CANDIDATE_RESCHEDULE',
      label: '候选人改期',
      category: 'cancel',
      requires_description: false,
    },
    {
      code: 'OTHER',
      label: '其他',
      category: 'cancel',
      requires_description: true,
    },
    {
      code: 'CANDIDATE_NO_SHOW',
      label: '候选人未到场',
      category: 'abnormal',
      requires_description: false,
    },
    {
      code: 'OTHER',
      label: '其他',
      category: 'abnormal',
      requires_description: true,
    },
  ],
}

const staff = {
  items: [
    { id: 'owner-1', display_name: '王磊', username: 'wanglei' },
    { id: 'user-2', display_name: '张敏', username: 'zhangmin' },
  ],
}

vi.mock('../src/api/interviews', () => ({
  getInterviewTimeline: vi.fn(),
  createInterviewRound: vi.fn(),
  updateInterviewRound: vi.fn(),
  scheduleInterviewRound: vi.fn(),
  rescheduleInterviewRound: vi.fn(),
  cancelInterviewRound: vi.fn(),
  startInterviewRound: vi.fn(),
  finishInterviewRound: vi.fn(),
  completeInterviewRound: vi.fn(),
  endInterviewAbnormally: vi.fn(),
  reorderInterviewRounds: vi.fn(),
  checkInterviewConflicts: vi.fn(),
  listInterviewReasonCodes: vi.fn(),
  listInterviewStaff: vi.fn(),
  getRoundTranscripts: vi.fn(),
  createTranscriptDraft: vi.fn(),
  getTranscriptReasonCodes: vi.fn(),
  completeWithoutTranscript: vi.fn(),
  previewTranscript: vi.fn(),
  importTranscript: vi.fn(),
}))

vi.mock('../src/api/invitations', () => ({
  confirmInvitation: vi.fn(),
  generateInvitations: vi.fn(),
  listInvitations: vi.fn(),
  getInvitationDetail: vi.fn(),
  updateInvitation: vi.fn(),
  auditInvitationCopy: vi.fn(),
  recordInvitationSent: vi.fn(),
}))

vi.mock('../src/components/InterviewInvitationDrawer.vue', () => ({
  default: {
    name: 'InterviewInvitationDrawer',
    props: ['open', 'round', 'canManage'],
    emits: ['update:open', 'refreshed'],
    template:
      '<div v-if="open" data-test="invitation-drawer-stub">邀约抽屉</div>',
  },
}))

vi.mock('../src/components/interviews/TranscriptImportDrawer.vue', () => ({
  default: {
    name: 'TranscriptImportDrawer',
    props: ['open', 'roundId'],
    emits: ['update:open', 'imported'],
    template:
      '<div v-if="open" data-test="import-drawer-stub">导入抽屉</div>',
  },
}))

const router = createRouter({
  history: createMemoryHistory(),
  routes: [
    {
      path: '/applications/:applicationId/interviews',
      name: 'application-interviews',
      component: InterviewTimelineView,
    },
    {
      path: '/applications/:applicationId/score-report',
      name: 'score-report',
      component: { template: '<div>score</div>' },
    },
    {
      path: '/interview-rounds/:roundId/transcript',
      name: 'interview-transcript',
      component: { template: '<div>transcript</div>' },
    },
  ],
})

const globalStubs = {
  AdminLayout: { template: '<div class="layout"><slot /></div>' },
  'el-button': {
    props: ['disabled', 'loading', 'type', 'plain', 'link'],
    template:
      '<button :disabled="disabled || loading" :data-type="type"><slot /></button>',
  },
  'el-alert': {
    props: ['title', 'type'],
    template: '<div class="el-alert" :data-type="type">{{ title }}<slot /></div>',
  },
  'el-tag': {
    props: ['type'],
    template: '<span class="el-tag"><slot /></span>',
  },
  'el-drawer': {
    props: ['modelValue', 'title'],
    template: '<div v-if="modelValue" class="drawer">{{ title }}<slot /><slot name="footer" /></div>',
  },
  'el-dialog': {
    props: ['modelValue', 'title'],
    template: '<div v-if="modelValue" class="dialog">{{ title }}<slot /><slot name="footer" /></div>',
  },
  'el-form': { template: '<form><slot /></form>' },
  'el-form-item': {
    props: ['label'],
    template: '<div class="form-item">{{ label }}<slot /></div>',
  },
  'el-input': {
    props: ['modelValue', 'type'],
    emits: ['update:modelValue'],
    template:
      '<input :value="modelValue" @input="$emit(\'update:modelValue\', ($event.target).value)" />',
  },
  'el-select': {
    props: ['modelValue', 'multiple'],
    emits: ['update:modelValue'],
    template:
      '<select :value="modelValue" @change="$emit(\'update:modelValue\', ($event.target).value)"><slot /></select>',
  },
  'el-option': {
    props: ['label', 'value'],
    template: '<option :value="value">{{ label }}</option>',
  },
  'el-date-picker': {
    props: ['modelValue'],
    emits: ['update:modelValue'],
    template:
      '<input class="date" :value="modelValue" @input="$emit(\'update:modelValue\', ($event.target).value)" />',
  },
  'el-radio-group': {
    props: ['modelValue'],
    emits: ['update:modelValue'],
    template: '<div class="radio-group"><slot /></div>',
  },
  'el-radio': {
    props: ['value', 'label'],
    template: '<label class="radio">{{ value || label }}<slot /></label>',
  },
  'el-timeline': { template: '<div class="timeline"><slot /></div>' },
  'el-timeline-item': { template: '<div class="timeline-item"><slot /></div>' },
  'el-empty': {
    props: ['description'],
    template: '<div class="el-empty">{{ description }}</div>',
  },
}

async function mountView(permissions: string[]) {
  setActivePinia(createPinia())
  const store = useAuthStore()
  store.user = makeUser(permissions)
  store.initialized = true
  await router.push(`/applications/${APP_ID}/interviews`)
  await router.isReady()
  return mount(InterviewTimelineView, {
    global: {
      plugins: [router],
      stubs: globalStubs,
      directives: { loading: {} },
    },
  })
}

async function mountReady(permissions: string[]) {
  const wrapper = await mountView(permissions)
  await flushPromises()
  return wrapper
}

function fillScheduleForm(wrapper: VueWrapper) {
  wrapper.vm.form.name = '终面'
  wrapper.vm.form.owner_id = 'owner-1'
  wrapper.vm.form.interviewer_ids = ['owner-1']
  wrapper.vm.form.start_at = '2026-08-14T10:00:00'
  wrapper.vm.form.end_at = '2026-08-14T11:00:00'
  wrapper.vm.form.timezone = 'Asia/Shanghai'
  wrapper.vm.form.meeting_url = 'https://meet.example.com/x'
}

describe('InterviewTimelineView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(interviewsApi.getInterviewTimeline).mockResolvedValue(makeTimeline())
    vi.mocked(interviewsApi.listInterviewReasonCodes).mockResolvedValue(reasonCodes)
    vi.mocked(interviewsApi.listInterviewStaff).mockResolvedValue(staff)
    vi.mocked(interviewsApi.getRoundTranscripts).mockResolvedValue({
      transcript: null,
      versions: [],
    })
    vi.mocked(interviewsApi.getTranscriptReasonCodes).mockResolvedValue({
      items: [
        {
          code: 'CONTENT_UNUSABLE',
          label: '内容无法使用',
          requires_description: false,
        },
        {
          code: 'OTHER',
          label: '其他',
          requires_description: true,
        },
      ],
    })
    vi.mocked(interviewsApi.checkInterviewConflicts).mockResolvedValue({
      has_candidate_conflict: false,
      has_interviewer_conflict: false,
      candidate_conflicts: [],
      interviewer_conflicts: [],
    })
    vi.mocked(interviewsApi.createInterviewRound).mockResolvedValue(
      makeRound({ status: 'DRAFT', allowed_actions: ['edit', 'schedule', 'cancel'] }),
    )
    vi.mocked(interviewsApi.startInterviewRound).mockResolvedValue(
      makeRound({ status: 'IN_PROGRESS', allowed_actions: ['finish', 'end_abnormally'] }),
    )
    vi.mocked(interviewsApi.cancelInterviewRound).mockResolvedValue(
      makeRound({ status: 'CANCELLED', allowed_actions: [] }),
    )
  })

  it('renders header business info and sorted timeline', async () => {
    vi.mocked(interviewsApi.getInterviewTimeline).mockResolvedValue(
      makeTimeline([
        makeRound({
          id: ROUND_SCHEDULED,
          name: '第二轮',
          sequence_no: 2,
          status: 'SCHEDULED',
        }),
        makeRound({
          id: ROUND_DRAFT,
          name: '第一轮',
          sequence_no: 1,
          status: 'DRAFT',
          current_schedule: null,
          allowed_actions: ['edit', 'schedule', 'cancel'],
        }),
      ]),
    )
    const wrapper = await mountReady(['recruitment.manage', 'interview.execute'])
    expect(wrapper.text()).toContain('张三')
    expect(wrapper.text()).toContain('前端工程师')
    expect(wrapper.text()).toContain('V1.0')
    expect(wrapper.text()).toContain('面试中')
    expect(wrapper.text()).toContain('新增面试轮次')
    const names = wrapper.findAll('.round-name').map((node) => node.text())
    expect(names[0]).toContain('第一轮')
    expect(names[1]).toContain('第二轮')
  })

  it('shows draft/scheduled/in-progress/pending/readonly actions from backend', async () => {
    vi.mocked(interviewsApi.getInterviewTimeline).mockResolvedValue(
      makeTimeline([
        makeRound({
          id: ROUND_DRAFT,
          name: '草稿轮',
          sequence_no: 1,
          status: 'DRAFT',
          allowed_actions: ['edit', 'schedule', 'cancel'],
          current_schedule: null,
        }),
        makeRound({
          id: ROUND_SCHEDULED,
          name: '已安排',
          sequence_no: 2,
          status: 'SCHEDULED',
          allowed_actions: ['edit', 'reschedule', 'cancel', 'start'],
        }),
        makeRound({
          id: ROUND_PROGRESS,
          name: '进行中',
          sequence_no: 3,
          status: 'IN_PROGRESS',
          allowed_actions: ['finish', 'end_abnormally'],
        }),
        makeRound({
          id: ROUND_PENDING,
          name: '待转写',
          sequence_no: 4,
          status: 'PENDING_TRANSCRIPT',
          allowed_actions: ['complete'],
        }),
        makeRound({
          id: ROUND_DONE,
          name: '已完成',
          sequence_no: 5,
          status: 'COMPLETED',
          allowed_actions: [],
        }),
      ]),
    )
    vi.mocked(interviewsApi.getRoundTranscripts).mockImplementation(async (roundId) => {
      if (roundId === ROUND_PENDING) {
        return { transcript: null, versions: [] }
      }
      if (roundId === ROUND_DONE) {
        return {
          transcript: {
            id: 'tr-done',
            interview_round_id: ROUND_DONE,
            original_version_id: 't1',
            current_draft_version_id: null,
            current_confirmed_version_id: 'c1',
            version: 2,
            created_at: '2026-08-17T00:00:00Z',
            updated_at: '2026-08-17T00:00:00Z',
          },
          versions: [],
        }
      }
      return { transcript: null, versions: [] }
    })
    const wrapper = await mountReady(['recruitment.manage', 'interview.execute'])
    const text = wrapper.text()
    expect(text).toContain('编辑')
    expect(text).toContain('安排')
    expect(text).toContain('改期')
    expect(text).toContain('取消')
    expect(text).toContain('开始')
    expect(text).toContain('结束')
    expect(text).toContain('异常结束')
    expect(text).toContain('导入听记文本')
    expect(text).toContain('无转写完成')
    expect(text).not.toContain('确认完成')
    expect(text).not.toContain('DOCX')
    expect(text).not.toContain('AI评分')
    expect(text).not.toContain('Offer')
    const done = wrapper.find(`[data-round-id="${ROUND_DONE}"]`)
    expect(done.text()).toContain('查看确认版本')
    expect(done.text()).toContain('版本历史')
    expect(done.text()).toContain('再次校对')
    expect(done.text()).not.toContain('编辑')
    expect(wrapper.text()).not.toContain('保存并发送邀约')
  })

  it('shows transcript workflow entry points by import/draft state', async () => {
    vi.mocked(interviewsApi.getInterviewTimeline).mockResolvedValue(
      makeTimeline([
        makeRound({
          id: ROUND_PENDING,
          name: '待转写-已导入',
          sequence_no: 1,
          status: 'PENDING_TRANSCRIPT',
          allowed_actions: ['complete'],
        }),
        makeRound({
          id: '99999999-9999-9999-9999-999999999999',
          name: '待转写-有草稿',
          sequence_no: 2,
          status: 'PENDING_TRANSCRIPT',
          allowed_actions: ['complete'],
        }),
      ]),
    )
    vi.mocked(interviewsApi.getRoundTranscripts).mockImplementation(async (roundId) => {
      if (roundId === ROUND_PENDING) {
        return {
          transcript: {
            id: 'tr-1',
            interview_round_id: ROUND_PENDING,
            original_version_id: 't1',
            current_draft_version_id: null,
            current_confirmed_version_id: null,
            version: 1,
            created_at: '2026-08-17T00:00:00Z',
            updated_at: '2026-08-17T00:00:00Z',
          },
          versions: [],
        }
      }
      return {
        transcript: {
          id: 'tr-2',
          interview_round_id: '99999999-9999-9999-9999-999999999999',
          original_version_id: 't1',
          current_draft_version_id: 'd1',
          current_confirmed_version_id: null,
          version: 1,
          created_at: '2026-08-17T00:00:00Z',
          updated_at: '2026-08-17T00:00:00Z',
        },
        versions: [],
      }
    })
    const wrapper = await mountReady(['recruitment.manage'])
    const imported = wrapper.find(`[data-round-id="${ROUND_PENDING}"]`)
    expect(imported.text()).toContain('开始校对')
    expect(imported.text()).toContain('查看原始版本')
    expect(imported.text()).not.toContain('导入听记文本')
    const drafted = wrapper.find(
      '[data-round-id="99999999-9999-9999-9999-999999999999"]',
    )
    expect(drafted.text()).toContain('继续校对')
    expect(drafted.text()).toContain('确认校对')
  })

  it('shows interviewer confirmed transcript view only', async () => {
    vi.mocked(interviewsApi.getInterviewTimeline).mockResolvedValue(
      makeTimeline([
        makeRound({
          id: ROUND_DONE,
          status: 'COMPLETED',
          allowed_actions: [],
        }),
      ]),
    )
    vi.mocked(interviewsApi.getRoundTranscripts).mockResolvedValue({
      transcript: {
        id: 'tr-1',
        interview_round_id: ROUND_DONE,
        original_version_id: 't1',
        current_draft_version_id: null,
        current_confirmed_version_id: 'c1',
        version: 2,
        created_at: '2026-08-17T00:00:00Z',
        updated_at: '2026-08-17T00:00:00Z',
      },
      versions: [],
    })
    const wrapper = await mountReady(['interview.execute'])
    expect(wrapper.text()).toContain('查看已确认转写')
    expect(wrapper.text()).not.toContain('再次校对')
    expect(wrapper.text()).not.toContain('导入听记文本')
    expect(wrapper.text()).not.toContain('无转写完成')
  })

  it('opens without-transcript dialog using API reason codes and validates OTHER', async () => {
    vi.mocked(interviewsApi.getInterviewTimeline).mockResolvedValue(
      makeTimeline([
        makeRound({
          id: ROUND_PENDING,
          status: 'PENDING_TRANSCRIPT',
          allowed_actions: ['complete'],
        }),
      ]),
    )
    vi.mocked(interviewsApi.completeWithoutTranscript).mockResolvedValue({
      round_id: ROUND_PENDING,
      status: 'COMPLETED',
      version: 2,
      transcript_completion_mode: 'WITHOUT_TRANSCRIPT',
      transcript_completion_reason_code: 'OTHER',
      transcript_completion_reason_description: '无法取得听记',
      transcript_completed_by: 'user-hr',
      transcript_completed_at: '2026-08-17T00:00:00Z',
    })
    const wrapper = await mountReady(['recruitment.manage'])
    await wrapper.get('[data-test="complete-without-transcript"]').trigger('click')
    await flushPromises()
    expect(interviewsApi.getTranscriptReasonCodes).toHaveBeenCalled()
    expect(wrapper.text()).toContain('内容无法使用')
    expect(wrapper.text()).toContain('其他')
    await wrapper.get('[data-test="without-reason-code"]').setValue('OTHER')
    await nextTick()
    await wrapper.get('[data-test="submit-without-transcript"]').trigger('click')
    await flushPromises()
    expect(interviewsApi.completeWithoutTranscript).not.toHaveBeenCalled()
    await wrapper.get('[data-test="without-description"]').setValue('无法取得听记')
    await wrapper.get('[data-test="submit-without-transcript"]').trigger('click')
    await flushPromises()
    expect(interviewsApi.completeWithoutTranscript).toHaveBeenCalledWith(
      ROUND_PENDING,
      expect.objectContaining({
        reason_code: 'OTHER',
        description: '无法取得听记',
      }),
    )
    expect(interviewsApi.getInterviewTimeline).toHaveBeenCalledTimes(2)
  })

  it('hides management actions for interviewer-only allowed_actions', async () => {
    vi.mocked(interviewsApi.getInterviewTimeline).mockResolvedValue(
      makeTimeline([
        makeRound({
          allowed_actions: ['start'],
        }),
      ]),
    )
    const wrapper = await mountReady(['interview.execute'])
    expect(wrapper.text()).toContain('开始')
    expect(wrapper.text()).not.toContain('新增面试轮次')
    expect(wrapper.text()).not.toContain('改期')
  })

  it('shows online meeting fields and offline location fields', async () => {
    const wrapper = await mountView(['recruitment.manage'])
    await nextTick()
    await wrapper.get('[data-test="add-round"]').trigger('click')
    await nextTick()
    expect(wrapper.text()).toContain('会议链接')
    const formatSelect = wrapper.find('[data-test="format-select"]')
    await formatSelect.setValue('OFFLINE')
    await nextTick()
    expect(wrapper.text()).toContain('地点')
    expect(wrapper.text()).toContain('联系人')
    expect(wrapper.text()).toContain('保存草稿')
    expect(wrapper.text()).toContain('保存安排')
    expect(wrapper.text()).not.toContain('保存并发送邀约')
  })

  it('calls conflict check before saving a schedule', async () => {
    const wrapper = await mountReady(['recruitment.manage'])
    await wrapper.get('[data-test="add-round"]').trigger('click')
    await nextTick()
    wrapper.vm.form.name = '终面'
    wrapper.vm.form.owner_id = 'owner-1'
    wrapper.vm.form.interviewer_ids = ['owner-1']
    wrapper.vm.form.start_at = '2026-08-14T10:00:00'
    wrapper.vm.form.end_at = '2026-08-14T11:00:00'
    wrapper.vm.form.timezone = 'Asia/Shanghai'
    wrapper.vm.form.meeting_url = 'https://meet.example.com/x'
    await wrapper.get('[data-test="save-schedule"]').trigger('click')
    await flushPromises()
    expect(interviewsApi.checkInterviewConflicts).toHaveBeenCalled()
    expect(interviewsApi.createInterviewRound).toHaveBeenCalledTimes(1)
    const payload = vi.mocked(interviewsApi.createInterviewRound).mock.calls[0][1]
    expect(payload).toEqual(
      expect.objectContaining({
        name: '终面',
        schedule: expect.objectContaining({
          meeting_url: 'https://meet.example.com/x',
        }),
      }),
    )
    expect(typeof payload.idempotency_key).toBe('string')
    expect(payload.idempotency_key?.length).toBeGreaterThan(0)
  })

  it('requires reschedule reason and shows old/new schedule', async () => {
    const wrapper = await mountReady(['recruitment.manage'])
    await wrapper.get('[data-test="reschedule"]').trigger('click')
    await nextTick()
    expect(wrapper.text()).toContain('原安排')
    expect(wrapper.text()).toContain('新安排')
    expect(wrapper.text()).toContain('改期原因')
  })

  it('loads reason codes from backend and requires OTHER description', async () => {
    const wrapper = await mountReady(['recruitment.manage'])
    await wrapper.get('[data-test="cancel"]').trigger('click')
    await nextTick()
    expect(interviewsApi.listInterviewReasonCodes).toHaveBeenCalled()
    expect(wrapper.text()).toContain('候选人改期')
    wrapper.vm.cancelForm.reason_code = 'OTHER'
    await nextTick()
    expect(wrapper.text()).toContain('说明')
  })

  it('does not fall back to frontend constants when reason codes fail', async () => {
    vi.mocked(interviewsApi.listInterviewReasonCodes).mockRejectedValue(new Error('fail'))
    const wrapper = await mountReady(['recruitment.manage'])
    expect(wrapper.text()).toContain('原因选项加载失败')
    expect(wrapper.text()).not.toContain('CANDIDATE_RESCHEDULE')
  })

  it('refreshes after status actions and shows 409 hint without overwriting', async () => {
    vi.mocked(interviewsApi.startInterviewRound).mockRejectedValue({
      response: { status: 409, data: { detail: '面试信息已被其他人员更新，请刷新后重试' } },
    })
    const wrapper = await mountReady(['recruitment.manage', 'interview.execute'])
    await wrapper.get('[data-test="start"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('刷新后重试')
    expect(ElMessage.error).toHaveBeenCalled()
  })

  it('does not show system-send or AI scoring actions', async () => {
    const wrapper = await mountView(['recruitment.manage'])
    await nextTick()
    expect(wrapper.text()).not.toContain('发送邀约')
    expect(wrapper.text()).not.toContain('系统发送')
    expect(wrapper.text()).not.toContain('送达')
    expect(wrapper.text()).not.toContain('SMTP')
    expect(wrapper.text()).not.toContain('AI 面试评分')
    expect(wrapper.text()).not.toContain('上传转写')
  })

  it('shows scheduled invitation actions from allowed_actions', async () => {
    const wrapper = await mountReady(['recruitment.manage', 'interview.execute'])
    const card = wrapper.get(`[data-round-id="${ROUND_SCHEDULED}"]`)
    expect(card.find('[data-test="generate-invitation"]').exists()).toBe(true)
    expect(card.find('[data-test="view-invitation"]').exists()).toBe(true)
    expect(card.find('[data-test="confirm-invitation"]').exists()).toBe(true)
    expect(card.text()).toContain('生成邀约邮件')
    expect(card.text()).toContain('确认邀约完成')
  })

  it('shows confirmed invitation meta and view entry', async () => {
    vi.mocked(interviewsApi.getInterviewTimeline).mockResolvedValue(
      makeTimeline([
        makeRound({
          id: ROUND_CONFIRMED,
          name: '已确认轮',
          status: 'CONFIRMED',
          allowed_actions: ['edit', 'reschedule', 'cancel', 'start', 'view_invitation'],
          invitation_confirmed_at: '2026-08-14T03:00:00Z',
          invitation_confirmed_by_name: '王磊',
          invitation_confirmed_schedule_version: 1,
        }),
      ]),
    )
    const wrapper = await mountReady(['recruitment.manage', 'interview.execute'])
    const card = wrapper.get(`[data-round-id="${ROUND_CONFIRMED}"]`)
    expect(card.text()).toContain('编辑')
    expect(card.text()).toContain('改期')
    expect(card.text()).toContain('取消')
    expect(card.text()).toContain('开始')
    expect(card.find('[data-test="confirm-invitation"]').exists()).toBe(false)
    expect(card.find('[data-test="view-invitation"]').exists()).toBe(true)
    expect(card.get('[data-test="invitation-confirmed-meta"]').text()).toContain('王磊')
    expect(card.get('[data-test="invitation-confirmed-meta"]').text()).toContain('确认安排版本 1')
  })

  it('shows cancellation invitation entry for cancelled rounds', async () => {
    vi.mocked(interviewsApi.getInterviewTimeline).mockResolvedValue(
      makeTimeline([
        makeRound({
          id: ROUND_CANCELLED,
          name: '已取消',
          status: 'CANCELLED',
          allowed_actions: ['generate_cancellation', 'view_invitation'],
        }),
      ]),
    )
    const wrapper = await mountReady(['recruitment.manage'])
    const card = wrapper.get(`[data-round-id="${ROUND_CANCELLED}"]`)
    expect(card.text()).toContain('生成取消邮件')
    expect(card.text()).toContain('查看取消通知记录')
  })

  it('shows reschedule invite hint after schedule version update', async () => {
    vi.mocked(interviewsApi.getInterviewTimeline).mockResolvedValue(
      makeTimeline([
        makeRound({
          status: 'SCHEDULED',
          current_schedule: {
            id: 'sch-2',
            schedule_version: 2,
            status: 'ACTIVE',
            start_at_utc: '2026-08-15T02:00:00Z',
            end_at_utc: '2026-08-15T03:00:00Z',
            timezone: 'Asia/Shanghai',
            format: 'ONLINE',
            meeting_mode: 'MANUAL',
            meeting_url: 'https://meet.example.com/abc',
            meeting_no: '123',
            has_meeting_password: false,
            location: null,
            contact_name: null,
            contact_phone_masked: null,
            reschedule_reason: '冲突',
            created_at: '2026-08-14T01:00:00Z',
          },
          invitation_confirmed_schedule_version: 1,
        }),
      ]),
    )
    const wrapper = await mountReady(['recruitment.manage'])
    expect(wrapper.get('[data-test="reschedule-invite-hint"]').text()).toContain(
      '原邀约内容已失效',
    )
  })

  it('confirm invitation request carries schedule_version version and idempotency_key', async () => {
    const invitationsApi = await import('../src/api/invitations')
    vi.mocked(invitationsApi.confirmInvitation).mockResolvedValue({
      round_id: ROUND_SCHEDULED,
      status: 'CONFIRMED',
      schedule_version: 1,
      version: 2,
      confirmed_at: '2026-08-14T04:00:00Z',
      confirmed_by_name: 'HR',
    })
    vi.mocked(interviewsApi.getInterviewTimeline)
      .mockResolvedValueOnce(makeTimeline())
      .mockResolvedValueOnce(
        makeTimeline([
          makeRound({
            status: 'CONFIRMED',
            allowed_actions: ['view_invitation'],
            invitation_confirmed_by_name: 'HR',
            invitation_confirmed_schedule_version: 1,
          }),
        ]),
      )
    const wrapper = await mountReady(['recruitment.manage'])
    await wrapper.get('[data-test="confirm-invitation"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-test="confirm-invitation-dialog"]').exists()).toBe(true)
    await wrapper.get('[data-test="submit-confirm-invitation"]').trigger('click')
    await flushPromises()
    expect(invitationsApi.confirmInvitation).toHaveBeenCalledWith(
      ROUND_SCHEDULED,
      expect.objectContaining({
        schedule_version: 1,
        version: 1,
        idempotency_key: expect.stringMatching(/\S+/),
      }),
    )
  })

  it('shows confirmed actions from backend allowed_actions', async () => {
    vi.mocked(interviewsApi.getInterviewTimeline).mockResolvedValue(
      makeTimeline([
        makeRound({
          id: ROUND_CONFIRMED,
          name: '已确认轮',
          status: 'CONFIRMED',
          allowed_actions: ['edit', 'reschedule', 'cancel', 'start', 'view_invitation'],
        }),
      ]),
    )
    const wrapper = await mountReady(['recruitment.manage', 'interview.execute'])
    const card = wrapper.get(`[data-round-id="${ROUND_CONFIRMED}"]`)
    expect(card.text()).toContain('编辑')
    expect(card.text()).toContain('改期')
    expect(card.text()).toContain('取消')
    expect(card.text()).toContain('开始')
    expect(card.find('[data-test="confirm-invitation"]').exists()).toBe(false)
  })
  it('keeps completed cancelled and abnormal rounds read-only', async () => {
    vi.mocked(interviewsApi.getInterviewTimeline).mockResolvedValue(
      makeTimeline([
        makeRound({
          id: ROUND_DONE,
          name: '已完成',
          sequence_no: 1,
          status: 'COMPLETED',
          allowed_actions: [],
        }),
        makeRound({
          id: ROUND_CANCELLED,
          name: '已取消',
          sequence_no: 2,
          status: 'CANCELLED',
          allowed_actions: [],
        }),
        makeRound({
          id: ROUND_ABNORMAL,
          name: '异常结束',
          sequence_no: 3,
          status: 'ENDED_ABNORMALLY',
          allowed_actions: [],
        }),
      ]),
    )
    const wrapper = await mountReady(['recruitment.manage'])
    for (const id of [ROUND_DONE, ROUND_CANCELLED, ROUND_ABNORMAL]) {
      const card = wrapper.get(`[data-round-id="${id}"]`)
      expect(card.find('[data-test="edit"]').exists()).toBe(false)
      expect(card.find('[data-test="reschedule"]').exists()).toBe(false)
      expect(card.find('[data-test="cancel"]').exists()).toBe(false)
      expect(card.find('[data-test="start"]').exists()).toBe(false)
    }
  })

  it('hides write actions when caller has no manage or execute permission', async () => {
    vi.mocked(interviewsApi.getInterviewTimeline).mockResolvedValue(
      makeTimeline([makeRound({ allowed_actions: [] })]),
    )
    const wrapper = await mountReady([])
    expect(wrapper.find('[data-test="add-round"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="edit"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="reschedule"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="start"]').exists()).toBe(false)
  })

  it('rejects invalid time range before calling APIs', async () => {
    const wrapper = await mountReady(['recruitment.manage'])
    await wrapper.get('[data-test="add-round"]').trigger('click')
    await nextTick()
    fillScheduleForm(wrapper)
    wrapper.vm.form.end_at = '2026-08-14T09:00:00'
    await wrapper.get('[data-test="save-schedule"]').trigger('click')
    await flushPromises()
    expect(interviewsApi.checkInterviewConflicts).not.toHaveBeenCalled()
    expect(interviewsApi.createInterviewRound).not.toHaveBeenCalled()
    expect(ElMessage.error).toHaveBeenCalled()
  })

  it('blocks save when candidate conflict is returned', async () => {
    vi.mocked(interviewsApi.checkInterviewConflicts).mockResolvedValue({
      has_candidate_conflict: true,
      has_interviewer_conflict: false,
      candidate_conflicts: [
        {
          round_id: ROUND_SCHEDULED,
          round_name: '冲突轮',
          start_at_utc: '2026-08-14T02:00:00Z',
          end_at_utc: '2026-08-14T03:00:00Z',
        },
      ],
      interviewer_conflicts: [],
    })
    const wrapper = await mountReady(['recruitment.manage'])
    await wrapper.get('[data-test="add-round"]').trigger('click')
    await nextTick()
    fillScheduleForm(wrapper)
    await wrapper.get('[data-test="save-schedule"]').trigger('click')
    await flushPromises()
    expect(interviewsApi.createInterviewRound).not.toHaveBeenCalled()
    expect(ElMessage.error).toHaveBeenCalledWith(expect.stringContaining('候选人时间冲突'))
  })

  it('blocks save when interviewer conflict is returned without override reason', async () => {
    vi.mocked(interviewsApi.checkInterviewConflicts).mockResolvedValue({
      has_candidate_conflict: false,
      has_interviewer_conflict: true,
      candidate_conflicts: [],
      interviewer_conflicts: [
        {
          interviewer_id: 'owner-1',
          interviewer_name: '王磊',
          round_id: ROUND_SCHEDULED,
          round_name: '其他轮',
          start_at_utc: '2026-08-14T02:00:00Z',
          end_at_utc: '2026-08-14T03:00:00Z',
        },
      ],
    })
    const wrapper = await mountReady(['recruitment.manage'])
    await wrapper.get('[data-test="add-round"]').trigger('click')
    await nextTick()
    fillScheduleForm(wrapper)
    await wrapper.get('[data-test="save-schedule"]').trigger('click')
    await flushPromises()
    expect(interviewsApi.createInterviewRound).not.toHaveBeenCalled()
    expect(ElMessage.error).toHaveBeenCalledWith(expect.stringContaining('面试官时间冲突'))
  })

  it('requires override reason before retrying interviewer conflict', async () => {
    vi.mocked(interviewsApi.checkInterviewConflicts)
      .mockResolvedValueOnce({
        has_candidate_conflict: false,
        has_interviewer_conflict: true,
        candidate_conflicts: [],
        interviewer_conflicts: [],
      })
      .mockResolvedValueOnce({
        has_candidate_conflict: false,
        has_interviewer_conflict: false,
        candidate_conflicts: [],
        interviewer_conflicts: [],
      })
    const wrapper = await mountReady(['recruitment.manage'])
    await wrapper.get('[data-test="add-round"]').trigger('click')
    await nextTick()
    fillScheduleForm(wrapper)
    await wrapper.get('[data-test="save-schedule"]').trigger('click')
    await flushPromises()
    expect(interviewsApi.createInterviewRound).not.toHaveBeenCalled()
    wrapper.vm.form.override_reason = '已沟通调班'
    await wrapper.get('[data-test="save-schedule"]').trigger('click')
    await flushPromises()
    expect(interviewsApi.checkInterviewConflicts).toHaveBeenLastCalledWith(
      expect.objectContaining({
        override_interviewer_conflict: true,
        override_reason: '已沟通调班',
      }),
    )
    expect(interviewsApi.createInterviewRound).toHaveBeenCalled()
  })

  it('blocks OTHER cancel without description', async () => {
    const wrapper = await mountReady(['recruitment.manage'])
    await wrapper.get('[data-test="cancel"]').trigger('click')
    await nextTick()
    wrapper.vm.cancelForm.reason_code = 'OTHER'
    wrapper.vm.cancelForm.description = ''
    await nextTick()
    await wrapper.get('[data-test="submit-cancel"]').trigger('click')
    await flushPromises()
    expect(interviewsApi.cancelInterviewRound).not.toHaveBeenCalled()
    expect(ElMessage.error).toHaveBeenCalled()
  })

  it('reloads timeline after a successful status action', async () => {
    const wrapper = await mountReady(['recruitment.manage', 'interview.execute'])
    const initialCalls = vi.mocked(interviewsApi.getInterviewTimeline).mock.calls.length
    await wrapper.get('[data-test="start"]').trigger('click')
    await flushPromises()
    expect(interviewsApi.startInterviewRound).toHaveBeenCalled()
    expect(vi.mocked(interviewsApi.getInterviewTimeline).mock.calls.length).toBeGreaterThan(
      initialCalls,
    )
  })

  it('shows 409 hint and a refresh action', async () => {
    vi.mocked(interviewsApi.startInterviewRound).mockRejectedValue({
      response: { status: 409, data: { detail: '面试信息已被其他人员更新，请刷新后重试' } },
    })
    const wrapper = await mountReady(['recruitment.manage', 'interview.execute'])
    await wrapper.get('[data-test="start"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('刷新后重试')
    expect(wrapper.find('[data-test="refresh-conflict"]').exists()).toBe(true)
  })

  it('shows confirm send_summary field and no delivery wording', async () => {
    const wrapper = await mountReady(['recruitment.manage'])
    await wrapper.get('[data-test="confirm-invitation"]').trigger('click')
    await flushPromises()
    const dialog = wrapper.get('[data-test="confirm-invitation-dialog"]')
    expect(dialog.text()).toContain('发送概况')
    expect(dialog.text()).toContain('不表示邮件送达')
    expect(wrapper.find('[data-test="confirm-send-summary"]').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('系统发送成功')
    expect(wrapper.text()).not.toContain('SMTP')
    expect(wrapper.text()).not.toContain('失败重试')
  })

  it('hides invitation write actions for execute-only interviewer', async () => {
    vi.mocked(interviewsApi.getInterviewTimeline).mockResolvedValue(
      makeTimeline([
        makeRound({
          allowed_actions: ['start', 'view_invitation'],
        }),
      ]),
    )
    const wrapper = await mountReady(['interview.execute'])
    expect(wrapper.find('[data-test="generate-invitation"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="confirm-invitation"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="view-invitation"]').exists()).toBe(true)
  })
})
