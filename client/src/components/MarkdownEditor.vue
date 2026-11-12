<script setup lang="ts">
import {computed, onMounted, onUnmounted, ref, watch} from 'vue'
import {FontAwesomeIcon} from '@fortawesome/vue-fontawesome'
import {faXmark} from '@fortawesome/free-solid-svg-icons/faXmark'
import {faFloppyDisk} from '@fortawesome/free-solid-svg-icons/faFloppyDisk'
import {faSpinner} from '@fortawesome/free-solid-svg-icons/faSpinner'
import {getMarkdown, originalUrl, saveMarkdown, type ReportDocument} from '../api'
import DocumentViewer from './viewers/DocumentViewer.vue'
import MarkdownViewer from './viewers/MarkdownViewer.vue'

const props = defineProps<{ reportId: number; document: ReportDocument; readonly: boolean }>()
const emit = defineEmits<{ close: []; saved: [] }>()
const dialog = ref<HTMLDialogElement | null>(null)
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const content = ref('')
const savedContent = ref('')
const revision = ref(0)
const preview = ref(props.readonly)
const dirty = computed(() => content.value !== savedContent.value)
const url = computed(() => originalUrl(props.reportId, props.document.id))
let restorePageScroll: (() => void) | undefined

watch(() => props.readonly, readonly => {
  if (readonly) preview.value = true
})

function close() {
  if (saving.value) return
  if (dirty.value && !window.confirm('Закрыть документ без сохранения правок?')) return
  emit('close')
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const markdown = await getMarkdown(props.reportId, props.document.id)
    content.value = savedContent.value = markdown.content
    revision.value = markdown.revision
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : 'Не удалось загрузить текст.'
  } finally {
    loading.value = false
  }
}

async function save() {
  if (!dirty.value || saving.value) return
  saving.value = true
  error.value = ''
  try {
    const markdown = await saveMarkdown(props.reportId, props.document.id, content.value, revision.value)
    content.value = savedContent.value = markdown.content
    revision.value = markdown.revision
    emit('saved')
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : 'Не удалось сохранить текст.'
  } finally {
    saving.value = false
  }
}

onMounted(() => {
  const rootOverflow = document.documentElement.style.overflow
  const bodyOverflow = document.body.style.overflow
  document.documentElement.style.overflow = 'hidden'
  document.body.style.overflow = 'hidden'
  restorePageScroll = () => {
    document.documentElement.style.overflow = rootOverflow
    document.body.style.overflow = bodyOverflow
  }
  dialog.value?.showModal()
  void load()
})

onUnmounted(() => restorePageScroll?.())
</script>

<template>
  <dialog ref="dialog"
          class="m-0 h-dvh max-h-none w-screen max-w-none overflow-hidden overscroll-contain border-0 bg-white p-3 text-sm text-slate-800 open:flex open:flex-col sm:p-5 sm:pb-3 backdrop:bg-slate-900/50"
          aria-labelledby="editor-heading" @cancel.prevent="close">
    <div class="mb-4 flex shrink-0 items-start justify-between gap-4">
      <div class="min-w-0">
        <h2 id="editor-heading" class="line-clamp-2 break-all text-lg font-semibold">{{ document.name }}</h2>
      </div>
      <div class="flex shrink-0 items-center gap-2">
        <button v-if="!readonly" type="button"
                class="inline-flex h-9 cursor-pointer items-center justify-center gap-2 rounded bg-blue-600 px-3 text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
                :aria-label="saving ? 'Сохраняем…' : 'Сохранить изменения'"
                :title="saving ? 'Сохраняем…' : 'Сохранить изменения'" :aria-busy="saving"
                :disabled="!revision || !dirty || !content.trim() || saving" @click="save">
          <FontAwesomeIcon :icon="saving ? faSpinner : faFloppyDisk" class="size-4"
                           :class="{'animate-spin': saving}" aria-hidden="true"/>
          <span>{{ saving ? 'Сохраняем…' : 'Сохранить' }}</span>
        </button>
        <button type="button"
                class="inline-flex size-9 cursor-pointer items-center justify-center rounded border border-slate-300 hover:bg-slate-50 disabled:opacity-50"
                aria-label="Закрыть" title="Закрыть" :disabled="saving" @click="close">
          <FontAwesomeIcon :icon="faXmark" class="size-4" aria-hidden="true"/>
        </button>
      </div>
    </div>

    <p v-if="error" class="mb-4 max-h-[20dvh] shrink-0 overflow-auto rounded bg-red-50 p-3 text-red-700" role="alert">
      {{ error }}</p>

    <div class="grid min-h-0 flex-1 grid-rows-2 gap-4 sm:grid-cols-2 sm:grid-rows-1">
      <DocumentViewer :src="url" :name="document.name" :content-type="document.content_type"/>
      <section class="flex min-h-0 min-w-0 flex-col overflow-hidden rounded border border-slate-200"
               :aria-busy="loading">
        <div class="flex h-14 shrink-0 items-center justify-between gap-2 border-b border-slate-200 px-3">
          <h3 v-if="preview" class="font-medium">Текст документа</h3>
          <label v-else for="markdown-content" class="font-medium">Markdown</label>
          <div v-if="!readonly" class="flex gap-1" role="group" aria-label="Режим просмотра текста">
            <button type="button" class="cursor-pointer rounded px-2 py-1 hover:bg-slate-100"
                    :class="preview ? 'bg-blue-50 text-blue-700' : 'text-slate-500'"
                    :aria-pressed="preview" @click="preview = true">Просмотр</button>
            <button type="button" class="cursor-pointer rounded px-2 py-1 hover:bg-slate-100"
                    :class="!preview ? 'bg-blue-50 text-blue-700' : 'text-slate-500'"
                    :aria-pressed="!preview" @click="preview = false">Исходный текст</button>
          </div>
        </div>
        <p v-if="loading" class="p-6 text-slate-500" role="status">Загружаем текст…</p>
        <div v-else-if="!revision" class="p-6">
          <button class="cursor-pointer text-blue-700 hover:underline" @click="load">Повторить загрузку текста</button>
        </div>
        <MarkdownViewer v-else-if="preview" :content="content"/>
        <textarea v-else id="markdown-content" v-model="content"
                  class="block min-h-0 w-full flex-1 resize-none overflow-auto overscroll-contain p-4 font-mono text-sm leading-relaxed focus:outline-blue-500"
                  :readonly="readonly || saving" maxlength="1000000" spellcheck="false"/>
      </section>
    </div>

  </dialog>
</template>
