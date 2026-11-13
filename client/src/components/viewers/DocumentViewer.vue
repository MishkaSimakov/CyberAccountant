<script lang="ts">
import {defineAsyncComponent, defineComponent, type PropType} from 'vue'
import type {EntityBox} from '../../api'

export default defineComponent({
  components: {
    PdfViewer: defineAsyncComponent(() => import('./PdfViewer.vue')),
  },
  props: {
    src: {type: String, required: true},
    highlights: {type: Array as PropType<EntityBox[]>, default: () => []},
  },
})
</script>

<template>
  <section class="flex min-h-0 min-w-0 flex-col overflow-hidden rounded border border-slate-200">
    <PdfViewer :key="src" :src="src" :highlights="highlights"/>
  </section>
</template>
