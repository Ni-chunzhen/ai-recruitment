<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, ref, watch } from 'vue'

import {
  importTranscript,
  previewTranscript,
  type TranscriptImportSegment,
  type TranscriptPreview,
  type TranscriptPreviewSegment,
  type TranscriptSpeakerRole,
} from '../../api/interviews'

const TRANSCRIPT_NON_AI_NOTICE =
  '系统仅按规则解析文本，不使用AI判断说话人。请在保存前核对敏感信息和说话人归属。'

const SPEAKER_ROLES: Array<{ value: TranscriptSpeakerRole; label: string }> = [
  { value: 'CANDIDATE', label: '候选人' },
  { value: 'INTERVIEWER', label: '面试官' },
  { value: 'OTHER', label: '其他' },
  { value: 'UNKNOWN', label: '未知' },
]

const props = defineProps<{
  open: boolean
  roundId: string | null
}>()

const emit = defineEmits<{
  'update:open': [value: boolean]
  imported: [versionId: string]
}>()

const activeTab = ref<'paste' | 'upload'>('paste')
const pasteText = ref('')
const selectedFile = ref<File | null>(null)
const previewing = ref(false)
const importing = ref(false)
const preview = ref<TranscriptPreview | null>(null)
const editableSegments = ref<TranscriptPreviewSegment[]>([])

const fileAccept = '.txt,.md,text/plain,text/markdown'

const drawerOpen = computed({
  get: () => props.open,
  set: (value: boolean) => emit('update:open', value),
})

function newIdempotencyKey() {
  return `transcript-import-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}

function resetState() {
  activeTab.value = 'paste'
  pasteText.value = ''
  selectedFile.value = null
  preview.value = null
  editableSegments.value = []
}

watch(
  () => props.open,
  (open) => {
    if (!open) resetState()
  },
)

function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0] ?? null
  selectedFile.value = file
  preview.value = null
  editableSegments.value = []
}

async function runPreview() {
  if (!props.roundId) return
  previewing.value = true
  try {
    let result: TranscriptPreview
    if (activeTab.value === 'paste') {
      const text = pasteText.value
      if (!text.trim()) {
        ElMessage.warning('请先粘贴听记文本')
        return
      }
      result = await previewTranscript(props.roundId, { text })
    } else {
      if (!selectedFile.value) {
        ElMessage.warning('请先选择 TXT/MD 文件')
        return
      }
      const form = new FormData()
      form.append('file', selectedFile.value)
      result = await previewTranscript(props.roundId, form)
    }
    preview.value = result
    editableSegments.value = result.segments.map((segment) => ({ ...segment }))
  } catch (error) {
    ElMessage.error(
      (error as { response?: { data?: { detail?: string } } })?.response?.data
        ?.detail || '预览失败',
    )
  } finally {
    previewing.value = false
  }
}

function updateSegmentRole(index: number, role: string) {
  const segment = editableSegments.value[index]
  if (!segment) return
  segment.speaker_role = role
}

function updateSegmentName(index: number, name: string) {
  const segment = editableSegments.value[index]
  if (!segment) return
  segment.speaker_name = name
}

function updateSegmentKey(index: number, key: string) {
  const segment = editableSegments.value[index]
  if (!segment) return
  segment.speaker_key = key
}

function updateSegmentText(index: number, text: string) {
  const segment = editableSegments.value[index]
  if (!segment) return
  segment.text = text
}

function formatTime(ms: number | null) {
  if (ms == null) return '—'
  const totalSec = Math.floor(ms / 1000)
  const m = Math.floor(totalSec / 60)
  const s = totalSec % 60
  return `${m}:${String(s).padStart(2, '0')}`
}

async function confirmImport() {
  if (!props.roundId || !preview.value || !editableSegments.value.length) return
  try {
    await ElMessageBox.confirm(
      '确认导入并创建不可变原始版本 T1？导入后不可修改原始版本。',
      '确认导入听记',
      { type: 'warning', confirmButtonText: '确认导入', cancelButtonText: '返回' },
    )
  } catch {
    return
  }

  importing.value = true
  try {
    const segments: TranscriptImportSegment[] = editableSegments.value.map(
      (segment) => ({
        speaker_key: segment.speaker_key,
        speaker_name: segment.speaker_name,
        speaker_role: segment.speaker_role,
        text: segment.text,
        start_time_ms: segment.start_time_ms,
        end_time_ms: segment.end_time_ms,
        is_unclear: false,
        is_included_in_analysis: true,
        source_segment_refs: [segment.segment_no],
      }),
    )
    const rawText =
      activeTab.value === 'paste'
        ? pasteText.value
        : editableSegments.value.map((item) => item.text).join('\n')
    const result = await importTranscript(props.roundId, {
      idempotency_key: newIdempotencyKey(),
      source_method: preview.value.source_method,
      raw_text: rawText,
      filename: preview.value.filename,
      source_sha256: preview.value.sha256,
      segments,
    })
    ElMessage.success('已导入原始版本 T1')
    emit('imported', result.id)
    drawerOpen.value = false
  } catch (error) {
    ElMessage.error(
      (error as { response?: { data?: { detail?: string } } })?.response?.data
        ?.detail || '导入失败',
    )
  } finally {
    importing.value = false
  }
}

defineExpose({
  activeTab,
  pasteText,
  preview,
  editableSegments,
  runPreview,
  confirmImport,
})
</script>

<template>
  <el-drawer
    v-model="drawerOpen"
    title="导入听记文本"
    size="720px"
    data-test="transcript-import-drawer"
  >
    <el-alert
      :title="TRANSCRIPT_NON_AI_NOTICE"
      type="warning"
      :closable="false"
      show-icon
      data-test="non-ai-notice"
      style="margin-bottom: 12px"
    />

    <div class="tabs" data-test="import-tabs">
      <button
        type="button"
        class="tab"
        :class="{ active: activeTab === 'paste' }"
        data-test="tab-paste"
        @click="activeTab = 'paste'"
      >
        粘贴文本
      </button>
      <button
        type="button"
        class="tab"
        :class="{ active: activeTab === 'upload' }"
        data-test="tab-upload"
        @click="activeTab = 'upload'"
      >
        上传 TXT/MD
      </button>
    </div>

    <div v-if="activeTab === 'paste'" class="form-item">
      <label>听记文本</label>
      <el-input
        v-model="pasteText"
        type="textarea"
        :rows="10"
        data-test="paste-input"
        placeholder="粘贴听记工具导出的纯文本"
      />
    </div>
    <div v-else class="form-item">
      <label>选择文件（仅 TXT / MD）</label>
      <input
        type="file"
        data-test="file-input"
        :accept="fileAccept"
        @change="onFileChange"
      />
      <p v-if="selectedFile" class="file-name" data-test="file-name">
        {{ selectedFile.name }}
      </p>
    </div>

    <div class="actions">
      <el-button
        type="primary"
        data-test="preview-btn"
        :loading="previewing"
        @click="runPreview"
      >
        解析预览
      </el-button>
    </div>

    <div v-if="preview" class="preview-panel" data-test="preview-meta">
      <div class="meta-grid">
        <span>编码 <strong data-test="meta-encoding">{{ preview.encoding }}</strong></span>
        <span>大小 <strong data-test="meta-size">{{ preview.size }}</strong></span>
        <span>SHA256 <strong data-test="meta-sha256">{{ preview.sha256 }}</strong></span>
        <span>字符数 <strong data-test="meta-char-count">{{ preview.char_count }}</strong></span>
        <span>片段数 <strong data-test="meta-segment-count">{{ preview.segment_count }}</strong></span>
      </div>

      <div class="segment-list" data-test="preview-segments">
        <div
          v-for="(segment, index) in editableSegments"
          :key="`${segment.segment_no}-${index}`"
          class="segment-row"
          :class="{ unknown: segment.speaker_role === 'UNKNOWN' }"
          :data-test="`preview-segment-${index}`"
        >
          <div class="segment-head">
            <span class="seg-no">#{{ segment.segment_no }}</span>
            <el-input
              :model-value="segment.speaker_key"
              data-test="speaker-key"
              size="small"
              @update:model-value="updateSegmentKey(index, String($event))"
            />
            <el-input
              :model-value="segment.speaker_name"
              data-test="speaker-name"
              size="small"
              @update:model-value="updateSegmentName(index, String($event))"
            />
            <select
              class="native-select"
              data-test="speaker-role"
              :value="segment.speaker_role"
              @change="
                updateSegmentRole(
                  index,
                  ($event.target as HTMLSelectElement).value,
                )
              "
            >
              <option
                v-for="role in SPEAKER_ROLES"
                :key="role.value"
                :value="role.value"
              >
                {{ role.label }}
              </option>
            </select>
            <span class="time" data-test="segment-time">
              {{ formatTime(segment.start_time_ms) }}–{{ formatTime(segment.end_time_ms) }}
            </span>
          </div>
          <el-input
            :model-value="segment.text"
            type="textarea"
            :rows="2"
            data-test="segment-text"
            @update:model-value="updateSegmentText(index, String($event))"
          />
          <span
            v-if="segment.speaker_role === 'UNKNOWN'"
            class="unknown-badge"
            data-test="unknown-badge"
          >
            UNKNOWN
          </span>
        </div>
      </div>

      <div class="actions">
        <el-button
          type="primary"
          data-test="confirm-import"
          :loading="importing"
          @click="confirmImport"
        >
          确认导入
        </el-button>
      </div>
    </div>
  </el-drawer>
</template>

<style scoped>
.tabs {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}
.tab {
  border: 1px solid var(--border);
  background: var(--bg);
  border-radius: 8px;
  padding: 6px 12px;
  cursor: pointer;
}
.tab.active {
  border-color: var(--primary, #2563eb);
  color: var(--primary, #2563eb);
  background: var(--surface);
}
.form-item {
  margin-bottom: 12px;
}
.form-item label {
  display: block;
  margin-bottom: 6px;
  font-size: 13px;
  color: var(--muted);
}
.actions {
  margin: 12px 0;
}
.preview-panel {
  border-top: 1px solid var(--border);
  padding-top: 12px;
}
.meta-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  font-size: 12px;
  color: var(--muted);
  margin-bottom: 12px;
}
.meta-grid strong {
  color: var(--fg);
  margin-left: 4px;
  word-break: break-all;
}
.segment-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-height: 420px;
  overflow: auto;
}
.segment-row {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px;
  background: var(--surface);
}
.segment-row.unknown {
  border-color: #f59e0b;
  background: #fffbeb;
}
.segment-head {
  display: grid;
  grid-template-columns: 40px 1fr 1fr 120px auto;
  gap: 6px;
  margin-bottom: 8px;
  align-items: center;
}
.seg-no {
  font-size: 12px;
  color: var(--muted);
}
.time {
  font-size: 12px;
  color: var(--muted);
  white-space: nowrap;
}
.unknown-badge {
  display: inline-block;
  margin-top: 6px;
  font-size: 12px;
  color: #b45309;
}
.native-select {
  height: 28px;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 0 6px;
  background: var(--surface);
}
.file-name {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--muted);
}
</style>
