<script lang="ts">
import {defineComponent} from 'vue'
import {FontAwesomeIcon} from '@fortawesome/vue-fontawesome'
import {faArrowUpFromBracket} from '@fortawesome/free-solid-svg-icons/faArrowUpFromBracket'

export default defineComponent({
  components: {FontAwesomeIcon},
  props: {
    title: {type: String, required: true},
    description: {type: String, required: true},
    multiple: Boolean,
    disabled: Boolean,
  },
  emits: {select: (_files: File[]) => true},
  data() {
    return {dragging: false, faArrowUpFromBracket}
  },
  methods: {
    select(event: Event) {
      const input = event.target as HTMLInputElement
      this.$emit('select', Array.from(input.files || []))
      input.value = ''
    },
    drop(event: DragEvent) {
      this.dragging = false
      if (!this.disabled) this.$emit('select', Array.from(event.dataTransfer?.files || []))
    },
  },
})
</script>

<template>
  <label
      class="relative flex flex-col items-center rounded-lg border-2 border-dashed p-6 text-center transition-colors focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-blue-600"
      :class="disabled ? 'cursor-not-allowed border-slate-200 bg-slate-50 opacity-60' : dragging ? 'border-blue-500 bg-blue-50' : 'cursor-pointer border-slate-300 hover:border-blue-400 hover:bg-blue-50/50'"
      @dragover.prevent="dragging = !disabled" @dragleave.prevent="dragging = false"
      @drop.prevent="drop($event)">
    <FontAwesomeIcon :icon="faArrowUpFromBracket" class="mb-3 size-5 text-blue-600" aria-hidden="true"/>
    <span class="font-medium">{{ title }}</span>
    <span class="mt-1 text-xs text-slate-500">{{ description }}</span>
    <span class="mt-3 text-sm text-blue-700">{{
        disabled ? 'Файл уже выбран' : 'Выберите файл' + (multiple ? 'ы' : '') + ' или перетащите сюда'
      }}</span>
    <input class="absolute inset-0 h-full w-full cursor-pointer opacity-0 disabled:cursor-not-allowed" type="file"
           accept=".pdf,.docx,.xlsx,.png,.jpg,.jpeg" :aria-label="title" :multiple="multiple" :disabled="disabled"
           @change="select"/>
  </label>
</template>
