<script lang="ts">
import {defineComponent} from 'vue'
import {getEntityGeometry, pdfUrl, type EntityGeometry} from '../../api'
import DocumentViewer from './DocumentViewer.vue'

export default defineComponent({
  components: {DocumentViewer},
  props: {
    reportId: {type: Number, required: true},
    documentId: {type: Number, required: true},
    selectedEntityId: Number,
  },
  data() {
    return {geometry: null as EntityGeometry | null, loading: false, error: '', requestId: 0}
  },
  computed: {
    src(): string { return pdfUrl(this.reportId, this.documentId) },
    selection(): string { return `${this.reportId}/${this.documentId}/${this.selectedEntityId}` },
  },
  watch: {selection: {immediate: true, handler() { void this.load() }}},
  unmounted() { this.requestId++ },
  methods: {
    async load() {
      const request = ++this.requestId
      this.geometry = null
      this.error = ''
      this.loading = false
      if (this.selectedEntityId === undefined) return
      this.loading = true
      try {
        const result = await getEntityGeometry(this.reportId, this.documentId, this.selectedEntityId)
        if (this.requestId === request) this.geometry = result
      } catch (cause) {
        if (this.requestId === request) this.error = cause instanceof Error ? cause.message : 'Не удалось загрузить подсветку.'
      } finally {
        if (this.requestId === request) this.loading = false
      }
    },
  },
})
</script>

<template>
  <section class="flex min-h-0 flex-1 flex-col gap-3" aria-label="Сущность в исходном документе">
    <p v-if="loading" role="status" class="shrink-0 text-sm text-slate-500">Находим фрагмент в документе…</p>
    <p v-if="geometry?.message || error" role="status" class="shrink-0 text-sm text-amber-900">{{ geometry?.message || error }}</p>
    <DocumentViewer class="flex-1" :src="src" :highlights="geometry?.boxes ?? []"/>
  </section>
</template>
