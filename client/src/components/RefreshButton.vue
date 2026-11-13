<script lang="ts">
import {defineComponent} from 'vue'
import {FontAwesomeIcon} from '@fortawesome/vue-fontawesome'
import {faRotateRight} from '@fortawesome/free-solid-svg-icons/faRotateRight'

export default defineComponent({
  components: {FontAwesomeIcon},
  props: {loading: Boolean},
  emits: {refresh: () => true},
  data() {
    return {faRotateRight, spinning: false}
  },
  watch: {
    loading: {
      immediate: true,
      handler(loading: boolean) {
        if (loading) this.spinning = true
      },
    },
  },
  methods: {
    finishRotation() {
      if (!this.loading) this.spinning = false
    },
  },
})
</script>

<template>
  <button type="button"
          class="inline-flex cursor-pointer items-center gap-2 rounded px-3 py-2 text-slate-600 hover:bg-slate-100 disabled:opacity-50"
          :disabled="loading || spinning" :aria-busy="loading || spinning" @click="$emit('refresh')">
    <FontAwesomeIcon :icon="faRotateRight" class="size-3 text-slate-400" :class="{'animate-spin': spinning}"
                     aria-hidden="true" @animationiteration="finishRotation"/>
    Обновить
  </button>
</template>
