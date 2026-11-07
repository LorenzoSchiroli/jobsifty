import { useState } from 'react'
import { AdvancedResponse } from '../api/search'

const box = { margin: '1rem 0', padding: '1rem', border: '1px solid #2a2a2a', borderRadius: 8 } as const
const heading = { fontSize: '0.85rem', opacity: 0.7, marginBottom: '0.4rem' } as const

// The posting window a search ran on, e.g. " · last 7 days"
function windowText(hours: number | null): string {
  if (hours === null) return ''
  if (hours < 24) return ` · last ${hours} hours`
  const days = Math.round(hours / 24)
  return ` · last ${days} day${days === 1 ? '' : 's'}`
}

interface Props {
  run: AdvancedResponse | null
  running: boolean
  onAnswer: (answer: string) => void
  // 'top' = everything before the result cards, 'searches' = the log after them
  part: 'top' | 'searches'
}

export default function AgentRun({ run, running, onAnswer, part }: Props) {
  const [text, setText] = useState('')
  // Stated requires and avoids are kept in every result (the page shows them as required filters).
  const kept = [...(run?.brief?.stated ?? []), ...(run?.brief?.avoid ?? []).filter(i => i.source === 'stated')]
  // Stated items no field carries are judged by the LLM at the end; a partial run never got there.
  const unchecked = kept.filter(i => i.field === null).map(i => `"${i.text}"`)
  const question = run?.status === 'question' ? run.question : null

  function send(answer: string) {
    const a = answer.trim()
    if (!a) return
    setText('')
    onAnswer(a)
  }

  if (part === 'searches') {
    if (!run || question || running || run.searches.length === 0) return null
    return (
      <div style={box}>
        <div style={heading}>How I searched</div>
        {run.searches.map((s, i) => {
          // matches: jobs meeting every soft filter (null without soft filters); new_jobs: not found before
          const newJobs = `${s.new_jobs} new job${s.new_jobs === 1 ? '' : 's'}`
          const count = s.matches === null ? newJobs : `${s.matches} matching its filters, ${newJobs}`
          return (
            <div key={i} style={{ fontSize: '0.78rem', opacity: 0.6, marginBottom: '0.15rem' }}>
              {s.probe ? `what if ${s.probe}: ` : ''}"{s.query}" — {count}{windowText(s.max_age_hours)}
            </div>
          )
        })}
      </div>
    )
  }

  return (
    <div>
      {question && !running && (
        <div style={box}>
          <div style={{ fontSize: '0.9rem', marginBottom: '0.6rem' }}>{question.text}</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem', marginBottom: '0.6rem' }}>
            {question.options.map(o => (
              <button key={o} type="button" onClick={() => send(o)}>{o}</button>
            ))}
          </div>
          <form onSubmit={e => { e.preventDefault(); send(text) }} style={{ display: 'flex', gap: '0.5rem' }}>
            <input value={text} onChange={e => setText(e.target.value)} placeholder="Or answer in your own words" />
            <button type="submit" disabled={!text.trim()}>Send</button>
          </form>
        </div>
      )}

      {run && !question && (run.summary || run.status === 'partial') && (
        <div style={box}>
          <div style={heading}>Summary</div>
          {run.status === 'partial' && (
            <div style={{ fontSize: '0.8rem', color: '#fbbf24', marginBottom: '0.4rem' }}>
              The assistant could not finish (an LLM error or timeout): these are the most relevant jobs found so far, without fit scores.
              {unchecked.length > 0 && ` They were not checked against ${unchecked.join(', ')}.`}
            </div>
          )}
          {run.summary && <div style={{ fontSize: '0.9rem' }}>{run.summary}</div>}
        </div>
      )}
    </div>
  )
}
