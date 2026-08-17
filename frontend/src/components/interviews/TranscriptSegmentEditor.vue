<script setup lang="ts">
export interface EditableSegment {
  localId: string
  id?: string
  segment_no: number
  speaker_key: string
  speaker_name: string
  speaker_role: string
  start_time_ms: number | null
  end_time_ms: number | null
  text: string
  source_type: string
  source_segment_refs: number[]
  is_included_in_analysis: boolean
  is_unclear: boolean
}

const props = defineProps<{
  segments: EditableSegment[]
  readonly: boolean
  selectedLocalId: string | null
}>()

const emit = defineEmits<{
  'update:selectedLocalId': [value: string | null]
  'update:text': [localId: string, text: string]
  merge: [localId: string]
  split: [localId: string, cursor: number]
  remove: [localId: string]
  'insert-before': [localId: string]
  'insert-after': [localId: string]
  'move-up': [localId: string]
  'move-down': [localId: string]
}>()

function onSelect(localId: string) {
  emit('update:selectedLocalId', localId)
}

function onTextInput(localId: string, event: Event) {
  const target = event.target as HTMLTextAreaElement
  emit('update:text', localId, target.value)
}

function onSplit(localId: string, event: Event) {
  const target = event.currentTarget as HTMLElement
  const area = target
    .closest('.segment-card')
    ?.querySelector('textarea') as HTMLTextAreaElement | null
  const cursor = area?.selectionStart ?? 0
  emit('split', localId, cursor)
}

function segmentClass(segment: EditableSegment) {
  return {
    active: segment.localId === props.selectedLocalId,
    unclear: segment.is_unclear || segment.speaker_role === 'UNKNOWN',
    excluded: !segment.is_included_in_analysis,
  }
}
</script>

<template>
  <div class="segment-editor" data-test="segment-editor">
    <div
      v-for="segment in segments"
      :key="segment.localId"
      class="segment-card"
      :class="segmentClass(segment)"
      :data-test="`segment-${segment.localId}`"
      @click="onSelect(segment.localId)"
    >
      <div class="segment-toolbar">
        <span class="seg-no">#{{ segment.segment_no }}</span>
        <span
          v-if="segment.source_type === 'MANUAL_ADDITION'"
          class="manual-badge"
          data-test="manual-addition-label"
        >
          人工补充
        </span>
        <span
          v-if="segment.speaker_role === 'UNKNOWN'"
          class="warn-badge"
          data-test="unknown-label"
        >
          UNKNOWN
        </span>
        <span
          v-if="segment.is_unclear"
          class="warn-badge"
          data-test="unclear-label"
        >
          听不清
        </span>
        <span
          v-if="!segment.is_included_in_analysis"
          class="exclude-badge"
          data-test="exclude-label"
        >
          不参与分析
        </span>
        <div v-if="!readonly" class="ops">
          <button type="button" data-test="merge-btn" @click.stop="emit('merge', segment.localId)">
            合并下一条
          </button>
          <button type="button" data-test="split-btn" @click.stop="onSplit(segment.localId, $event)">
            拆分
          </button>
          <button type="button" data-test="delete-btn" @click.stop="emit('remove', segment.localId)">
            删除
          </button>
          <button
            type="button"
            data-test="insert-before-btn"
            @click.stop="emit('insert-before', segment.localId)"
          >
            前插
          </button>
          <button
            type="button"
            data-test="insert-after-btn"
            @click.stop="emit('insert-after', segment.localId)"
          >
            后插
          </button>
          <button type="button" data-test="move-up-btn" @click.stop="emit('move-up', segment.localId)">
            上移
          </button>
          <button
            type="button"
            data-test="move-down-btn"
            @click.stop="emit('move-down', segment.localId)"
          >
            下移
          </button>
        </div>
      </div>
      <div class="speaker-line">
        {{ segment.speaker_name }}（{{ segment.speaker_role }}）
      </div>
      <textarea
        :value="segment.text"
        :readonly="readonly"
        data-test="segment-text"
        rows="3"
        @input="onTextInput(segment.localId, $event)"
        @focus="onSelect(segment.localId)"
      />
    </div>
    <el-empty v-if="!segments.length" description="暂无片段" />
  </div>
</template>

<style scoped>
.segment-editor {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-height: 240px;
}
.segment-card {
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 10px;
  background: var(--surface);
  cursor: pointer;
}
.segment-card.active {
  border-color: var(--primary, #2563eb);
}
.segment-card.unclear {
  border-color: #f59e0b;
  background: #fffbeb;
}
.segment-card.excluded {
  opacity: 0.55;
}
.segment-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
  margin-bottom: 6px;
}
.seg-no {
  font-size: 12px;
  color: var(--muted);
}
.manual-badge {
  background: #dbeafe;
  color: #1d4ed8;
  border-radius: 4px;
  padding: 1px 6px;
  font-size: 12px;
}
.warn-badge {
  background: #fef3c7;
  color: #b45309;
  border-radius: 4px;
  padding: 1px 6px;
  font-size: 12px;
}
.exclude-badge {
  background: #f3f4f6;
  color: #6b7280;
  border-radius: 4px;
  padding: 1px 6px;
  font-size: 12px;
}
.ops {
  margin-left: auto;
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.ops button {
  border: 1px solid var(--border);
  background: var(--bg);
  border-radius: 4px;
  font-size: 12px;
  padding: 2px 6px;
  cursor: pointer;
}
.speaker-line {
  font-size: 12px;
  color: var(--muted);
  margin-bottom: 6px;
}
textarea {
  width: 100%;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 8px;
  resize: vertical;
  background: var(--bg);
  color: var(--fg);
  font-family: inherit;
}
</style>
