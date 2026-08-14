<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'

import {
  cancelAdminAITask,
  getAdminAITask,
  listAdminAITasks,
  retryAdminAITask,
  type AITaskAdminDetail,
  type AITaskAdminListItem,
} from '../api/adminAiTasks'
import AdminLayout from '../layouts/AdminLayout.vue'
import { useAuthStore } from '../stores/auth'

const CONFLICT_HINT = '任务状态已变化，请刷新后重试'
const POLL_MS = 10000

const authStore = useAuthStore()
const canManageAiTasks = computed(() => authStore.hasPermission('ai_task.manage'))

const loading = ref(false)
const items = ref<AITaskAdminListItem[]>([])
const total = ref(0)
const drawerOpen = ref(false)
const detail = ref<AITaskAdminDetail | null>(null)
const conflictMessage = ref('')
let pollTimer: number | null = null

const filter = reactive({
  task_type: '',
  status: '',
  business_type: '',
  keyword: '',
  created_from: '',
  created_to: '',
  page: 1,
  page_size: 20,
})

const statusLabel: Record<string, string> = {
  pending: '待处理',
  running: '执行中',
  succeeded: '成功',
  failed: '失败',
  output_invalid: '输出异常',
  cancelled: '已取消',
}

const canRetry = computed(
  () =>
    canManageAiTasks.value &&
    (detail.value?.status === 'failed' || detail.value?.status === 'output_invalid'),
)
const canCancel = computed(
  () => canManageAiTasks.value && detail.value?.status === 'pending',
)
const isRunning = computed(() => detail.value?.status === 'running')

function queryPayload() {
  return {
    task_type: filter.task_type || undefined,
    status: filter.status || undefined,
    business_type: filter.business_type || undefined,
    keyword: filter.keyword || undefined,
    created_from: filter.created_from || undefined,
    created_to: filter.created_to || undefined,
    page: filter.page,
    page_size: filter.page_size,
  }
}

function isConflict(err: unknown) {
  return (err as { response?: { status?: number } })?.response?.status === 409
}

function stopPoll() {
  if (pollTimer != null) {
    window.clearInterval(pollTimer)
    pollTimer = null
  }
}

function startPollIfNeeded() {
  stopPoll()
  const inflight = detail.value?.status === 'pending' || detail.value?.status === 'running'
  if (!drawerOpen.value || !inflight) return
  pollTimer = window.setInterval(() => {
    if (detail.value) void loadDetail(detail.value.id)
  }, POLL_MS)
}

async function loadList() {
  loading.value = true
  try {
    const data = await listAdminAITasks(queryPayload())
    items.value = data.items
    total.value = data.total
  } catch {
    ElMessage.error('加载AI任务失败')
  } finally {
    loading.value = false
  }
}

async function loadDetail(taskId: string) {
  detail.value = await getAdminAITask(taskId)
  startPollIfNeeded()
}

async function openDetail(row: AITaskAdminListItem) {
  conflictMessage.value = ''
  drawerOpen.value = true
  await loadDetail(row.id)
}

function closeDrawer() {
  drawerOpen.value = false
  stopPoll()
}

function onSearch() {
  filter.page = 1
  void loadList()
}

function onReset() {
  filter.task_type = ''
  filter.status = ''
  filter.business_type = ''
  filter.keyword = ''
  filter.created_from = ''
  filter.created_to = ''
  filter.page = 1
  void loadList()
}

function onPageChange(page: number) {
  filter.page = page
  void loadList()
}

async function copyText(value: string | null) {
  if (!value) return
  await navigator.clipboard.writeText(value)
  ElMessage.success('已复制')
}

async function onRetry() {
  if (!detail.value) return
  try {
    await ElMessageBox.confirm('确认重新执行该AI任务？将开启新的重试轮次。', '重新执行', {
      type: 'warning',
    })
  } catch {
    return
  }
  try {
    await retryAdminAITask(detail.value.id)
    ElMessage.success('已重新执行')
    await loadDetail(detail.value.id)
    await loadList()
  } catch (err: unknown) {
    if (isConflict(err)) {
      conflictMessage.value = CONFLICT_HINT
      return
    }
    ElMessage.error('重新执行失败')
  }
}

async function onCancel() {
  if (!detail.value) return
  try {
    await ElMessageBox.confirm('确认取消该待处理任务？', '取消任务', {
      type: 'warning',
    })
  } catch {
    return
  }
  try {
    await cancelAdminAITask(detail.value.id, 'admin cancelled')
    ElMessage.success('已取消')
    await loadDetail(detail.value.id)
    await loadList()
  } catch (err: unknown) {
    if (isConflict(err)) {
      conflictMessage.value = CONFLICT_HINT
      return
    }
    ElMessage.error('取消失败')
  }
}

onMounted(() => {
  void loadList()
})

onBeforeUnmount(() => {
  stopPoll()
})
</script>

<template>
  <AdminLayout>
    <div class="page-section">
      <div class="panel-head">
        <h2>AI任务中心</h2>
        <el-button @click="loadList">刷新</el-button>
      </div>

      <div class="filter-bar">
        <el-select v-model="filter.task_type" clearable placeholder="任务类型" style="width: 180px">
          <el-option label="JD解析" value="JD_PARSE" />
          <el-option label="维度推荐" value="SCORE_DIMENSION_RECOMMEND" />
          <el-option label="简历解析" value="RESUME_PARSE" />
          <el-option label="简历评分" value="RESUME_SCORE" />
        </el-select>
        <el-select v-model="filter.status" clearable placeholder="状态" style="width: 140px">
          <el-option label="待处理" value="pending" />
          <el-option label="执行中" value="running" />
          <el-option label="成功" value="succeeded" />
          <el-option label="失败" value="failed" />
          <el-option label="输出异常" value="output_invalid" />
          <el-option label="已取消" value="cancelled" />
        </el-select>
        <el-select v-model="filter.business_type" clearable placeholder="业务类型" style="width: 140px">
          <el-option label="岗位" value="job" />
          <el-option label="简历版本" value="resume_version" />
          <el-option label="应聘" value="application" />
        </el-select>
        <el-input
          v-model="filter.keyword"
          clearable
          placeholder="业务ID或关键词"
          style="width: 200px"
        />
        <el-date-picker
          v-model="filter.created_from"
          type="date"
          placeholder="开始日期"
          value-format="YYYY-MM-DD"
          style="width: 140px"
        />
        <el-date-picker
          v-model="filter.created_to"
          type="date"
          placeholder="结束日期"
          value-format="YYYY-MM-DD"
          style="width: 140px"
        />
        <el-button type="primary" plain @click="onSearch">查询</el-button>
        <el-button @click="onReset">重置</el-button>
        <el-button @click="loadList">刷新</el-button>
      </div>

      <el-alert
        v-if="conflictMessage"
        :title="conflictMessage"
        type="warning"
        show-icon
        :closable="false"
        style="margin-bottom: 12px"
      >
        <el-button @click="loadList(); detail && loadDetail(detail.id)">刷新</el-button>
      </el-alert>

      <el-table v-loading="loading" :data="items" stripe @row-click="openDetail">
        <el-table-column prop="id" label="任务编号" min-width="220" />
        <el-table-column prop="task_type" label="任务类型" width="160" />
        <el-table-column prop="business_type" label="业务类型" width="120" />
        <el-table-column prop="business_id" label="业务ID" min-width="180" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag size="small">{{ statusLabel[row.status] || row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="attempt_count" label="总执行次数" width="110" />
        <el-table-column prop="retry_cycle_no" label="当前重试轮次" width="120" />
        <el-table-column prop="cycle_attempt_count" label="当前轮次次数" width="120" />
        <el-table-column prop="created_at" label="创建时间" width="170" />
        <el-table-column prop="finished_at" label="完成时间" width="170" />
        <el-table-column prop="error_message" label="错误摘要" min-width="180" />
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click.stop="openDetail(row)">查看</el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-pagination
        class="pager"
        :current-page="filter.page"
        :page-size="filter.page_size"
        :total="total"
        layout="total, prev, pager, next"
        @current-change="onPageChange"
      />

      <el-drawer v-model="drawerOpen" title="任务详情" size="640px" @close="closeDrawer">
        <div v-if="detail" class="drawer-body">
          <div class="drawer-actions">
            <el-button v-if="canRetry" type="warning" @click="onRetry">重新执行</el-button>
            <el-button v-if="canCancel" @click="onCancel">取消任务</el-button>
            <span v-if="isRunning" class="running-hint">执行中</span>
            <el-button @click="closeDrawer">关闭</el-button>
          </div>

          <section class="block">
            <h3>任务信息</h3>
            <p>任务编号：{{ detail.id }} <el-button link type="primary" @click="copyText(detail.id)">复制</el-button></p>
            <p>任务类型：{{ detail.task_type }}</p>
            <p>业务类型：{{ detail.business_type }}</p>
            <p>业务ID：{{ detail.business_id }}</p>
            <p>状态：{{ statusLabel[detail.status] || detail.status }}</p>
            <p>历史执行总数：{{ detail.attempt_count }}</p>
            <p>当前人工重试轮次：{{ detail.retry_cycle_no }}</p>
            <p>当前轮次执行次数：{{ detail.cycle_attempt_count }}</p>
            <p>创建时间：{{ detail.created_at }}</p>
            <p>开始时间：{{ detail.started_at || '—' }}</p>
            <p>结束时间：{{ detail.finished_at || '—' }}</p>
          </section>

          <section class="block">
            <h3>错误摘要</h3>
            <p>错误分类：{{ detail.error_category || '—' }}</p>
            <p>错误码：{{ detail.error_code || '—' }}</p>
            <p>{{ detail.error_message || '无' }}</p>
          </section>

          <section class="block">
            <h3>执行记录</h3>
            <table class="attempt-table">
              <thead>
                <tr>
                  <th>全局次数</th>
                  <th>人工重试轮次</th>
                  <th>本轮次数</th>
                  <th>状态</th>
                  <th>开始时间</th>
                  <th>耗时</th>
                  <th>HTTP状态</th>
                  <th>错误分类</th>
                  <th>provider_run_id</th>
                  <th>request_id</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in detail.attempts" :key="row.id">
                  <td>{{ row.attempt_no }}</td>
                  <td>{{ row.retry_cycle_no }}</td>
                  <td>{{ row.cycle_attempt_no }}</td>
                  <td>{{ row.status }}</td>
                  <td>{{ row.started_at }}</td>
                  <td>{{ row.duration_ms ?? '—' }}</td>
                  <td>{{ row.http_status ?? '—' }}</td>
                  <td>{{ row.error_category || '—' }}</td>
                  <td>
                    {{ row.provider_run_id || '—' }}
                    <el-button
                      v-if="row.provider_run_id"
                      link
                      type="primary"
                      @click="copyText(row.provider_run_id)"
                    >
                      复制
                    </el-button>
                  </td>
                  <td>
                    {{ row.request_id || '—' }}
                    <el-button
                      v-if="row.request_id"
                      link
                      type="primary"
                      @click="copyText(row.request_id)"
                    >
                      复制
                    </el-button>
                  </td>
                </tr>
              </tbody>
            </table>
          </section>
        </div>
      </el-drawer>
    </div>
  </AdminLayout>
</template>

<style scoped>
.page-section {
  max-width: 1280px;
}
.panel-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}
.filter-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
  padding: 12px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
}
.pager {
  margin-top: 12px;
  justify-content: flex-end;
}
.drawer-actions {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 16px;
}
.running-hint {
  color: var(--accent);
  font-size: 13px;
}
.block {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 12px 14px;
  margin-bottom: 12px;
}
.block h3 {
  margin: 0 0 8px;
  font-size: 14px;
}
.block p {
  margin: 4px 0;
  font-size: 13px;
  color: var(--fg);
}
.attempt-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
.attempt-table th,
.attempt-table td {
  border-bottom: 1px solid var(--border);
  padding: 6px 8px;
  text-align: left;
  vertical-align: top;
  white-space: nowrap;
}
</style>
