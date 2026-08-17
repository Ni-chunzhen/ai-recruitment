<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, onMounted, ref } from 'vue'
import { onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'

import {
  confirmTranscriptDraft,
  createTranscriptDraft,
  getInterviewTimeline,
  getRoundTranscripts,
  getTranscriptVersion,
  saveTranscriptDraft,
  type InterviewRound,
  type InterviewTimeline,
  type TranscriptChangeCounts,
  type TranscriptList,
  type TranscriptVersionDetail,
} from '../api/interviews'
import TranscriptConfirmDialog from '../components/interviews/TranscriptConfirmDialog.vue'
import TranscriptSegmentEditor, {
  type EditableSegment,
} from '../components/interviews/TranscriptSegmentEditor.vue'
import AdminLayout from '../layouts/AdminLayout.vue'
import { useAuthStore } from '../stores/auth'

const DRAFT_CONFLICT_HINT =
  '转写草稿已被其他人员更新，请刷新后重新检查修改。'

const SPEAKER_ROLES = [
  { value: 'CANDIDATE', label: '候选人' },
  { value: 'INTERVIEWER', label: '面试官' },
  { value: 'OTHER', label: '其他' },
  { value: 'UNKNOWN', label: '未知' },
] as const

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()

const roundId = computed(() => route.params.roundId as string)
const applicationId = computed(
  () => (route.query.applicationId as string | undefined) || '',
)
const canManage = computed(() => authStore.hasPermission('recruitment.manage'))

const loading = ref(false)
const saving = ref(false)
const confirming = ref(false)
const timeline = ref<InterviewTimeline | null>(null)
const transcriptList = ref<TranscriptList | null>(null)
const currentDetail = ref<TranscriptVersionDetail | null>(null)
const segments = ref<EditableSegment[]>([])
const selectedLocalId = ref<string | null>(null)
const dirty = ref(false)
const saveStatus = ref('未保存')
const conflictOpen = ref(false)
const confirmOpen = ref(false)
const changeCounts = ref<TranscriptChangeCounts>({
  speaker_changes: 0,
  text_corrections: 0,
  merge_split_count: 0,
  deleted_count: 0,
  manual_addition_count: 0,
  excluded_from_analysis_count: 0,
  reorder_count: 0,
})

const round = computed<InterviewRound | null>(() => {
  if (!timeline.value) return null
  return timeline.value.rounds.find((item) => item.id === roundId.value) ?? null
})

const selectedSegment = computed(
  () =>
    segments.value.find((item) => item.localId === selectedLocalId.value) ??
    null,
)

const isEditable = computed(() => {
  if (!canManage.value) return false
  const detail = currentDetail.value
  return (
    detail?.version_type === 'DRAFT' && detail.status === 'EDITING'
  )
})

const sortedVersions = computed(() => {
  const versions = transcriptList.value?.versions ?? []
  return [...versions].sort((a, b) => {
    const rank = (type: string) =>
      type === 'ORIGINAL' ? 0 : type === 'DRAFT' ? 1 : 2
    const diff = rank(a.version_type) - rank(b.version_type)
    if (diff !== 0) return diff
    return a.version_no - b.version_no
  })
})

const unknownCount = computed(
  () => segments.value.filter((item) => item.speaker_role === 'UNKNOWN').length,
)
const unclearCount = computed(
  () => segments.value.filter((item) => item.is_unclear).length,
)

const confirmBlockers = computed(() => {
  const blockers: string[] = []
  if (dirty.value) blockers.push('存在未保存的修改')
  if (!segments.value.length) blockers.push('至少需要一个片段')
  if (segments.value.some((item) => !item.text.trim())) {
    blockers.push('存在空正文片段')
  }
  for (const item of segments.value) {
    const start = item.start_time_ms
    const end = item.end_time_ms
    if ((start == null) !== (end == null)) {
      blockers.push('存在非法时间范围')
      break
    }
    if (start != null && end != null && (start < 0 || start >= end)) {
      blockers.push('存在非法时间范围')
      break
    }
  }
  if (
    !segments.value.some(
      (item) => item.is_included_in_analysis && item.text.trim(),
    )
  ) {
    blockers.push('至少需要一个参与分析的片段')
  }
  return blockers
})

function newIdempotencyKey(prefix: string) {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}

function newLocalId() {
  return `seg-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

function toEditable(detail: TranscriptVersionDetail): EditableSegment[] {
  return detail.segments.map((segment) => ({
    localId: segment.id,
    id: segment.id,
    segment_no: segment.segment_no,
    speaker_key: segment.speaker_key,
    speaker_name: segment.speaker_name,
    speaker_role: segment.speaker_role,
    start_time_ms: segment.start_time_ms,
    end_time_ms: segment.end_time_ms,
    text: segment.text,
    source_type: segment.source_type,
    source_segment_refs: [...segment.source_segment_refs],
    is_included_in_analysis: segment.is_included_in_analysis,
    is_unclear: segment.is_unclear,
  }))
}

function renumber() {
  segments.value.forEach((item, index) => {
    item.segment_no = index + 1
  })
}

function markDirty() {
  dirty.value = true
  saveStatus.value = '有未保存修改'
}

function formatLocal(iso: string | null | undefined, timezone: string) {
  if (!iso) return '—'
  try {
    return new Intl.DateTimeFormat('zh-CN', {
      timeZone: timezone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    }).format(new Date(iso))
  } catch {
    return iso
  }
}

function isConflictError(err: unknown) {
  return (err as { response?: { status?: number } })?.response?.status === 409
}

async function loadTimeline() {
  if (!applicationId.value) return
  timeline.value = await getInterviewTimeline(applicationId.value)
}

async function loadTranscriptList() {
  transcriptList.value = await getRoundTranscripts(roundId.value)
}

function resolveInitialVersionId(list: TranscriptList): string | null {
  const queryVersion = route.query.versionId as string | undefined
  if (queryVersion) return queryVersion
  const focus = route.query.focus as string | undefined
  const transcript = list.transcript
  if (!transcript) return list.versions[0]?.id ?? null
  if (focus === 'original' && transcript.original_version_id) {
    return transcript.original_version_id
  }
  if (focus === 'confirmed' && transcript.current_confirmed_version_id) {
    return transcript.current_confirmed_version_id
  }
  if (focus === 'draft' && transcript.current_draft_version_id) {
    return transcript.current_draft_version_id
  }
  if (!canManage.value) {
    return transcript.current_confirmed_version_id
  }
  return (
    transcript.current_draft_version_id ||
    transcript.current_confirmed_version_id ||
    transcript.original_version_id ||
    list.versions[0]?.id ||
    null
  )
}

async function loadVersion(versionId: string) {
  const detail = await getTranscriptVersion(versionId)
  currentDetail.value = detail
  segments.value = toEditable(detail)
  selectedLocalId.value = segments.value[0]?.localId ?? null
  dirty.value = false
  saveStatus.value = '已同步'
  conflictOpen.value = false
  if (
    route.query.openConfirm === '1' &&
    detail.version_type === 'DRAFT' &&
    detail.status === 'EDITING' &&
    canManage.value
  ) {
    confirmOpen.value = true
  }
}

async function ensureDraftIfNeeded(list: TranscriptList): Promise<{
  list: TranscriptList
  versionId: string | null
}> {
  if (!canManage.value) {
    return { list, versionId: resolveInitialVersionId(list) }
  }
  const focus = route.query.focus as string | undefined
  const autoDraft =
    focus === 'draft' ||
    focus === 'proofread' ||
    route.query.autoDraft === '1'
  const transcript = list.transcript
  if (!autoDraft || !transcript) {
    return { list, versionId: resolveInitialVersionId(list) }
  }
  if (transcript.current_draft_version_id) {
    return {
      list,
      versionId: transcript.current_draft_version_id,
    }
  }
  const draft = await createTranscriptDraft(transcript.id, {
    idempotency_key: newIdempotencyKey('draft-create'),
  })
  const refreshed = await getRoundTranscripts(roundId.value)
  transcriptList.value = refreshed
  return { list: refreshed, versionId: draft.id }
}

async function load() {
  loading.value = true
  try {
    await loadTimeline()
    let list = await getRoundTranscripts(roundId.value)
    transcriptList.value = list
    const ensured = await ensureDraftIfNeeded(list)
    list = ensured.list
    const versionId = ensured.versionId
    if (!versionId) {
      currentDetail.value = null
      segments.value = []
      return
    }
    await loadVersion(versionId)
  } catch (error) {
    ElMessage.error(
      (error as { response?: { data?: { detail?: string } } })?.response?.data
        ?.detail || '加载转写失败',
    )
  } finally {
    loading.value = false
  }
}

async function selectVersion(versionId: string) {
  if (dirty.value && isEditable.value) {
    ElMessage.warning('请先保存当前草稿修改')
    return
  }
  await router.replace({
    query: { ...route.query, versionId, focus: undefined },
  })
  await loadVersion(versionId)
}

function updateSelectedField<K extends keyof EditableSegment>(
  key: K,
  value: EditableSegment[K],
) {
  const segment = selectedSegment.value
  if (!segment || !isEditable.value) return
  segment[key] = value
  if (key === 'speaker_key' || key === 'speaker_name' || key === 'speaker_role') {
    if (segment.source_type === 'ORIGINAL') {
      segment.source_type = 'CORRECTED'
    }
  }
  markDirty()
}

function onTextUpdate(localId: string, text: string) {
  const segment = segments.value.find((item) => item.localId === localId)
  if (!segment || !isEditable.value) return
  segment.text = text
  if (segment.source_type === 'ORIGINAL') {
    segment.source_type = 'CORRECTED'
  }
  markDirty()
}

function mergeSegment(localId: string) {
  if (!isEditable.value) return
  const index = segments.value.findIndex((item) => item.localId === localId)
  if (index < 0 || index >= segments.value.length - 1) return
  const current = segments.value[index]
  const next = segments.value[index + 1]
  current.text = `${current.text}\n${next.text}`.trim()
  current.end_time_ms = next.end_time_ms ?? current.end_time_ms
  current.source_segment_refs = [
    ...new Set([...current.source_segment_refs, ...next.source_segment_refs]),
  ]
  if (current.source_type === 'ORIGINAL') current.source_type = 'CORRECTED'
  segments.value.splice(index + 1, 1)
  renumber()
  changeCounts.value.merge_split_count += 1
  markDirty()
}

function splitSegment(localId: string, cursor: number) {
  if (!isEditable.value) return
  const index = segments.value.findIndex((item) => item.localId === localId)
  if (index < 0) return
  const current = segments.value[index]
  const left = current.text.slice(0, cursor)
  const right = current.text.slice(cursor)
  if (!left.trim() || !right.trim()) {
    ElMessage.warning('请将光标放在正文中间再拆分')
    return
  }
  current.text = left
  if (current.source_type === 'ORIGINAL') current.source_type = 'CORRECTED'
  const created: EditableSegment = {
    localId: newLocalId(),
    segment_no: current.segment_no + 1,
    speaker_key: current.speaker_key,
    speaker_name: current.speaker_name,
    speaker_role: current.speaker_role,
    start_time_ms: null,
    end_time_ms: null,
    text: right,
    source_type: 'CORRECTED',
    source_segment_refs: [...current.source_segment_refs],
    is_included_in_analysis: current.is_included_in_analysis,
    is_unclear: false,
  }
  segments.value.splice(index + 1, 0, created)
  renumber()
  changeCounts.value.merge_split_count += 1
  markDirty()
}

function removeSegment(localId: string) {
  if (!isEditable.value) return
  if (segments.value.length <= 1) {
    ElMessage.warning('至少保留一个片段')
    return
  }
  segments.value = segments.value.filter((item) => item.localId !== localId)
  renumber()
  changeCounts.value.deleted_count += 1
  if (selectedLocalId.value === localId) {
    selectedLocalId.value = segments.value[0]?.localId ?? null
  }
  markDirty()
}

function insertRelative(localId: string, position: 'before' | 'after') {
  if (!isEditable.value) return
  const index = segments.value.findIndex((item) => item.localId === localId)
  if (index < 0) return
  const created: EditableSegment = {
    localId: newLocalId(),
    segment_no: 0,
    speaker_key: 'manual',
    speaker_name: '人工补充',
    speaker_role: 'OTHER',
    start_time_ms: null,
    end_time_ms: null,
    text: '',
    source_type: 'MANUAL_ADDITION',
    source_segment_refs: [],
    is_included_in_analysis: true,
    is_unclear: false,
  }
  const insertAt = position === 'before' ? index : index + 1
  segments.value.splice(insertAt, 0, created)
  renumber()
  selectedLocalId.value = created.localId
  changeCounts.value.manual_addition_count += 1
  markDirty()
}

function moveSegment(localId: string, direction: -1 | 1) {
  if (!isEditable.value) return
  const index = segments.value.findIndex((item) => item.localId === localId)
  const target = index + direction
  if (index < 0 || target < 0 || target >= segments.value.length) return
  const copy = [...segments.value]
  const [item] = copy.splice(index, 1)
  copy.splice(target, 0, item)
  segments.value = copy
  renumber()
  changeCounts.value.reorder_count += 1
  markDirty()
}

async function saveDraft() {
  if (!currentDetail.value || !isEditable.value) return
  saving.value = true
  try {
    const result = await saveTranscriptDraft(currentDetail.value.id, {
      draft_version_id: currentDetail.value.id,
      version: currentDetail.value.version,
      idempotency_key: newIdempotencyKey('draft-save'),
      segments: segments.value.map((item) => ({
        speaker_key: item.speaker_key,
        speaker_name: item.speaker_name,
        speaker_role: item.speaker_role,
        text: item.text,
        start_time_ms: item.start_time_ms,
        end_time_ms: item.end_time_ms,
        is_unclear: item.is_unclear,
        is_included_in_analysis: item.is_included_in_analysis,
        source_segment_refs: item.source_segment_refs,
      })),
    })
    currentDetail.value = result.version
    changeCounts.value = result.change_counts
    segments.value = toEditable(result.version)
    dirty.value = false
    saveStatus.value = '已保存'
    await loadTranscriptList()
    ElMessage.success('草稿已保存')
  } catch (error) {
    if (isConflictError(error)) {
      conflictOpen.value = true
      ElMessage.error(DRAFT_CONFLICT_HINT)
      return
    }
    ElMessage.error(
      (error as { response?: { data?: { detail?: string } } })?.response?.data
        ?.detail || '保存失败',
    )
  } finally {
    saving.value = false
  }
}

function openConfirm() {
  if (!isEditable.value) return
  confirmOpen.value = true
}

async function submitConfirm() {
  if (!currentDetail.value || confirmBlockers.value.length) return
  confirming.value = true
  try {
    const result = await confirmTranscriptDraft(currentDetail.value.id, {
      idempotency_key: newIdempotencyKey('draft-confirm'),
      version: currentDetail.value.version,
    })
    ElMessage.success(`已确认 ${result.version_label}`)
    confirmOpen.value = false
    await loadTranscriptList()
    await loadVersion(result.id)
    await loadTimeline()
  } catch (error) {
    if (isConflictError(error)) {
      conflictOpen.value = true
      ElMessage.error(DRAFT_CONFLICT_HINT)
      return
    }
    ElMessage.error(
      (error as { response?: { data?: { detail?: string } } })?.response?.data
        ?.detail || '确认失败',
    )
  } finally {
    confirming.value = false
  }
}

async function refreshLatest() {
  conflictOpen.value = false
  dirty.value = false
  await load()
}

function backToTimeline() {
  if (applicationId.value) {
    void router.push(`/applications/${applicationId.value}/interviews`)
    return
  }
  router.back()
}

onBeforeRouteUpdate(async (to) => {
  if (
    to.params.roundId !== route.params.roundId ||
    to.query.versionId !== route.query.versionId ||
    to.query.focus !== route.query.focus
  ) {
    await load()
  }
})

onMounted(() => {
  void load()
})

defineExpose({
  segments,
  dirty,
  saveStatus,
  currentDetail,
  confirmBlockers,
  changeCounts,
  isEditable,
  saveDraft,
  openConfirm,
  submitConfirm,
  mergeSegment,
  splitSegment,
  removeSegment,
  insertRelative,
  moveSegment,
  refreshLatest,
})
</script>

<template>
  <AdminLayout>
    <div v-loading="loading" class="page" data-test="transcript-workspace">
      <div class="panel page-section-compact">
        <div class="panel-head">
          <div>
            <el-button link type="primary" data-test="back-timeline" @click="backToTimeline">
              ← 返回时间轴
            </el-button>
            <h1>听记校对</h1>
            <div class="meta-row" v-if="timeline && round">
              <span class="meta-chip">
                候选人 <strong>{{ timeline.candidate_name }}</strong>
              </span>
              <span class="meta-chip">
                岗位 <strong>{{ timeline.job_name || '—' }}</strong>
              </span>
              <span class="meta-chip">
                轮次 <strong>{{ round.name }}</strong>
              </span>
              <span class="meta-chip">
                时间
                <strong>
                  {{
                    formatLocal(
                      round.current_schedule?.start_at_utc,
                      round.current_schedule?.timezone || 'Asia/Shanghai',
                    )
                  }}
                </strong>
              </span>
              <span class="meta-chip" data-test="version-chip">
                版本 <strong>{{ currentDetail?.version_label || '—' }}</strong>
              </span>
              <span class="meta-chip" data-test="status-chip">
                状态
                <strong>
                  {{
                    currentDetail?.version_type === 'DRAFT'
                      ? '校对中'
                      : currentDetail?.version_type === 'CONFIRMED'
                        ? '已确认'
                        : currentDetail?.version_type === 'ORIGINAL'
                          ? '原始只读'
                          : '—'
                  }}
                </strong>
              </span>
              <span class="meta-chip" data-test="save-status">
                保存 <strong>{{ saveStatus }}</strong>
              </span>
            </div>
          </div>
        </div>
      </div>

      <el-alert
        v-if="conflictOpen"
        :title="DRAFT_CONFLICT_HINT"
        type="warning"
        show-icon
        :closable="false"
        data-test="conflict-alert"
        style="margin-bottom: 12px"
      >
        <el-button data-test="refresh-latest" @click="refreshLatest">
          刷新最新版本
        </el-button>
      </el-alert>

      <div class="workspace">
        <aside class="history panel" data-test="version-history">
          <h3>版本历史</h3>
          <button
            v-for="item in sortedVersions"
            :key="item.id"
            type="button"
            class="version-item"
            :class="{ active: item.id === currentDetail?.id }"
            :data-test="`version-${item.version_label}`"
            @click="selectVersion(item.id)"
          >
            <strong>{{ item.version_label }}</strong>
            <span>{{ item.version_type }} · {{ item.segment_count }} 段</span>
          </button>
          <el-empty v-if="!sortedVersions.length" description="暂无版本" />
        </aside>

        <main class="center panel">
          <TranscriptSegmentEditor
            v-model:selected-local-id="selectedLocalId"
            :segments="segments"
            :readonly="!isEditable"
            @update:text="onTextUpdate"
            @merge="mergeSegment"
            @split="splitSegment"
            @remove="removeSegment"
            @insert-before="insertRelative($event, 'before')"
            @insert-after="insertRelative($event, 'after')"
            @move-up="moveSegment($event, -1)"
            @move-down="moveSegment($event, 1)"
          />
        </main>

        <aside class="side panel" data-test="segment-side">
          <h3>片段属性</h3>
          <template v-if="selectedSegment">
            <div class="form-item">
              <label>说话人标识</label>
              <el-input
                :model-value="selectedSegment.speaker_key"
                data-test="side-speaker-key"
                :disabled="!isEditable"
                @update:model-value="updateSelectedField('speaker_key', String($event))"
              />
            </div>
            <div class="form-item">
              <label>说话人</label>
              <el-input
                :model-value="selectedSegment.speaker_name"
                data-test="side-speaker-name"
                :disabled="!isEditable"
                @update:model-value="updateSelectedField('speaker_name', String($event))"
              />
            </div>
            <div class="form-item">
              <label>角色</label>
              <select
                class="native-select"
                data-test="side-speaker-role"
                :disabled="!isEditable"
                :value="selectedSegment.speaker_role"
                @change="
                  updateSelectedField(
                    'speaker_role',
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
            </div>
            <div class="form-item">
              <label>开始时间(ms)</label>
              <el-input
                :model-value="selectedSegment.start_time_ms ?? ''"
                data-test="side-start-ms"
                :disabled="!isEditable"
                @update:model-value="
                  updateSelectedField(
                    'start_time_ms',
                    $event === '' ? null : Number($event),
                  )
                "
              />
            </div>
            <div class="form-item">
              <label>结束时间(ms)</label>
              <el-input
                :model-value="selectedSegment.end_time_ms ?? ''"
                data-test="side-end-ms"
                :disabled="!isEditable"
                @update:model-value="
                  updateSelectedField(
                    'end_time_ms',
                    $event === '' ? null : Number($event),
                  )
                "
              />
            </div>
            <div class="form-item">
              <label>来源</label>
              <div data-test="side-source-type">{{ selectedSegment.source_type }}</div>
            </div>
            <label class="check-row">
              <input
                type="checkbox"
                data-test="side-unclear"
                :disabled="!isEditable"
                :checked="selectedSegment.is_unclear"
                @change="
                  updateSelectedField(
                    'is_unclear',
                    ($event.target as HTMLInputElement).checked,
                  )
                "
              />
              听不清
            </label>
            <label class="check-row">
              <input
                type="checkbox"
                data-test="side-include-analysis"
                :disabled="!isEditable"
                :checked="selectedSegment.is_included_in_analysis"
                @change="
                  updateSelectedField(
                    'is_included_in_analysis',
                    ($event.target as HTMLInputElement).checked,
                  )
                "
              />
              参与分析
            </label>
          </template>
          <el-empty v-else description="选择片段" />
        </aside>
      </div>

      <div class="bottom-bar" data-test="bottom-bar">
        <el-button data-test="back-btn" @click="backToTimeline">返回时间轴</el-button>
        <div class="spacer" />
        <el-button
          v-if="isEditable"
          type="primary"
          plain
          data-test="save-draft"
          :loading="saving"
          @click="saveDraft"
        >
          保存草稿
        </el-button>
        <el-button
          v-if="isEditable"
          type="primary"
          data-test="confirm-proofread"
          @click="openConfirm"
        >
          确认校对
        </el-button>
      </div>
    </div>

    <TranscriptConfirmDialog
      v-model:open="confirmOpen"
      :version-label="currentDetail?.version_label || ''"
      :segment-count="segments.length"
      :change-counts="changeCounts"
      :unknown-count="unknownCount"
      :unclear-count="unclearCount"
      :blockers="confirmBlockers"
      :confirming="confirming"
      @confirm="submitConfirm"
    />
  </AdminLayout>
</template>

<style scoped>
.page {
  max-width: 1280px;
  padding-bottom: 72px;
}
.panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 16px;
  margin-bottom: 14px;
}
.panel-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}
h1 {
  margin: 4px 0 10px;
  font-size: 22px;
}
h3 {
  margin: 0 0 10px;
  font-size: 15px;
}
.meta-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.meta-chip {
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 4px 10px;
  font-size: 12px;
  color: var(--muted);
}
.meta-chip strong {
  color: var(--fg);
  font-weight: 600;
}
.workspace {
  display: grid;
  grid-template-columns: 200px minmax(0, 1fr) 240px;
  gap: 12px;
  align-items: start;
}
.history,
.side,
.center {
  margin-bottom: 0;
  min-height: 480px;
}
.version-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  width: 100%;
  text-align: left;
  border: 1px solid var(--border);
  background: var(--bg);
  border-radius: 8px;
  padding: 8px;
  margin-bottom: 8px;
  cursor: pointer;
}
.version-item.active {
  border-color: var(--primary, #2563eb);
  background: var(--surface);
}
.version-item span {
  font-size: 12px;
  color: var(--muted);
}
.form-item {
  margin-bottom: 10px;
}
.form-item label {
  display: block;
  margin-bottom: 4px;
  font-size: 12px;
  color: var(--muted);
}
.native-select {
  width: 100%;
  height: 32px;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 0 8px;
  background: var(--surface);
}
.check-row {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  margin-bottom: 8px;
}
.bottom-bar {
  position: sticky;
  bottom: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 10px 12px;
  margin-top: 12px;
}
.spacer {
  flex: 1;
}
@media (max-width: 960px) {
  .workspace {
    grid-template-columns: 1fr;
  }
}
</style>
