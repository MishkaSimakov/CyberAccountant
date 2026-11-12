<script setup lang="ts">
import {computed, nextTick, onMounted, onUnmounted, ref, useId, watch} from 'vue'
import {Marked, type Tokens} from 'marked'

const props = defineProps<{ content: string }>()
const popup = ref<HTMLDivElement | null>(null)
const closeButton = ref<HTMLButtonElement | null>(null)
const popupId = useId()
const attributeNames = ['id', 'type', 'value', 'unit'] as const
interface Entity {
  text: string
  attributes: Record<string, string | null>
}
const selected = ref<Entity | null>(null)
const position = ref({left: '0px', top: '0px'})
let anchor: HTMLButtonElement | null = null

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
  function tokenizeEntity(src: string) {
    const match = /^<entity\b((?:"[^"]*"|'[^']*'|[^'">])*)>([\s\S]*?)<\/entity\s*>/i.exec(src)
    if (!match) return
    const attributes: Entity['attributes'] = {}
    for (const attribute of match[1]!.matchAll(/([\w-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')/g)) {
      attributes[attribute[1]!.toLowerCase()] = decodeText(attribute[2] ?? attribute[3] ?? '')
    }
    return {type: 'entity', raw: match[0], text: decodeText(match[2]!), attributes}
  }
  function renderEntity(token: Tokens.Generic) {
    const index = entities.push({text: token.text, attributes: token.attributes}) - 1
    return `<button type="button" class="entity-highlight" data-entity-index="${index}" aria-haspopup="dialog" aria-controls="${escapeHtml(popupId)}" aria-expanded="false" title="Показать атрибуты сущности">${escapeHtml(token.text)}</button>`
  }
  const parser = new Marked({
    gfm: true,
    renderer: {
      html: ({text}) => escapeHtml(text),
      link({href, title, tokens}) {
        const text = this.parser.parseInline(tokens)
        if (!['http:', 'https:', 'mailto:'].includes(new URL(href, window.location.href).protocol)) return text
        return `<a href="${escapeHtml(href)}" title="${escapeHtml(title ?? '')}" target="_blank" rel="noopener noreferrer">${text}</a>`
      },
    },
    extensions: [
      {name: 'entity', level: 'inline', start: src => src.search(/<entity\b/i), tokenizer: tokenizeEntity, renderer: renderEntity},
      {name: 'blockEntity', level: 'block', tokenizer(src) {
        const token = tokenizeEntity(src)
        if (token) return {...token, type: 'blockEntity'}
      }, renderer: token => `<p>${renderEntity(token)}</p>`},
    ],
  })
  return {html: parser.parse(props.content, {async: false}), entities}
})

function dismiss(restoreFocus = false) {
  popup.value?.hidePopover()
  if (restoreFocus) anchor?.focus({preventScroll: true})
}

function onToggle(event: ToggleEvent) {
  if (event.newState === 'closed') {
    anchor?.setAttribute('aria-expanded', 'false')
    anchor = null
    selected.value = null
  }
}

async function inspect(event: MouseEvent) {
  const button = (event.target as Element).closest<HTMLButtonElement>('button[data-entity-index]')
  if (!button) return
  const entity = rendered.value.entities[Number(button.dataset.entityIndex)]
  if (!entity) return
  if (anchor === button && popup.value?.matches(':popover-open')) {
    dismiss(true)
    return
  }
  anchor?.setAttribute('aria-expanded', 'false')
  anchor = button
  selected.value = entity
  await nextTick()
  if (!popup.value || !button.isConnected) return
  popup.value.showPopover()
  button.setAttribute('aria-expanded', 'true')
  const rect = button.getBoundingClientRect()
  const {width, height} = popup.value.getBoundingClientRect()
  const left = Math.max(12, Math.min(rect.left, window.innerWidth - width - 12))
  const top = rect.bottom + height + 8 <= window.innerHeight - 12
      ? rect.bottom + 8 : Math.max(12, rect.top - height - 8)
  position.value = {left: `${left}px`, top: `${top}px`}
  closeButton.value?.focus({preventScroll: true})
}

const onResize = () => dismiss()
watch(() => props.content, () => dismiss())
onMounted(() => window.addEventListener('resize', onResize))
onUnmounted(() => window.removeEventListener('resize', onResize))
</script>

<template>
  <div class="flex min-h-0 flex-1 flex-col">
    <div class="markdown-viewer min-h-0 flex-1 overflow-auto overscroll-contain p-4 leading-relaxed"
         aria-label="Текст документа" @click="inspect" @scroll.passive="dismiss()" v-html="rendered.html"/>
    <div :id="popupId" ref="popup" popover="auto" role="dialog" aria-label="Атрибуты сущности"
         class="fixed m-0 max-h-[calc(100dvh-24px)] w-80 max-w-[calc(100vw-24px)] overflow-auto rounded-lg border border-slate-200 bg-white p-4 text-sm text-slate-800 shadow-xl"
         :style="position" @toggle="onToggle" @keydown.esc.stop.prevent="dismiss(true)">
      <div class="mb-3 flex items-center justify-between gap-3">
        <h4 class="font-semibold">Атрибуты сущности</h4>
        <button ref="closeButton" type="button"
                class="cursor-pointer rounded px-2 py-1 text-slate-500 hover:bg-slate-100 focus-visible:outline-blue-500"
                aria-label="Закрыть атрибуты" @click="dismiss(true)">✕
        </button>
      </div>
      <template v-if="selected">
        <p class="mb-3 break-words rounded bg-amber-100 px-2 py-1">{{ selected.text }}</p>
        <dl class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2">
          <template v-for="name in attributeNames" :key="name">
            <dt class="font-mono text-slate-500">{{ name }}</dt>
            <dd class="whitespace-pre-wrap break-words font-mono">{{ selected.attributes[name] ?? '—' }}</dd>
          </template>
        </dl>
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
