import type { AuditJob, JobLog, RegionOption, ResultResponse } from './types'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  })
  if (!response.ok) {
    let message = '요청을 처리하지 못했습니다.'
    try {
      const body = await response.json()
      if (typeof body.detail === 'string') message = body.detail
      else if (body.detail?.message) message = body.detail.message
      else if (Array.isArray(body.detail) && body.detail[0]?.msg) message = body.detail[0].msg
    } catch {
      // 응답 본문이 JSON이 아니면 기본 안내를 사용한다.
    }
    throw new Error(message)
  }
  return response.json() as Promise<T>
}

export const api = {
  regions: () => request<{ regions: RegionOption[] }>('/api/config/regions'),
  jobs: () => request<{ jobs: AuditJob[] }>('/api/jobs'),
  job: (jobId: string) => request<AuditJob>(`/api/jobs/${jobId}`),
  createJob: (payload: unknown) =>
    request<AuditJob>('/api/jobs', { method: 'POST', body: JSON.stringify(payload) }),
  cancelJob: (jobId: string) =>
    request<AuditJob>(`/api/jobs/${jobId}/cancel`, { method: 'POST' }),
  logs: (jobId: string) => request<{ logs: JobLog[] }>(`/api/jobs/${jobId}/logs`),
  results: (jobId: string, filters?: object) => {
    const query = new URLSearchParams()
    Object.entries(filters ?? {}).forEach(([key, value]) => {
      if (typeof value === 'string' && value) query.set(key, value)
    })
    const suffix = query.size ? `?${query.toString()}` : ''
    return request<ResultResponse>(`/api/jobs/${jobId}/results${suffix}`)
  },
}
