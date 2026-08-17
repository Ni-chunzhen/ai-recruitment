<script setup lang="ts">
import { computed } from 'vue'

import type { TranscriptChangeCounts } from '../../api/interviews'

const props = defineProps<{
  open: boolean
  versionLabel: string
  segmentCount: number
  changeCounts: TranscriptChangeCounts
  unknownCount: number
  unclearCount: number
  blockers: string[]
  confirming: boolean
}>()

const emit = defineEmits<{
  'update:open': [value: boolean]
  confirm: []
}>()

const dialogOpen = computed({
  get: () => props.open,
  set: (value: boolean) => emit('update:open', value),
})

const canConfirm = computed(() => props.blockers.length === 0)
</script>

<template>
  <el-dialog
    v-model="dialogOpen"
    title="确认校对"
    width="560px"
    data-test="transcript-confirm-dialog"
  >
    <p data-test="confirm-version">当前版本：{{ versionLabel }}</p>
    <ul class="counts" data-test="confirm-counts">
      <li>片段总数：{{ segmentCount }}</li>
      <li>说话人修正：{{ changeCounts.speaker_changes }}</li>
      <li>文本修正：{{ changeCounts.text_corrections }}</li>
      <li>合并/拆分：{{ changeCounts.merge_split_count }}</li>
      <li>删除：{{ changeCounts.deleted_count }}</li>
      <li>人工补充：{{ changeCounts.manual_addition_count }}</li>
      <li>排除分析：{{ changeCounts.excluded_from_analysis_count }}</li>
      <li>未知说话人：{{ unknownCount }}</li>
      <li>听不清：{{ unclearCount }}</li>
    </ul>
    <p class="hint">
      确认后将生成不可修改的确认版本 Cn，并完成本轮面试事实。
    </p>
    <el-alert
      v-if="blockers.length"
      type="error"
      :closable="false"
      show-icon
      data-test="confirm-blockers"
    >
      <ul>
        <li v-for="item in blockers" :key="item">{{ item }}</li>
      </ul>
    </el-alert>
    <template #footer>
      <el-button @click="dialogOpen = false">返回</el-button>
      <el-button
        type="primary"
        data-test="submit-confirm"
        :disabled="!canConfirm"
        :loading="confirming"
        @click="emit('confirm')"
      >
        二次确认并完成
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.counts {
  margin: 8px 0;
  padding-left: 18px;
  font-size: 13px;
  color: var(--fg);
}
.hint {
  font-size: 13px;
  color: var(--muted);
}
</style>
