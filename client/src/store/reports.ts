import type {Module} from 'vuex'
import {createReport, getReports, type Report} from '../api'
import type {RootState} from './index'

export interface ReportForm {
    name: string
    contract_number: string
    period_start: string
    period_end: string
    target_row: string
}

export interface ReportsState {
    items: Report[]
    loading: boolean
    loadError: string
    createError: string
    saving: boolean
    notice: string
}

export const reports: Module<ReportsState, RootState> = {
    namespaced: true,
    state: () => ({items: [], loading: true, loadError: '', createError: '', saving: false, notice: ''}),
    mutations: {
        patch(state, changes: Partial<ReportsState>) {
            Object.assign(state, changes)
        },
        upsert(state, report: Report) {
            const index = state.items.findIndex(item => item.id === report.id)
            if (index < 0) state.items.unshift(report)
            else state.items.splice(index, 1, report)
        },
    },
    actions: {
        async load({state, commit}) {
            if (state.saving) return
            commit('patch', {loading: true, loadError: ''})
            try {
                commit('patch', {items: await getReports()})
            } catch {
                commit('patch', {loadError: 'Не удалось загрузить отчёты. Проверьте соединение с сервером и попробуйте ещё раз.'})
            } finally {
                commit('patch', {loading: false})
            }
        },
        async create({state, commit}, form: ReportForm) {
            if (state.saving || state.loading) return null
            commit('patch', {createError: ''})
            let validationError = ''
            if (!form.name.trim()) validationError = 'Введите название отчёта.'
            else if (Boolean(form.period_start) !== Boolean(form.period_end)) {
                validationError = 'Укажите начало и конец периода подтверждающих документов.'
            } else if (form.period_start && form.period_end < form.period_start) {
                validationError = 'Конец периода не может быть раньше начала.'
            }
            if (validationError) {
                commit('patch', {createError: validationError})
                return null
            }
            commit('patch', {saving: true})
            try {
                const report = await createReport({
                    name: form.name.trim(),
                    contract_number: form.contract_number.trim() || null,
                    period_start: form.period_start || null,
                    period_end: form.period_end || null,
                    target_row: form.target_row.trim() || null,
                })
                commit('upsert', report)
                commit('patch', {loadError: '', notice: `Отчёт «${report.name}» создан.`})
                return report
            } catch (error) {
                commit('patch', {
                    createError: error instanceof TypeError || (error instanceof Error && error.name === 'TimeoutError')
                        ? 'Не удалось получить ответ сервера. Обновите список перед повторной попыткой: отчёт мог быть создан.'
                        : error instanceof Error ? error.message : 'Не удалось создать отчёт. Попробуйте ещё раз.',
                })
                return null
            } finally {
                commit('patch', {saving: false})
            }
        },
    },
}
