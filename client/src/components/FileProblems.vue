<script lang="ts">
import {defineComponent, type PropType} from 'vue'
import {FontAwesomeIcon} from '@fortawesome/vue-fontawesome'
import {faChevronDown} from '@fortawesome/free-solid-svg-icons/faChevronDown'
import {faFileLines} from '@fortawesome/free-solid-svg-icons/faFileLines'
import type {ReportDocument, VerificationResult} from '../api'
import RequirementText from './RequirementText.vue'

interface FileProblem {
  title: string
  description: string
}

export default defineComponent({
  components: {FontAwesomeIcon, RequirementText},
  props: {
    files: {type: Object as PropType<VerificationResult['files']>, required: true},
    documents: {type: Array as PropType<ReportDocument[]>, required: true},
    entityDocuments: {type: Object as PropType<Record<number, number>>, required: true},
  },
  emits: {select: (id: number) => Number.isInteger(id)},
  data() {
    return {faChevronDown, faFileLines}
  },
  computed: {
    fileProblems() {
      const grouped = new Map<string, FileProblem[]>()
      const add = (id: string, problem: FileProblem) => {
        const problems = grouped.get(id) ?? []
        problems.push(problem)
        grouped.set(id, problems)
      }
      for (const [id, reasons] of Object.entries(this.files.out_of_scope)) {
        for (const reason of reasons) {
          add(id, {title: 'Документ вне рамок проверки', description: reason})
        }
      }
      for (const [id, original] of Object.entries(this.files.duplicates)) {
        add(id, {title: 'Дубликат', description: `Совпадает с файлом «${this.fileName(original)}».`})
      }
      for (const id of this.files.dangling) {
        add(id, {
          title: 'Документ не используется в формулах проверки',
          description: 'Ни одна формула проверки не ссылается на сущности этого документа.',
        })
      }
      return Array.from(grouped, ([id, problems]) => ({id, name: this.fileName(id), problems}))
    },
  },
  methods: {
    fileName(id: string): string {
      return this.documents.find(document => String(document.id) === id)?.name ?? `Файл ${id}`
    },
  },
})
</script>

<template>
  <section class="mt-6 overflow-hidden rounded-lg border border-slate-200 bg-white"
           aria-labelledby="file-problems-heading">
    <h2 id="file-problems-heading" class="border-b border-slate-200 p-4 font-semibold">
      Проблемы файлов <span class="ml-2 font-normal tabular-nums text-slate-500">{{ fileProblems.length }}</span>
    </h2>
    <p v-if="!fileProblems.length" class="p-8 text-center text-slate-500">Проблем с файлами не найдено.</p>
    <ul v-else class="divide-y divide-slate-200">
      <li v-for="file in fileProblems" :key="file.id">
        <details class="group">
          <summary
              class="flex cursor-pointer list-none items-center gap-3 p-4 hover:bg-slate-50 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-blue-500 group-open:bg-slate-50 [&::-webkit-details-marker]:hidden">
            <FontAwesomeIcon :icon="faFileLines" class="size-4 shrink-0 text-slate-400" aria-hidden="true"/>
            <span class="min-w-0 flex-1 break-words text-sm font-medium">{{ file.name }}</span>
            <span class="shrink-0 rounded bg-amber-50 px-2 py-1 text-xs tabular-nums text-amber-800">
              Проблем: {{ file.problems.length }}
            </span>
            <FontAwesomeIcon :icon="faChevronDown"
                             class="size-3 shrink-0 text-slate-400 transition-transform group-open:rotate-180 motion-reduce:transition-none"
                             aria-hidden="true"/>
          </summary>
          <ul class="space-y-4 border-t border-slate-100 p-4 sm:pl-11">
            <li v-for="(problem, index) in file.problems" :key="index">
              <p class="text-sm font-medium text-amber-800">{{ problem.title }}</p>
              <RequirementText :text="problem.description" :entity-documents="entityDocuments"
                               class="mt-1 block whitespace-pre-wrap break-words text-sm leading-7 text-slate-700"
                               @select="$emit('select', $event)"/>
            </li>
          </ul>
        </details>
      </li>
    </ul>
  </section>
</template>
