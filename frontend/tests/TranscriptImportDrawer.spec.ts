import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'

import * as interviewsApi from '../src/api/interviews'
import TranscriptImportDrawer from '../src/components/interviews/TranscriptImportDrawer.vue'

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

const { ElMessageBox } = await import('element-plus')

const ROUND_ID = '44444444-4444-4444-4444-444444444444'

vi.mock('../src/api/interviews', () => ({
  previewTranscript: vi.fn(),
  importTranscript: vi.fn(),
}))

const previewPayload = {
  encoding: 'utf-8',
  sha256: 'a'.repeat(64),
  char_count: 12,
  segment_count: 2,
  matched_rules: ['interviewer', 'unknown'],
  source_method: 'PASTE',
  filename: null,
  size: 24,
  mime: null,
  segments: [
    {
      segment_no: 1,
      speaker_key: 'interviewer',
      speaker_name: '面试官',
      speaker_role: 'INTERVIEWER',
      start_time_ms: 0,
      end_time_ms: 1000,
      text: '你好',
      matched_rule: 'interviewer',
    },
    {
      segment_no: 2,
      speaker_key: 'unknown',
      speaker_name: '未知',
      speaker_role: 'UNKNOWN',
      start_time_ms: null,
      end_time_ms: null,
      text: '嗯',
      matched_rule: 'unknown',
    },
  ],
}

const stubs = {
  'el-drawer': {
    props: ['modelValue', 'title'],
    template:
      '<div v-if="modelValue" class="drawer" data-test="transcript-import-drawer">{{ title }}<slot /></div>',
  },
  'el-alert': {
    props: ['title'],
    template: '<div class="alert" data-test="non-ai-notice">{{ title }}</div>',
  },
  'el-button': {
    props: ['loading', 'disabled', 'type'],
    template: '<button :disabled="disabled || loading"><slot /></button>',
  },
  'el-input': {
    props: ['modelValue', 'type'],
    emits: ['update:modelValue'],
    template:
      '<textarea v-if="type === \'textarea\'" :value="modelValue" @input="$emit(\'update:modelValue\', ($event.target).value)" /><input v-else :value="modelValue" @input="$emit(\'update:modelValue\', ($event.target).value)" />',
  },
}

async function mountDrawer() {
  const wrapper = mount(TranscriptImportDrawer, {
    props: {
      open: true,
      roundId: ROUND_ID,
    },
    global: { stubs },
  })
  await nextTick()
  return wrapper
}

describe('TranscriptImportDrawer', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(interviewsApi.previewTranscript).mockResolvedValue(previewPayload)
    vi.mocked(interviewsApi.importTranscript).mockResolvedValue({
      id: 'ver-t1',
      transcript_id: 'tr-1',
      interview_round_id: ROUND_ID,
      version_type: 'ORIGINAL',
      version_no: 1,
      version_label: 'T1',
      status: 'IMMUTABLE',
      source_method: 'PASTE',
      source_filename: null,
      source_size: 24,
      source_mime: null,
      source_encoding: 'utf-8',
      source_sha256: 'a'.repeat(64),
      based_on_version_id: null,
      confirmed_by: null,
      confirmed_at: null,
      version: 1,
      created_at: '2026-08-17T00:00:00Z',
      updated_at: '2026-08-17T00:00:00Z',
      segments: [],
      raw_text: '你好',
    })
  })

  it('shows paste/upload tabs and fixed non-AI notice', async () => {
    const wrapper = await mountDrawer()
    expect(wrapper.get('[data-test="tab-paste"]').text()).toContain('粘贴文本')
    expect(wrapper.get('[data-test="tab-upload"]').text()).toContain('上传 TXT/MD')
    expect(wrapper.get('[data-test="non-ai-notice"]').text()).toContain(
      '不使用AI判断说话人',
    )
  })

  it('restricts file accept to txt/md only', async () => {
    const wrapper = await mountDrawer()
    await wrapper.get('[data-test="tab-upload"]').trigger('click')
    await nextTick()
    const input = wrapper.get('[data-test="file-input"]')
    expect(input.attributes('accept')).toBe(
      '.txt,.md,text/plain,text/markdown',
    )
    expect(input.attributes('accept')).not.toContain('docx')
    expect(input.attributes('accept')).not.toContain('audio')
  })

  it('previews metadata and UNKNOWN segments', async () => {
    const wrapper = await mountDrawer()
    wrapper.vm.pasteText = '面试官：你好\n未知：嗯'
    await wrapper.get('[data-test="preview-btn"]').trigger('click')
    await flushPromises()
    expect(interviewsApi.previewTranscript).toHaveBeenCalledWith(ROUND_ID, {
      text: '面试官：你好\n未知：嗯',
    })
    expect(wrapper.get('[data-test="meta-encoding"]').text()).toBe('utf-8')
    expect(wrapper.get('[data-test="meta-size"]').text()).toBe('24')
    expect(wrapper.get('[data-test="meta-sha256"]').text()).toBe('a'.repeat(64))
    expect(wrapper.get('[data-test="meta-char-count"]').text()).toBe('12')
    expect(wrapper.get('[data-test="meta-segment-count"]').text()).toBe('2')
    expect(wrapper.find('[data-test="unknown-badge"]').exists()).toBe(true)
  })

  it('allows correcting speaker/role before import and confirms with idempotency', async () => {
    const wrapper = await mountDrawer()
    wrapper.vm.pasteText = '面试官：你好\n未知：嗯'
    await wrapper.get('[data-test="preview-btn"]').trigger('click')
    await flushPromises()

    const roleSelect = wrapper.findAll('[data-test="speaker-role"]')[1]
    await roleSelect.setValue('CANDIDATE')
    const nameInputs = wrapper.findAll('[data-test="speaker-name"]')
    await nameInputs[1].setValue('张三')

    await wrapper.get('[data-test="confirm-import"]').trigger('click')
    await flushPromises()

    expect(ElMessageBox.confirm).toHaveBeenCalled()
    expect(interviewsApi.importTranscript).toHaveBeenCalledTimes(1)
    const [, body] = vi.mocked(interviewsApi.importTranscript).mock.calls[0]
    expect(body.idempotency_key.length).toBeGreaterThan(0)
    expect(body.source_sha256).toBe('a'.repeat(64))
    expect(body.segments[1].speaker_role).toBe('CANDIDATE')
    expect(body.segments[1].speaker_name).toBe('张三')
    expect(wrapper.emitted('imported')?.[0]).toEqual(['ver-t1'])
  })

  it('previews BOM file without BOM in first interviewer segment and keeps non-AI notice', async () => {
    const bomPreview = {
      ...previewPayload,
      encoding: 'utf-8-sig',
      source_method: 'TXT',
      filename: 'notes.txt',
      segments: [
        {
          segment_no: 1,
          speaker_key: 'interviewer',
          speaker_name: '面试官',
          speaker_role: 'INTERVIEWER',
          start_time_ms: 0,
          end_time_ms: 1000,
          text: '你好',
          matched_rule: 'interviewer',
        },
        {
          segment_no: 2,
          speaker_key: 'candidate',
          speaker_name: '候选人',
          speaker_role: 'CANDIDATE',
          start_time_ms: null,
          end_time_ms: null,
          text: '嗯',
          matched_rule: 'candidate',
        },
      ],
    }
    vi.mocked(interviewsApi.previewTranscript).mockResolvedValueOnce(bomPreview)

    const wrapper = await mountDrawer()
    await wrapper.get('[data-test="tab-upload"]').trigger('click')
    await nextTick()

    const bomBytes = new Uint8Array([
      0xef, 0xbb, 0xbf, ...new TextEncoder().encode('面试官：你好\n候选人：嗯'),
    ])
    const file = new File([bomBytes], 'notes.txt', { type: 'text/plain' })
    wrapper.vm.selectedFile = file
    await wrapper.get('[data-test="preview-btn"]').trigger('click')
    await flushPromises()

    expect(interviewsApi.previewTranscript).toHaveBeenCalledTimes(1)
    const callArg = vi.mocked(interviewsApi.previewTranscript).mock.calls[0][1]
    expect(callArg).toBeInstanceOf(FormData)

    expect(wrapper.get('[data-test="non-ai-notice"]').text()).toContain('不使用AI')
    const firstRole = wrapper.findAll('[data-test="speaker-role"]')[0]
    expect((firstRole.element as HTMLSelectElement).value).toBe('INTERVIEWER')
    expect(wrapper.vm.editableSegments[0].speaker_role).toBe('INTERVIEWER')
    expect(wrapper.vm.editableSegments[0].text).toBe('你好')
    expect(wrapper.vm.editableSegments[0].text).not.toContain('\uFEFF')
    expect(wrapper.vm.editableSegments[0].speaker_role).not.toBe('UNKNOWN')
  })
})
