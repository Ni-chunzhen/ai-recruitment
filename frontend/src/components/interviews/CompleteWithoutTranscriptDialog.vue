<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, reactive, ref, watch } from 'vue'

import {
  completeWithoutTranscript,
  getTranscriptReasonCodes,
  type TranscriptReasonCode,
} from '../../api/interviews'

const props = defineProps<{
  open: boolean
  roundId: string | null
  roundVersion: number
}>()

const emit = defineEmits<{
  'update:open': [value: boolean]
  completed: []
}>()

const loading = ref(false)
const saving = ref(false)
const codes = ref<TranscriptReasonCode[]>([])
const loadError = ref(false)
const form = reactive({
  reason_code: '',
  description: '',
})

const dialogOpen = computed({
  get: () => props.open,
  set: (value: boolean) => emit('update:open', value),
})

const selected = computed(
  () => codes.value.find((item) => item.code === form.reason_code) ?? null,
)

function newIdempotencyKey() {
  return `complete-without-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}

async function loadCodes() {
  loading.value = true
  loadError.value = false
  try {
    const data = await getTranscriptReasonCodes()
    codes.value = data.items
  } catch {
    loadError.value = true
    codes.value = []
    ElMessage.error('原因选项加载失败')
  } finally {
    loading.value = false
  }
}

watch(
  () => props.open,
  (open) => {
    if (open) {
      form.reason_code = ''
      form.description = ''
      void loadCodes()
    }
  },
  { immediate: true },
)

async function submit() {
  if (!props.roundId) return
  if (!form.reason_code) {
    ElMessage.warning('请选择原因')
    return
  }
  if (selected.value?.requires_description && !form.description.trim()) {
    ElMessage.warning('选择「其他」时必须填写说明')
    return
  }
  saving.value = true
  try {
    await completeWithoutTranscript(props.roundId, {
      reason_code: form.reason_code,
      description: form.description.trim() || null,
      version: props.roundVersion,
      idempotency_key: newIdempotencyKey(),
    })
    ElMessage.success('已无转写完成')
    dialogOpen.value = false
    emit('completed')
  } catch (error) {
    ElMessage.error(
      (error as { response?: { data?: { detail?: string } } })?.response?.data
        ?.detail || '操作失败',
    )
  } finally {
    saving.value = false
  }
}

defineExpose({ form, codes })
</script>

<template>
  <el-dialog
    v-model="dialogOpen"
    title="无转写完成"
    width="520px"
    data-test="complete-without-transcript-dialog"
  >
    <el-alert
      v-if="loadError"
      title="原因选项加载失败"
      type="error"
      :closable="false"
      show-icon
      data-test="reason-codes-error"
      style="margin-bottom: 12px"
    />
    <div v-loading="loading">
      <el-form-item label="原因">
        <select
          class="native-select"
          data-test="without-reason-code"
          :value="form.reason_code"
          @change="form.reason_code = ($event.target as HTMLSelectElement).value"
        >
          <option value="" disabled>请选择</option>
          <option v-for="item in codes" :key="item.code" :value="item.code">
            {{ item.label }}
          </option>
        </select>
      </el-form-item>
      <el-form-item
        v-if="selected?.requires_description"
        label="说明（必填）"
      >
        <el-input
          v-model="form.description"
          type="textarea"
          :rows="3"
          data-test="without-description"
          placeholder="请说明无转写完成的原因"
        />
      </el-form-item>
    </div>
    <template #footer>
      <el-button @click="dialogOpen = false">返回</el-button>
      <el-button
        type="primary"
        data-test="submit-without-transcript"
        :loading="saving"
        :disabled="loadError || !codes.length"
        @click="submit"
      >
        确认完成
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.native-select {
  width: 100%;
  height: 32px;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 0 8px;
  background: var(--surface);
}
</style>
