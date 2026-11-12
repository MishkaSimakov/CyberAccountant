<script lang="ts">
import {defineComponent, markRaw} from 'vue'
import {renderAsync} from 'docx-preview'
import zoomScript from './docxZoom.js?raw'

export default defineComponent({
  props: {src: {type: String, required: true}},
  data() {
    return {
      loading: true,
      error: '',
      preview: '',
      lifetime: markRaw(new AbortController()),
    }
  },
  async mounted() {
    try {
      const response = await fetch(this.src, {signal: this.lifetime.signal})
      if (!response.ok) throw new Error('Failed to load DOCX')
      const data = await response.arrayBuffer()
      if (this.lifetime.signal.aborted) return
      const container = document.createElement('div')
      await renderAsync(data, container, undefined, {
        ignoreWidth: true,
        useBase64URL: true,
        renderAltChunks: false,
      })
      if (this.lifetime.signal.aborted) return
      const nonce = btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(16))))
      // Isolate document styles and links from the application in a sandboxed frame.
      this.preview = `<!doctype html><html lang="ru"><head><meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'nonce-${nonce}'; img-src data:; font-src data:; style-src 'unsafe-inline';">
        <style>
          html, body { height: 100%; margin: 0; overflow: hidden; background: #f1f5f9; }
          #docx-viewport { position: absolute; inset: 0; overflow: auto; overscroll-behavior: contain; scrollbar-gutter: stable; overflow-anchor: none; }
          #docx-stage { position: relative; }
          #docx-content { position: absolute; top: 0; left: 0; transform-origin: top left; }
          .docx-wrapper { padding: 16px !important; background: #f1f5f9 !important; }
          .docx-wrapper > section.docx { box-sizing: border-box; max-width: 100%; margin-bottom: 16px; }
          img, svg { max-width: 100%; }
        </style></head><body><div id="docx-viewport" tabindex="0" role="region" aria-label="Оригинал документа DOCX">
        <div id="docx-stage"><div id="docx-content">${container.innerHTML}</div></div></div>
        <script nonce="${nonce}">${zoomScript}<\/script></body></html>`
    } catch {
      if (!this.lifetime.signal.aborted) this.error = 'Не удалось загрузить DOCX. Откройте оригинал файла.'
    } finally {
      if (!this.lifetime.signal.aborted) this.loading = false
    }
  },
  unmounted() {
    this.lifetime.abort()
  },
})
</script>

<template>
  <div class="relative flex min-h-0 flex-1 flex-col bg-slate-100" :aria-busy="loading">
    <p v-if="loading" class="p-6 text-slate-500" role="status">Загружаем DOCX…</p>
    <p v-else-if="error" class="bg-red-50 p-6 text-red-700" role="alert">{{ error }}</p>
    <!-- CSP permits only our zoom handler; the frame keeps an isolated origin. -->
    <iframe v-else class="min-h-0 w-full flex-1 border-0" :srcdoc="preview" sandbox="allow-scripts"
            title="Оригинал документа DOCX"/>
  </div>
</template>
