import { useEffect, useState } from 'react'
import { getPreferenceMemory, setPreferenceMemory } from '../api/preferences'

export default function Preferences() {
  const [memory, setMemory] = useState('')
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    getPreferenceMemory().then(p => setMemory(p.memory_text || '')).catch(() => {})
  }, [])

  const save = async () => {
    setError(''); setNote('')
    setBusy(true)
    try {
      await setPreferenceMemory(memory)
      setNote('Saved correctly')
    } catch {
      setError('Save failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ padding: '2rem', maxWidth: 800, margin: '0 auto' }}>
      <h2 style={{ marginTop: 0 }}>Preferences</h2>
      <p style={{ fontSize: '0.9rem', opacity: 0.7, marginTop: 0 }}>
        Used by agent mode.
      </p>
      <textarea
        value={memory}
        onChange={e => { setMemory(e.target.value); setNote('') }}
        rows={12}
        placeholder="Nothing learned yet. Run an agent search, or write your preferences here."
        style={{ width: '100%', resize: 'vertical', fontSize: '0.9rem', background: '#141414', color: '#eee', border: '1px solid #2a2a2a', borderRadius: 6, padding: '0.75rem' }}
      />
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginTop: '0.5rem' }}>
        <button onClick={save} disabled={busy}
                style={{ background: '#3b82f6', border: 'none', color: '#fff' }}>
          Save preferences
        </button>
        {note && <span style={{ fontSize: '0.9rem', color: '#4ade80' }}>✓ {note}</span>}
        {error && <span style={{ fontSize: '0.85rem', color: '#f87171' }}>{error}</span>}
      </div>
    </div>
  )
}
