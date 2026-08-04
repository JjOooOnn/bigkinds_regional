import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'

const regions = [
  { order: 1, name: '서울특별시' },
  { order: 2, name: '부산광역시' },
]

function json(data: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  }))
}

function baseJob(overrides = {}) {
  return {
    job_id: 'job-1', created_at: '2026-07-20T09:00:00+09:00', started_at: '', ended_at: '',
    start_date: '2026-07-08', end_date: '2026-07-08', regions: ['서울특별시'], headed: false,
    resume: false, resume_from_job_id: '', max_issues: null, timeout_seconds: 30, retries: 2,
    link_delay_seconds: 0.5, debug: false, status: 'queued', status_label: '대기', current_date: '',
    current_region: '', current_issue: '', current_issue_order: 0, current_issue_total: 0,
    current_region_completed_issues: 0, current_region_total_issues: null,
    current_issue_processed_articles: 0, current_issue_total_articles: null,
    current_publisher: '', current_article_title: '',
    total_regions: 1, completed_regions: 0, total_region_units: 1, known_links: 0,
    processed_links: 0, normal_count: 0, error_count: 0, progress_percent: 0,
    download_available: false, excel_file_name: '', error_message: '', ...overrides,
  }
}

beforeEach(() => {
  localStorage.clear()
  vi.restoreAllMocks()
})

describe('점검 설정 화면', () => {
  it('날짜 오류와 지역 복수 선택을 표시하고 유효할 때 실행한다', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation((input, init) => {
      const url = String(input)
      if (url === '/api/config/regions') return json({ regions })
      if (url === '/api/jobs' && init?.method === 'POST') return json(baseJob(), 201)
      if (url === '/api/jobs') return json({ jobs: [] })
      if (url.endsWith('/logs')) return json({ logs: [] })
      if (url === '/api/jobs/job-1') return json(baseJob())
      return json({ detail: 'not found' }, 404)
    })
    render(<App />)
    await screen.findByText('점검을 시작할까요?')
    const dates = screen.getAllByLabelText(/일$/) as HTMLInputElement[]
    fireEvent.change(dates[0], { target: { value: '2026-07-09' } })
    fireEvent.change(dates[1], { target: { value: '2026-07-08' } })
    expect(screen.getByText('시작일은 종료일보다 늦을 수 없습니다.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '점검 시작' })).toBeDisabled()

    fireEvent.change(dates[0], { target: { value: '2026-07-08' } })
    fireEvent.click(screen.getByLabelText('전체 지역'))
    expect(await screen.findByLabelText('서울특별시')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '점검 시작' })).toBeDisabled()
    fireEvent.click(screen.getByLabelText('서울특별시'))
    expect(screen.getByRole('button', { name: '점검 시작' })).toBeEnabled()
    fireEvent.click(screen.getByRole('button', { name: '점검 시작' }))
    await screen.findByText('링크를 확인하고 있어요')
    expect(fetchMock).toHaveBeenCalledWith('/api/jobs', expect.objectContaining({ method: 'POST' }))
  })

  it('API 오류를 이해하기 쉬운 문구로 표시한다', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation((input, init) => {
      if (String(input) === '/api/config/regions') return json({ regions })
      if (String(input) === '/api/jobs' && init?.method === 'POST') return json({ detail: '이미 실행 중인 작업이 있습니다.' }, 409)
      return json({ jobs: [] })
    })
    render(<App />)
    await screen.findByText('점검을 시작할까요?')
    fireEvent.click(screen.getByRole('button', { name: '점검 시작' }))
    expect(await screen.findByText('이미 실행 중인 작업이 있습니다.')).toBeInTheDocument()
  })
})

describe('진행과 결과 화면', () => {
  it('새로고침 후 저장된 작업의 진행 상태를 복원한다', async () => {
    localStorage.setItem('bigkinds-current-job', 'job-1')
    const running = baseJob({
      status: 'running', status_label: '실행 중', current_date: '2026-07-08',
      current_region: '충청북도', current_issue: '산사태 위기경보', current_issue_order: 3,
      current_issue_total: 7, processed_links: 5, normal_count: 4, error_count: 1,
      completed_regions: 1, total_region_units: 2, progress_percent: 50,
      current_region_completed_issues: 2, current_region_total_issues: 7,
      current_issue_processed_articles: 3, current_issue_total_articles: 5,
      current_publisher: '테스트일보', current_article_title: '산사태 위기경보 관련 기사',
    })
    vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
      const url = String(input)
      if (url === '/api/config/regions') return json({ regions })
      if (url === '/api/jobs') return json({ jobs: [running] })
      if (url === '/api/jobs/job-1') return json(running)
      if (url.endsWith('/logs')) return json({ logs: [{ created_at: '2026-07-20T09:00:01+09:00', level: 'info', message: '충청북도 점검 시작' }] })
      return json({})
    })
    render(<App />)
    expect(await screen.findByText('링크를 확인하고 있어요')).toBeInTheDocument()
    expect(screen.getByText('충청북도 · 3번째 이슈 확인 중')).toBeInTheDocument()
    expect(screen.getByRole('progressbar', { name: '전체 일정' })).toHaveAttribute('aria-valuenow', '50')
    expect(screen.getByRole('progressbar', { name: '현재 지역 이슈' })).toHaveAttribute('aria-valuenow', '29')
    expect(screen.getByRole('progressbar', { name: '현재 이슈 기사' })).toHaveAttribute('aria-valuenow', '60')
    expect(screen.getByText('테스트일보')).toBeInTheDocument()
    expect(screen.getByText('산사태 위기경보 관련 기사')).toBeInTheDocument()
  })

  it('분모를 확인하기 전에는 하위 진행을 불확정 상태로 표시한다', async () => {
    localStorage.setItem('bigkinds-current-job', 'job-1')
    const running = baseJob({
      status: 'running', status_label: '실행 중', current_date: '2026-07-08', current_region: '충청북도',
    })
    vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
      const url = String(input)
      if (url === '/api/config/regions') return json({ regions })
      if (url === '/api/jobs') return json({ jobs: [running] })
      if (url === '/api/jobs/job-1') return json(running)
      if (url.endsWith('/logs')) return json({ logs: [] })
      return json({})
    })
    render(<App />)
    expect(await screen.findByRole('progressbar', { name: '현재 지역 이슈' })).not.toHaveAttribute('aria-valuenow')
    expect(screen.getByRole('progressbar', { name: '현재 이슈 기사' })).toHaveAttribute('aria-valuetext', '목록 확인 중')
    expect(screen.getAllByText('목록 확인 중')).toHaveLength(2)
  })

  it.each([
    ['cancel_requested', '중단 요청', '중단 요청을 전달했어요'],
    ['cancelling', '중단 처리 중', '현재 결과를 정리하고 있어요'],
    ['force_terminating', '강제 종료 중', '작업 프로세스 종료를 기다리고 있어요'],
  ])('취소 단계 %s를 구분해 표시한다', async (status, statusLabel, title) => {
    localStorage.setItem('bigkinds-current-job', 'job-1')
    const cancelling = baseJob({ status, status_label: statusLabel })
    vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
      const url = String(input)
      if (url === '/api/config/regions') return json({ regions })
      if (url === '/api/jobs') return json({ jobs: [cancelling] })
      if (url === '/api/jobs/job-1') return json(cancelling)
      if (url.endsWith('/logs')) return json({ logs: [] })
      return json({})
    })
    render(<App />)
    expect(await screen.findByRole('heading', { name: title })).toBeInTheDocument()
    expect(screen.getByText(statusLabel)).toBeInTheDocument()
  })

  it('완료 결과에서 필터와 Excel 다운로드를 제공한다', async () => {
    localStorage.setItem('bigkinds-current-job', 'job-1')
    const completed = baseJob({
      status: 'completed', status_label: '완료', ended_at: '2026-07-20T09:01:00+09:00',
      processed_links: 4, normal_count: 3, error_count: 1, download_available: true,
      excel_file_name: 'report.xlsx', progress_percent: 100,
    })
    const result = {
      summary: { total_links: 4, normal_count: 3, error_count: 1, normal_rate: 0.75, verdict_counts: { 링크오류: 1 } },
      total_errors: 1,
      errors: [{ requested_date: '2026-07-08', region: '충청북도', publisher: '테스트일보', article_title: '없는 기사', link_working_yn: 'N', verdict: '링크오류', error_message: '찾을 수 없음', original_url: 'https://example.com/original', final_url: 'https://example.com/final', http_status: 404 }],
    }
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
      const url = String(input)
      if (url === '/api/config/regions') return json({ regions })
      if (url === '/api/jobs') return json({ jobs: [completed] })
      if (url === '/api/jobs/job-1') return json(completed)
      if (url.endsWith('/logs')) return json({ logs: [] })
      if (url.includes('/results')) return json(result)
      return json({})
    })
    render(<App />)
    expect(await screen.findByText('점검이 완료됐어요')).toBeInTheDocument()
    expect(await screen.findByText('없는 기사')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Excel 다운로드' })).toHaveAttribute('href', '/api/jobs/job-1/download')
    fireEvent.change(screen.getByLabelText('언론사명'), { target: { value: '테스트' } })
    fireEvent.click(screen.getByRole('button', { name: '필터 적용' }))
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).includes('publisher=%ED%85%8C%EC%8A%A4%ED%8A%B8'))).toBe(true))
  })
})
