<script lang="ts">
import {defineComponent} from 'vue'

export default defineComponent({
  props: {text: {type: String, required: true}},
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
      <mark v-if="part.reference"
            class="whitespace-nowrap rounded bg-amber-100 px-1 py-0.5 font-medium text-amber-900">{{ part.text }}</mark>
      <template v-else>{{ part.text }}</template>
    </template>
  </span>
</template>
