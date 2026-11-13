<script lang="ts">
import {defineComponent, type PropType} from 'vue'
import {FontAwesomeIcon} from '@fortawesome/vue-fontawesome'
import {faFileLines} from '@fortawesome/free-solid-svg-icons/faFileLines'
import {faTrashCan} from '@fortawesome/free-solid-svg-icons/faTrashCan'
import {faSpinner} from '@fortawesome/free-solid-svg-icons/faSpinner'
import type {ReportDocument} from '../../api'
import LogModal from '../LogModal.vue'

export default defineComponent({
  components: {FontAwesomeIcon, LogModal},
  props: {
    file: {type: Object as PropType<Pick<ReportDocument, 'name' | 'size' | 'role' | 'error'>>, required: true},
    status: {type: String as PropType<ReportDocument['status'] | 'uploading'>, required: true},
    edited: Boolean,
    duplicate: Boolean,
    removable: Boolean,
    removing: Boolean,
    disabled: Boolean,
    logUrl: {type: String, default: ''},
  },
  emits: ['remove'],
  data() {
    return {
      faFileLines, faTrashCan, faSpinner,
      showingLog: false,
      statusLabels: {
        uploading: 'Загружается…',
        ocr_queued: 'В очереди распознавания',
        ocr_running: 'Распознаётся',
        ocr_completed: 'Распознан',
        entities_queued: 'В очереди извлечения сущностей',
        entities_running: 'Извлекаем сущности',
        completed: 'Сущности извлечены',
        failed: 'Ошибка',
      },
    }
  },
  computed: {
    busy(): boolean {
      return ['uploading', 'ocr_queued', 'ocr_running', 'entities_queued', 'entities_running'].includes(this.status)
    },
    sizeLabel(): string {
      return this.file.size < 1024 * 1024
          ? `${Math.ceil(this.file.size / 1024)} КБ`
          : `${(this.file.size / 1024 / 1024).toFixed(1)} МБ`
    },
  },
})
</script>

<template>
  <li class="flex flex-wrap items-start gap-3 p-4">
    <FontAwesomeIcon :icon="busy ? faSpinner : faFileLines"
                     class="mt-1 size-4 shrink-0 text-slate-400"
                     :class="{'animate-spin': busy}" aria-hidden="true"/>
    <div class="min-w-0 flex-1 basis-1/2 sm:basis-0">
      <p class="break-all font-medium">{{ file.name }}</p>
      <p class="mt-1 text-xs text-slate-500">
        {{ file.role === 'final_report' ? 'Итоговый отчёт' : 'Подтверждающий документ' }} ·
        {{ sizeLabel }}<span v-if="edited" class="ml-2 text-blue-700">Изменён пользователем</span>
      </p>
      <div v-if="file.error" class="mt-2 flex flex-wrap items-baseline gap-x-2 gap-y-1 text-xs">
        <p class="min-w-0 break-words text-red-700" role="alert">{{ file.error }}</p>
        <button v-if="logUrl" type="button"
                class="shrink-0 cursor-pointer rounded text-blue-700 underline underline-offset-2 hover:text-blue-900"
                :aria-label="`Подробнее об ошибке в ${file.name}`" @click="showingLog = true">Подробнее
        </button>
      </div>
    </div>
    <div class="flex flex-wrap items-center gap-2">
      <span class="inline-flex h-7 items-center rounded px-3 text-xs leading-none" role="status"
            :class="['ocr_completed', 'completed'].includes(status) ? 'bg-green-50 text-green-700' : status === 'failed' ? 'bg-red-50 text-red-700' : 'bg-blue-50 text-blue-700'">{{
          statusLabels[status]
        }}</span>
      <span v-if="duplicate"
            class="inline-flex h-7 items-center rounded bg-amber-100 px-3 text-xs leading-none text-amber-900">Дубликат</span>
    </div>
    <div class="ml-auto flex items-center gap-2">
      <slot/>
      <button v-if="removable"
              class="inline-flex h-7 cursor-pointer items-center rounded px-3 text-xs leading-none text-slate-500 hover:bg-red-50 hover:text-red-700 disabled:opacity-50"
              :aria-label="`Удалить ${file.name}`" :disabled="removing || disabled"
              @click="$emit('remove')">
        <FontAwesomeIcon :icon="faTrashCan" class="size-3" aria-hidden="true"/>
        <span v-if="removing" class="ml-2">Удаляем…</span>
      </button>
    </div>
  </li>
  <LogModal v-if="showingLog" :src="logUrl" :title="`Журнал обработки — ${file.name}`"
            @close="showingLog = false"/>
</template>
