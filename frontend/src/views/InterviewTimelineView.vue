<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import {
  cancelInterviewRound,
  checkInterviewConflicts,
  completeInterviewRound,
  createInterviewRound,
  endInterviewAbnormally,
  finishInterviewRound,
  getInterviewTimeline,
  listInterviewReasonCodes,
  listInterviewStaff,
  rescheduleInterviewRound,
  scheduleInterviewRound,
  startInterviewRound,
  updateInterviewRound,
  type InterviewFormat,
  type InterviewReasonCode,
  type InterviewRound,
  type InterviewStaffItem,
  type InterviewTimeline,
} from '../api/interviews'
import AdminLayout from '../layouts/AdminLayout.vue'
import { useAuthStore } from '../stores/auth'

const CONFLICT_HINT = '面试信息已被其他人员更新，请刷新后重试'
const pipelineLabels: Record<string, string> = {
  interviewing: '面试中',
  pending_hr_screen: '待HR筛选',
  pending_parse: '待解析',
  rejected: '已淘汰',
  talent_pool: '人才库',
}
const statusLabels: Record<string, string> = {
  DRAFT: '草稿',
  SCHEDULED: '已安排',
  CONFIRMED: '已确认',
  IN_PROGRESS: '进行中',
  PENDING_TRANSCRIPT: '待转写',
  COMPLETED: '已完成',
  CANCELLED: '已取消',
  ENDED_ABNORMALLY: '异常结束',
}

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const applicationId = computed(() => route.params.applicationId as string)
const canManage = computed(() => authStore.hasPermission('recruitment.manage'))

const loading = ref(false)
const saving = ref(false)
const timeline = ref<InterviewTimeline | null>(null)
const reasonCodes = ref<InterviewReasonCode[]>([])
const reasonCodesError = ref(false)
const staff = ref<InterviewStaffItem[]>([])
const conflictMessage = ref('')
const drawerOpen = ref(false)
const drawerMode = ref<'create' | 'edit' | 'schedule'>('create')
const rescheduleOpen = ref(false)
const cancelOpen = ref(false)
const abnormalOpen = ref(false)
const activeRound = ref<InterviewRound | null>(null)

const form = reactive({
  name: '',
  sequence_no: undefined as number | undefined,
  format: 'ONLINE' as InterviewFormat,
  owner_id: '',
  interviewer_ids: [] as string[],
  primary_id: '',
  timezone: 'Asia/Shanghai',
  start_at: '',
  end_at: '',
  meeting_url: '',
  meeting_no: '',
  meeting_password: '',
  location: '',
  contact_name: '',
  contact_phone: '',
  override_reason: '',
})

const rescheduleForm = reactive({
  timezone: 'Asia/Shanghai',
  start_at: '',
  end_at: '',
  format: 'ONLINE' as InterviewFormat,
  meeting_url: '',
  meeting_no: '',
  location: '',
  contact_name: '',
  contact_phone: '',
  reschedule_reason: '',
  meeting_password: '',
  clear_meeting_password: false,
  override_reason: '',
})

const cancelForm = reactive({
  reason_code: '',
  description: '',
})

const abnormalForm = reactive({
  reason_code: '',
  description: '',
})

const sortedRounds = computed(() =>
  [...(timeline.value?.rounds || [])].sort((a, b) => a.sequence_no - b.sequence_no),
)

const cancelCodes = computed(() =>
  reasonCodes.value.filter((item) => item.category === 'cancel'),
)
const abnormalCodes = computed(() =>
  reasonCodes.value.filter((item) => item.category === 'abnormal'),
)

function newIdempotencyKey(prefix: string) {
  return `${prefix}-${applicationId.value}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

function isConflictError(err: unknown) {
  return (err as { response?: { status?: number } })?.response?.status === 409
}

function errorDetail(err: unknown, fallback: string) {
  return (
    (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
    (err instanceof Error ? err.message : fallback)
  )
}

function hasAction(round: InterviewRound, action: string) {
  return (round.allowed_actions as string[]).includes(action)
}

function toIso(value: string) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return new Date(`${value}+08:00`).toISOString()
  }
  return date.toISOString()
}

function formatLocal(iso: string | null | undefined, timezone: string) {
  if (!iso) return '—'
  try {
    return new Intl.DateTimeFormat('zh-CN', {
      timeZone: timezone || 'Asia/Shanghai',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    }).format(new Date(iso))
  } catch {
    return iso
  }
}

function interviewerLabel(round: InterviewRound) {
  return round.interviewers
    .map((item) => `${item.display_name || item.interviewer_id}${item.is_primary ? '（主）' : ''}`)
    .join('、')
}

function resetForm() {
  form.name = ''
  form.sequence_no = (timeline.value?.total_round_count || 0) + 1
  form.format = 'ONLINE'
  form.owner_id = staff.value[0]?.id || authStore.user?.id || ''
  form.interviewer_ids = form.owner_id ? [form.owner_id] : []
  form.primary_id = form.owner_id
  form.timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Shanghai'
  form.start_at = ''
  form.end_at = ''
  form.meeting_url = ''
  form.meeting_no = ''
  form.meeting_password = ''
  form.location = ''
  form.contact_name = ''
  form.contact_phone = ''
  form.override_reason = ''
}

async function load() {
  loading.value = true
  conflictMessage.value = ''
  reasonCodesError.value = false
  try {
    const [timelineData] = await Promise.all([
      getInterviewTimeline(applicationId.value),
      listInterviewReasonCodes()
        .then((data) => {
          reasonCodes.value = data.items
        })
        .catch(() => {
          reasonCodes.value = []
          reasonCodesError.value = true
        }),
      canManage.value
        ? listInterviewStaff()
            .then((data) => {
              staff.value = data.items
            })
            .catch(() => {
              staff.value = []
            })
        : Promise.resolve(),
    ])
    timeline.value = timelineData
  } catch (err: unknown) {
    timeline.value = null
    ElMessage.error(String(errorDetail(err, '加载面试时间轴失败')))
  } finally {
    loading.value = false
  }
}

function openCreate() {
  drawerMode.value = 'create'
  activeRound.value = null
  resetForm()
  drawerOpen.value = true
}

function openEdit(round: InterviewRound) {
  drawerMode.value = 'edit'
  activeRound.value = round
  form.name = round.name
  form.sequence_no = round.sequence_no
  form.format = (round.format as InterviewFormat) || 'ONLINE'
  form.owner_id = round.owner_id
  form.interviewer_ids = round.interviewers.map((item) => item.interviewer_id)
  form.primary_id =
    round.interviewers.find((item) => item.is_primary)?.interviewer_id || form.interviewer_ids[0] || ''
  drawerOpen.value = true
}

function openSchedule(round: InterviewRound) {
  drawerMode.value = 'schedule'
  activeRound.value = round
  resetForm()
  form.name = round.name
  form.format = (round.format as InterviewFormat) || 'ONLINE'
  drawerOpen.value = true
}

function openReschedule(round: InterviewRound) {
  activeRound.value = round
  const current = round.current_schedule
  rescheduleForm.timezone = current?.timezone || 'Asia/Shanghai'
  rescheduleForm.format = (current?.format as InterviewFormat) || (round.format as InterviewFormat)
  rescheduleForm.meeting_url = current?.meeting_url || ''
  rescheduleForm.meeting_no = current?.meeting_no || ''
  rescheduleForm.location = current?.location || ''
  rescheduleForm.contact_name = current?.contact_name || ''
  rescheduleForm.contact_phone = ''
  rescheduleForm.start_at = ''
  rescheduleForm.end_at = ''
  rescheduleForm.reschedule_reason = ''
  rescheduleForm.meeting_password = ''
  rescheduleForm.clear_meeting_password = false
  rescheduleForm.override_reason = ''
  rescheduleOpen.value = true
}

function openCancel(round: InterviewRound) {
  activeRound.value = round
  cancelForm.reason_code = ''
  cancelForm.description = ''
  cancelOpen.value = true
}

function openAbnormal(round: InterviewRound) {
  activeRound.value = round
  abnormalForm.reason_code = ''
  abnormalForm.description = ''
  abnormalOpen.value = true
}

function assignments() {
  const ids = form.interviewer_ids.length ? form.interviewer_ids : form.owner_id ? [form.owner_id] : []
  const primary = form.primary_id || ids[0]
  return ids.map((id) => ({ interviewer_id: id, is_primary: id === primary }))
}

async function ensureConflicts(payload: {
  interviewer_ids: string[]
  start_at_utc: string
  end_at_utc: string
  timezone: string
  exclude_round_id?: string
  override_reason?: string
}) {
  const overrideReason = (payload.override_reason || '').trim()
  const result = await checkInterviewConflicts({
    application_id: applicationId.value,
    interviewer_ids: payload.interviewer_ids,
    start_at_utc: payload.start_at_utc,
    end_at_utc: payload.end_at_utc,
    timezone: payload.timezone,
    exclude_round_id: payload.exclude_round_id,
    override_interviewer_conflict: Boolean(overrideReason),
    override_reason: overrideReason || undefined,
  })
  if (result.has_candidate_conflict) {
    throw new Error('候选人时间冲突，无法保存')
  }
  if (result.has_interviewer_conflict && !overrideReason) {
    throw new Error('面试官时间冲突，覆盖必须填写原因')
  }
}

async function saveDraft() {
  await saveRound(false)
}

async function saveSchedule() {
  await saveRound(true)
}

function validateTimes(startAt: string, endAt: string) {
  if (!startAt || !endAt) {
    throw new Error('请填写开始和结束时间')
  }
  if (new Date(toIso(endAt)).getTime() <= new Date(toIso(startAt)).getTime()) {
    throw new Error('结束时间必须晚于开始时间')
  }
}

async function saveRound(withSchedule: boolean) {
  if (!form.name.trim() || !form.owner_id) {
    ElMessage.error('请填写轮次名称和负责人')
    return
  }
  saving.value = true
  try {
    const interviewers = assignments()
    if (!interviewers.length) {
      throw new Error('至少一名面试官')
    }
    const schedulePayload = withSchedule
      ? {
          start_at_utc: toIso(form.start_at),
          end_at_utc: toIso(form.end_at),
          timezone: form.timezone,
          format: form.format,
          meeting_mode: form.format === 'ONLINE' ? 'MANUAL' : null,
          meeting_url: form.format === 'ONLINE' ? form.meeting_url : null,
          meeting_no: form.meeting_no || null,
          meeting_password: form.meeting_password || null,
          location: form.format === 'OFFLINE' ? form.location : null,
          contact_name: form.contact_name || null,
          contact_phone: form.contact_phone || null,
        }
      : undefined
    if (withSchedule && schedulePayload) {
      validateTimes(form.start_at, form.end_at)
      await ensureConflicts({
        interviewer_ids: interviewers.map((item) => item.interviewer_id),
        start_at_utc: schedulePayload.start_at_utc,
        end_at_utc: schedulePayload.end_at_utc,
        timezone: form.timezone,
        exclude_round_id: activeRound.value?.id,
        override_reason: form.override_reason,
      })
    }
    if (drawerMode.value === 'create') {
      await createInterviewRound(applicationId.value, {
        name: form.name.trim(),
        sequence_no: form.sequence_no,
        format: form.format,
        owner_id: form.owner_id,
        interviewers,
        schedule: schedulePayload,
        idempotency_key: newIdempotencyKey('create'),
      })
    } else if (drawerMode.value === 'edit' && activeRound.value) {
      await updateInterviewRound(activeRound.value.id, {
        name: form.name.trim(),
        format: form.format,
        owner_id: form.owner_id,
        interviewers,
        version: activeRound.value.version,
      })
    } else if (drawerMode.value === 'schedule' && activeRound.value && schedulePayload) {
      await scheduleInterviewRound(activeRound.value.id, {
        ...schedulePayload,
        version: activeRound.value.version,
        idempotency_key: newIdempotencyKey('schedule'),
      })
    }
    ElMessage.success(withSchedule ? '安排已保存' : '草稿已保存')
    drawerOpen.value = false
    await load()
  } catch (err: unknown) {
    handleActionError(err, '保存失败')
  } finally {
    saving.value = false
  }
}

async function submitReschedule() {
  if (!activeRound.value) return
  if (!rescheduleForm.reschedule_reason.trim()) {
    ElMessage.error('改期原因必填')
    return
  }
  saving.value = true
  try {
    validateTimes(rescheduleForm.start_at, rescheduleForm.end_at)
    const start = toIso(rescheduleForm.start_at)
    const end = toIso(rescheduleForm.end_at)
    await ensureConflicts({
      interviewer_ids: activeRound.value.interviewers.map((item) => item.interviewer_id),
      start_at_utc: start,
      end_at_utc: end,
      timezone: rescheduleForm.timezone,
      exclude_round_id: activeRound.value.id,
      override_reason: rescheduleForm.override_reason,
    })
    await rescheduleInterviewRound(activeRound.value.id, {
      start_at_utc: start,
      end_at_utc: end,
      timezone: rescheduleForm.timezone,
      format: rescheduleForm.format,
      meeting_mode: rescheduleForm.format === 'ONLINE' ? 'MANUAL' : null,
      meeting_url: rescheduleForm.format === 'ONLINE' ? rescheduleForm.meeting_url : null,
      meeting_no: rescheduleForm.meeting_no || null,
      meeting_password: rescheduleForm.meeting_password || null,
      clear_meeting_password: rescheduleForm.clear_meeting_password,
      location: rescheduleForm.format === 'OFFLINE' ? rescheduleForm.location : null,
      contact_name: rescheduleForm.contact_name || null,
      contact_phone: rescheduleForm.contact_phone || null,
      reschedule_reason: rescheduleForm.reschedule_reason.trim(),
      version: activeRound.value.version,
      idempotency_key: newIdempotencyKey('reschedule'),
      override_interviewer_conflict: Boolean(rescheduleForm.override_reason.trim()),
      override_reason: rescheduleForm.override_reason.trim() || undefined,
    })
    ElMessage.success('改期已保存')
    rescheduleOpen.value = false
    await load()
  } catch (err: unknown) {
    handleActionError(err, '改期失败')
  } finally {
    saving.value = false
  }
}

async function submitCancel() {
  if (!activeRound.value) return
  const selected = cancelCodes.value.find((item) => item.code === cancelForm.reason_code)
  if (!cancelForm.reason_code) {
    ElMessage.error('请选择取消原因')
    return
  }
  if (selected?.requires_description && !cancelForm.description.trim()) {
    ElMessage.error('选择其他时必须填写说明')
    return
  }
  saving.value = true
  try {
    await ElMessageBox.confirm('确认取消该面试轮次？此操作不可恢复。', '取消面试', {
      type: 'warning',
    })
    await cancelInterviewRound(activeRound.value.id, {
      reason_code: cancelForm.reason_code,
      description: cancelForm.description || undefined,
      version: activeRound.value.version,
      idempotency_key: newIdempotencyKey('cancel'),
    })
    ElMessage.success('已取消')
    cancelOpen.value = false
    await load()
  } catch (err: unknown) {
    if (err === 'cancel') return
    handleActionError(err, '取消失败')
  } finally {
    saving.value = false
  }
}

async function submitAbnormal() {
  if (!activeRound.value) return
  const selected = abnormalCodes.value.find((item) => item.code === abnormalForm.reason_code)
  if (!abnormalForm.reason_code) {
    ElMessage.error('请选择异常原因')
    return
  }
  if (selected?.requires_description && !abnormalForm.description.trim()) {
    ElMessage.error('选择其他时必须填写说明')
    return
  }
  saving.value = true
  try {
    await ElMessageBox.confirm('确认异常结束本轮面试？', '异常结束', { type: 'warning' })
    await endInterviewAbnormally(activeRound.value.id, {
      reason_code: abnormalForm.reason_code,
      description: abnormalForm.description || undefined,
      version: activeRound.value.version,
      idempotency_key: newIdempotencyKey('abnormal'),
    })
    ElMessage.success('已异常结束')
    abnormalOpen.value = false
    await load()
  } catch (err: unknown) {
    handleActionError(err, '操作失败')
  } finally {
    saving.value = false
  }
}

async function runAction(round: InterviewRound, action: 'start' | 'finish' | 'complete') {
  saving.value = true
  try {
    if (action === 'complete') {
      await ElMessageBox.confirm('确认本轮面试事实已完成？不会改变招聘决定。', '确认完成', {
        type: 'warning',
      })
    }
    const payload = {
      version: round.version,
      idempotency_key: newIdempotencyKey(action),
    }
    if (action === 'start') await startInterviewRound(round.id, payload)
    if (action === 'finish') await finishInterviewRound(round.id, payload)
    if (action === 'complete') await completeInterviewRound(round.id, payload)
    ElMessage.success('已更新')
    await load()
  } catch (err: unknown) {
    handleActionError(err, '操作失败')
  } finally {
    saving.value = false
  }
}

function handleActionError(err: unknown, fallback: string) {
  if (isConflictError(err)) {
    conflictMessage.value = String(errorDetail(err, CONFLICT_HINT))
    ElMessage.error(conflictMessage.value)
    return
  }
  ElMessage.error(String(errorDetail(err, fallback)))
}

defineExpose({ form, cancelForm, rescheduleForm, abnormalForm })

onMounted(() => {
  void load()
})
</script>

<template>
  <AdminLayout>
    <div v-loading="loading" class="page">
      <div class="panel page-section-compact">
        <div class="panel-head">
          <div>
            <el-button link type="primary" @click="router.back()">← 返回</el-button>
            <h1>面试安排</h1>
            <div class="meta-row" v-if="timeline">
              <span class="meta-chip">候选人 <strong>{{ timeline.candidate_name }}</strong></span>
              <span class="meta-chip">岗位 <strong>{{ timeline.job_name || '—' }}</strong></span>
              <span class="meta-chip">岗位版本 <strong>{{ timeline.job_version_label || '—' }}</strong></span>
              <span class="meta-chip">
                应聘状态
                <strong>{{ pipelineLabels[timeline.pipeline_status] || timeline.pipeline_status }}</strong>
              </span>
              <span class="meta-chip">
                轮次
                <strong>{{ timeline.completed_round_count }}/{{ timeline.total_round_count }}</strong>
                已完成
              </span>
            </div>
          </div>
          <el-button
            v-if="canManage"
            type="primary"
            data-test="add-round"
            @click="openCreate"
          >
            新增面试轮次
          </el-button>
        </div>
      </div>

      <el-alert
        v-if="reasonCodesError"
        title="原因选项加载失败"
        type="error"
        show-icon
        :closable="false"
        style="margin-bottom: 12px"
      />
      <el-alert
        v-if="conflictMessage"
        :title="conflictMessage"
        type="warning"
        show-icon
        :closable="false"
        style="margin-bottom: 12px"
      >
        <el-button data-test="refresh-conflict" @click="load">刷新</el-button>
      </el-alert>

      <div class="panel">
        <div class="panel-head">
          <h3>面试时间轴</h3>
        </div>
        <el-empty v-if="!sortedRounds.length" description="尚未安排面试轮次" />
        <el-timeline v-else>
          <el-timeline-item
            v-for="round in sortedRounds"
            :key="round.id"
            :timestamp="`第 ${round.sequence_no} 轮`"
          >
            <div class="round-card" :data-round-id="round.id">
              <div class="round-head">
                <div>
                  <strong class="round-name">{{ round.name }}</strong>
                  <el-tag size="small" class="status-tag">{{ statusLabels[round.status] || round.status }}</el-tag>
                  <el-tag size="small" type="info">{{ round.format === 'OFFLINE' ? '线下' : '线上' }}</el-tag>
                </div>
                <div class="round-actions">
                  <el-button
                    v-if="hasAction(round, 'edit')"
                    link
                    type="primary"
                    size="small"
                    data-test="edit"
                    @click="openEdit(round)"
                  >
                    编辑
                  </el-button>
                  <el-button
                    v-if="hasAction(round, 'schedule')"
                    link
                    type="primary"
                    size="small"
                    data-test="schedule-action"
                    @click="openSchedule(round)"
                  >
                    安排
                  </el-button>
                  <el-button
                    v-if="hasAction(round, 'reschedule')"
                    link
                    type="primary"
                    size="small"
                    data-test="reschedule"
                    @click="openReschedule(round)"
                  >
                    改期
                  </el-button>
                  <el-button
                    v-if="hasAction(round, 'cancel')"
                    link
                    type="danger"
                    size="small"
                    data-test="cancel"
                    @click="openCancel(round)"
                  >
                    取消
                  </el-button>
                  <el-button
                    v-if="hasAction(round, 'start')"
                    type="primary"
                    size="small"
                    data-test="start"
                    :loading="saving"
                    @click="runAction(round, 'start')"
                  >
                    开始
                  </el-button>
                  <el-button
                    v-if="hasAction(round, 'finish')"
                    type="primary"
                    size="small"
                    data-test="finish"
                    @click="runAction(round, 'finish')"
                  >
                    结束
                  </el-button>
                  <el-button
                    v-if="hasAction(round, 'end_abnormally')"
                    type="danger"
                    plain
                    size="small"
                    data-test="end-abnormally"
                    @click="openAbnormal(round)"
                  >
                    异常结束
                  </el-button>
                  <el-button
                    v-if="hasAction(round, 'complete')"
                    type="primary"
                    size="small"
                    data-test="complete"
                    @click="runAction(round, 'complete')"
                  >
                    确认完成
                  </el-button>
                </div>
              </div>
              <p class="round-meta">
                时间
                {{ formatLocal(round.current_schedule?.start_at_utc, round.current_schedule?.timezone || 'Asia/Shanghai') }}
                –
                {{ formatLocal(round.current_schedule?.end_at_utc, round.current_schedule?.timezone || 'Asia/Shanghai') }}
                · 时区 {{ round.current_schedule?.timezone || '—' }}
                · 负责人 {{ round.owner_name || '—' }}
                · 面试官 {{ interviewerLabel(round) || '—' }}
                · 安排版本 {{ round.current_schedule?.schedule_version || '—' }}
              </p>
            </div>
          </el-timeline-item>
        </el-timeline>
      </div>
    </div>

    <el-drawer v-model="drawerOpen" :title="drawerMode === 'create' ? '新增面试轮次' : drawerMode === 'edit' ? '编辑面试轮次' : '安排面试'" size="520px">
      <el-form label-position="top" class="drawer-form">
        <el-form-item label="轮次名称">
          <el-input v-model="form.name" placeholder="例如：第一轮专业面" />
        </el-form-item>
        <el-form-item label="顺序">
          <el-input v-model.number="form.sequence_no" />
        </el-form-item>
        <el-form-item label="形式">
          <select
            class="native-select"
            data-test="format-select"
            :value="form.format"
            @change="form.format = ($event.target as HTMLSelectElement).value as InterviewFormat"
          >
            <option value="ONLINE">线上</option>
            <option value="OFFLINE">线下</option>
          </select>
        </el-form-item>
        <el-form-item label="负责人">
          <el-select v-model="form.owner_id" filterable placeholder="选择负责人">
            <el-option
              v-for="item in staff"
              :key="item.id"
              :label="item.display_name"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="面试官">
          <el-select v-model="form.interviewer_ids" multiple filterable placeholder="选择面试官">
            <el-option
              v-for="item in staff"
              :key="item.id"
              :label="item.display_name"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <template v-if="drawerMode !== 'edit'">
          <el-form-item label="时区">
            <el-input v-model="form.timezone" />
          </el-form-item>
          <el-form-item label="开始时间">
            <el-date-picker v-model="form.start_at" type="datetime" style="width: 100%" />
          </el-form-item>
          <el-form-item label="结束时间">
            <el-date-picker v-model="form.end_at" type="datetime" style="width: 100%" />
          </el-form-item>
          <template v-if="form.format === 'ONLINE'">
            <el-form-item label="会议链接">
              <el-input v-model="form.meeting_url" placeholder="手工会议入口" />
            </el-form-item>
            <el-form-item label="会议号">
              <el-input v-model="form.meeting_no" />
            </el-form-item>
            <el-form-item label="会议密码">
              <el-input
                v-model="form.meeting_password"
                type="password"
                data-test="meeting-password"
                placeholder="不填则不设置"
              />
            </el-form-item>
          </template>
          <template v-else>
            <el-form-item label="地点">
              <el-input v-model="form.location" />
            </el-form-item>
            <el-form-item label="联系人">
              <el-input v-model="form.contact_name" />
            </el-form-item>
          </template>
          <el-form-item label="面试官冲突覆盖原因">
            <el-input
              v-model="form.override_reason"
              data-test="override-reason"
              placeholder="有面试官冲突时必填"
            />
          </el-form-item>
        </template>
      </el-form>
      <template #footer>
        <el-button @click="drawerOpen = false">取消</el-button>
        <el-button
          v-if="drawerMode !== 'schedule'"
          data-test="save-draft"
          :loading="saving"
          @click="saveDraft"
        >
          保存草稿
        </el-button>
        <el-button
          type="primary"
          data-test="save-schedule"
          :loading="saving"
          @click="saveSchedule"
        >
          保存安排
        </el-button>
      </template>
    </el-drawer>

    <el-dialog v-model="rescheduleOpen" title="改期" width="560px">
      <div v-if="activeRound?.current_schedule" class="old-new">
        <p><strong>原安排</strong>：{{ formatLocal(activeRound.current_schedule.start_at_utc, activeRound.current_schedule.timezone) }} – {{ formatLocal(activeRound.current_schedule.end_at_utc, activeRound.current_schedule.timezone) }}（{{ activeRound.current_schedule.timezone }}）版本 {{ activeRound.current_schedule.schedule_version }}</p>
        <p><strong>新安排</strong></p>
      </div>
      <el-form label-position="top">
        <el-form-item label="开始时间">
          <el-date-picker v-model="rescheduleForm.start_at" type="datetime" style="width: 100%" />
        </el-form-item>
        <el-form-item label="结束时间">
          <el-date-picker v-model="rescheduleForm.end_at" type="datetime" style="width: 100%" />
        </el-form-item>
        <el-form-item label="时区">
          <el-input v-model="rescheduleForm.timezone" />
        </el-form-item>
        <el-form-item label="改期原因">
          <el-input v-model="rescheduleForm.reschedule_reason" type="textarea" :rows="3" />
        </el-form-item>
        <el-form-item label="会议密码">
          <el-input v-model="rescheduleForm.meeting_password" type="password" placeholder="留空则保留原密码" />
        </el-form-item>
        <el-form-item>
          <label>
            <input v-model="rescheduleForm.clear_meeting_password" type="checkbox" data-test="clear-meeting-password" />
            清除会议密码
          </label>
        </el-form-item>
        <el-form-item label="面试官冲突覆盖原因">
          <el-input v-model="rescheduleForm.override_reason" data-test="reschedule-override-reason" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="rescheduleOpen = false">取消</el-button>
        <el-button type="primary" data-test="submit-reschedule" :loading="saving" @click="submitReschedule">确认改期</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="cancelOpen" title="取消面试" width="480px">
      <el-form label-position="top">
        <el-form-item label="取消原因">
          <el-select v-model="cancelForm.reason_code" placeholder="从后端加载">
            <el-option
              v-for="item in cancelCodes"
              :key="item.code + item.category"
              :label="item.label"
              :value="item.code"
            />
          </el-select>
        </el-form-item>
        <el-form-item v-if="cancelForm.reason_code === 'OTHER' || cancelCodes.find((item) => item.code === cancelForm.reason_code)?.requires_description" label="说明">
          <el-input v-model="cancelForm.description" type="textarea" :rows="3" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="cancelOpen = false">返回</el-button>
        <el-button type="danger" data-test="submit-cancel" :loading="saving" @click="submitCancel">确认取消</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="abnormalOpen" title="异常结束" width="480px">
      <el-form label-position="top">
        <el-form-item label="异常原因">
          <el-select v-model="abnormalForm.reason_code">
            <el-option
              v-for="item in abnormalCodes"
              :key="item.code + item.category"
              :label="item.label"
              :value="item.code"
            />
          </el-select>
        </el-form-item>
        <el-form-item v-if="abnormalForm.reason_code === 'OTHER'" label="说明">
          <el-input v-model="abnormalForm.description" type="textarea" :rows="3" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="abnormalOpen = false">返回</el-button>
        <el-button type="danger" :loading="saving" @click="submitAbnormal">确认</el-button>
      </template>
    </el-dialog>
  </AdminLayout>
</template>

<style scoped>
.page {
  max-width: 1100px;
}
.panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 16px;
  margin-bottom: 14px;
}
.panel-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: flex-start;
  margin-bottom: 8px;
}
h1 {
  margin: 4px 0 10px;
  font-size: 22px;
}
h3 {
  margin: 0;
  font-size: 15px;
}
.meta-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.meta-chip {
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 4px 10px;
  font-size: 12px;
  color: var(--muted);
}
.meta-chip strong {
  color: var(--fg);
  font-weight: 600;
}
.round-card {
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 12px;
  background: var(--surface);
}
.round-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
}
.round-name {
  margin-right: 8px;
}
.status-tag {
  margin-right: 6px;
}
.round-meta {
  margin: 8px 0 0;
  color: var(--muted);
  font-size: 12px;
}
.round-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.native-select {
  width: 100%;
  height: 32px;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 0 8px;
  background: var(--surface);
}
.drawer-form,
.old-new {
  font-size: 13px;
}
</style>
