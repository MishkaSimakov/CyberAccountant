<script lang="ts">
import {defineComponent, markRaw, type PropType, type Raw} from 'vue'
import type {EntityBox} from '../../api'
import {getDocument, GlobalWorkerOptions, type PDFDocumentLoadingTask} from 'pdfjs-dist/legacy/build/pdf.mjs'
import {EventBus, PDFLinkService, PDFViewer} from 'pdfjs-dist/legacy/web/pdf_viewer.mjs'
import workerUrl from 'pdfjs-dist/legacy/build/pdf.worker.min.mjs?url'
import 'pdfjs-dist/legacy/web/pdf_viewer.css'

GlobalWorkerOptions.workerSrc = workerUrl

export default defineComponent({
  props: {
    src: {type: String, required: true},
    highlights: {type: Array as PropType<EntityBox[]>, default: () => []},
  },
  data() {
    return {
      loading: true,
      error: '',
      lifetime: markRaw(new AbortController()),
      viewer: undefined as Raw<PDFViewer> | undefined,
      loadingTask: undefined as Raw<PDFDocumentLoadingTask> | undefined,
      safariGestureScale: undefined as number | undefined,
      resizeObserver: undefined as ResizeObserver | undefined,
      revealPending: false,
      highlightAnimationStartedAt: undefined as number | undefined,
    }
  },
  watch: {
    highlights() {
      this.revealHighlights()
    },
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
        this.revealHighlights()
      })
      eventBus.on('pagerendered', ({error: cause}: { error?: Error }) => {
        if (cause) this.error = 'Не удалось отобразить PDF. Откройте PDF отдельно.'
        this.paintHighlights()
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
        this.error = 'Не удалось загрузить PDF. Откройте PDF отдельно.'
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
    revealHighlights() {
      this.revealPending = true
      const first = this.highlights[0]
      if (first && this.viewer && !this.loading) this.viewer.currentPageNumber = first.page
      this.paintHighlights()
    },
    paintHighlights() {
      // In PDF units of padding so it scales with the document.
      const margin = 2;
      const bounceScales = [1, 1.5, .9, 1.1, 1]
      const bounceDuration = 700

      const pages = this.$refs.pages as HTMLDivElement | undefined
      if (!pages || !this.viewer || this.lifetime.signal.aborted) return
      pages.querySelectorAll('[data-entity-highlight]').forEach(element => element.remove())
      const groups = new Map<number, EntityBox[]>()
      for (const box of this.highlights) {
        const group = groups.get(box.page) ?? []
        group.push(box)
        groups.set(box.page, group)
      }
      for (const [page, boxes] of groups) {
        const view = this.viewer.getPageView(page - 1)
        const pageElement = view?.div as HTMLElement | undefined
        const canvas = pageElement?.querySelector<HTMLElement>('.canvasWrapper')
        if (!canvas) continue // Other pages will be painted on pagerendered.
        canvas.style.position = 'relative'
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg')
        svg.setAttribute('viewBox', '0 0 1 1')
        svg.setAttribute('preserveAspectRatio', 'none')
        svg.setAttribute('role', 'img')
        svg.setAttribute('aria-label', 'Выделенная сущность')
        svg.setAttribute('data-entity-highlight', String(page))
        svg.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;pointer-events:none;z-index:3'
        const path = document.createElementNS('http://www.w3.org/2000/svg', 'path')

        const {width, height} = view.viewport.clone({scale: 1})
        const marginX = margin / width, marginY = margin / height
        path.setAttribute('d', boxes.map(({bbox_normalized: [x0, y0, x1, y1]}) => {
          const left = Math.max(0, x0 - marginX), top = Math.max(0, y0 - marginY)
          const right = Math.min(1, x1 + marginX), bottom = Math.min(1, y1 + marginY)
          return `M${left} ${top}H${right}V${bottom}H${left}Z`
        }).join(' '))
        path.setAttribute('fill', '#facc15')
        path.setAttribute('fill-opacity', '.5')
        path.setAttribute('stroke', '#92400e')
        path.setAttribute('stroke-width', '2.5')
        path.setAttribute('vector-effect', 'non-scaling-stroke')
        svg.append(path)
        canvas.append(svg)
        if (page === this.highlights[0]?.page && !this.loading
            && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
          // Resume the opening bounce across page repaints without restarting it.
          this.highlightAnimationStartedAt ??= performance.now()
          const elapsed = performance.now() - this.highlightAnimationStartedAt
          if (elapsed < bounceDuration) {
            path.style.transformBox = 'fill-box'
            path.style.transformOrigin = 'center'
            const animation = path.animate(
                bounceScales.map(scale => ({transform: `scale(${scale})`})),
                {duration: bounceDuration, easing: 'ease-out'},
            )
            animation.currentTime = elapsed
          }
        }
      }
      const first = this.highlights[0]
      if (!this.revealPending || !first || this.loading) return
      const pageElement = this.viewer.getPageView(first.page - 1)?.div as HTMLElement | undefined
      const canvas = pageElement?.querySelector<HTMLElement>('.canvasWrapper')
      const container = this.$refs.container as HTMLDivElement
      if (!canvas?.querySelector('[data-entity-highlight]') || !canvas.clientHeight) return
      const pageRect = canvas.getBoundingClientRect(), containerRect = container.getBoundingClientRect()
      const [x0, y0, x1, y1] = first.bbox_normalized
      container.scrollTo({
        top: container.scrollTop + pageRect.top - containerRect.top + (y0 + y1) / 2 * pageRect.height - container.clientHeight / 2,
        left: container.scrollLeft + pageRect.left - containerRect.left + (x0 + x1) / 2 * pageRect.width - container.clientWidth / 2,
      })
      this.revealPending = false
    },
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
         aria-label="PDF документа">
      <div ref="pages" class="pdfViewer"/>
    </div>
    <p v-if="loading" class="absolute inset-x-0 top-0 bg-slate-100 p-6 text-slate-500" role="status">Загружаем PDF…</p>
    <p v-if="error" class="absolute inset-x-0 top-0 bg-red-50 p-6 text-red-700" role="alert">{{ error }}</p>
  </div>
</template>
