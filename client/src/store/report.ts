import type {Module} from 'vuex'
import {
    deleteDocument, getReport, runEntityExtraction, uploadDocument,
    type DocumentRole, type ReportDetails, type ReportDocument,
} from '../api'
import type {RootState} from './index'

interface LocalUpload {
    id: number
    name: string
    role: DocumentRole
    error: string
}

export interface ReportState {
    id: number | null
    session: number
    request: number
    current: ReportDetails | null
    loading: boolean
    loadError: string
    pollError: string
    actionError: string
    advancing: boolean
    deleting: number[]
    uploads: LocalUpload[]
}

const initialState = (): ReportState => ({
    id: null, session: 0, request: 0, current: null, loading: true,
    loadError: '', pollError: '', actionError: '', advancing: false, deleting: [], uploads: [],
})

function errorText(cause: unknown) {
    return cause instanceof Error ? cause.message : 'Не удалось выполнить запрос. Попробуйте ещё раз.'
}

// Timers are lifecycle resources, so keep them out of serializable store state.
let timer: ReturnType<typeof setTimeout> | undefined
let uploadId = 0

export const report: Module<ReportState, RootState> = {
    namespaced: true,
    state: initialState,
    getters: {
        editable: state => state.current?.stage === 'documents' && !state.advancing,
        finalPresent: state => Boolean(state.current?.documents.some(item => item.role === 'final_report')
            || state.uploads.some(item => item.role === 'final_report')),
        canAdvance: (state, getters) => Boolean(getters.editable && !state.pollError && !state.uploads.length
            && !state.deleting.length && state.current?.documents.every(item => item.ocr_status === 'completed')
            && state.current?.available_actions.includes('run_entity_extraction')),
        advanceTooltip: state => [
            ...(state.current?.blockers ?? []),
            ...(state.uploads.length ? ['Дождитесь загрузки файлов и удалите неудачные загрузки.'] : []),
            ...(state.deleting.length ? ['Дождитесь удаления файлов.'] : []),
        ].join('\n'),
    },
    mutations: {
        reset(state, id: number | null) {
            Object.assign(state, initialState(), {id, session: state.session + 1})
        },
        patch(state, changes: Partial<ReportState>) {
            Object.assign(state, changes)
        },
        addUpload(state, upload: LocalUpload) {
            state.uploads.push(upload)
        },
        failUpload(state, {id, error}: { id: number; error: string }) {
            const upload = state.uploads.find(item => item.id === id)
            if (upload) upload.error = error
        },
        removeUpload(state, id: number) {
            state.uploads = state.uploads.filter(item => item.id !== id)
        },
        addDocument(state, document: ReportDocument) {
            if (state.current && !state.current.documents.some(item => item.id === document.id)) {
                state.current.documents.push(document)
            }
        },
    },
    actions: {
        start({commit, dispatch}, id: number) {
            clearTimeout(timer)
            commit('reset', id)
            return dispatch('poll')
        },
        stop({commit}) {
            clearTimeout(timer)
            commit('reset', null)
        },
        async poll({state, dispatch}) {
            const session = state.session
            await dispatch('refresh')
            if (state.id !== null && state.session === session) {
                timer = setTimeout(() => void dispatch('poll'), 2000)
            }
        },
        async refresh({state, commit}) {
            if (state.id === null || state.advancing) return
            const session = state.session
            const request = state.request + 1
            commit('patch', {request})
            try {
                const current = await getReport(state.id)
                if (state.session !== session || state.request !== request) return
                commit('patch', {current, pollError: '', loadError: ''})
                commit('reports/upsert', current, {root: true})
            } catch (cause) {
                if (state.session !== session || state.request !== request) return
                commit('patch', state.current
                    ? {pollError: 'Не удалось обновить статусы. Повторяем соединение…'}
                    : {loadError: errorText(cause)})
            } finally {
                if (state.session === session && state.request === request) commit('patch', {loading: false})
            }
        },
        async addFiles({state, getters, commit, dispatch}, {files, role}: { files: File[]; role: DocumentRole }) {
            if (!getters.editable || state.id === null) return
            const id = state.id
            const session = state.session
            commit('patch', {actionError: ''})
            if (role === 'final_report' && (files.length !== 1 || getters.finalPresent)) {
                commit('patch', {actionError: 'Выберите один итоговый отчёт. Чтобы заменить его, сначала удалите выбранный файл.'})
                return
            }
            await Promise.all(files.map(async file => {
                const local: LocalUpload = {id: ++uploadId, name: file.name, role, error: ''}
                commit('addUpload', local)
                const fail = (error: string) => {
                    if (state.session === session) commit('failUpload', {id: local.id, error})
                }
                if (!/\.(pdf|docx|xlsx|png|jpe?g)$/i.test(file.name)) {
                    fail('Неподдерживаемый формат. Выберите PDF, DOCX, XLSX, PNG или JPEG.')
                    return
                }
                if (!file.size || file.size > 20 * 1024 * 1024) {
                    fail(file.size ? 'Размер файла превышает 20 МБ.' : 'Файл пуст. Выберите файл с содержимым.')
                    return
                }
                try {
                    const document = await uploadDocument(id, file, role)
                    if (state.session !== session) return
                    commit('patch', {request: state.request + 1})
                    commit('addDocument', document)
                    commit('removeUpload', local.id)
                } catch (cause) {
                    fail(`${errorText(cause)} Обновите список перед повторной загрузкой: файл мог быть принят сервером.`)
                }
                if (state.session === session) await dispatch('refresh')
            }))
        },
        dismissUpload({state, commit}, id: number) {
            if (state.uploads.some(item => item.id === id && item.error)) commit('removeUpload', id)
        },
        async remove({state, getters, commit, dispatch}, documentId: number) {
            if (!getters.editable || state.id === null || state.deleting.includes(documentId)) return
            const session = state.session
            commit('patch', {deleting: [...state.deleting, documentId], actionError: ''})
            try {
                await deleteDocument(state.id, documentId)
                if (state.session === session) await dispatch('refresh')
            } catch (cause) {
                if (state.session === session) commit('patch', {actionError: errorText(cause)})
            } finally {
                if (state.session === session) commit('patch', {deleting: state.deleting.filter(id => id !== documentId)})
            }
        },
        async advance({state, getters, commit, dispatch}) {
            if (!getters.canAdvance || state.id === null) return
            const session = state.session
            commit('patch', {advancing: true, actionError: '', request: state.request + 1})
            try {
                const current = await runEntityExtraction(state.id)
                if (state.session !== session) return
                commit('patch', {current})
                commit('reports/upsert', current, {root: true})
            } catch (cause) {
                if (state.session === session) commit('patch', {actionError: errorText(cause)})
            } finally {
                if (state.session === session) {
                    commit('patch', {advancing: false})
                    await dispatch('refresh')
                }
            }
        },
    },
}
