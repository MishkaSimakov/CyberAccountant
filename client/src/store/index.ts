import type {InjectionKey} from 'vue'
import {createStore, useStore as baseUseStore, type Store} from 'vuex'
import {reports, type ReportsState} from './reports'
import {report, type ReportState} from './report'
import {editor, type EditorState} from './editor'

export interface RootState {
    reports: ReportsState
    report: ReportState
    editor: EditorState
}

export const storeKey: InjectionKey<Store<RootState>> = Symbol('store')

export const store = createStore<RootState>({
    strict: import.meta.env.DEV,
    modules: {reports, report, editor},
})

export const useStore = () => baseUseStore(storeKey)
