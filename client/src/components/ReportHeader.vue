<script lang="ts">
import {defineComponent, type PropType} from 'vue'
import {RouterLink} from 'vue-router'
import type {Report} from '../api'

export default defineComponent({
  components: {RouterLink},
  props: {
    report: {type: Object as PropType<Report | null>, default: null},
  },
})
</script>

<template>
  <header>
    <RouterLink :to="{name: 'reports'}" class="mb-5 inline-block text-slate-500 hover:text-blue-700">← Все отчёты
    </RouterLink>
    <template v-if="report">
      <h1 class="break-words text-2xl font-semibold sm:text-3xl">{{ report.name }}</h1>
      <div class="mt-3 flex flex-wrap gap-x-6 gap-y-2 text-slate-500">
        <span v-if="report.contract_number">Договор: {{ report.contract_number }}</span>
        <span v-if="report.period_start && report.period_end">Период: {{
            report.period_start.split('-').reverse().join('.')
          }} — {{ report.period_end.split('-').reverse().join('.') }}</span>
        <span v-if="report.target_row">Строка: {{ report.target_row }}</span>
      </div>
    </template>
  </header>
</template>
