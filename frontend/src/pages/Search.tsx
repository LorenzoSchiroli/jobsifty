import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { me } from '../api/auth'
import { searchJobs, startAdvancedSearch, resumeAdvancedSearch, Job } from '../api/search'
import { useSearchMode } from '../hooks/useSearchMode'
import JobCard from '../components/JobCard'
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

  // agent clarify state
  const [threadId, setThreadId] = useState<string | null>(null)
  const [questions, setQuestions] = useState<string[]>([])
  const [answers, setAnswers] = useState<string[]>([])

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

  const resetAdvanced = () => {
    setThreadId(null)
    setQuestions([])
    setAnswers([])
  }

  async function handleSearch(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    setNoCvError(false)
    setResults([])
    resetAdvanced()
    setLoading(true)
    setSearched(true)
    try {
      if (mode === 'basic') {
        setResults(await searchJobs(query))
      } else {
        const start = await startAdvancedSearch(query)
        setThreadId(start.thread_id)
        setQuestions(start.clarify_questions)
        setAnswers(new Array(start.clarify_questions.length).fill(''))
        if (start.clarify_questions.length === 0) {
          setResults(await resumeAdvancedSearch(start.thread_id, []))
          resetAdvanced()
        }
      }
    } catch (err: any) {
      const detail = err.response?.data?.detail
      if (err.response?.status === 400 && typeof detail === 'string' && detail.startsWith('No CV saved')) setNoCvError(true)
      else setError(detail || 'Search failed')
    } finally {
      setLoading(false)
    }
  }

  async function submitAnswers(e: React.FormEvent) {
    e.preventDefault()
    if (!threadId) return
    setError('')
    setLoading(true)
    try {
      setResults(await resumeAdvancedSearch(threadId, answers))
      resetAdvanced()
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Search failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{ maxWidth: 720, margin: '4vh auto', padding: '2rem' }}>
      <h2 style={{ marginBottom: '1rem' }}>Search Jobs</h2>

      <form onSubmit={handleSearch} className={`search-form${mode === 'agent' ? ' agent' : ''}`}
            style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.5rem' }}>
        <input className="search-input" value={query} onChange={e => setQuery(e.target.value)} placeholder="e.g. machine learning engineer remote" required />
        <button type="submit" className="search-btn" disabled={loading} style={{ whiteSpace: 'nowrap' }}>
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

      {threadId && questions.length > 0 && (
        <form onSubmit={submitAnswers} style={{ margin: '1rem 0', padding: '1rem', border: '1px solid #2a2a2a', borderRadius: 8 }}>
          <div style={{ fontSize: '0.85rem', opacity: 0.7, marginBottom: '0.5rem' }}>A couple quick questions:</div>
          {questions.map((q, i) => (
            <div key={i} style={{ marginBottom: '0.6rem' }}>
              <label style={{ display: 'block', fontSize: '0.85rem', marginBottom: '0.2rem' }}>{q}</label>
              <input
                value={answers[i] || ''}
                onChange={e => setAnswers(prev => prev.map((a, j) => (j === i ? e.target.value : a)))}
                style={{ width: '100%' }}
              />
            </div>
          ))}
          <button type="submit" disabled={loading}>{loading ? '...' : 'Continue'}</button>
        </form>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
        {results.map(job => <JobCard key={job.id} job={job} />)}
        {searched && !threadId && results.length === 0 && !loading && !error && !noCvError && (
          <p style={{ opacity: 0.4 }}>No results found.</p>
        )}
      </div>

      {!query && results.length === 0 && !loading && !threadId && <SearchHelp />}
    </div>
  )
}
