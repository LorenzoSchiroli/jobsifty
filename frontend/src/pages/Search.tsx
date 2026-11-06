import { useEffect, useRef, useState } from 'react'
import axios from 'axios'
import { Link, useNavigate } from 'react-router-dom'
import { me } from '../api/auth'
import { searchJobs, startAdvancedSearch, resumeAdvancedSearch, Job, AdvancedResponse } from '../api/search'
import { useSearchMode } from '../hooks/useSearchMode'
import JobCard from '../components/JobCard'
import AgentRun from '../components/AgentRun'
import SearchHelp from '../components/SearchHelp'
import type { SearchMode } from '../hooks/useSearchMode'

export default function Search() {
  const { mode, setMode } = useSearchMode()
  const navigate = useNavigate()
  const [cvPrompt, setCvPrompt] = useState(false)
  const [noCvError, setNoCvError] = useState(false)
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<Job[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [searched, setSearched] = useState(false)
  const [run, setRun] = useState<AdvancedResponse | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  const pending = run?.status === 'question'

  // Runs one request; a newer one (or a mode switch) aborts it, and a response
  // or error from a request that is no longer current is dropped.
  async function track<T>(fetch: (signal: AbortSignal) => Promise<T>, apply: (data: T) => void) {
    abortRef.current?.abort()
    const ctrl = new AbortController()
    abortRef.current = ctrl
    setError('')
    setNoCvError(false)
    setLoading(true)
    try {
      const data = await fetch(ctrl.signal)
      if (abortRef.current === ctrl) apply(data)
    } catch (err: any) {
      if (abortRef.current !== ctrl || ctrl.signal.aborted || axios.isCancel(err)) return
      const detail = err.response?.data?.detail
      if (err.response?.status === 400 && typeof detail === 'string' && detail.startsWith('No CV saved')) setNoCvError(true)
      else setError(detail || 'Search failed')
    } finally {
      if (abortRef.current === ctrl) setLoading(false)
    }
  }

  // A stored "agent" choice can outlive the CV (e.g. after it was deleted): ask again.
  useEffect(() => {
    if (mode !== 'agent') return
    me().then(u => { if (!u.has_cv) setCvPrompt(true) }).catch(() => {})
  }, [])

  const chooseMode = async (m: SearchMode) => {
    setCvPrompt(false)
    if (m === 'agent') {
      const hasCv = await me().then(u => u.has_cv).catch(() => true)
      if (!hasCv) { setCvPrompt(true); return }
    }
    setMode(m)
  }

  function applyRun(r: AdvancedResponse) {
    setRun(r)
    setResults(r.results)
  }

  useEffect(() => {
    abortRef.current?.abort()
    abortRef.current = null
    setRun(null)
    setResults([])
    setError('')
    setNoCvError(false)
    setLoading(false)
    setSearched(false)
  }, [mode])

  async function handleSearch(e: React.FormEvent) {
    e.preventDefault()
    setResults([])
    setRun(null)
    setSearched(true)
    if (mode === 'basic') await track(signal => searchJobs(query, signal), setResults)
    else await track(signal => startAdvancedSearch(query, signal), applyRun)
  }

  async function answer(a: string) {
    if (!run) return
    const threadId = run.thread_id
    await track(signal => resumeAdvancedSearch(threadId, a, signal), applyRun)
  }

  return (
    <div style={{ maxWidth: 720, margin: '4vh auto', padding: '2rem' }}>
      <h2 style={{ marginBottom: '1rem' }}>Search Jobs</h2>

      <form onSubmit={handleSearch} className={`search-form${mode === 'agent' ? ' agent' : ''}`}
            style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.5rem' }}>
        <input className="search-input" value={query} onChange={e => setQuery(e.target.value)} placeholder="e.g. machine learning engineer remote" required />
        {/* Busy while a request runs, but still clickable: a new submit replaces the running one */}
        <button type="submit" className="search-btn" aria-busy={loading} style={{ whiteSpace: 'nowrap', opacity: loading ? 0.6 : 1 }}>
          {loading ? '...' : 'Search'}
        </button>
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
          <span style={{ flex: 1, fontSize: '0.9rem' }}>No CV saved. Agent mode needs one. Do you want to add it?</span>
          <button type="button" onClick={() => { setCvPrompt(false); setMode('basic') }} style={{ fontSize: '0.85rem', padding: '0.3rem 0.8rem' }}>
            Not now
          </button>
          <button type="button" onClick={() => { setMode('agent'); navigate('/cv') }}
                  style={{ fontSize: '0.85rem', padding: '0.3rem 0.8rem', background: '#3b82f6', border: 'none', color: '#fff' }}>
            Add CV
          </button>
        </div>
      )}

      {error && <p style={{ color: '#f87171' }}>{error}</p>}
      {noCvError && (
        <p style={{ color: '#f87171' }}>
          No CV saved. <Link to="/cv" className="text-link">Add your CV</Link> to use agent mode.
        </p>
      )}

      {mode === 'agent' && (run || loading) && <AgentRun part="top" run={run} running={loading} onAnswer={answer} />}

      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
        {results.map(job => <JobCard key={job.id} job={job} />)}
        {searched && !pending && results.length === 0 && !loading && !error && !noCvError && (
          <p style={{ opacity: 0.4 }}>No results found.</p>
        )}
      </div>

      {mode === 'agent' && run && <AgentRun part="searches" run={run} running={loading} onAnswer={answer} />}

      {!query && results.length === 0 && !loading && !pending && <SearchHelp />}
    </div>
  )
}
