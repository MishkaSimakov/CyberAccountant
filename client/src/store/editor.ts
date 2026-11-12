import type {Module} from 'vuex'
import {getMarkdown, pdfUrl, saveMarkdown, type MarkdownRevision} from '../api'
import type {RootState} from './index'

export interface EditorState {
    reportId: number | null
    documentId: number | null
    session: number
    loading: boolean
    saving: boolean
    error: string
    content: string
    savedContent: string
    revision: number
}

const initialState = (): EditorState => ({
    reportId: null, documentId: null, session: 0, loading: true, saving: false,
    error: '', content: '', savedContent: '', revision: 0,
})

export const editor: Module<EditorState, RootState> = {
    namespaced: true,
    state: initialState,
    getters: {
        dirty: state => state.content !== state.savedContent,
        pdfUrl: state => state.reportId !== null && state.documentId !== null
            ? pdfUrl(state.reportId, state.documentId) : '',
        editable: (state, _getters, rootState) => state.reportId === rootState.report.id
            && !rootState.report.advancing
            && ['documents', 'entities'].includes(rootState.report.current?.stage ?? ''),
    },
    mutations: {
        reset(state, document: { reportId: number; documentId: number } | null) {
            Object.assign(state, initialState(), document, {session: state.session + 1})
        },
        patch(state, changes: Partial<EditorState>) {
            Object.assign(state, changes)
        },
        loaded(state, markdown: MarkdownRevision) {
            state.content = state.savedContent = markdown.content
            state.revision = markdown.revision
        },
        setContent(state, content: string) {
            if (!state.saving && !state.loading && state.revision) state.content = content
        },
    },
    actions: {
        open({commit, dispatch}, document: { reportId: number; documentId: number }) {
            commit('reset', document)
            return dispatch('load')
        },
        async load({state, commit}) {
            if (state.reportId === null || state.documentId === null || state.saving) return
            const session = state.session
            commit('patch', {loading: true, error: ''})
            try {
                const markdown = await getMarkdown(state.reportId, state.documentId)
                if (state.session === session) commit('loaded', markdown)
            } catch (cause) {
                if (state.session === session) commit('patch', {
                    error: cause instanceof Error ? cause.message : 'Не удалось загрузить текст.',
                })
            } finally {
                if (state.session === session) commit('patch', {loading: false})
            }
        },
        async save({state, getters, commit}) {
            if (!getters.editable || !getters.dirty || state.saving || !state.revision
                || !state.content.trim() || state.reportId === null || state.documentId === null) return false
            const session = state.session
            commit('patch', {saving: true, error: ''})
            try {
                const markdown = await saveMarkdown(state.reportId, state.documentId, state.content, state.revision)
                if (state.session !== session) return false
                commit('loaded', markdown)
                return true
            } catch (cause) {
                if (state.session === session) commit('patch', {
                    error: cause instanceof Error ? cause.message : 'Не удалось сохранить текст.',
                })
                return false
            } finally {
                if (state.session === session) commit('patch', {saving: false})
            }
        },
    },
}
