<script lang="ts">
import {defineComponent} from 'vue'
import {RouterLink} from 'vue-router'
import {FontAwesomeIcon} from '@fortawesome/vue-fontawesome'
import {faPen} from '@fortawesome/free-solid-svg-icons/faPen'
import {faPlus} from '@fortawesome/free-solid-svg-icons/faPlus'
import {faTrash} from '@fortawesome/free-solid-svg-icons/faTrash'
import {
  deleteRequirement,
  getReport,
  getRequirements,
  runVerification,
  type ReportDetails,
  type ReportDocument,
  type Requirement,
} from '../api'
import ReportHeader from '../components/ReportHeader.vue'
import RequirementEditor from '../components/RequirementEditor.vue'
import RequirementText from '../components/RequirementText.vue'
import MarkdownEditor from '../components/MarkdownEditor.vue'

export default defineComponent({
  components: {RouterLink, FontAwesomeIcon, ReportHeader, RequirementEditor, RequirementText, MarkdownEditor},
  props: {reportId: {type: Number, required: true}},
  data() {
    return {
      report: null as ReportDetails | null,
      requirements: [] as Requirement[],
      loading: true,
      ready: false,
      error: '',
      starting: false,
      deleting: null as number | null,
      editorOpen: false,
      selectedRequirement: null as Requirement | null,
      session: 0,
      entityDocuments: {} as Record<number, number>,
      selectedEntityId: undefined as number | undefined,
      faPen, faPlus, faTrash,
    }
  },
  computed: {
    canVerify(): boolean {
      return this.ready && !this.loading && !this.starting && this.deleting === null && !this.editorOpen
    },
    selectedDocument(): ReportDocument | undefined {
      const entityId = this.selectedEntityId
      if (entityId === undefined) return
      return this.report?.documents.find(document => document.id === this.entityDocuments[entityId])
    },
  },
  watch: {
    reportId: {
      immediate: true,
      handler() {
        void this.load()
      },
    },
  },
  beforeUnmount() {
    this.session++
  },
  methods: {
    async load() {
      const session = ++this.session
      this.loading = true
      this.ready = false
      this.error = ''
      this.requirements = []
      this.editorOpen = false
      this.selectedEntityId = undefined
      try {
        const report = await getReport(this.reportId)
        if (session !== this.session) return
        this.report = report
        if (report.stage !== 'requirements' || report.processing_status !== 'completed') {
          await this.$router.replace({name: 'report', params: {reportId: this.reportId}})
          return
        }
        const definitions = await getRequirements(this.reportId)
        if (session !== this.session) return
        this.entityDocuments = definitions.entity_documents
        this.requirements = definitions.requirements
        this.ready = true
      } catch (cause) {
        if (session === this.session) this.error = cause instanceof Error ? cause.message : 'Не удалось загрузить требования.'
      } finally {
        if (session === this.session) this.loading = false
      }
    },
    openEditor(requirement: Requirement | null = null) {
      if (this.starting || this.deleting !== null) return
      this.selectedRequirement = requirement
      this.editorOpen = true
    },
    saved(requirement: Requirement) {
      const index = this.requirements.findIndex(item => item.id === requirement.id)
      if (index < 0) this.requirements.push(requirement)
      else this.requirements.splice(index, 1, requirement)
      this.editorOpen = false
      this.error = ''
    },
    async remove(requirement: Requirement) {
      if (this.starting || this.deleting !== null || this.editorOpen) return
      const session = this.session
      this.deleting = requirement.id
      this.error = ''
      try {
        await deleteRequirement(this.reportId, requirement.id)
        if (session === this.session) this.requirements = this.requirements.filter(item => item.id !== requirement.id)
      } catch (cause) {
        if (session === this.session) this.error = cause instanceof Error ? cause.message : 'Не удалось удалить требование.'
      } finally {
        if (session === this.session) this.deleting = null
      }
    },
    async verify() {
      if (!this.canVerify) return
      const session = this.session
      this.starting = true
      this.error = ''
      try {
        await runVerification(this.reportId)
        if (session === this.session) await this.$router.push({name: 'report', params: {reportId: this.reportId}})
      } catch (cause) {
        if (session === this.session) this.error = cause instanceof Error ? cause.message : 'Не удалось запустить проверку.'
      } finally {
        if (session === this.session) this.starting = false
      }
    },
  },
})
</script>

<template>
  <main class="mx-auto max-w-6xl p-6">
    <ReportHeader :report="report"/>
    <p v-if="loading" class="mt-6 rounded-lg border border-slate-200 bg-white p-8 text-center text-slate-500"
       role="status">
      Загружаем требования…
    </p>
    <template v-else>
      <div class="mt-6 flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 class="text-xl font-semibold">Редактирование требований</h2>
          <p class="mt-2 text-sm text-slate-500">Добавьте, исправьте или удалите требования, затем запустите
            проверку.</p>
        </div>
        <div class="flex flex-wrap justify-end gap-3">
          <RouterLink :to="{name: 'report', params: {reportId}, query: {view: 'entities'}}"
                      class="rounded border border-slate-300 bg-white px-4 py-2 hover:bg-slate-50 focus-visible:outline-blue-500">
            Просмотреть файлы и сущности
          </RouterLink>
          <button type="button" :disabled="!canVerify"
                  class="cursor-pointer rounded bg-blue-600 px-4 py-2 text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
                  @click="verify">{{ starting ? 'Запускаем…' : 'Начать проверку →' }}
          </button>
        </div>
      </div>
      <div v-if="error" class="mt-4 rounded bg-red-50 p-4 text-red-700" role="alert">
        {{ error }}
        <button v-if="!ready" type="button" class="ml-3 cursor-pointer underline" @click="load">Повторить загрузку
        </button>
      </div>
      <section v-if="ready" class="mt-6 overflow-hidden rounded-lg border border-slate-200 bg-white"
               aria-labelledby="requirements-heading">
        <div class="flex items-center justify-between gap-4 border-b border-slate-200 p-4">
          <h2 id="requirements-heading" class="font-semibold">Требования <span
              class="ml-1 text-sm font-normal tabular-nums text-slate-400">{{ requirements.length }}</span></h2>
          <button type="button" :disabled="starting || deleting !== null"
                  class="inline-flex cursor-pointer items-center gap-2 rounded border border-slate-300 px-3 py-2 text-sm hover:bg-slate-50 disabled:opacity-50"
                  @click="openEditor()">
            <FontAwesomeIcon :icon="faPlus" class="size-3.5" aria-hidden="true"/>
            Добавить требование
          </button>
        </div>
        <p v-if="!requirements.length" class="p-8 text-center text-slate-500">Требований пока нет. Добавьте первое
          требование.</p>
        <ul v-else class="divide-y divide-slate-200">
          <li v-for="requirement in requirements" :key="requirement.id"
              class="grid grid-cols-[2rem_minmax(0,1fr)_auto] items-center gap-3 px-4 py-3 hover:bg-slate-50 sm:grid-cols-[2.5rem_minmax(0,1fr)_auto] sm:gap-4">
            <span class="text-sm tabular-nums text-slate-400"><span class="sr-only">Требование </span>{{
                requirement.id
              }}</span>
            <RequirementText
                :text="requirement.kind === 'formula' ? requirement.equation : requirement.text || 'Условие не указано'"
                :title="requirement.kind === 'formula' ? requirement.equation : requirement.text || 'Условие не указано'"
                :entity-documents="entityDocuments" @select="selectedEntityId = $event"
                class="min-w-0 truncate text-sm leading-7" :class="requirement.kind === 'formula' ? 'font-mono' : ''"/>
            <div class="flex items-center gap-1">
              <button type="button" :disabled="starting || deleting !== null"
                      :aria-label="`Редактировать требование ${requirement.id}`" title="Редактировать требование"
                      aria-haspopup="dialog"
                      class="inline-flex size-9 cursor-pointer items-center justify-center rounded text-slate-500 hover:bg-slate-100 hover:text-blue-700 focus-visible:outline-blue-500 disabled:opacity-50"
                      @click="openEditor(requirement)">
                <FontAwesomeIcon :icon="faPen" class="size-3.5" aria-hidden="true"/>
              </button>
              <button type="button" :disabled="starting || deleting !== null"
                      :aria-label="`Удалить требование ${requirement.id}`" title="Удалить требование"
                      class="inline-flex size-9 cursor-pointer items-center justify-center rounded text-red-600 hover:bg-red-50 hover:text-red-700 focus-visible:outline-red-500 disabled:opacity-50"
                      @click="remove(requirement)">
                <FontAwesomeIcon :icon="faTrash" class="size-3.5" aria-hidden="true"/>
              </button>
            </div>
          </li>
        </ul>
      </section>
    </template>
    <RequirementEditor v-if="editorOpen" :report-id="reportId" :requirement="selectedRequirement" @saved="saved"
                       @close="editorOpen = false"/>
    <MarkdownEditor v-if="selectedDocument" :report-id="reportId" :document="selectedDocument" readonly entities
                    :selected-entity-id="selectedEntityId" @close="selectedEntityId = undefined"/>
  </main>
</template>
