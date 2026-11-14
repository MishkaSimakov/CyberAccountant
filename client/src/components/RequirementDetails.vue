<script lang="ts">
import {defineComponent, type PropType, useId} from 'vue'
import {mapActions, mapState} from 'vuex'
import {FontAwesomeIcon} from '@fortawesome/vue-fontawesome'
import {faBellSlash} from '@fortawesome/free-solid-svg-icons/faBellSlash'
import {faBell} from '@fortawesome/free-solid-svg-icons/faBell'
import {faSpinner} from '@fortawesome/free-solid-svg-icons/faSpinner'
import type {VerifiedEntity, VerifiedRequirement} from '../api'
import {formatAmount} from '../formatAmount'
import RequirementText from './RequirementText.vue'

export default defineComponent({
  components: {FontAwesomeIcon, RequirementText},
  props: {
    requirement: {type: Object as PropType<VerifiedRequirement>, required: true},
    entities: {type: Object as PropType<Record<number, VerifiedEntity>>, required: true},
    entityDocuments: {type: Object as PropType<Record<number, number>>, required: true},
  },
  emits: {select: (id: number) => Number.isInteger(id)},
  setup() {
    return {notesId: useId()}
  },
  data() {
    return {notes: this.requirement.notes, faBellSlash, faBell, faSpinner}
  },
  computed: {
    ...mapState('requirements', ['saving', 'saveErrors']),
    statusLabel(): string {
      return {
        true: 'Выполнено',
        false: 'Нарушено',
        error: 'Ошибка проверки',
        unverifiable: 'Не удалось проверить',
      }[this.requirement.verdict]
    },
    verdictText(): string {
      return this.requirement.error ? `${this.statusLabel}: ${this.requirement.error}` : this.statusLabel
    },
    statusClass(): string {
      return {
        true: 'text-green-700',
        false: 'text-red-700',
        error: 'text-orange-700',
        unverifiable: 'text-amber-700',
      }[this.requirement.verdict]
    },
  },
  watch: {
    'requirement.notes'(notes: string) {
      this.notes = notes
    },
  },
  methods: {
    ...mapActions('requirements', ['update']),
    entityValue(entity: VerifiedEntity | undefined): string {
      if (!entity || entity.value === null) return 'Не определено'
      if (entity.type === 'amount') return formatAmount(entity.value, entity.unit)
      if (entity.type !== 'date') return String(entity.value)
      const formatDate = (value: string | number) => new Date((Number(value) - 719163) * 86400000)
          .toLocaleDateString('ru-RU', {timeZone: 'UTC'})
      const start = formatDate(entity.value)
      return entity.end_value !== null && entity.end_value !== entity.value
          ? `${start} — ${formatDate(entity.end_value)}` : start
    },
  },
})
</script>

<template>
  <div class="space-y-4 text-sm">
    <div v-if="requirement.kind === 'formula'">
      <h4 class="font-medium">Формула</h4>
      <RequirementText :text="requirement.equation" :entity-documents="entityDocuments"
                       class="mt-1 block whitespace-pre-wrap break-words font-mono leading-7 text-slate-500"
                       @select="$emit('select', $event)"/>
    </div>
    <div>
      <h4 class="font-medium">Вердикт</h4>
      <p class="mt-1 whitespace-pre-wrap break-words" :class="statusClass">{{ verdictText }}</p>
      <p v-if="requirement.silenced" class="mt-1 text-slate-500">Предупреждение отключено</p>
    </div>
    <div>
      <h4 class="font-medium">Связанные сущности</h4>
      <p v-if="!requirement.ids_related.length" class="mt-2 text-slate-500">Нет связанных сущностей.</p>
      <div v-else class="mt-2 w-fit max-w-full overflow-x-auto rounded border border-slate-200">
        <table class="text-left text-sm">
          <thead class="bg-slate-50 text-xs text-slate-500">
          <tr>
            <th class="px-5 py-3 font-medium">Сущность</th>
            <th class="px-5 py-3 font-medium">Значение в проверке</th>
          </tr>
          </thead>
          <tbody class="divide-y divide-slate-100">
          <tr v-for="id in requirement.ids_related" :key="id">
            <td class="whitespace-nowrap px-5 py-3 align-top">
              <RequirementText :text="`#${id}`" :entity-documents="entityDocuments"
                               @select="$emit('select', $event)"/>
            </td>
            <td class="whitespace-pre-wrap break-words px-5 py-3 align-top tabular-nums">{{ entityValue(entities[id]) }}</td>
          </tr>
          </tbody>
        </table>
      </div>
    </div>
    <form @submit.prevent="update({id: requirement.id, changes: {notes}})">
      <label :for="notesId" class="block font-medium">Заметки</label>
      <textarea :id="notesId" v-model="notes" rows="3" maxlength="10000"
                :disabled="saving.includes(requirement.id)"
                class="mt-2 block w-full resize-y rounded border border-slate-200 bg-slate-50 p-3 text-sm leading-relaxed placeholder:text-slate-400 focus:border-blue-500 focus:bg-white focus:outline-blue-500 disabled:opacity-60"
                placeholder="Добавьте пояснение или запишите, что нужно проверить…"/>
      <div class="mt-3 flex flex-wrap items-center justify-between gap-3">
        <div class="flex flex-wrap items-center gap-3">
          <button type="submit" :disabled="saving.includes(requirement.id) || notes === requirement.notes"
                  class="inline-flex cursor-pointer items-center gap-2 rounded border border-slate-300 px-3 py-2 text-sm hover:bg-slate-50 focus-visible:outline-blue-500 disabled:cursor-default disabled:opacity-50">
            <FontAwesomeIcon v-if="saving.includes(requirement.id)" :icon="faSpinner"
                             class="size-3 animate-spin" aria-hidden="true"/>
            Сохранить заметку
          </button>
          <span class="text-xs text-slate-500" role="status">{{
              notes !== requirement.notes ? 'Есть несохранённые изменения' : requirement.notes ? 'Заметка сохранена' : ''
            }}</span>
        </div>
        <button v-if="requirement.verdict !== 'true'" type="button"
                :disabled="saving.includes(requirement.id)"
                class="inline-flex cursor-pointer items-center gap-2 rounded px-3 py-2 text-sm text-slate-600 hover:bg-slate-100 focus-visible:outline-blue-500 disabled:cursor-default disabled:opacity-50"
                :aria-pressed="requirement.silenced"
                @click="update({id: requirement.id, changes: {silenced: !requirement.silenced}})">
          <FontAwesomeIcon :icon="requirement.silenced ? faBell : faBellSlash" class="size-3.5" aria-hidden="true"/>
          {{ requirement.silenced ? 'Включить предупреждение' : 'Отключить предупреждение' }}
        </button>
      </div>
    </form>
    <p v-if="saveErrors[requirement.id]" class="text-red-700" role="alert">{{ saveErrors[requirement.id] }}</p>
  </div>
</template>
