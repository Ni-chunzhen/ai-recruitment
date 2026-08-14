<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { cancelAITask, listAITasks, retryAITask, type AITask } from '../api/aiTasks'
import {
  createResumeScoreTask,
  createScreeningDecision,
  getApplication,
  getScoreHistory,
  getScoreHistoryItem,
  getScoreReport,
  listScreeningReasonCodes,
  pollAITaskUntilDone,
  pipelineStatusLabel,
  type ApplicationOut,
  type ScoreReport,
  type ScreeningDecisionType,
  type ScreeningReasonCode,
  type ScreeningReasonCodeItem,
} from '../api/resumes'
import AdminLayout from '../layouts/AdminLayout.vue'

const AI_DISCLAIMER = 'AI辅助建议，最终筛选结果由招聘人员确认。'
const CONFLICT_HINT = '候选人状态已被其他人员更新，请刷新后重试'

const route = useRoute()
const router = useRouter()
const applicationId = computed(() => route.params.applicationId as string)

const loading = ref(false)
const scoring = ref(false)
const deciding = ref(false)
const cancelling = ref(false)
const report = ref<ScoreReport | null>(null)
const history = ref<ScoreReport[]>([])
const activeTask = ref<AITask | null>(null)
const application = ref<ApplicationOut | null>(null)
const viewingHistory = ref(false)
const lockVersion = ref(1)
const reason = ref('')
const reasonCode = ref<ScreeningReasonCode | ''>('')
const reasonCodes = ref<ScreeningReasonCodeItem[]>([])
const reasonCodesError = ref(false)
const conflictMessage = ref('')
let pollAbort: AbortController | null = null

const labels: Record<ScreeningDecisionType, string> = {
  enter_interview: '进入面试',
  hold: '待定',
  reject: '淘汰',
  talent_pool: '人才库',
}

const taskPending = computed(() => activeTask.value?.status === 'pending')
const taskRunning = computed(() => activeTask.value?.status === 'running')
const taskFailed = computed(() => activeTask.value?.status === 'failed')
const taskInvalid = computed(() => activeTask.value?.status === 'output_invalid')
const taskCancelled = computed(() => activeTask.value?.status === 'cancelled')

const pipelineStatus = computed(() => application.value?.pipeline_status)
const isTerminalPipeline = computed(
  () => pipelineStatus.value === 'rejected' || pipelineStatus.value === 'talent_pool',
)
const isInterviewing = computed(() => pipelineStatus.value === 'interviewing')
const applicationClosed = computed(
  () => Boolean(application.value) && application.value?.status !== 'in_progress',
)

const canScreen = computed(
  () =>
    Boolean(report.value?.is_current) &&
    !viewingHistory.value &&
    !isTerminalPipeline.value &&
    !applicationClosed.value,
)

const selectedReason = computed(() =>
  reasonCodes.value.find((item) => item.code === reasonCode.value),
)

function stopPoll() {
  pollAbort?.abort()
  pollAbort = null
}

function isConflictError(err: unknown) {
  return (err as { response?: { status?: number } })?.response?.status === 409
}

function errorDetail(err: unknown, fallback: string) {
  return (
    (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
    (err instanceof Error ? err.message : fallback)
  )
}

async function loadReport() {
  try {
    report.value = await getScoreReport(applicationId.value)
    lockVersion.value = report.value.lock_version || 1
    viewingHistory.value = false
  } catch {
    report.value = null
  }
}

async function loadHistory() {
  try {
    const hist = await getScoreHistory(applicationId.value)
    history.value = hist.items
  } catch {
    history.value = []
  }
}

async function loadLatestTask() {
  const { items } = await listAITasks('application', applicationId.value)
  activeTask.value =
    items.find((item) => item.task_type === 'RESUME_SCORE') ?? null
}

async function loadApplication() {
  application.value = await getApplication(applicationId.value)
  if (application.value.lock_version) {
    lockVersion.value = application.value.lock_version
  }
}

async function refreshBusinessContext() {
  conflictMessage.value = ''
  await Promise.all([
    loadApplication().catch(() => undefined),
    loadReport(),
    loadHistory(),
  ])
}

async function followTask(taskId: string) {
  stopPoll()
  pollAbort = new AbortController()
  scoring.value = true
  try {
    const task = await pollAITaskUntilDone(taskId, { signal: pollAbort.signal })
    activeTask.value = task
    if (task.status === 'succeeded') {
      ElMessage.success('评分完成')
      await Promise.all([loadReport(), loadHistory()])
    } else if (task.status === 'output_invalid') {
      ElMessage.error('AI结果格式异常，未生成正式报告')
    } else if (task.status === 'failed') {
      ElMessage.error(task.error_message || '评分失败')
    }
  } catch (err: unknown) {
    if ((err as { name?: string }).name === 'AbortError') return
    ElMessage.error(err instanceof Error ? err.message : '评分任务跟踪失败')
  } finally {
    scoring.value = false
  }
}

async function load() {
  loading.value = true
  reasonCodesError.value = false
  try {
    await Promise.all([
      loadReport(),
      loadHistory(),
      loadLatestTask(),
      listScreeningReasonCodes()
        .then((data) => {
          reasonCodes.value = data.items
          reasonCodesError.value = false
        })
        .catch(() => {
          reasonCodes.value = []
          reasonCodesError.value = true
        }),
      loadApplication().catch(() => {
        application.value = null
      }),
    ])
  } finally {
    loading.value = false
  }
  const status = activeTask.value?.status
  if (status === 'pending' || status === 'running') {
    await followTask(activeTask.value!.id)
  }
}

async function onScore() {
  scoring.value = true
  try {
    const created = await createResumeScoreTask(applicationId.value, {
      idempotency_key: report.value
        ? `score-${applicationId.value}-${Date.now()}`
        : `score-${applicationId.value}`,
    })
    ElMessage.success('评分任务已创建')
    activeTask.value = {
      ...(activeTask.value || ({} as AITask)),
      id: created.task_id,
      status: created.status as AITask['status'],
      task_type: 'RESUME_SCORE',
    }
    await followTask(created.task_id)
  } catch (err: unknown) {
    ElMessage.error(String(errorDetail(err, '发起评分失败')))
  } finally {
    scoring.value = false
  }
}

async function onRetry() {
  if (!activeTask.value) return
  scoring.value = true
  try {
    const task = await retryAITask(activeTask.value.id)
    ElMessage.success('已重新执行该评分任务')
    activeTask.value = task
    await followTask(task.id)
  } catch (err: unknown) {
    ElMessage.error(String(errorDetail(err, '重试失败')))
  } finally {
    scoring.value = false
  }
}

async function onCancel() {
  if (!activeTask.value) return
  cancelling.value = true
  try {
    const task = await cancelAITask(activeTask.value.id, 'user cancelled pending score')
    activeTask.value = task
    ElMessage.success('已取消待处理任务')
  } catch (err: unknown) {
    ElMessage.error(String(errorDetail(err, '取消失败')))
  } finally {
    cancelling.value = false
  }
}

async function openHistory(row: ScoreReport) {
  try {
    const item = await getScoreHistoryItem(applicationId.value, row.result_id)
    report.value = item
    viewingHistory.value = !item.is_current
  } catch (err: unknown) {
    ElMessage.error(String(errorDetail(err, '加载历史报告失败')))
  }
}

async function returnToCurrent() {
  await loadReport()
}

function decisionAllowed(decision: ScreeningDecisionType) {
  if (!canScreen.value || deciding.value) return false
  if (decision === 'enter_interview' && isInterviewing.value) return false
  return true
}

function reasonRequired(decision: ScreeningDecisionType) {
  return decision === 'reject' || decision === 'talent_pool'
}

async function onDecide(decision: ScreeningDecisionType) {
  if (!report.value) return
  if (!decisionAllowed(decision)) return
  if (reasonRequired(decision)) {
    if (reasonCodesError.value) {
      ElMessage.warning('原因选项加载失败')
      return
    }
    if (!reasonCode.value) {
      ElMessage.warning('淘汰和人才库必须选择原因')
      return
    }
    const allowed = selectedReason.value?.allowed_decisions || []
    if (allowed.length && !allowed.includes(decision)) {
      ElMessage.warning('当前原因不适用于该决定')
      return
    }
    if (selectedReason.value?.requires_description && !reason.value.trim()) {
      ElMessage.warning('选择「其他」时必须填写说明')
      return
    }
  }
  try {
    await ElMessageBox.confirm(
      `确认对 ${report.value.candidate_name} / ${report.value.job_name} 执行「${labels[decision]}」？\n当前评分版本 ${report.value.version_label}，目标状态：${labels[decision]}。\n此为人工决定，与 AI 建议无关。`,
      '人工筛选',
      { type: 'warning' },
    )
  } catch {
    return
  }
  deciding.value = true
  conflictMessage.value = ''
  try {
    const out = await createScreeningDecision(applicationId.value, {
      decision,
      reason_code: reasonCode.value || undefined,
      reason: reason.value || undefined,
      lock_version: lockVersion.value,
      idempotency_key: `screen-${applicationId.value}-${decision}-${lockVersion.value}`,
    })
    lockVersion.value = out.lock_version
    ElMessage.success(`已记录：${labels[decision]}`)
    try {
      await loadApplication()
    } catch {
      application.value = {
        ...(application.value || ({} as ApplicationOut)),
        id: applicationId.value,
        pipeline_status: out.to_pipeline_status,
        lock_version: out.lock_version,
      }
    }
  } catch (err: unknown) {
    if (isConflictError(err)) {
      conflictMessage.value = CONFLICT_HINT
      return
    }
    ElMessage.error(String(errorDetail(err, '操作失败')))
  } finally {
    deciding.value = false
  }
}

onMounted(() => {
  void load()
})

onBeforeUnmount(() => {
  stopPoll()
})
</script>

<template>
  <AdminLayout>
    <div v-loading="loading" class="page">
      <div class="panel-head">
        <div>
          <el-button link type="primary" @click="router.back()">← 返回</el-button>
          <h1>简历匹配报告</h1>
          <p class="sub" v-if="report">
            {{ report.candidate_name }} · {{ report.job_name }} · 岗位
            {{ report.job_version_label }} · 简历 {{ report.resume_version_label }} · 评分
            {{ report.version_label }}
            ·
            <span v-if="report.is_current">当前</span>
            <span v-else>历史</span>
            <span v-if="report.is_stale"> · 过期</span>
            · {{ report.created_at }}
          </p>
          <p class="sub" v-if="application">
            应聘状态：{{ pipelineStatusLabel[application.pipeline_status] || application.pipeline_status }}
          </p>
        </div>
        <div class="head-actions">
          <el-button v-if="viewingHistory" @click="returnToCurrent">返回当前报告</el-button>
          <el-button
            v-if="taskPending"
            :loading="cancelling"
            @click="onCancel"
          >
            取消任务
          </el-button>
          <el-button
            v-if="taskFailed || taskInvalid"
            type="warning"
            :loading="scoring"
            @click="onRetry"
          >
            人工重试
          </el-button>
          <el-button
            v-if="isInterviewing"
            @click="router.push({ name: 'application-interviews', params: { applicationId: applicationId } })"
          >
            面试安排
          </el-button>
          <el-button
            type="primary"
            :loading="scoring"
            :disabled="taskPending || taskRunning"
            @click="onScore"
          >
            {{ report ? '重新评分' : '发起评分' }}
          </el-button>
        </div>
      </div>

      <el-alert
        v-if="taskPending || taskRunning"
        title="评分处理中，离开页面后任务会继续执行"
        type="info"
        show-icon
        :closable="false"
        style="margin-bottom: 12px"
      />
      <el-alert
        v-if="taskCancelled"
        title="评分任务已取消，可重新发起评分。"
        type="info"
        show-icon
        :closable="false"
        style="margin-bottom: 12px"
      />
      <el-alert
        v-if="taskFailed"
        :title="activeTask?.error_message || '评分失败'"
        type="error"
        show-icon
        :closable="false"
        style="margin-bottom: 12px"
      />
      <el-alert
        v-if="taskInvalid"
        title="AI结果格式异常，未生成正式报告"
        type="error"
        show-icon
        :closable="false"
        style="margin-bottom: 12px"
      />
      <el-alert
        v-if="reasonCodesError"
        title="原因选项加载失败"
        type="error"
        show-icon
        :closable="false"
        style="margin-bottom: 12px"
      />
      <el-alert
        v-if="conflictMessage"
        :title="conflictMessage"
        type="warning"
        show-icon
        :closable="false"
        style="margin-bottom: 12px"
      >
        <el-button @click="refreshBusinessContext">刷新</el-button>
      </el-alert>

      <template v-if="report">
        <el-alert
          v-if="report.is_stale"
          title="该报告已因岗位版本迁移标记为过期，仍为当前有效结果，建议重新评分"
          type="warning"
          show-icon
          :closable="false"
          style="margin-bottom: 12px"
        />
        <el-alert
          v-if="report.information_insufficient"
          title="简历信息不足，请勿将缺失直接解释为能力不足"
          type="info"
          show-icon
          :closable="false"
          style="margin-bottom: 12px"
        />
        <el-alert
          v-for="warning in report.validation_warnings || []"
          :key="warning"
          :title="warning"
          type="warning"
          show-icon
          :closable="false"
          style="margin-bottom: 12px"
        />

        <div class="overview panel">
          <div class="score">
            <div class="label">加权总分（后端复算）</div>
            <div class="value">{{ report.calculated_total_score }}</div>
          </div>
          <div class="meta">
            <div>AI 原始总分：{{ report.model_total_score ?? '—' }}</div>
            <div>分差：{{ report.score_difference ?? '—' }}</div>
            <div>AI 建议：{{ report.recommendation || '—' }}</div>
            <div>分数带：{{ report.score_band || '—' }}</div>
            <div class="disclaimer">{{ AI_DISCLAIMER }}</div>
          </div>
        </div>

        <div class="panel" v-if="report.risks?.length">
          <h3>风险提示</h3>
          <ul>
            <li v-for="item in report.risks" :key="item">{{ item }}</li>
          </ul>
        </div>

        <div class="panel">
          <h3>维度明细</h3>
          <div v-for="d in report.dimensions" :key="d.name" class="dim-row">
            <div class="dim-head">
              <strong>{{ d.name }}</strong>
              <span>权重 {{ d.weight }} · 得分 {{ d.score }} · 贡献 {{ d.weighted_score ?? '—' }}</span>
            </div>
            <p class="desc">{{ d.description || '—' }}</p>
            <el-progress :percentage="Math.min(100, Math.max(0, d.score))" :stroke-width="8" />
            <p class="evidence">依据：{{ d.evidence || '—' }}</p>
            <p class="gap" v-if="d.gap">差距：{{ d.gap }}</p>
            <p class="gap" v-if="d.risk">风险：{{ d.risk }}</p>
          </div>
        </div>

        <div class="panel" v-if="report.must_have_check?.length || report.summary">
          <h3>必备条件与摘要</h3>
          <p class="desc">{{ report.summary || '—' }}</p>
          <ul v-if="report.must_have_check?.length">
            <li v-for="item in report.must_have_check" :key="item">{{ item }}</li>
          </ul>
        </div>

        <div class="panel" v-if="canScreen">
          <h3>人工筛选</h3>
          <el-select
            v-model="reasonCode"
            clearable
            placeholder="选择原因（淘汰 / 人才库必填）"
            style="width: 100%; margin-bottom: 12px"
          >
            <el-option
              v-for="opt in reasonCodes"
              :key="opt.code"
              :label="opt.label"
              :value="opt.code"
            />
          </el-select>
          <el-input
            v-model="reason"
            type="textarea"
            :rows="2"
            placeholder="说明（选择「其他」时必填）"
            style="margin-bottom: 12px"
          />
          <div class="decide-actions">
            <el-button
              type="primary"
              :loading="deciding"
              :disabled="!decisionAllowed('enter_interview')"
              @click="onDecide('enter_interview')"
            >
              进入面试
            </el-button>
            <el-button
              :loading="deciding"
              :disabled="!decisionAllowed('hold')"
              @click="onDecide('hold')"
            >
              待定
            </el-button>
            <el-button
              type="danger"
              plain
              :loading="deciding"
              :disabled="!decisionAllowed('reject')"
              @click="onDecide('reject')"
            >
              淘汰
            </el-button>
            <el-button
              plain
              :loading="deciding"
              :disabled="!decisionAllowed('talent_pool')"
              @click="onDecide('talent_pool')"
            >
              人才库
            </el-button>
          </div>
          <p class="hint">AI 建议不会自动改变应聘状态，必须由招聘人员确认。页面不会根据 AI 建议预选淘汰。</p>
        </div>

        <div class="panel" v-if="history.length">
          <h3>历史评分版本</h3>
          <el-table :data="history" size="small" @row-click="openHistory">
            <el-table-column prop="version_label" label="版本" width="80" />
            <el-table-column prop="calculated_total_score" label="后端复算总分" width="120" />
            <el-table-column prop="job_version_label" label="岗位版本" width="100" />
            <el-table-column prop="resume_version_label" label="简历版本" width="100" />
            <el-table-column label="状态" width="160">
              <template #default="{ row }">
                <span v-if="row.is_current">当前</span>
                <span v-else>历史</span>
                <span v-if="row.is_stale"> 过期</span>
              </template>
            </el-table-column>
            <el-table-column prop="created_at" label="时间" width="170" />
          </el-table>
          <p class="hint">点击行可查看该版本只读报告，历史评分不可删除。</p>
        </div>
      </template>

      <el-empty v-else description="尚无正式评分报告">
        <el-button
          type="primary"
          :loading="scoring"
          :disabled="taskPending || taskRunning"
          @click="onScore"
        >
          发起评分
        </el-button>
      </el-empty>
    </div>
  </AdminLayout>
</template>

<style scoped>
.page {
  max-width: 960px;
}
.panel-head {
  display: flex;
  justify-content: space-between;
  margin-bottom: 16px;
  gap: 12px;
}
.head-actions {
  display: flex;
  gap: 8px;
  align-items: flex-start;
}
h1 {
  margin: 4px 0 0;
  font-size: 24px;
}
.sub {
  margin: 6px 0 0;
  color: var(--muted);
  font-size: 13px;
}
.panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 16px;
  margin-bottom: 14px;
}
.overview {
  display: flex;
  gap: 24px;
  align-items: center;
}
.score .label {
  font-size: 12px;
  color: var(--muted);
}
.score .value {
  font-size: 36px;
  font-weight: 700;
  color: var(--accent);
}
.disclaimer {
  margin-top: 8px;
  font-size: 12px;
  color: var(--muted);
}
.dim-row {
  padding: 12px 0;
  border-bottom: 1px solid var(--border);
}
.dim-row:last-child {
  border-bottom: 0;
}
.dim-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  font-size: 13px;
}
.desc,
.evidence,
.gap,
.hint {
  font-size: 12px;
  color: var(--muted);
  margin: 6px 0;
}
.decide-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
</style>
