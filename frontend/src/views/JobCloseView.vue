<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import {
  getClosePreview,
  resolveCloseApplication,
  type CloseAction,
  type ClosePreview,
  type ClosePreviewItem,
} from '../api/candidates'
import { closeJob, getJob, listJobs, type JobDetail, type JobListItem } from '../api/jobs'
import AdminLayout from '../layouts/AdminLayout.vue'

const route = useRoute()
const router = useRouter()
const jobId = computed(() => route.params.id as string)

const loading = ref(false)
const closing = ref(false)
const job = ref<JobDetail | null>(null)
const preview = ref<ClosePreview | null>(null)
const openJobs = ref<JobListItem[]>([])

const draft = reactive<Record<string, { action: CloseAction; reason: string; target_job_id: string }>>(
  {},
)

function ensureDraft(item: ClosePreviewItem) {
  if (!draft[item.application_id]) {
    draft[item.application_id] = {
      action: 'reject',
      reason: '',
      target_job_id: '',
    }
  }
  return draft[item.application_id]
}

async function load() {
  loading.value = true
  try {
    job.value = await getJob(jobId.value)
    preview.value = await getClosePreview(jobId.value)
    for (const item of preview.value.items) ensureDraft(item)
    const listed = await listJobs({ status: 'open', page_size: 100 })
    openJobs.value = listed.items.filter((item) => item.id !== jobId.value)
  } catch {
    ElMessage.error('加载关闭预览失败')
    await router.push({ name: 'job-detail', params: { id: jobId.value } })
  } finally {
    loading.value = false
  }
}

async function onResolve(item: ClosePreviewItem) {
  const form = ensureDraft(item)
  if (!form.reason.trim()) {
    ElMessage.warning('请填写处理原因')
    return
  }
  if (form.action === 'transfer' && !form.target_job_id) {
    ElMessage.warning('请选择目标岗位')
    return
  }
  try {
    await resolveCloseApplication(jobId.value, item.application_id, {
      action: form.action,
      reason: form.reason.trim(),
      target_job_id: form.action === 'transfer' ? form.target_job_id : undefined,
    })
    ElMessage.success('已处理')
    preview.value = await getClosePreview(jobId.value)
    for (const next of preview.value.items) ensureDraft(next)
  } catch (error) {
    ElMessage.error((error as Error)?.message || '处理失败')
  }
}

async function onFinalClose() {
  if (!preview.value?.can_close) {
    ElMessage.warning('仍有在途候选人未处理')
    return
  }
  try {
    const { value } = await ElMessageBox.prompt('请填写关闭原因（必填）', '关闭岗位', {
      confirmButtonText: '确认关闭',
      cancelButtonText: '取消',
      inputPattern: /\S+/,
      inputErrorMessage: '原因不能为空',
    })
    closing.value = true
    await closeJob(jobId.value, value)
    ElMessage.success('岗位已关闭')
    await router.push({ name: 'job-detail', params: { id: jobId.value } })
  } catch (err) {
    if (err !== 'cancel') ElMessage.error('关闭失败')
  } finally {
    closing.value = false
  }
}
onMounted(load)
</script>

<template>
  <AdminLayout>
    <div v-loading="loading" class="page">
      <div class="page-head">
        <div>
          <el-button link type="primary" @click="router.push({ name: 'job-detail', params: { id: jobId } })">
            ← 返回岗位详情
          </el-button>
          <h1>关闭前处理在途候选人</h1>
          <p class="muted">
            {{ job?.name || '—' }} · {{ job?.code || '' }} · 在途
            {{ preview?.in_flight_count ?? 0 }} 人
          </p>
        </div>
        <el-button
          type="danger"
          :disabled="!preview?.can_close"
          :loading="closing"
          @click="onFinalClose"
        >
          {{ preview?.can_close ? '确认关闭岗位' : '请先处理完全部在途' }}
        </el-button>
      </div>

      <el-alert
        v-if="preview && !preview.can_close"
        type="warning"
        :closable="false"
        title="存在在途候选人，须全部处理为：转移到其他招聘中岗位 / 标记淘汰 / 终止流程后，才能关闭。"
        class="mb"
      />
      <el-alert
        v-else-if="preview?.can_close"
        type="success"
        :closable="false"
        title="在途候选人已清零，可以关闭岗位。"
        class="mb"
      />

      <el-empty v-if="preview && preview.items.length === 0" description="没有待处理的在途候选人" />

      <div v-for="item in preview?.items || []" :key="item.application_id" class="card">
        <div class="card-head">
          <strong>{{ item.candidate_name }}</strong>
          <el-tag size="small" :type="item.interview_started ? 'warning' : 'info'">
            {{ item.interview_started ? '已开始面试' : '未开始面试' }}
          </el-tag>
        </div>
        <el-form label-width="96px" class="form">
          <el-form-item label="处理方式">
            <el-radio-group v-model="ensureDraft(item).action">
              <el-radio-button value="reject">淘汰</el-radio-button>
              <el-radio-button value="transfer">转移岗位</el-radio-button>
              <el-radio-button value="terminate">终止流程</el-radio-button>
            </el-radio-group>
          </el-form-item>
          <el-form-item v-if="ensureDraft(item).action === 'transfer'" label="目标岗位">
            <el-select
              v-model="ensureDraft(item).target_job_id"
              filterable
              placeholder="选择招聘中岗位"
              style="width: 100%"
            >
              <el-option
                v-for="opt in openJobs"
                :key="opt.id"
                :label="`${opt.code} · ${opt.name}`"
                :value="opt.id"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="原因">
            <el-input
              v-model="ensureDraft(item).reason"
              type="textarea"
              :rows="2"
              placeholder="必填：淘汰/转移/终止原因"
            />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" @click="onResolve(item)">提交处理</el-button>
          </el-form-item>
        </el-form>
      </div>
    </div>
  </AdminLayout>
</template>

<style scoped>
.page {
  max-width: 920px;
}
.page-head {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: flex-start;
  margin-bottom: 16px;
}
.page-head h1 {
  margin: 4px 0;
  font-size: 22px;
}
.muted {
  color: var(--el-text-color-secondary);
  margin: 0;
}
.mb {
  margin-bottom: 16px;
}
.card {
  border: 1px solid var(--el-border-color);
  border-radius: 10px;
  padding: 16px;
  margin-bottom: 12px;
  background: var(--el-bg-color);
}
.card-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}
.form {
  max-width: 640px;
}
</style>
