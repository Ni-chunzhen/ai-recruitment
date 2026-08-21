<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, onMounted, reactive, ref } from 'vue'

import {
  getIntegrationsSummary,
  testIntegrationProvider,
  updateDifyIntegrations,
  updateMinioIntegrations,
  type ConnectivityTestResult,
  type FieldStatus,
  type IntegrationsSummary,
} from '../api/integrations'
import AdminLayout from '../layouts/AdminLayout.vue'

const RESTART_HINT =
  '请重启 API，并重启使用 Dify 的 AI worker / 使用 MinIO 的相关进程后生效；邮件队列名变更不在本页。'

const loading = ref(false)
const savingDify = ref(false)
const savingMinio = ref(false)
const testing = ref<'dify' | 'minio' | null>(null)
const summary = ref<IntegrationsSummary | null>(null)
const showRestartHint = ref(false)
const testResults = reactive<Record<'dify' | 'minio', ConnectivityTestResult | null>>({
  dify: null,
  minio: null,
})

const difyForm = reactive({
  api_base_url: '',
  jd_parse_workflow_id: '',
  score_dimension_workflow_id: '',
  resume_parse_workflow_id: '',
  resume_score_workflow_id: '',
  interview_question_generate_workflow_id: '',
  ai_provider: 'mock' as 'mock' | 'dify',
  api_key: '',
  jd_parse_api_key: '',
  score_dimension_api_key: '',
  resume_parse_api_key: '',
  resume_score_api_key: '',
  interview_question_generate_api_key: '',
})

const minioForm = reactive({
  endpoint: '',
  bucket: '',
  secure: 'false',
  presign_seconds: '600',
  access_key: '',
  secret_key: '',
})

const liveEnabledEnv = computed(() => Boolean(summary.value?.dify?.live_enabled_env))

function asField(block: Record<string, unknown> | undefined, key: string): FieldStatus | null {
  const raw = block?.[key]
  if (!raw || typeof raw !== 'object') return null
  return raw as FieldStatus
}

function configuredLabel(field: FieldStatus | null) {
  if (!field) return '未配置'
  return field.configured ? '已配置' : '未配置'
}

function applySummary(data: IntegrationsSummary) {
  summary.value = data
  const d = data.dify
  difyForm.api_base_url = asField(d, 'api_base_url')?.value ?? ''
  difyForm.jd_parse_workflow_id = asField(d, 'jd_parse_workflow_id')?.value ?? ''
  difyForm.score_dimension_workflow_id =
    asField(d, 'score_dimension_workflow_id')?.value ?? ''
  difyForm.resume_parse_workflow_id = asField(d, 'resume_parse_workflow_id')?.value ?? ''
  difyForm.resume_score_workflow_id = asField(d, 'resume_score_workflow_id')?.value ?? ''
  difyForm.interview_question_generate_workflow_id =
    asField(d, 'interview_question_generate_workflow_id')?.value ?? ''
  const provider = asField(d, 'ai_provider')?.value
  difyForm.ai_provider = provider === 'dify' ? 'dify' : 'mock'
  // secrets always stay empty — never echo
  difyForm.api_key = ''
  difyForm.jd_parse_api_key = ''
  difyForm.score_dimension_api_key = ''
  difyForm.resume_parse_api_key = ''
  difyForm.resume_score_api_key = ''
  difyForm.interview_question_generate_api_key = ''

  const m = data.minio
  minioForm.endpoint = asField(m, 'endpoint')?.value ?? ''
  minioForm.bucket = asField(m, 'bucket')?.value ?? ''
  minioForm.secure = asField(m, 'secure')?.value ?? 'false'
  minioForm.presign_seconds = asField(m, 'presign_seconds')?.value ?? '600'
  minioForm.access_key = ''
  minioForm.secret_key = ''
}

async function loadSummary() {
  loading.value = true
  try {
    applySummary(await getIntegrationsSummary())
  } catch {
    ElMessage.error('加载集成配置失败')
  } finally {
    loading.value = false
  }
}

function buildDifyPayload() {
  const payload: Record<string, string> = {
    api_base_url: difyForm.api_base_url,
    jd_parse_workflow_id: difyForm.jd_parse_workflow_id,
    score_dimension_workflow_id: difyForm.score_dimension_workflow_id,
    resume_parse_workflow_id: difyForm.resume_parse_workflow_id,
    resume_score_workflow_id: difyForm.resume_score_workflow_id,
    interview_question_generate_workflow_id:
      difyForm.interview_question_generate_workflow_id,
    ai_provider: difyForm.ai_provider,
  }
  const secrets: Array<keyof typeof difyForm> = [
    'api_key',
    'jd_parse_api_key',
    'score_dimension_api_key',
    'resume_parse_api_key',
    'resume_score_api_key',
    'interview_question_generate_api_key',
  ]
  for (const key of secrets) {
    const value = difyForm[key]
    if (typeof value === 'string' && value.trim() !== '') {
      payload[key] = value
    }
  }
  return payload
}

function buildMinioPayload() {
  const payload: Record<string, string> = {
    endpoint: minioForm.endpoint,
    bucket: minioForm.bucket,
    secure: minioForm.secure,
    presign_seconds: minioForm.presign_seconds,
  }
  if (minioForm.access_key.trim() !== '') payload.access_key = minioForm.access_key
  if (minioForm.secret_key.trim() !== '') payload.secret_key = minioForm.secret_key
  return payload
}

async function saveDify() {
  savingDify.value = true
  try {
    const data = await updateDifyIntegrations(buildDifyPayload())
    applySummary(data)
    showRestartHint.value = Boolean(data.restart_required)
    ElMessage.success('Dify 配置已保存')
  } catch {
    ElMessage.error('保存 Dify 配置失败')
  } finally {
    savingDify.value = false
  }
}

async function saveMinio() {
  savingMinio.value = true
  try {
    const data = await updateMinioIntegrations(buildMinioPayload())
    applySummary(data)
    showRestartHint.value = Boolean(data.restart_required)
    ElMessage.success('MinIO 配置已保存')
  } catch {
    ElMessage.error('保存 MinIO 配置失败')
  } finally {
    savingMinio.value = false
  }
}

async function runTest(provider: 'dify' | 'minio') {
  testing.value = provider
  try {
    const result = await testIntegrationProvider(provider)
    testResults[provider] = {
      ok: result.ok,
      error_code: result.error_code,
      latency_ms: result.latency_ms,
    }
  } catch {
    testResults[provider] = { ok: false, error_code: 'request_failed', latency_ms: 0 }
    ElMessage.error(`${provider} 连通性测试失败`)
  } finally {
    testing.value = null
  }
}

onMounted(() => {
  void loadSummary()
})
</script>

<template>
  <AdminLayout>
    <div class="page" data-test="integration-config-page">
      <header class="page-head">
        <h1>第三方集成配置</h1>
        <p class="sub">仅系统管理员可管理。密钥只写不回显；保存后需重启进程生效。</p>
      </header>

      <el-alert
        v-if="showRestartHint"
        data-test="restart-required-hint"
        type="warning"
        :closable="false"
        :title="RESTART_HINT"
        show-icon
      />

      <section v-loading="loading" class="block" data-test="dify-block">
        <div class="block-head">
          <h2>Dify</h2>
          <span class="badge">live 开关只读：{{ liveEnabledEnv ? '开启(env)' : '关闭(env)' }}</span>
        </div>
        <div class="grid">
          <label>
            <span>API Base URL</span>
            <el-input v-model="difyForm.api_base_url" />
          </label>
          <label>
            <span>AI Provider</span>
            <el-select v-model="difyForm.ai_provider" style="width: 100%">
              <el-option label="mock" value="mock" />
              <el-option label="dify" value="dify" />
            </el-select>
          </label>
          <label>
            <span>默认 API Key（{{ configuredLabel(asField(summary?.dify, 'api_key')) }}）</span>
            <el-input
              v-model="difyForm.api_key"
              type="password"
              show-password
              placeholder="已配置则留空表示不修改"
              autocomplete="new-password"
            />
          </label>
          <label>
            <span>JD Parse API Key（{{ configuredLabel(asField(summary?.dify, 'jd_parse_api_key')) }}）</span>
            <el-input
              v-model="difyForm.jd_parse_api_key"
              type="password"
              show-password
              placeholder="已配置则留空表示不修改"
              autocomplete="new-password"
            />
          </label>
          <label>
            <span>JD Parse Workflow ID</span>
            <el-input v-model="difyForm.jd_parse_workflow_id" />
          </label>
          <label>
            <span>Score Dimension API Key（{{ configuredLabel(asField(summary?.dify, 'score_dimension_api_key')) }}）</span>
            <el-input
              v-model="difyForm.score_dimension_api_key"
              type="password"
              show-password
              placeholder="已配置则留空表示不修改"
              autocomplete="new-password"
            />
          </label>
          <label>
            <span>Score Dimension Workflow ID</span>
            <el-input v-model="difyForm.score_dimension_workflow_id" />
          </label>
          <label>
            <span>Resume Parse API Key（{{ configuredLabel(asField(summary?.dify, 'resume_parse_api_key')) }}）</span>
            <el-input
              v-model="difyForm.resume_parse_api_key"
              type="password"
              show-password
              placeholder="已配置则留空表示不修改"
              autocomplete="new-password"
            />
          </label>
          <label>
            <span>Resume Parse Workflow ID</span>
            <el-input v-model="difyForm.resume_parse_workflow_id" />
          </label>
          <label>
            <span>Resume Score API Key（{{ configuredLabel(asField(summary?.dify, 'resume_score_api_key')) }}）</span>
            <el-input
              v-model="difyForm.resume_score_api_key"
              type="password"
              show-password
              placeholder="已配置则留空表示不修改"
              autocomplete="new-password"
            />
          </label>
          <label>
            <span>Resume Score Workflow ID</span>
            <el-input v-model="difyForm.resume_score_workflow_id" />
          </label>
          <label>
            <span>Interview Question API Key（{{ configuredLabel(asField(summary?.dify, 'interview_question_generate_api_key')) }}）</span>
            <el-input
              v-model="difyForm.interview_question_generate_api_key"
              type="password"
              show-password
              placeholder="已配置则留空表示不修改"
              autocomplete="new-password"
            />
          </label>
          <label>
            <span>Interview Question Workflow ID</span>
            <el-input v-model="difyForm.interview_question_generate_workflow_id" />
          </label>
        </div>
        <div class="actions">
          <el-button type="primary" :loading="savingDify" @click="saveDify">保存 Dify</el-button>
          <el-button :loading="testing === 'dify'" data-test="dify-test-btn" @click="runTest('dify')">
            测试连通性
          </el-button>
        </div>
        <p v-if="testResults.dify" class="test-result" data-test="dify-test-result">
          ok={{ testResults.dify.ok }} · error_code={{ testResults.dify.error_code ?? 'null' }} ·
          latency_ms={{ testResults.dify.latency_ms }}
        </p>
      </section>

      <section class="block" data-test="minio-block">
        <div class="block-head">
          <h2>MinIO</h2>
        </div>
        <div class="grid">
          <label>
            <span>Endpoint</span>
            <el-input v-model="minioForm.endpoint" />
          </label>
          <label>
            <span>Bucket</span>
            <el-input v-model="minioForm.bucket" />
          </label>
          <label>
            <span>Secure</span>
            <el-select v-model="minioForm.secure" style="width: 100%">
              <el-option label="false" value="false" />
              <el-option label="true" value="true" />
            </el-select>
          </label>
          <label>
            <span>Presign Seconds</span>
            <el-input v-model="minioForm.presign_seconds" />
          </label>
          <label>
            <span>Access Key（{{ configuredLabel(asField(summary?.minio, 'access_key')) }}）</span>
            <el-input
              v-model="minioForm.access_key"
              type="password"
              show-password
              placeholder="已配置则留空表示不修改"
              autocomplete="new-password"
            />
          </label>
          <label>
            <span>Secret Key（{{ configuredLabel(asField(summary?.minio, 'secret_key')) }}）</span>
            <el-input
              v-model="minioForm.secret_key"
              type="password"
              show-password
              placeholder="已配置则留空表示不修改"
              autocomplete="new-password"
            />
          </label>
        </div>
        <div class="actions">
          <el-button type="primary" :loading="savingMinio" @click="saveMinio">保存 MinIO</el-button>
          <el-button
            :loading="testing === 'minio'"
            data-test="minio-test-btn"
            @click="runTest('minio')"
          >
            测试连通性
          </el-button>
        </div>
        <p v-if="testResults.minio" class="test-result" data-test="minio-test-result">
          ok={{ testResults.minio.ok }} · error_code={{ testResults.minio.error_code ?? 'null' }} ·
          latency_ms={{ testResults.minio.latency_ms }}
        </p>
      </section>

      <section class="block readonly" data-test="mail-block">
        <div class="block-head">
          <h2>邮件（只读）</h2>
        </div>
        <dl class="mail-readonly">
          <div>
            <dt>投递方式</dt>
            <dd>{{ summary?.mail.delivery_provider || 'console' }}</dd>
          </div>
          <div>
            <dt>队列名</dt>
            <dd>{{ summary?.mail.queue_name || 'mail_outbound' }}</dd>
          </div>
          <div>
            <dt>外部发信协议</dt>
            <dd>{{ summary?.mail.smtp_enabled ? '开启' : '关闭' }}</dd>
          </div>
          <div>
            <dt>说明</dt>
            <dd>{{ summary?.mail.note || '一期仅 Console' }}</dd>
          </div>
        </dl>
      </section>
    </div>
  </AdminLayout>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 20px;
  max-width: 960px;
}

.page-head h1 {
  margin: 0 0 6px;
  font-size: 22px;
}

.sub {
  margin: 0;
  color: var(--muted);
  font-size: 13px;
}

.block {
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 16px 18px 18px;
  background: var(--surface);
}

.block.readonly {
  opacity: 0.95;
}

.block-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
}

.block-head h2 {
  margin: 0;
  font-size: 16px;
}

.badge {
  font-size: 12px;
  color: var(--muted);
}

.grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px 16px;
}

label {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 13px;
  color: var(--fg);
}

.actions {
  margin-top: 16px;
  display: flex;
  gap: 10px;
}

.test-result {
  margin: 10px 0 0;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  color: var(--muted);
}

.mail-readonly {
  margin: 0;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px 20px;
}

.mail-readonly dt {
  font-size: 12px;
  color: var(--muted);
}

.mail-readonly dd {
  margin: 2px 0 0;
}

@media (max-width: 720px) {
  .grid,
  .mail-readonly {
    grid-template-columns: 1fr;
  }
}
</style>
