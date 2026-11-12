<script lang="ts">
import {defineAsyncComponent, defineComponent} from 'vue'

export default defineComponent({
  components: {
    PdfViewer: defineAsyncComponent(() => import('./PdfViewer.vue')),
  },
  props: {
    src: {type: String, required: true},
  },
})
</script>

<template>
  <section class="flex min-h-0 min-w-0 flex-col overflow-hidden rounded border border-slate-200">
    <div class="flex h-14 shrink-0 items-center justify-between gap-2 border-b border-slate-200 px-3">
      <h3 class="font-medium">Документ</h3>
      <a :href="src" target="_blank" rel="noopener" class="text-blue-700 hover:underline">Открыть PDF ↗</a>
    </div>
    <PdfViewer :key="src" :src="src"/>
  </section>
</template>
