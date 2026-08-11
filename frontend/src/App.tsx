import { FormEvent, useCallback, useEffect, useMemo, useState } from 'react'
import { api } from './api'
import type { AuditJob, JobLog, JobStatus, RegionOption, ResultResponse } from './types'

const CANCELLING = new Set(['cancel_requested', 'cancelling', 'force_terminating'])
const ACTIVE = new Set(['queued', 'running', ...CANCELLING])
const RESUMABLE = new Set(['partial_failed', 'failed'])
const VERDICTS = ['링크오류', '접근제한', '서버오류', '타임아웃', '클릭오류', '빈화면', '확인필요']
const VERDICT_BADGE_CLASSES: Partial<Record<string, string>> = {
  링크오류: 'verdict-badge-link-error',
  확인필요: 'verdict-badge-review',
}
const RESULT_TITLES: Partial<Record<JobStatus, string>> = {
  completed: '점검이 완료됐어요',
  partial_failed: '일부 점검 결과를 확인해 주세요',
  failed: '점검을 완료하지 못했어요',
  cancelled: '중단된 작업 결과예요',
}
const STORAGE_KEY = 'bigkinds-current-job'
const EMPTY_FILTERS = { verdict: '', region: '', publisher: '', title: '' }
const CANCELLATION_COPY: Partial<Record<JobStatus, { title: string; detail: string }>> = {
  cancel_requested: {
    title: '중단 요청을 전달했어요',
    detail: '현재 기사 작업이 안전한 지점에 도달하면 정리를 시작합니다.',
  },
  cancelling: {
    title: '현재 결과를 정리하고 있어요',
    detail: '완료된 체크포인트와 가능한 부분 결과를 보존합니다.',
  },
  force_terminating: {
    title: '작업 프로세스 종료를 기다리고 있어요',
    detail: '정리 제한시간을 넘긴 작업만 기록된 PID로 종료합니다.',
  },
}

type Screen = 'setup' | 'history' | 'job'

function today(): string {
  const now = new Date()
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60_000)
  return local.toISOString().slice(0, 10)
}

function formatDateTime(value: string): string {
  if (!value) return '-'
  return new Intl.DateTimeFormat('ko-KR', {
    dateStyle: 'medium', timeStyle: 'short',
  }).format(new Date(value))
}

function elapsed(job: AuditJob, tick: number): string {
  void tick
  const start = new Date(job.started_at || job.created_at).getTime()
  const end = job.ended_at ? new Date(job.ended_at).getTime() : Date.now()
  const seconds = Math.max(0, Math.floor((end - start) / 1000))
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const rest = seconds % 60
  return hours ? `${hours}시간 ${minutes}분` : minutes ? `${minutes}분 ${rest}초` : `${rest}초`
}

function ProgressStage({
  label,
  completed,
  total,
  scope,
}: {
  label: string
  completed: number
  total: number | null
  scope: string
}) {
  const known = typeof total === 'number'
  const percent = !known ? 0 : total === 0 ? 100 : Math.min(100, completed / total * 100)
  const count = !known ? '목록 확인 중' : `${completed.toLocaleString()} / ${total.toLocaleString()}`
  return (
    <div className="progress-stage">
      <div className="progress-copy">
        <strong>{label}</strong>
        <span>{count}</span>
      </div>
      <div
        className={`progress-track${known ? '' : ' is-indeterminate'}`}
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={known ? Math.round(percent) : undefined}
        aria-valuetext={known ? `${count}, ${Math.round(percent)}%` : '목록 확인 중'}
      >
        <span style={{ width: `${percent}%` }} />
      </div>
      <p className="progress-scope">{scope}</p>
    </div>
  )
}

function statusIcon(status: string): string {
  if (status === 'completed') return '✓'
  if (status === 'failed' || status === 'partial_failed') return '!'
  if (status === 'cancelled') return '■'
  return '●'
}

function EmptyState({ children }: { children: React.ReactNode }) {
  return <div className="empty-state">{children}</div>
}

function SetupScreen({
  regions,
  jobs,
  onCreated,
}: {
  regions: RegionOption[]
  jobs: AuditJob[]
  onCreated: (job: AuditJob) => void
}) {
  const [startDate, setStartDate] = useState(today())
  const [endDate, setEndDate] = useState(today())
  const [allRegions, setAllRegions] = useState(true)
  const [selected, setSelected] = useState<string[]>([])
  const [headed, setHeaded] = useState(false)
  const [resume, setResume] = useState(false)
  const [resumeJobId, setResumeJobId] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [apiError, setApiError] = useState('')
  const dateError = startDate && endDate && startDate > endDate
    ? '시작일은 종료일보다 늦을 수 없습니다.'
    : ''
  const regionError = !allRegions && selected.length === 0
    ? '점검할 지역을 하나 이상 선택해 주세요.'
    : ''
  const resumableJobs = jobs.filter(
    (job) => RESUMABLE.has(job.status) && job.manual_resume_available,
  )
  const canSubmit = Boolean(startDate && endDate && !dateError && !regionError && !submitting)

  function toggleRegion(name: string) {
    setSelected((current) => current.includes(name)
      ? current.filter((item) => item !== name)
      : [...current, name])
  }

  function chooseResumeJob(jobId: string) {
    setResumeJobId(jobId)
    const previous = jobs.find((job) => job.job_id === jobId)
    if (!previous) return
    setStartDate(previous.start_date)
    setEndDate(previous.end_date)
    setSelected(previous.regions)
    setAllRegions(previous.regions.length === regions.length)
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!canSubmit) return
    setSubmitting(true)
    setApiError('')
    try {
      const job = await api.createJob({
        start_date: startDate,
        end_date: endDate,
        all_regions: allRegions,
        regions: allRegions ? [] : selected,
        headed,
        resume: resume && Boolean(resumeJobId),
        resume_from_job_id: resume ? resumeJobId || null : null,
      })
      onCreated(job)
    } catch (error) {
      setApiError(error instanceof Error ? error.message : '작업을 만들지 못했습니다.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="page setup-page">
      <section className="hero">
        <p className="eyebrow">LOCAL LINK AUDIT</p>
        <h1>점검을 시작할까요?</h1>
        <p>날짜와 지역을 선택하면 지역이슈의 뉴스 링크가 정상적으로 열리는지 자동으로 확인합니다.</p>
      </section>

      <form onSubmit={submit} noValidate>
        <section className="form-card" aria-labelledby="period-title">
          <div className="section-heading">
            <span className="step-number">1</span>
            <div>
              <h2 id="period-title">어느 기간을 확인할까요?</h2>
              <p>시작일과 종료일을 모두 포함해 점검합니다.</p>
            </div>
          </div>
          <div className="date-grid">
            <label>
              <span>시작일</span>
              <input
                type="date"
                value={startDate}
                onChange={(event) => setStartDate(event.target.value)}
                aria-describedby={dateError ? 'date-error' : undefined}
              />
            </label>
            <span className="date-separator" aria-hidden="true">→</span>
            <label>
              <span>종료일</span>
              <input
                type="date"
                value={endDate}
                onChange={(event) => setEndDate(event.target.value)}
                aria-describedby={dateError ? 'date-error' : undefined}
              />
            </label>
          </div>
          {dateError && <p className="field-error" id="date-error" role="alert">{dateError}</p>}
        </section>

        <section className="form-card" aria-labelledby="region-title">
          <div className="section-heading region-heading">
            <span className="step-number">2</span>
            <div>
              <h2 id="region-title">점검할 지역을 선택해 주세요</h2>
              <p>{allRegions ? '17개 시도를 모두 점검합니다.' : `${selected.length}개 지역을 선택했습니다.`}</p>
            </div>
            <label className="switch-label">
              <input
                type="checkbox"
                checked={allRegions}
                onChange={(event) => setAllRegions(event.target.checked)}
              />
              <span className="switch" aria-hidden="true" />
              전체 지역
            </label>
          </div>
          {!allRegions && (
            <>
              <div className="selection-actions">
                <button type="button" className="text-button" onClick={() => setSelected(regions.map((item) => item.name))}>전체 선택</button>
                <button type="button" className="text-button" onClick={() => setSelected([])}>선택 해제</button>
              </div>
              <fieldset className="region-grid" aria-describedby={regionError ? 'region-error' : undefined}>
                <legend className="sr-only">지역 복수 선택</legend>
                {regions.map((region) => (
                  <label className={`region-option ${selected.includes(region.name) ? 'selected' : ''}`} key={region.name}>
                    <input
                      type="checkbox"
                      checked={selected.includes(region.name)}
                      onChange={() => toggleRegion(region.name)}
                    />
                    <span>{region.name}</span>
                  </label>
                ))}
              </fieldset>
              {regionError && <p className="field-error" id="region-error" role="alert">{regionError}</p>}
            </>
          )}
        </section>

        <section className="form-card" aria-labelledby="mode-title">
          <div className="section-heading">
            <span className="step-number">3</span>
            <div>
              <h2 id="mode-title">어떻게 실행할까요?</h2>
              <p>브라우저 창 없이 실행하는 방식이 기본입니다.</p>
            </div>
          </div>
          <div className="mode-grid" role="radiogroup" aria-label="브라우저 실행 방식">
            <label className={`mode-option ${!headed ? 'selected' : ''}`}>
              <input type="radio" name="headed" checked={!headed} onChange={() => setHeaded(false)} />
              <span className="mode-icon" aria-hidden="true">◌</span>
              <strong>백그라운드 실행</strong>
              <small>추천 · 브라우저 창을 띄우지 않아요</small>
            </label>
            <label className={`mode-option ${headed ? 'selected' : ''}`}>
              <input type="radio" name="headed" checked={headed} onChange={() => setHeaded(true)} />
              <span className="mode-icon" aria-hidden="true">▣</span>
              <strong>브라우저 표시</strong>
              <small>DOM이나 클릭을 직접 확인할 때 사용해요</small>
            </label>
          </div>

          {resumableJobs.length > 0 && (
            <div className="resume-box">
              <label className="check-line">
                <input type="checkbox" checked={resume} onChange={(event) => setResume(event.target.checked)} />
                일부 실패한 작업에서 재개
              </label>
              {resume && (
                <label>
                  <span>재개할 작업</span>
                  <select value={resumeJobId} onChange={(event) => chooseResumeJob(event.target.value)} required>
                    <option value="">작업을 선택해 주세요</option>
                    {resumableJobs.map((job) => (
                      <option value={job.job_id} key={job.job_id}>
                        {formatDateTime(job.created_at)} · {job.start_date}~{job.end_date} · {job.status_label}
                      </option>
                    ))}
                  </select>
                </label>
              )}
            </div>
          )}
        </section>

        {apiError && <div className="notice error-notice" role="alert">{apiError}</div>}
        <button className="primary-button start-button" type="submit" disabled={!canSubmit || (resume && !resumeJobId)}>
          {submitting ? '작업을 만들고 있어요…' : '점검 시작'}
        </button>
      </form>
    </main>
  )
}

function ProgressScreen({
  job,
  logs,
  onCancel,
  cancelling,
  tick,
}: {
  job: AuditJob
  logs: JobLog[]
  onCancel: () => void
  cancelling: boolean
  tick: number
}) {
  const cancellation = CANCELLATION_COPY[job.status]
  const detail = job.current_region
    ? `${job.current_region}${job.current_issue ? ` · ${job.current_issue_order || ''}번째 이슈 확인 중` : ' 점검 중'}`
    : '점검 환경을 준비하고 있어요'
  return (
    <main className="page progress-page">
      <section className="status-hero" aria-live="polite">
        <div className="pulse-mark" aria-hidden="true"><span /></div>
        <p className="eyebrow">{job.status_label}</p>
        <h1>{cancellation?.title ?? '링크를 확인하고 있어요'}</h1>
        <p>{cancellation?.detail ?? detail}</p>
        {!cancellation && job.current_issue && <p className="current-issue-title">{job.current_issue}</p>}
      </section>

      <section className="progress-card">
        <ProgressStage
          label="전체 일정"
          completed={job.completed_regions}
          total={job.total_region_units || job.total_regions}
          scope="완료 날짜×지역 / 전체 날짜×지역"
        />
        <ProgressStage
          label="현재 지역 이슈"
          completed={job.current_region_completed_issues}
          total={job.current_region_total_issues}
          scope="완료 이슈 / 현재 지역의 전체 이슈"
        />
        <ProgressStage
          label="현재 이슈 기사"
          completed={job.current_issue_processed_articles}
          total={job.current_issue_total_articles}
          scope="처리 기사 / 현재 이슈의 전체 기사"
        />
        {(job.current_publisher || job.current_article_title) && (
          <p className="current-article">
            <strong>{job.current_publisher || '언론사 확인 중'}</strong>
            <span>{job.current_article_title || '기사 제목 확인 중'}</span>
          </p>
        )}
      </section>

      <section className="metric-grid" aria-label="점검 진행 수치">
        <div className="current-date-metric"><span>현재 날짜</span><strong>{job.current_date || '-'}</strong></div>
        <div><span>처리한 링크</span><strong>{job.processed_links.toLocaleString()}</strong><small>현재 확인 {job.known_links.toLocaleString()}개</small></div>
        <div><span>정상</span><strong className="positive">{job.normal_count.toLocaleString()}</strong></div>
        <div><span>확인 필요</span><strong>{job.error_count.toLocaleString()}</strong></div>
        <div><span>실행 시간</span><strong>{elapsed(job, tick)}</strong></div>
      </section>

      <details className="log-panel">
        <summary>상세 진행 내용 <span>{logs.length}개</span></summary>
        <div className="log-list" aria-live="polite">
          {logs.length ? logs.map((log, index) => (
            <p key={`${log.created_at}-${index}`} className={log.level}>
              <time>{new Date(log.created_at).toLocaleTimeString('ko-KR')}</time>
              <span>{log.message}</span>
            </p>
          )) : <p className="muted">아직 기록된 진행 내용이 없습니다.</p>}
        </div>
      </details>

      <div className="bottom-actions">
        <button className="danger-button" type="button" disabled={cancelling || CANCELLING.has(job.status)} onClick={onCancel}>
          {CANCELLING.has(job.status) || cancelling ? '중단 요청됨' : '점검 중단'}
        </button>
        <p>이 페이지를 닫아도 로컬 서버가 실행 중이면 작업은 계속됩니다.</p>
      </div>
    </main>
  )
}

interface Filters { verdict: string; region: string; publisher: string; title: string }

function ResultsScreen({
  job,
  results,
  regions,
  filters,
  onFilters,
  onApplyFilters,
  loading,
}: {
  job: AuditJob
  results: ResultResponse | null
  regions: RegionOption[]
  filters: Filters
  onFilters: (filters: Filters) => void
  onApplyFilters: () => void
  loading: boolean
}) {
  const summary = results?.summary ?? {
    total_links: job.processed_links,
    normal_count: job.normal_count,
    error_count: job.error_count,
    normal_rate: job.processed_links ? job.normal_count / job.processed_links : 0,
    verdict_counts: {},
  }
  const completeTitle = RESULT_TITLES[job.status] ?? `${job.status_label} 작업 결과예요`
  return (
    <main className="page results-page">
      <section className="result-hero">
        <div className={`result-icon status-${job.status}`} aria-hidden="true">{statusIcon(job.status)}</div>
        <div>
          <p className="eyebrow">{job.status_label}</p>
          <h1>{completeTitle}</h1>
          <p>확인이 필요한 링크가 {summary.error_count.toLocaleString()}개 있어요.</p>
        </div>
        {job.download_available ? (
          <a className="primary-button compact" href={`/api/jobs/${job.job_id}/download`}>Excel 다운로드</a>
        ) : (
          <button className="primary-button compact" type="button" disabled>Excel 파일 없음</button>
        )}
      </section>

      {job.error_message && <div className="notice warning-notice" role="status">{job.error_message}</div>}
      {job.manual_resume_available && (
        <div className="notice resume-notice" role="status">
          {job.manual_resume_reason || '체크포인트에서 수동으로 재개할 수 있습니다.'}
        </div>
      )}

      <section className="summary-grid" aria-label="점검 결과 요약">
        <div><span>전체 링크</span><strong>{summary.total_links.toLocaleString()}</strong></div>
        <div><span>정상</span><strong className="positive">{summary.normal_count.toLocaleString()}</strong></div>
        <div><span>오류</span><strong>{summary.error_count.toLocaleString()}</strong></div>
        <div><span>정상률</span><strong>{(summary.normal_rate * 100).toFixed(1)}%</strong></div>
      </section>

      <section className="verdict-strip" aria-label="오류 유형 요약">
        {VERDICTS.map((verdict) => (
          <div key={verdict}><span>{verdict}</span><strong>{summary.verdict_counts[verdict] ?? 0}</strong></div>
        ))}
      </section>

      <section className="errors-section">
        <div className="section-title-row">
          <div>
            <h2>오류 링크</h2>
            <p>원본 링크는 빅카인즈 출처 카드가 실제로 연 주소입니다.</p>
          </div>
          <span className="count-pill">{results?.total_errors ?? 0}건</span>
        </div>
        <div className="filter-grid">
          <label><span>오류 유형</span><select value={filters.verdict} onChange={(event) => onFilters({ ...filters, verdict: event.target.value })}><option value="">전체</option>{VERDICTS.map((item) => <option key={item}>{item}</option>)}</select></label>
          <label><span>지역</span><select value={filters.region} onChange={(event) => onFilters({ ...filters, region: event.target.value })}><option value="">전체</option>{regions.map((item) => <option key={item.name}>{item.name}</option>)}</select></label>
          <label><span>언론사명</span><input value={filters.publisher} onChange={(event) => onFilters({ ...filters, publisher: event.target.value })} placeholder="언론사 검색" /></label>
          <label><span>기사제목</span><input value={filters.title} onChange={(event) => onFilters({ ...filters, title: event.target.value })} placeholder="제목 검색" /></label>
          <button type="button" className="secondary-button filter-button" onClick={onApplyFilters} disabled={loading}>{loading ? '조회 중…' : '필터 적용'}</button>
        </div>

        {!results?.errors.length ? (
          <EmptyState>{loading ? '결과를 불러오고 있습니다.' : '조건에 맞는 오류 링크가 없습니다.'}</EmptyState>
        ) : (
          <div className="error-list">
            {results.errors.map((item, index) => (
              <article className="error-card" key={`${item.original_url}-${index}`}>
                <div className="error-meta"><span>{item.requested_date}</span><span>{item.region}</span><span>{item.publisher || '언론사 미확인'}</span><span className={`verdict-badge ${VERDICT_BADGE_CLASSES[item.verdict] ?? ''}`}>{item.verdict}</span></div>
                <h3>{item.article_title || '기사제목 미확인'}</h3>
                <p className="error-message">{item.error_message || '정상 기사 화면을 확인하지 못했습니다.'}</p>
                <dl>
                  <div><dt>원본URL</dt><dd>{item.original_url || '-'}</dd></div>
                  <div><dt>최종URL</dt><dd>{item.final_url || '-'}</dd></div>
                </dl>
                <div className="link-actions">
                  {item.original_url && <a href={item.original_url} target="_blank" rel="noreferrer">원본 링크 열기</a>}
                  {item.final_url && <a href={item.final_url} target="_blank" rel="noreferrer">최종 링크 열기</a>}
                </div>
              </article>
            ))}
          </div>
        )}
      </section>
    </main>
  )
}

function HistoryScreen({ jobs, onOpen, onRefresh }: { jobs: AuditJob[]; onOpen: (job: AuditJob) => void; onRefresh: () => void }) {
  return (
    <main className="page history-page">
      <section className="hero compact-hero">
        <p className="eyebrow">RUN HISTORY</p>
        <h1>이전 실행 기록</h1>
        <p>서버를 다시 시작해도 로컬 SQLite에 저장된 작업 기록을 확인할 수 있습니다.</p>
      </section>
      <div className="history-toolbar"><button type="button" className="secondary-button" onClick={onRefresh}>새로고침</button></div>
      {!jobs.length ? <EmptyState>아직 실행 기록이 없습니다.</EmptyState> : (
        <div className="history-list">
          {jobs.map((job) => {
            const rate = job.processed_links ? job.normal_count / job.processed_links * 100 : 0
            return (
              <article className="history-card" key={job.job_id}>
                <button type="button" className="history-main" onClick={() => onOpen(job)}>
                  <span className={`status-dot status-${job.status}`} aria-hidden="true">{statusIcon(job.status)}</span>
                  <span className="history-copy">
                    <strong>{job.start_date} ~ {job.end_date}</strong>
                    <small>{formatDateTime(job.created_at)} · {job.regions.length === 17 ? '전체 지역' : `${job.regions.length}개 지역`}</small>
                  </span>
                  <span className="history-stat"><strong>{rate.toFixed(1)}%</strong><small>정상률</small></span>
                  <span className="history-stat"><strong>{job.processed_links}</strong><small>전체 링크</small></span>
                  <span className="history-stat"><strong>{job.error_count}</strong><small>오류</small></span>
                  <span className="status-label">{job.status_label}</span>
                </button>
                {job.download_available
                  ? <a className="download-link" href={`/api/jobs/${job.job_id}/download`}>Excel</a>
                  : !ACTIVE.has(job.status) && <span className="download-unavailable">파일 없음</span>}
              </article>
            )
          })}
        </div>
      )}
    </main>
  )
}

export default function App() {
  const [screen, setScreen] = useState<Screen>('setup')
  const [regions, setRegions] = useState<RegionOption[]>([])
  const [jobs, setJobs] = useState<AuditJob[]>([])
  const [job, setJob] = useState<AuditJob | null>(null)
  const [logs, setLogs] = useState<JobLog[]>([])
  const [results, setResults] = useState<ResultResponse | null>(null)
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS)
  const [loadingResults, setLoadingResults] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [globalError, setGlobalError] = useState('')
  const [tick, setTick] = useState(0)

  const refreshJobs = useCallback(async () => {
    try {
      const response = await api.jobs()
      setJobs(response.jobs)
    } catch (error) {
      setGlobalError(error instanceof Error ? error.message : '실행 기록을 불러오지 못했습니다.')
    }
  }, [])

  const loadResults = useCallback(async (jobId: string, currentFilters: Filters = EMPTY_FILTERS) => {
    setLoadingResults(true)
    try {
      setResults(await api.results(jobId, currentFilters))
      setGlobalError('')
    } catch (error) {
      setGlobalError(error instanceof Error ? error.message : '결과를 불러오지 못했습니다.')
    } finally {
      setLoadingResults(false)
    }
  }, [])

  const refreshJob = useCallback(async (jobId: string) => {
    try {
      const [nextJob, nextLogs] = await Promise.all([api.job(jobId), api.logs(jobId)])
      setJob(nextJob)
      setJobs((items) => items.some((item) => item.job_id === nextJob.job_id)
        ? items.map((item) => item.job_id === nextJob.job_id ? nextJob : item)
        : [nextJob, ...items])
      setLogs(nextLogs.logs)
      setGlobalError('')
      if (!ACTIVE.has(nextJob.status)) await loadResults(jobId)
    } catch (error) {
      localStorage.removeItem(STORAGE_KEY)
      setGlobalError(error instanceof Error ? error.message : '작업 상태를 불러오지 못했습니다.')
    }
  }, [loadResults])

  useEffect(() => {
    async function bootstrap() {
      try {
        const [regionResponse, jobResponse] = await Promise.all([api.regions(), api.jobs()])
        setRegions(regionResponse.regions)
        setJobs(jobResponse.jobs)
        const stored = localStorage.getItem(STORAGE_KEY)
        if (stored) {
          setScreen('job')
          await refreshJob(stored)
        }
      } catch (error) {
        setGlobalError(error instanceof Error ? error.message : '서버에 연결하지 못했습니다.')
      }
    }
    void bootstrap()
  }, [refreshJob])

  useEffect(() => {
    if (!job || !ACTIVE.has(job.status)) return
    const interval = window.setInterval(() => {
      void refreshJob(job.job_id)
      setTick((value) => value + 1)
    }, 1500)
    return () => window.clearInterval(interval)
  }, [job, refreshJob])

  const activeJob = useMemo(() => jobs.find((item) => ACTIVE.has(item.status)), [jobs])

  function openJob(nextJob: AuditJob) {
    localStorage.setItem(STORAGE_KEY, nextJob.job_id)
    setJob(nextJob)
    setScreen('job')
    setResults(null)
    setFilters(EMPTY_FILTERS)
    void refreshJob(nextJob.job_id)
  }

  async function cancelJob() {
    if (!job) return
    setCancelling(true)
    try {
      setJob(await api.cancelJob(job.job_id))
      await refreshJob(job.job_id)
    } catch (error) {
      setGlobalError(error instanceof Error ? error.message : '중단 요청을 보내지 못했습니다.')
    } finally {
      setCancelling(false)
    }
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <button className="brand" type="button" onClick={() => setScreen('setup')} aria-label="빅카인즈 링크 점검 시작 화면">
          <span aria-hidden="true">B</span>
          <strong>빅카인즈 링크 점검</strong>
        </button>
        <nav aria-label="주요 메뉴">
          {activeJob && <button className="active-job-link" type="button" onClick={() => openJob(activeJob)}>● 진행 중인 점검</button>}
          <button type="button" className={screen === 'setup' ? 'active' : ''} onClick={() => setScreen('setup')}>새 점검</button>
          <button type="button" className={screen === 'history' ? 'active' : ''} onClick={() => { setScreen('history'); void refreshJobs() }}>실행 기록</button>
        </nav>
      </header>

      {globalError && <div className="global-error" role="alert"><span>{globalError}</span><button type="button" onClick={() => setGlobalError('')} aria-label="오류 안내 닫기">×</button></div>}

      {screen === 'setup' && <SetupScreen regions={regions} jobs={jobs} onCreated={(created) => { setJobs((items) => [created, ...items]); openJob(created) }} />}
      {screen === 'history' && <HistoryScreen jobs={jobs} onOpen={openJob} onRefresh={refreshJobs} />}
      {screen === 'job' && job && (ACTIVE.has(job.status)
        ? <ProgressScreen job={job} logs={logs} onCancel={cancelJob} cancelling={cancelling} tick={tick} />
        : <ResultsScreen job={job} results={results} regions={regions} filters={filters} onFilters={setFilters} onApplyFilters={() => void loadResults(job.job_id, filters)} loading={loadingResults} />)}
      {screen === 'job' && !job && <main className="page"><EmptyState>작업 정보를 불러오고 있습니다.</EmptyState></main>}

      <footer>모든 데이터는 이 PC에만 저장됩니다 · 외부 로그인이나 클라우드 저장소를 사용하지 않습니다.</footer>
    </div>
  )
}
