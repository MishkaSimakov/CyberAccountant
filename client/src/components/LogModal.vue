<script lang="ts">
import {defineComponent} from 'vue'
import {FontAwesomeIcon} from '@fortawesome/vue-fontawesome'
import {faXmark} from '@fortawesome/free-solid-svg-icons/faXmark'

// Bound the rendered text even when the full log is many megabytes long.
const PAGE_SIZE = 50_000

export default defineComponent({
  components: {FontAwesomeIcon},
  props: {
    src: {type: String, required: true},
    title: {type: String, default: 'Журнал обработки'},
    filename: {type: String, default: 'processing.log'},
  },
  emits: ['close'],
  data() {
    return {
      faXmark,
      log: '',
      loading: true,
      loadError: '',
      controller: null as AbortController | null,
      page: 0,
      restorePageScroll: null as (() => void) | null,
    }
  },
  computed: {
    pageCount(): number {
      return Math.max(1, Math.ceil(this.log.length / PAGE_SIZE))
    },
    visibleLog(): string {
      // Keep surrogate pairs together at page boundaries.
      const boundary = (offset: number) => {
        const previous = this.log.charCodeAt(offset - 1)
        const next = this.log.charCodeAt(offset)
        return previous >= 0xD800 && previous <= 0xDBFF && next >= 0xDC00 && next <= 0xDFFF ? offset - 1 : offset
      }
      const end = this.log.length - (this.pageCount - this.page - 1) * PAGE_SIZE
      return this.log.slice(boundary(Math.max(0, end - PAGE_SIZE)), boundary(end))
    },
  },
  watch: {
    src() {
      void this.load()
    },
  },
  mounted() {
    const overflow = document.documentElement.style.overflow
    document.documentElement.style.overflow = 'hidden'
    this.restorePageScroll = () => {
      document.documentElement.style.overflow = overflow
    }
    const dialog = this.$refs.dialog as HTMLDialogElement
    dialog.showModal()
    void this.load()
  },
  beforeUnmount() {
    this.controller?.abort()
    const dialog = this.$refs.dialog as HTMLDialogElement
    dialog.close()
    this.restorePageScroll?.()
  },
  methods: {
    async load() {
      this.controller?.abort()
      const controller = new AbortController()
      this.controller = controller
      this.loading = true
      this.loadError = ''
      try {
        const response = await fetch(this.src, {signal: controller.signal, cache: 'no-store'})
        if (!response.ok) throw new Error('Не удалось загрузить журнал. Повторите попытку.')
        const log = await response.text()
        if (controller.signal.aborted) return
        this.log = log
        this.page = this.pageCount - 1
        this.loading = false
        await this.$nextTick()
        const output = this.$refs.output as HTMLElement | undefined
        output?.scrollTo(0, output.scrollHeight)
      } catch (cause) {
        if (!controller.signal.aborted) {
          this.loadError = cause instanceof Error ? cause.message : 'Не удалось загрузить журнал.'
        }
      } finally {
        if (!controller.signal.aborted) this.loading = false
      }
    },
    async changePage(page: number) {
      this.page = page
      await this.$nextTick()
      const output = this.$refs.output as HTMLElement | undefined
      output?.scrollTo(0, 0)
    },
  },
})
</script>

<template>
  <Teleport to="body">
    <dialog ref="dialog" :aria-label="title" @cancel.prevent="$emit('close')"
            class="m-auto h-[85dvh] max-h-[calc(100dvh-2rem)] w-[calc(100%-2rem)] max-w-6xl overflow-hidden rounded-lg border border-slate-200 bg-white p-0 text-slate-800 shadow-xl backdrop:bg-slate-900/50 open:flex open:flex-col">
      <header class="flex shrink-0 items-center justify-between gap-4 border-b border-slate-200 px-5 py-4">
        <h2 class="min-w-0 break-words text-lg font-semibold">{{ title }}</h2>
        <button type="button" autofocus aria-label="Закрыть" title="Закрыть"
                class="inline-flex size-9 shrink-0 cursor-pointer items-center justify-center rounded border border-slate-300 hover:bg-slate-50 disabled:opacity-50"
                @click="$emit('close')">
          <FontAwesomeIcon :icon="faXmark" class="size-4" aria-hidden="true"/>
        </button>
      </header>
      <p v-if="loading" class="min-h-0 flex-1 p-5 text-sm text-slate-500" role="status">Загружаем журнал…</p>
      <div v-else-if="loadError" class="min-h-0 flex-1 p-5 text-sm">
        <p class="text-red-700" role="alert">{{ loadError }}</p>
        <button type="button" class="mt-3 cursor-pointer text-blue-700 hover:underline" @click="load">Повторить</button>
      </div>
      <p v-else-if="!log" class="min-h-0 flex-1 p-5 text-sm text-slate-500">Журнал пока пуст.</p>
      <pre v-else ref="output" tabindex="0" aria-label="Содержимое журнала" dir="ltr"
           class="m-0 min-h-0 flex-1 overflow-auto overscroll-contain bg-slate-950 p-4 font-mono text-xs leading-5 whitespace-pre text-slate-100"
           v-text="visibleLog"/>
      <footer class="flex shrink-0 flex-wrap items-center justify-between gap-3 border-t border-slate-200 px-5 py-3">
        <div v-if="!loading && !loadError && pageCount > 1" class="flex flex-wrap items-center gap-3 text-sm">
          <button type="button" :disabled="page === 0"
                  class="cursor-pointer rounded border border-slate-300 px-3 py-2 hover:bg-slate-50 disabled:cursor-default disabled:opacity-50"
                  @click="changePage(page - 1)">Назад
          </button>
          <span role="status" aria-live="polite">Часть {{ page + 1 }} из {{ pageCount }}</span>
          <button type="button" :disabled="page + 1 >= pageCount"
                  class="cursor-pointer rounded border border-slate-300 px-3 py-2 hover:bg-slate-50 disabled:cursor-default disabled:opacity-50"
                  @click="changePage(page + 1)">Далее
          </button>
        </div>
        <a :href="src" :download="filename"
           class="ml-auto cursor-pointer rounded border border-slate-300 px-3 py-2 text-sm hover:bg-slate-50">Скачать полный журнал</a>
      </footer>
    </dialog>
  </Teleport>
</template>
