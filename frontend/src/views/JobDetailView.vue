<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import {
  closeJob,
  copyJob,
  copyVersionToDraft,
  diffJobVersions,
  getJob,
  getJobVersion,
  listJobVersions,
  pauseJob,
  resumeJob,
  type JobDetail,
  type JobStatus,
  type JobVersion,
  type JobVersionListItem,
  type VersionDiffResponse,
} from '../api/jobs'
import {
  createJobCandidate,
  getClosePreview,
  listJobCandidates,
  migrateApplicationVersion,
  type JobApplication,
} from '../api/candidates'
import { pipelineStatusLabel, uploadResumes, type PipelineStatus } from '../api/resumes'
import AdminLayout from '../layouts/AdminLayout.vue'

const route = useRoute()
const router = useRouter()
const loading = ref(false)
const versionsLoading = ref(false)
const job = ref<JobDetail | null>(null)
const tab = ref((route.query.tab as string) || 'overview')
const versions = ref<JobVersionListItem[]>([])
const compareFrom = ref('')
const compareTo = ref('')
const diffLoading = ref(false)
const diffResult = ref<VersionDiffResponse | null>(null)
const drawerVisible = ref(false)
const activeVersion = ref<JobVersion | null>(null)
const candidates = ref<JobApplication[]>([])
const candidatesLoading = ref(false)
const newCandidateName = ref('')
const migrateTarget = ref<Record<string, string>>({})
const migrateReason = ref<Record<string, string>>({})
const dialogUpload = ref(false)
const uploadFiles = ref<File[]>([])
const uploading = ref(false)

const version = computed(() => job.value?.current_version || job.value?.draft_version)
const publishedVersions = computed(() => versions.value.filter((item) => !item.is_draft))
const canCopyVersionToDraft = computed(
  () => job.value?.status === 'open' || job.value?.status === 'paused',
)
const statusType: Record<JobStatus, '' | 'success' | 'warning' | 'info'> = {
  draft: 'info',
  open: 'success',
  paused: 'warning',
  closed: 'info',
}

function formatValue(value: unknown) {
  if (value == null || value === '') return '—'
  if (Array.isArray(value)) {
    if (!value.length) return '—'
    if (typeof value[0] === 'object') return JSON.stringify(value, null, 2)
    return value.join('\n')
  }
  if (typeof value === 'object') return JSON.stringify(value, null, 2)
  return String(value)
}

async function loadVersions() {
  if (!job.value) return
  versionsLoading.value = true
  try {
    const data = await listJobVersions(job.value.id)
    versions.value = data.items
    const published = data.items.filter((item) => !item.is_draft)
    if (published.length >= 2) {
      compareTo.value = published[0].id
      compareFrom.value = published[1].id
    } else if (published.length === 1) {
      compareTo.value = published[0].id
      compareFrom.value = published[0].id
    }
  } catch {
    ElMessage.error('加载版本历史失败')
  } finally {
    versionsLoading.value = false
  }
}

async function loadCandidates() {
  if (!job.value) return
  candidatesLoading.value = true
  try {
    const data = await listJobCandidates(job.value.id)
    candidates.value = data.items
  } catch {
    ElMessage.error('加载候选人失败')
  } finally {
    candidatesLoading.value = false
  }
}

function onUploadFileChange(file: { raw?: File }) {
  if (file.raw) {
    if (uploadFiles.value.length >= 5) {
      ElMessage.warning('最多 5 份')
      return false
    }
    uploadFiles.value.push(file.raw)
  }
  return false
}

async function submitJobUpload() {
  if (!job.value || !uploadFiles.value.length) return
  uploading.value = true
  try {
    const res = await uploadResumes({ files: uploadFiles.value, jobId: job.value.id })
    ElMessage.success(`已上传 ${res.items.length} 份并启动解析`)
    dialogUpload.value = false
    uploadFiles.value = []
    tab.value = 'candidates'
    await loadCandidates()
    const firstPending = res.items.find((i) => i.resume_version_id)
    if (firstPending) {
      await router.push({
        name: 'resume-review',
        params: { versionId: firstPending.resume_version_id },
      })
    }
  } catch (err: unknown) {
    const detail =
      (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
      '上传失败'
    ElMessage.error(String(detail))
  } finally {
    uploading.value = false
  }
}

async function load() {
  loading.value = true
  try {
    job.value = await getJob(route.params.id as string)
    await loadVersions()
    if (tab.value === 'candidates') await loadCandidates()
  } catch {
    ElMessage.error('加载岗位失败')
    await router.push({ name: 'jobs' })
  } finally {
    loading.value = false
  }
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

async function onPause() {
  if (!job.value) return
  try {
    const reason = await askReason('暂停岗位')
    job.value = await pauseJob(job.value.id, reason)
    ElMessage.success('已暂停')
  } catch (err) {
    if (err !== 'cancel') ElMessage.error('暂停失败')
  }
}

async function onResume() {
  if (!job.value) return
  try {
    const reason = await askReason('恢复招聘')
    job.value = await resumeJob(job.value.id, reason)
    ElMessage.success('已恢复')
  } catch (err) {
    if (err !== 'cancel') ElMessage.error('恢复失败')
  }
}

async function onClose() {
  if (!job.value) return
  try {
    const preview = await getClosePreview(job.value.id)
    if (preview.in_flight_count > 0) {
      await router.push({ name: 'job-close', params: { id: job.value.id } })
      return
    }
    const reason = await askReason('关闭岗位')
    job.value = await closeJob(job.value.id, reason)
    ElMessage.success('已关闭')
  } catch (err) {
    if (err !== 'cancel') ElMessage.error('关闭失败')
  }
}

async function onAddCandidate() {
  if (!job.value) return
  const name = newCandidateName.value.trim()
  if (!name) {
    ElMessage.warning('请填写候选人姓名')
    return
  }
  try {
    await createJobCandidate(job.value.id, { name })
    newCandidateName.value = ''
    ElMessage.success('已绑定到当前岗位版本')
    await loadCandidates()
    await loadVersions()
  } catch {
    ElMessage.error('添加候选人失败')
  }
}

async function onMigrate(row: JobApplication) {
  if (!job.value) return
  const toVersionId = migrateTarget.value[row.id]
  if (!toVersionId) {
    ElMessage.warning('请选择目标版本')
    return
  }
  const reason = (migrateReason.value[row.id] || '').trim()
  if (row.interview_started && !reason) {
    ElMessage.warning('已开始面试的迁移必须填写原因')
    return
  }
  try {
    if (row.interview_started) {
      await ElMessageBox.confirm(
        '该候选人已开始面试，迁移将标记面试任务为待取消/待重建（占位）。确认继续？',
        '高风险迁移',
        { type: 'warning' },
      )
    }
    await migrateApplicationVersion(job.value.id, row.id, {
      to_version_id: toVersionId,
      reason: reason || undefined,
    })
    ElMessage.success('已迁移版本')
    await loadCandidates()
    await loadVersions()
  } catch (err) {
    if (err !== 'cancel') ElMessage.error('迁移失败')
  }
}

async function onCopy() {
  if (!job.value) return
  try {
    const copied = await copyJob(job.value.id)
    ElMessage.success(`已复制为 ${copied.code}`)
    await router.push({ name: 'job-edit', params: { id: copied.id } })
  } catch {
    ElMessage.error('复制失败')
  }
}

async function openVersion(row: JobVersionListItem) {
  if (!job.value) return
  try {
    activeVersion.value = await getJobVersion(job.value.id, row.id)
    drawerVisible.value = true
  } catch {
    ElMessage.error('加载版本详情失败')
  }
}

async function onCopyVersionToDraft(row: JobVersionListItem) {
  if (!job.value) return
  try {
    await ElMessageBox.confirm(
      `将用 ${row.version_label || '该版本'} 覆盖当前未发布草稿（若无草稿则创建），是否继续？`,
      '复制此版本为新草稿',
      { type: 'warning' },
    )
    job.value = await copyVersionToDraft(job.value.id, row.id)
    ElMessage.success('已生成未发布草稿')
    await loadVersions()
    await router.push({ name: 'job-edit', params: { id: job.value.id } })
  } catch (err) {
    if (err !== 'cancel') ElMessage.error('复制为草稿失败')
  }
}

async function runDiff() {
  if (!job.value || !compareFrom.value || !compareTo.value) return
  if (compareFrom.value === compareTo.value) {
    ElMessage.warning('请选择两个不同版本对比')
    return
  }
  diffLoading.value = true
  try {
    diffResult.value = await diffJobVersions(job.value.id, compareFrom.value, compareTo.value)
  } catch {
    ElMessage.error('对比失败')
  } finally {
    diffLoading.value = false
  }
}

watch(tab, (value) => {
  if (value === 'versions' && job.value && !versions.value.length) {
    void loadVersions()
  }
  if (value === 'candidates' && job.value) {
    void loadCandidates()
  }
})

onMounted(() => {
  if (route.query.tab === 'versions') tab.value = 'versions'
  if (route.query.tab === 'candidates') tab.value = 'candidates'
  void load()
})
</script>

<template>
  <AdminLayout>
    <div v-loading="loading" class="page">
      <template v-if="job">
        <div class="panel-head">
          <div>
            <el-button link type="primary" @click="router.push({ name: 'jobs' })">← 返回岗位管理</el-button>
            <h2>{{ job.name || '未命名岗位' }}</h2>
            <p class="meta">
              {{ job.code }} · {{ job.department || '—' }} · {{ job.location || '—' }}
            </p>
          </div>
          <div class="actions">
            <el-tag :type="statusType[job.status]" size="small">{{ job.status_label }}</el-tag>
            <el-button
              v-if="job.status === 'open' || job.status === 'paused'"
              @click="dialogUpload = true"
            >
              上传简历
            </el-button>
            <el-button
              v-if="job.status !== 'closed'"
              type="primary"
              @click="router.push({ name: 'job-edit', params: { id: job.id } })"
            >
              编辑
            </el-button>
            <el-button v-if="job.status === 'open'" type="warning" plain @click="onPause">暂停</el-button>
            <el-button v-if="job.status === 'paused'" type="success" plain @click="onResume">恢复</el-button>
            <el-button
              v-if="job.status === 'open' || job.status === 'paused'"
              type="danger"
              plain
              @click="onClose"
            >
              关闭
            </el-button>
            <el-button @click="onCopy">复制为新岗位</el-button>
          </div>
        </div>

        <el-alert
          v-if="job.draft_version_id && job.status !== 'draft'"
          title="存在未发布草稿，编辑可继续完善后发布新版本"
          type="warning"
          show-icon
          :closable="false"
          style="margin-bottom: 16px"
        />

        <el-tabs v-model="tab">
          <el-tab-pane label="概览" name="overview">
            <el-descriptions :column="2" border>
              <el-descriptions-item label="负责人">{{ job.owner_name || '—' }}</el-descriptions-item>
              <el-descriptions-item label="招聘人数">{{ job.headcount ?? '—' }}</el-descriptions-item>
              <el-descriptions-item label="职级">{{ job.level || '—' }}</el-descriptions-item>
              <el-descriptions-item label="紧急程度">{{ job.urgency || '—' }}</el-descriptions-item>
              <el-descriptions-item label="当前版本">
                {{ job.current_version?.version_label || '—' }}
              </el-descriptions-item>
              <el-descriptions-item label="草稿版本">
                {{ job.draft_version_id ? '有未发布草稿' : '无' }}
              </el-descriptions-item>
              <el-descriptions-item v-if="job.pause_reason" label="暂停原因">
                {{ job.pause_reason }}
              </el-descriptions-item>
              <el-descriptions-item v-if="job.close_reason" label="关闭原因">
                {{ job.close_reason }}
              </el-descriptions-item>
            </el-descriptions>
          </el-tab-pane>

          <el-tab-pane label="JD / 评分" name="jd">
            <template v-if="version">
              <h3>岗位职责</h3>
              <ol>
                <li v-for="(item, idx) in version.structured_jd.responsibilities" :key="`r-${idx}`">
                  {{ item }}
                </li>
              </ol>
              <h3>任职要求</h3>
              <ol>
                <li v-for="(item, idx) in version.structured_jd.requirements" :key="`q-${idx}`">
                  {{ item }}
                </li>
              </ol>
              <h3>评分维度</h3>
              <el-table :data="version.score_dimensions" stripe>
                <el-table-column prop="name" label="维度" />
                <el-table-column prop="weight" label="权重" width="100">
                  <template #default="{ row }">{{ row.weight }}%</template>
                </el-table-column>
                <el-table-column prop="description" label="说明" />
              </el-table>
            </template>
            <el-empty v-else description="暂无版本内容" />
          </el-tab-pane>

          <el-tab-pane label="版本历史" name="versions">
            <div class="compare-bar">
              <span>版本对比</span>
              <el-select v-model="compareFrom" placeholder="源版本" style="width: 160px">
                <el-option
                  v-for="item in publishedVersions"
                  :key="`from-${item.id}`"
                  :label="item.version_label || '未命名'"
                  :value="item.id"
                />
              </el-select>
              <span>→</span>
              <el-select v-model="compareTo" placeholder="目标版本" style="width: 160px">
                <el-option
                  v-for="item in publishedVersions"
                  :key="`to-${item.id}`"
                  :label="item.version_label || '未命名'"
                  :value="item.id"
                />
              </el-select>
              <el-button type="primary" plain :loading="diffLoading" @click="runDiff">对比</el-button>
            </div>

            <el-table v-loading="versionsLoading" :data="versions" stripe class="version-table">
              <el-table-column label="版本号" width="110">
                <template #default="{ row }">
                  <el-link type="primary" @click="openVersion(row)">
                    {{ row.version_label || (row.is_draft ? '草稿' : '—') }}
                  </el-link>
                </template>
              </el-table-column>
              <el-table-column label="发布时间" width="170">
                <template #default="{ row }">
                  {{ row.published_at || row.updated_at }}
                </template>
              </el-table-column>
              <el-table-column prop="upgrade_type_label" label="升级类型" width="110" />
              <el-table-column prop="change_summary" label="变更摘要" min-width="180" />
              <el-table-column label="状态" width="110">
                <template #default="{ row }">
                  <el-tag :type="row.is_current ? 'success' : 'info'" size="small">
                    {{ row.status_label }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="绑定候选人" width="110" align="center">
                <template #default="{ row }">
                  {{ row.bound_candidates || '—' }}
                </template>
              </el-table-column>
              <el-table-column label="操作" width="180" fixed="right">
                <template #default="{ row }">
                  <el-button link type="primary" size="small" @click="openVersion(row)">查看</el-button>
                  <el-button
                    v-if="canCopyVersionToDraft && !row.is_draft"
                    link
                    type="primary"
                    size="small"
                    @click="onCopyVersionToDraft(row)"
                  >
                    复制为草稿
                  </el-button>
                </template>
              </el-table-column>
            </el-table>
            <p class="hint">历史版本只读，不可删除或覆盖；发布后版本号只增不减。</p>

            <div v-if="diffResult" class="diff-panel">
              <h3>
                差异：{{ diffResult.from_version.version_label }} →
                {{ diffResult.to_version.version_label }}
              </h3>
              <el-empty v-if="!diffResult.has_changes" description="两个版本内容一致" />
              <el-table v-else :data="diffResult.changes" stripe>
                <el-table-column prop="label" label="字段" width="140" />
                <el-table-column label="变更前" min-width="220">
                  <template #default="{ row }">
                    <pre class="diff-pre">{{ formatValue(row.before) }}</pre>
                  </template>
                </el-table-column>
                <el-table-column label="变更后" min-width="220">
                  <template #default="{ row }">
                    <pre class="diff-pre">{{ formatValue(row.after) }}</pre>
                  </template>
                </el-table-column>
              </el-table>
            </div>
          </el-tab-pane>

          <el-tab-pane label="候选人" name="candidates">
            <div class="compare-bar">
              <el-button type="primary" @click="dialogUpload = true">上传简历（绑定本岗位版本）</el-button>
              <el-input
                v-model="newCandidateName"
                placeholder="仅姓名快速绑定（无简历）"
                style="max-width: 280px"
                @keyup.enter="onAddCandidate"
              />
              <el-button @click="onAddCandidate">添加并绑定</el-button>
            </div>
            <p class="hint" v-if="job.current_version?.version_label">
              上传将绑定当前生效版本：{{ job.current_version.version_label }}
            </p>
            <el-table v-loading="candidatesLoading" :data="candidates" stripe>
              <el-table-column prop="candidate_name" label="姓名" width="120" />
              <el-table-column label="流程状态" width="120">
                <template #default="{ row }">
                  {{
                    pipelineStatusLabel[(row.pipeline_status as PipelineStatus) || 'pending_hr_screen'] ||
                    row.pipeline_status ||
                    row.status
                  }}
                </template>
              </el-table-column>
              <el-table-column label="关闭状态" width="110">
                <template #default="{ row }">{{ row.status }}</template>
              </el-table-column>
              <el-table-column label="面试" width="90">
                <template #default="{ row }">
                  {{ row.interview_started ? '已开始' : '未开始' }}
                </template>
              </el-table-column>
              <el-table-column label="绑定版本" min-width="120">
                <template #default="{ row }">
                  {{
                    versions.find((v) => v.id === row.job_version_id)?.version_label ||
                    row.job_version_id.slice(0, 8)
                  }}
                </template>
              </el-table-column>
              <el-table-column label="操作" min-width="240" fixed="right">
                <template #default="{ row }">
                  <el-button
                    v-if="row.resume_version_id"
                    link
                    type="primary"
                    size="small"
                    @click="
                      router.push({
                        name: 'resume-review',
                        params: { versionId: row.resume_version_id },
                      })
                    "
                  >
                    校对
                  </el-button>
                  <el-button
                    link
                    type="primary"
                    size="small"
                    @click="
                      router.push({
                        name: 'score-report',
                        params: { applicationId: row.id },
                      })
                    "
                  >
                    匹配报告
                  </el-button>
                  <template v-if="row.status === 'in_progress'">
                    <el-select
                      v-model="migrateTarget[row.id]"
                      placeholder="迁移"
                      size="small"
                      style="width: 100px; margin: 0 6px"
                    >
                      <el-option
                        v-for="item in publishedVersions"
                        :key="item.id"
                        :label="item.version_label || '—'"
                        :value="item.id"
                        :disabled="item.id === row.job_version_id"
                      />
                    </el-select>
                    <el-button link type="primary" size="small" @click="onMigrate(row)">迁移</el-button>
                  </template>
                </template>
              </el-table-column>
            </el-table>
            <p class="hint">评分与筛选不自动改状态；岗位版本迁移后需重新评分。</p>
          </el-tab-pane>
        </el-tabs>

        <el-dialog v-model="dialogUpload" title="上传简历到本岗位" width="520px" @closed="uploadFiles = []">
          <el-alert
            :title="`将绑定岗位版本 ${job.current_version?.version_label || '当前生效版'}，上传后进入解析与校对`"
            type="info"
            show-icon
            :closable="false"
            style="margin-bottom: 12px"
          />
          <el-upload
            drag
            :auto-upload="false"
            :show-file-list="false"
            :on-change="onUploadFileChange"
            multiple
            accept=".pdf,.doc,.docx,.txt"
          >
            <div>拖拽或点击上传，最多 5 份</div>
          </el-upload>
          <ul class="upload-files">
            <li v-for="(f, i) in uploadFiles" :key="i">
              {{ f.name }}
              <el-button link type="danger" @click="uploadFiles.splice(i, 1)">移除</el-button>
            </li>
          </ul>
          <template #footer>
            <el-button @click="dialogUpload = false">取消</el-button>
            <el-button
              type="primary"
              :disabled="!uploadFiles.length"
              :loading="uploading"
              @click="submitJobUpload"
            >
              上传并解析
            </el-button>
          </template>
        </el-dialog>
      </template>
    </div>

    <el-drawer
      v-model="drawerVisible"
      :title="`JD ${activeVersion?.version_label || ''}`"
      size="520px"
      direction="rtl"
    >
      <template v-if="activeVersion">
        <el-descriptions :column="1" border size="small" style="margin-bottom: 16px">
          <el-descriptions-item label="升级类型">
            {{ activeVersion.upgrade_type || '—' }}
          </el-descriptions-item>
          <el-descriptions-item label="变更摘要">
            {{ activeVersion.change_summary || '—' }}
          </el-descriptions-item>
          <el-descriptions-item label="发布时间">
            {{ activeVersion.published_at || '—' }}
          </el-descriptions-item>
        </el-descriptions>
        <h4>岗位职责</h4>
        <ol>
          <li
            v-for="(item, idx) in activeVersion.structured_jd.responsibilities"
            :key="`dr-${idx}`"
          >
            {{ item }}
          </li>
        </ol>
        <h4>任职要求</h4>
        <ol>
          <li
            v-for="(item, idx) in activeVersion.structured_jd.requirements"
            :key="`dq-${idx}`"
          >
            {{ item }}
          </li>
        </ol>
        <h4>评分维度</h4>
        <ul>
          <li v-for="(dim, idx) in activeVersion.score_dimensions" :key="`dd-${idx}`">
            {{ dim.name }}（{{ dim.weight }}%）
          </li>
        </ul>
        <p v-if="activeVersion.raw_jd_text" class="raw-jd">{{ activeVersion.raw_jd_text }}</p>
      </template>
    </el-drawer>
  </AdminLayout>
</template>

<style scoped>
.page {
  max-width: 1100px;
}
.panel-head {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}
.panel-head h2 {
  margin: 8px 0 4px;
  font-size: 22px;
}
.meta {
  margin: 0;
  color: var(--muted);
  font-size: 13px;
}
.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: flex-start;
}
h3,
h4 {
  margin: 16px 0 8px;
  font-size: 16px;
}
.compare-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  margin-bottom: 14px;
}
.hint {
  margin-top: 12px;
  color: var(--muted);
  font-size: 12px;
}
.upload-files {
  list-style: none;
  padding: 0;
  margin: 12px 0 0;
}
.upload-files li {
  display: flex;
  justify-content: space-between;
  font-size: 13px;
  padding: 4px 0;
}
.diff-panel {
  margin-top: 24px;
  border-top: 1px solid var(--border);
  padding-top: 16px;
}
.diff-pre {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.5;
}
.raw-jd {
  margin-top: 16px;
  white-space: pre-wrap;
  font-size: 13px;
  line-height: 1.6;
  color: var(--muted);
}
</style>
