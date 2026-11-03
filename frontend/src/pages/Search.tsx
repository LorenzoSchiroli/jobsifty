import { useRef, useState } from 'react'
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

  // Runs one request; a newer one aborts it and its result and error are dropped.
  async function track(call: (signal: AbortSignal) => Promise<void>) {
    abortRef.current?.abort()
    const ctrl = new AbortController()
    abortRef.current = ctrl
    setError('')
    setLoading(true)
    try {
      await call(ctrl.signal)
    } catch (err: any) {
      if (ctrl.signal.aborted || axios.isCancel(err)) return
      setError(err.response?.data?.detail || 'Search failed')
    } finally {
      if (abortRef.current === ctrl) setLoading(false)
    }
  }

  function applyRun(r: AdvancedResponse) {
    setRun(r)
    setResults(r.results)
  }

  async function handleSearch(e: React.FormEvent) {
    e.preventDefault()
    setResults([])
    setRun(null)
    setSearched(true)
    await track(async signal => {
      if (mode === 'basic') setResults(await searchJobs(query))
      else applyRun(await startAdvancedSearch(query, signal))
    })
  }

  async function answer(a: string) {
    if (!run) return
    const threadId = run.thread_id
    await track(async signal => {
      applyRun(await resumeAdvancedSearch(threadId, a, signal))
    })
  }

  return (
    <div style={{ maxWidth: 720, margin: '4vh auto', padding: '2rem' }}>
      <h2 style={{ marginBottom: '1rem' }}>Search Jobs <span style={{ fontSize: '0.8rem', opacity: 0.5 }}>({mode})</span></h2>

      <form onSubmit={handleSearch} style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.5rem' }}>
        <input value={query} onChange={e => setQuery(e.target.value)} placeholder="e.g. machine learning engineer remote" required />
        <button type="submit" disabled={loading} style={{ whiteSpace: 'nowrap' }}>
          {loading ? '...' : 'Search'}
        </button>
      </form>

      {error && <p style={{ color: '#f87171' }}>{error}</p>}

      {mode === 'advanced' && (run || loading) && <AgentRun run={run} running={loading} onAnswer={answer} />}

      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
        {results.map(job => <JobCard key={job.id} job={job} />)}
        {searched && !pending && results.length === 0 && !loading && !error && (
          <p style={{ opacity: 0.4 }}>No results found.</p>
        )}
      </div>

      {!query && results.length === 0 && !loading && !pending && <SearchHelp />}
    </div>
  )
}
