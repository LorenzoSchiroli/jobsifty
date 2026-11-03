import { useEffect, useRef, useState } from 'react'
import axios from 'axios'
import { searchJobs, startAdvancedSearch, resumeAdvancedSearch, Job, AdvancedResponse } from '../api/search'
import { useSearchMode } from '../hooks/useSearchMode'
import JobCard from '../components/JobCard'
import AgentRun from '../components/AgentRun'
import SearchHelp from '../components/SearchHelp'

export default function Search() {
  const { mode } = useSearchMode()
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
    setLoading(true)
    try {
      const data = await fetch(ctrl.signal)
      if (abortRef.current === ctrl) apply(data)
    } catch (err: any) {
      if (abortRef.current !== ctrl || ctrl.signal.aborted || axios.isCancel(err)) return
      setError(err.response?.data?.detail || 'Search failed')
    } finally {
      if (abortRef.current === ctrl) setLoading(false)
    }
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
      <h2 style={{ marginBottom: '1rem' }}>Search Jobs <span style={{ fontSize: '0.8rem', opacity: 0.5 }}>({mode})</span></h2>

      <form onSubmit={handleSearch} style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.5rem' }}>
        <input value={query} onChange={e => setQuery(e.target.value)} placeholder="e.g. machine learning engineer remote" required />
        <button type="submit" style={{ whiteSpace: 'nowrap' }}>
          Search
        </button>
      </form>

      {error && <p style={{ color: '#f87171' }}>{error}</p>}

      {mode === 'advanced' && (run || loading) && <AgentRun part="top" run={run} running={loading} onAnswer={answer} />}

      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
        {results.map(job => <JobCard key={job.id} job={job} />)}
        {searched && !pending && results.length === 0 && !loading && !error && (
          <p style={{ opacity: 0.4 }}>No results found.</p>
        )}
      </div>

      {mode === 'advanced' && run && <AgentRun part="searches" run={run} running={loading} onAnswer={answer} />}

      {!query && results.length === 0 && !loading && !pending && <SearchHelp />}
    </div>
  )
}
