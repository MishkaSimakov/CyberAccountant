<script setup lang="ts">
import {onMounted, onUnmounted, ref} from 'vue'
import ReportPage from './pages/ReportPage.vue'
import ReportsPage from './pages/ReportsPage.vue'

const reportId = ref<number | null>(null)

function readRoute() {
  const match = window.location.hash.match(/^#\/reports\/(\d+)$/)
  reportId.value = match ? Number(match[1]) : null
}

onMounted(() => {
  readRoute()
  window.addEventListener('hashchange', readRoute)
})
onUnmounted(() => window.removeEventListener('hashchange', readRoute))
</script>

<template>
  <div class="min-h-screen bg-slate-50 font-sans text-sm text-slate-800">
    <header class="border-b border-slate-200 bg-white">
      <div class="mx-auto max-w-6xl p-6">
        <a class="text-xl font-semibold" href="#/">Cyber Accountant</a>
      </div>
    </header>

    <ReportPage v-if="reportId !== null" :key="reportId" :report-id="reportId"/>
    <ReportsPage v-else/>
  </div>
</template>
