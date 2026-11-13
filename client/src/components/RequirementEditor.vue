<script lang="ts">
import {defineComponent, type PropType} from 'vue'
import {FontAwesomeIcon} from '@fortawesome/vue-fontawesome'
import {faXmark} from '@fortawesome/free-solid-svg-icons/faXmark'
import {
  saveRequirementDefinition,
  validateFormula,
  type Requirement,
  type RequirementDefinition,
} from '../api'

export default defineComponent({
  components: {FontAwesomeIcon},
  props: {
    reportId: {type: Number, required: true},
    requirement: {type: Object as PropType<Requirement | null>, default: null},
  },
  emits: {
    saved: (_requirement: Requirement) => true,
    close: () => true,
  },
  data() {
    const item = this.requirement
    return {
      kind: item?.kind ?? 'formula',
      equation: item?.kind === 'formula' ? item.equation : '',
      text: item?.text ?? '',
      condition: item?.kind === 'assertion' ? item.condition === null ? 'unknown' : String(item.condition) : 'unknown',
      baseline: '',
      saving: false,
      validating: false,
      formulaValid: false,
      validationError: '',
      actionError: '',
      validationRequest: 0,
      timer: undefined as ReturnType<typeof setTimeout> | undefined,
      restorePageScroll: undefined as (() => void) | undefined,
      faXmark,
    }
  },
  computed: {
    definition(): RequirementDefinition {
      if (this.kind === 'formula') return {
        kind: 'formula', equation: this.equation.trim(), text: this.text.trim() || null,
        tag: this.requirement?.kind === 'formula' ? this.requirement.tag : null,
      }
      return {
        kind: 'assertion', text: this.text.trim(),
        condition: this.condition === 'unknown' ? null : this.condition === 'true',
        ids_related: this.requirement?.kind === 'assertion' ? this.requirement.ids_related : [],
      }
    },
    dirty(): boolean {
      return JSON.stringify(this.definition) !== this.baseline
    },
    valid(): boolean {
      return this.kind === 'formula' ? this.formulaValid && !this.validating : Boolean(this.text.trim())
    },
  },
  watch: {
    equation() {
      this.scheduleValidation()
    },
    kind() {
      this.scheduleValidation()
    },
  },
  mounted() {
    this.baseline = JSON.stringify(this.definition)
    this.scheduleValidation()
    const rootOverflow = document.documentElement.style.overflow
    const bodyOverflow = document.body.style.overflow
    document.documentElement.style.overflow = 'hidden'
    document.body.style.overflow = 'hidden'
    this.restorePageScroll = () => {
      document.documentElement.style.overflow = rootOverflow
      document.body.style.overflow = bodyOverflow
    }
    const dialog = this.$refs.dialog as HTMLDialogElement
    dialog.showModal()
  },
  beforeUnmount() {
    clearTimeout(this.timer)
    this.validationRequest++
    this.restorePageScroll?.()
  },
  methods: {
    close() {
      if (this.saving) return
      if (this.dirty && !window.confirm('Закрыть требование без сохранения правок?')) return
      this.$emit('close')
    },
    scheduleValidation() {
      clearTimeout(this.timer)
      const request = ++this.validationRequest
      this.formulaValid = false
      this.validationError = ''
      this.validating = false
      if (this.kind !== 'formula' || !this.equation.trim()) return
      this.validating = true
      const equation = this.equation
      this.timer = setTimeout(async () => {
        try {
          const result = await validateFormula(this.reportId, equation)
          if (request !== this.validationRequest) return
          this.formulaValid = result.valid
          this.validationError = result.error ?? ''
        } catch (cause) {
          if (request !== this.validationRequest) return
          this.validationError = cause instanceof Error ? cause.message : 'Не удалось проверить формулу.'
        } finally {
          if (request === this.validationRequest) this.validating = false
        }
      }, 350)
    },
    async save() {
      if (!this.valid || this.saving) return
      this.saving = true
      this.actionError = ''
      try {
        const item = await saveRequirementDefinition(this.reportId, this.requirement?.id ?? null, this.definition)
        this.baseline = JSON.stringify(this.definition)
        this.$emit('saved', item)
      } catch (cause) {
        this.actionError = cause instanceof Error ? cause.message : 'Не удалось сохранить требование.'
      } finally {
        this.saving = false
      }
    },
  },
})
</script>

<template>
  <dialog ref="dialog" aria-labelledby="requirement-editor-heading" @cancel.prevent="close"
          class="m-auto max-h-[calc(100dvh-2rem)] w-[calc(100%-2rem)] max-w-2xl overflow-y-auto overscroll-contain rounded-lg border border-slate-200 bg-white p-0 text-slate-800 shadow-xl backdrop:bg-slate-900/50">
    <div class="flex items-center justify-between gap-4 border-b border-slate-200 px-5 py-4">
      <h2 id="requirement-editor-heading" class="text-lg font-semibold">
        {{ requirement ? `Редактирование требования № ${requirement.id}` : 'Новое требование' }}
      </h2>
      <button type="button" :disabled="saving" aria-label="Закрыть" title="Закрыть"
              class="inline-flex size-9 shrink-0 cursor-pointer items-center justify-center rounded text-slate-500 hover:bg-slate-100 disabled:opacity-50"
              @click="close">
        <FontAwesomeIcon :icon="faXmark" class="size-4" aria-hidden="true"/>
      </button>
    </div>
    <form class="p-5" @submit.prevent="save">
      <fieldset :disabled="saving" class="space-y-5 disabled:opacity-60">
        <div>
          <label for="requirement-kind" class="mb-2 block text-sm font-medium">Тип требования</label>
          <select id="requirement-kind" v-model="kind" autofocus
                  class="w-full rounded border border-slate-300 bg-white px-3 py-2 text-sm focus:border-blue-500 focus:outline-none">
            <option value="formula">Формула</option>
            <option value="assertion">Текстовое утверждение</option>
          </select>
        </div>
        <div v-if="kind === 'formula'">
          <label for="requirement-equation" class="mb-2 block text-sm font-medium">Формула</label>
          <textarea id="requirement-equation" v-model="equation" rows="3" maxlength="10000" spellcheck="false" required
                    placeholder="#0 == #1 + #2" :aria-invalid="Boolean(validationError)"
                    aria-describedby="requirement-formula-status"
                    class="w-full rounded border border-slate-300 p-3 font-mono text-sm focus:border-blue-500 focus:outline-none"/>
          <p id="requirement-formula-status" class="mt-2 text-sm" aria-live="polite"
             :class="validationError ? 'text-red-700' : formulaValid ? 'text-green-700' : 'text-slate-500'">
            {{
              validating ? 'Проверяем формат…' : validationError || (formulaValid ? 'Формула корректна' : 'Введите формулу для проверки формата')
            }}
          </p>
          <button v-if="validationError" type="button" class="mt-1 cursor-pointer text-xs text-blue-700 hover:underline"
                  @click="scheduleValidation">Проверить ещё раз
          </button>
          <details class="mt-3 text-xs text-slate-500">
            <summary class="cursor-pointer font-medium hover:text-slate-700">Правила формул</summary>
            <div class="mt-2 space-y-2 leading-relaxed">
              <p>Используйте ссылки на сущности (#0), числа (100.50), даты (31.12.2026) и строки в двойных кавычках
                ("текст").</p>
              <p>Операции: + - * /; сравнения: == != &lt; &lt;= &gt; &gt;=; логика: &amp;&amp; ||. Можно использовать
                скобки и функции norm(#0), raw(#0).</p>
              <p>Формула должна возвращать истину или ложь. Например: <code class="font-mono">#0 == #1 + #2</code>.</p>
            </div>
          </details>
        </div>
        <div>
          <label for="requirement-text" class="mb-2 block text-sm font-medium">{{
              kind === 'formula' ? 'Описание (необязательно)' : 'Утверждение'
            }}</label>
          <textarea id="requirement-text" v-model="text" rows="4" maxlength="10000" :required="kind === 'assertion'"
                    class="w-full rounded border border-slate-300 p-3 text-sm focus:border-blue-500 focus:outline-none"/>
        </div>
        <div v-if="kind === 'assertion'">
          <label for="requirement-condition" class="mb-2 block text-sm font-medium">Значение</label>
          <select id="requirement-condition" v-model="condition"
                  class="w-full rounded border border-slate-300 bg-white px-3 py-2 text-sm">
            <option value="true">Истина</option>
            <option value="false">Ложь</option>
            <option value="unknown">Неизвестно</option>
          </select>
        </div>
      </fieldset>
      <p v-if="actionError" class="mt-4 text-sm text-red-700" role="alert">{{ actionError }}</p>
      <div class="mt-6 flex justify-end gap-3">
        <button type="button" :disabled="saving"
                class="cursor-pointer rounded border border-slate-300 px-4 py-2 text-sm hover:bg-slate-50 disabled:opacity-50"
                @click="close">Отмена
        </button>
        <button type="submit" :disabled="!valid || saving || (requirement !== null && !dirty)"
                class="cursor-pointer rounded bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50">
          {{ saving ? 'Сохраняем…' : requirement ? 'Сохранить' : 'Добавить' }}
        </button>
      </div>
    </form>
  </dialog>
</template>
