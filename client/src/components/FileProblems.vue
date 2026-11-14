<script lang="ts">
import {defineComponent, type PropType} from 'vue'
import {FontAwesomeIcon} from '@fortawesome/vue-fontawesome'
import {faChevronDown} from '@fortawesome/free-solid-svg-icons/faChevronDown'
import {faFileLines} from '@fortawesome/free-solid-svg-icons/faFileLines'
import type {ReportDocument, VerifiedEntity, VerifiedRequirement, VerificationResult} from '../api'
import RequirementText from './RequirementText.vue'
import RequirementDetails from './RequirementDetails.vue'
import RequirementStatus from './RequirementStatus.vue'

interface FileProblem {
  title: string
  description?: string
  requirements?: VerifiedRequirement[]
}

export default defineComponent({
  components: {FontAwesomeIcon, RequirementText, RequirementDetails, RequirementStatus},
  props: {
    files: {type: Object as PropType<VerificationResult['files']>, required: true},
    documents: {type: Array as PropType<ReportDocument[]>, required: true},
    requirements: {type: Array as PropType<VerifiedRequirement[]>, required: true},
    entities: {type: Object as PropType<Record<number, VerifiedEntity>>, required: true},
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
        const requirements = this.requirements.filter(requirement =>
            requirement.kind === 'formula' && requirement.tag === 'scope' && requirement.verdict === 'false'
            && reasons.includes(requirement.equation)
            && requirement.ids_related.some(entityId => String(this.entityDocuments[entityId]) === id))
        if (requirements.length) add(id, {title: 'Документ вне рамок проверки', requirements})
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
            <li v-for="problem in file.problems" :key="problem.title">
              <p class="text-sm font-medium text-amber-800">{{ problem.title }}</p>
              <ul v-if="problem.requirements?.length" class="mt-2 divide-y divide-slate-200 rounded border border-slate-200">
                <li v-for="requirement in problem.requirements" :key="requirement.id">
                  <details class="group/requirement">
                    <summary
                        class="flex cursor-pointer list-none items-start gap-3 p-4 hover:bg-slate-50 focus-visible:outline-blue-500 [&::-webkit-details-marker]:hidden">
                      <span class="pt-1 text-sm text-slate-400"><span class="sr-only">Требование </span>{{
                          requirement.id
                        }}</span>
                      <RequirementText :text="requirement.text || 'Название не указано'"
                                       :entity-documents="entityDocuments"
                                       class="min-w-0 flex-1 whitespace-pre-wrap break-words text-sm font-medium leading-7"
                                       :class="requirement.silenced ? 'text-slate-500' : 'text-slate-800'"
                                       @select="$emit('select', $event)"/>
                      <RequirementStatus :requirement="requirement"/>
                      <FontAwesomeIcon :icon="faChevronDown"
                                       class="mt-2 size-3 shrink-0 text-slate-400 transition-transform group-open/requirement:rotate-180 motion-reduce:transition-none"
                                       aria-hidden="true"/>
                    </summary>
                    <RequirementDetails :requirement="requirement" :entities="entities"
                                        :entity-documents="entityDocuments" class="border-t border-slate-100 p-4"
                                        @select="$emit('select', $event)"/>
                  </details>
                </li>
              </ul>
              <RequirementText v-else-if="problem.description" :text="problem.description"
                               :entity-documents="entityDocuments"
                               class="mt-1 block whitespace-pre-wrap break-words text-sm leading-7 text-slate-700"
                               @select="$emit('select', $event)"/>
            </li>
          </ul>
        </details>
      </li>
    </ul>
  </section>
</template>
