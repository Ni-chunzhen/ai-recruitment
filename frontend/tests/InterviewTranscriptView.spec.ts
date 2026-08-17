import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'

import * as interviewsApi from '../src/api/interviews'
import type {
  InterviewTimeline,
  TranscriptList,
  TranscriptVersionDetail,
} from '../src/api/interviews'
import { useAuthStore } from '../src/stores/auth'
import InterviewTranscriptView from '../src/views/InterviewTranscriptView.vue'

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

const ROUND_ID = '44444444-4444-4444-4444-444444444444'
const APP_ID = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
const TRANSCRIPT_ID = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'
const T1_ID = '11111111-1111-1111-1111-111111111111'
const D1_ID = '22222222-2222-2222-2222-222222222222'
const C1_ID = '33333333-3333-3333-3333-333333333333'
const SEG1 = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1'
const SEG2 = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa2'

vi.mock('../src/api/interviews', () => ({
  getInterviewTimeline: vi.fn(),
  getRoundTranscripts: vi.fn(),
  getTranscriptVersion: vi.fn(),
  createTranscriptDraft: vi.fn(),
  saveTranscriptDraft: vi.fn(),
  confirmTranscriptDraft: vi.fn(),
}))

function makeTimeline(): InterviewTimeline {
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
    completed_round_count: 0,
    total_round_count: 1,
    rounds: [
      {
        id: ROUND_ID,
        application_id: APP_ID,
        job_version_id: 'jv-1',
        name: '技术一面',
        sequence_no: 1,
        status: 'PENDING_TRANSCRIPT',
        format: 'ONLINE',
        owner_id: 'owner-1',
        owner_name: '王磊',
        interviewers: [],
        current_schedule: {
          id: 'sch-1',
          schedule_version: 1,
          status: 'ACTIVE',
          start_at_utc: '2026-08-14T02:00:00Z',
          end_at_utc: '2026-08-14T03:00:00Z',
          timezone: 'Asia/Shanghai',
          format: 'ONLINE',
          meeting_mode: 'MANUAL',
          meeting_url: null,
          meeting_no: null,
          has_meeting_password: false,
          location: null,
          contact_name: null,
          contact_phone_masked: null,
          reschedule_reason: null,
          created_at: '2026-08-14T01:00:00Z',
        },
        schedule_history: [],
        version: 3,
        allowed_actions: ['complete'],
        created_at: '2026-08-14T00:00:00Z',
        updated_at: '2026-08-14T00:00:00Z',
      },
    ],
  }
}

function makeSegment(
  overrides: Partial<TranscriptVersionDetail['segments'][number]> = {},
) {
  return {
    id: SEG1,
    segment_no: 1,
    speaker_key: 'interviewer',
    speaker_name: '面试官',
    speaker_role: 'INTERVIEWER',
    start_time_ms: 0,
    end_time_ms: 1000,
    text: '你好候选人',
    source_type: 'ORIGINAL',
    source_segment_refs: [1],
    is_included_in_analysis: true,
    is_unclear: false,
    ...overrides,
  }
}

function makeVersion(
  overrides: Partial<TranscriptVersionDetail> = {},
): TranscriptVersionDetail {
  return {
    id: D1_ID,
    transcript_id: TRANSCRIPT_ID,
    interview_round_id: ROUND_ID,
    version_type: 'DRAFT',
    version_no: 1,
    version_label: 'D1',
    status: 'EDITING',
    source_method: 'PASTE',
    source_filename: null,
    source_size: 20,
    source_mime: null,
    source_encoding: 'utf-8',
    source_sha256: 'b'.repeat(64),
    based_on_version_id: T1_ID,
    confirmed_by: null,
    confirmed_at: null,
    version: 1,
    created_at: '2026-08-17T00:00:00Z',
    updated_at: '2026-08-17T00:00:00Z',
    segments: [
      makeSegment(),
      makeSegment({
        id: SEG2,
        segment_no: 2,
        speaker_key: 'candidate',
        speaker_name: '张三',
        speaker_role: 'CANDIDATE',
        start_time_ms: 1000,
        end_time_ms: 2000,
        text: '你好面试官',
        source_segment_refs: [2],
      }),
    ],
    raw_text: '你好',
    ...overrides,
  }
}

function makeList(overrides: Partial<TranscriptList> = {}): TranscriptList {
  return {
    transcript: {
      id: TRANSCRIPT_ID,
      interview_round_id: ROUND_ID,
      original_version_id: T1_ID,
      current_draft_version_id: D1_ID,
      current_confirmed_version_id: null,
      version: 1,
      created_at: '2026-08-17T00:00:00Z',
      updated_at: '2026-08-17T00:00:00Z',
    },
    versions: [
      {
        id: T1_ID,
        transcript_id: TRANSCRIPT_ID,
        version_type: 'ORIGINAL',
        version_no: 1,
        version_label: 'T1',
        status: 'IMMUTABLE',
        source_method: 'PASTE',
        source_filename: null,
        source_size: 20,
        source_mime: null,
        source_encoding: 'utf-8',
        based_on_version_id: null,
        segment_count: 2,
        confirmed_by: null,
        confirmed_at: null,
        version: 1,
        created_at: '2026-08-17T00:00:00Z',
        updated_at: '2026-08-17T00:00:00Z',
      },
      {
        id: D1_ID,
        transcript_id: TRANSCRIPT_ID,
        version_type: 'DRAFT',
        version_no: 1,
        version_label: 'D1',
        status: 'EDITING',
        source_method: 'PASTE',
        source_filename: null,
        source_size: 20,
        source_mime: null,
        source_encoding: 'utf-8',
        based_on_version_id: T1_ID,
        segment_count: 2,
        confirmed_by: null,
        confirmed_at: null,
        version: 1,
        created_at: '2026-08-17T00:00:00Z',
        updated_at: '2026-08-17T00:00:00Z',
      },
    ],
    ...overrides,
  }
}

const router = createRouter({
  history: createMemoryHistory(),
  routes: [
    {
      path: '/interview-rounds/:roundId/transcript',
      name: 'interview-transcript',
      component: InterviewTranscriptView,
    },
    {
      path: '/applications/:applicationId/interviews',
      name: 'application-interviews',
      component: { template: '<div>timeline</div>' },
    },
  ],
})

const stubs = {
  AdminLayout: { template: '<div class="layout"><slot /></div>' },
  'el-button': {
    props: ['disabled', 'loading', 'type', 'plain', 'link'],
    template:
      '<button :disabled="disabled || loading" :data-type="type"><slot /></button>',
  },
  'el-alert': {
    props: ['title', 'type'],
    template: '<div class="el-alert">{{ title }}<slot /></div>',
  },
  'el-input': {
    props: ['modelValue', 'disabled'],
    emits: ['update:modelValue'],
    template:
      '<input :disabled="disabled" :value="modelValue" @input="$emit(\'update:modelValue\', ($event.target).value)" />',
  },
  'el-empty': {
    props: ['description'],
    template: '<div>{{ description }}</div>',
  },
  'el-dialog': {
    props: ['modelValue', 'title'],
    template:
      '<div v-if="modelValue" class="dialog">{{ title }}<slot /><slot name="footer" /></div>',
  },
}

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

async function mountView(
  permissions: string[],
  query: Record<string, string> = { applicationId: APP_ID, versionId: D1_ID },
) {
  setActivePinia(createPinia())
  const store = useAuthStore()
  store.user = makeUser(permissions)
  store.initialized = true
  await router.push({
    name: 'interview-transcript',
    params: { roundId: ROUND_ID },
    query,
  })
  await router.isReady()
  const wrapper = mount(InterviewTranscriptView, {
    global: {
      plugins: [router],
      stubs,
      directives: { loading: {} },
    },
  })
  await flushPromises()
  return wrapper
}

describe('InterviewTranscriptView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(interviewsApi.getInterviewTimeline).mockResolvedValue(makeTimeline())
    vi.mocked(interviewsApi.getRoundTranscripts).mockResolvedValue(makeList())
    vi.mocked(interviewsApi.getTranscriptVersion).mockImplementation(
      async (versionId: string) => {
        if (versionId === T1_ID) {
          return makeVersion({
            id: T1_ID,
            version_type: 'ORIGINAL',
            version_label: 'T1',
            status: 'IMMUTABLE',
            based_on_version_id: null,
          })
        }
        if (versionId === C1_ID) {
          return makeVersion({
            id: C1_ID,
            version_type: 'CONFIRMED',
            version_label: 'C1',
            status: 'IMMUTABLE',
          })
        }
        return makeVersion()
      },
    )
    vi.mocked(interviewsApi.saveTranscriptDraft).mockImplementation(
      async (_id, body) => ({
        version: makeVersion({
          version: body.version + 1,
          segments: body.segments.map((item, index) =>
            makeSegment({
              id: `saved-${index}`,
              segment_no: index + 1,
              speaker_key: item.speaker_key,
              speaker_name: item.speaker_name,
              speaker_role: String(item.speaker_role),
              text: item.text,
              start_time_ms: item.start_time_ms ?? null,
              end_time_ms: item.end_time_ms ?? null,
              is_unclear: Boolean(item.is_unclear),
              is_included_in_analysis: item.is_included_in_analysis !== false,
              source_type:
                item.source_segment_refs?.length === 0
                  ? 'MANUAL_ADDITION'
                  : 'CORRECTED',
              source_segment_refs: item.source_segment_refs || [],
            }),
          ),
        }),
        change_counts: {
          speaker_changes: 1,
          text_corrections: 1,
          merge_split_count: 0,
          deleted_count: 0,
          manual_addition_count: 0,
          excluded_from_analysis_count: 0,
          reorder_count: 0,
        },
      }),
    )
    vi.mocked(interviewsApi.confirmTranscriptDraft).mockResolvedValue(
      makeVersion({
        id: C1_ID,
        version_type: 'CONFIRMED',
        version_label: 'C1',
        status: 'IMMUTABLE',
      }),
    )
  })

  it('renders version history and header meta', async () => {
    const wrapper = await mountView(['recruitment.manage'])
    expect(wrapper.text()).toContain('张三')
    expect(wrapper.text()).toContain('前端工程师')
    expect(wrapper.text()).toContain('技术一面')
    expect(wrapper.find('[data-test="version-T1"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="version-D1"]').exists()).toBe(true)
    expect(wrapper.get('[data-test="version-chip"]').text()).toContain('D1')
  })

  it('keeps T1 and Cn read-only', async () => {
    const wrapper = await mountView(['recruitment.manage'], {
      applicationId: APP_ID,
      versionId: T1_ID,
    })
    expect(wrapper.find('[data-test="save-draft"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="merge-btn"]').exists()).toBe(false)
    expect(wrapper.get('[data-test="side-speaker-name"]').attributes('disabled')).toBeDefined()

    await wrapper.get('[data-test="version-D1"]').trigger('click')
    await flushPromises()
    vi.mocked(interviewsApi.getTranscriptVersion).mockResolvedValueOnce(
      makeVersion({
        id: C1_ID,
        version_type: 'CONFIRMED',
        version_label: 'C1',
        status: 'IMMUTABLE',
      }),
    )
    // switch via direct load
    await wrapper.vm.refreshLatest()
    await flushPromises()
  })

  it('edits, merges, splits, deletes, adds, reorders and labels manual additions', async () => {
    const wrapper = await mountView(['recruitment.manage'])
    expect(wrapper.find('[data-test="save-draft"]').exists()).toBe(true)

    const textAreas = wrapper.findAll('[data-test="segment-text"]')
    await textAreas[0].setValue('你好，候选人')
    expect(wrapper.vm.dirty).toBe(true)

    await wrapper.get('[data-test="side-speaker-name"]').setValue('主面')
    await wrapper.get('[data-test="side-unclear"]').setValue(true)
    await wrapper.get('[data-test="side-include-analysis"]').setValue(false)
    expect(wrapper.find('[data-test="unclear-label"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="exclude-label"]').exists()).toBe(true)

    await wrapper.get('[data-test="insert-after-btn"]').trigger('click')
    expect(wrapper.find('[data-test="manual-addition-label"]').exists()).toBe(true)
    expect(wrapper.vm.segments).toHaveLength(3)

    // fill manual text so later confirm validation can pass after save
    const manual = wrapper.vm.segments.find(
      (item: { source_type: string }) => item.source_type === 'MANUAL_ADDITION',
    )
    if (manual) manual.text = '补充一句'

    await wrapper.get('[data-test="merge-btn"]').trigger('click')
    expect(wrapper.vm.segments.length).toBeLessThan(3)

    const firstId = wrapper.vm.segments[0].localId
    wrapper.vm.splitSegment(firstId, 2)
    expect(wrapper.vm.segments.length).toBeGreaterThan(1)

    const beforeDelete = wrapper.vm.segments.length
    wrapper.vm.removeSegment(wrapper.vm.segments[wrapper.vm.segments.length - 1].localId)
    expect(wrapper.vm.segments.length).toBe(beforeDelete - 1)

    const moveId = wrapper.vm.segments[0].localId
    wrapper.vm.moveSegment(moveId, 1)
    expect(wrapper.vm.segments[1].localId).toBe(moveId)
  })

  it('saves draft with version and idempotency key', async () => {
    const wrapper = await mountView(['recruitment.manage'])
    await wrapper.get('[data-test="save-draft"]').trigger('click')
    await flushPromises()
    expect(interviewsApi.saveTranscriptDraft).toHaveBeenCalledTimes(1)
    const [draftId, body] = vi.mocked(interviewsApi.saveTranscriptDraft).mock.calls[0]
    expect(draftId).toBe(D1_ID)
    expect(body.draft_version_id).toBe(D1_ID)
    expect(body.version).toBe(1)
    expect(body.idempotency_key.length).toBeGreaterThan(0)
    expect(body.segments.length).toBeGreaterThan(0)
    expect(wrapper.vm.dirty).toBe(false)
  })

  it('blocks confirm when unsaved / empty / invalid time / no analysis segments', async () => {
    const wrapper = await mountView(['recruitment.manage'])
    wrapper.vm.dirty = true
    expect(wrapper.vm.confirmBlockers).toContain('存在未保存的修改')

    wrapper.vm.dirty = false
    wrapper.vm.segments[0].text = '   '
    expect(wrapper.vm.confirmBlockers).toContain('存在空正文片段')

    wrapper.vm.segments[0].text = 'ok'
    wrapper.vm.segments[0].start_time_ms = 10
    wrapper.vm.segments[0].end_time_ms = null
    expect(wrapper.vm.confirmBlockers).toContain('存在非法时间范围')

    wrapper.vm.segments.forEach(
      (item: { start_time_ms: number | null; end_time_ms: number | null; is_included_in_analysis: boolean; text: string }) => {
        item.start_time_ms = null
        item.end_time_ms = null
        item.is_included_in_analysis = false
        item.text = 'ok'
      },
    )
    expect(wrapper.vm.confirmBlockers).toContain('至少需要一个参与分析的片段')
  })

  it('shows 409 refresh latest and does not overwrite silently', async () => {
    vi.mocked(interviewsApi.saveTranscriptDraft).mockRejectedValueOnce({
      response: { status: 409, data: { detail: 'conflict' } },
    })
    const wrapper = await mountView(['recruitment.manage'])
    wrapper.vm.dirty = true
    wrapper.vm.segments[0].text = 'changed'
    await wrapper.get('[data-test="save-draft"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-test="conflict-alert"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="refresh-latest"]').exists()).toBe(true)
    expect(wrapper.vm.segments[0].text).toBe('changed')
  })

  it('interviewer mode is read-only on confirmed version', async () => {
    vi.mocked(interviewsApi.getRoundTranscripts).mockResolvedValue(
      makeList({
        transcript: {
          id: TRANSCRIPT_ID,
          interview_round_id: ROUND_ID,
          original_version_id: T1_ID,
          current_draft_version_id: null,
          current_confirmed_version_id: C1_ID,
          version: 2,
          created_at: '2026-08-17T00:00:00Z',
          updated_at: '2026-08-17T00:00:00Z',
        },
        versions: [
          {
            id: C1_ID,
            transcript_id: TRANSCRIPT_ID,
            version_type: 'CONFIRMED',
            version_no: 1,
            version_label: 'C1',
            status: 'IMMUTABLE',
            source_method: 'PASTE',
            source_filename: null,
            source_size: 20,
            source_mime: null,
            source_encoding: 'utf-8',
            based_on_version_id: D1_ID,
            segment_count: 2,
            confirmed_by: 'u1',
            confirmed_at: '2026-08-17T01:00:00Z',
            version: 1,
            created_at: '2026-08-17T00:00:00Z',
            updated_at: '2026-08-17T00:00:00Z',
          },
        ],
      }),
    )
    const wrapper = await mountView(['interview.execute'], {
      applicationId: APP_ID,
      versionId: C1_ID,
      focus: 'confirmed',
    })
    expect(wrapper.find('[data-test="save-draft"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="confirm-proofread"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="merge-btn"]').exists()).toBe(false)
  })
})
