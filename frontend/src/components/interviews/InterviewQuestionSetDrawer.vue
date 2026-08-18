<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, ref, watch } from 'vue'

import {
  confirmQuestionSet,
  createQuestionSetVersion,
  generateQuestionSet,
  getQuestionSet,
  getQuestionSetVersion,
  type InterviewQuestionItemWrite,
  type InterviewQuestionSet,
  type InterviewQuestionVersionDetail,
  type InterviewQuestionEvidenceSource,
} from '../../api/interviewAi'
import type { InterviewRound } from '../../api/interviews'

const DISPATCH_HINT = '任务已创建；待投递/处理中'
const MUTATION_STATUSES = new Set(['SCHEDULED', 'CONFIRMED', 'IN_PROGRESS'])
const EVIDENCE_OPTIONS: Array<{
  value: InterviewQuestionEvidenceSource
  label: string
}> = [
  { value: 'JOB_REQUIREMENT', label: '岗位要求' },
  { value: 'RESUME_EXPERIENCE', label: '简历经历' },
  { value: 'GENERAL', label: '通用' },
]

const props = defineProps<{
  open: boolean
  round: InterviewRound | null
  canManage: boolean
}>()

const emit = defineEmits<{
  'update:open': [value: boolean]
}>()

const loading = ref(false)
const saving = ref(false)
const questionSet = ref<InterviewQuestionSet | null>(null)
const detail = ref<InterviewQuestionVersionDetail | null>(null)
const selectedId = ref<string | null>(null)
const conflictMessage = ref('')
const dispatchMessage = ref('')
const editOpen = ref(false)
const editorItems = ref<InterviewQuestionItemWrite[]>([])

const canMutate = computed(
  () =>
    props.canManage &&
    Boolean(props.round) &&
    MUTATION_STATUSES.has(String(props.round?.status)),
)

function newIdempotencyKey(prefix: string) {
  return `${prefix}-${props.round?.id || 'round'}-${Date.now()}-${Math.random()
    .toString(36)
    .slice(2, 8)}`
}

function errorDetail(err: unknown, fallback: string) {
  return (
    (err as { response?: { data?: { detail?: string } } })?.response?.data
      ?.detail || (err instanceof Error ? err.message : fallback)
  )
}

function isConflictError(err: unknown) {
  return (err as { response?: { status?: number } })?.response?.status === 409
}

function formatTime(value: string | null | undefined) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date)
}

function sourceLabel(sourceType: string) {
  if (sourceType === 'AI_GENERATED') return 'AI 生成'
  if (sourceType === 'MANUAL_EDIT') return '人工编辑'
  return sourceType
}

async function loadList() {
  if (!props.round) return
  loading.value = true
  conflictMessage.value = ''
  try {
    questionSet.value = await getQuestionSet(props.round.id)
    if (
      selectedId.value &&
      !questionSet.value.versions.some((item) => item.id === selectedId.value)
    ) {
      selectedId.value = null
      detail.value = null
    }
  } catch (err) {
    questionSet.value = null
    ElMessage.error(String(errorDetail(err, '加载题纲失败')))
  } finally {
    loading.value = false
  }
}

async function loadVersion(versionId: string) {
  if (!props.round) return
  try {
    detail.value = await getQuestionSetVersion(props.round.id, versionId)
    selectedId.value = versionId
  } catch (err) {
    ElMessage.error(String(errorDetail(err, '加载题纲详情失败')))
  }
}

async function onGenerate() {
  if (!props.round || !canMutate.value) return
  saving.value = true
  try {
    await generateQuestionSet(props.round.id, {
      idempotency_key: newIdempotencyKey('question-gen'),
    })
    dispatchMessage.value = DISPATCH_HINT
    ElMessage.info(DISPATCH_HINT)
    await loadList()
  } catch (err) {
    if (isConflictError(err)) {
      conflictMessage.value = String(errorDetail(err, '请刷新后重试'))
      ElMessage.error(conflictMessage.value)
      return
    }
    ElMessage.error(String(errorDetail(err, '生成题纲失败')))
  } finally {
    saving.value = false
  }
}

function toWriteItems(
  items: InterviewQuestionVersionDetail['items'],
): InterviewQuestionItemWrite[] {
  return items.map((item) => ({
    dimension_key: item.dimension_key,
    question: item.question,
    purpose: item.purpose,
    evidence_source: item.evidence_source as InterviewQuestionEvidenceSource,
    resume_evidence: item.resume_evidence,
    follow_up_prompts: [...item.follow_up_prompts],
    risk_flags: [...item.risk_flags],
    display_order: item.display_order,
  }))
}

async function startEdit() {
  if (!props.round || !canMutate.value || !questionSet.value?.current_version_id) {
    return
  }
  const currentId = questionSet.value.current_version_id
  if (detail.value?.id !== currentId) {
    await loadVersion(currentId)
  }
  if (!detail.value) return
  editorItems.value = toWriteItems(detail.value.items)
  editOpen.value = true
}

async function submitEdit() {
  if (!props.round || !questionSet.value?.current_version_id) return
  saving.value = true
  try {
    await createQuestionSetVersion(props.round.id, {
      idempotency_key: newIdempotencyKey('question-edit'),
      expected_current_version_id: questionSet.value.current_version_id,
      questions: editorItems.value,
    })
    editOpen.value = false
    await loadList()
  } catch (err) {
    if (isConflictError(err)) {
      conflictMessage.value = String(errorDetail(err, '请刷新后重试'))
      ElMessage.error(conflictMessage.value)
      return
    }
    ElMessage.error(String(errorDetail(err, '保存题纲失败')))
  } finally {
    saving.value = false
  }
}

async function onConfirm() {
  if (!props.round || !canMutate.value || !questionSet.value?.current_version_id) {
    return
  }
  saving.value = true
  try {
    await confirmQuestionSet(props.round.id, {
      idempotency_key: newIdempotencyKey('question-confirm'),
      expected_current_version_id: questionSet.value.current_version_id,
    })
    await loadList()
  } catch (err) {
    if (isConflictError(err)) {
      conflictMessage.value = String(errorDetail(err, '请刷新后重试'))
      ElMessage.error(conflictMessage.value)
      return
    }
    ElMessage.error(String(errorDetail(err, '确认题纲失败')))
  } finally {
    saving.value = false
  }
}

watch(
  () => [props.open, props.round?.id] as const,
  async ([open]) => {
    if (open && props.round) {
      selectedId.value = null
      detail.value = null
      dispatchMessage.value = ''
      conflictMessage.value = ''
      await loadList()
    }
  },
  { immediate: true },
)

const currentLabel = computed(() => {
  const currentId = questionSet.value?.current_version_id
  return (
    questionSet.value?.versions.find((item) => item.id === currentId)
      ?.version_label || '—'
  )
})
</script>

<template>
  <el-drawer
    :model-value="open"
    size="760px"
    title="面试题纲"
    data-test="question-set-drawer"
    @update:model-value="emit('update:open', $event)"
  >
    <div v-loading="loading" class="ai-drawer">
      <el-alert
        v-if="conflictMessage"
        :title="conflictMessage"
        type="warning"
        show-icon
        :closable="false"
      >
        <el-button data-test="refresh-question-set" @click="loadList">刷新</el-button>
      </el-alert>
      <div v-if="dispatchMessage" class="dispatch-hint" data-test="dispatch-status">
        {{ dispatchMessage }}
      </div>
      <div class="summary">
        <div>状态 {{ questionSet?.status || '—' }}</div>
        <div>当前版本 {{ currentLabel }}</div>
        <div>确认人 {{ questionSet?.confirmed_by || '—' }}</div>
        <div>确认时间 {{ formatTime(questionSet?.confirmed_at) }}</div>
        <el-button
          v-if="canMutate"
          type="primary"
          size="small"
          data-test="generate-question-set"
          :loading="saving"
          @click="onGenerate"
        >
          生成题纲
        </el-button>
        <el-button
          v-if="canMutate && questionSet?.current_version_id"
          size="small"
          data-test="edit-question-set"
          :loading="saving"
          @click="startEdit"
        >
          基于当前版本编辑
        </el-button>
        <el-button
          v-if="canMutate && questionSet?.current_version_id"
          type="primary"
          plain
          size="small"
          data-test="confirm-question-set"
          :loading="saving"
          @click="onConfirm"
        >
          确认 READY
        </el-button>
      </div>

      <div class="body">
        <div class="version-list">
          <button
            v-for="item in questionSet?.versions || []"
            :key="item.id"
            type="button"
            class="version-item"
            :class="{ active: item.id === selectedId }"
            :data-test="`question-version-${item.id}`"
            @click="loadVersion(item.id)"
          >
            <div class="version-name">{{ item.version_label }}</div>
            <div class="version-meta">
              {{ sourceLabel(item.source_type) }}
              · {{ item.question_count }} 题
              · {{ item.is_current ? '当前' : '历史' }}
            </div>
          </button>
          <el-empty v-if="!(questionSet?.versions || []).length" description="尚未生成题纲" />
        </div>

        <div v-if="detail" class="detail-pane" data-test="question-detail">
          <div class="meta-row">
            {{ detail.version_label }} · {{ sourceLabel(detail.source_type) }}
            · {{ detail.is_current ? '当前' : '历史' }}
          </div>
          <div v-for="item in detail.items" :key="item.id" class="question-card">
            <div class="question-head">
              {{ item.display_order }}. {{ item.dimension_key }}
            </div>
            <p>{{ item.question }}</p>
            <p class="muted">用途 {{ item.purpose }}</p>
            <p v-if="item.resume_evidence" class="muted">
              简历依据 {{ item.resume_evidence }}
            </p>
            <p v-if="item.follow_up_prompts.length" class="muted">
              追问 {{ item.follow_up_prompts.join('、') }}
            </p>
          </div>
        </div>
      </div>
    </div>

    <el-dialog v-model="editOpen" title="编辑题纲" width="640px">
      <div v-for="(item, index) in editorItems" :key="index" class="edit-card">
        <div class="form-item">
          <label>题干</label>
          <el-input v-model="item.question" type="textarea" :rows="3" />
        </div>
        <div class="form-item">
          <label>用途</label>
          <el-input v-model="item.purpose" type="textarea" :rows="2" />
        </div>
        <div class="form-item">
          <label>证据来源</label>
          <el-select v-model="item.evidence_source">
            <el-option
              v-for="option in EVIDENCE_OPTIONS"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            />
          </el-select>
        </div>
      </div>
      <template #footer>
        <el-button @click="editOpen = false">取消</el-button>
        <el-button
          type="primary"
          data-test="submit-question-edit"
          :loading="saving"
          @click="submitEdit"
        >
          保存为新版本
        </el-button>
      </template>
    </el-dialog>
  </el-drawer>
</template>

<style scoped>
.ai-drawer {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.summary {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  padding: 10px 12px;
  background: #f5f7fa;
  border: 1px solid #e5e7eb;
  font-size: 13px;
  color: #4b5563;
}
.body {
  display: grid;
  grid-template-columns: 220px 1fr;
  gap: 12px;
  min-height: 420px;
}
.version-list {
  border: 1px solid #e5e7eb;
  background: #fff;
  overflow: auto;
}
.version-item {
  display: block;
  width: 100%;
  text-align: left;
  border: 0;
  border-bottom: 1px solid #f0f2f5;
  background: #fff;
  padding: 10px 12px;
  cursor: pointer;
}
.version-item.active {
  background: #ecf5ff;
}
.version-name {
  font-weight: 600;
}
.version-meta,
.muted,
.meta-row {
  font-size: 12px;
  color: #6b7280;
}
.detail-pane,
.question-card,
.edit-card {
  border: 1px solid #e5e7eb;
  background: #fff;
  padding: 12px;
}
.question-card,
.edit-card {
  margin-bottom: 10px;
}
.question-head {
  font-weight: 600;
  margin-bottom: 6px;
}
.dispatch-hint {
  color: #b45309;
  background: #fffbeb;
  border: 1px solid #fde68a;
  padding: 8px 10px;
  font-size: 13px;
}
.form-item {
  margin-bottom: 10px;
}
.form-item label {
  display: block;
  margin-bottom: 4px;
  font-size: 12px;
  color: #6b7280;
}
</style>
