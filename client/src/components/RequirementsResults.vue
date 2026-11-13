<script lang="ts">
import {defineComponent, type PropType} from 'vue'
import {mapActions, mapState} from 'vuex'
import {FontAwesomeIcon} from '@fortawesome/vue-fontawesome'
import {faCheck} from '@fortawesome/free-solid-svg-icons/faCheck'
import {faXmark} from '@fortawesome/free-solid-svg-icons/faXmark'
import {faChevronDown} from '@fortawesome/free-solid-svg-icons/faChevronDown'
import {faBellSlash} from '@fortawesome/free-solid-svg-icons/faBellSlash'
import {faBell} from '@fortawesome/free-solid-svg-icons/faBell'
import {faSpinner} from '@fortawesome/free-solid-svg-icons/faSpinner'
import {faCircleQuestion} from '@fortawesome/free-solid-svg-icons/faCircleQuestion'
import {faTriangleExclamation} from '@fortawesome/free-solid-svg-icons/faTriangleExclamation'
import type {VerifiedRequirement} from '../api'
import RequirementText from './RequirementText.vue'

export default defineComponent({
  components: {FontAwesomeIcon, RequirementText},
  props: {
    requirements: {type: Array as PropType<VerifiedRequirement[]>, required: true},
    entityDocuments: {type: Object as PropType<Record<number, number>>, required: true},
  },
  emits: {select: (id: number) => Number.isInteger(id)},
  data() {
    return {
      filter: 'all' as 'all' | 'failed' | 'passed' | 'unverifiable' | 'error' | 'silenced',
      notes: Object.fromEntries(this.requirements.map(item => [item.id, item.notes])) as Record<number, string>,
      faCheck, faXmark, faChevronDown, faBellSlash, faBell, faSpinner,
    }
  },
  computed: {
    ...mapState('requirements', ['saving', 'saveErrors']),
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
  methods: {
    ...mapActions('requirements', ['update']),
    statusLabel(requirement: VerifiedRequirement): string {
      const label = {
        true: 'Выполнено',
        false: 'Нарушено',
        error: 'Ошибка проверки',
        unverifiable: 'Не удалось проверить',
      }[requirement.verdict]
      return requirement.silenced ? `${label} — предупреждение отключено` : label
    },
    statusClass(requirement: VerifiedRequirement): string {
      if (requirement.silenced) return 'border-slate-200 bg-slate-100 text-slate-400'
      return {
        true: 'border-green-200 bg-green-50 text-green-700',
        false: 'border-red-200 bg-red-50 text-red-700',
        error: 'border-orange-200 bg-orange-50 text-orange-700',
        unverifiable: 'border-amber-200 bg-amber-50 text-amber-700',
      }[requirement.verdict]
    },
    statusIcon(requirement: VerifiedRequirement) {
      return {
        true: faCheck,
        false: faXmark,
        error: faTriangleExclamation,
        unverifiable: faCircleQuestion,
      }[requirement.verdict]
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
                  :text="requirement.kind === 'formula' ? requirement.equation : requirement.text || 'Условие не указано'"
                  :entity-documents="entityDocuments" @select="$emit('select', $event)"
                  class="min-w-0 whitespace-pre-wrap break-words text-sm leading-7"
                  :class="[requirement.kind === 'formula' ? 'font-mono' : '', requirement.silenced ? 'text-slate-500' : 'text-slate-800']"/>
              <span class="inline-flex h-7 w-10 items-center justify-center rounded border"
                    :class="statusClass(requirement)"
                    :title="statusLabel(requirement)">
                <FontAwesomeIcon :icon="statusIcon(requirement)" class="size-3.5" aria-hidden="true"/>
                <span class="sr-only">{{ statusLabel(requirement) }}</span>
              </span>
              <FontAwesomeIcon :icon="faChevronDown"
                               class="size-3 text-slate-400 transition-transform group-open:rotate-180 motion-reduce:transition-none"
                               aria-hidden="true"/>
            </summary>
            <div class="border-t border-slate-100 p-4 sm:pl-18">
              <div class="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-slate-500">
                <span>{{ requirement.kind === 'formula' ? 'Формула' : 'Текстовое условие' }}</span>
                <span>{{
                    statusLabel(requirement)
                  }}</span>
              </div>
              <p v-if="requirement.kind === 'formula' && requirement.text"
                 class="mt-3 whitespace-pre-wrap break-words text-sm leading-7">
                <RequirementText :text="requirement.text" :entity-documents="entityDocuments"
                                 @select="$emit('select', $event)"/>
              </p>
              <p v-if="requirement.error" class="mt-3 whitespace-pre-wrap break-words text-sm text-orange-700">
                {{ requirement.error }}
              </p>
              <div v-if="requirement.ids_related.length" class="mt-4 flex flex-wrap items-center gap-2 text-xs">
                <span class="text-slate-500">Связанные сущности:</span>
                <RequirementText v-for="id in requirement.ids_related" :key="id" :text="`#${id}`" class="font-mono"
                                 :entity-documents="entityDocuments" @select="$emit('select', $event)"/>
              </div>
              <form class="mt-4"
                    @submit.prevent="update({id: requirement.id, changes: {notes: notes[requirement.id]}})">
                <label :for="`requirement-notes-${requirement.id}`" class="block text-sm font-medium">Заметки</label>
                <textarea :id="`requirement-notes-${requirement.id}`" v-model="notes[requirement.id]"
                          rows="3" maxlength="10000" :disabled="saving.includes(requirement.id)"
                          class="mt-2 block w-full resize-y rounded border border-slate-200 bg-slate-50 p-3 text-sm leading-relaxed placeholder:text-slate-400 focus:border-blue-500 focus:bg-white focus:outline-blue-500 disabled:opacity-60"
                          placeholder="Добавьте пояснение или запишите, что нужно проверить…"/>
                <div class="mt-3 flex flex-wrap items-center justify-between gap-3">
                  <div class="flex flex-wrap items-center gap-3">
                    <button type="submit"
                            :disabled="saving.includes(requirement.id) || notes[requirement.id] === requirement.notes"
                            class="inline-flex cursor-pointer items-center gap-2 rounded border border-slate-300 px-3 py-2 text-sm hover:bg-slate-50 focus-visible:outline-blue-500 disabled:cursor-default disabled:opacity-50">
                      <FontAwesomeIcon v-if="saving.includes(requirement.id)" :icon="faSpinner"
                                       class="size-3 animate-spin" aria-hidden="true"/>
                      Сохранить заметку
                    </button>
                    <span class="text-xs text-slate-500" role="status">{{
                        notes[requirement.id] !== requirement.notes ? 'Есть несохранённые изменения' : requirement.notes ? 'Заметка сохранена' : ''
                      }}</span>
                  </div>
                  <button v-if="requirement.verdict !== 'true'" type="button"
                          :disabled="saving.includes(requirement.id)"
                          class="inline-flex cursor-pointer items-center gap-2 rounded px-3 py-2 text-sm text-slate-600 hover:bg-slate-100 focus-visible:outline-blue-500 disabled:cursor-default disabled:opacity-50"
                          :aria-pressed="requirement.silenced"
                          @click="update({id: requirement.id, changes: {silenced: !requirement.silenced}})">
                    <FontAwesomeIcon :icon="requirement.silenced ? faBell : faBellSlash" class="size-3.5"
                                     aria-hidden="true"/>
                    {{ requirement.silenced ? 'Включить предупреждение' : 'Отключить предупреждение' }}
                  </button>
                </div>
              </form>
              <p v-if="saveErrors[requirement.id]" class="mt-3 text-sm text-red-700" role="alert">
                {{ saveErrors[requirement.id] }}</p>
            </div>
          </details>
        </li>
      </ul>
    </div>
  </section>
</template>
