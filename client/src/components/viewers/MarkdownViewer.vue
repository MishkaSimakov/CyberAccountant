<script setup lang="ts">
import {computed, nextTick, onMounted, onUnmounted, ref, shallowRef, useId, watch} from 'vue'
import {Marked, type Token, type Tokens} from 'marked'
import {sourceRanges, textOffsets, type SourceRange} from './entitySource'

const props = withDefaults(defineProps<{ content: string; readonly?: boolean; entities?: boolean }>(), {
  readonly: true,
  entities: false,
})
const emit = defineEmits<{ 'update:content': [content: string] }>()
const viewer = ref<HTMLDivElement | null>(null)
const popup = ref<HTMLDivElement | null>(null)
const closeButton = ref<HTMLButtonElement | null>(null)
const valueInput = ref<HTMLInputElement | null>(null)
const popupId = useId()
const attributeNames = ['id', 'type', 'value', 'unit'] as const

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

const selected = shallowRef<Entity | null>(null)
const selection = shallowRef<Entity | null>(null)
const selectionError = ref('')
const draftType = ref('id')
const draftValue = ref('')
const draftUnit = ref('string')
const position = ref({left: '0px', top: '0px'})
let anchor: HTMLElement | null = null
let selectionRect: DOMRect | null = null
let selectionPointerId: number | null = null
let selectionTimer: ReturnType<typeof setTimeout> | undefined

const units = computed(() => draftType.value === 'number' ? ['rubles', 'euros', 'kg']
    : draftType.value === 'date' ? ['date'] : ['string'])
watch(draftType, () => {
  if (!units.value.includes(draftUnit.value)) draftUnit.value = units.value[0]!
})
const valueError = computed(() => {
  if (!draftValue.value.trim()) return 'Укажите значение.'
  if (draftType.value === 'number' && !/^-?\d+(?:\.\d+)?$/.test(draftValue.value)) {
    return 'Укажите число с точкой, без пробелов: 1015500.00.'
  }
  if (draftType.value === 'date') {
    const value = draftValue.value
    if (!/^\d{4}(?:-\d{2}){0,2}$/.test(value)) return 'Используйте формат YYYY, YYYY-MM или YYYY-MM-DD.'
    const full = value.length === 4 ? `${value}-01-01` : value.length === 7 ? `${value}-01` : value
    const date = new Date(`${full}T00:00:00Z`)
    if (value.startsWith('0000') || Number.isNaN(date.getTime()) || date.toISOString().slice(0, 10) !== full) {
      return 'Укажите существующую дату.'
    }
  }
  return ''
})

function escapeHtml(text: string) {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;')
}

function decodeText(text: string) {
  const textarea = document.createElement('textarea')
  textarea.innerHTML = text.replace(/</g, '&lt;').replace(/>/g, '&gt;')
  return textarea.value
}

const rendered = computed(() => {
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
    if (!props.entities) return escapeHtml(token.text)
    const start = props.content.indexOf(token.raw)
    const uniqueRange = start >= 0 && props.content.indexOf(token.raw, start + 1) < 0
        ? {start, end: start + token.raw.length} : undefined
    const index = entities.push({
      text: token.text,
      body: token.body,
      attributes: token.attributes,
      range: ranges.get(token) ?? uniqueRange
    }) - 1
    return `<button type="button" class="entity-highlight" data-entity-index="${index}" aria-haspopup="dialog" aria-controls="${escapeHtml(popupId)}" aria-expanded="false" title="Атрибуты сущности">${escapeHtml(token.text)}</button>`
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
        if (!props.entities || !range || token.raw !== token.text) return html
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
  const tokens = parser.lexer(props.content)
  if (props.entities) ranges = sourceRanges(props.content, tokens)
  return {html: parser.parser(tokens), entities, segments}
})

function dismiss(restoreFocus = false) {
  popup.value?.hidePopover()
  if (restoreFocus) (anchor ?? viewer.value)?.focus({preventScroll: true})
  resetPopup()
}

function resetPopup() {
  anchor?.setAttribute('aria-expanded', 'false')
  anchor = null
  selected.value = null
  selection.value = null
  selectionRect = null
}

function onToggle(event: ToggleEvent) {
  if (event.newState === 'closed' && !popup.value?.matches(':popover-open')) {
    resetPopup()
  }
}

function positionPopup(rect: DOMRect) {
  if (!popup.value) return
  const {width, height} = popup.value.getBoundingClientRect()
  const left = Math.max(12, Math.min(rect.left, window.innerWidth - width - 12))
  const top = rect.bottom + height + 8 <= window.innerHeight - 12
      ? rect.bottom + 8 : Math.max(12, rect.top - height - 8)
  position.value = {left: `${left}px`, top: `${top}px`}
}

async function open(entity: Entity, button: HTMLElement | null, rect: DOMRect) {
  anchor?.setAttribute('aria-expanded', 'false')
  anchor = button
  selected.value = entity
  selection.value = null
  draftType.value = ['id', 'date', 'number'].includes(entity.attributes.type ?? '') ? entity.attributes.type! : 'id'
  draftValue.value = entity.attributes.value ?? entity.text
  draftUnit.value = units.value.includes(entity.attributes.unit ?? '') ? entity.attributes.unit! : units.value[0]!
  await nextTick()
  if (!popup.value || selected.value !== entity || (button && !button.isConnected)) return
  popup.value.showPopover()
  button?.setAttribute('aria-expanded', 'true')
  positionPopup(rect)
  const focusTarget = props.readonly ? closeButton.value : valueInput.value
  focusTarget?.focus({preventScroll: true})
}

function inspect(event: MouseEvent) {
  if (!props.entities) return
  if (event.detail && window.getSelection()?.toString()) return
  const button = (event.target as Element).closest<HTMLButtonElement>('button[data-entity-index]')
  if (!button) return
  const entity = rendered.value.entities[Number(button.dataset.entityIndex)]
  if (!entity) return
  if (anchor === button && popup.value?.matches(':popover-open')) {
    dismiss(true)
    return
  }
  void open(entity, button, button.getBoundingClientRect())
}

async function captureSelection() {
  if (!props.entities || props.readonly || selected.value || selectionPointerId !== null || selectionTimer !== undefined) return
  dismiss()
  selectionError.value = ''
  const current = window.getSelection()
  if (!current?.rangeCount || current.isCollapsed || !current.toString().trim()) return
  const range = current.getRangeAt(0)
  if (!viewer.value?.contains(range.commonAncestorContainer)) return
  const start = range.startContainer
  const span = start.parentElement?.closest<HTMLElement>('[data-source-index]')
  if (start.nodeType !== Node.TEXT_NODE || start !== range.endContainer || !span) {
    selectionError.value = 'Выделите фрагмент внутри одного участка текста, без пересечения сущностей и форматирования.'
    return
  }
  const segment = rendered.value.segments[Number(span.dataset.sourceIndex)]
  if (!segment || start.textContent !== segment.text) return
  const from = segment.offsets[range.startOffset]
  const to = segment.offsets[range.endOffset]
  if (from === undefined || to === undefined || from === to) return
  const entity: Entity = {
    text: current.toString(), body: escapeHtml(current.toString()), attributes: {},
    range: {start: segment.start + from, end: segment.start + to},
    isNew: true,
  }
  selection.value = entity
  selectionRect = range.getBoundingClientRect()
  await nextTick()
  if (!popup.value || selection.value !== entity || selected.value) return
  popup.value.showPopover()
  positionPopup(selectionRect)
}

function beginSelection(event: PointerEvent) {
  if (!props.entities || props.readonly || event.button !== 0) return
  clearTimeout(selectionTimer)
  selectionTimer = undefined
  selectionPointerId = event.pointerId
  dismiss()
}

function finishSelection(event: PointerEvent) {
  if (selectionPointerId !== event.pointerId) return
  selectionPointerId = null
  // Opening during the drag lets the release click light-dismiss the new popover.
  // Wait until that click has completed before showing the selection action.
  selectionTimer = setTimeout(() => {
    selectionTimer = undefined
    void captureSelection()
  }, 0)
}

function addEntity() {
  if (!props.entities || props.readonly || !selection.value || !selectionRect) return
  void open(selection.value, null, selectionRect)
}

function apply() {
  const entity = selected.value
  if (!props.entities || props.readonly || !entity?.range || valueError.value) return
  const id = entity.attributes.id ? ` id="${escapeHtml(entity.attributes.id)}"` : ''
  const markup = `<entity type="${draftType.value}" value="${escapeHtml(draftValue.value)}" unit="${draftUnit.value}"${id}>${entity.body}</entity>`
  replaceEntity(markup)
}

function removeEntity() {
  const entity = selected.value
  if (!entity || entity.isNew) return
  replaceEntity(entity.body)
}

function replaceEntity(markup: string) {
  const entity = selected.value
  if (!props.entities || props.readonly || !entity?.range) return
  dismiss()
  window.getSelection()?.removeAllRanges()
  emit('update:content', props.content.slice(0, entity.range.start) + markup + props.content.slice(entity.range.end))
  viewer.value?.focus({preventScroll: true})
}

const onResize = () => dismiss()
watch(() => props.content, () => {
  dismiss()
  selection.value = null
  selectionError.value = ''
})
watch(() => [props.readonly, props.entities], () => {
  dismiss()
  selection.value = null
})
onMounted(() => {
  window.addEventListener('resize', onResize)
  document.addEventListener('selectionchange', captureSelection)
  document.addEventListener('pointerup', finishSelection)
  document.addEventListener('pointercancel', finishSelection)
})
onUnmounted(() => {
  window.removeEventListener('resize', onResize)
  document.removeEventListener('selectionchange', captureSelection)
  document.removeEventListener('pointerup', finishSelection)
  document.removeEventListener('pointercancel', finishSelection)
  clearTimeout(selectionTimer)
})
</script>

<template>
  <div class="flex min-h-0 flex-1 flex-col">
    <div ref="viewer" tabindex="-1"
         class="markdown-viewer min-h-0 flex-1 overflow-auto overscroll-contain p-4 leading-relaxed"
         aria-label="Текст документа" @pointerdown="beginSelection" @click="inspect" @scroll.passive="dismiss()" v-html="rendered.html"/>
    <div v-if="entities" :id="popupId" ref="popup" popover="auto" :role="selected ? 'dialog' : 'toolbar'"
         :aria-label="selected ? 'Атрибуты сущности' : 'Действия с выделенным текстом'"
         class="fixed m-0 max-h-[calc(100dvh-24px)] max-w-[calc(100vw-24px)] overflow-auto rounded-xl border border-slate-200 bg-white text-sm text-slate-800 shadow-xl"
         :class="selected ? 'w-80 p-4' : 'p-1.5'"
         :style="position" @toggle="onToggle" @keydown.esc.stop.prevent="dismiss(true)">
      <button v-if="!selected && selection" type="button" aria-haspopup="dialog" :aria-controls="popupId"
              class="inline-flex cursor-pointer items-center gap-2 rounded-lg bg-blue-600 px-3 py-2 font-medium text-white transition-colors hover:bg-blue-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
              @pointerdown.prevent @click="addEntity">
        <svg class="size-4" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.75" aria-hidden="true">
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
            <input ref="valueInput" v-model="draftValue" required :aria-invalid="!!valueError" :aria-describedby="`${popupId}-error`"
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
</style>
