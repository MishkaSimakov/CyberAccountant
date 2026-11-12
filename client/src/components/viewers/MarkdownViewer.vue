<script lang="ts">
import {defineComponent, markRaw} from 'vue'
import {Marked, type Token, type Tokens} from 'marked'
import {sourceRanges, textOffsets, type SourceRange} from './entitySource'

interface Entity {
  text: string
  body: string
  attributes: Record<string, string | null>
  range?: SourceRange
  isNew?: boolean
}

interface TextSegment extends SourceRange {
  text: string
  offsets: number[]
}

function escapeHtml(text: string) {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;')
}

function decodeText(text: string) {
  const textarea = document.createElement('textarea')
  textarea.innerHTML = text.replace(/</g, '&lt;').replace(/>/g, '&gt;')
  return textarea.value
}

let nextPopupId = 0

export default defineComponent({
  props: {
    content: {type: String, required: true},
    readonly: {type: Boolean, default: true},
    entities: Boolean,
    selectedEntityId: Number,
  },
  emits: {'update:content': (_content: string) => true},
  data() {
    return {
      popupId: `entity-popup-${++nextPopupId}`,
      attributeNames: ['id', 'type', 'value', 'unit'] as const,
      selected: null as Entity | null,
      selection: null as Entity | null,
      selectionError: '',
      draftType: 'id',
      draftValue: '',
      draftUnit: 'string',
      position: {left: '0px', top: '0px'},
      anchor: null as HTMLElement | null,
      selectionRect: null as DOMRect | null,
      selectionPointerId: null as number | null,
      selectionTimer: undefined as ReturnType<typeof setTimeout> | undefined,
    }
  },
  computed: {
    units(): string[] {
      return this.draftType === 'number' ? ['rubles', 'euros', 'kg']
          : this.draftType === 'date' ? ['date'] : ['string']
    },
    valueError(): string {
      if (!this.draftValue.trim()) return 'Укажите значение.'
      if (this.draftType === 'number' && !/^-?\d+(?:\.\d+)?$/.test(this.draftValue)) {
        return 'Укажите число с точкой, без пробелов: 1015500.00.'
      }
      if (this.draftType === 'date') {
        const value = this.draftValue
        if (!/^\d{4}(?:-\d{2}){0,2}$/.test(value)) return 'Используйте формат YYYY, YYYY-MM или YYYY-MM-DD.'
        const full = value.length === 4 ? `${value}-01-01` : value.length === 7 ? `${value}-01` : value
        const date = new Date(`${full}T00:00:00Z`)
        if (value.startsWith('0000') || Number.isNaN(date.getTime()) || date.toISOString().slice(0, 10) !== full) {
          return 'Укажите существующую дату.'
        }
      }
      return ''
    },
    rendered(): { html: string; entities: Entity[]; segments: TextSegment[] } {
      const content = this.content
      const showEntities = this.entities
      const popupId = this.popupId
      const selectedEntityId = this.selectedEntityId

      const entities: Entity[] = []
      const segments: TextSegment[] = []
      let ranges = new Map<Token, SourceRange>()

      function tokenizeEntity(src: string) {
        const match = /^<entity\b((?:"[^"]*"|'[^']*'|[^'">])*)>([\s\S]*?)<\/entity\s*>/i.exec(src)
        if (!match) return
        const attributes: Entity['attributes'] = {}
        for (const attribute of match[1]!.matchAll(/([\w-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')/g)) {
          attributes[attribute[1]!.toLowerCase()] = decodeText(attribute[2] ?? attribute[3] ?? '')
        }
        return {type: 'entity', raw: match[0], text: decodeText(match[2]!), body: match[2]!, attributes}
      }

      function renderEntity(token: Tokens.Generic) {
        if (!showEntities) return escapeHtml(token.text)
        const start = content.indexOf(token.raw)
        const uniqueRange = start >= 0 && content.indexOf(token.raw, start + 1) < 0
            ? {start, end: start + token.raw.length} : undefined
        const index = entities.push({
          text: token.text,
          body: token.body,
          attributes: token.attributes,
          range: ranges.get(token) ?? uniqueRange
        }) - 1
        const selected = selectedEntityId !== undefined && token.attributes.id === String(selectedEntityId)
        return `<button type="button" class="entity-highlight${selected ? ' entity-selected' : ''}" data-entity-index="${index}" aria-haspopup="dialog" aria-controls="${escapeHtml(popupId)}" aria-expanded="false" title="Атрибуты сущности">${escapeHtml(token.text)}</button>`
      }

      const parser = new Marked({
        gfm: true,
        renderer: {
          html: ({text}) => escapeHtml(text),
          text(token) {
            if ('tokens' in token && token.tokens) return this.parser.parseInline(token.tokens)
            const range = ranges.get(token)
            const decoded = decodeText(token.text)
            const html = escapeHtml(decoded)
            if (!showEntities || !range || token.raw !== token.text) return html
            const segment = {...range, ...textOffsets(token.raw, decodeText)}
            const index = segments.push(segment) - 1
            return `<span data-source-index="${index}">${html}</span>`
          },
          link({href, title, tokens}) {
            const text = this.parser.parseInline(tokens)
            if (!['http:', 'https:', 'mailto:'].includes(new URL(href, window.location.href).protocol)) return text
            return `<a href="${escapeHtml(href)}" title="${escapeHtml(title ?? '')}" target="_blank" rel="noopener noreferrer">${text}</a>`
          },
        },
        extensions: [
          {
            name: 'entity',
            level: 'inline',
            start: src => src.search(/<entity\b/i),
            tokenizer: tokenizeEntity,
            renderer: renderEntity
          },
          {
            name: 'blockEntity', level: 'block', tokenizer(src) {
              const token = tokenizeEntity(src)
              if (token) return {...token, type: 'blockEntity'}
            }, renderer: token => `<p>${renderEntity(token)}</p>`
          },
        ],
      })
      const tokens = parser.lexer(content)
      if (showEntities) ranges = sourceRanges(content, tokens)
      return {html: parser.parser(tokens), entities, segments}
    },
  },
  watch: {
    'rendered.html'() {
      void this.revealSelectedEntity()
    },
    draftType() {
      if (!this.units.includes(this.draftUnit)) this.draftUnit = this.units[0]!
    },
    content() {
      this.dismiss()
      this.selection = null
      this.selectionError = ''
    },
    readonly() {
      this.dismiss()
    },
    entities() {
      this.dismiss()
    },
  },
  mounted() {
    void this.revealSelectedEntity()
    window.addEventListener('resize', this.onResize)
    document.addEventListener('selectionchange', this.captureSelection)
    document.addEventListener('pointerup', this.finishSelection)
    document.addEventListener('pointercancel', this.finishSelection)
  },
  beforeUnmount() {
    window.removeEventListener('resize', this.onResize)
    document.removeEventListener('selectionchange', this.captureSelection)
    document.removeEventListener('pointerup', this.finishSelection)
    document.removeEventListener('pointercancel', this.finishSelection)
    clearTimeout(this.selectionTimer)
  },
  methods: {
    async revealSelectedEntity() {
      if (this.selectedEntityId === undefined) return
      await this.$nextTick()
      const viewer = this.$refs.viewer as HTMLDivElement | undefined
      const entity = viewer?.querySelector<HTMLButtonElement>('.entity-selected')
      entity?.scrollIntoView({block: 'center'})
      entity?.focus({preventScroll: true})
    },
    onResize() {
      this.dismiss()
    },
    dismiss(restoreFocus = false) {
      const popup = this.$refs.popup as HTMLDivElement | undefined
      const viewer = this.$refs.viewer as HTMLDivElement | undefined
      popup?.hidePopover()
      if (restoreFocus) (this.anchor ?? viewer)?.focus({preventScroll: true})
      this.resetPopup()
    },
    resetPopup() {
      this.anchor?.setAttribute('aria-expanded', 'false')
      this.anchor = null
      this.selected = null
      this.selection = null
      this.selectionRect = null
    },
    onToggle(event: ToggleEvent) {
      const popup = this.$refs.popup as HTMLDivElement | undefined
      if (event.newState === 'closed' && !popup?.matches(':popover-open')) {
        this.resetPopup()
      }
    },
    positionPopup(rect: DOMRect) {
      const popup = this.$refs.popup as HTMLDivElement | undefined
      if (!popup) return
      const {width, height} = popup.getBoundingClientRect()
      const left = Math.max(12, Math.min(rect.left, window.innerWidth - width - 12))
      const top = rect.bottom + height + 8 <= window.innerHeight - 12
          ? rect.bottom + 8 : Math.max(12, rect.top - height - 8)
      this.position = {left: `${left}px`, top: `${top}px`}
    },
    async open(entity: Entity, button: HTMLElement | null, rect: DOMRect) {
      this.anchor?.setAttribute('aria-expanded', 'false')
      this.anchor = button
      this.selected = markRaw(entity)
      this.selection = null
      this.draftType = ['id', 'date', 'number'].includes(entity.attributes.type ?? '') ? entity.attributes.type! : 'id'
      this.draftValue = entity.attributes.value ?? entity.text
      this.draftUnit = this.units.includes(entity.attributes.unit ?? '') ? entity.attributes.unit! : this.units[0]!
      await this.$nextTick()
      const popup = this.$refs.popup as HTMLDivElement | undefined
      const closeButton = this.$refs.closeButton as HTMLButtonElement | undefined
      const valueInput = this.$refs.valueInput as HTMLInputElement | undefined
      if (!popup || this.selected !== entity || (button && !button.isConnected)) return
      popup.showPopover()
      button?.setAttribute('aria-expanded', 'true')
      this.positionPopup(rect)
      const focusTarget = this.readonly ? closeButton : valueInput
      focusTarget?.focus({preventScroll: true})
    },
    inspect(event: MouseEvent) {
      const popup = this.$refs.popup as HTMLDivElement | undefined
      if (!this.entities) return
      if (event.detail && window.getSelection()?.toString()) return
      const button = (event.target as Element).closest<HTMLButtonElement>('button[data-entity-index]')
      if (!button) return
      const entity = this.rendered.entities[Number(button.dataset.entityIndex)]
      if (!entity) return
      if (this.anchor === button && popup?.matches(':popover-open')) {
        this.dismiss(true)
        return
      }
      void this.open(entity, button, button.getBoundingClientRect())
    },
    async captureSelection() {
      const viewer = this.$refs.viewer as HTMLDivElement | undefined
      if (!this.entities || this.readonly || this.selected || this.selectionPointerId !== null || this.selectionTimer !== undefined) return
      this.dismiss()
      this.selectionError = ''
      const current = window.getSelection()
      if (!current?.rangeCount || current.isCollapsed || !current.toString().trim()) return
      const range = current.getRangeAt(0)
      if (!viewer?.contains(range.commonAncestorContainer)) return
      const start = range.startContainer
      const span = start.parentElement?.closest<HTMLElement>('[data-source-index]')
      if (start.nodeType !== Node.TEXT_NODE || start !== range.endContainer || !span) {
        this.selectionError = 'Выделите фрагмент внутри одного участка текста, без пересечения сущностей и форматирования.'
        return
      }
      const segment = this.rendered.segments[Number(span.dataset.sourceIndex)]
      if (!segment || start.textContent !== segment.text) return
      const from = segment.offsets[range.startOffset]
      const to = segment.offsets[range.endOffset]
      if (from === undefined || to === undefined || from === to) return
      const entity: Entity = {
        text: current.toString(), body: escapeHtml(current.toString()), attributes: {},
        range: {start: segment.start + from, end: segment.start + to},
        isNew: true,
      }
      this.selection = markRaw(entity)
      this.selectionRect = range.getBoundingClientRect()
      await this.$nextTick()
      const popup = this.$refs.popup as HTMLDivElement | undefined
      if (!popup || this.selection !== entity || this.selected) return
      popup.showPopover()
      this.positionPopup(this.selectionRect)
    },
    beginSelection(event: PointerEvent) {
      if (!this.entities || this.readonly || event.button !== 0) return
      clearTimeout(this.selectionTimer)
      this.selectionTimer = undefined
      this.selectionPointerId = event.pointerId
      this.dismiss()
    },
    finishSelection(event: PointerEvent) {
      if (this.selectionPointerId !== event.pointerId) return
      this.selectionPointerId = null
      // Opening during the drag lets the release click light-dismiss the new popover.
      // Wait until that click has completed before showing the selection action.
      this.selectionTimer = setTimeout(() => {
        this.selectionTimer = undefined
        void this.captureSelection()
      }, 0)
    },
    addEntity() {
      if (!this.entities || this.readonly || !this.selection || !this.selectionRect) return
      void this.open(this.selection, null, this.selectionRect)
    },
    apply() {
      const entity = this.selected
      if (!this.entities || this.readonly || !entity?.range || this.valueError) return
      const id = entity.attributes.id ? ` id="${escapeHtml(entity.attributes.id)}"` : ''
      const markup = `<entity type="${this.draftType}" value="${escapeHtml(this.draftValue)}" unit="${this.draftUnit}"${id}>${entity.body}</entity>`
      this.replaceEntity(markup)
    },
    removeEntity() {
      const entity = this.selected
      if (!entity || entity.isNew) return
      this.replaceEntity(entity.body)
    },
    replaceEntity(markup: string) {
      const viewer = this.$refs.viewer as HTMLDivElement | undefined
      const entity = this.selected
      if (!this.entities || this.readonly || !entity?.range) return
      this.dismiss()
      window.getSelection()?.removeAllRanges()
      this.$emit('update:content', this.content.slice(0, entity.range.start) + markup + this.content.slice(entity.range.end))
      viewer?.focus({preventScroll: true})
    },
  },
})
</script>

<template>
  <div class="flex min-h-0 flex-1 flex-col">
    <div ref="viewer" tabindex="-1"
         class="markdown-viewer min-h-0 flex-1 overflow-auto overscroll-contain p-4 leading-relaxed"
         aria-label="Текст документа" @pointerdown="beginSelection" @click="inspect" @scroll.passive="dismiss()"
         v-html="rendered.html"/>
    <div v-if="entities" :id="popupId" ref="popup" popover="auto" :role="selected ? 'dialog' : 'toolbar'"
         :aria-label="selected ? 'Атрибуты сущности' : 'Действия с выделенным текстом'"
         class="fixed m-0 max-h-[calc(100dvh-24px)] max-w-[calc(100vw-24px)] overflow-auto rounded-xl border border-slate-200 bg-white text-sm text-slate-800 shadow-xl"
         :class="selected ? 'w-80 p-4' : 'p-1.5'"
         :style="position" @toggle="onToggle" @keydown.esc.stop.prevent="dismiss(true)">
      <button v-if="!selected && selection" type="button" aria-haspopup="dialog" :aria-controls="popupId"
              class="inline-flex cursor-pointer items-center gap-2 rounded-lg bg-blue-600 px-3 py-2 font-medium text-white transition-colors hover:bg-blue-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
              @pointerdown.prevent @click="addEntity">
        <svg class="size-4" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.75"
             aria-hidden="true">
          <path d="M10 4v12M4 10h12" stroke-linecap="round"/>
        </svg>
        Добавить сущность
      </button>
      <template v-if="selected">
        <div class="mb-3 flex items-center justify-between gap-3">
          <h4 class="font-semibold">{{ selected.isNew ? 'Новая сущность' : 'Атрибуты сущности' }}</h4>
          <button ref="closeButton" type="button"
                  class="cursor-pointer rounded px-2 py-1 text-slate-500 hover:bg-slate-100 focus-visible:outline-blue-500"
                  aria-label="Закрыть атрибуты" @click="dismiss(true)">✕
          </button>
        </div>
        <p class="mb-3 break-words rounded bg-amber-100 px-2 py-1">{{ selected.text }}</p>
        <dl v-if="readonly" class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2">
          <template v-for="name in attributeNames" :key="name">
            <dt class="font-mono text-slate-500">{{ name }}</dt>
            <dd class="whitespace-pre-wrap break-words font-mono">{{ selected.attributes[name] ?? '—' }}</dd>
          </template>
        </dl>
        <form v-else class="space-y-3" @submit.prevent="apply">
          <label class="block">Тип
            <span class="relative mt-1 block">
            <select v-model="draftType"
                    class="w-full cursor-pointer appearance-none rounded-lg border border-slate-300 bg-slate-50 py-2.5 pl-3 pr-10 text-sm text-slate-800 shadow-sm transition-colors hover:border-slate-400 focus:border-blue-500 focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-500/20">
              <option value="id">Идентификатор (id)</option>
              <option value="date">Дата (date)</option>
              <option value="number">Число (number)</option>
            </select>
              <svg class="pointer-events-none absolute right-3 top-1/2 size-4 -translate-y-1/2 text-slate-500"
                   viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.75" aria-hidden="true">
                <path d="m6 8 4 4 4-4" stroke-linecap="round" stroke-linejoin="round"/>
              </svg>
            </span>
          </label>
          <label class="block">Значение
            <input ref="valueInput" v-model="draftValue" required :aria-invalid="!!valueError"
                   :aria-describedby="`${popupId}-error`"
                   :inputmode="draftType === 'number' ? 'decimal' : 'text'"
                   class="mt-1 w-full rounded-lg border border-slate-300 bg-slate-50 px-3 py-2.5 text-sm shadow-sm transition-colors hover:border-slate-400 focus:border-blue-500 focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-500/20"/>
          </label>
          <label class="block">Единица
            <span class="relative mt-1 block">
            <select v-model="draftUnit"
                    class="w-full cursor-pointer appearance-none rounded-lg border border-slate-300 bg-slate-50 py-2.5 pl-3 pr-10 text-sm text-slate-800 shadow-sm transition-colors hover:border-slate-400 focus:border-blue-500 focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-500/20">
              <option v-for="unit in units" :key="unit" :value="unit">{{ unit }}</option>
            </select>
              <svg class="pointer-events-none absolute right-3 top-1/2 size-4 -translate-y-1/2 text-slate-500"
                   viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.75" aria-hidden="true">
                <path d="m6 8 4 4 4-4" stroke-linecap="round" stroke-linejoin="round"/>
              </svg>
            </span>
          </label>
          <p v-if="valueError" :id="`${popupId}-error`" class="text-xs text-red-700" role="status">{{ valueError }}</p>
          <p v-if="!selected.range" class="text-xs text-red-700" role="alert">Не удалось определить положение сущности в
            исходном тексте.</p>
          <div class="flex justify-end gap-2">
            <button v-if="!selected.isNew" type="button" :disabled="!selected.range"
                    class="cursor-pointer rounded bg-red-600 px-3 py-2 text-white hover:bg-red-700 disabled:cursor-not-allowed disabled:opacity-50"
                    aria-label="Удалить сущность" @click="removeEntity">Удалить
            </button>
            <button type="submit" :disabled="!!valueError || !selected.range"
                    class="cursor-pointer rounded bg-blue-600 px-3 py-2 text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50">
              Сохранить
            </button>
          </div>
        </form>
      </template>
    </div>
  </div>
</template>

<style scoped>
.markdown-viewer :deep(h1), .markdown-viewer :deep(h2), .markdown-viewer :deep(h3),
.markdown-viewer :deep(h4), .markdown-viewer :deep(h5), .markdown-viewer :deep(h6) {
  margin: 1.25em 0 .6em;
  font-weight: 600;
  line-height: 1.3;
}

.markdown-viewer :deep(h1) {
  font-size: 1.6em;
}

.markdown-viewer :deep(h2) {
  font-size: 1.35em;
}

.markdown-viewer :deep(h3) {
  font-size: 1.15em;
}

.markdown-viewer :deep(> :first-child) {
  margin-top: 0;
}

.markdown-viewer :deep(p), .markdown-viewer :deep(ul), .markdown-viewer :deep(ol),
.markdown-viewer :deep(blockquote), .markdown-viewer :deep(pre), .markdown-viewer :deep(table) {
  margin-bottom: 1em;
}

.markdown-viewer :deep(ul) {
  list-style: disc;
  padding-left: 1.5em;
}

.markdown-viewer :deep(ol) {
  list-style: decimal;
  padding-left: 1.5em;
}

.markdown-viewer :deep(blockquote) {
  border-left: 3px solid #cbd5e1;
  padding-left: 1em;
  color: #475569;
}

.markdown-viewer :deep(pre) {
  overflow: auto;
  background: #f1f5f9;
  padding: .75em;
  border-radius: .25em;
}

.markdown-viewer :deep(code) {
  background: #f1f5f9;
  border-radius: .2em;
  padding: .1em .25em;
}

.markdown-viewer :deep(a) {
  color: #1d4ed8;
  text-decoration: underline;
}

.markdown-viewer :deep(hr) {
  border-color: #e2e8f0;
  margin: 1em 0;
}

.markdown-viewer :deep(table) {
  width: 100%;
  border-collapse: collapse;
  font-size: .9em;
}

.markdown-viewer :deep(th), .markdown-viewer :deep(td) {
  border: 1px solid #e2e8f0;
  padding: .6em .75em;
  text-align: left;
}

.markdown-viewer :deep(th) {
  background: #f8fafc;
  font-weight: 600;
}

.markdown-viewer :deep(.entity-highlight) {
  display: inline;
  border-radius: .2em;
  padding: .1em .15em;
  background: #fef08a;
  color: inherit;
  font: inherit;
  text-align: inherit;
  cursor: pointer;
  box-decoration-break: clone;
  -webkit-box-decoration-break: clone;
}

.markdown-viewer :deep(.entity-highlight:hover),
.markdown-viewer :deep(.entity-highlight[aria-expanded="true"]) {
  background: #fde047;
}

.markdown-viewer :deep(.entity-highlight:focus-visible) {
  outline: 2px solid #2563eb;
  outline-offset: 2px;
}

.markdown-viewer :deep(.entity-highlight.entity-selected) {
  background: #fdba74;
  outline: 2px solid #ea580c;
  outline-offset: 2px;
}
</style>
