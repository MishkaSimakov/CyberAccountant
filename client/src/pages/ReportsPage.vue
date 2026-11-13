<script lang="ts">
import {defineComponent} from 'vue'
import {mapActions, mapState} from 'vuex'
import {RouterLink} from 'vue-router'
import {FontAwesomeIcon} from '@fortawesome/vue-fontawesome'
import {faPlus} from '@fortawesome/free-solid-svg-icons/faPlus'
import {faRotateRight} from '@fortawesome/free-solid-svg-icons/faRotateRight'
import {faXmark} from '@fortawesome/free-solid-svg-icons/faXmark'
import type {Report} from '../api'

const dateFormatter = new Intl.DateTimeFormat('ru-RU', {timeZone: 'UTC'})

export default defineComponent({
  components: {RouterLink, FontAwesomeIcon},
  data() {
    return {
      form: {name: '', contract_number: '', period_start: '', period_end: '', target_row: ''},
      faPlus, faRotateRight, faXmark,
      stages: {
        documents: 'Загрузка документов',
        entities: 'Разметка сущностей',
        requirements: 'Редактирование требований',
        verification: 'Проверка требований',
      },
      statuses: {
        idle: 'Ожидает действий',
        queued: 'В очереди',
        running: 'В обработке',
        completed: 'Завершено',
        failed: 'Ошибка обработки',
      },
      statusClasses: {
        idle: 'bg-slate-100 text-slate-600',
        queued: 'bg-blue-50 text-blue-700',
        running: 'bg-blue-50 text-blue-700',
        completed: 'bg-emerald-50 text-emerald-700',
        failed: 'bg-red-50 text-red-700',
      },

    }
  },
  computed: {
    ...mapState('reports', ['loading', 'loadError', 'createError', 'saving', 'notice']),
    reports(): Report[] {
      return this.$store.state.reports.items
    },
  },
  mounted() {
    void this.loadReports()
  },
  methods: {
    ...mapActions('reports', {loadReports: 'load', createReport: 'create'}),
    formatDate(value: string) {
      return dateFormatter.format(new Date(`${value}T00:00:00Z`))
    },
    openCreateDialog() {
      Object.assign(this.form, {name: '', contract_number: '', period_start: '', period_end: '', target_row: ''})
      this.$store.commit('reports/patch', {createError: ''})
      const dialog = this.$refs.dialog as HTMLDialogElement
      dialog.showModal()
    },
    closeCreateDialog() {
      if (!this.saving) (this.$refs.dialog as HTMLDialogElement).close()
    },
    async submitReport() {
      const report: Report | null = await this.createReport({...this.form})
      if (!report) return
      const dialog = this.$refs.dialog as HTMLDialogElement
      dialog.close()
      await this.$router.push({name: 'report', params: {reportId: report.id}})
    },
  },
})
</script>

<template>
  <main class="mx-auto max-w-6xl p-6">
    <div class="mb-6 flex flex-wrap items-center justify-between gap-4">
      <h1 class="text-3xl font-semibold">Отчёты</h1>
      <button
          class="inline-flex cursor-pointer items-center gap-2 rounded bg-blue-600 px-4 py-2 text-white hover:bg-blue-700 disabled:opacity-50"
          :disabled="loading" @click="openCreateDialog">
        <FontAwesomeIcon :icon="faPlus" class="size-4" aria-hidden="true"/>
        Создать отчёт
      </button>
    </div>

    <p v-if="notice" class="mb-4 rounded bg-green-50 p-4 text-green-800" role="status">{{ notice }}</p>

    <section class="rounded-lg border border-slate-200 bg-white" aria-labelledby="reports-heading"
             :aria-busy="loading">
      <div class="flex items-center justify-between gap-4 p-4">
        <h2 id="reports-heading" class="font-semibold">
          Все отчёты <span v-if="!loading && !loadError" class="ml-2 text-slate-500">{{ reports.length }}</span>
        </h2>
        <button
            class="inline-flex cursor-pointer items-center gap-2 rounded px-3 py-2 text-slate-600 hover:bg-slate-100 disabled:opacity-50"
            :disabled="loading" @click="loadReports">
          <FontAwesomeIcon :icon="faRotateRight" class="size-3 text-slate-400" aria-hidden="true"/>
          Обновить
        </button>
      </div>

      <div v-if="loading" class="border-t border-slate-200 p-8 text-center text-slate-500" role="status">
        Загружаем отчёты…
      </div>
      <div v-else-if="loadError" class="space-y-4 border-t border-slate-200 p-8 text-center" role="alert">
        <h3 class="font-semibold">Отчёты недоступны</h3>
        <p class="text-slate-500">{{ loadError }}</p>
        <button
            class="inline-flex cursor-pointer items-center gap-2 rounded border border-slate-300 px-4 py-2 hover:bg-slate-50"
            @click="loadReports">
          <FontAwesomeIcon :icon="faRotateRight" class="size-3 text-slate-400" aria-hidden="true"/>
          Попробовать снова
        </button>
      </div>
      <div v-else-if="reports.length === 0" class="space-y-4 border-t border-slate-200 p-8 text-center">
        <h3 class="font-semibold">Пока нет отчётов</h3>
        <button
            class="inline-flex cursor-pointer items-center gap-2 rounded bg-blue-600 px-4 py-2 text-white hover:bg-blue-700"
            @click="openCreateDialog">
          <FontAwesomeIcon :icon="faPlus" class="size-4" aria-hidden="true"/>
          Создать отчёт
        </button>
      </div>
      <div v-else class="overflow-x-auto">
        <table class="w-full text-left">
          <thead class="border-y border-slate-200 bg-slate-50 text-slate-500">
          <tr>
            <th class="p-4 font-medium" scope="col">Название отчёта</th>
            <th class="p-4 font-medium" scope="col">Период</th>
            <th class="p-4 font-medium" scope="col">Договор</th>
            <th class="p-4 font-medium" scope="col">Этап</th>
            <th class="p-4 font-medium" scope="col">Статус</th>
          </tr>
          </thead>
          <tbody class="divide-y divide-slate-200">
          <tr v-for="report in reports" :key="report.id">
            <td class="p-4">
              <RouterLink
                  :to="{name: report.stage === 'verification' && report.processing_status === 'completed' ? 'requirements' : 'report', params: {reportId: report.id}}"
                  class="block max-w-sm font-medium break-all text-blue-700 hover:underline">{{ report.name }}
              </RouterLink>
              <p v-if="report.target_row" class="mt-1 text-xs text-slate-500">{{ report.target_row }}</p>
            </td>
            <td class="p-4 whitespace-nowrap">
              <template v-if="report.period_start && report.period_end">
                {{ formatDate(report.period_start) }} — {{ formatDate(report.period_end) }}
              </template>
              <span v-else class="text-slate-500">Не указан</span>
            </td>
            <td class="p-4">{{ report.contract_number || '—' }}</td>
            <td class="p-4 text-xs whitespace-nowrap text-slate-500">{{ stages[report.stage] }}</td>
            <td class="p-4">
                  <span class="rounded px-2 py-1 text-xs whitespace-nowrap"
                        :class="statusClasses[report.processing_status]">
                    {{ statuses[report.processing_status] }}
                  </span>
            </td>
          </tr>
          </tbody>
        </table>
      </div>
    </section>
  </main>

  <dialog ref="dialog"
          class="m-auto max-h-screen w-11/12 max-w-lg overflow-y-auto rounded-lg bg-white p-6 backdrop:bg-slate-900/50"
          aria-labelledby="create-heading" @cancel="saving && $event.preventDefault()">
    <form class="space-y-4" @submit.prevent="submitReport">
      <div class="flex items-center justify-between gap-4">
        <h2 id="create-heading" class="text-xl font-semibold">Новый отчёт</h2>
        <button type="button" class="cursor-pointer rounded p-2 text-slate-500 hover:bg-slate-100 disabled:opacity-50"
                aria-label="Закрыть" :disabled="saving" @click="closeCreateDialog">
          <FontAwesomeIcon :icon="faXmark" class="size-4" aria-hidden="true"/>
        </button>
      </div>
      <fieldset class="space-y-4" :disabled="saving">
        <div>
          <label class="mb-1 block" for="report-name">Название отчёта *</label>
          <input id="report-name" v-model="form.name" class="w-full rounded border border-slate-300 p-2" name="name"
                 required maxlength="200" placeholder="Например, КФЗ за 3 этап" autofocus/>
        </div>

        <fieldset>
          <legend class="mb-2">Период подтверждающих документов</legend>
          <div class="grid gap-4 sm:grid-cols-2">
            <div>
              <label class="mb-1 block text-slate-500" for="period-start">Начало</label>
              <input id="period-start" v-model="form.period_start" class="w-full rounded border border-slate-300 p-2"
                     name="period_start" type="date" :max="form.period_end || '9999-12-31'"
                     :required="Boolean(form.period_end)"/>
            </div>
            <div>
              <label class="mb-1 block text-slate-500" for="period-end">Конец</label>
              <input id="period-end" v-model="form.period_end" class="w-full rounded border border-slate-300 p-2"
                     name="period_end" type="date" :min="form.period_start || undefined" max="9999-12-31"
                     :required="Boolean(form.period_start)"/>
            </div>
          </div>
        </fieldset>

        <div>
          <label class="mb-1 block" for="contract-number">Номер договора</label>
          <input id="contract-number" v-model="form.contract_number"
                 class="w-full rounded border border-slate-300 p-2" name="contract_number" maxlength="100"
                 placeholder="Например, 24/03/15-01"/>
        </div>

        <div>
          <label class="mb-1 block" for="target-row">Проверяемая строка отчёта</label>
          <input id="target-row" v-model="form.target_row" class="w-full rounded border border-slate-300 p-2"
                 name="target_row" maxlength="200" placeholder="Например, 3 этап, 2026 г."/>
        </div>
      </fieldset>
      <p v-if="createError" class="rounded bg-red-50 p-3 text-red-700" role="alert">{{ createError }}</p>
      <div class="flex justify-end gap-3 border-t border-slate-200 pt-4">
        <button type="button"
                class="cursor-pointer rounded border border-slate-300 px-4 py-2 hover:bg-slate-50 disabled:opacity-50"
                :disabled="saving" @click="closeCreateDialog">Отмена
        </button>
        <button type="submit"
                class="inline-flex cursor-pointer items-center gap-2 rounded bg-blue-600 px-4 py-2 text-white hover:bg-blue-700 disabled:opacity-50"
                :disabled="saving">
          <FontAwesomeIcon :icon="faPlus" class="size-4" aria-hidden="true"/>
          {{ saving ? 'Создаём…' : 'Создать отчёт' }}
        </button>
      </div>
    </form>
  </dialog>
</template>
