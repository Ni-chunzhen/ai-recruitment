<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, reactive, ref, watch } from 'vue'

import {
  auditInvitationCopy,
  generateInvitations,
  getInvitationDetail,
  listInvitations,
  recordInvitationSent,
  updateInvitation,
  type InvitationChannelType,
  type InvitationMessageDetail,
  type InvitationMessageSummary,
  type InvitationStatusCounts,
} from '../api/invitations'
import type { InterviewRound } from '../api/interviews'

const props = defineProps<{
  open: boolean
  round: InterviewRound | null
  canManage: boolean
}>()

const emit = defineEmits<{
  'update:open': [value: boolean]
  refreshed: []
}>()

const statusLabels: Record<string, string> = {
  DRAFT: '草稿',
  READY: '可复制',
  RECORDED_SENT: '人工登记已发送',
  VOIDED: '已失效',
}

const eventLabels: Record<string, string> = {
  INITIAL: '首次邀约',
  RESCHEDULE: '改期通知',
  CANCELLATION: '取消通知',
}

const loading = ref(false)
const saving = ref(false)
const items = ref<InvitationMessageSummary[]>([])
const counts = ref<InvitationStatusCounts>({
  generated: 0,
  ready: 0,
  recorded_sent: 0,
  voided: 0,
})
const selectedId = ref<string | null>(null)
const detail = ref<InvitationMessageDetail | null>(null)
const editor = reactive({
  subject: '',
  body_html: '',
  body_text: '',
})
const recordOpen = ref(false)
const recordForm = reactive({
  sent_at: '',
  channel_type: 'CORPORATE_EMAIL' as InvitationChannelType,
  channel_note: '',
})

const selected = computed(
  () => items.value.find((item) => item.id === selectedId.value) || null,
)

const scheduleVersion = computed(
  () => props.round?.current_schedule?.schedule_version ?? null,
)

function newIdempotencyKey(prefix: string) {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}

function isConflictError(error: unknown) {
  return (
    typeof error === 'object' &&
    error !== null &&
    'response' in error &&
    (error as { response?: { status?: number } }).response?.status === 409
  )
}

async function loadList() {
  if (!props.round) return
  loading.value = true
  try {
    const data = await listInvitations(props.round.id)
    items.value = data.items
    counts.value = data.counts
    if (!selectedId.value && data.items.length) {
      selectedId.value = data.items[0].id
    }
    if (
      selectedId.value &&
      !data.items.some((item) => item.id === selectedId.value)
    ) {
      selectedId.value = data.items[0]?.id ?? null
    }
    if (selectedId.value) {
      await loadDetail(selectedId.value)
    } else {
      detail.value = null
    }
  } catch (error) {
    ElMessage.error(isConflictError(error) ? '请刷新后重试' : '加载邀约失败')
  } finally {
    loading.value = false
  }
}

async function loadDetail(messageId: string) {
  try {
    const data = await getInvitationDetail(messageId)
    detail.value = data
    editor.subject = data.subject
    editor.body_html = data.body_html
    editor.body_text = data.body_text || ''
  } catch {
    ElMessage.error('加载邮件正文失败')
  }
}

async function onGenerate() {
  if (!props.round || !props.canManage) return
  saving.value = true
  try {
    const eventType =
      props.round.status === 'CANCELLED'
        ? 'CANCELLATION'
        : (props.round.current_schedule?.schedule_version || 1) > 1
          ? 'RESCHEDULE'
          : 'INITIAL'
    await generateInvitations(props.round.id, {
      event_type: eventType,
      idempotency_key: newIdempotencyKey('invite-gen'),
    })
    ElMessage.success('已生成邀约邮件')
    await loadList()
    emit('refreshed')
  } catch (error) {
    ElMessage.error(isConflictError(error) ? '请刷新后重试' : '生成失败')
  } finally {
    saving.value = false
  }
}

async function onSave() {
  if (!detail.value || !props.canManage) return
  saving.value = true
  try {
    const updated = await updateInvitation(detail.value.id, {
      version: detail.value.version,
      subject: editor.subject,
      body_html: editor.body_html,
      body_text: editor.body_text,
    })
    detail.value = updated
    editor.subject = updated.subject
    editor.body_html = updated.body_html
    editor.body_text = updated.body_text || ''
    ElMessage.success('已保存新版本')
    await loadList()
  } catch (error) {
    ElMessage.error(
      isConflictError(error)
        ? '邮件已被其他人员更新，请刷新后重试'
        : '保存失败',
    )
  } finally {
    saving.value = false
  }
}

async function copyText(value: string, copyType: 'SUBJECT' | 'HTML_BODY' | 'FULL_TEXT') {
  if (!detail.value || !props.canManage) return
  try {
    await navigator.clipboard.writeText(value)
    await auditInvitationCopy(detail.value.id, { copy_type: copyType })
    ElMessage.success('已复制到剪贴板（未表示已发送）')
  } catch {
    ElMessage.error('复制失败')
  }
}

function openRecord() {
  if (!detail.value) return
  recordForm.sent_at = new Date().toISOString().slice(0, 19)
  recordForm.channel_type = 'CORPORATE_EMAIL'
  recordForm.channel_note = ''
  recordOpen.value = true
}

async function submitRecord() {
  if (!detail.value || !detail.value.current_version_id) return
  saving.value = true
  try {
    await recordInvitationSent(detail.value.id, {
      sent_at: new Date(recordForm.sent_at).toISOString(),
      message_version_id: detail.value.current_version_id,
      channel_type: recordForm.channel_type,
      channel_note: recordForm.channel_note || undefined,
      idempotency_key: newIdempotencyKey('invite-sent'),
    })
    ElMessage.success('已登记人工发送')
    recordOpen.value = false
    await loadList()
  } catch (error) {
    ElMessage.error(isConflictError(error) ? '请刷新后重试' : '登记失败')
  } finally {
    saving.value = false
  }
}

watch(
  () => [props.open, props.round?.id] as const,
  async ([open]) => {
    if (open && props.round) {
      selectedId.value = null
      detail.value = null
      await loadList()
    }
  },
  { immediate: true },
)

watch(selectedId, async (id) => {
  if (id) await loadDetail(id)
})
</script>

<template>
  <el-drawer
    :model-value="open"
    size="720px"
    title="邀约邮件"
    data-test="invitation-drawer"
    @update:model-value="emit('update:open', $event)"
  >
    <div v-loading="loading" class="invite-drawer">
      <div class="invite-summary">
        <div>
          事件：{{ eventLabels[selected?.event_type || ''] || selected?.event_type || '—' }}
        </div>
        <div>安排版本：{{ scheduleVersion ?? '—' }}</div>
        <div>当前安排已生成：{{ counts.generated + counts.ready + counts.recorded_sent }}</div>
        <div>可复制：{{ counts.ready }}</div>
        <div>已登记：{{ counts.recorded_sent }}</div>
        <div data-test="voided-count">已失效历史：{{ counts.voided }}</div>
        <el-button
          v-if="canManage"
          type="primary"
          size="small"
          data-test="generate-invitations"
          :loading="saving"
          @click="onGenerate"
        >
          {{ round?.status === 'CANCELLED' ? '生成取消邮件' : '生成邀约邮件' }}
        </el-button>
      </div>

      <div class="invite-body">
        <div class="recipient-list" data-test="recipient-list">
          <button
            v-for="item in items"
            :key="item.id"
            type="button"
            class="recipient-item"
            :class="{ active: item.id === selectedId }"
            :data-test="`recipient-${item.audience_type.toLowerCase()}`"
            @click="selectedId = item.id"
          >
            <div class="recipient-name">{{ item.recipient_name }}</div>
            <div class="recipient-meta">
              {{ item.audience_type === 'CANDIDATE' ? '候选人' : '面试官' }}
              · {{ item.recipient_email_masked || '邮箱缺失' }}
            </div>
            <el-tag
              size="small"
              data-test="invitation-status-tag"
              :type="item.status === 'VOIDED' ? 'info' : undefined"
            >
              {{ statusLabels[item.status] || item.status }}
            </el-tag>
            <div
              v-if="item.status === 'VOIDED'"
              class="voided-hint"
              data-test="voided-hint"
            >
              旧安排已失效
            </div>
          </button>
          <el-empty v-if="!items.length" description="尚未生成邮件" />
        </div>

        <div v-if="detail" class="editor-pane" data-test="invitation-editor">
          <div class="form-item">
            <label>收件人</label>
            <div>{{ detail.recipient_name }} · {{ detail.recipient_email_masked || '—' }}</div>
          </div>
          <div class="form-item">
            <label>邮件主题</label>
            <el-input
              v-model="editor.subject"
              data-test="invitation-subject"
              :disabled="!canManage || detail.status === 'VOIDED'"
            />
          </div>
          <div class="form-item">
            <label>富文本正文</label>
            <el-input
              v-model="editor.body_html"
              type="textarea"
              :rows="10"
              data-test="invitation-body-html"
              :disabled="!canManage || detail.status === 'VOIDED'"
            />
          </div>
          <div class="form-item">
            <label>纯文本预览</label>
            <pre class="text-preview" data-test="invitation-body-text">{{ editor.body_text }}</pre>
          </div>
          <div class="meta-row">
            模板 {{ detail.template_code }} / {{ detail.template_version }}
            · 内容版本 v{{ detail.current_version_no }}
          </div>
          <div v-if="canManage && detail.status !== 'VOIDED'" class="editor-actions">
            <el-button type="primary" data-test="save-invitation" :loading="saving" @click="onSave">
              保存新版本
            </el-button>
            <el-button data-test="copy-subject" @click="copyText(editor.subject, 'SUBJECT')">
              复制主题
            </el-button>
            <el-button data-test="copy-html" @click="copyText(editor.body_html, 'HTML_BODY')">
              复制富文本正文
            </el-button>
            <el-button data-test="copy-text" @click="copyText(editor.body_text, 'FULL_TEXT')">
              复制完整纯文本
            </el-button>
            <el-button
              v-if="detail.status === 'READY' || detail.status === 'RECORDED_SENT'"
              type="primary"
              plain
              data-test="record-sent"
              @click="openRecord"
            >
              登记已人工发送
            </el-button>
          </div>
        </div>
      </div>
    </div>

    <el-dialog
      v-model="recordOpen"
      title="登记已人工发送"
      width="480px"
      data-test="record-sent-dialog"
    >
      <p class="disclaimer" data-test="record-disclaimer">
        此记录由用户人工登记，系统无法验证邮件是否送达。
      </p>
      <div class="form-item">
        <label>实际发送时间</label>
        <el-input v-model="recordForm.sent_at" data-test="record-sent-at" />
      </div>
      <div class="form-item">
        <label>使用的内容版本</label>
        <div data-test="record-version">v{{ detail?.current_version_no }}</div>
      </div>
      <div class="form-item">
        <label>外部发送渠道</label>
        <el-select v-model="recordForm.channel_type" data-test="record-channel">
          <el-option label="企业邮箱" value="CORPORATE_EMAIL" />
          <el-option label="工作邮箱" value="WORK_EMAIL" />
          <el-option label="其他" value="OTHER" />
        </el-select>
      </div>
      <div class="form-item">
        <label>备注</label>
        <el-input v-model="recordForm.channel_note" type="textarea" data-test="record-note" />
      </div>
      <template #footer>
        <el-button @click="recordOpen = false">取消</el-button>
        <el-button type="primary" data-test="submit-record-sent" :loading="saving" @click="submitRecord">
          确认登记
        </el-button>
      </template>
    </el-dialog>
  </el-drawer>
</template>

<style scoped>
.invite-drawer {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-height: 100%;
}
.invite-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  padding: 10px 12px;
  background: #f5f7fa;
  border: 1px solid #e5e7eb;
  font-size: 13px;
  color: #4b5563;
}
.invite-body {
  display: grid;
  grid-template-columns: 220px 1fr;
  gap: 12px;
  min-height: 480px;
}
.recipient-list {
  border: 1px solid #e5e7eb;
  background: #fff;
  overflow: auto;
}
.recipient-item {
  display: block;
  width: 100%;
  text-align: left;
  border: 0;
  border-bottom: 1px solid #f0f2f5;
  background: #fff;
  padding: 10px 12px;
  cursor: pointer;
}
.recipient-item.active {
  background: #ecf5ff;
}
.recipient-name {
  font-weight: 600;
  color: #1f2937;
}
.recipient-meta {
  font-size: 12px;
  color: #6b7280;
  margin: 4px 0 6px;
}
.voided-hint {
  margin-top: 4px;
  font-size: 12px;
  color: #b45309;
}
.editor-pane {
  border: 1px solid #e5e7eb;
  background: #fff;
  padding: 12px;
}
.form-item {
  margin-bottom: 12px;
}
.form-item label {
  display: block;
  margin-bottom: 4px;
  color: #6b7280;
  font-size: 12px;
}
.text-preview {
  margin: 0;
  padding: 10px;
  background: #f9fafb;
  border: 1px solid #e5e7eb;
  white-space: pre-wrap;
  font-size: 12px;
  color: #374151;
  min-height: 80px;
}
.meta-row {
  font-size: 12px;
  color: #6b7280;
  margin-bottom: 10px;
}
.editor-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.disclaimer {
  color: #b45309;
  background: #fffbeb;
  border: 1px solid #fde68a;
  padding: 8px 10px;
  font-size: 13px;
}
</style>
