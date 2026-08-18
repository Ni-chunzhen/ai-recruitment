<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, ref, watch } from 'vue'

import {
  generateRoundAnalysis,
  getRoundAnalysis,
  getRoundAnalysisVersion,
  type InterviewAnalysisSet,
  type InterviewAnalysisVersionDetail,
} from '../../api/interviewAi'
import type { InterviewRound } from '../../api/interviews'

const DISPATCH_HINT = '任务已创建；待投递/处理中'
const STALE_HINT = '转写已更新，此分析基于旧确认转写'

const props = defineProps<{
  open: boolean
  round: InterviewRound | null
  canManage: boolean
  hasConfirmedTranscript: boolean
}>()

const emit = defineEmits<{
  'update:open': [value: boolean]
}>()

const loading = ref(false)
const saving = ref(false)
const analysisSet = ref<InterviewAnalysisSet | null>(null)
const detail = ref<InterviewAnalysisVersionDetail | null>(null)
const selectedId = ref<string | null>(null)
const conflictMessage = ref('')
const dispatchMessage = ref('')

const canGenerate = computed(
  () =>
    props.canManage &&
    props.hasConfirmedTranscript &&
    props.round?.status === 'COMPLETED',
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

function formatScore(value: string | number | null | undefined) {
  if (value == null || value === '') return '—'
  return String(value)
}

async function loadList() {
  if (!props.round) return
  loading.value = true
  conflictMessage.value = ''
  try {
    analysisSet.value = await getRoundAnalysis(props.round.id)
    if (
      selectedId.value &&
      !analysisSet.value.versions.some((item) => item.version_id === selectedId.value)
    ) {
      selectedId.value = null
      detail.value = null
    }
  } catch (err) {
    analysisSet.value = null
    ElMessage.error(String(errorDetail(err, '加载单轮分析失败')))
  } finally {
    loading.value = false
  }
}

async function loadVersion(versionId: string) {
  if (!props.round) return
  try {
    detail.value = await getRoundAnalysisVersion(props.round.id, versionId)
    selectedId.value = versionId
  } catch (err) {
    ElMessage.error(String(errorDetail(err, '加载分析详情失败')))
  }
}

async function onGenerate() {
  if (!props.round || !canGenerate.value) return
  saving.value = true
  try {
    await generateRoundAnalysis(props.round.id, {
      idempotency_key: newIdempotencyKey('analysis-gen'),
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
    ElMessage.error(String(errorDetail(err, '生成单轮分析失败')))
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
</script>

<template>
  <el-drawer
    :model-value="open"
    size="760px"
    title="单轮分析"
    data-test="analysis-drawer"
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
        <el-button data-test="refresh-analysis" @click="loadList">刷新</el-button>
      </el-alert>
      <div v-if="dispatchMessage" class="dispatch-hint" data-test="dispatch-status">
        {{ dispatchMessage }}
      </div>
      <div class="summary">
        <div>当前版本 {{ analysisSet?.current_version_id ? '已有版本' : '—' }}</div>
        <el-button
          v-if="canGenerate"
          type="primary"
          size="small"
          data-test="generate-round-analysis"
          :loading="saving"
          @click="onGenerate"
        >
          生成单轮分析
        </el-button>
      </div>

      <div class="body">
        <div class="version-list">
          <button
            v-for="item in analysisSet?.versions || []"
            :key="item.version_id"
            type="button"
            class="version-item"
            :class="{ active: item.version_id === selectedId }"
            :data-test="`analysis-version-${item.version_id}`"
            @click="loadVersion(item.version_id)"
          >
            <div class="version-name">{{ item.version_label }}</div>
            <div class="version-meta">
              转写 {{ item.transcript_version_label || item.transcript_version_id }}
              · 总分 {{ formatScore(item.overall_score) }}
              · {{ item.dimension_count }} 维 / {{ item.evidence_count }} 证
              · {{ item.is_current ? '当前' : '历史' }}
            </div>
            <el-tag
              v-if="item.is_stale"
              size="small"
              data-test="analysis-stale-tag"
            >
              STALE
            </el-tag>
          </button>
          <el-empty v-if="!(analysisSet?.versions || []).length" description="尚未生成单轮分析" />
        </div>

        <div v-if="detail" class="detail-pane" data-test="analysis-detail">
          <div
            v-if="detail.is_stale"
            class="stale-hint"
            data-test="analysis-stale-hint"
          >
            {{ STALE_HINT }}
          </div>
          <div class="meta-row">
            {{ detail.version_label }} · 总分 {{ formatScore(detail.overall_score) }}
            · {{ detail.is_current ? '当前' : '历史' }}
          </div>
          <p>{{ detail.overall_summary }}</p>
          <div
            v-for="dimension in detail.dimensions"
            :key="dimension.id"
            class="dimension-card"
          >
            <div class="question-head">
              {{ dimension.display_order }}. {{ dimension.dimension_name }}
              （{{ dimension.dimension_key }}）
              评分 {{ dimension.score ?? '—' }}
            </div>
            <p>{{ dimension.analysis }}</p>
            <p v-if="dimension.insufficient_information" class="muted">
              信息不足 {{ dimension.insufficient_information }}
            </p>
            <div v-if="dimension.evidence.length" class="evidence-list">
              <div
                v-for="item in dimension.evidence"
                :key="item.id"
                class="evidence-item"
              >
                片段 {{ item.segment_no }}：{{ item.quote }}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
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
  grid-template-columns: 240px 1fr;
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
  margin: 4px 0;
}
.detail-pane,
.dimension-card {
  border: 1px solid #e5e7eb;
  background: #fff;
  padding: 12px;
}
.dimension-card {
  margin-top: 10px;
}
.question-head {
  font-weight: 600;
  margin-bottom: 6px;
}
.dispatch-hint,
.stale-hint {
  color: #b45309;
  background: #fffbeb;
  border: 1px solid #fde68a;
  padding: 8px 10px;
  font-size: 13px;
}
.evidence-item {
  font-size: 13px;
  margin-top: 6px;
  padding: 8px;
  background: #f9fafb;
  border: 1px solid #e5e7eb;
}
</style>
