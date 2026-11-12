<script lang="ts">
import {defineComponent} from 'vue'
import {mapActions, mapGetters, mapState} from 'vuex'
import {FontAwesomeIcon} from '@fortawesome/vue-fontawesome'
import {faCheck} from '@fortawesome/free-solid-svg-icons/faCheck'
import type {ReportDetails, ReportDocument} from '../api'
import MarkdownEditor from '../components/MarkdownEditor.vue'
import ReportHeader from '../components/ReportHeader.vue'
import DocumentsStage from '../components/report-stages/DocumentsStage.vue'
import EntitiesStage from '../components/report-stages/EntitiesStage.vue'
import VerificationStage from '../components/report-stages/VerificationStage.vue'

export default defineComponent({
  components: {FontAwesomeIcon, MarkdownEditor, ReportHeader, DocumentsStage, EntitiesStage, VerificationStage},
  props: {reportId: {type: Number, required: true}},
  data() {
    return {
      selected: null as ReportDocument | null,
      faCheck,
      steps: [
        {stage: 'documents', title: 'Загрузка документов'},
        {stage: 'entities', title: 'Разметка сущностей'},
        {stage: 'verification', title: 'Проверка требований'},
      ],
    }
  },
  computed: {
    ...mapState('report', ['loading', 'loadError', 'pollError', 'actionError', 'advancing']),
    ...mapGetters('report', ['canAdvance', 'advanceTooltip']),
    report(): ReportDetails | null {
      return this.$store.state.report.current
    },
    stageIndex(): number {
      return this.steps.findIndex(step => step.stage === this.report?.stage)
    },
    completed(): boolean {
      return this.report?.stage === 'verification' && this.report.processing_status === 'completed'
    },
  },
  watch: {
    completed(completed: boolean) {
      if (completed) void this.$router.replace({name: 'requirements', params: {reportId: this.reportId}})
    },
    reportId: {
      immediate: true,
      handler(id: number) {
        this.selected = null
        void this.start(id)
      },
    },
  },
  unmounted() {
    void this.stop()
  },
  methods: {
    ...mapActions('report', ['start', 'stop', 'refresh', 'advance']),
  },
})
</script>

<template>
  <main class="mx-auto max-w-6xl p-6">
    <ReportHeader :report="completed ? null : report"/>
    <p v-if="loading" class="rounded-lg border border-slate-200 bg-white p-8 text-center text-slate-500" role="status">
      Загружаем отчёт…</p>
    <div v-else-if="!report" class="space-y-4 rounded-lg border border-slate-200 bg-white p-8 text-center">
      <p role="alert">{{ loadError }}</p>
      <button class="cursor-pointer rounded border border-slate-300 px-4 py-2 hover:bg-slate-50" @click="refresh">
        Попробовать снова
      </button>
    </div>

    <template v-if="report && !completed">
      <nav class="my-6 overflow-hidden rounded-lg border border-slate-200 bg-white"
           aria-label="Этапы проверки отчёта">
        <ol class="grid divide-y divide-slate-200 sm:grid-cols-3 sm:divide-x sm:divide-y-0">
          <li v-for="(step, index) in steps" :key="step.stage" class="flex items-center gap-3 p-4"
              :class="index === stageIndex ? 'bg-blue-50' : ''"
              :aria-current="index === stageIndex ? 'step' : undefined">
            <span class="flex size-8 shrink-0 items-center justify-center rounded-full font-semibold"
                  :class="index === stageIndex ? 'bg-blue-600 text-white' : index < stageIndex ? 'bg-green-100 text-green-700' : 'bg-slate-100 text-slate-400'">
              <FontAwesomeIcon v-if="index < stageIndex" :icon="faCheck" class="size-3" aria-label="Завершён"/>
              <template v-else>{{ index + 1 }}</template>
            </span>
            <p class="font-medium" :class="index > stageIndex ? 'text-slate-400' : ''">{{ step.title }}</p>
          </li>
        </ol>
      </nav>

      <p v-if="pollError" class="mb-4 rounded bg-amber-50 p-3 text-amber-900" role="status">{{ pollError }}</p>
      <p v-if="actionError" class="mb-4 rounded bg-red-50 p-3 text-red-700" role="alert">{{ actionError }}</p>

      <DocumentsStage v-if="report.stage === 'documents'" :report="report" @select="selected = $event"/>
      <EntitiesStage v-else-if="report.stage === 'entities'" :report="report" @select="selected = $event"/>
      <VerificationStage v-else-if="report.stage === 'verification'" :report="report"/>

      <div v-if="report.stage !== 'verification'" class="mt-6 flex flex-wrap items-start justify-end gap-4">
        <div class="sm:text-right" :class="{'cursor-not-allowed': !canAdvance}"
             :title="!canAdvance ? advanceTooltip : undefined">
          <button
              class="cursor-pointer rounded bg-blue-600 px-4 py-2 text-white hover:bg-blue-700 disabled:pointer-events-none disabled:opacity-50"
              :disabled="!canAdvance" @click="advance">{{
              advancing ? 'Переходим…' : report.stage === 'documents' ? 'К разметке сущностей →' : 'Проверить требования →'
            }}
          </button>
        </div>
      </div>
    </template>

    <MarkdownEditor v-if="selected" :report-id="reportId" :document="selected"
                    :readonly="advancing || report?.stage === 'verification'"
                    :entities="report?.stage !== 'documents'"
                    @close="selected = null" @saved="refresh"/>
  </main>
</template>
