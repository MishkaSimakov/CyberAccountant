<script lang="ts">
import {defineComponent, markRaw, type Raw} from 'vue'
import {getDocument, GlobalWorkerOptions, type PDFDocumentLoadingTask} from 'pdfjs-dist/legacy/build/pdf.mjs'
import {EventBus, PDFLinkService, PDFViewer} from 'pdfjs-dist/legacy/web/pdf_viewer.mjs'
import workerUrl from 'pdfjs-dist/legacy/build/pdf.worker.min.mjs?url'
import 'pdfjs-dist/legacy/web/pdf_viewer.css'

GlobalWorkerOptions.workerSrc = workerUrl

export default defineComponent({
  props: {src: {type: String, required: true}},
  data() {
    return {
      loading: true,
      error: '',
      lifetime: markRaw(new AbortController()),
      viewer: undefined as Raw<PDFViewer> | undefined,
      loadingTask: undefined as Raw<PDFDocumentLoadingTask> | undefined,
      safariGestureScale: undefined as number | undefined,
      resizeObserver: undefined as ResizeObserver | undefined,
    }
  },
  async mounted() {
    const container = this.$refs.container as HTMLDivElement
    const pages = this.$refs.pages as HTMLDivElement
    try {
      const eventBus = new EventBus()
      const linkService = new PDFLinkService({eventBus, externalLinkTarget: 2})
      const options = {
        container,
        viewer: pages,
        eventBus,
        linkService,
        abortSignal: this.lifetime.signal,
      }
      this.viewer = markRaw(new PDFViewer(options))
      linkService.setViewer(this.viewer)
      eventBus.on('pagesinit', () => {
        if (!this.viewer || this.lifetime.signal.aborted) return
        this.viewer.currentScaleValue = 'page-width'
        this.loading = false
      })
      eventBus.on('pagerendered', ({error: cause}: { error?: Error }) => {
        if (cause) this.error = 'Не удалось отобразить PDF. Откройте оригинал файла.'
      })
      container.addEventListener('wheel', this.onWheel, {passive: false, signal: this.lifetime.signal})
      for (const name of ['gesturestart', 'gesturechange', 'gestureend']) {
        container.addEventListener(name, this.onGesture, {passive: false, signal: this.lifetime.signal})
      }
      this.resizeObserver = markRaw(new ResizeObserver(() => {
        if (this.viewer?.currentScaleValue === 'page-width') this.viewer.currentScaleValue = 'page-width'
      }))
      this.resizeObserver.observe(container)
      this.loadingTask = markRaw(getDocument({url: this.src, useSystemFonts: true}))
      const pdf = await this.loadingTask.promise
      if (this.lifetime.signal.aborted) return
      linkService.setDocument(pdf)
      this.viewer.setDocument(pdf)
    } catch {
      if (!this.lifetime.signal.aborted) {
        this.error = 'Не удалось загрузить PDF. Откройте оригинал файла.'
        this.loading = false
      }
    }
  },
  unmounted() {
    this.lifetime.abort()
    this.resizeObserver?.disconnect()
    this.viewer?.setDocument(null)
    void this.loadingTask?.destroy()
  },
  methods: {
    scaleAt(factor: number, x: number, y: number) {
      if (!this.viewer || this.loading || this.error) return
      const current = this.viewer.currentScale
      const next = Math.min(5, Math.max(0.25, current * factor))
      this.viewer.updateScale({scaleFactor: next / current, origin: [x, y], drawingDelay: 150})
    },
    onWheel(event: WheelEvent) {
      // Trackpad pinches arrive as Ctrl+wheel; ordinary two-finger scrolling pans the PDF.
      if (!event.ctrlKey) return
      event.preventDefault()
      if (this.safariGestureScale !== undefined) return
      const container = this.$refs.container as HTMLDivElement | undefined
      const delta = event.deltaY * (event.deltaMode === WheelEvent.DOM_DELTA_LINE ? 16
          : event.deltaMode === WheelEvent.DOM_DELTA_PAGE ? container?.clientHeight ?? 800 : 1)
      this.scaleAt(Math.exp(-delta * 0.01), event.clientX, event.clientY)
    },
    onGesture(event: Event) {
      // Safari on macOS exposes native gesture events instead of Ctrl+wheel.
      const gesture = event as Event & { scale: number; clientX: number; clientY: number }
      event.preventDefault()
      if (event.type === 'gesturestart') {
        this.safariGestureScale = gesture.scale
      } else if (event.type === 'gesturechange') {
        this.scaleAt(gesture.scale / (this.safariGestureScale ?? 1), gesture.clientX, gesture.clientY)
        this.safariGestureScale = gesture.scale
      } else {
        this.safariGestureScale = undefined
      }
    },
  },
})
</script>

<template>
  <div class="relative min-h-0 flex-1 bg-slate-100" :aria-busy="loading">
    <div ref="container" class="absolute inset-0 overflow-auto overscroll-contain" tabindex="0" role="region"
         aria-label="Оригинал документа PDF">
      <div ref="pages" class="pdfViewer"/>
    </div>
    <p v-if="loading" class="absolute inset-x-0 top-0 bg-slate-100 p-6 text-slate-500" role="status">Загружаем PDF…</p>
    <p v-if="error" class="absolute inset-x-0 top-0 bg-red-50 p-6 text-red-700" role="alert">{{ error }}</p>
  </div>
</template>
