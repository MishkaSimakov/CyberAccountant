<script lang="ts">
import {defineAsyncComponent, defineComponent} from 'vue'
import ImageViewer from './ImageViewer.vue'

export default defineComponent({
  components: {
    ImageViewer,
    PdfViewer: defineAsyncComponent(() => import('./PdfViewer.vue')),
    DocxViewer: defineAsyncComponent(() => import('./DocxViewer.vue')),
    XlsxViewer: defineAsyncComponent(() => import('./XlsxViewer.vue')),
  },
  props: {
    src: {type: String, required: true},
    name: {type: String, required: true},
    contentType: {type: String, required: true},
  },
  computed: {
    fileType(): string {
      const contentType = this.contentType.split(';')[0]!.trim().toLowerCase()
      if (contentType === 'application/vnd.openxmlformats-officedocument.wordprocessingml.document') return 'docx'
      if (contentType === 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet') return 'xlsx'
      return /\.(docx|xlsx)$/i.exec(this.name)?.[1]?.toLowerCase() ?? contentType
    },
  },
})
</script>

<template>
  <section class="flex min-h-0 min-w-0 flex-col overflow-hidden rounded border border-slate-200">
    <div class="flex h-14 shrink-0 items-center justify-between gap-2 border-b border-slate-200 px-3">
      <h3 class="font-medium">Оригинал</h3>
      <a :href="src" target="_blank" rel="noopener" class="text-blue-700 hover:underline">Открыть файл ↗</a>
    </div>
    <PdfViewer v-if="fileType === 'application/pdf'" :key="src" :src="src"/>
    <DocxViewer v-else-if="fileType === 'docx'" :key="src" :src="src"/>
    <XlsxViewer v-else-if="fileType === 'xlsx'" :key="src" :src="src"/>
    <ImageViewer v-else-if="contentType.startsWith('image/')" :src="src" :name="name"/>
    <div v-else
         class="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 overflow-auto overscroll-contain p-6 text-center text-slate-500">
      <p>Для этого типа документа предпросмотр в браузере недоступен.</p>
      <a :href="src" download class="text-blue-700 hover:underline">Скачать оригинал</a>
    </div>
  </section>
</template>
