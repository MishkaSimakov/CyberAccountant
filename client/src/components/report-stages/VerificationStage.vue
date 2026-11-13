<script lang="ts">
import {defineComponent, type PropType} from 'vue'
import {mapActions, mapGetters, mapState} from 'vuex'
import {FontAwesomeIcon} from '@fortawesome/vue-fontawesome'
import {faSpinner} from '@fortawesome/free-solid-svg-icons/faSpinner'
import {editRequirements, reportLogUrl, type ReportDetails} from '../../api'
import LogModal from '../LogModal.vue'

export default defineComponent({
  components: {FontAwesomeIcon, LogModal},
  props: {report: {type: Object as PropType<ReportDetails>, required: true}},
  data() {
    return {faSpinner, showingLog: false}
  },
  computed: {
    ...mapState('report', ['advancing']),
    ...mapGetters('report', ['canAdvance']),
  },
  methods: {
    reportLogUrl,
    ...mapActions('report', ['advance']),
    async edit() {
      try {
        await editRequirements(this.report.id)
        await this.$router.push({name: 'requirements-edit', params: {reportId: this.report.id}})
      } catch (cause) {
        this.$store.commit('report/patch', {actionError: cause instanceof Error ? cause.message : 'Не удалось открыть требования.'})
      }
    },
  },
})
</script>

<template>
  <section
      class="rounded-lg border border-slate-200 bg-white p-10 text-center"
      :aria-busy="report.processing_status !== 'failed'" aria-labelledby="verification-heading">
    <template v-if="report.processing_status === 'failed'">
      <h2 id="verification-heading" class="text-lg font-semibold">{{
          report.stage === 'requirements' ? 'Не удалось подготовить требования' : 'Не удалось проверить требования'
        }}</h2>
      <p class="mt-3 text-slate-500" role="alert">Во время обработки произошла ошибка. Повторите попытку.</p>
      <button v-if="report.stage === 'requirements'" type="button"
              class="mt-2 cursor-pointer rounded text-sm text-blue-700 underline underline-offset-2 hover:text-blue-900"
              @click="showingLog = true">Подробнее
      </button>
      <div class="mt-5 flex flex-wrap items-center justify-center gap-3">
        <button
            class="cursor-pointer rounded bg-blue-600 px-4 py-2 text-white hover:bg-blue-700 disabled:opacity-50"
            :disabled="!canAdvance" @click="advance">{{ advancing ? 'Запускаем…' : 'Повторить' }}
        </button>
        <button v-if="report.stage === 'verification'" type="button"
                class="cursor-pointer rounded border border-slate-300 px-4 py-2 hover:bg-slate-50"
                :disabled="advancing" @click="edit">Редактировать требования
        </button>
      </div>
    </template>
    <template v-else>
      <FontAwesomeIcon :icon="faSpinner" class="mx-auto mb-4 block size-8 animate-spin text-blue-600"
                       aria-hidden="true"/>
      <h2 id="verification-heading" class="text-lg font-semibold">
        {{ report.stage === 'requirements' ? 'Подготавливаем требования' : 'Проверяем требования' }}</h2>
      <p class="mt-2 text-slate-500">Это может занять некоторое время. Можно вернуться к отчёту позже.</p>
    </template>
  </section>
  <LogModal v-if="showingLog" :src="reportLogUrl(report.id)" :title="`Журнал обработки — ${report.name}`"
            :filename="`report-${report.id}.log`" @close="showingLog = false"/>
</template>
