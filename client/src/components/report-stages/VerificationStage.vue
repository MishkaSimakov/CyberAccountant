<script lang="ts">
import {defineComponent, type PropType} from 'vue'
import {mapActions, mapGetters, mapState} from 'vuex'
import {FontAwesomeIcon} from '@fortawesome/vue-fontawesome'
import {faSpinner} from '@fortawesome/free-solid-svg-icons/faSpinner'
import type {ReportDetails} from '../../api'

export default defineComponent({
  components: {FontAwesomeIcon},
  props: {report: {type: Object as PropType<ReportDetails>, required: true}},
  data() {
    return {faSpinner}
  },
  computed: {
    ...mapState('report', ['advancing']),
    ...mapGetters('report', ['canAdvance']),
  },
  methods: {
    ...mapActions('report', ['advance']),
  },
})
</script>

<template>
  <section
      class="rounded-lg border border-slate-200 bg-white p-10 text-center"
      :aria-busy="report.processing_status !== 'failed'" aria-labelledby="verification-heading">
    <template v-if="report.processing_status === 'failed'">
      <h2 id="verification-heading" class="text-lg font-semibold">Не удалось проверить требования</h2>
      <p class="mt-3 text-slate-500" role="alert">Во время проверки произошла ошибка. Попробуйте запустить её
        снова.</p>
      <button
          class="mt-5 cursor-pointer rounded bg-blue-600 px-4 py-2 text-white hover:bg-blue-700 disabled:opacity-50"
          :disabled="!canAdvance" @click="advance">{{ advancing ? 'Запускаем…' : 'Повторить проверку' }}
      </button>
    </template>
    <template v-else>
      <FontAwesomeIcon :icon="faSpinner" class="mx-auto mb-4 block size-8 animate-spin text-blue-600"
                       aria-hidden="true"/>
      <h2 id="verification-heading" class="text-lg font-semibold">Проверяем требования</h2>
      <p class="mt-2 text-slate-500">Это может занять некоторое время. Можно вернуться к отчёту позже.</p>
    </template>
  </section>
</template>
