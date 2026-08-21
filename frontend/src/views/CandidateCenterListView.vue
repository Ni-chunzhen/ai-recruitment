<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import {
  listCandidateCenterApplications,
  type CandidateCenterListItem,
  type CandidateCenterListQuery,
} from '../api/candidateCenter'
import AdminLayout from '../layouts/AdminLayout.vue'
import { pipelineStatusLabel, type PipelineStatus } from '../api/resumes'

const router = useRouter()
const loading = ref(false)
const loadFailed = ref(false)
const items = ref<CandidateCenterListItem[]>([])
const total = ref(0)

const filter = reactive({
  assigned: true,
  keyword: '',
  status: '',
  pipeline_status: '',
  page: 1,
  page_size: 20,
})

const assignedFilter = computed(() => (filter.assigned ? 'assigned' : 'all'))

const pipelineFilterOptions: Array<{ value: string; label: string }> = [
  { value: '', label: '全部流程' },
  { value: 'pending_parse', label: pipelineStatusLabel.pending_parse },
  { value: 'pending_hr_screen', label: pipelineStatusLabel.pending_hr_screen },
  { value: 'interviewing', label: pipelineStatusLabel.interviewing },
  { value: 'pending_offer', label: pipelineStatusLabel.pending_offer },
  { value: 'rejected', label: pipelineStatusLabel.rejected },
  { value: 'talent_pool', label: pipelineStatusLabel.talent_pool },
]

function buildQuery(): CandidateCenterListQuery {
  return {
    assigned: filter.assigned,
    keyword: filter.keyword || undefined,
    status: filter.status || undefined,
    pipeline_status: filter.pipeline_status || undefined,
    page: filter.page,
    page_size: filter.page_size,
  }
}

async function loadList() {
  loading.value = true
  loadFailed.value = false
  try {
    const data = await listCandidateCenterApplications(buildQuery())
    items.value = data.items
    total.value = data.total
  } catch {
    loadFailed.value = true
    ElMessage.error('加载候选人中心列表失败')
  } finally {
    loading.value = false
  }
}

function setAssigned(value: boolean) {
  if (filter.assigned === value) return
  filter.assigned = value
  filter.page = 1
  void loadList()
}

function onSearch() {
  filter.page = 1
  void loadList()
}

function onPageChange(page: number) {
  filter.page = page
  void loadList()
}

function goDetail(row: CandidateCenterListItem) {
  void router.push({
    name: 'candidate-center-detail',
    params: {
      candidateId: row.candidate_id,
      applicationId: row.application_id,
    },
  })
}

function pipelineStatusText(status: string) {
  return pipelineStatusLabel[status as PipelineStatus] || status
}

onMounted(() => {
  void loadList()
})
</script>

<template>
  <AdminLayout>
    <div class="page">
      <div class="panel-head">
        <div>
          <h1>候选人中心</h1>
          <p class="sub">统一查看候选人应聘记录与进度状态</p>
        </div>
      </div>

      <div class="filter-bar">
        <el-button
          :type="assignedFilter === 'assigned' ? 'primary' : 'default'"
          plain
          @click="setAssigned(true)"
        >
          已分配面试轮次
        </el-button>
        <el-button
          data-test="assigned-filter-all"
          :type="assignedFilter === 'all' ? 'primary' : 'default'"
          plain
          @click="setAssigned(false)"
        >
          全部应聘记录
        </el-button>
        <el-input
          v-model="filter.keyword"
          clearable
          placeholder="搜索候选人姓名、手机号、邮箱、岗位"
          style="width: 280px"
          @keyup.enter="onSearch"
        />
        <el-input
          v-model="filter.status"
          clearable
          placeholder="关闭状态"
          style="width: 140px"
          @keyup.enter="onSearch"
        />
        <el-select
          v-model="filter.pipeline_status"
          clearable
          placeholder="流程状态"
          data-test="pipeline-status-filter"
          style="width: 180px"
        >
          <el-option
            v-for="opt in pipelineFilterOptions"
            :key="opt.value || 'all'"
            :label="opt.label"
            :value="opt.value"
          />
        </el-select>
        <el-button type="primary" plain @click="onSearch">查询</el-button>
      </div>

      <el-alert
        v-if="loadFailed"
        title="请求失败，请稍后重试"
        type="error"
        show-icon
        :closable="false"
        style="margin-bottom: 12px"
      />

      <el-table
        v-loading="loading"
        :data="items"
        stripe
        data-test="candidate-center-table"
        @row-click="goDetail"
      >
        <el-table-column prop="name" label="候选人" width="120" />
        <el-table-column prop="job_name" label="岗位" min-width="140" />
        <el-table-column prop="status" label="状态" width="120" />
        <el-table-column label="流程状态" width="130">
          <template #default="{ row }">
            {{ pipelineStatusText(row.pipeline_status) }}
          </template>
        </el-table-column>
        <el-table-column label="轮次" min-width="120">
          <template #default="{ row }">
            {{ row.round_name || '—' }}
          </template>
        </el-table-column>
        <el-table-column prop="schedule_status" label="安排状态" width="120" />
        <el-table-column prop="invitation_status" label="邀约状态" width="120" />
        <el-table-column prop="transcript_status" label="转写状态" width="120" />
        <el-table-column prop="question_status" label="题纲状态" width="120" />
        <el-table-column prop="analysis_status" label="分析状态" width="120" />
      </el-table>

      <el-empty
        v-if="!loading && !loadFailed && !items.length"
        description="暂无应聘记录"
        style="margin-top: 20px"
      />

      <div class="pager">
        <el-pagination
          v-model:current-page="filter.page"
          :page-size="filter.page_size"
          :total="total"
          layout="total, prev, pager, next"
          @current-change="onPageChange"
        />
      </div>
    </div>
  </AdminLayout>
</template>

<style scoped>
.page {
  max-width: 1240px;
}

.panel-head {
  display: flex;
  justify-content: space-between;
  margin-bottom: 16px;
}

h1 {
  margin: 0;
  font-size: 24px;
}

.sub {
  margin: 6px 0 0;
  color: var(--muted);
  font-size: 13px;
}

.filter-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
  align-items: center;
}

.pager {
  margin-top: 16px;
  display: flex;
  justify-content: flex-end;
}
</style>
