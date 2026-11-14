<script lang="ts">
import {defineComponent, type PropType} from 'vue'
import {FontAwesomeIcon} from '@fortawesome/vue-fontawesome'
import {faChevronDown} from '@fortawesome/free-solid-svg-icons/faChevronDown'
import type {VerifiedEntity, VerifiedRequirement} from '../api'
import RequirementText from './RequirementText.vue'
import RequirementDetails from './RequirementDetails.vue'
import RequirementStatus from './RequirementStatus.vue'

export default defineComponent({
  components: {FontAwesomeIcon, RequirementText, RequirementDetails, RequirementStatus},
  props: {
    requirements: {type: Array as PropType<VerifiedRequirement[]>, required: true},
    entities: {type: Object as PropType<Record<number, VerifiedEntity>>, required: true},
    entityDocuments: {type: Object as PropType<Record<number, number>>, required: true},
  },
  emits: {select: (id: number) => Number.isInteger(id)},
  data() {
    return {
      filter: 'all' as 'all' | 'failed' | 'passed' | 'unverifiable' | 'error' | 'silenced',
      faChevronDown,
    }
  },
  computed: {
    failedCount(): number {
      return this.requirements.filter(requirement => requirement.verdict === 'false' && !requirement.silenced).length
    },
    passedCount(): number {
      return this.requirements.filter(requirement => requirement.verdict === 'true').length
    },
    unverifiableCount(): number {
      return this.requirements.filter(requirement => requirement.verdict === 'unverifiable' && !requirement.silenced).length
    },
    errorCount(): number {
      return this.requirements.filter(requirement => requirement.verdict === 'error' && !requirement.silenced).length
    },
    silencedCount(): number {
      return this.requirements.filter(requirement => requirement.silenced).length
    },
    filteredRequirements(): VerifiedRequirement[] {
      return this.requirements.filter(requirement => {
        if (this.filter === 'failed') return requirement.verdict === 'false' && !requirement.silenced
        if (this.filter === 'passed') return requirement.verdict === 'true'
        if (this.filter === 'unverifiable' || this.filter === 'error') return requirement.verdict === this.filter && !requirement.silenced
        if (this.filter === 'silenced') return requirement.silenced
        return true
      })
    },
    filters() {
      return [
        {value: 'all' as const, label: 'Все', count: this.requirements.length},
        {value: 'failed' as const, label: 'Нарушенные', count: this.failedCount},
        {value: 'passed' as const, label: 'Выполненные', count: this.passedCount},
        {value: 'unverifiable' as const, label: 'Не удалось проверить', count: this.unverifiableCount},
        {value: 'error' as const, label: 'Ошибки', count: this.errorCount},
        {value: 'silenced' as const, label: 'Отключённые', count: this.silencedCount},
      ]
    },
  },

})
</script>

<template>
  <section class="mt-6" aria-labelledby="results-heading">
    <div class="overflow-hidden rounded-lg border border-slate-200 bg-white">
      <div class="flex flex-wrap items-center justify-between gap-4 border-b border-slate-200 p-4">
        <h2 id="results-heading" class="font-semibold">Требования</h2>
        <div class="flex flex-wrap gap-1" role="group" aria-label="Фильтр требований по результату">
          <button v-for="item in filters" :key="item.value" type="button"
                  class="cursor-pointer rounded px-3 py-2 text-sm hover:bg-slate-100 focus-visible:outline-blue-500"
                  :class="filter === item.value ? 'bg-blue-50 text-blue-700' : 'text-slate-500'"
                  :aria-pressed="filter === item.value" @click="filter = item.value">
            {{ item.label }} <span class="ml-1 tabular-nums">{{ item.count }}</span>
          </button>
        </div>
      </div>
      <p v-if="!requirements.length" class="p-8 text-center text-slate-500">Проверка завершена. Требования не
        найдены.</p>
      <p v-else-if="!filteredRequirements.length" class="p-8 text-center text-slate-500">
        Нет требований с выбранным результатом.
      </p>
      <ul v-else class="divide-y divide-slate-200">
        <li v-for="requirement in filteredRequirements" :key="requirement.id">
          <details class="group">
            <summary
                class="grid cursor-pointer list-none grid-cols-[2rem_minmax(0,1fr)_2.5rem_1rem] items-center gap-3 px-4 py-4 transition-colors hover:bg-slate-50 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-blue-500 group-open:bg-slate-50 sm:grid-cols-[2.5rem_minmax(0,1fr)_2.5rem_1rem] sm:gap-4 [&::-webkit-details-marker]:hidden">
              <span class="text-sm tabular-nums text-slate-400"><span
                  class="sr-only">Требование </span>{{ requirement.id }}</span>
              <RequirementText
                  :text="requirement.text || (requirement.kind === 'formula' ? 'Название не указано' : 'Условие не указано')"
                  :entity-documents="entityDocuments" @select="$emit('select', $event)"
                  class="min-w-0 whitespace-pre-wrap break-words text-sm leading-7"
                  :class="requirement.silenced ? 'text-slate-500' : 'text-slate-800'"/>
              <RequirementStatus :requirement="requirement"/>
              <FontAwesomeIcon :icon="faChevronDown"
                               class="size-3 text-slate-400 transition-transform group-open:rotate-180 motion-reduce:transition-none"
                               aria-hidden="true"/>
            </summary>
            <RequirementDetails :requirement="requirement" :entities="entities" :entity-documents="entityDocuments"
                                class="border-t border-slate-100 p-4 sm:pl-18"
                                @select="$emit('select', $event)"/>
          </details>
        </li>
      </ul>
    </div>
  </section>
</template>
