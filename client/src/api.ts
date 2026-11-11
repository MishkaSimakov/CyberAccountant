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
    signal: AbortSignal.timeout(10000),
  })
  if (!response.ok) {
    throw new Error(response.status === 422
      ? 'Проверьте название отчёта и период подтверждающих документов.'
      : 'Сервер не смог выполнить запрос. Попробуйте ещё раз.')
  }
  return response.json() as Promise<T>
}

export const getReports = () => request<Report[]>('/reports')

export const createReport = (report: ReportCreate) => request<Report>('/reports', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify(report),
})
