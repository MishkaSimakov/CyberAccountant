<script lang="ts">
import {defineComponent} from 'vue'
import {RouterLink, RouterView} from 'vue-router'

export default defineComponent({
  components: {RouterLink, RouterView},
})
</script>

<template>
  <div class="min-h-screen bg-slate-50 font-sans text-sm text-slate-800">
    <header class="border-b border-slate-200 bg-white">
      <div class="mx-auto max-w-6xl p-6">
        <RouterLink class="text-xl font-semibold" :to="{name: 'reports'}">Cyber Accountant</RouterLink>
      </div>
    </header>

    <RouterView/>
  </div>
</template>
