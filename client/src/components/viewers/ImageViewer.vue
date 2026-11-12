<script lang="ts">
import {defineComponent} from 'vue'

export default defineComponent({
  props: {
    src: {type: String, required: true},
    name: {type: String, required: true},
  },
})
</script>

<template>
  <div class="min-h-0 flex-1 overflow-auto overscroll-contain bg-slate-50 p-3">
    <img :src="src" :alt="`Оригинал: ${name}`" class="h-auto w-full"/>
  </div>
</template>
