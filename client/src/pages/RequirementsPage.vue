<script lang="ts">
import {defineComponent} from 'vue'
import {mapActions, mapState} from 'vuex'
import {FontAwesomeIcon} from '@fortawesome/vue-fontawesome'
import {faSpinner} from '@fortawesome/free-solid-svg-icons/faSpinner'
import type {Report, Requirement} from '../api'
import ReportHeader from '../components/ReportHeader.vue'
import RequirementsResults from '../components/RequirementsResults.vue'

export default defineComponent({
  components: {FontAwesomeIcon, ReportHeader, RequirementsResults},
  props: {reportId: {type: Number, required: true}},
  data() {
    return {faSpinner}
  },
  computed: {
    ...mapState('requirements', ['loading', 'error']),
    report(): Report | null {
      return this.$store.state.requirements.report
    },
    requirements(): Requirement[] | null {
      return this.$store.state.requirements.items
    },
  },
  watch: {
    reportId: {
      immediate: true,
      handler(id: number) {
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
    ...mapActions('requirements', ['start', 'stop', 'load']),
  },
})
</script>

<template>
  <main class="mx-auto max-w-6xl p-6">
    <ReportHeader :report="report"/>
    <div v-if="loading" class="mt-6 rounded-lg border border-slate-200 bg-white p-8 text-center text-slate-500"
         role="status">
      <FontAwesomeIcon :icon="faSpinner" class="mr-2 size-4 animate-spin" aria-hidden="true"/>
      Загружаем результаты проверки…
    </div>
    <div v-else-if="error" class="mt-6 rounded-lg border border-slate-200 bg-white p-8 text-center">
      <p role="alert">{{ error }}</p>
      <button class="mt-4 cursor-pointer text-blue-700 hover:underline" @click="load">Повторить загрузку</button>
    </div>
    <RequirementsResults v-else-if="requirements !== null" :key="reportId" :requirements="requirements"/>
  </main>
</template>
