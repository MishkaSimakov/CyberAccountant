<script lang="ts">
import {defineComponent} from 'vue'
import {RouterLink} from 'vue-router'
import {mapActions, mapState} from 'vuex'
import {FontAwesomeIcon} from '@fortawesome/vue-fontawesome'
import {faSpinner} from '@fortawesome/free-solid-svg-icons/faSpinner'
import {faChevronDown} from '@fortawesome/free-solid-svg-icons/faChevronDown'
import {faXmark} from '@fortawesome/free-solid-svg-icons/faXmark'
import {editRequirements, reportArchiveUrl} from '../api'
import {formatAmount} from '../formatAmount'
import type {
  FieldVerdict,
  Report,
  ReportDetails,
  ReportDocument,
  VerifiedEntity,
  VerifiedRequirement,
  VerificationResult
} from '../api'
import ReportHeader from '../components/ReportHeader.vue'
import RequirementsResults from '../components/RequirementsResults.vue'
import RequirementDetails from '../components/RequirementDetails.vue'
import RequirementStatus from '../components/RequirementStatus.vue'
import ReportWorkspace from '../components/ReportWorkspace.vue'
import EntityPdfPreview from '../components/viewers/EntityPdfPreview.vue'
import RequirementText from '../components/RequirementText.vue'
import FileProblems from '../components/FileProblems.vue'

export default defineComponent({
  components: {
    RouterLink,
    FontAwesomeIcon,
    ReportHeader,
    RequirementsResults,
    RequirementDetails,
    RequirementStatus,
    ReportWorkspace,
    EntityPdfPreview,
    RequirementText,
    FileProblems
  },
  props: {reportId: {type: Number, required: true}},
  data() {
    return {
      faSpinner, faChevronDown, faXmark, expandedFields: [] as number[],
      selectedEntityId: undefined as number | undefined, editing: false, editError: '',
      selectionVersion: 0,
    }
  },
  computed: {
    ...mapState('requirements', ['loading', 'error', 'entityDocuments']),
    report(): ReportDetails | null {
      return this.$store.state.requirements.report
    },
    result(): VerificationResult | null {
      return this.$store.state.requirements.result
    },
    requirements(): VerifiedRequirement[] | null {
      const items = this.$store.state.requirements.items
      if (items === null || this.result === null) return null
      const definitions = new Map(items.map(item => [item.id, item]))
      return this.result.requirement_results.map(outcome => {
        const definition = definitions.get(outcome.requirement_id)
        if (!definition) throw new Error('Не найдено определение требования.')
        return {...definition, ...outcome}
      })
    },
    entities(): Record<number, VerifiedEntity> {
      return Object.fromEntries((this.result?.entities ?? []).map(entity => [entity.num, entity]))
    },
    fieldRequirements(): Record<number, VerifiedRequirement[]> {
      const related: Record<number, VerifiedRequirement[]> = {}
      for (const requirement of this.requirements ?? []) {
        for (const id of requirement.ids_related) {
          (related[id] ??= []).push(requirement)
        }
      }
      return related
    },
    selectedDocument(): ReportDocument | undefined {
      if (this.selectedEntityId === undefined) return
      const documentId = this.$store.state.requirements.entityDocuments[this.selectedEntityId]
      return this.report?.documents.find(document => document.id === documentId)
    },
  },
  watch: {
    reportId: {
      immediate: true,
      handler(id: number) {
        this.selectedEntityId = undefined
        this.expandedFields = []
        void this.start(id)
      },
    },
    report(report: Report | null) {
      if (report && (report.stage !== 'verification' || report.processing_status !== 'completed')) {
        void this.$router.replace({name: 'report', params: {reportId: this.reportId}})
      }
    },
  },
  unmounted() {
    void this.stop()
  },
  methods: {
    selectEntity(id: number) {
      this.selectedEntityId = id
      this.selectionVersion++
    },
    reportArchiveUrl,
    ...mapActions('requirements', ['start', 'stop', 'load']),
    toggleField(id: number) {
      this.expandedFields = this.expandedFields.includes(id)
          ? this.expandedFields.filter(value => value !== id)
          : [...this.expandedFields, id]
    },
    async edit() {
      if (this.editing || this.$store.state.requirements.saving.length) return
      this.editing = true
      this.editError = ''
      try {
        const report = await editRequirements(this.reportId)
        this.$store.commit('reports/upsert', report)
        await this.$router.push({name: 'requirements-edit', params: {reportId: this.reportId}})
      } catch (cause) {
        this.editError = cause instanceof Error ? cause.message : 'Не удалось открыть требования.'
      } finally {
        this.editing = false
      }
    },
    fieldValue(field: FieldVerdict, value: string | null, difference = false): string {
      if (value === null) return '—'
      if (field.type === 'date' && !difference) return new Date((Number(value) - 719163) * 86400000)
          .toLocaleDateString('ru-RU', {timeZone: 'UTC'})
      if (field.type === 'amount') return formatAmount(value, field.unit)
      return value
    },
  },
})
</script>

<template>
  <ReportWorkspace :preview-open="!!selectedDocument" @close="selectedEntityId = undefined">
    <ReportHeader :report="report"/>
    <div v-if="loading"
         class="mt-6 flex items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white p-8 text-center text-slate-500"
         role="status">
      <FontAwesomeIcon :icon="faSpinner" class="size-4 shrink-0 animate-spin" aria-hidden="true"/>
      Загружаем результаты проверки…
    </div>
    <div v-else-if="error" class="mt-6 rounded-lg border border-slate-200 bg-white p-8 text-center">
      <p role="alert">{{ error }}</p>
      <button class="mt-4 cursor-pointer text-blue-700 hover:underline" @click="load">Повторить загрузку</button>
    </div>
    <template v-else-if="result && requirements !== null">
      <div class="mt-6 flex flex-wrap justify-end gap-3">
        <a :href="reportArchiveUrl(reportId)" :download="`report-${reportId}.zip`"
           class="rounded border border-slate-300 bg-white px-4 py-2 hover:bg-slate-50 focus-visible:outline-blue-500">
          Скачать все файлы (ZIP)
        </a>
        <RouterLink :to="{name: 'report', params: {reportId}, query: {view: 'entities'}}"
                    class="rounded border border-slate-300 bg-white px-4 py-2 hover:bg-slate-50 focus-visible:outline-blue-500">
          Просмотреть файлы и сущности
        </RouterLink>
        <button type="button" :disabled="editing || $store.state.requirements.saving.length > 0"
                class="cursor-pointer rounded border border-slate-300 bg-white px-4 py-2 hover:bg-slate-50 disabled:opacity-50"
                @click="edit">{{ editing ? 'Открываем…' : 'Редактировать требования' }}
        </button>
      </div>
      <p v-if="editError" class="mt-3 text-red-700" role="alert">{{ editError }}</p>
      <section class="mt-6 overflow-hidden rounded-lg border border-slate-200 bg-white"
               aria-labelledby="fields-heading">
        <h2 id="fields-heading" class="border-b border-slate-200 p-4 font-semibold">Проверка значений отчёта</h2>
        <div class="overflow-x-auto">
          <table class="w-full text-left text-sm">
            <thead class="bg-slate-50 text-slate-500">
            <tr>
              <th class="p-4">Показатель КФЗ</th>
              <th class="p-4">Значение в КФЗ</th>
              <th class="p-4">Ожидаемо по валидным документам</th>
              <th class="p-4">Расхождение</th>
              <th class="p-4">Статус</th>
            </tr>
            </thead>
            <tbody class="divide-y divide-slate-100">
            <template v-for="field in result.card.cells" :key="field.num">
              <tr class="cursor-pointer hover:bg-slate-50" @click="toggleField(field.num)">
                <td class="p-4">
                  <div class="flex items-center gap-3">
                    <button type="button"
                            class="cursor-pointer rounded p-1 text-slate-500 focus-visible:outline-blue-500"
                            :aria-expanded="expandedFields.includes(field.num)"
                            :aria-controls="`field-requirements-${field.num}`"
                            :aria-label="`Требования для поля #${field.num}`" @click.stop="toggleField(field.num)">
                      <FontAwesomeIcon :icon="faChevronDown"
                                       class="size-3 transition-transform motion-reduce:transition-none"
                                       :class="{'rotate-180': expandedFields.includes(field.num)}" aria-hidden="true"/>
                    </button>
                    <RequirementText :text="`#${field.num}`" :entity-documents="entityDocuments"
                                     @select="selectEntity"/>
                  </div>
                </td>
                <td class="whitespace-nowrap p-4 tabular-nums">{{ fieldValue(field, field.stated) }}</td>
                <td class="whitespace-nowrap p-4 tabular-nums">{{ fieldValue(field, field.expected) }}</td>
                <td class="whitespace-nowrap p-4 tabular-nums">
                  {{ fieldValue(field, field.diff ?? (field.status === 'ok' ? '0' : null), true) }}
                </td>
                <td class="p-4"
                    :class="field.status === 'ok' ? 'text-green-700' : field.status === 'mismatch' ? 'text-red-700' : 'text-amber-700'">
                  {{ {ok: 'Совпадает', mismatch: 'Расхождение', unverified: 'Недостаточно данных'}[field.status] }}
                </td>
              </tr>
              <tr v-show="expandedFields.includes(field.num)" :id="`field-requirements-${field.num}`">
                <td colspan="5" class="bg-slate-50 p-4">
                  <h3 class="font-medium">Связанные требования ({{ fieldRequirements[field.num]?.length ?? 0 }})</h3>
                  <p v-if="!fieldRequirements[field.num]?.length" class="mt-3 text-slate-500">
                    Для этого поля нет связанных требований.
                  </p>
                  <ul v-else class="mt-3 divide-y divide-slate-200 rounded border border-slate-200 bg-white">
                    <li v-for="requirement in fieldRequirements[field.num]" :key="requirement.id">
                      <details class="group/requirement">
                        <summary
                            class="flex cursor-pointer list-none items-start gap-3 p-4 hover:bg-slate-50 focus-visible:outline-blue-500 [&::-webkit-details-marker]:hidden">
                          <span class="pt-1 text-slate-400"><span class="sr-only">Требование </span>{{ requirement.id }}</span>
                          <RequirementText
                              :text="requirement.text || (requirement.kind === 'formula' ? 'Название не указано' : 'Условие не указано')"
                              :entity-documents="entityDocuments" @select="selectEntity"
                              class="min-w-0 flex-1 whitespace-pre-wrap break-words font-medium leading-7"
                              :class="requirement.silenced ? 'text-slate-500' : 'text-slate-800'"/>
                          <RequirementStatus :requirement="requirement"/>
                          <FontAwesomeIcon :icon="faChevronDown"
                                           class="mt-2 size-3 shrink-0 text-slate-400 transition-transform group-open/requirement:rotate-180 motion-reduce:transition-none"
                                           aria-hidden="true"/>
                        </summary>
                        <RequirementDetails :requirement="requirement" :entities="entities"
                                            :entity-documents="entityDocuments"
                                            class="border-t border-slate-100 p-4"
                                            @select="selectEntity"/>
                      </details>
                    </li>
                  </ul>
                </td>
              </tr>
            </template>
            </tbody>
          </table>
        </div>
      </section>
      <FileProblems :key="reportId" :files="result.files" :documents="report?.documents ?? []"
                    :requirements="requirements" :entities="entities"
                    :entity-documents="entityDocuments" @select="selectEntity"/>
      <RequirementsResults :key="reportId" :requirements="requirements" :entities="entities"
                           :entity-documents="entityDocuments"
                           @select="selectEntity"/>
    </template>
    <template #preview>
      <template v-if="selectedDocument">
        <div class="flex h-14 shrink-0 items-center justify-between gap-3 border-l border-slate-200 px-3">
          <h2 class="min-w-0 break-words font-semibold">{{ selectedDocument.name }}</h2>
          <button type="button"
                  class="inline-flex size-9 shrink-0 cursor-pointer items-center justify-center rounded hover:bg-slate-50 focus-visible:outline-blue-500"
                  aria-label="Закрыть PDF" title="Закрыть PDF" @click="selectedEntityId = undefined">
            <FontAwesomeIcon :icon="faXmark" class="size-4" aria-hidden="true"/>
          </button>
        </div>
        <EntityPdfPreview bare :report-id="reportId" :document-id="selectedDocument.id"
                          :selection-version="selectionVersion"
                          :selected-entity-id="selectedEntityId"/>
      </template>
    </template>
  </ReportWorkspace>
</template>
