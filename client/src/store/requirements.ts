import type {Module} from 'vuex'
import {getReport, getRequirements, type Report, type Requirement} from '../api'
import type {RootState} from './index'

export interface RequirementsState {
    id: number | null
    session: number
    report: Report | null
    items: Requirement[] | null
    loading: boolean
    error: string
}

const initialState = (): RequirementsState => ({
    id: null, session: 0, report: null, items: null, loading: false, error: '',
})

export const requirements: Module<RequirementsState, RootState> = {
    namespaced: true,
    state: initialState,
    mutations: {
        reset(state, id: number | null) {
            Object.assign(state, initialState(), {id, session: state.session + 1})
        },
        patch(state, changes: Partial<RequirementsState>) {
            Object.assign(state, changes)
        },
    },
    actions: {
        start({commit, dispatch}, id: number) {
            commit('reset', id)
            return dispatch('load')
        },
        stop({commit}) {
            commit('reset', null)
        },
        async load({state, commit}) {
            if (state.id === null || state.loading) return
            const id = state.id
            const session = state.session
            commit('patch', {loading: true, error: ''})
            try {
                const report = await getReport(id)
                if (state.session !== session) return
                commit('patch', {report})
                if (report.stage !== 'verification' || report.processing_status !== 'completed') return
                const items = await getRequirements(id)
                if (state.session === session) commit('patch', {items})
            } catch (cause) {
                if (state.session === session) commit('patch', {
                    error: cause instanceof Error ? cause.message : 'Не удалось загрузить результаты проверки.',
                })
            } finally {
                if (state.session === session) commit('patch', {loading: false})
            }
        },
    },
}
