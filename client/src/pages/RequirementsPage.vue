<script lang="ts">
import {defineComponent} from 'vue'
import {mapActions, mapState} from 'vuex'
import {FontAwesomeIcon} from '@fortawesome/vue-fontawesome'
import {faSpinner} from '@fortawesome/free-solid-svg-icons/faSpinner'
import {editRequirements, reportArchiveUrl} from '../api'
import type {FieldVerdict, Report, ReportDetails, ReportDocument, VerifiedRequirement, VerificationResult} from '../api'
import ReportHeader from '../components/ReportHeader.vue'
import RequirementsResults from '../components/RequirementsResults.vue'
import MarkdownEditor from '../components/MarkdownEditor.vue'
import RequirementText from '../components/RequirementText.vue'
import FileProblems from '../components/FileProblems.vue'

export default defineComponent({
  components: {FontAwesomeIcon, ReportHeader, RequirementsResults, MarkdownEditor, RequirementText, FileProblems},
  props: {reportId: {type: Number, required: true}},
  data() {
    return {faSpinner, selectedEntityId: undefined as number | undefined, editing: false, editError: ''}
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
    reportArchiveUrl,
    ...mapActions('requirements', ['start', 'stop', 'load']),
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
      if (field.type === 'amount' && Number.isFinite(Number(value))) {
        return Number(value).toLocaleString('ru-RU', {minimumFractionDigits: 2, maximumFractionDigits: 2})
      }
      return value
    },
  },
})
</script>

<template>
  <main class="mx-auto max-w-6xl p-6">
    <ReportHeader :report="report"/>
    <div v-if="loading" class="mt-6 flex items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white p-8 text-center text-slate-500"
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
              <th class="p-4">Поле</th>
              <th class="p-4">Указано</th>
              <th class="p-4">Ожидается</th>
              <th class="p-4">Расхождение</th>
              <th class="p-4">Статус</th>
            </tr>
            </thead>
            <tbody class="divide-y divide-slate-100">
            <tr v-for="field in result.card.cells" :key="field.num">
              <td class="p-4">
                <RequirementText :text="`#${field.num}`" :entity-documents="entityDocuments"
                                 @select="selectedEntityId = $event"/>
                <span class="ml-2 text-slate-500">{{ field.raw }}</span>
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
            </tbody>
          </table>
        </div>
      </section>
      <FileProblems :key="reportId" :files="result.files" :documents="report?.documents ?? []"
                    :entity-documents="entityDocuments" @select="selectedEntityId = $event"/>
      <RequirementsResults :key="reportId" :requirements="requirements" :entity-documents="entityDocuments"
                           @select="selectedEntityId = $event"/>
    </template>
    <MarkdownEditor v-if="selectedDocument" :report-id="reportId" :document="selectedDocument"
                    readonly entities pdf-entities :selected-entity-id="selectedEntityId" @close="selectedEntityId = undefined"/>
  </main>
</template>
