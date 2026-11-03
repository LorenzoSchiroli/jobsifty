import { useState } from 'react'
import { AdvancedResponse, BriefItem } from '../api/search'

const box = { margin: '1rem 0', padding: '1rem', border: '1px solid #2a2a2a', borderRadius: 8 } as const
const heading = { fontSize: '0.85rem', opacity: 0.7, marginBottom: '0.4rem' } as const

function Items({ title, items }: { title: string; items: BriefItem[] }) {
  if (items.length === 0) return null
  return (
    <div style={{ marginBottom: '0.6rem' }}>
      <div style={{ fontSize: '0.8rem', opacity: 0.6, marginBottom: '0.25rem' }}>{title}</div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.3rem' }}>
        {items.map((it, i) => (
          <span key={i} style={{ fontSize: '0.75rem', background: '#2a2a2a', padding: '0.15rem 0.5rem', borderRadius: 4 }}>
            {it.kind === 'avoid' ? 'avoid: ' : it.kind === 'prefer' ? 'prefer: ' : ''}{it.text}
          </span>
        ))}
      </div>
    </div>
  )
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
  const stated = run?.brief?.stated ?? []
  const fromYou = [...(run?.brief?.inferred ?? []), ...(run?.brief?.avoid ?? [])]
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
        {run.searches.map((s, i) => (
          <div key={i} style={{ fontSize: '0.78rem', opacity: 0.6, marginBottom: '0.15rem' }}>
            {s.probe ? `what if ${s.probe}: ` : ''}"{s.query}" — {s.matches} match{s.matches === 1 ? '' : 'es'}
          </div>
        ))}
      </div>
    )
  }

  return (
    <div>
      {running && <p style={{ opacity: 0.6 }}>Searching… this can take a couple of minutes</p>}

      {run && (stated.length > 0 || fromYou.length > 0) && (
        <div style={box}>
          <div style={heading}>What I understood</div>
          <Items title="You asked for (kept in every result)" items={stated} />
          <Items title="From your CV and past searches" items={fromYou} />
        </div>
      )}

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
              The search ran out of budget before it finished: these are the best results found so far.
            </div>
          )}
          <div style={{ fontSize: '0.9rem' }}>{run.summary}</div>
        </div>
      )}
    </div>
  )
}
