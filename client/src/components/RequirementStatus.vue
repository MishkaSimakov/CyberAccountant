<script lang="ts">
import {defineComponent, type PropType} from 'vue'
import {FontAwesomeIcon} from '@fortawesome/vue-fontawesome'
import {faCheck} from '@fortawesome/free-solid-svg-icons/faCheck'
import {faXmark} from '@fortawesome/free-solid-svg-icons/faXmark'
import {faCircleQuestion} from '@fortawesome/free-solid-svg-icons/faCircleQuestion'
import {faTriangleExclamation} from '@fortawesome/free-solid-svg-icons/faTriangleExclamation'
import type {VerifiedRequirement} from '../api'

export default defineComponent({
  components: {FontAwesomeIcon},
  props: {
    requirement: {type: Object as PropType<VerifiedRequirement>, required: true},
  },
  computed: {
    label(): string {
      const label = {
        true: 'Выполнено',
        false: 'Нарушено',
        error: 'Ошибка проверки',
        unverifiable: 'Не удалось проверить',
      }[this.requirement.verdict]
      return this.requirement.silenced ? `${label} — предупреждение отключено` : label
    },
    statusClass(): string {
      if (this.requirement.silenced) return 'border-slate-200 bg-slate-100 text-slate-400'
      return {
        true: 'border-green-200 bg-green-50 text-green-700',
        false: 'border-red-200 bg-red-50 text-red-700',
        error: 'border-orange-200 bg-orange-50 text-orange-700',
        unverifiable: 'border-amber-200 bg-amber-50 text-amber-700',
      }[this.requirement.verdict]
    },
    icon() {
      return {
        true: faCheck,
        false: faXmark,
        error: faTriangleExclamation,
        unverifiable: faCircleQuestion,
      }[this.requirement.verdict]
    },
  },
})
</script>

<template>
  <span class="inline-flex h-7 w-10 shrink-0 items-center justify-center rounded border"
        :class="statusClass" :title="label">
    <FontAwesomeIcon :icon="icon" class="size-3.5" aria-hidden="true"/>
    <span class="sr-only">{{ label }}</span>
  </span>
</template>
