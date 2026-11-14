<script lang="ts">
import {defineComponent, type PropType} from 'vue'

export default defineComponent({
  props: {
    text: {type: String, required: true},
    entityDocuments: {type: Object as PropType<Record<number, number>>, required: true},
  },
  emits: {select: (id: number) => Number.isInteger(id)},
  computed: {
    parts() {
      return this.text.split(/(#\d+)/g).map(text => ({text, reference: /^#\d+$/.test(text)}))
    },
  },
})
</script>

<template>
  <span>
    <template v-for="(part, index) in parts" :key="index">
      <button v-if="part.reference && entityDocuments[Number(part.text.slice(1))] !== undefined" type="button"
              class="cursor-pointer whitespace-nowrap rounded bg-amber-100 px-1 py-0.5 font-medium text-amber-900 hover:bg-amber-200 focus-visible:outline-blue-500"
              :aria-label="`Показать источник сущности ${part.text}`"
              @click.stop.prevent="$emit('select', Number(part.text.slice(1)))">{{ part.text }}</button>
      <mark v-else-if="part.reference"
            class="whitespace-nowrap rounded bg-amber-100 px-1 py-0.5 font-medium text-amber-900">{{ part.text }}</mark>
      <template v-else>{{ part.text }}</template>
    </template>
  </span>
</template>
