<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import {
  confirmResumeVersion,
  getResumeVersion,
  reparseResumeVersion,
  resumeStatusLabel,
  saveResumeDraft,
  type ResumeStructuredContent,
  type ResumeVersion,
} from '../api/resumes'
import AdminLayout from '../layouts/AdminLayout.vue'

const route = useRoute()
const router = useRouter()
const loading = ref(false)
const saving = ref(false)
const version = ref<ResumeVersion | null>(null)
const form = reactive<ResumeStructuredContent>({
  name: '',
  name_pending: false,
  phone: '',
  email: '',
  years_of_experience: null,
  education: [],
  work_experience: [],
  projects: [],
  skills: [],
  standardized_text: '',
  field_sources: {},
})
const skillsText = ref('')
const manualText = ref('')
const linkCandidateId = ref('')

const isConfirmed = computed(() => version.value?.status === 'confirmed')
const leftText = computed(
  () => version.value?.extracted_text || '暂无提取文本，可在下方粘贴后重新解析',
)
const aiJson = computed(() =>
  version.value?.ai_structured
    ? JSON.stringify(version.value.ai_structured, null, 2)
    : '尚无 AI 结构化结果',
)

function applyContent(
  content: Record<string, unknown> | ResumeStructuredContent | null | undefined,
) {
  const c = (content || {}) as Partial<ResumeStructuredContent>
  form.name = String(c.name || '')
  form.name_pending = Boolean(c.name_pending)
  form.phone = String(c.phone || '')
  form.email = String(c.email || '')
  form.years_of_experience =
    c.years_of_experience == null ? null : Number(c.years_of_experience)
  form.education = Array.isArray(c.education) ? [...c.education] : []
  form.work_experience = Array.isArray(c.work_experience) ? [...c.work_experience] : []
  form.projects = Array.isArray(c.projects) ? [...c.projects] : []
  form.skills = Array.isArray(c.skills) ? c.skills.map(String) : []
  form.standardized_text = String(c.standardized_text || '')
  form.field_sources = { ...(c.field_sources || {}) }
  skillsText.value = form.skills.join('、')
}

async function load() {
  loading.value = true
  try {
    version.value = await getResumeVersion(route.params.versionId as string)
    const preferred =
      (version.value.draft_content as ResumeStructuredContent | null) ||
      (version.value.confirmed_content as ResumeStructuredContent | null) ||
      (version.value.ai_structured as ResumeStructuredContent | null)
    applyContent(preferred || { standardized_text: version.value.extracted_text || '' })
    manualText.value = version.value.extracted_text || form.standardized_text || ''
  } catch {
    ElMessage.error('加载简历版本失败')
    await router.push({ name: 'resumes' })
  } finally {
    loading.value = false
  }
}

function syncSkills() {
  form.skills = skillsText.value
    .split(/[,，、\s]+/)
    .map((s) => s.trim())
    .filter(Boolean)
}

async function onSaveDraft() {
  if (isConfirmed.value) {
    ElMessage.warning('已确认版本只读，修改请生成新确认版本')
    return
  }
  syncSkills()
  saving.value = true
  try {
    version.value = await saveResumeDraft(version.value!.id, { ...form })
    ElMessage.success('草稿已保存（未触发评分）')
  } catch (err: unknown) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败')
  } finally {
    saving.value = false
  }
}

async function onConfirm() {
  syncSkills()
  if (!form.standardized_text.trim()) {
    ElMessage.warning('标准化文本不能为空')
    return
  }
  if (!form.name.trim()) {
    form.name = '待补充'
    form.name_pending = true
  }
  try {
    await ElMessageBox.confirm(
      '确认后将生成不可变确认版本，才能用于岗位匹配与评分。是否继续？',
      '确认简历版本',
      { type: 'warning' },
    )
  } catch {
    return
  }
  saving.value = true
  try {
    version.value = await confirmResumeVersion(version.value!.id, {
      content: { ...form },
      link_candidate_id: linkCandidateId.value || undefined,
    })
    ElMessage.success(`已确认 ${version.value.version_label}`)
    applyContent(version.value.confirmed_content as ResumeStructuredContent)
  } catch (err: unknown) {
    const detail =
      (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
      '确认失败'
    ElMessage.error(String(detail))
  } finally {
    saving.value = false
  }
}

async function onReparse() {
  if (!manualText.value.trim()) {
    ElMessage.warning('请先粘贴简历文本')
    return
  }
  saving.value = true
  try {
    version.value = await reparseResumeVersion(version.value!.id, manualText.value)
    ElMessage.success('已重新发起解析')
    await load()
  } catch (err: unknown) {
    ElMessage.error(err instanceof Error ? err.message : '重新解析失败')
  } finally {
    saving.value = false
  }
}

onMounted(() => {
  void load()
})
</script>

<template>
  <AdminLayout>
    <div v-loading="loading" class="page">
      <div class="panel-head">
        <div>
          <el-button link type="primary" @click="router.push({ name: 'resumes' })">
            ← 返回简历库
          </el-button>
          <h1>简历校对</h1>
          <p class="sub" v-if="version">
            {{ version.original_filename || version.version_label }} ·
            <el-tag size="small">{{ resumeStatusLabel[version.status] }}</el-tag>
            · {{ version.version_label }}
          </p>
        </div>
        <div class="actions">
          <el-button :disabled="isConfirmed" :loading="saving" @click="onSaveDraft">
            保存草稿
          </el-button>
          <el-button type="primary" :disabled="isConfirmed" :loading="saving" @click="onConfirm">
            确认版本
          </el-button>
        </div>
      </div>

      <el-alert
        v-if="version?.duplicate_hints?.length"
        type="warning"
        show-icon
        :closable="false"
        style="margin-bottom: 12px"
        title="检测到可能重复的候选人（手机号/邮箱），请人工判断是否关联"
      />
      <div v-if="version?.duplicate_hints?.length" class="dup-list">
        <el-radio-group v-model="linkCandidateId">
          <el-radio value="">创建新候选人</el-radio>
          <el-radio v-for="h in version.duplicate_hints" :key="h.id" :value="h.id">
            关联 {{ h.name }}（{{ h.match_on.join('/') }}）
          </el-radio>
        </el-radio-group>
      </div>

      <div class="review-grid">
        <section class="panel">
          <h3>原文 / 提取文本</h3>
          <a v-if="version?.preview_url" :href="version.preview_url" target="_blank" rel="noreferrer">
            预览原文件
          </a>
          <pre class="mono">{{ leftText }}</pre>
          <el-divider />
          <p class="hint">解析失败时可手工粘贴文本后重新解析，或直接在右侧确认</p>
          <el-input v-model="manualText" type="textarea" :rows="6" />
          <el-button style="margin-top: 8px" :loading="saving" @click="onReparse">
            用文本重新解析
          </el-button>
        </section>

        <section class="panel">
          <h3>人工确认内容</h3>
          <el-alert
            title="AI 结果仅供参考；确认内容将作为正式评分输入。人工补充请如实填写。"
            type="info"
            show-icon
            :closable="false"
            style="margin-bottom: 12px"
          />
          <el-form label-position="top" :disabled="isConfirmed">
            <el-row :gutter="12">
              <el-col :span="12">
                <el-form-item label="姓名">
                  <el-input v-model="form.name" placeholder="缺失时填「待补充」" />
                </el-form-item>
              </el-col>
              <el-col :span="12">
                <el-form-item label="工作年限">
                  <el-input-number
                    v-model="form.years_of_experience"
                    :min="0"
                    :max="50"
                    style="width: 100%"
                  />
                </el-form-item>
              </el-col>
              <el-col :span="12">
                <el-form-item label="手机号">
                  <el-input v-model="form.phone" />
                </el-form-item>
              </el-col>
              <el-col :span="12">
                <el-form-item label="邮箱">
                  <el-input v-model="form.email" />
                </el-form-item>
              </el-col>
              <el-col :span="24">
                <el-form-item label="技能（顿号/逗号分隔）">
                  <el-input v-model="skillsText" />
                </el-form-item>
              </el-col>
              <el-col :span="24">
                <el-form-item label="标准化文本" required>
                  <el-input v-model="form.standardized_text" type="textarea" :rows="10" />
                </el-form-item>
              </el-col>
            </el-row>
          </el-form>

          <el-divider />
          <h4>AI 原始结构化结果</h4>
          <pre class="mono ai">{{ aiJson }}</pre>
        </section>
      </div>
    </div>
  </AdminLayout>
</template>

<style scoped>
.page {
  max-width: 1200px;
}
.panel-head {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
  align-items: flex-start;
}
h1 {
  margin: 4px 0 0;
  font-size: 24px;
}
.sub {
  margin: 6px 0 0;
  color: var(--muted);
  font-size: 13px;
}
.actions {
  display: flex;
  gap: 8px;
}
.review-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}
@media (max-width: 960px) {
  .review-grid {
    grid-template-columns: 1fr;
  }
}
.panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 16px;
}
.panel h3 {
  margin: 0 0 12px;
  font-size: 16px;
}
.mono {
  white-space: pre-wrap;
  word-break: break-word;
  font-family: var(--font-mono);
  font-size: 12px;
  background: color-mix(in oklab, var(--fg), transparent 96%);
  padding: 12px;
  border-radius: 8px;
  max-height: 360px;
  overflow: auto;
}
.mono.ai {
  max-height: 220px;
}
.hint {
  color: var(--muted);
  font-size: 12px;
  margin: 0 0 8px;
}
.dup-list {
  margin-bottom: 12px;
}
</style>
