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
  misses?: string[]  // agent mode, a closest job: the stated requirements it does not meet
}

// A request that reports its steps: the API answers one JSON object per line, {"status": "..."}
// for each step, then {"result": ...} or {"error": ...} (backend/progress.py). onStatus gets each
// status as it arrives; errors keep axios's shape (err.response.data.detail) for the callers.
async function postWithProgress<T>(url: string, body: unknown, onStatus: (s: string) => void,
                                   signal?: AbortSignal): Promise<T> {
  let seen = 0
  const parse = (text: string) => text.split('\n').filter(Boolean).map(l => JSON.parse(l))
  try {
    const r = await client.post<string>(url, body, {
      signal, responseType: 'text', headers: { Accept: 'application/x-ndjson' },
      onDownloadProgress: e => {
        const text: string = (e.event?.target as XMLHttpRequest | undefined)?.responseText ?? ''
        const done = text.slice(0, text.lastIndexOf('\n') + 1)  // complete lines only
        const lines = parse(done)
        lines.slice(seen).forEach(l => { if (l.status) onStatus(l.status) })
        seen = lines.length
      },
    })
    const last = parse(r.data).pop()
    if (!last || 'error' in last) throw { response: { data: { detail: last?.error ?? 'Search failed' } } }
    return last.result as T
  } catch (err: any) {
    // An error before the stream started (401, 404, 503) comes as text: give callers its JSON.
    if (typeof err.response?.data === 'string') {
      try { err.response.data = JSON.parse(err.response.data) } catch { /* not JSON: leave it */ }
    }
    throw err
  }
}

// What a search filtered on, as labels; the same shape in basic and agent mode
export interface ActiveFilters {
  required: string[]   // every result meets these
  preferred: string[]  // rank matching jobs higher, exclude nothing
}

export interface SearchResponse {
  filters: ActiveFilters
  results: Job[]
}

export const searchJobs = (query: string, onStatus: (s: string) => void, signal?: AbortSignal) =>
  postWithProgress<SearchResponse>('/jobs/search', { query }, onStatus, signal)

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
  // jobs of the window meeting every soft filter; null when the search set none
  matches: number | null
  // jobs of the window no earlier search of the run had found
  new_jobs: number
  // what a loosened search eased, in the filters panel's words ("in Munich → in Germany")
  loosened: string[]
  // the posting window the search ran on; null when it had none
  max_age_hours: number | null
}

export interface AdvancedResponse {
  status: 'done' | 'question' | 'partial'
  thread_id: string
  question: AgentQuestion | null
  filters: ActiveFilters
  brief: Partial<Brief>
  summary: string
  searches: SearchLog[]
  results: Job[]  // the matches: every stated requirement met
  closest: Job[]  // the closest jobs filling the list up to 10, each with its misses
  usage: Record<string, unknown> | null
}

export const startAdvancedSearch = (query: string, onStatus: (s: string) => void, signal?: AbortSignal) =>
  postWithProgress<AdvancedResponse>('/jobs/search/advanced', { query }, onStatus, signal)

export const resumeAdvancedSearch = (threadId: string, answer: string, onStatus: (s: string) => void,
                                     signal?: AbortSignal) =>
  postWithProgress<AdvancedResponse>('/jobs/search/advanced/resume', { thread_id: threadId, answer },
                                     onStatus, signal)
