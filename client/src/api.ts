export interface Report {
    id: number
    name: string
    contract_number: string | null
    period_start: string | null
    period_end: string | null
    target_row: string | null
    stage: 'documents' | 'entities' | 'verification'
    processing_status: 'idle' | 'queued' | 'running' | 'completed' | 'failed'
}

export type ReportCreate = Pick<Report, 'name' | 'contract_number' | 'period_start' | 'period_end' | 'target_row'>

async function request<T>(path: string, options?: RequestInit): Promise<T> {
    const response = await fetch(`/api/v1${path}`, {
        ...options,
        signal: AbortSignal.timeout(options?.body instanceof FormData ? 60000 : 10000),
    })
    if (!response.ok) {
        const body = await response.json().catch(() => null)
        throw new Error(typeof body?.detail === 'string' ? body.detail : response.status === 422
            ? 'Проверьте название отчёта и период подтверждающих документов.'
            : 'Сервер не смог выполнить запрос. Попробуйте ещё раз.')
    }
    return response.status === 204 ? undefined as T : response.json() as Promise<T>
}

export const getReports = () => request<Report[]>('/reports')

export const createReport = (report: ReportCreate) => request<Report>('/reports', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(report),
})

export type DocumentRole = 'final_report' | 'supporting'

export interface ReportDocument {
    id: number
    name: string
    size: number
    content_type: string
    role: DocumentRole
    ocr_status: 'queued' | 'running' | 'completed' | 'failed'
    extraction_status: 'idle' | 'completed'
    markdown_revision: number
    user_edited: boolean
    error: string | null
}

export interface ReportDetails extends Report {
    documents: ReportDocument[]
    progress: { completed: number; total: number }
    blockers: string[]
    available_actions: string[]
    processing_mode: 'stub'
}

export interface MarkdownRevision {
    content: string
    revision: number
}

export const getReport = (id: number) => request<ReportDetails>(`/reports/${id}`)

export const uploadDocument = (reportId: number, file: File, role: DocumentRole) => {
    const body = new FormData()
    body.append('file', file)
    body.append('role', role)
    return request<ReportDocument>(`/reports/${reportId}/documents`, {method: 'POST', body})
}

export const deleteDocument = (reportId: number, documentId: number) =>
    request<void>(`/reports/${reportId}/documents/${documentId}`, {method: 'DELETE'})

export const originalUrl = (reportId: number, documentId: number) =>
    `/api/v1/reports/${reportId}/documents/${documentId}/original`

export const getMarkdown = (reportId: number, documentId: number) =>
    request<MarkdownRevision>(`/reports/${reportId}/documents/${documentId}/markdown`)

export const saveMarkdown = (reportId: number, documentId: number, content: string, baseRevision: number) =>
    request<MarkdownRevision>(`/reports/${reportId}/documents/${documentId}/markdown`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({content, base_revision: baseRevision}),
    })

export const runEntityExtraction = (reportId: number) =>
    request<ReportDetails>(`/reports/${reportId}/run-entity-extraction`, {method: 'POST'})

export interface Requirement {
    id: number
    verdict: boolean
    equation: string | null
    evidence: string | null
    ids_related: number[]
}

export const runVerification = (reportId: number) =>
    request<ReportDetails>(`/reports/${reportId}/run-verification`, {method: 'POST'})

export const getRequirements = (reportId: number) =>
    request<Requirement[]>(`/reports/${reportId}/requirements`)
