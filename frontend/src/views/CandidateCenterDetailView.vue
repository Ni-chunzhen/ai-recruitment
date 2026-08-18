<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import {
  getCandidateCenterApplicationDetail,
  type CandidateCenterDetail,
  type CandidateCenterRound,
} from '../api/candidateCenter'
import AdminLayout from '../layouts/AdminLayout.vue'
import { pipelineStatusLabel, type PipelineStatus } from '../api/resumes'

const route = useRoute()
const router = useRouter()

const loading = ref(false)
const loadFailed = ref(false)
const detail = ref<CandidateCenterDetail | null>(null)

const candidateId = computed(() => route.params.candidateId as string)
const applicationId = computed(() => route.params.applicationId as string)
const hasCurrentDetail = computed(
  () => detail.value?.application_id === applicationId.value && detail.value?.candidate_id === candidateId.value,
)

function pipelineText(status: string) {
  return pipelineStatusLabel[status as PipelineStatus] || status
}

async function loadDetail() {
  loading.value = true
  loadFailed.value = false
  detail.value = null
  try {
    detail.value = await getCandidateCenterApplicationDetail(candidateId.value, applicationId.value)
  } catch {
    loadFailed.value = true
    ElMessage.error('加载详情失败')
  } finally {
    loading.value = false
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
</style>
