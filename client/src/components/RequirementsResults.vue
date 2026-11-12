<script lang="ts">
import {defineComponent, type PropType} from 'vue'
import type {Requirement} from '../api'

export default defineComponent({
  props: {
    requirements: {type: Array as PropType<Requirement[]>, required: true},
  },
  data() {
    return {
      filter: 'all' as 'all' | 'failed' | 'passed',
    }
  },
  computed: {
    failedCount(): number {
      return this.requirements.filter(requirement => requirement.verdict === false).length
    },
    passedCount(): number {
      return this.requirements.length - this.failedCount
    },
    filteredRequirements(): Requirement[] {
      return this.requirements.filter(requirement => this.filter === 'all'
          || requirement.verdict === (this.filter === 'passed'))
    },
    filters() {
      return [
        {value: 'all' as const, label: 'Все', count: this.requirements.length},
        {value: 'failed' as const, label: 'Нарушенные', count: this.failedCount},
        {value: 'passed' as const, label: 'Выполненные', count: this.passedCount},
      ]
    },
  },
})
</script>

<template>
  <section class="mt-6 space-y-5" aria-labelledby="results-heading">
    <div class="overflow-hidden rounded-lg border border-slate-200 bg-white">
      <div class="flex flex-wrap items-center justify-between gap-4 border-b border-slate-200 p-4">
        <h2 id="results-heading" class="font-semibold">Требования</h2>
        <div class="flex flex-wrap gap-1" role="group" aria-label="Фильтр требований по результату">
          <button v-for="item in filters" :key="item.value" type="button"
                  class="cursor-pointer rounded px-3 py-2 hover:bg-slate-100"
                  :class="filter === item.value ? 'bg-blue-50 text-blue-700' : 'text-slate-500'"
                  :aria-pressed="filter === item.value" @click="filter = item.value">
            {{ item.label }} <span class="ml-1">{{ item.count }}</span>
          </button>
        </div>
      </div>
      <p v-if="!requirements.length" class="p-8 text-center text-slate-500">Проверка завершена. Требования не
        найдены.</p>
      <p v-else-if="!filteredRequirements.length" class="p-8 text-center text-slate-500">
        {{ filter === 'failed' ? 'Нарушенных требований нет.' : 'Выполненных требований нет.' }}
      </p>
      <ul v-else class="mt-4 divide-y divide-slate-200">
        <li v-for="requirement in filteredRequirements" :key="requirement.id" class="p-4">
          <div class="flex flex-wrap items-center justify-between gap-3">
            <p class="text-xs text-slate-500">Требование {{ requirement.id }} ·
              {{ requirement.equation ? 'Формула' : 'Текстовое условие' }}</p>
            <span class="rounded px-3 py-1 text-xs font-medium"
                  :class="requirement.verdict ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'">
              {{ requirement.verdict ? 'Выполнено' : 'Нарушено' }}
            </span>
          </div>
          <pre v-if="requirement.equation"
               class="mt-3 whitespace-pre-wrap break-words rounded bg-slate-50 p-3 font-mono text-sm">{{
              requirement.equation
            }}</pre>
          <p v-if="requirement.evidence" class="mt-3 whitespace-pre-wrap break-words leading-relaxed">
            {{ requirement.evidence }}</p>
          <p v-if="requirement.ids_related.length" class="mt-3 break-words text-xs text-slate-500">Связанные сущности:
            {{ requirement.ids_related.map(id => `#${id}`).join(', ') }}</p>
        </li>
      </ul>
    </div>
  </section>
</template>
