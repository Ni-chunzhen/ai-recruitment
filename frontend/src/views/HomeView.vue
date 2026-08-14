<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { fetchHealth } from '../api/health'
import { listJobs, type JobListItem } from '../api/jobs'
import {
  listPendingReviews,
  resumeStatusLabel,
  uploadResumes,
  type ResumeListItem,
  type ResumeStatus,
} from '../api/resumes'
import AdminLayout from '../layouts/AdminLayout.vue'
import { useAuthStore } from '../stores/auth'

type ViewState = 'loading' | 'success' | 'error'

const router = useRouter()
const authStore = useAuthStore()
const state = ref<ViewState>('loading')
const serviceName = ref('')
const errorMessage = ref('')
const canManage = computed(() => authStore.hasPermission('recruitment.manage'))

const pending = ref<ResumeListItem[]>([])
const jobs = ref<JobListItem[]>([])
const dialogWithJob = ref(false)
const dialogQuick = ref(false)
const uploadJobId = ref('')
const fileList = ref<File[]>([])
const uploading = ref(false)

async function checkHealth() {
  state.value = 'loading'
  errorMessage.value = ''
  try {
    const response = await fetchHealth()
    if (response.code === 0 && response.data.status === 'ok') {
      serviceName.value = response.data.service
      state.value = 'success'
      return
    }
    errorMessage.value = response.message || '健康检查返回异常'
    state.value = 'error'
  } catch {
    errorMessage.value = '无法连接后端服务'
    state.value = 'error'
  }
}

async function loadWorkbench() {
  if (!canManage.value) return
  try {
    const [pendingData, jobData] = await Promise.all([
      listPendingReviews(),
      listJobs({ status: 'open' }),
    ])
    pending.value = pendingData.items
    jobs.value = jobData.items
  } catch {
    /* workbench soft-fail */
  }
}

function onFileChange(file: { raw?: File }) {
  if (file.raw) {
    if (fileList.value.length >= 5) {
      ElMessage.warning('最多 5 份')
      return false
    }
    fileList.value.push(file.raw)
  }
  return false
}

function removeFile(i: number) {
  fileList.value.splice(i, 1)
}

async function submitUpload(withJob: boolean) {
  if (!fileList.value.length) return
  if (withJob && !uploadJobId.value) {
    ElMessage.warning('请选择目标岗位')
    return
  }
  uploading.value = true
  try {
    const res = await uploadResumes({
      files: fileList.value,
      jobId: withJob ? uploadJobId.value : undefined,
    })
    ElMessage.success(`已启动 ${res.items.length} 份简历解析`)
    dialogWithJob.value = false
    dialogQuick.value = false
    fileList.value = []
    uploadJobId.value = ''
    await loadWorkbench()
    await router.push({ name: 'resumes' })
  } catch (err: unknown) {
    const detail =
      (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
      '上传失败'
    ElMessage.error(String(detail))
  } finally {
    uploading.value = false
  }
}

onMounted(async () => {
  await checkHealth()
  await loadWorkbench()
})
</script>

<template>
  <AdminLayout>
    <div class="home">
      <h1>招聘工作台</h1>
      <p class="sub">阶段 5：简历上传 → 解析校对 → 岗位评分闭环</p>

      <section v-if="state === 'loading'" class="status loading">正在检测后端连接...</section>
      <section v-else-if="state === 'success'" class="status success">
        <p>前后端连接正常 · {{ serviceName }}</p>
      </section>
      <section v-else class="status error">
        <p>后端连接失败：{{ errorMessage }}</p>
        <el-button @click="checkHealth">重新检测</el-button>
      </section>

      <div v-if="canManage" class="actions">
        <el-button type="primary" @click="router.push({ name: 'jobs' })">岗位管理</el-button>
        <el-button @click="router.push({ name: 'resumes' })">简历库</el-button>
      </div>

      <template v-if="canManage">
        <section class="module">
          <div class="module-head">
            <div>
              <h3><span class="num">1</span>简历来源</h3>
              <p class="desc">有明确岗位时按岗上传；批量入库走快速入口</p>
            </div>
          </div>
          <div class="upload-entry-grid">
            <button type="button" class="upload-entry primary" @click="dialogWithJob = true">
              <h5>按岗位上传简历</h5>
              <p>绑定当前生效岗位版本，最多 5 份，上传后解析再校对</p>
              <span class="tag">主流程 · 推荐</span>
            </button>
            <button type="button" class="upload-entry secondary" @click="dialogQuick = true">
              <h5>快速上传（仅入库）</h5>
              <p>暂不关联岗位，确认后「待匹配」</p>
              <span class="tag">补充场景</span>
            </button>
          </div>
        </section>

        <section class="module">
          <div class="module-head">
            <div>
              <h3><span class="num">2</span>待确认简历</h3>
              <p class="desc">解析完成待校对 / 解析失败 · 共 {{ pending.length }} 条</p>
            </div>
            <el-button link type="primary" @click="loadWorkbench">刷新</el-button>
          </div>
          <div v-if="pending.length" class="pending-list">
            <div v-for="item in pending" :key="item.resume_version_id" class="pending-item">
              <div>
                <div class="title">{{ item.original_filename || item.candidate_name }}</div>
                <div class="meta">
                  {{ item.candidate_name }} ·
                  {{ resumeStatusLabel[item.status as ResumeStatus] }} ·
                  {{ item.updated_at }}
                </div>
              </div>
              <el-button
                type="primary"
                size="small"
                @click="
                  router.push({
                    name: 'resume-review',
                    params: { versionId: item.resume_version_id },
                  })
                "
              >
                去校对
              </el-button>
            </div>
          </div>
          <div v-else class="empty">暂无待确认简历</div>
        </section>
      </template>
    </div>

    <el-dialog
      v-model="dialogWithJob"
      title="按岗位上传简历"
      width="560px"
      @closed="fileList = []"
    >
      <el-form label-position="top">
        <el-form-item label="目标岗位" required>
          <el-select v-model="uploadJobId" filterable placeholder="选择招聘中岗位" style="width: 100%">
            <el-option
              v-for="j in jobs"
              :key="j.id"
              :label="`${j.name} · ${j.current_version_label || ''}`"
              :value="j.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="简历文件" required>
          <el-upload drag :auto-upload="false" :show-file-list="false" :on-change="onFileChange" multiple>
            <div>拖拽或点击上传，最多 5 份</div>
          </el-upload>
          <ul class="files">
            <li v-for="(f, i) in fileList" :key="i">
              {{ f.name }}
              <el-button link type="danger" @click="removeFile(i)">移除</el-button>
            </li>
          </ul>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogWithJob = false">取消</el-button>
        <el-button
          type="primary"
          :disabled="!uploadJobId || !fileList.length"
          :loading="uploading"
          @click="submitUpload(true)"
        >
          上传并解析
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="dialogQuick" title="快速上传（仅入库）" width="520px" @closed="fileList = []">
      <el-alert
        title="不创建应聘记录；确认后再选岗位匹配。"
        type="info"
        show-icon
        :closable="false"
        style="margin-bottom: 12px"
      />
      <el-upload drag :auto-upload="false" :show-file-list="false" :on-change="onFileChange" multiple>
        <div>拖拽或点击上传，最多 5 份</div>
      </el-upload>
      <ul class="files">
        <li v-for="(f, i) in fileList" :key="i">
          {{ f.name }}
          <el-button link type="danger" @click="removeFile(i)">移除</el-button>
        </li>
      </ul>
      <template #footer>
        <el-button @click="dialogQuick = false">取消</el-button>
        <el-button
          type="primary"
          :disabled="!fileList.length"
          :loading="uploading"
          @click="submitUpload(false)"
        >
          开始解析
        </el-button>
      </template>
    </el-dialog>
  </AdminLayout>
</template>

<style scoped>
.home {
  max-width: 960px;
}
h1 {
  margin: 0 0 8px;
  font-size: 28px;
}
.sub {
  color: var(--muted);
  margin: 0 0 20px;
}
.status {
  margin-bottom: 16px;
  padding: 12px 14px;
  border-radius: 8px;
}
.loading {
  background: #f3f4f6;
}
.success {
  background: #ecfdf5;
  color: #065f46;
}
.error {
  background: #fef2f2;
  color: #991b1b;
}
.actions {
  display: flex;
  gap: 8px;
  margin-bottom: 20px;
}
.module {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 16px;
  margin-bottom: 16px;
}
.module-head {
  display: flex;
  justify-content: space-between;
  margin-bottom: 12px;
}
.module-head h3 {
  margin: 0;
  font-size: 16px;
}
.module-head .num {
  display: inline-flex;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: color-mix(in oklab, var(--accent), transparent 85%);
  color: var(--accent);
  align-items: center;
  justify-content: center;
  font-size: 12px;
  margin-right: 8px;
}
.desc {
  margin: 4px 0 0;
  color: var(--muted);
  font-size: 13px;
}
.upload-entry-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}
@media (max-width: 720px) {
  .upload-entry-grid {
    grid-template-columns: 1fr;
  }
}
.upload-entry {
  text-align: left;
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 14px;
  background: var(--bg);
  cursor: pointer;
}
.upload-entry.primary {
  border-color: color-mix(in oklab, var(--accent), transparent 50%);
}
.upload-entry h5 {
  margin: 0 0 6px;
  font-size: 14px;
}
.upload-entry p {
  margin: 0;
  font-size: 12px;
  color: var(--muted);
}
.upload-entry .tag {
  display: inline-block;
  margin-top: 10px;
  font-size: 11px;
  color: var(--accent);
}
.pending-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.pending-item {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
}
.title {
  font-size: 14px;
  font-weight: 600;
}
.meta {
  font-size: 12px;
  color: var(--muted);
  margin-top: 4px;
}
.empty {
  color: var(--muted);
  font-size: 13px;
  padding: 20px 0;
  text-align: center;
}
.files {
  list-style: none;
  padding: 0;
  margin: 12px 0 0;
}
.files li {
  display: flex;
  justify-content: space-between;
  font-size: 13px;
  padding: 4px 0;
}
</style>
