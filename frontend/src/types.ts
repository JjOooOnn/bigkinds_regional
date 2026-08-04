export type JobStatus =
  | 'queued'
  | 'running'
  | 'cancel_requested'
  | 'cancelling'
  | 'force_terminating'
  | 'cancelled'
  | 'completed'
  | 'partial_failed'
  | 'failed'

export interface RegionOption {
  order: number
  name: string
}

export interface AuditJob {
  job_id: string
  created_at: string
  started_at: string
  ended_at: string
  start_date: string
  end_date: string
  regions: string[]
  headed: boolean
  resume: boolean
  resume_from_job_id: string
  manual_resume_available: boolean
  manual_resume_reason: string
  checkpoint_state: string
  max_issues: number | null
  timeout_seconds: number
  retries: number
  link_delay_seconds: number
  debug: boolean
  status: JobStatus
  status_label: string
  current_date: string
  current_region: string
  current_issue: string
  current_issue_order: number
  current_issue_total: number
  current_region_completed_issues: number
  current_region_total_issues: number | null
  current_issue_processed_articles: number
  current_issue_total_articles: number | null
  current_publisher: string
  current_article_title: string
  total_regions: number
  completed_regions: number
  total_region_units: number
  known_links: number
  processed_links: number
  normal_count: number
  error_count: number
  progress_percent: number
  download_available: boolean
  excel_file_name: string
  error_message: string
}

export interface JobLog {
  created_at: string
  level: string
  message: string
}

export interface ErrorResult {
  requested_date: string
  region: string
  publisher: string
  article_title: string
  link_working_yn: string
  verdict: string
  error_message: string
  original_url: string
  final_url: string
  http_status: number | null
}

export interface ResultResponse {
  summary: {
    total_links: number
    normal_count: number
    error_count: number
    normal_rate: number
    verdict_counts: Record<string, number>
  }
  total_errors: number
  errors: ErrorResult[]
}
