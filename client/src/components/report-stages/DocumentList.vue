<script lang="ts">
import {defineComponent, type PropType} from 'vue'
import {mapActions, mapState} from 'vuex'
import {FontAwesomeIcon} from '@fortawesome/vue-fontawesome'
import {faFileLines} from '@fortawesome/free-solid-svg-icons/faFileLines'
import {faTrashCan} from '@fortawesome/free-solid-svg-icons/faTrashCan'
import {faSpinner} from '@fortawesome/free-solid-svg-icons/faSpinner'
import {faRotateRight} from '@fortawesome/free-solid-svg-icons/faRotateRight'
import type {ReportDetails, ReportDocument} from '../../api'

export default defineComponent({
  components: {FontAwesomeIcon},
  props: {report: {type: Object as PropType<ReportDetails>, required: true}},
  emits: {select: (document: ReportDocument) => Boolean(document)},
  data() {
    return {
      faFileLines, faTrashCan, faSpinner, faRotateRight,
      statusLabels: {
        queued: 'Загружен · в очереди',
        running: 'Обрабатывается',
        completed: 'Обработан',
        failed: 'Ошибка',
      },
    }
  },
  computed: {
    ...mapState('report', ['advancing', 'deleting', 'uploads']),
  },
  methods: {
    ...mapActions('report', ['refresh', 'remove', 'dismissUpload']),
    sizeLabel(size: number) {
      return size < 1024 * 1024 ? `${Math.max(1, Math.round(size / 1024))} КБ` : `${(size / 1024 / 1024).toFixed(1)} МБ`
    },
  },
})
</script>

<template>
  <section class="rounded-lg border border-slate-200 bg-white"
           aria-labelledby="documents-heading">
    <div class="flex flex-wrap items-center justify-between gap-3 p-4">
      <h2 id="documents-heading" class="font-semibold">Документы <span
          class="ml-2 text-slate-500">{{ report.documents.length + uploads.length }}</span></h2>
      <div class="flex items-center gap-4">
            <span class="text-xs text-slate-500" aria-live="polite">Обработано {{
                report.progress.completed
              }} из {{ report.progress.total }}</span>
        <button
            class="inline-flex cursor-pointer items-center gap-2 rounded px-3 py-2 text-slate-600 hover:bg-slate-100 disabled:opacity-50"
            @click="refresh">
          <FontAwesomeIcon :icon="faRotateRight" class="size-3 text-slate-400" aria-hidden="true"/>
          Обновить
        </button>
      </div>
    </div>
    <p v-if="!report.documents.length && !uploads.length"
       class="border-t border-slate-200 p-8 text-center text-slate-500">Пока нет документов. Выберите файлы или
      перетащите их в область загрузки.</p>
    <ul v-else class="divide-y divide-slate-200 border-t border-slate-200">
      <li v-for="upload in uploads" :key="`upload-${upload.id}`" class="flex items-start gap-3 p-4">
        <FontAwesomeIcon :icon="upload.error ? faFileLines : faSpinner" class="mt-1 size-4 shrink-0 text-slate-400"
                         :class="upload.error ? '' : 'animate-spin'" aria-hidden="true"/>
        <div class="min-w-0 flex-1"><p class="break-all font-medium">{{ upload.name }}</p>
          <p class="mt-1 text-xs text-slate-500">
            {{ upload.role === 'final_report' ? 'Итоговый отчёт' : 'Подтверждающий документ' }}</p>
          <p class="mt-2 text-xs" :class="upload.error ? 'text-red-700' : 'text-blue-700'"
             :role="upload.error ? 'alert' : 'status'">{{ upload.error || 'Загружается…' }}</p></div>
        <button v-if="upload.error" class="cursor-pointer rounded px-3 py-2 text-red-700 hover:bg-red-50"
                :aria-label="`Удалить неудачную загрузку ${upload.name}`"
                @click="dismissUpload(upload.id)">Удалить
        </button>
      </li>
      <li v-for="document in report.documents" :key="document.id" class="flex flex-wrap items-start gap-3 p-4">
        <FontAwesomeIcon :icon="['queued', 'running'].includes(document.ocr_status) ? faSpinner : faFileLines"
                         class="mt-1 size-4 shrink-0 text-slate-400"
                         :class="['queued', 'running'].includes(document.ocr_status) ? 'animate-spin' : ''"
                         aria-hidden="true"/>
        <div class="min-w-0 flex-1 basis-1/2 sm:basis-0">
          <p class="break-all font-medium">{{ document.name }}</p>
          <p class="mt-1 text-xs text-slate-500">
            {{ document.role === 'final_report' ? 'Итоговый отчёт' : 'Подтверждающий документ' }} ·
            {{ sizeLabel(document.size) }}<span v-if="document.user_edited" class="ml-2 text-blue-700">Изменён пользователем</span>
          </p>
          <p v-if="document.error" class="mt-2 text-xs text-red-700" role="alert">{{ document.error }}</p>
        </div>
        <span class="inline-flex h-7 items-center rounded px-3 text-xs leading-none"
              :class="document.ocr_status === 'completed' ? 'bg-green-50 text-green-700' : document.ocr_status === 'failed' ? 'bg-red-50 text-red-700' : 'bg-blue-50 text-blue-700'">{{
            statusLabels[document.ocr_status]
          }}</span>
        <div class="ml-auto flex items-center gap-2">
          <button v-if="document.ocr_status === 'completed'"
                  class="inline-flex h-7 cursor-pointer items-center rounded px-3 text-xs leading-none text-blue-700 hover:bg-blue-50 disabled:opacity-50"
                  :disabled="deleting.includes(document.id) || advancing" @click="$emit('select', document)">Просмотреть
          </button>
          <button v-if="report.stage === 'documents'"
                  class="inline-flex h-7 cursor-pointer items-center rounded px-3 text-xs leading-none text-slate-500 hover:bg-red-50 hover:text-red-700 disabled:opacity-50"
                  :aria-label="`Удалить ${document.name}`" :disabled="deleting.includes(document.id) || advancing"
                  @click="remove(document.id)">
            <FontAwesomeIcon :icon="faTrashCan" class="size-3" aria-hidden="true"/>
            <span v-if="deleting.includes(document.id)" class="ml-2">Удаляем…</span></button>
        </div>
      </li>
    </ul>
  </section>
</template>
