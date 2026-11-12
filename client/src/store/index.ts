import {createStore, type Store} from 'vuex'
import {reports, type ReportsState} from './reports'
import {report, type ReportState} from './report'
import {editor, type EditorState} from './editor'
import {requirements, type RequirementsState} from './requirements'

export interface RootState {
    reports: ReportsState
    report: ReportState
    editor: EditorState
    requirements: RequirementsState
}

declare module 'vue' {
    interface ComponentCustomProperties {
        $store: Store<RootState>
    }
}

export const store = createStore<RootState>({
    strict: import.meta.env.DEV,
    modules: {reports, report, editor, requirements},
})
