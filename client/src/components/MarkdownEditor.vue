<script setup lang="ts">
import {computed, onMounted, onUnmounted, readonly as readOnly, ref, toRefs, watch} from 'vue'
import {FontAwesomeIcon} from '@fortawesome/vue-fontawesome'
import {faXmark} from '@fortawesome/free-solid-svg-icons/faXmark'
import {faFloppyDisk} from '@fortawesome/free-solid-svg-icons/faFloppyDisk'
import {faSpinner} from '@fortawesome/free-solid-svg-icons/faSpinner'
import type {ReportDocument} from '../api'
import {useStore} from '../store'
import DocumentViewer from './viewers/DocumentViewer.vue'
import MarkdownViewer from './viewers/MarkdownViewer.vue'

const props = defineProps<{ reportId: number; document: ReportDocument; readonly: boolean; entities?: boolean }>()
const emit = defineEmits<{ close: []; saved: [] }>()
const dialog = ref<HTMLDialogElement | null>(null)
const store = useStore()
const {loading, saving, error, revision} = toRefs(readOnly(store.state.editor))
const content = computed({
  get: () => store.state.editor.content,
  set: value => store.commit('editor/setContent', value),
})
const dirty = computed<boolean>(() => store.getters['editor/dirty'])
const url = computed<string>(() => store.getters['editor/originalUrl'])
const preview = ref(props.readonly || props.entities)
const load = () => store.dispatch('editor/load')
let restorePageScroll: (() => void) | undefined

watch(() => props.readonly || props.entities, previewOnly => {
  if (previewOnly) preview.value = true
})

function close() {
  if (saving.value) return
  if (dirty.value && !window.confirm('Закрыть документ без сохранения правок?')) return
  emit('close')
}

async function save() {
  if (props.readonly) return
  if (await store.dispatch('editor/save')) emit('saved')
}

function saveEntity(updatedContent: string) {
  if (props.readonly || saving.value || !revision.value) return
  content.value = updatedContent
  void save()
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
})

watch(() => [props.reportId, props.document.id] as const, ([reportId, documentId]) => {
  preview.value = props.readonly || props.entities
  void store.dispatch('editor/open', {reportId, documentId})
}, {immediate: true})

onUnmounted(() => {
  restorePageScroll?.()
  store.commit('editor/reset', null)
})
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
          <div v-if="!readonly && !entities" class="flex gap-1" role="group" aria-label="Режим просмотра текста">
            <button type="button" class="cursor-pointer rounded px-2 py-1 hover:bg-slate-100"
                    :class="preview ? 'bg-blue-50 text-blue-700' : 'text-slate-500'"
                    :aria-pressed="preview" @click="preview = true">Просмотр
            </button>
            <button type="button" class="cursor-pointer rounded px-2 py-1 hover:bg-slate-100"
                    :class="!preview ? 'bg-blue-50 text-blue-700' : 'text-slate-500'"
                    :aria-pressed="!preview" @click="preview = false">Исходный текст
            </button>
          </div>
        </div>
        <p v-if="loading" class="p-6 text-slate-500" role="status">Загружаем текст…</p>
        <div v-else-if="!revision" class="p-6">
          <button class="cursor-pointer text-blue-700 hover:underline" @click="load">Повторить загрузку текста</button>
        </div>
        <MarkdownViewer v-else-if="preview" :content="content" :entities="entities" :readonly="readonly || saving"
                        @update:content="saveEntity"/>
        <textarea v-else id="markdown-content" v-model="content"
                  class="block min-h-0 w-full flex-1 resize-none overflow-auto overscroll-contain p-4 font-mono text-sm leading-relaxed focus:outline-blue-500"
                  :readonly="readonly || saving" maxlength="1000000" spellcheck="false"/>
      </section>
    </div>

  </dialog>
</template>
