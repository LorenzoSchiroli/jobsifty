import { useState } from 'react'
import { searchJobs, startAdvancedSearch, resumeAdvancedSearch, Job } from '../api/search'
import { useSearchMode } from '../hooks/useSearchMode'
import JobCard from '../components/JobCard'
import SearchHelp from '../components/SearchHelp'

export default function Search() {
  const { mode } = useSearchMode()
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<Job[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [searched, setSearched] = useState(false)

  // advanced clarify state
  const [threadId, setThreadId] = useState<string | null>(null)
  const [questions, setQuestions] = useState<string[]>([])
  const [answers, setAnswers] = useState<string[]>([])

  const resetAdvanced = () => {
    setThreadId(null)
    setQuestions([])
    setAnswers([])
  }

  async function handleSearch(e: React.FormEvent) {
    e.preventDefault()
    setError('')
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
      setError(err.response?.data?.detail || 'Search failed')
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
      <h2 style={{ marginBottom: '1rem' }}>Search Jobs <span style={{ fontSize: '0.8rem', opacity: 0.5 }}>({mode})</span></h2>

      <form onSubmit={handleSearch} style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.5rem' }}>
        <input value={query} onChange={e => setQuery(e.target.value)} placeholder="e.g. machine learning engineer remote" required />
        <button type="submit" disabled={loading} style={{ whiteSpace: 'nowrap' }}>
          {loading ? '...' : 'Search'}
        </button>
      </form>

      {error && <p style={{ color: '#f87171' }}>{error}</p>}

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
        {searched && !threadId && results.length === 0 && !loading && !error && (
          <p style={{ opacity: 0.4 }}>No results found.</p>
        )}
      </div>

      {!query && results.length === 0 && !loading && !threadId && <SearchHelp />}
    </div>
  )
}
