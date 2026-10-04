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
  source: string | null
  created_at: string
  location: string | null
  employment_type: string | null
  location_type: string | null
  seniority: string | null
  languages_required: string[]
  company: Company
  fit_score?: number
  fit_rationale?: string
  fit_gaps?: string
}

export const searchJobs = (query: string) =>
  client.post<Job[]>('/jobs/search', { query }).then(r => r.data)

export interface AdvancedStart {
  thread_id: string
  clarify_questions: string[]
}

export const startAdvancedSearch = (query: string) =>
  client.post<AdvancedStart>('/jobs/search/advanced', { query }).then(r => r.data)

export const resumeAdvancedSearch = (threadId: string, answers: string[]) =>
  client
    .post<Job[]>('/jobs/search/advanced/resume', { thread_id: threadId, clarify_answers: answers })
    .then(r => r.data)
