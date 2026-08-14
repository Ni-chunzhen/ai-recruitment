<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import { listJobs, type JobListItem } from '../api/jobs'
import {
  createApplication,
  createResumeScoreTask,
  listResumes,
  resumeStatusLabel,
  uploadResumes,
  type ResumeListItem,
  type ResumeStatus,
} from '../api/resumes'
import AdminLayout from '../layouts/AdminLayout.vue'

const router = useRouter()
const loading = ref(false)
const uploading = ref(false)
const matching = ref(false)
const items = ref<ResumeListItem[]>([])
const total = ref(0)
const jobs = ref<JobListItem[]>([])
const dialogQuick = ref(false)
const dialogMatch = ref(false)
const matchTarget = ref<ResumeListItem | null>(null)
const matchJobId = ref('')
const fileList = ref<File[]>([])
const filter = reactive({
  keyword: '',
  status: '' as '' | ResumeStatus,
  linked: '' as '' | 'yes' | 'no',
})

async function load() {
  loading.value = true
  try {
    const data = await listResumes({
      keyword: filter.keyword || undefined,
      status: filter.status || undefined,
      linked: filter.linked === '' ? null : filter.linked === 'yes',
    })
    items.value = data.items
    total.value = data.total
  } catch {
    ElMessage.error('加载简历库失败')
  } finally {
    loading.value = false
  }
}

async function loadJobs() {
  try {
    const data = await listJobs({ status: 'open' })
    jobs.value = data.items
  } catch {
    jobs.value = []
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

function removeFile(index: number) {
  fileList.value.splice(index, 1)
}

async function submitQuick() {
  if (!fileList.value.length) return
  uploading.value = true
  try {
    const res = await uploadResumes({ files: fileList.value })
    ElMessage.success(`已入库 ${res.items.length} 份，正在解析`)
    dialogQuick.value = false
    fileList.value = []
    await load()
  } catch (err: unknown) {
    const detail =
      (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
      '上传失败'
    ElMessage.error(String(detail))
  } finally {
    uploading.value = false
  }
}

function openReview(row: ResumeListItem) {
  void router.push({ name: 'resume-review', params: { versionId: row.resume_version_id } })
}

function openMatch(row: ResumeListItem) {
  matchTarget.value = row
  matchJobId.value = ''
  dialogMatch.value = true
}

async function submitMatch() {
  if (!matchTarget.value || !matchJobId.value) return
  matching.value = true
  try {
    const app = await createApplication({
      candidate_id: matchTarget.value.candidate_id,
      job_id: matchJobId.value,
      resume_version_id: matchTarget.value.resume_version_id,
    })
    try {
      await createResumeScoreTask(app.id, {
        resume_version_id: matchTarget.value.resume_version_id,
        idempotency_key: `match-${app.id}`,
      })
    } catch {
      /* 评分可稍后在报告页发起 */
    }
    dialogMatch.value = false
    ElMessage.success('已创建应聘并尝试发起评分')
    await router.push({ name: 'score-report', params: { applicationId: app.id } })
  } catch (err: unknown) {
    const detail =
      (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
      '匹配失败'
    ElMessage.error(String(detail))
  } finally {
    matching.value = false
  }
}

onMounted(async () => {
  await Promise.all([load(), loadJobs()])
})
</script>

<template>
  <AdminLayout>
    <div class="page">
      <div class="panel-head">
        <div>
          <h1>简历库</h1>
          <p class="sub">共 {{ total }} 条 · 未关联岗位且已确认显示「待匹配」</p>
        </div>
        <el-button type="primary" @click="dialogQuick = true">快速上传（仅入库）</el-button>
      </div>

      <div class="filter-bar">
        <el-input
          v-model="filter.keyword"
          clearable
          placeholder="搜索姓名、手机号、邮箱"
          style="width: 240px"
        />
        <el-select v-model="filter.status" clearable placeholder="解析状态" style="width: 140px">
          <el-option
            v-for="(label, key) in resumeStatusLabel"
            :key="key"
            :label="label"
            :value="key"
          />
        </el-select>
        <el-select v-model="filter.linked" clearable placeholder="是否关联岗位" style="width: 150px">
          <el-option label="已关联" value="yes" />
          <el-option label="未关联" value="no" />
        </el-select>
        <el-button type="primary" plain @click="load">查询</el-button>
      </div>

      <el-table v-loading="loading" :data="items" stripe>
        <el-table-column prop="candidate_name" label="姓名" width="110" />
        <el-table-column prop="phone" label="手机号" width="130">
          <template #default="{ row }">{{ row.phone || '—' }}</template>
        </el-table-column>
        <el-table-column prop="email" label="邮箱" min-width="160">
          <template #default="{ row }">{{ row.email || '—' }}</template>
        </el-table-column>
        <el-table-column label="解析状态" width="110">
          <template #default="{ row }">
            <el-tag size="small">{{ resumeStatusLabel[row.status as ResumeStatus] }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="关联岗位" min-width="160">
          <template #default="{ row }">
            <span v-if="row.awaiting_match">待匹配</span>
            <span v-else-if="row.job_names?.length">{{ row.job_names.join('、') }}</span>
            <span v-else>—</span>
          </template>
        </el-table-column>
        <el-table-column prop="original_filename" label="简历文件" min-width="160" />
        <el-table-column prop="updated_at" label="更新时间" width="170" />
        <el-table-column label="操作" width="180" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openReview(row)">校对/查看</el-button>
            <el-button
              v-if="row.status === 'confirmed'"
              link
              type="primary"
              @click="openMatch(row)"
            >
              匹配岗位
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="dialogQuick" title="快速上传（仅入库）" width="520px">
      <el-alert
        title="暂不关联岗位：解析确认后状态为「待匹配」，再选择岗位创建应聘记录。"
        type="info"
        show-icon
        :closable="false"
        style="margin-bottom: 12px"
      />
      <el-upload drag :auto-upload="false" :show-file-list="false" :on-change="onFileChange" multiple>
        <div>拖拽或点击上传 PDF / DOCX / TXT（最多 5 份）</div>
      </el-upload>
      <ul class="files">
        <li v-for="(f, i) in fileList" :key="i">
          {{ f.name }}
          <el-button link type="danger" @click="removeFile(i)">移除</el-button>
        </li>
      </ul>
      <template #footer>
        <el-button @click="dialogQuick = false">取消</el-button>
        <el-button type="primary" :disabled="!fileList.length" :loading="uploading" @click="submitQuick">
          开始解析
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="dialogMatch" title="匹配岗位" width="480px">
      <el-alert
        title="将创建独立应聘记录并绑定岗位当前生效版本；可随后发起评分。"
        type="info"
        show-icon
        :closable="false"
        style="margin-bottom: 12px"
      />
      <el-select v-model="matchJobId" filterable placeholder="选择招聘中岗位" style="width: 100%">
        <el-option
          v-for="j in jobs"
          :key="j.id"
          :label="`${j.name} · ${j.current_version_label || ''}`"
          :value="j.id"
        />
      </el-select>
      <template #footer>
        <el-button @click="dialogMatch = false">取消</el-button>
        <el-button type="primary" :disabled="!matchJobId" :loading="matching" @click="submitMatch">
          创建应聘并评分
        </el-button>
      </template>
    </el-dialog>
  </AdminLayout>
</template>

<style scoped>
.page {
  max-width: 1100px;
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
