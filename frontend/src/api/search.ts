import client from './client'

export interface Company {
  name: string
  country: string | null
  is_consulting: boolean | null
  is_startup: boolean | null
  financial_health_score: number | null
  industry: string | null
}

export interface Job {
  id: string
  title: string
  url: string
  location: string | null
  posted_at: string | null
  employment_type: string | null
  location_type: string | null
  seniority: string | null
  languages_required: string[]
  company: Company
  fit_score?: number | null
  fit_rationale?: string
  fit_gaps?: string
  trade_offs?: string[]
}

export const searchJobs = (query: string, signal?: AbortSignal) =>
  client.post<Job[]>('/jobs/search', { query }, { signal }).then(r => r.data)

export interface BriefItem {
  text: string
  kind: 'require' | 'prefer' | 'avoid'
  source: 'stated' | 'inferred'
  field: string | null
  value: string | boolean | number | string[] | null
}

export interface Brief {
  stated: BriefItem[]
  inferred: BriefItem[]
  avoid: BriefItem[]
  notes: string
}

export interface AgentQuestion {
  text: string
  options: string[]
}

export interface SearchLog {
  query: string
  filters: Record<string, unknown>
  matches: number
  probe: string | null
}

export interface AdvancedResponse {
  status: 'done' | 'question' | 'partial'
  thread_id: string
  question: AgentQuestion | null
  brief: Partial<Brief>
  summary: string
  searches: SearchLog[]
  results: Job[]
  usage: Record<string, unknown> | null
}

export const startAdvancedSearch = (query: string, signal?: AbortSignal) =>
  client.post<AdvancedResponse>('/jobs/search/advanced', { query }, { signal }).then(r => r.data)

export const resumeAdvancedSearch = (threadId: string, answer: string, signal?: AbortSignal) =>
  client
    .post<AdvancedResponse>('/jobs/search/advanced/resume', { thread_id: threadId, answer }, { signal })
    .then(r => r.data)
