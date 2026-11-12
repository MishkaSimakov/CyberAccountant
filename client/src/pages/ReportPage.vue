<script lang="ts">
import {defineComponent} from 'vue'
import {mapActions, mapGetters, mapState} from 'vuex'
import {FontAwesomeIcon} from '@fortawesome/vue-fontawesome'
import {faFileLines} from '@fortawesome/free-solid-svg-icons/faFileLines'
import {faTrashCan} from '@fortawesome/free-solid-svg-icons/faTrashCan'
import {faSpinner} from '@fortawesome/free-solid-svg-icons/faSpinner'
import {faCheck} from '@fortawesome/free-solid-svg-icons/faCheck'
import {faRotateRight} from '@fortawesome/free-solid-svg-icons/faRotateRight'
import type {ReportDetails, ReportDocument} from '../api'
import UploadZone from '../components/UploadZone.vue'
import MarkdownEditor from '../components/MarkdownEditor.vue'
import ReportHeader from '../components/ReportHeader.vue'

export default defineComponent({
  components: {FontAwesomeIcon, UploadZone, MarkdownEditor, ReportHeader},
  props: {reportId: {type: Number, required: true}},
  data() {
    return {
      selected: null as ReportDocument | null,
      faFileLines, faTrashCan, faSpinner, faCheck, faRotateRight,
      steps: [
        {stage: 'documents', title: 'Загрузка документов'},
        {stage: 'entities', title: 'Разметка сущностей'},
        {stage: 'verification', title: 'Проверка требований'},
      ],
      statusLabels: {
        queued: 'Загружен · в очереди',
        running: 'Обрабатывается',
        completed: 'Обработан',
        failed: 'Ошибка',
      },
    }
  },
  computed: {
    ...mapState('report', ['loading', 'loadError', 'pollError', 'actionError', 'advancing', 'deleting', 'uploads']),
    ...mapGetters('report', ['finalPresent', 'canAdvance', 'advanceTooltip']),
    report(): ReportDetails | null {
      return this.$store.state.report.current
    },
    stageIndex(): number {
      return this.steps.findIndex(step => step.stage === this.report?.stage)
    },
    completed(): boolean {
      return this.report?.stage === 'verification' && this.report.processing_status === 'completed'
    },
  },
  watch: {
    completed(completed: boolean) {
      if (completed) void this.$router.replace({name: 'requirements', params: {reportId: this.reportId}})
    },
    reportId: {
      immediate: true,
      handler(id: number) {
        this.selected = null
        void this.start(id)
      },
    },
  },
  unmounted() {
    void this.stop()
  },
  methods: {
    ...mapActions('report', ['start', 'stop', 'refresh', 'addFiles', 'remove', 'advance', 'dismissUpload']),
    sizeLabel(size: number) {
      return size < 1024 * 1024 ? `${Math.max(1, Math.round(size / 1024))} КБ` : `${(size / 1024 / 1024).toFixed(1)} МБ`
    },
  },
})
</script>

<template>
  <main class="mx-auto max-w-6xl p-6">
    <ReportHeader :report="completed ? null : report"/>
    <p v-if="loading" class="rounded-lg border border-slate-200 bg-white p-8 text-center text-slate-500" role="status">
      Загружаем отчёт…</p>
    <div v-else-if="!report" class="space-y-4 rounded-lg border border-slate-200 bg-white p-8 text-center">
      <p role="alert">{{ loadError }}</p>
      <button class="cursor-pointer rounded border border-slate-300 px-4 py-2 hover:bg-slate-50" @click="refresh">
        Попробовать снова
      </button>
    </div>

    <template v-if="report && !completed">
      <nav class="my-6 overflow-hidden rounded-lg border border-slate-200 bg-white"
           aria-label="Этапы проверки отчёта">
        <ol class="grid divide-y divide-slate-200 sm:grid-cols-3 sm:divide-x sm:divide-y-0">
          <li v-for="(step, index) in steps" :key="step.stage" class="flex items-center gap-3 p-4"
              :class="index === stageIndex ? 'bg-blue-50' : ''"
              :aria-current="index === stageIndex ? 'step' : undefined">
            <span class="flex size-8 shrink-0 items-center justify-center rounded-full font-semibold"
                  :class="index === stageIndex ? 'bg-blue-600 text-white' : index < stageIndex ? 'bg-green-100 text-green-700' : 'bg-slate-100 text-slate-400'">
              <FontAwesomeIcon v-if="index < stageIndex" :icon="faCheck" class="size-3" aria-label="Завершён"/>
              <template v-else>{{ index + 1 }}</template>
            </span>
            <p class="font-medium" :class="index > stageIndex ? 'text-slate-400' : ''">{{ step.title }}</p>
          </li>
        </ol>
      </nav>

      <p v-if="pollError" class="mb-4 rounded bg-amber-50 p-3 text-amber-900" role="status">{{ pollError }}</p>
      <p v-if="actionError" class="mb-4 rounded bg-red-50 p-3 text-red-700" role="alert">{{ actionError }}</p>

      <section v-if="report.stage === 'verification'"
               class="rounded-lg border border-slate-200 bg-white p-10 text-center"
               :aria-busy="report.processing_status !== 'failed'" aria-labelledby="verification-heading">
        <template v-if="report.processing_status === 'failed'">
          <h2 id="verification-heading" class="text-lg font-semibold">Не удалось проверить требования</h2>
          <p class="mt-3 text-slate-500" role="alert">Во время проверки произошла ошибка. Попробуйте запустить её
            снова.</p>
          <button
              class="mt-5 cursor-pointer rounded bg-blue-600 px-4 py-2 text-white hover:bg-blue-700 disabled:opacity-50"
              :disabled="!canAdvance" @click="advance">{{ advancing ? 'Запускаем…' : 'Повторить проверку' }}
          </button>
        </template>
        <template v-else>
          <FontAwesomeIcon :icon="faSpinner" class="mb-4 size-8 animate-spin text-blue-600" aria-hidden="true"/>
          <h2 id="verification-heading" class="text-lg font-semibold">Проверяем требования</h2>
          <p class="mt-3 text-slate-500" role="status">Выделяем требования и сверяем их с документами. Это может занять
            некоторое время.</p>
          <p class="mt-2 text-slate-500">Результаты появятся автоматически. Можно вернуться к отчёту позже.</p>
        </template>
      </section>

      <section v-if="report.stage === 'documents'" class="mb-5 rounded-lg border border-slate-200 bg-white p-5"
               aria-labelledby="upload-heading">
        <h2 id="upload-heading" class="text-lg font-semibold">Загрузите документы</h2>

        <div class="mt-5 grid gap-4 sm:grid-cols-2">
          <UploadZone title="Итоговый отчёт" description="Один файл с показателями для проверки"
                      :disabled="finalPresent || advancing" @select="addFiles({files: $event, role: 'final_report'})"/>
          <UploadZone title="Подтверждающие документы" description="Первичные и расчётные документы" multiple
                      :disabled="advancing" @select="addFiles({files: $event, role: 'supporting'})"/>
        </div>
        <p class="mt-3 text-xs text-slate-500">PDF, DOCX, XLSX, PNG, JPEG · до 20 МБ на файл.</p>
      </section>

      <section v-if="report.stage !== 'verification'" class="rounded-lg border border-slate-200 bg-white"
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
                      :disabled="deleting.includes(document.id) || advancing" @click="selected = document">Просмотреть
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

      <div v-if="report.stage !== 'verification'" class="mt-6 flex flex-wrap items-start justify-end gap-4">
        <div class="sm:text-right" :class="{'cursor-not-allowed': !canAdvance}"
             :title="!canAdvance ? advanceTooltip : undefined">
          <button
              class="cursor-pointer rounded bg-blue-600 px-4 py-2 text-white hover:bg-blue-700 disabled:pointer-events-none disabled:opacity-50"
              :disabled="!canAdvance" @click="advance">{{
              advancing ? 'Переходим…' : report.stage === 'documents' ? 'К разметке сущностей →' : 'Проверить требования →'
            }}
          </button>
        </div>
      </div>
    </template>

    <MarkdownEditor v-if="selected" :report-id="reportId" :document="selected"
                    :readonly="advancing || report?.stage === 'verification'"
                    :entities="report?.stage !== 'documents'"
                    @close="selected = null" @saved="refresh"/>
  </main>
</template>
