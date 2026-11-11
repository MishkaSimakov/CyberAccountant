<script setup lang="ts">
import {computed, onMounted, ref} from 'vue'
import {getMarkdown, originalUrl, saveMarkdown, type ReportDocument} from '../api'

const props = defineProps<{ reportId: number; document: ReportDocument; readonly: boolean }>()
const emit = defineEmits<{ close: []; saved: [] }>()
const dialog = ref<HTMLDialogElement | null>(null)
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const content = ref('')
const savedContent = ref('')
const revision = ref(0)
const dirty = computed(() => content.value !== savedContent.value)
const canPreview = computed(() => props.document.content_type === 'application/pdf' || props.document.content_type.startsWith('image/'))
const url = computed(() => originalUrl(props.reportId, props.document.id))

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
  dialog.value?.showModal()
  void load()
})
</script>

<template>
  <dialog ref="dialog"
          class="m-auto max-h-[95dvh] w-[95vw] max-w-7xl overflow-y-auto rounded-lg bg-white p-5 text-sm text-slate-800 backdrop:bg-slate-900/50"
          aria-labelledby="editor-heading" @cancel.prevent="close">
    <div class="mb-4 flex items-start justify-between gap-4">
      <div class="min-w-0">
        <h2 id="editor-heading" class="break-all text-lg font-semibold">{{ document.name }}</h2>
      </div>
      <button type="button"
              class="cursor-pointer rounded border border-slate-300 px-3 py-2 hover:bg-slate-50 disabled:opacity-50"
              :disabled="saving" @click="close">Закрыть
      </button>
    </div>

    <div class="grid gap-4 lg:grid-cols-2">
      <section class="min-w-0 rounded border border-slate-200">
        <div class="flex items-center justify-between gap-2 border-b border-slate-200 p-3">
          <h3 class="font-medium">Оригинал</h3>
          <a :href="url" target="_blank" rel="noopener" class="text-blue-700 hover:underline">Открыть файл ↗</a>
        </div>
        <iframe v-if="canPreview && document.content_type === 'application/pdf'" :src="url"
                title="Оригинал документа PDF" class="h-[55dvh] w-full"/>
        <div v-else-if="canPreview" class="h-[55dvh] overflow-auto bg-slate-50 p-3">
          <img :src="url" :alt="`Оригинал: ${document.name}`" class="h-auto w-full"/>
        </div>
        <div v-else class="flex h-[55dvh] flex-col items-center justify-center gap-3 p-6 text-center text-slate-500">
          <p>Для DOCX и XLSX предпросмотр в браузере недоступен.</p>
          <a :href="url" download class="text-blue-700 hover:underline">Скачать оригинал</a>
        </div>
      </section>
      <section class="min-w-0 rounded border border-slate-200" :aria-busy="loading">
        <div class="border-b border-slate-200 p-3"><label for="markdown-content" class="font-medium">Markdown</label>
        </div>
        <p v-if="loading" class="p-6 text-slate-500" role="status">Загружаем текст…</p>
        <div v-else-if="!revision" class="p-6">
          <button class="cursor-pointer text-blue-700 hover:underline" @click="load">Повторить загрузку текста</button>
        </div>
        <textarea v-else id="markdown-content" v-model="content"
                  class="block h-[55dvh] w-full resize-none p-4 font-mono text-sm leading-relaxed focus:outline-blue-500"
                  :readonly="readonly || saving" maxlength="1000000" spellcheck="false"/>
      </section>
    </div>

    <p v-if="error" class="mt-4 rounded bg-red-50 p-3 text-red-700" role="alert">{{ error }}</p>
    <div class="mt-4 flex flex-wrap items-center justify-between gap-3">
      <button v-if="!readonly"
              class="cursor-pointer rounded bg-blue-600 px-4 py-2 text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
              :disabled="!revision || !dirty || !content.trim() || saving" @click="save">
        {{ saving ? 'Сохраняем…' : 'Сохранить изменения' }}
      </button>
    </div>
  </dialog>
</template>
