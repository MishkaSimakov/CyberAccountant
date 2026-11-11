<script setup lang="ts">
import {defineAsyncComponent} from 'vue'
import ImageViewer from './ImageViewer.vue'

defineProps<{ src: string; name: string; contentType: string }>()

const PdfViewer = defineAsyncComponent(() => import('./PdfViewer.vue'))
</script>

<template>
  <section class="flex min-h-0 min-w-0 flex-col overflow-hidden rounded border border-slate-200">
    <div class="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-slate-200 p-3">
      <h3 class="font-medium">Оригинал</h3>
      <a :href="src" target="_blank" rel="noopener" class="text-blue-700 hover:underline">Открыть файл ↗</a>
    </div>
    <PdfViewer v-if="contentType === 'application/pdf'" :key="src" :src="src"/>
    <ImageViewer v-else-if="contentType.startsWith('image/')" :src="src" :name="name"/>
    <div v-else
         class="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 overflow-auto overscroll-contain p-6 text-center text-slate-500">
      <p>Для этого типа документа предпросмотр в браузере недоступен.</p>
      <a :href="src" download class="text-blue-700 hover:underline">Скачать оригинал</a>
    </div>
  </section>
</template>
