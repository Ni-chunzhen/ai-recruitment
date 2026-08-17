import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as invitationsApi from '../src/api/invitations'
import type { InterviewRound } from '../src/api/interviews'
import InterviewInvitationDrawer from '../src/components/InterviewInvitationDrawer.vue'

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

vi.mock('../src/api/invitations', () => ({
  generateInvitations: vi.fn(),
  listInvitations: vi.fn(),
  getInvitationDetail: vi.fn(),
  updateInvitation: vi.fn(),
  auditInvitationCopy: vi.fn(),
  recordInvitationSent: vi.fn(),
}))

const round: InterviewRound = {
  id: '22222222-2222-2222-2222-222222222222',
  application_id: 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
  job_version_id: 'jv-1',
  name: '技术一面',
  sequence_no: 1,
  status: 'SCHEDULED',
  format: 'ONLINE',
  owner_id: 'owner-1',
  owner_name: '王磊',
  interviewers: [
    { interviewer_id: 'u1', display_name: '王面试', is_primary: true },
    { interviewer_id: 'u2', display_name: '赵面试', is_primary: false },
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
    has_meeting_password: true,
    location: null,
    contact_name: null,
    contact_phone_masked: null,
    reschedule_reason: null,
    created_at: '2026-08-14T01:00:00Z',
  },
  schedule_history: [],
  version: 1,
  allowed_actions: ['generate_invitation', 'view_invitation', 'confirm_invitation'],
  created_at: '2026-08-14T00:00:00Z',
  updated_at: '2026-08-14T00:00:00Z',
}

const summaries = [
  {
    id: 'msg-c',
    interview_round_id: round.id,
    schedule_id: 'sch-1',
    schedule_version: 1,
    event_type: 'INITIAL',
    audience_type: 'CANDIDATE',
    recipient_key: 'cand',
    recipient_name: '张三',
    recipient_email_masked: 'z***@example.com',
    status: 'READY',
    current_version_id: 'ver-1',
    current_version_no: 1,
    template_code: 'candidate_initial',
    version: 1,
    missing_fields: [],
    created_at: '2026-08-14T01:00:00Z',
    updated_at: '2026-08-14T01:00:00Z',
  },
  {
    id: 'msg-i1',
    interview_round_id: round.id,
    schedule_id: 'sch-1',
    schedule_version: 1,
    event_type: 'INITIAL',
    audience_type: 'INTERVIEWER',
    recipient_user_id: 'u1',
    recipient_key: 'u1',
    recipient_name: '王面试',
    recipient_email_masked: 'w***@example.com',
    status: 'DRAFT',
    current_version_id: 'ver-2',
    current_version_no: 1,
    template_code: 'interviewer_initial',
    version: 1,
    missing_fields: ['recipient_email'],
    created_at: '2026-08-14T01:00:00Z',
    updated_at: '2026-08-14T01:00:00Z',
  },
  {
    id: 'msg-i2',
    interview_round_id: round.id,
    schedule_id: 'sch-1',
    schedule_version: 1,
    event_type: 'INITIAL',
    audience_type: 'INTERVIEWER',
    recipient_user_id: 'u2',
    recipient_key: 'u2',
    recipient_name: '赵面试',
    recipient_email_masked: 'z***@corp.com',
    status: 'RECORDED_SENT',
    current_version_id: 'ver-3',
    current_version_no: 1,
    template_code: 'interviewer_initial',
    version: 1,
    missing_fields: [],
    created_at: '2026-08-14T01:00:00Z',
    updated_at: '2026-08-14T01:00:00Z',
  },
]

const detail = {
  ...summaries[0],
  subject: '【面试邀请】后端工程师 - 技术一面',
  body_html: '<p>您好</p>',
  body_text: '您好',
  template_code: 'candidate_initial',
  template_version: '1',
  content_hash: 'abc',
  current_version_no: 1,
}

const stubs = {
  'el-drawer': {
    props: ['modelValue', 'title'],
    template:
      '<div v-if="modelValue" data-test="invitation-drawer">{{ title }}<slot /></div>',
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
  'el-input': {
    props: ['modelValue', 'type', 'disabled'],
    inheritAttrs: false,
    emits: ['update:modelValue'],
    template:
      '<textarea v-if="type===\'textarea\'" v-bind="$attrs" :value="modelValue" :disabled="disabled" @input="$emit(\'update:modelValue\', ($event.target).value)" /><input v-else v-bind="$attrs" :value="modelValue" :disabled="disabled" @input="$emit(\'update:modelValue\', ($event.target).value)" />',
  },
  'el-select': {
    props: ['modelValue'],
    emits: ['update:modelValue'],
    template: '<select :value="modelValue" @change="$emit(\'update:modelValue\', ($event.target).value)"><slot /></select>',
  },
  'el-option': {
    props: ['label', 'value'],
    template: '<option :value="value">{{ label }}</option>',
  },
  'el-tag': { template: '<span class="tag"><slot /></span>' },
  'el-empty': { props: ['description'], template: '<div>{{ description }}</div>' },
}

describe('InterviewInvitationDrawer', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    Object.assign(navigator, {
      clipboard: { writeText: vi.fn().mockResolvedValue(undefined) },
    })
    vi.mocked(invitationsApi.listInvitations).mockResolvedValue({
      items: summaries,
      counts: { generated: 1, ready: 1, recorded_sent: 1, voided: 0 },
    })
    vi.mocked(invitationsApi.getInvitationDetail).mockResolvedValue(detail)
    vi.mocked(invitationsApi.generateInvitations).mockResolvedValue({ items: summaries })
    vi.mocked(invitationsApi.updateInvitation).mockResolvedValue({
      ...detail,
      version: 2,
      current_version_no: 2,
      subject: '新主题',
      body_html: '<p>新正文</p>',
      body_text: '新正文',
    })
    vi.mocked(invitationsApi.auditInvitationCopy).mockResolvedValue(summaries[0])
    vi.mocked(invitationsApi.recordInvitationSent).mockResolvedValue({
      id: 'rec-1',
      message_id: 'msg-c',
      message_version_id: 'ver-1',
      sent_at: '2026-08-14T05:00:00Z',
      channel_type: 'CORPORATE_EMAIL',
      status: 'RECORDED_SENT',
    })
  })

  it('lists candidate and interviewers with status tags', async () => {
    const wrapper = mount(InterviewInvitationDrawer, {
      props: { open: true, round, canManage: true },
      global: { stubs, directives: { loading: {} } },
    })
    await flushPromises()
    expect(wrapper.get('[data-test="recipient-list"]').text()).toContain('张三')
    expect(wrapper.get('[data-test="recipient-list"]').text()).toContain('王面试')
    expect(wrapper.get('[data-test="recipient-list"]').text()).toContain('赵面试')
    expect(wrapper.text()).toContain('草稿')
    expect(wrapper.text()).toContain('可复制')
    expect(wrapper.text()).toContain('人工登记已发送')
    expect(wrapper.text()).not.toContain('系统发送')
    expect(wrapper.text()).not.toContain('送达成功')
  })

  it('supports rich text edit save and three copy actions', async () => {
    const wrapper = mount(InterviewInvitationDrawer, {
      props: { open: true, round, canManage: true },
      global: { stubs, directives: { loading: {} } },
    })
    await flushPromises()
    expect(wrapper.get('[data-test="invitation-subject"]').element).toBeTruthy()
    expect(wrapper.get('[data-test="invitation-body-html"]').element).toBeTruthy()
    expect(wrapper.get('[data-test="invitation-body-text"]').text()).toContain('您好')

    await wrapper.get('[data-test="save-invitation"]').trigger('click')
    await flushPromises()
    expect(invitationsApi.updateInvitation).toHaveBeenCalled()

    await wrapper.get('[data-test="copy-subject"]').trigger('click')
    await wrapper.get('[data-test="copy-html"]').trigger('click')
    await wrapper.get('[data-test="copy-text"]').trigger('click')
    await flushPromises()
    expect(invitationsApi.auditInvitationCopy).toHaveBeenCalledTimes(3)
    expect(wrapper.text()).not.toContain('已发送成功')
  })

  it('shows record-sent dialog disclaimer and fields', async () => {
    const wrapper = mount(InterviewInvitationDrawer, {
      props: { open: true, round, canManage: true },
      global: { stubs, directives: { loading: {} } },
    })
    await flushPromises()
    await wrapper.get('[data-test="record-sent"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-test="record-disclaimer"]').text()).toContain(
      '系统无法验证邮件是否送达',
    )
    expect(wrapper.find('[data-test="record-sent-at"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="record-channel"]').exists()).toBe(true)
    await wrapper.get('[data-test="submit-record-sent"]').trigger('click')
    await flushPromises()
    expect(invitationsApi.recordInvitationSent).toHaveBeenCalledWith(
      'msg-c',
      expect.objectContaining({
        message_version_id: 'ver-1',
        channel_type: 'CORPORATE_EMAIL',
        idempotency_key: expect.stringMatching(/\S+/),
      }),
    )
  })

  it('supports independent record-sent for each interviewer message', async () => {
    vi.mocked(invitationsApi.getInvitationDetail).mockImplementation(async (id: string) => {
      if (id === 'msg-i2') {
        return {
          ...summaries[2],
          subject: '面试官邮件',
          body_html: '<p>面试官</p>',
          body_text: '面试官',
          template_code: 'interviewer_initial',
          template_version: '1',
          content_hash: 'h2',
          current_version_no: 1,
          current_version_id: 'ver-3',
        }
      }
      return detail
    })
    const wrapper = mount(InterviewInvitationDrawer, {
      props: { open: true, round, canManage: true },
      global: { stubs, directives: { loading: {} } },
    })
    await flushPromises()
    await wrapper.get('[data-test="recipient-interviewer"]').trigger('click')
    // click second interviewer button - there may be two with same data-test
    const interviewerButtons = wrapper.findAll('[data-test="recipient-interviewer"]')
    expect(interviewerButtons.length).toBeGreaterThanOrEqual(2)
    await interviewerButtons[1].trigger('click')
    await flushPromises()
    await wrapper.get('[data-test="record-sent"]').trigger('click')
    await flushPromises()
    await wrapper.get('[data-test="submit-record-sent"]').trigger('click')
    await flushPromises()
    expect(invitationsApi.recordInvitationSent).toHaveBeenCalledWith(
      'msg-i2',
      expect.objectContaining({
        message_version_id: 'ver-3',
        idempotency_key: expect.stringMatching(/\S+/),
      }),
    )
  })

  it('hides write actions without manage permission', async () => {
    const wrapper = mount(InterviewInvitationDrawer, {
      props: { open: true, round, canManage: false },
      global: { stubs, directives: { loading: {} } },
    })
    await flushPromises()
    expect(wrapper.find('[data-test="generate-invitations"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="save-invitation"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="record-sent"]').exists()).toBe(false)
  })

  it('shows VOIDED as 已失效 and keeps history read-only', async () => {
    const voided = {
      id: 'msg-void',
      interview_round_id: round.id,
      schedule_id: 'sch-old',
      schedule_version: 1,
      event_type: 'INITIAL',
      audience_type: 'CANDIDATE',
      recipient_key: 'cand-old',
      recipient_name: '旧安排候选人',
      recipient_email_masked: 'o***@example.com',
      status: 'VOIDED',
      current_version_id: 'ver-void',
      current_version_no: 1,
      template_code: 'candidate_initial',
      version: 1,
      missing_fields: [],
      created_at: '2026-08-14T01:00:00Z',
      updated_at: '2026-08-14T01:00:00Z',
    }
    vi.mocked(invitationsApi.listInvitations).mockResolvedValue({
      items: [...summaries, voided],
      counts: { generated: 1, ready: 1, recorded_sent: 1, voided: 1 },
    })
    vi.mocked(invitationsApi.getInvitationDetail).mockResolvedValue({
      ...voided,
      subject: '旧主题',
      body_html: '<p>旧正文</p>',
      body_text: '旧正文',
      template_code: 'candidate_initial',
      template_version: '1',
      content_hash: 'void-hash',
      current_version_no: 1,
    })
    const wrapper = mount(InterviewInvitationDrawer, {
      props: { open: true, round, canManage: true },
      global: { stubs, directives: { loading: {} } },
    })
    await flushPromises()
    expect(wrapper.get('[data-test="voided-count"]').text()).toContain('已失效历史：1')
    expect(wrapper.text()).toContain('已失效')
    expect(wrapper.text()).toContain('旧安排已失效')
    expect(wrapper.text()).toContain('当前安排已生成：3')

    const voidedBtn = wrapper
      .findAll('button.recipient-item')
      .find((btn) => btn.text().includes('旧安排候选人'))
    expect(voidedBtn).toBeTruthy()
    await voidedBtn!.trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-test="save-invitation"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="copy-subject"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="record-sent"]').exists()).toBe(false)
    expect(
      wrapper.get('[data-test="invitation-subject"]').attributes('disabled'),
    ).toBeDefined()
  })
})
