import { useEffect, useRef, useState } from 'react'
import axios from 'axios'
import { useNavigate } from 'react-router-dom'
import { me } from '../api/auth'
import { searchJobs, startAdvancedSearch, resumeAdvancedSearch, Job, AdvancedResponse, ActiveFilters as Filters } from '../api/search'
import { useSearchMode } from '../hooks/useSearchMode'
import JobCard from '../components/JobCard'
import AgentRun from '../components/AgentRun'
import ActiveFilters from '../components/ActiveFilters'
import SearchHelp from '../components/SearchHelp'
import type { SearchMode } from '../hooks/useSearchMode'

// A spinner and the step the search is on; each new step replaces the last.
function Progress({ status }: { status: string }) {
  return (
    <div role="status" style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', margin: '0.75rem 0', opacity: 0.75 }}>
      <span className="spinner" aria-hidden />
      <span style={{ fontSize: '0.9rem' }}>{status}…</span>
    </div>
  )
}

export default function Search() {
  const { mode, setMode } = useSearchMode()
  const navigate = useNavigate()
  const [cvPrompt, setCvPrompt] = useState(false)
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<Job[]>([])
  const [filters, setFilters] = useState<Filters | null>(null)  // what the last answer filtered on
  const [loading, setLoading] = useState(false)
  const [status, setStatus] = useState('')  // the step the running request is on
  const [error, setError] = useState('')
  const [searched, setSearched] = useState(false)
  const [run, setRun] = useState<AdvancedResponse | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  const pending = run?.status === 'question'
  const closest = mode === 'agent' ? run?.closest ?? [] : []  // agent mode: jobs that miss a requirement, shown apart

  // Runs one request; a newer one (or a mode switch) aborts it, and a response
  // or error from a request that is no longer current is dropped.
  async function track<T>(fetch: (signal: AbortSignal, onStatus: (s: string) => void) => Promise<T>,
                          apply: (data: T) => void) {
    abortRef.current?.abort()
    const ctrl = new AbortController()
    abortRef.current = ctrl
    setError('')
    setStatus('Starting')
    setLoading(true)
    try {
      const data = await fetch(ctrl.signal, s => { if (abortRef.current === ctrl) setStatus(s) })
      if (abortRef.current === ctrl) apply(data)
    } catch (err: any) {
      if (abortRef.current !== ctrl || ctrl.signal.aborted || axios.isCancel(err)) return
      setError(err.response?.data?.detail || 'Search failed')
    } finally {
      if (abortRef.current === ctrl) setLoading(false)
    }
  }

  // Agent mode works without a CV, but matches better with one: say so, also for a stored "agent" choice.
  useEffect(() => {
    if (mode !== 'agent') return
    me().then(u => { if (!u.has_cv) setCvPrompt(true) }).catch(() => {})
  }, [])

  const chooseMode = async (m: SearchMode) => {
    setCvPrompt(false)
    setMode(m)
    if (m === 'agent' && !(await me().then(u => u.has_cv).catch(() => true))) setCvPrompt(true)
  }

  function applyRun(r: AdvancedResponse) {
    setRun(r)
    setResults(r.results)
    setFilters(r.filters)
  }

  // Aborts the running request and clears the page, as before the search; the server stops the
  // work (and an agent run deletes its thread), so a question it had asked cannot be answered.
  function stop() {
    abortRef.current?.abort()
    abortRef.current = null
    setRun(null)
    setResults([])
    setFilters(null)
    setError('')
    setLoading(false)
    setSearched(false)
  }

  useEffect(stop, [mode])

  async function handleSearch(e: React.FormEvent) {
    e.preventDefault()
    setResults([])
    setFilters(null)
    setRun(null)
    setSearched(true)
    if (mode === 'basic') {
      await track((signal, onStatus) => searchJobs(query, onStatus, signal), r => { setResults(r.results); setFilters(r.filters) })
    }
    else await track((signal, onStatus) => startAdvancedSearch(query, onStatus, signal), applyRun)
  }

  async function answer(a: string) {
    if (!run) return
    const threadId = run.thread_id
    await track((signal, onStatus) => resumeAdvancedSearch(threadId, a, onStatus, signal), applyRun)
  }

  return (
    <div style={{ maxWidth: 720, margin: '4vh auto', padding: '2rem' }}>
      <h2 style={{ marginBottom: '1rem' }}>Search Jobs</h2>

      <form onSubmit={handleSearch} className={`search-form${mode === 'agent' ? ' agent' : ''}`}
            style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.5rem' }}>
        <input className="search-input" value={query} onChange={e => setQuery(e.target.value)} placeholder="e.g. machine learning engineer remote" required />
        {/* Stop while a request runs: closing it stops the work on the server too. Two keys, so two
            elements: a reused one would be a submit button again by the end of the Stop click, and submit. */}
        {loading
          ? <button key="stop" type="button" className="search-btn stop-btn" onClick={stop} style={{ whiteSpace: 'nowrap' }}>Stop</button>
          : <button key="search" type="submit" className="search-btn" style={{ whiteSpace: 'nowrap' }}>Search</button>}
      </form>

      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.75rem', flexWrap: 'wrap' }}>
        <div role="group" aria-label="Search mode" style={{ display: 'flex', border: '1px solid #333', borderRadius: 6, overflow: 'hidden' }}>
          {(['basic', 'agent'] as const).map(m => (
            <button
              key={m}
              type="button"
              aria-pressed={mode === m}
              className={`mode-btn${mode === m && m === 'agent' ? ' agent-on' : ''}`}
              onClick={() => chooseMode(m)}
              style={{
                padding: '0.25rem 0.8rem', fontSize: '0.85rem', border: 'none', borderRadius: 0,
                background: mode === m ? '#3b82f6' : '#1a1a1a', color: '#fff', textTransform: 'capitalize',
              }}
            >
              {m === 'agent' ? '✦ agent' : m}
            </button>
          ))}
        </div>
      </div>

      {cvPrompt && (
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', flexWrap: 'wrap', padding: '0.75rem 1rem', marginBottom: '1rem', border: '1px solid #2a2a2a', borderRadius: 8, background: '#141414' }}>
          <span style={{ flex: 1, fontSize: '0.9rem' }}>No CV saved: the agent works from your request and past searches only. Add one to have jobs matched to your profile.</span>
          <button type="button" onClick={() => setCvPrompt(false)} style={{ fontSize: '0.85rem', padding: '0.3rem 0.8rem' }}>
            Not now
          </button>
          <button type="button" onClick={() => navigate('/cv')}
                  style={{ fontSize: '0.85rem', padding: '0.3rem 0.8rem', background: '#3b82f6', border: 'none', color: '#fff' }}>
            Add CV
          </button>
        </div>
      )}

      {error && <p style={{ color: '#f87171' }}>{error}</p>}
      {loading && <Progress status={status} />}

      {filters && !loading && <ActiveFilters filters={filters} />}

      {mode === 'agent' && (run || loading) && <AgentRun part="top" run={run} running={loading} onAnswer={answer} />}

      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
        {results.map(job => <JobCard key={job.id} job={job} />)}
        {searched && !pending && results.length === 0 && !closest.length && !loading && !error && (
          <p style={{ opacity: 0.4 }}>No results found.</p>
        )}
      </div>

      {closest.length > 0 && !loading && (
        <div style={{ marginTop: '1.5rem' }}>
          <h3 style={{ fontSize: '1rem', marginBottom: '0.25rem' }}>Closest jobs</h3>
          <p style={{ fontSize: '0.85rem', opacity: 0.6, marginTop: 0, marginBottom: '0.75rem' }}>
            {results.length === 0
              ? 'No job meets all your requirements. These come closest; each says what it misses.'
              : `Only ${results.length} job${results.length === 1 ? ' meets' : 's meet'} all your requirements. These come closest; each says what it misses.`}
          </p>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
            {closest.map(job => <JobCard key={job.id} job={job} />)}
          </div>
        </div>
      )}

      {mode === 'agent' && run && <AgentRun part="searches" run={run} running={loading} onAnswer={answer} />}

      {!query && results.length === 0 && !loading && !pending && <SearchHelp />}
    </div>
  )
}
