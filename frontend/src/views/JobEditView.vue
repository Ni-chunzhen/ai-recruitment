<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type { AxiosError } from 'axios'

import {
  applyAITask,
  createAITask,
  pollAITask,
  retryAITask,
  type AITask,
} from '../api/aiTasks'
import {
  createJob,
  emptyStructuredJd,
  getJob,
  publishJob,
  saveJobDraft,
  type JobDetail,
  type ScoreDimension,
  type StructuredJd,
} from '../api/jobs'
import AdminLayout from '../layouts/AdminLayout.vue'

const route = useRoute()
const router = useRouter()

const jobId = computed(() => (route.params.id as string | undefined) || '')
const isCreate = computed(() => route.name === 'job-create' || !jobId.value)

const loading = ref(false)
const saving = ref(false)
const job = ref<JobDetail | null>(null)
const step = ref(0)

const basic = reactive({
  name: '',
  department: '',
  level: '' as string | null,
  headcount: 1 as number | null,
  location: '',
  owner_name: '',
  urgency: '普通' as string | null,
})

const rawJdText = ref('')
const structuredJd = reactive<StructuredJd>(emptyStructuredJd())
const dimensions = ref<ScoreDimension[]>([])
const newSkill = ref('')

const jdTask = ref<AITask | null>(null)
const dimTask = ref<AITask | null>(null)
const jdPending = ref<StructuredJd | null>(null)
const dimPending = ref<ScoreDimension[] | null>(null)
const jdRunning = ref(false)
const dimRunning = ref(false)
const applying = ref(false)
let pollAbort: AbortController | null = null

const weightTotal = computed(() =>
  dimensions.value.reduce((sum, item) => sum + Number(item.weight || 0), 0),
)

const editingPublishedDraft = computed(
  () =>
    !!job.value &&
    (job.value.status === 'open' || job.value.status === 'paused') &&
    !!job.value.draft_version_id &&
    !!job.value.current_version,
)

const draftBanner = computed(() => {
  if (!editingPublishedDraft.value || !job.value?.current_version?.version_label) return ''
  return `正在编辑基于 ${job.value.current_version.version_label} 的未发布草稿`
})

const jdStatusLabel = computed(() => {
  if (jdRunning.value) return 'AI 解析处理中…'
  if (jdTask.value?.status === 'succeeded') return 'AI 解析完成，请确认后应用'
  if (jdTask.value?.status === 'failed') return `AI 解析失败：${jdTask.value.error_message || '未知错误'}`
  return ''
})

const dimStatusLabel = computed(() => {
  if (dimRunning.value) return '评分维度推荐处理中…'
  if (dimTask.value?.status === 'succeeded') return '维度推荐完成，请确认后应用'
  if (dimTask.value?.status === 'failed') {
    return `维度推荐失败：${dimTask.value.error_message || '未知错误'}`
  }
  return ''
})

function linesToList(text: string) {
  return text
    .split('\n')
    .map((item) => item.trim())
    .filter(Boolean)
}

function listToLines(items: string[]) {
  return items.join('\n')
}

function applyJob(detail: JobDetail) {
  job.value = detail
  basic.name = detail.name
  basic.department = detail.department
  basic.level = detail.level
  basic.headcount = detail.headcount ?? 1
  basic.location = detail.location
  basic.owner_name = detail.owner_name
  basic.urgency = detail.urgency || '普通'

  const version = detail.draft_version || detail.current_version
  rawJdText.value = version?.raw_jd_text || ''
  const jd = version?.structured_jd || emptyStructuredJd()
  structuredJd.responsibilities = [...jd.responsibilities]
  structuredJd.requirements = [...jd.requirements]
  structuredJd.must_have = [...jd.must_have]
  structuredJd.nice_to_have = [...jd.nice_to_have]
  structuredJd.skills = [...jd.skills]
  dimensions.value = (version?.score_dimensions || []).map((item) => ({
    name: item.name,
    weight: item.weight,
    description: item.description,
    anchors: [...(item.anchors || [])],
    custom: item.custom,
  }))
}

function buildPayload() {
  return {
    name: basic.name,
    department: basic.department,
    level: basic.level,
    headcount: basic.headcount,
    location: basic.location,
    owner_name: basic.owner_name,
    urgency: basic.urgency,
    raw_jd_text: rawJdText.value,
    structured_jd: {
      responsibilities: [...structuredJd.responsibilities],
      requirements: [...structuredJd.requirements],
      must_have: [...structuredJd.must_have],
      nice_to_have: [...structuredJd.nice_to_have],
      skills: [...structuredJd.skills],
    },
    score_dimensions: dimensions.value.map((item) => ({
      name: item.name,
      weight: Number(item.weight),
      description: item.description,
      anchors: item.anchors,
      custom: item.custom,
    })),
  }
}

async function ensureJobSaved() {
  const payload = buildPayload()
  if (isCreate.value && !job.value) {
    const created = await createJob(payload)
    applyJob(created)
    await router.replace({ name: 'job-edit', params: { id: created.id } })
    return created
  }
  const saved = await saveJobDraft(job.value!.id, payload)
  applyJob(saved)
  return saved
}

async function onSaveDraft() {
  saving.value = true
  try {
    await ensureJobSaved()
    ElMessage.success('草稿已保存')
  } catch {
    ElMessage.error('保存失败')
  } finally {
    saving.value = false
  }
}

function validationMessage(error: unknown) {
  const axiosError = error as AxiosError<{
    detail?: { errors?: { field: string; message: string }[] } | string
  }>
  const detail = axiosError.response?.data?.detail
  if (detail && typeof detail === 'object' && Array.isArray(detail.errors)) {
    return detail.errors.map((item) => item.message).join('；')
  }
  if (typeof detail === 'string') return detail
  return '发布失败'
}

async function onPublish() {
  saving.value = true
  try {
    const current = await ensureJobSaved()
    const published = await publishJob(current.id)
    applyJob(published)
    ElMessage.success(`已发布 ${published.current_version?.version_label || ''}`)
    await router.push({ name: 'job-detail', params: { id: published.id } })
  } catch (error) {
    ElMessage.error(validationMessage(error))
  } finally {
    saving.value = false
  }
}

function addSkill() {
  const text = newSkill.value.trim()
  if (!text) return
  if (!structuredJd.skills.includes(text)) structuredJd.skills.push(text)
  newSkill.value = ''
}

function removeSkill(index: number) {
  structuredJd.skills.splice(index, 1)
}

function addDimension() {
  dimensions.value.push({
    name: '新维度',
    weight: 10,
    description: '',
    anchors: ['', '', '', '', ''],
    custom: true,
  })
}

function removeDimension(index: number) {
  dimensions.value.splice(index, 1)
}

function taskBusinessResult(task: AITask): Record<string, unknown> {
  return (
    (task as unknown as { result_payload?: Record<string, unknown> | null }).result_payload ||
    {}
  )
}

function extractJdPending(task: AITask) {
  const payload = taskBusinessResult(task)
  return {
    responsibilities: [...((payload.responsibilities as string[]) || [])],
    requirements: [...((payload.requirements as string[]) || [])],
    must_have: [...((payload.must_have as string[]) || [])],
    nice_to_have: [...((payload.nice_to_have as string[]) || [])],
    skills: [...((payload.skills as string[]) || [])],
  }
}

function extractDimPending(task: AITask) {
  const dims = (taskBusinessResult(task).dimensions as ScoreDimension[]) || []
  return dims.map((item) => ({
    name: item.name,
    weight: Number(item.weight),
    description: item.description || '',
    anchors: [...(item.anchors || ['', '', '', '', ''])],
    custom: false,
  }))
}

async function runAndPoll(
  kind: 'jd' | 'dim',
  starter: () => Promise<AITask>,
) {
  pollAbort?.abort()
  pollAbort = new AbortController()
  if (kind === 'jd') {
    jdRunning.value = true
    jdPending.value = null
  } else {
    dimRunning.value = true
    dimPending.value = null
  }
  try {
    const created = await starter()
    if (kind === 'jd') jdTask.value = created
    else dimTask.value = created
    const finished = await pollAITask(created.id, { signal: pollAbort.signal })
    if (kind === 'jd') {
      jdTask.value = finished
      if (finished.status === 'succeeded') {
        jdPending.value = extractJdPending(finished)
        const dims = extractDimPending(finished)
        if (dims.length) dimPending.value = dims
      }
    } else {
      dimTask.value = finished
      if (finished.status === 'succeeded') dimPending.value = extractDimPending(finished)
    }
    if (finished.status === 'failed') {
      ElMessage.error(finished.error_message || 'AI 任务失败')
    } else {
      ElMessage.success(
        kind === 'jd'
          ? 'AI 已完成 JD 结构化与能力维度生成，请确认后应用'
          : 'AI 结果已就绪，请确认后应用',
      )
    }
  } catch (error) {
    if ((error as Error).name !== 'AbortError') {
      ElMessage.error((error as Error).message || 'AI 任务失败')
    }
  } finally {
    if (kind === 'jd') jdRunning.value = false
    else dimRunning.value = false
  }
}

async function onParseJd() {
  if (!rawJdText.value.trim()) {
    ElMessage.warning('请先填写原始 JD 文本')
    return
  }
  await runAndPoll('jd', async () => {
    const current = await ensureJobSaved()
    return createAITask({
      task_type: 'JD_PARSE',
      business_id: current.id,
      input: { raw_jd_text: rawJdText.value },
    })
  })
  if (jdTask.value?.status === 'succeeded') step.value = 1
}

async function onRecommendDimensions() {
  await runAndPoll('dim', async () => {
    const current = await ensureJobSaved()
    return createAITask({
      task_type: 'SCORE_DIMENSION_RECOMMEND',
      business_id: current.id,
      input: {
        structured_jd: {
          responsibilities: [...structuredJd.responsibilities],
          requirements: [...structuredJd.requirements],
          must_have: [...structuredJd.must_have],
          nice_to_have: [...structuredJd.nice_to_have],
          skills: [...structuredJd.skills],
        },
      },
    })
  })
  if (dimTask.value?.status === 'succeeded') step.value = 2
}

async function onRetry(kind: 'jd' | 'dim') {
  const task = kind === 'jd' ? jdTask.value : dimTask.value
  if (!task || task.status !== 'failed') return
  await runAndPoll(kind, () => retryAITask(task.id))
}

async function onApply(kind: 'jd' | 'dim') {
  const task = kind === 'jd' ? jdTask.value : dimTask.value
  if (!task || task.status !== 'succeeded') return
  applying.value = true
  try {
    const detail = await applyAITask(task.id)
    applyJob(detail)
    if (kind === 'jd') {
      jdPending.value = null
      // JD_PARSE 串联维度时 apply 已一并写入
      if ((taskBusinessResult(task).dimensions as ScoreDimension[] | undefined)?.length) {
        dimPending.value = null
      }
    } else {
      dimPending.value = null
    }
    ElMessage.success('已应用到草稿（尚未发布）')
  } catch (error) {
    ElMessage.error(validationMessage(error) || '应用失败')
  } finally {
    applying.value = false
  }
}

function discardPending(kind: 'jd' | 'dim') {
  if (kind === 'jd') {
    jdPending.value = null
    // 同源串联结果一并丢弃
    if (jdTask.value && taskBusinessResult(jdTask.value).dimensions) dimPending.value = null
  } else {
    dimPending.value = null
  }
}

async function load() {
  if (isCreate.value) return
  loading.value = true
  try {
    const detail = await getJob(jobId.value)
    applyJob(detail)
  } catch {
    ElMessage.error('加载岗位失败')
    await router.push({ name: 'jobs' })
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  void load()
})

onBeforeUnmount(() => {
  pollAbort?.abort()
})
</script>

<template>
  <AdminLayout>
    <div v-loading="loading" class="wizard">
      <div class="sticky-head">
        <div class="left">
          <el-button text @click="router.push({ name: 'jobs' })">← 返回岗位管理</el-button>
          <el-tag v-if="draftBanner" type="warning" effect="plain">{{ draftBanner }}</el-tag>
        </div>
        <div class="right">
          <el-button :loading="saving" @click="onSaveDraft">保存草稿</el-button>
          <el-button type="primary" :loading="saving" @click="onPublish">发布岗位</el-button>
        </div>
      </div>

      <el-alert
        v-if="draftBanner"
        :title="draftBanner"
        type="warning"
        show-icon
        :closable="false"
        style="margin-bottom: 16px"
      />

      <div class="steps">
        <button type="button" class="step" :class="{ active: step === 0 }" @click="step = 0">1 基础信息</button>
        <button type="button" class="step" :class="{ active: step === 1 }" @click="step = 1">2 结构化 JD</button>
        <button type="button" class="step" :class="{ active: step === 2 }" @click="step = 2">3 评分维度</button>
        <button type="button" class="step" :class="{ active: step === 3 }" @click="step = 3">4 预览发布</button>
      </div>

      <section v-show="step === 0" class="panel">
        <h3>岗位基础信息</h3>
        <el-form label-position="top">
          <el-form-item label="岗位名称" required>
            <el-input v-model="basic.name" placeholder="例如：大模型应用产品经理" />
          </el-form-item>
          <div class="grid-2">
            <el-form-item label="所属部门" required>
              <el-input v-model="basic.department" />
            </el-form-item>
            <el-form-item label="职级/级别">
              <el-select v-model="basic.level" clearable style="width: 100%">
                <el-option label="初级" value="初级" />
                <el-option label="中级" value="中级" />
                <el-option label="高级" value="高级" />
                <el-option label="专家" value="专家" />
              </el-select>
            </el-form-item>
          </div>
          <div class="grid-3">
            <el-form-item label="招聘人数" required>
              <el-input-number v-model="basic.headcount" :min="1" style="width: 100%" />
            </el-form-item>
            <el-form-item label="工作地点">
              <el-input v-model="basic.location" />
            </el-form-item>
            <el-form-item label="招聘负责人" required>
              <el-input v-model="basic.owner_name" />
            </el-form-item>
          </div>
          <el-form-item label="紧急程度">
            <el-radio-group v-model="basic.urgency">
              <el-radio value="普通">普通</el-radio>
              <el-radio value="紧急">紧急</el-radio>
            </el-radio-group>
          </el-form-item>
          <el-form-item label="原始 JD">
            <el-input v-model="rawJdText" type="textarea" :rows="8" placeholder="粘贴完整 JD 文本…" />
          </el-form-item>
          <div class="ai-bar">
            <el-button type="primary" plain :loading="jdRunning" @click="onParseJd">AI 解析 JD</el-button>
            <el-button
              v-if="jdTask?.status === 'failed'"
              type="danger"
              plain
              :loading="jdRunning"
              @click="onRetry('jd')"
            >
              重试解析
            </el-button>
            <span v-if="jdStatusLabel" class="ai-status" :class="{ fail: jdTask?.status === 'failed' }">
              {{ jdStatusLabel }}
            </span>
          </div>
          <p class="hint">AI 解析会先做 JD 结构化，再生成能力维度；异步执行不阻塞手工填写，结果需确认后才写入草稿。SOP 不进发布校验。</p>
        </el-form>
      </section>

      <section v-show="step === 1" class="panel">
        <div class="jd-head">
          <h3>结构化 JD</h3>
          <el-button type="primary" plain :loading="jdRunning" @click="onParseJd">重新 AI 解析</el-button>
        </div>

        <div v-if="jdPending" class="pending-box">
          <div class="jd-head">
            <strong>待确认：AI 解析结果（不会自动覆盖当前内容）</strong>
            <div>
              <el-button size="small" @click="discardPending('jd')">丢弃</el-button>
              <el-button size="small" type="primary" :loading="applying" @click="onApply('jd')">
                应用到草稿
              </el-button>
            </div>
          </div>
          <pre class="pending-pre">{{ JSON.stringify(jdPending, null, 2) }}</pre>
        </div>

        <div v-for="key in ['responsibilities', 'requirements', 'must_have', 'nice_to_have'] as const" :key="key" class="jd-block">
          <div class="jd-head">
            <strong>
              {{
                {
                  responsibilities: '岗位职责',
                  requirements: '任职要求',
                  must_have: '必备要求',
                  nice_to_have: '加分项',
                }[key]
              }}
            </strong>
          </div>
          <el-input
            :model-value="listToLines(structuredJd[key])"
            type="textarea"
            :rows="4"
            placeholder="每行一条"
            @update:model-value="(val: string) => (structuredJd[key] = linesToList(val))"
          />
        </div>
        <div class="jd-block">
          <div class="jd-head"><strong>技能关键词</strong></div>
          <div class="skills">
            <el-tag
              v-for="(skill, index) in structuredJd.skills"
              :key="skill"
              closable
              @close="removeSkill(index)"
            >
              {{ skill }}
            </el-tag>
          </div>
          <div class="skill-add">
            <el-input v-model="newSkill" placeholder="输入后添加" @keyup.enter="addSkill" />
            <el-button @click="addSkill">添加</el-button>
          </div>
        </div>
      </section>

      <section v-show="step === 2" class="panel">
        <div class="jd-head">
          <h3>评分维度</h3>
          <div class="ai-bar">
            <el-button type="primary" plain :loading="dimRunning" @click="onRecommendDimensions">
              AI 推荐评分维度
            </el-button>
            <el-button
              v-if="dimTask?.status === 'failed'"
              type="danger"
              plain
              :loading="dimRunning"
              @click="onRetry('dim')"
            >
              重试推荐
            </el-button>
            <span>
              权重合计
              <strong :class="weightTotal === 100 ? 'ok' : 'bad'">{{ weightTotal }}%</strong>
            </span>
          </div>
        </div>
        <p v-if="dimStatusLabel" class="ai-status" :class="{ fail: dimTask?.status === 'failed' }">
          {{ dimStatusLabel }}
        </p>

        <div v-if="dimPending" class="pending-box">
          <div class="jd-head">
            <strong>待确认：AI 推荐维度（不会自动覆盖当前内容）</strong>
            <div>
              <el-button size="small" @click="discardPending('dim')">丢弃</el-button>
              <el-button size="small" type="primary" :loading="applying" @click="onApply('dim')">
                应用到草稿
              </el-button>
            </div>
          </div>
          <pre class="pending-pre">{{ JSON.stringify(dimPending, null, 2) }}</pre>
        </div>

        <el-alert
          v-if="dimensions.length > 0 && dimensions.length < 3"
          title="维度少于 3 个，建议补充（不阻塞发布）"
          type="warning"
          show-icon
          :closable="false"
          style="margin-bottom: 12px"
        />
        <div v-for="(dim, index) in dimensions" :key="index" class="dim-card">
          <div class="dim-head">
            <el-input v-model="dim.name" placeholder="维度名称" />
            <el-button link type="danger" @click="removeDimension(index)">删除</el-button>
          </div>
          <el-input v-model="dim.description" type="textarea" :rows="2" placeholder="维度说明" />
          <div class="weight-row">
            <span>权重</span>
            <el-slider v-model="dim.weight" :min="0" :max="100" :step="5" />
            <strong>{{ dim.weight }}%</strong>
          </div>
          <div v-for="score in 5" :key="score" class="anchor-row">
            <span>{{ score }} 分</span>
            <el-input v-model="dim.anchors[score - 1]" placeholder="评分锚点描述" />
          </div>
        </div>
        <el-button style="width: 100%" @click="addDimension">+ 添加维度</el-button>
      </section>

      <section v-show="step === 3" class="panel">
        <h3>预览发布</h3>
        <el-descriptions :column="2" border>
          <el-descriptions-item label="岗位名称">{{ basic.name || '—' }}</el-descriptions-item>
          <el-descriptions-item label="部门">{{ basic.department || '—' }}</el-descriptions-item>
          <el-descriptions-item label="负责人">{{ basic.owner_name || '—' }}</el-descriptions-item>
          <el-descriptions-item label="招聘人数">{{ basic.headcount || '—' }}</el-descriptions-item>
          <el-descriptions-item label="职责条数">{{ structuredJd.responsibilities.length }}</el-descriptions-item>
          <el-descriptions-item label="要求条数">{{ structuredJd.requirements.length }}</el-descriptions-item>
          <el-descriptions-item label="评分数">{{ dimensions.length }}</el-descriptions-item>
          <el-descriptions-item label="权重合计">
            <span :class="weightTotal === 100 ? 'ok' : 'bad'">{{ weightTotal }}%</span>
          </el-descriptions-item>
        </el-descriptions>
        <p class="hint">发布前将强校验核心字段与权重 100%；AI 结果需先「应用到草稿」才会进入发布内容。</p>
        <div class="preview-actions">
          <el-button @click="step = 2">上一步</el-button>
          <el-button type="primary" :loading="saving" @click="onPublish">确认发布</el-button>
        </div>
      </section>
    </div>
  </AdminLayout>
</template>

<style scoped>
.wizard {
  max-width: 960px;
}
.sticky-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
  position: sticky;
  top: 0;
  background: var(--bg);
  padding: 8px 0;
  z-index: 2;
}
.left,
.right {
  display: flex;
  gap: 8px;
  align-items: center;
}
.steps {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}
.step {
  border: 1px solid var(--border);
  background: var(--surface);
  border-radius: 999px;
  padding: 8px 14px;
  cursor: pointer;
  color: var(--muted);
}
.step.active {
  border-color: var(--accent);
  color: var(--accent);
  background: color-mix(in oklab, var(--accent) 10%, white);
}
.panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 20px;
}
.grid-2,
.grid-3 {
  display: grid;
  gap: 12px;
}
.grid-2 {
  grid-template-columns: 1fr 1fr;
}
.grid-3 {
  grid-template-columns: 1fr 1fr 1fr;
}
.hint {
  color: var(--muted);
  font-size: 13px;
}
.jd-block {
  margin-bottom: 16px;
}
.jd-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}
.skills {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 8px;
}
.skill-add {
  display: flex;
  gap: 8px;
}
.dim-card {
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 14px;
  margin-bottom: 12px;
}
.dim-head,
.weight-row,
.anchor-row,
.preview-actions {
  display: flex;
  gap: 10px;
  align-items: center;
  margin: 8px 0;
}
.ok {
  color: var(--success);
}
.bad {
  color: var(--danger);
}
.ai-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  margin-bottom: 8px;
}
.ai-status {
  font-size: 13px;
  color: var(--muted);
}
.ai-status.fail {
  color: var(--danger);
}
.pending-box {
  border: 1px dashed var(--accent);
  border-radius: 10px;
  padding: 12px;
  margin-bottom: 16px;
  background: color-mix(in oklab, var(--accent) 6%, white);
}
.pending-pre {
  margin: 8px 0 0;
  max-height: 220px;
  overflow: auto;
  font-size: 12px;
  font-family: var(--font-mono);
  white-space: pre-wrap;
}
@media (max-width: 800px) {
  .grid-2,
  .grid-3 {
    grid-template-columns: 1fr;
  }
}
</style>
