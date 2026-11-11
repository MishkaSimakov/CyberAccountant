<script setup lang="ts">
import {computed, onMounted, onUnmounted, ref} from 'vue'
import {RouterLink} from 'vue-router'
import {FontAwesomeIcon} from '@fortawesome/vue-fontawesome'
import {faFileLines} from '@fortawesome/free-solid-svg-icons/faFileLines'
import {faTrashCan} from '@fortawesome/free-solid-svg-icons/faTrashCan'
import {faSpinner} from '@fortawesome/free-solid-svg-icons/faSpinner'
import {faCheck} from '@fortawesome/free-solid-svg-icons/faCheck'
import {faRotateRight} from '@fortawesome/free-solid-svg-icons/faRotateRight'
import {
  deleteDocument,
  getReport,
  runEntityExtraction,
  uploadDocument,
  type DocumentRole,
  type ReportDetails,
  type ReportDocument
} from '../api'
import UploadZone from '../components/UploadZone.vue'
import MarkdownEditor from '../components/MarkdownEditor.vue'

const props = defineProps<{ reportId: number }>()
const report = ref<ReportDetails | null>(null)
const loading = ref(true)
const loadError = ref('')
const pollError = ref('')
const actionError = ref('')
const advancing = ref(false)
const deleting = ref<number[]>([])
const selected = ref<ReportDocument | null>(null)

interface LocalUpload {
  id: number;
  name: string;
  role: DocumentRole;
  error: string
}

const uploads = ref<LocalUpload[]>([])
let uploadId = 0
let timer: ReturnType<typeof setTimeout> | undefined
let disposed = false
let fetchId = 0
const steps = [
  {stage: 'documents', title: 'Загрузка документов'},
  {stage: 'entities', title: 'Разметка сущностей'},
  {stage: 'verification', title: 'Проверка требований'},
]
const stageIndex = computed(() => steps.findIndex(step => step.stage === report.value?.stage))
const editable = computed(() => report.value?.stage === 'documents' && !advancing.value)
const finalPresent = computed(() => report.value?.documents.some(item => item.role === 'final_report') || uploads.value.some(item => item.role === 'final_report'))
const canAdvance = computed(() => editable.value && !pollError.value && !uploads.value.length && !deleting.value.length
    && report.value?.documents.every(document => document.ocr_status === 'completed')
    && report.value?.available_actions.includes('run_entity_extraction'))
const advanceTooltip = computed(() => [
  ...(report.value?.blockers ?? []),
  ...(uploads.value.length ? ['Дождитесь загрузки файлов и удалите неудачные загрузки.'] : []),
  ...(deleting.value.length ? ['Дождитесь удаления файлов.'] : []),
].join('\n'))
const statusLabels: Record<ReportDocument['ocr_status'], string> = {
  queued: 'Загружен · в очереди',
  running: 'Обрабатывается',
  completed: 'Обработан',
  failed: 'Ошибка'
}

function errorText(cause: unknown) {
  return cause instanceof Error ? cause.message : 'Не удалось выполнить запрос. Попробуйте ещё раз.'
}

function sizeLabel(size: number) {
  return size < 1024 * 1024 ? `${Math.max(1, Math.round(size / 1024))} КБ` : `${(size / 1024 / 1024).toFixed(1)} МБ`
}

async function refresh() {
  const currentFetch = ++fetchId
  try {
    const result = await getReport(props.reportId)
    if (disposed || currentFetch !== fetchId) return
    report.value = result
    pollError.value = loadError.value = ''
  } catch (cause) {
    if (disposed || currentFetch !== fetchId) return
    if (report.value) pollError.value = 'Не удалось обновить статусы. Повторяем соединение…'
    else loadError.value = errorText(cause)
  } finally {
    if (!disposed) loading.value = false
  }
}

async function poll() {
  await refresh()
  if (!disposed) timer = setTimeout(poll, 2000)
}

async function addFiles(files: File[], role: DocumentRole) {
  if (!editable.value) return
  actionError.value = ''
  if (role === 'final_report' && (files.length !== 1 || finalPresent.value)) {
    actionError.value = 'Выберите один итоговый отчёт. Чтобы заменить его, сначала удалите выбранный файл.'
    return
  }
  await Promise.all(files.map(async file => {
    const local: LocalUpload = {id: ++uploadId, name: file.name, role, error: ''}
    uploads.value.push(local)
    const fail = (message: string) => {
      const entry = uploads.value.find(item => item.id === local.id)
      if (entry) entry.error = message
    }
    if (!/\.(pdf|docx|xlsx|png|jpe?g)$/i.test(file.name)) {
      fail('Неподдерживаемый формат. Выберите PDF, DOCX, XLSX, PNG или JPEG.')
      return
    }
    if (!file.size || file.size > 20 * 1024 * 1024) {
      fail(file.size ? 'Размер файла превышает 20 МБ.' : 'Файл пуст. Выберите файл с содержимым.')
      return
    }
    try {
      const document = await uploadDocument(props.reportId, file, role)
      if (disposed) return
      if (report.value && !report.value.documents.some(item => item.id === document.id)) report.value.documents.push(document)
      uploads.value = uploads.value.filter(item => item.id !== local.id)
    } catch (cause) {
      fail(`${errorText(cause)} Обновите список перед повторной загрузкой: файл мог быть принят сервером.`)
    }
    if (!disposed) await refresh()
  }))
}

async function remove(document: ReportDocument) {
  if (!editable.value || deleting.value.includes(document.id)) return
  deleting.value.push(document.id)
  actionError.value = ''
  try {
    await deleteDocument(props.reportId, document.id)
    await refresh()
  } catch (cause) {
    actionError.value = errorText(cause)
  } finally {
    deleting.value = deleting.value.filter(id => id !== document.id)
  }
}

async function advance() {
  if (!canAdvance.value) return
  advancing.value = true
  actionError.value = ''
  try {
    ++fetchId
    const result = await runEntityExtraction(props.reportId)
    if (!disposed) report.value = result
    await refresh()
  } catch (cause) {
    actionError.value = errorText(cause)
    await refresh()
  } finally {
    advancing.value = false
  }
}

onMounted(poll)
onUnmounted(() => {
  disposed = true
  clearTimeout(timer)
})
</script>

<template>
  <main class="mx-auto max-w-6xl p-6">
    <RouterLink :to="{name: 'reports'}" class="mb-5 inline-block text-slate-500 hover:text-blue-700">← Все отчёты
    </RouterLink>
    <p v-if="loading" class="rounded-lg border border-slate-200 bg-white p-8 text-center text-slate-500" role="status">
      Загружаем отчёт…</p>
    <div v-else-if="!report" class="space-y-4 rounded-lg border border-slate-200 bg-white p-8 text-center">
      <p role="alert">{{ loadError }}</p>
      <button class="cursor-pointer rounded border border-slate-300 px-4 py-2 hover:bg-slate-50" @click="refresh">
        Попробовать снова
      </button>
    </div>

    <template v-if="report">
      <h1 class="break-words text-2xl font-semibold sm:text-3xl">{{ report.name }}</h1>
      <div class="mt-3 flex flex-wrap gap-x-6 gap-y-2 text-slate-500">
        <span v-if="report.contract_number">Договор: {{ report.contract_number }}</span>
        <span v-if="report.period_start && report.period_end">Период: {{
            report.period_start.split('-').reverse().join('.')
          }} — {{ report.period_end.split('-').reverse().join('.') }}</span>
        <span v-if="report.target_row">Строка: {{ report.target_row }}</span>
      </div>

      <nav class="my-6 overflow-hidden rounded-lg border border-slate-200 bg-white" aria-label="Этапы проверки отчёта">
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

      <section v-if="report.stage === 'documents'" class="mb-5 rounded-lg border border-slate-200 bg-white p-5"
               aria-labelledby="upload-heading">
        <h2 id="upload-heading" class="text-lg font-semibold">Загрузите документы</h2>

        <div class="mt-5 grid gap-4 sm:grid-cols-2">
          <UploadZone title="Итоговый отчёт" description="Один файл с показателями для проверки"
                      :disabled="finalPresent || advancing" @select="addFiles($event, 'final_report')"/>
          <UploadZone title="Подтверждающие документы" description="Первичные и расчётные документы" multiple
                      :disabled="advancing" @select="addFiles($event, 'supporting')"/>
        </div>
        <p class="mt-3 text-xs text-slate-500">PDF, DOCX, XLSX, PNG, JPEG · до 20 МБ на файл.</p>
      </section>

      <section class="rounded-lg border border-slate-200 bg-white" aria-labelledby="documents-heading">
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
                    @click="uploads = uploads.filter(item => item.id !== upload.id)">Удалить
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
                      @click="remove(document)">
                <FontAwesomeIcon :icon="faTrashCan" class="size-3" aria-hidden="true"/>
                <span v-if="deleting.includes(document.id)" class="ml-2">Удаляем…</span></button>
            </div>
          </li>
        </ul>
      </section>

      <div v-if="report.stage === 'documents'" class="mt-6 flex flex-wrap items-start justify-end gap-4">
        <div class="sm:text-right" :class="{'cursor-not-allowed': !canAdvance}"
             :title="!canAdvance ? advanceTooltip : undefined">
          <button
              class="cursor-pointer rounded bg-blue-600 px-4 py-2 text-white hover:bg-blue-700 disabled:pointer-events-none disabled:opacity-50"
              :disabled="!canAdvance" @click="advance">{{ advancing ? 'Переходим…' : 'К разметке сущностей →' }}
          </button>
        </div>
      </div>
    </template>

    <MarkdownEditor v-if="selected" :report-id="reportId" :document="selected" :readonly="!editable"
                    @close="selected = null" @saved="refresh"/>
  </main>
</template>
