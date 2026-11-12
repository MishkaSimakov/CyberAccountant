<script lang="ts">
import {defineComponent, type PropType} from 'vue'
import {mapActions, mapGetters, mapState} from 'vuex'
import type {ReportDetails, ReportDocument} from '../../api'
import UploadZone from '../UploadZone.vue'
import DocumentList from './DocumentList.vue'

export default defineComponent({
  components: {UploadZone, DocumentList},
  props: {report: {type: Object as PropType<ReportDetails>, required: true}},
  emits: {select: (document: ReportDocument) => Boolean(document)},
  computed: {
    ...mapState('report', ['advancing']),
    ...mapGetters('report', ['finalPresent']),
  },
  methods: {
    ...mapActions('report', ['addFiles']),
  },
})
</script>

<template>
  <section class="mb-5 rounded-lg border border-slate-200 bg-white p-5"
           aria-labelledby="upload-heading">
    <h2 id="upload-heading" class="text-lg font-semibold">Загрузите документы</h2>

    <div class="mt-5 grid gap-4 sm:grid-cols-2">
      <UploadZone title="Итоговый отчёт" description="Один файл с показателями для проверки"
                  :disabled="finalPresent || advancing" @select="addFiles({files: $event, role: 'final_report'})"/>
      <UploadZone title="Подтверждающие документы" description="Первичные и расчётные документы" multiple
                  :disabled="advancing" @select="addFiles({files: $event, role: 'supporting'})"/>
    </div>
    <p class="mt-3 text-xs text-slate-500">PDF, DOCX, XLSX, PNG, JPEG · до 20 МБ на файл.</p>
  </section>
  <DocumentList :report="report" @select="$emit('select', $event)"/>
</template>
