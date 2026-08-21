<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import {
  getCandidateCenterApplicationDetail,
  type CandidateCenterDetail,
  type CandidateCenterRound,
} from '../api/candidateCenter'
import {
  createHiringDecision,
  listHiringDecisions,
  listHiringReasonCodes,
  type HiringDecisionOut,
  type HiringDecisionType,
  type HiringReasonCodeItem,
} from '../api/hiringDecisions'
import {
  generateComprehensiveAnalysis,
  getComprehensiveAnalysis,
  getComprehensiveAnalysisVersion,
  type ComprehensiveSet,
  type ComprehensiveVersionDetail,
  type CoverageReport,
} from '../api/comprehensiveAnalysis'
import { getRoundAnalysis } from '../api/interviewAi'
import AdminLayout from '../layouts/AdminLayout.vue'
import { pipelineStatusLabel, type PipelineStatus } from '../api/resumes'
import { useAuthStore } from '../stores/auth'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()

const loading = ref(false)
const loadFailed = ref(false)
const detail = ref<CandidateCenterDetail | null>(null)
const history = ref<HiringDecisionOut[]>([])
const reasonCatalog = ref<HiringReasonCodeItem[]>([])
const validAnalysisVersionId = ref<string | null>(null)
const analysisLoadError = ref('')

const dialogVisible = ref(false)
const pendingDecision = ref<HiringDecisionType | null>(null)
const selectedReasonCode = ref('')
const submitting = ref(false)

const comprehensiveSet = ref<ComprehensiveSet | null>(null)
const comprehensiveDetail = ref<ComprehensiveVersionDetail | null>(null)
const comprehensiveLoading = ref(false)
const comprehensiveError = ref('')
const comprehensiveGenerating = ref(false)

const candidateId = computed(() => route.params.candidateId as string)
const applicationId = computed(() => route.params.applicationId as string)
const hasCurrentDetail = computed(
  () =>
    detail.value?.application_id === applicationId.value &&
    detail.value?.candidate_id === candidateId.value,
)

const canManage = computed(() => authStore.hasPermission('recruitment.manage'))
const canWriteHiring = computed(
  () =>
    canManage.value &&
    detail.value?.pipeline_status === 'interviewing' &&
    detail.value?.status === 'in_progress',
)
const canGenerateComprehensive = computed(
  () =>
    canManage.value &&
    detail.value?.pipeline_status === 'interviewing' &&
    detail.value?.status === 'in_progress',
)
const comprehensiveWriteHint = computed(() => {
  if (!canManage.value || !detail.value) return ''
  if (detail.value.pipeline_status === 'pending_offer') {
    return '当前流程已进入后续阶段，综合分析仅可只读查看历史版本。'
  }
  if (!canGenerateComprehensive.value) {
    return '当前应聘状态不满足生成条件，综合分析写操作已禁用。'
  }
  const current = comprehensiveSet.value?.versions.find((item) => item.is_current)
  if (current?.is_stale) {
    return '当前综合版本已过期（引用单轮分析变更），可重新生成辅助版本。'
  }
  return ''
})
const comprehensiveFreshnessLabel = computed(() => {
  if (comprehensiveLoading.value) return '加载中'
  if (!comprehensiveDetail.value) return '暂无可验证状态'
  if (comprehensiveDetail.value.is_stale === true) return '已过期'
  if (comprehensiveDetail.value.is_stale === false) return '有效'
  return '暂无可验证状态'
})
const currentCoverage = computed((): CoverageReport | null => {
  const report =
    comprehensiveDetail.value?.coverage_report ||
    comprehensiveSet.value?.versions.find((item) => item.is_current)?.coverage_report
  if (!report || typeof report !== 'object') return null
  return report as CoverageReport
})
const dimensionSummaryRows = computed(() => {
  const refs = comprehensiveDetail.value?.round_refs || []
  const rows: Array<{
    round_sequence: number
    dimension_key: string
    dimension_name: string
    score: number | string | null
  }> = []
  for (const ref of refs) {
    for (const dim of ref.dimensions || []) {
      rows.push({
        round_sequence: ref.sequence_no,
        dimension_key: dim.dimension_key,
        dimension_name: dim.dimension_name || dim.dimension_key,
        score: dim.score ?? null,
      })
    }
  }
  return rows
})
const hasValidAnalysis = computed(() => Boolean(validAnalysisVersionId.value))
const writeDisabled = computed(() => !hasValidAnalysis.value)
const disabledReason = computed(() => {
  if (!canWriteHiring.value) return ''
  if (analysisLoadError.value) return analysisLoadError.value
  if (!hasValidAnalysis.value) return '无当前有效且非过期的单轮分析版本，无法决策'
  return ''
})

const decisionLabel: Record<HiringDecisionType, string> = {
  recommend_hire: '录用建议',
  reject: '淘汰',
  hold: '待定',
}

const reasonOptions = computed(() => {
  if (!pendingDecision.value) return []
  return reasonCatalog.value.filter((item) =>
    item.allowed_decisions.includes(pendingDecision.value as string),
  )
})

function pipelineText(status: string) {
  return pipelineStatusLabel[status as PipelineStatus] || status
}

function newIdempotencyKey() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `hd-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

async function resolveValidAnalysis(rounds: CandidateCenterRound[]) {
  validAnalysisVersionId.value = null
  analysisLoadError.value = ''
  for (const round of rounds) {
    try {
      const set = await getRoundAnalysis(round.round_id)
      const current = set.versions.find((item) => item.is_current && !item.is_stale)
      if (current) {
        validAnalysisVersionId.value = current.version_id
        return
      }
    } catch {
      // try next round
    }
  }
  if (rounds.length) {
    analysisLoadError.value = '无当前有效且非过期的单轮分析版本，无法决策'
  } else {
    analysisLoadError.value = '暂无面试轮次，无法选择分析版本'
  }
}

async function loadHistory() {
  if (!canManage.value) {
    history.value = []
    return
  }
  try {
    const data = await listHiringDecisions(applicationId.value)
    history.value = data.items
  } catch {
    history.value = []
  }
}

async function loadReasonCatalog() {
  if (!canManage.value) return
  try {
    const data = await listHiringReasonCodes()
    reasonCatalog.value = data.items
  } catch {
    reasonCatalog.value = []
  }
}

async function loadComprehensive() {
  if (!canManage.value) {
    comprehensiveSet.value = null
    comprehensiveDetail.value = null
    comprehensiveError.value = ''
    return
  }
  comprehensiveLoading.value = true
  comprehensiveError.value = ''
  try {
    const set = await getComprehensiveAnalysis(applicationId.value)
    comprehensiveSet.value = set
    const focusId =
      set.current_version_id ||
      set.versions.find((item) => item.is_current)?.version_id ||
      set.versions[0]?.version_id ||
      null
    if (focusId) {
      try {
        comprehensiveDetail.value = await getComprehensiveAnalysisVersion(
          applicationId.value,
          focusId,
        )
      } catch {
        comprehensiveDetail.value = null
      }
    } else {
      comprehensiveDetail.value = null
    }
  } catch {
    comprehensiveSet.value = null
    comprehensiveDetail.value = null
    comprehensiveError.value = '综合分析加载失败'
  } finally {
    comprehensiveLoading.value = false
  }
}

async function onGenerateComprehensive() {
  if (!canGenerateComprehensive.value || comprehensiveGenerating.value) return
  comprehensiveGenerating.value = true
  try {
    await generateComprehensiveAnalysis(applicationId.value, {
      idempotency_key: newIdempotencyKey(),
    })
    ElMessage.success('综合分析任务已入队')
    await loadComprehensive()
  } catch (err: unknown) {
    const status = (err as { response?: { status?: number } })?.response?.status
    if (status === 403) {
      ElMessage.error('无权限生成综合分析')
    } else if (status === 409) {
      ElMessage.error('当前状态不可生成，请刷新后重试')
    } else {
      ElMessage.error('生成综合分析失败')
    }
  } finally {
    comprehensiveGenerating.value = false
  }
}

async function loadDetail() {
  loading.value = true
  loadFailed.value = false
  detail.value = null
  try {
    detail.value = await getCandidateCenterApplicationDetail(
      candidateId.value,
      applicationId.value,
    )
    if (canManage.value) {
      await Promise.all([
        loadHistory(),
        loadReasonCatalog(),
        resolveValidAnalysis(detail.value.rounds || []),
        loadComprehensive(),
      ])
    } else {
      comprehensiveSet.value = null
      comprehensiveDetail.value = null
    }
  } catch {
    loadFailed.value = true
    ElMessage.error('加载详情失败')
  } finally {
    loading.value = false
  }
}

function openDecisionDialog(decision: HiringDecisionType) {
  if (!canWriteHiring.value || writeDisabled.value) return
  pendingDecision.value = decision
  selectedReasonCode.value = ''
  dialogVisible.value = true
}

async function submitDecision() {
  if (!detail.value || !pendingDecision.value || !validAnalysisVersionId.value) return
  if (!selectedReasonCode.value) {
    ElMessage.warning('请选择固定原因码')
    return
  }
  submitting.value = true
  try {
    const out = await createHiringDecision(detail.value.application_id, {
      decision: pendingDecision.value,
      reason_code: selectedReasonCode.value,
      analysis_version_id: validAnalysisVersionId.value,
      lock_version: detail.value.lock_version,
      idempotency_key: newIdempotencyKey(),
    })
    dialogVisible.value = false
    ElMessage.success('决策已保存')
    detail.value = {
      ...detail.value,
      lock_version: out.lock_version,
      pipeline_status: out.to_pipeline_status,
      status:
        out.decision === 'reject' ? 'rejected' : detail.value.status,
    }
    await loadDetail()
  } catch (err: unknown) {
    const status = (err as { response?: { status?: number } })?.response?.status
    if (status === 403) {
      ElMessage.error('无权限执行面后决策')
    } else if (status === 409) {
      ElMessage.error('状态冲突，请刷新后重试')
      await loadDetail()
    } else {
      ElMessage.error('提交决策失败')
    }
  } finally {
    submitting.value = false
  }
}

function goResumeReview() {
  if (!detail.value?.resume_summary?.resume_version_id) return
  void router.push({
    name: 'resume-review',
    params: { versionId: detail.value.resume_summary.resume_version_id },
  })
}

function goScoreReport() {
  if (!detail.value?.application_id) return
  void router.push({
    name: 'score-report',
    params: { applicationId: detail.value.application_id },
  })
}

function goInterviews() {
  if (!detail.value?.application_id) return
  void router.push({
    name: 'application-interviews',
    params: { applicationId: detail.value.application_id },
  })
}

function goTranscript(round: CandidateCenterRound) {
  void router.push({
    name: 'interview-transcript',
    params: { roundId: round.round_id },
  })
}

function goOtherApplication(otherApplicationId: string) {
  void router.push({
    name: 'candidate-center-detail',
    params: {
      candidateId: candidateId.value,
      applicationId: otherApplicationId,
    },
  })
}

watch([candidateId, applicationId], () => {
  void loadDetail()
})

onMounted(() => {
  void loadDetail()
})
</script>

<template>
  <AdminLayout>
    <div v-loading="loading" class="page">
      <div class="panel-head">
        <el-button link type="primary" @click="router.push({ name: 'candidate-center' })">
          ← 返回候选人中心
        </el-button>
      </div>

      <el-alert
        v-if="loadFailed"
        title="加载详情失败"
        type="error"
        show-icon
        :closable="false"
        style="margin-bottom: 12px"
      />

      <el-empty
        v-if="!loading && !loadFailed && !hasCurrentDetail"
        description="暂无详情数据"
        style="margin-top: 20px"
      />

      <template v-if="hasCurrentDetail && detail">
        <section class="panel">
          <h2>候选人信息</h2>
          <p>{{ detail.name }} · {{ detail.phone || '—' }} · {{ detail.email || '—' }}</p>
          <p>{{ detail.job_name }}（{{ detail.job_code }}）</p>
          <p>应聘状态：{{ detail.status }} · 流程状态：{{ pipelineText(detail.pipeline_status) }}</p>
        </section>

        <section v-if="canManage" class="panel" data-test="hiring-decision-panel">
          <h2>面后人工决策</h2>
          <p v-if="detail.pipeline_status === 'pending_offer'" class="hint">
            当前为录用建议待后续，不提供发送能力。
          </p>
          <div v-if="canWriteHiring" class="hiring-actions">
            <el-button
              data-test="hiring-recommend-hire"
              type="primary"
              plain
              :disabled="writeDisabled"
              @click="openDecisionDialog('recommend_hire')"
            >
              录用建议
            </el-button>
            <el-button
              data-test="hiring-reject"
              type="danger"
              plain
              :disabled="writeDisabled"
              @click="openDecisionDialog('reject')"
            >
              淘汰
            </el-button>
            <el-button
              data-test="hiring-hold"
              plain
              :disabled="writeDisabled"
              @click="openDecisionDialog('hold')"
            >
              待定
            </el-button>
          </div>
          <p
            v-if="canWriteHiring && disabledReason"
            data-test="hiring-disabled-reason"
            class="hint warn"
          >
            {{ disabledReason }}
          </p>

          <el-table
            data-test="hiring-decision-history"
            :data="history"
            stripe
            style="margin-top: 12px"
          >
            <el-table-column label="时间" min-width="160">
              <template #default="{ row }">{{ row.created_at }}</template>
            </el-table-column>
            <el-table-column label="决策" width="120">
              <template #default="{ row }">
                {{ decisionLabel[row.decision as HiringDecisionType] || row.decision }}
              </template>
            </el-table-column>
            <el-table-column prop="reason_code" label="原因码" min-width="160" />
            <el-table-column label="分数" width="90">
              <template #default="{ row }">{{ row.overall_score ?? '—' }}</template>
            </el-table-column>
            <el-table-column label="流水" min-width="180">
              <template #default="{ row }">
                {{ pipelineText(row.from_pipeline_status) }} →
                {{ pipelineText(row.to_pipeline_status) }}
              </template>
            </el-table-column>
          </el-table>
        </section>

        <section v-if="canManage" class="panel" data-test="comprehensive-analysis-panel">
          <h2>综合面试分析（辅助）</h2>
          <p class="hint">结构化参考结果，不触发流程状态变更。</p>
          <p
            v-if="comprehensiveWriteHint"
            data-test="comprehensive-write-hint"
            class="hint warn"
          >
            {{ comprehensiveWriteHint }}
          </p>
          <div class="hiring-actions">
            <el-button
              v-if="canGenerateComprehensive"
              data-test="comprehensive-generate"
              type="primary"
              plain
              :loading="comprehensiveGenerating"
              @click="onGenerateComprehensive"
            >
              生成综合分析
            </el-button>
            <el-button
              data-test="comprehensive-refresh"
              plain
              :loading="comprehensiveLoading"
              @click="loadComprehensive"
            >
              刷新
            </el-button>
          </div>
          <p v-if="comprehensiveError" class="hint warn">{{ comprehensiveError }}</p>
          <template v-if="comprehensiveSet">
            <p data-test="comprehensive-version-meta">
              当前版本：
              {{
                comprehensiveDetail?.version_label ||
                comprehensiveSet.versions.find((item) => item.is_current)?.version_label ||
                '暂无'
              }}
              ·
              <span v-if="comprehensiveDetail?.is_current || comprehensiveSet.current_version_id">
                当前
              </span>
              <span v-else>无当前</span>
              ·
              <span
                data-test="comprehensive-freshness"
                :data-freshness="comprehensiveFreshnessLabel"
              >
                <span
                  v-if="comprehensiveDetail?.is_stale === true"
                  data-test="comprehensive-stale-badge"
                >
                  已过期
                </span>
                <span
                  v-else-if="comprehensiveDetail?.is_stale === false"
                  data-test="comprehensive-fresh-badge"
                >
                  有效
                </span>
                <span v-else>{{ comprehensiveFreshnessLabel }}</span>
              </span>
              · 分数：{{ comprehensiveDetail?.overall_score ?? '—' }}
            </p>
            <div v-if="currentCoverage" data-test="comprehensive-coverage" class="coverage-block">
              <p>
                覆盖：合格 {{ currentCoverage.eligible_round_count }} /
                总 {{ currentCoverage.total_round_count }} 轮
                <span v-if="currentCoverage.single_round_only"> · 单轮覆盖不足提示</span>
                <span v-if="currentCoverage.coverage_insufficient"> · 覆盖不足</span>
              </p>
              <ul v-if="currentCoverage.gaps?.length" data-test="comprehensive-gaps">
                <li v-for="gap in currentCoverage.gaps" :key="`${gap.round_id}-${gap.reason_code}`">
                  轮次 {{ gap.sequence_no ?? '—' }}：{{ gap.reason_code }}
                </li>
              </ul>
            </div>
            <el-table
              v-if="dimensionSummaryRows.length"
              data-test="comprehensive-dimension-summary"
              :data="dimensionSummaryRows"
              stripe
              style="margin-top: 8px"
            >
              <el-table-column prop="round_sequence" label="轮次" width="80" />
              <el-table-column prop="dimension_name" label="维度" min-width="120" />
              <el-table-column prop="score" label="分数" width="90" />
            </el-table>
            <el-table
              data-test="comprehensive-version-history"
              :data="comprehensiveSet.versions"
              stripe
              style="margin-top: 12px"
            >
              <el-table-column prop="version_label" label="版本" width="90" />
              <el-table-column label="状态" width="120">
                <template #default="{ row }">
                  {{ row.is_current ? '当前' : '历史' }}
                  {{ row.is_stale ? '·过期' : '' }}
                </template>
              </el-table-column>
              <el-table-column label="分数" width="90">
                <template #default="{ row }">{{ row.overall_score ?? '—' }}</template>
              </el-table-column>
              <el-table-column prop="created_at" label="时间" min-width="160" />
            </el-table>
          </template>
          <el-empty v-else-if="!comprehensiveLoading" description="暂无综合分析版本" />
        </section>

        <section class="panel">
          <h2>简历摘要</h2>
          <template v-if="detail.resume_summary">
            <p>版本：{{ detail.resume_summary.version_label }}</p>
            <p>文件：{{ detail.resume_summary.original_filename || '—' }}</p>
            <el-button data-test="go-resume-review" type="primary" plain @click="goResumeReview">
              查看简历校对页
            </el-button>
          </template>
          <el-empty v-else description="无简历" />
        </section>

        <section class="panel">
          <h2>评分摘要</h2>
          <template v-if="detail.score_summary">
            <p>评分版本：{{ detail.score_summary.version_label }}</p>
            <p>后端复算总分：{{ detail.score_summary.calculated_total_score }}</p>
            <p>分数带：{{ detail.score_summary.score_band }}</p>
            <el-button data-test="go-score-report" type="primary" plain @click="goScoreReport">
              进入评分报告
            </el-button>
          </template>
          <p v-else>无评分</p>
        </section>

        <section class="panel">
          <div class="panel-title-row">
            <h2>面试时间轴</h2>
            <el-button data-test="go-interviews" type="primary" plain @click="goInterviews">
              打开面试时间轴页
            </el-button>
          </div>
          <el-table :data="detail.rounds" stripe>
            <el-table-column prop="name" label="轮次" min-width="120" />
            <el-table-column prop="status" label="状态" width="120" />
            <el-table-column prop="schedule_status" label="安排状态" width="120" />
            <el-table-column prop="invitation_status" label="邀约状态" width="120" />
            <el-table-column prop="transcript_status" label="转写状态" width="120" />
            <el-table-column prop="question_status" label="题纲状态" width="120" />
            <el-table-column prop="analysis_status" label="分析状态" width="120" />
            <el-table-column label="操作" width="120">
              <template #default="{ row }">
                <el-button
                  :data-test="`go-transcript-${row.round_id}`"
                  link
                  type="primary"
                  @click="goTranscript(row)"
                >
                  查看转写
                </el-button>
              </template>
            </el-table-column>
          </el-table>
          <div class="quick-actions">
            <el-button
              v-for="round in detail.rounds"
              :key="`quick-transcript-${round.round_id}`"
              :data-test="`go-transcript-${round.round_id}`"
              link
              type="primary"
              @click="goTranscript(round)"
            >
              查看 {{ round.name }} 转写
            </el-button>
          </div>
        </section>

        <section class="panel">
          <h2>其他应聘记录</h2>
          <el-table :data="detail.other_applications" stripe>
            <el-table-column prop="job_name" label="岗位" min-width="140" />
            <el-table-column prop="job_code" label="岗位编号" width="120" />
            <el-table-column prop="status" label="状态" width="120" />
            <el-table-column label="流程状态" width="140">
              <template #default="{ row }">
                {{ pipelineText(row.pipeline_status) }}
              </template>
            </el-table-column>
            <el-table-column label="操作" width="140">
              <template #default="{ row }">
                <el-button
                  :data-test="`other-application-${row.application_id}`"
                  link
                  type="primary"
                  @click="goOtherApplication(row.application_id)"
                >
                  查看详情
                </el-button>
              </template>
            </el-table-column>
          </el-table>
          <div class="quick-actions">
            <el-button
              v-for="item in detail.other_applications"
              :key="`quick-other-${item.application_id}`"
              :data-test="`other-application-${item.application_id}`"
              link
              type="primary"
              @click="goOtherApplication(item.application_id)"
            >
              查看 {{ item.job_name }} 详情
            </el-button>
          </div>
        </section>

        <el-dialog
          v-model="dialogVisible"
          data-test="hiring-decision-dialog"
          :title="pendingDecision ? decisionLabel[pendingDecision] : '面后人工决策'"
          width="480px"
        >
          <el-form label-width="100px">
            <el-form-item label="原因码">
              <el-select
                v-model="selectedReasonCode"
                data-test="hiring-reason-select"
                placeholder="选择固定原因码"
                style="width: 100%"
              >
                <el-option
                  v-for="item in reasonOptions"
                  :key="item.code"
                  :label="item.label"
                  :value="item.code"
                />
              </el-select>
            </el-form-item>
            <el-form-item label="分析版本">
              <span data-test="hiring-analysis-version">{{ validAnalysisVersionId || '—' }}</span>
            </el-form-item>
          </el-form>
          <template #footer>
            <el-button @click="dialogVisible = false">取消</el-button>
            <el-button
              data-test="hiring-submit"
              type="primary"
              :loading="submitting"
              @click="submitDecision"
            >
              确认
            </el-button>
          </template>
        </el-dialog>
      </template>
    </div>
  </AdminLayout>
</template>

<style scoped>
.page {
  max-width: 1100px;
}
.panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 14px 16px;
  margin-bottom: 12px;
}
.panel-head {
  margin-bottom: 8px;
}
.panel h2 {
  margin: 0 0 8px;
  font-size: 16px;
}
.panel p {
  margin: 4px 0;
  font-size: 13px;
}
.panel-title-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}
.quick-actions {
  margin-top: 8px;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.hiring-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 8px;
}
.hint {
  color: var(--text-secondary, #666);
  font-size: 13px;
}
.hint.warn {
  color: #b45309;
}
.coverage-block {
  margin-top: 8px;
}
.coverage-block ul {
  margin: 4px 0 0;
  padding-left: 18px;
}
</style>
