export interface Report {
    id: number
    name: string
    contract_number: string | null
    period_start: string | null
    period_end: string | null
    target_row: string | null
    stage: 'documents' | 'entities' | 'requirements' | 'verification'
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
    status: 'ocr_queued' | 'ocr_running' | 'ocr_completed' | 'entities_queued' | 'entities_running' | 'completed' | 'failed'
    markdown_revision: number
    user_edited: boolean
    error: string | null
}

export interface ReportDetails extends Report {
    documents: ReportDocument[]
    duplicate_groups: number[][]
    progress: { completed: number; total: number }
    blockers: string[]
    available_actions: string[]
    processing_mode: 'ocr' | 'entities' | 'requirements' | 'verification'
}

export interface MarkdownRevision {
    content: string
    revision: number
}

export interface EntityBox {
    page: number
    bbox_normalized: [number, number, number, number]
}

export interface EntityGeometry {
    entity_id: number
    document_revision: number
    text: string
    status: 'mapped' | 'partial' | 'unmapped'
    boxes: EntityBox[]
    message: string | null
}

export const getReport = (id: number) => request<ReportDetails>(`/reports/${id}`)

export const reportLogUrl = (reportId: number) => `/api/v1/reports/${reportId}/log`

export const reportArchiveUrl = (reportId: number) => `/api/v1/reports/${reportId}/archive`

export const uploadDocument = (reportId: number, file: File, role: DocumentRole) => {
    const body = new FormData()
    body.append('file', file)
    body.append('role', role)
    return request<ReportDocument>(`/reports/${reportId}/documents`, {method: 'POST', body})
}

export const deleteDocument = (reportId: number, documentId: number) =>
    request<void>(`/reports/${reportId}/documents/${documentId}`, {method: 'DELETE'})

export const pdfUrl = (reportId: number, documentId: number) =>
    `/api/v1/reports/${reportId}/documents/${documentId}/pdf`

export const documentLogUrl = (reportId: number, documentId: number) =>
    `/api/v1/reports/${reportId}/documents/${documentId}/log`

export const getMarkdown = (reportId: number, documentId: number) =>
    request<MarkdownRevision>(`/reports/${reportId}/documents/${documentId}/markdown`)

export const getEntityGeometry = (reportId: number, documentId: number, entityId: number) =>
    request<EntityGeometry>(`/reports/${reportId}/documents/${documentId}/entities/${entityId}/geometry`)

export const saveMarkdown = (reportId: number, documentId: number, content: string, baseRevision: number) =>
    request<MarkdownRevision>(`/reports/${reportId}/documents/${documentId}/markdown`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({content, base_revision: baseRevision}),
    })

export const runEntityExtraction = (reportId: number) =>
    request<ReportDetails>(`/reports/${reportId}/run-entity-extraction`, {method: 'POST'})

export const retryDocumentExtraction = (reportId: number, documentId: number) =>
    request<ReportDetails>(`/reports/${reportId}/documents/${documentId}/run-entity-extraction`, {method: 'POST'})

export type RequirementVerdict = 'true' | 'false' | 'error' | 'unverifiable'

interface RequirementMetadata {
    id: number
    text: string | null
    source: string
    notes: string
    silenced: boolean
}

export type Requirement = RequirementMetadata & (
    { kind: 'formula'; equation: string; tag: 'scope' | 'agg' | 'ref' | null }
    | { kind: 'assertion'; condition: boolean | null; ids_related: (number | string)[] }
    )

export interface RequirementResult {
    requirement_id: number
    verdict: RequirementVerdict
    error: string | null
    ids_related: number[]
}

export type VerifiedRequirement = Requirement & RequirementResult

export interface FieldVerdict {
    num: number
    raw: string
    type: string
    unit: string
    file: string
    status: 'ok' | 'mismatch' | 'unverified'
    stated: string | null
    expected: string | null
    diff: string | null
}

export interface VerificationResult {
    requirement_results: RequirementResult[]
    card: { cells: FieldVerdict[] }
    entity_documents: Record<number, number>
    files: {
        duplicates: Record<string, string>
        out_of_scope: Record<string, string[]>
        dangling: string[]
    }
}

export const runVerification = (reportId: number) =>
    request<ReportDetails>(`/reports/${reportId}/run-verification`, {method: 'POST'})

export interface RequirementsResponse {
    requirements: Requirement[]
    entity_documents: Record<number, number>
}

export const getRequirements = (reportId: number) =>
    request<RequirementsResponse>(`/reports/${reportId}/requirements`)

export const getVerificationResult = (reportId: number) =>
    request<VerificationResult>(`/reports/${reportId}/verification-result`)

export type RequirementUpdate = Partial<Pick<Requirement, 'notes' | 'silenced'>>

export const updateRequirement = (reportId: number, requirementId: number, changes: RequirementUpdate) =>
    request<Requirement>(`/reports/${reportId}/requirements/${requirementId}`, {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(changes),
    })

export type RequirementDefinition =
    { kind: 'formula'; equation: string; text: string | null; tag: 'scope' | 'agg' | 'ref' | null }
    | { kind: 'assertion'; text: string; condition: boolean | null; ids_related: (number | string)[] }

export const generateRequirements = (reportId: number) =>
    request<ReportDetails>(`/reports/${reportId}/generate-requirements`, {method: 'POST'})

export const editRequirements = (reportId: number) =>
    request<ReportDetails>(`/reports/${reportId}/edit-requirements`, {method: 'POST'})

export const validateFormula = (reportId: number, equation: string) =>
    request<{ valid: boolean; error: string | null }>(`/reports/${reportId}/requirements/validate-formula`, {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({equation}),
    })

export const saveRequirementDefinition = (reportId: number, id: number | null, definition: RequirementDefinition) =>
    request<Requirement>(`/reports/${reportId}/requirements${id === null ? '' : `/${id}`}`, {
        method: id === null ? 'POST' : 'PUT',
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify(definition),
    })

export const deleteRequirement = (reportId: number, id: number) =>
    request<void>(`/reports/${reportId}/requirements/${id}`, {method: 'DELETE'})
