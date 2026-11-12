<script lang="ts">
import {defineComponent, markRaw, type Raw} from 'vue'
import {read, utils, type WorkBook} from 'xlsx'

export default defineComponent({
  props: {src: {type: String, required: true}},
  data() {
    return {
      loading: true,
      error: '',
      workbook: undefined as Raw<WorkBook> | undefined,
      selectedSheet: 0,
      lifetime: markRaw(new AbortController()),
    }
  },
  computed: {
    sheetNames(): string[] {
      return this.workbook?.SheetNames.filter((_, index) => !this.workbook?.Workbook?.Sheets?.[index]?.Hidden) ?? []
    },
    preview(): string {
      const name = this.sheetNames[this.selectedSheet]
      const sheet = name ? this.workbook?.Sheets[name] : undefined
      const header = `<!doctype html><html lang="ru"><head><meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'none'; style-src 'unsafe-inline';">
        <style>
          body { margin: 0; padding: 16px; font: 14px/1.5 system-ui, sans-serif; color: #1e293b; }
          table { border-collapse: collapse; background: white; }
          td { min-width: 80px; padding: 6px 12px; border: 1px solid #e2e8f0; vertical-align: top; white-space: pre-wrap; }
          td[data-t="n"] { text-align: right; font-variant-numeric: tabular-nums; }
          a { color: #1d4ed8; }
          p { color: #64748b; }
        </style></head><body>`
      const footer = '</body></html>'
      // SheetJS renders formatted cell values and merged cells; the frame isolates document HTML.
      return sheet?.['!ref'] ? utils.sheet_to_html(sheet, {header, footer})
          : `${header}<p>Лист пуст.</p>${footer}`
    },
  },
  async mounted() {
    try {
      const response = await fetch(this.src, {signal: this.lifetime.signal})
      if (!response.ok) throw new Error('Failed to load XLSX')
      const data = await response.arrayBuffer()
      if (this.lifetime.signal.aborted) return
      this.workbook = markRaw(read(data, {type: 'array', cellHTML: false}))
    } catch {
      if (!this.lifetime.signal.aborted) this.error = 'Не удалось загрузить XLSX. Откройте оригинал файла.'
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
  <div class="flex min-h-0 min-w-0 flex-1 flex-col bg-slate-50" :aria-busy="loading">
    <p v-if="loading" class="p-6 text-slate-500" role="status">Загружаем XLSX…</p>
    <p v-else-if="error" class="bg-red-50 p-6 text-red-700" role="alert">{{ error }}</p>
    <template v-else>
      <!-- CSP blocks document scripts; allow-scripts avoids WebKit blocking frame events. -->
      <iframe :key="selectedSheet" class="min-h-0 w-full flex-1 border-0" :srcdoc="preview" sandbox="allow-scripts"
              :title="`Оригинал документа XLSX: ${sheetNames[selectedSheet] ?? ''}`"/>
      <div v-if="sheetNames.length" class="flex shrink-0 gap-1 overflow-x-auto border-t border-slate-200 bg-white p-2"
           role="group" aria-label="Листы книги">
        <button v-for="(name, index) in sheetNames" :key="name" type="button"
                class="shrink-0 cursor-pointer rounded px-3 py-1.5 hover:bg-slate-100"
                :class="selectedSheet === index ? 'bg-blue-50 text-blue-700' : 'text-slate-500'"
                :aria-pressed="selectedSheet === index" @click="selectedSheet = index">
          {{ name }}
        </button>
      </div>
    </template>
  </div>
</template>
