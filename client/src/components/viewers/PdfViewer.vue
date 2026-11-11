<script setup lang="ts">
import {onMounted, onUnmounted, ref} from 'vue'
import {getDocument, GlobalWorkerOptions, type PDFDocumentLoadingTask} from 'pdfjs-dist/legacy/build/pdf.mjs'
import {EventBus, PDFLinkService, PDFViewer} from 'pdfjs-dist/legacy/web/pdf_viewer.mjs'
import workerUrl from 'pdfjs-dist/legacy/build/pdf.worker.min.mjs?url'
import 'pdfjs-dist/legacy/web/pdf_viewer.css'

const props = defineProps<{ src: string }>()
const container = ref<HTMLDivElement | null>(null)
const pages = ref<HTMLDivElement | null>(null)
const loading = ref(true)
const error = ref('')
const lifetime = new AbortController()
let viewer: PDFViewer | undefined
let loadingTask: PDFDocumentLoadingTask | undefined
let safariGestureScale: number | undefined
let resizeObserver: ResizeObserver | undefined

GlobalWorkerOptions.workerSrc = workerUrl

function scaleAt(factor: number, x: number, y: number) {
  if (!viewer || loading.value || error.value) return
  const current = viewer.currentScale
  const next = Math.min(5, Math.max(0.25, current * factor))
  viewer.updateScale({scaleFactor: next / current, origin: [x, y], drawingDelay: 150})
}

function onWheel(event: WheelEvent) {
  // Trackpad pinches arrive as Ctrl+wheel; ordinary two-finger scrolling pans the PDF.
  if (!event.ctrlKey) return
  event.preventDefault()
  if (safariGestureScale !== undefined) return
  const delta = event.deltaY * (event.deltaMode === WheelEvent.DOM_DELTA_LINE ? 16
      : event.deltaMode === WheelEvent.DOM_DELTA_PAGE ? container.value?.clientHeight ?? 800 : 1)
  scaleAt(Math.exp(-delta * 0.01), event.clientX, event.clientY)
}

function onGesture(event: Event) {
  // Safari on macOS exposes native gesture events instead of Ctrl+wheel.
  const gesture = event as Event & { scale: number; clientX: number; clientY: number }
  event.preventDefault()
  if (event.type === 'gesturestart') {
    safariGestureScale = gesture.scale
  } else if (event.type === 'gesturechange') {
    scaleAt(gesture.scale / (safariGestureScale ?? 1), gesture.clientX, gesture.clientY)
    safariGestureScale = gesture.scale
  } else {
    safariGestureScale = undefined
  }
}

onMounted(async () => {
  if (!container.value || !pages.value) return
  try {
    const eventBus = new EventBus()
    const linkService = new PDFLinkService({eventBus, externalLinkTarget: 2})
    const options = {
      container: container.value,
      viewer: pages.value,
      eventBus,
      linkService,
      abortSignal: lifetime.signal,
    }
    viewer = new PDFViewer(options)
    linkService.setViewer(viewer)
    eventBus.on('pagesinit', () => {
      if (!viewer || lifetime.signal.aborted) return
      viewer.currentScaleValue = 'page-width'
      loading.value = false
    })
    eventBus.on('pagerendered', ({error: cause}: { error?: Error }) => {
      if (cause) error.value = 'Не удалось отобразить PDF. Откройте оригинал файла.'
    })
    container.value.addEventListener('wheel', onWheel, {passive: false, signal: lifetime.signal})
    for (const name of ['gesturestart', 'gesturechange', 'gestureend']) {
      container.value.addEventListener(name, onGesture, {passive: false, signal: lifetime.signal})
    }
    resizeObserver = new ResizeObserver(() => {
      if (viewer?.currentScaleValue === 'page-width') viewer.currentScaleValue = 'page-width'
    })
    resizeObserver.observe(container.value)
    loadingTask = getDocument({url: props.src, useSystemFonts: true})
    const pdf = await loadingTask.promise
    if (lifetime.signal.aborted) return
    linkService.setDocument(pdf)
    viewer.setDocument(pdf)
  } catch {
    if (!lifetime.signal.aborted) {
      error.value = 'Не удалось загрузить PDF. Откройте оригинал файла.'
      loading.value = false
    }
  }
})

onUnmounted(() => {
  lifetime.abort()
  resizeObserver?.disconnect()
  viewer?.setDocument(null)
  void loadingTask?.destroy()
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
