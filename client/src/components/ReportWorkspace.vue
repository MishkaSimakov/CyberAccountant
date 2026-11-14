<script lang="ts">
import {defineComponent} from 'vue'

export default defineComponent({
  props: {previewOpen: Boolean},
  emits: ['close'],
  data() {
    return {reportWidth: 50, resizing: false}
  },
  mounted() {
    window.addEventListener('keydown', this.onKeydown)
  },
  unmounted() {
    window.removeEventListener('keydown', this.onKeydown)
  },
  methods: {
    onKeydown(event: KeyboardEvent) {
      if (this.previewOpen && event.key === 'Escape' && !event.defaultPrevented) this.$emit('close')
    },
    resize(event: PointerEvent) {
      if (!this.resizing) return
      event.preventDefault()
      const bounds = (this.$refs.workspace as HTMLElement).getBoundingClientRect()
      this.setWidth((event.clientX - bounds.left) / bounds.width * 100)
    },
    startResize(event: PointerEvent) {
      if (event.button !== 0) return
      event.preventDefault()
      this.resizing = true
      const divider = event.currentTarget as HTMLElement
      divider.setPointerCapture(event.pointerId)
      this.resize(event)
    },
    setWidth(width: number) {
      this.reportWidth = Math.min(65, Math.max(35, width))
    },
    resizeWithKeyboard(event: KeyboardEvent) {
      switch (event.key) {
        case 'ArrowLeft':
          this.setWidth(this.reportWidth - 5);
          break
        case 'ArrowRight':
          this.setWidth(this.reportWidth + 5);
          break
        case 'Home':
          this.setWidth(35);
          break
        case 'End':
          this.setWidth(65);
          break
        default:
          return
      }
      event.preventDefault()
    },
  },
})
</script>

<template>
  <main ref="workspace" class="workspace mx-auto p-6"
        :class="{'preview-open': previewOpen, resizing}"
        :style="{'--report-width': `${reportWidth}%`}">
    <div id="report-content" class="min-w-0">
      <slot/>
    </div>
    <template v-if="previewOpen">
      <div class="divider" role="separator" tabindex="0" aria-label="Ширина отчёта"
           aria-orientation="vertical" aria-controls="report-content" :aria-valuenow="Math.round(reportWidth)"
           :aria-valuemin="35" :aria-valuemax="65"
           @pointerdown="startResize" @pointermove="resize" @pointerup="resizing = false"
           @pointercancel="resizing = false"
           @lostpointercapture="resizing = false" @keydown="resizeWithKeyboard"/>
      <aside class="preview-pane bg-white"
             aria-label="Источник сущности">
        <slot name="preview"/>
      </aside>
    </template>
  </main>
</template>

<style scoped>
.workspace {
  max-width: 72rem;
}

.preview-open {
  display: flex;
  max-width: none;
  flex-direction: column;
  padding: 0;
}

.preview-open > #report-content {
  padding: 1.5rem;
}

.resizing {
  cursor: col-resize;
  -webkit-user-select: none;
  user-select: none;
}

.preview-pane {
  position: sticky;
  top: 0;
  z-index: 10;
  display: flex;
  order: -1;
  height: 42dvh;
  min-height: 0;
  width: 100%;
  flex-shrink: 0;
  flex-direction: column;
  overflow: hidden;
}

.divider {
  display: none;
  -webkit-user-select: none;
  user-select: none;
}

@media (min-width: 64rem) {
  .preview-open {
    display: grid;
    grid-template-columns: minmax(0, var(--report-width)) 1rem minmax(0, 1fr);
    align-items: start;
    gap: 0;
  }

  .preview-pane {
    top: 0;
    order: 0;
    height: 100dvh;
  }

  .divider {
    position: sticky;
    top: 0;
    display: flex;
    height: 100dvh;
    cursor: col-resize;
    touch-action: none;
    align-items: center;
    justify-content: center;
  }

  .divider::after {
    content: '';
    width: 3px;
    height: 3rem;
    border-radius: 9999px;
    background: #cbd5e1;
  }

  .divider:hover::after,
  .divider:focus-visible::after {
    background: #2563eb;
  }

  .divider:focus-visible {
    outline: 2px solid #2563eb;
    outline-offset: -2px;
  }
}
</style>
