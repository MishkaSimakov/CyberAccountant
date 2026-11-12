<script lang="ts">
import {defineComponent, type PropType} from 'vue'
import type {ReportDetails, ReportDocument} from '../../api'
import DocumentList from './DocumentList.vue'

export default defineComponent({
  components: {DocumentList},
  props: {report: {type: Object as PropType<ReportDetails>, required: true}},
  emits: {select: (document: ReportDocument) => Boolean(document)},
})
</script>

<template>
  <DocumentList :report="report" @select="$emit('select', $event)"/>
</template>
