<script lang="ts">
import {defineComponent, type PropType} from 'vue'
import {mapActions, mapGetters, mapState} from 'vuex'
import {FontAwesomeIcon} from '@fortawesome/vue-fontawesome'
import {faXmark} from '@fortawesome/free-solid-svg-icons/faXmark'
import {faFloppyDisk} from '@fortawesome/free-solid-svg-icons/faFloppyDisk'
import {faSpinner} from '@fortawesome/free-solid-svg-icons/faSpinner'
import {pdfUrl, type ReportDocument} from '../api'
import DocumentViewer from './viewers/DocumentViewer.vue'
import MarkdownViewer from './viewers/MarkdownViewer.vue'
import EntityPdfPreview from './viewers/EntityPdfPreview.vue'

export default defineComponent({
  components: {FontAwesomeIcon, DocumentViewer, MarkdownViewer, EntityPdfPreview},
  props: {
    reportId: {type: Number, required: true},
    document: {type: Object as PropType<ReportDocument>, required: true},
    readonly: {type: Boolean, required: true},
    entities: Boolean,
    selectedEntityId: Number,
    pdfEntities: Boolean,
  },
  emits: ['close', 'saved'],
  data() {
    return {
      preview: true,
      restorePageScroll: undefined as (() => void) | undefined,
      faXmark, faFloppyDisk, faSpinner,
    }
  },
  computed: {
    ...mapState('editor', ['loading', 'saving', 'error', 'revision']),
    ...mapGetters('editor', ['dirty']),
    url(): string {
      return pdfUrl(this.reportId, this.document.id)
    },
    content: {
      get(): string {
        return this.$store.state.editor.content
      },
      set(value: string) {
        this.$store.commit('editor/setContent', value)
      },
    },
    previewOnly(): boolean {
      return this.readonly || this.entities
    },
    documentKey(): string {
      return `${this.reportId}/${this.document.id}`
    },
  },
  watch: {
    'document.status'(status: ReportDocument['status']) {
      if (status === 'completed' && !this.dirty && !this.saving) void this.load()
    },
    previewOnly(previewOnly: boolean) {
      if (previewOnly) this.preview = true
    },
    documentKey: {
      immediate: true,
      handler() {
        this.preview = true
        if (!this.pdfEntities) void this.open({reportId: this.reportId, documentId: this.document.id})
      },
    },
  },
  mounted() {
    const rootOverflow = document.documentElement.style.overflow
    const bodyOverflow = document.body.style.overflow
    document.documentElement.style.overflow = 'hidden'
    document.body.style.overflow = 'hidden'
    this.restorePageScroll = () => {
      document.documentElement.style.overflow = rootOverflow
      document.body.style.overflow = bodyOverflow
    }
    const dialog = this.$refs.dialog as HTMLDialogElement
    dialog.showModal()
  },
  unmounted() {
    this.restorePageScroll?.()
    this.$store.commit('editor/reset', null)
  },
  methods: {
    ...mapActions('editor', ['open', 'load']),
    close() {
      if (this.saving) return
      if (this.dirty && !window.confirm('Закрыть документ без сохранения правок?')) return
      this.$emit('close')
    },
    async save() {
      if (this.readonly) return
      if (await this.$store.dispatch('editor/save')) this.$emit('saved')
    },
    saveEntity(updatedContent: string) {
      if (this.readonly || this.saving || !this.revision) return
      this.content = updatedContent
      void this.save()
    },
  },
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

    <p v-if="error && !pdfEntities" class="mb-4 max-h-[20dvh] shrink-0 overflow-auto rounded bg-red-50 p-3 text-red-700"
       role="alert">
      {{ error }}</p>

    <EntityPdfPreview v-if="pdfEntities" :report-id="reportId" :document-id="document.id"
                      :selected-entity-id="selectedEntityId"/>
    <div v-else class="grid min-h-0 flex-1 grid-rows-2 gap-4 sm:grid-cols-2 sm:grid-rows-1">
      <DocumentViewer :src="url"/>
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
                        :selected-entity-id="selectedEntityId"
                        @update:content="saveEntity"/>
        <textarea v-else id="markdown-content" v-model="content"
                  class="block min-h-0 w-full flex-1 resize-none overflow-auto overscroll-contain p-4 font-mono text-sm leading-relaxed focus:outline-blue-500"
                  :readonly="readonly || saving" maxlength="1000000" spellcheck="false"/>
      </section>
    </div>

  </dialog>
</template>
