<script setup lang="ts">
defineProps<{ src: string; name: string }>()
</script>

<template>
  <div class="min-h-0 flex-1 overflow-auto overscroll-contain bg-slate-50 p-3">
    <img :src="src" :alt="`Оригинал: ${name}`" class="h-auto w-full"/>
  </div>
</template>
