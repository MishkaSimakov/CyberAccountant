<script lang="ts">
import {defineComponent, type PropType} from 'vue'
import {mapActions, mapState} from 'vuex'
import {FontAwesomeIcon} from '@fortawesome/vue-fontawesome'
import {faSpinner} from '@fortawesome/free-solid-svg-icons/faSpinner'
import {faRotateRight} from '@fortawesome/free-solid-svg-icons/faRotateRight'
import type {ReportDetails, ReportDocument} from '../../api'
import DocumentRow from './DocumentRow.vue'

export default defineComponent({
  components: {FontAwesomeIcon, DocumentRow},
  props: {report: {type: Object as PropType<ReportDetails>, required: true}},
  emits: {select: (document: ReportDocument) => Boolean(document)},
  data() {
    return {
      faSpinner, faRotateRight,
    }
  },
  computed: {
    ...mapState('report', ['advancing', 'deleting', 'retrying', 'uploads']),
    duplicateIds(): Set<number> {
      return new Set(this.report.duplicate_groups.flat())
    },
  },
  methods: {
    ...mapActions('report', ['refresh', 'remove', 'dismissUpload', 'retryExtraction']),
  },
})
</script>

<template>
  <div v-if="report.blockers.length"
       class="mb-4 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900"
       role="status" aria-live="polite">
    <ul class="list-disc space-y-1 pl-5">
      <li v-for="blocker in report.blockers" :key="blocker">{{ blocker }}</li>
    </ul>
  </div>
  <section class="rounded-lg border border-slate-200 bg-white"
           aria-labelledby="documents-heading">
    <div class="flex flex-wrap items-center justify-between gap-3 p-4">
      <h2 id="documents-heading" class="font-semibold">Документы <span
          class="ml-2 text-slate-500">{{ report.documents.length + uploads.length }}</span></h2>
      <div class="flex flex-wrap items-center gap-4">
            <span class="text-xs text-slate-500" aria-live="polite">Обработано {{
                report.progress.completed
              }} из {{ report.progress.total }}</span>
        <button
            class="inline-flex cursor-pointer items-center gap-2 rounded px-3 py-2 text-slate-600 hover:bg-slate-100 disabled:opacity-50"
            @click="refresh">
          <FontAwesomeIcon :icon="faRotateRight" class="size-3 text-slate-400" aria-hidden="true"/>
          Обновить
        </button>
      </div>
    </div>
    <p v-if="!report.documents.length && !uploads.length"
       class="border-t border-slate-200 p-8 text-center text-slate-500">Пока нет документов. Выберите файлы или
      перетащите их в область загрузки.</p>
    <ul v-else class="divide-y divide-slate-200 border-t border-slate-200">
      <DocumentRow v-for="upload in uploads" :key="`upload-${upload.id}`" :file="upload"
                   :status="upload.error ? 'failed' : 'uploading'" :removable="Boolean(upload.error)"
                   :disabled="advancing" @remove="dismissUpload(upload.id)"/>
      <DocumentRow v-for="document in report.documents" :key="document.id" :file="document"
                   :status="document.status" :edited="document.user_edited" :duplicate="duplicateIds.has(document.id)"
                   :removable="report.stage === 'documents'" :removing="deleting.includes(document.id)"
                   :disabled="advancing" @remove="remove(document.id)">
          <button
              v-if="report.stage === 'entities' && (document.status === 'failed' || retrying.includes(document.id))"
              class="inline-flex h-7 cursor-pointer items-center gap-2 rounded px-3 text-xs leading-none text-blue-700 hover:bg-blue-50 disabled:opacity-50"
              :aria-label="`Повторить извлечение из ${document.name}`"
              :disabled="advancing || retrying.includes(document.id)" @click="retryExtraction(document.id)">
            <FontAwesomeIcon :icon="retrying.includes(document.id) ? faSpinner : faRotateRight" class="size-3"
                             :class="{'animate-spin': retrying.includes(document.id)}" aria-hidden="true"/>
            {{ retrying.includes(document.id) ? 'Повторяем…' : 'Повторить извлечение' }}
          </button>
          <button v-if="document.status === (report.stage === 'documents' ? 'ocr_completed' : 'completed')"
                  class="inline-flex h-7 cursor-pointer items-center rounded px-3 text-xs leading-none text-blue-700 hover:bg-blue-50 disabled:opacity-50"
                  :disabled="deleting.includes(document.id) || advancing" @click="$emit('select', document)">Просмотреть
          </button>
      </DocumentRow>
    </ul>
  </section>
</template>
