<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import {
  closeJob,
  copyJob,
  deleteJob,
  listJobs,
  pauseJob,
  resumeJob,
  type JobListItem,
  type JobStatus,
} from '../api/jobs'
import { getClosePreview } from '../api/candidates'
import AdminLayout from '../layouts/AdminLayout.vue'

const router = useRouter()
const loading = ref(false)
const items = ref<JobListItem[]>([])
const total = ref(0)

const filter = reactive({
  keyword: '',
  department: '',
  owner: '',
  status: '' as JobStatus | '',
  page: 1,
  page_size: 20,
})

const statusTagType: Record<JobStatus, '' | 'success' | 'warning' | 'info' | 'danger'> = {
  draft: 'info',
  open: 'success',
  paused: 'warning',
  closed: 'info',
}

async function loadJobs() {
  loading.value = true
  try {
    const data = await listJobs({
      page: filter.page,
      page_size: filter.page_size,
      keyword: filter.keyword || undefined,
      department: filter.department || undefined,
      owner: filter.owner || undefined,
      status: filter.status || undefined,
    })
    items.value = data.items
    total.value = data.total
  } catch {
    ElMessage.error('加载岗位列表失败')
  } finally {
    loading.value = false
  }
}

function goCreate() {
  void router.push({ name: 'job-create' })
}

function goDetail(row: JobListItem) {
  void router.push({ name: 'job-detail', params: { id: row.id } })
}

function goEdit(row: JobListItem) {
  void router.push({ name: 'job-edit', params: { id: row.id } })
}

async function askReason(title: string) {
  const { value } = await ElMessageBox.prompt('请填写原因（必填）', title, {
    confirmButtonText: '确认',
    cancelButtonText: '取消',
    inputPattern: /\S+/,
    inputErrorMessage: '原因不能为空',
  })
  return value
}

async function onPause(row: JobListItem) {
  try {
    const reason = await askReason('暂停岗位')
    await pauseJob(row.id, reason)
    ElMessage.success('已暂停')
    await loadJobs()
  } catch (err) {
    if (err !== 'cancel') ElMessage.error('暂停失败')
  }
}

async function onResume(row: JobListItem) {
  try {
    const reason = await askReason('恢复招聘')
    await resumeJob(row.id, reason)
    ElMessage.success('已恢复招聘')
    await loadJobs()
  } catch (err) {
    if (err !== 'cancel') ElMessage.error('恢复失败')
  }
}

async function onClose(row: JobListItem) {
  try {
    const preview = await getClosePreview(row.id)
    if (preview.in_flight_count > 0) {
      await router.push({ name: 'job-close', params: { id: row.id } })
      return
    }
    const reason = await askReason('关闭岗位')
    await closeJob(row.id, reason)
    ElMessage.success('已关闭')
    await loadJobs()
  } catch (err) {
    if (err !== 'cancel') ElMessage.error('关闭失败')
  }
}

async function onCopy(row: JobListItem) {
  try {
    const copied = await copyJob(row.id)
    ElMessage.success(`已复制为 ${copied.code}`)
    await router.push({ name: 'job-edit', params: { id: copied.id } })
  } catch {
    ElMessage.error('复制失败')
  }
}

async function onDelete(row: JobListItem) {
  try {
    await ElMessageBox.confirm('确认物理删除该草稿？此操作不可恢复。', '删除草稿', {
      type: 'warning',
    })
    await deleteJob(row.id)
    ElMessage.success('已删除')
    await loadJobs()
  } catch (err) {
    if (err !== 'cancel') ElMessage.error('删除失败')
  }
}

onMounted(() => {
  void loadJobs()
})
</script>

<template>
  <AdminLayout>
    <div class="page-section">
      <div class="panel-head">
        <h2>岗位管理</h2>
        <el-button type="primary" @click="goCreate">新建岗位</el-button>
      </div>

      <div class="filter-bar">
        <el-input
          v-model="filter.keyword"
          class="search-unified"
          clearable
          placeholder="搜索岗位名称或编号"
          @keyup.enter="loadJobs"
        />
        <el-input
          v-model="filter.department"
          clearable
          placeholder="部门"
          style="width: 140px"
          @keyup.enter="loadJobs"
        />
        <el-input
          v-model="filter.owner"
          clearable
          placeholder="负责人"
          style="width: 120px"
          @keyup.enter="loadJobs"
        />
        <el-select v-model="filter.status" clearable placeholder="全部状态" style="width: 130px">
          <el-option label="草稿" value="draft" />
          <el-option label="招聘中" value="open" />
          <el-option label="已暂停" value="paused" />
          <el-option label="已关闭" value="closed" />
        </el-select>
        <el-button type="primary" plain @click="loadJobs">查询</el-button>
      </div>

      <el-table v-loading="loading" :data="items" stripe>
        <el-table-column prop="code" label="岗位编号" width="150" />
        <el-table-column prop="name" label="岗位名称" min-width="160" />
        <el-table-column prop="department" label="部门" width="110" />
        <el-table-column prop="location" label="工作地点" width="100" />
        <el-table-column prop="headcount" label="招聘人数" width="90" align="center" />
        <el-table-column prop="owner_name" label="负责人" width="100" />
        <el-table-column prop="current_version_label" label="JD版本" width="90" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusTagType[row.status as JobStatus]" size="small">
              {{ row.status_label }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="280" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="goDetail(row)">查看</el-button>
            <el-button
              link
              type="primary"
              size="small"
              @click="router.push({ name: 'job-detail', params: { id: row.id }, query: { tab: 'versions' } })"
            >
              版本
            </el-button>
            <el-button
              v-if="row.status !== 'closed'"
              link
              type="primary"
              size="small"
              @click="goEdit(row)"
            >
              编辑
            </el-button>
            <el-button
              v-if="row.status === 'open'"
              link
              type="warning"
              size="small"
              @click="onPause(row)"
            >
              暂停
            </el-button>
            <el-button
              v-if="row.status === 'paused'"
              link
              type="success"
              size="small"
              @click="onResume(row)"
            >
              恢复
            </el-button>
            <el-button
              v-if="row.status === 'open' || row.status === 'paused'"
              link
              type="danger"
              size="small"
              @click="onClose(row)"
            >
              关闭
            </el-button>
            <el-button link type="primary" size="small" @click="onCopy(row)">复制</el-button>
            <el-button
              v-if="row.status === 'draft'"
              link
              type="danger"
              size="small"
              @click="onDelete(row)"
            >
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pager">
        <el-pagination
          v-model:current-page="filter.page"
          :page-size="filter.page_size"
          :total="total"
          layout="total, prev, pager, next"
          @current-change="loadJobs"
        />
      </div>
    </div>
  </AdminLayout>
</template>

<style scoped>
.page-section {
  max-width: 1200px;
}

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.panel-head h2 {
  margin: 0;
  font-size: 22px;
}

.filter-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 16px;
}

.search-unified {
  width: 240px;
}

.pager {
  margin-top: 16px;
  display: flex;
  justify-content: flex-end;
}
</style>
