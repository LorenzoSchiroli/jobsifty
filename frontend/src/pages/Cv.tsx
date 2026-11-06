import { useEffect, useRef, useState } from 'react'
import { extractCV, getCV, redactCV, saveCV } from '../api/cv'

const KIND_LABEL: Record<string, string> = {
  email: 'email', phone: 'phone number', link: 'link', address: 'address',
  birth_date: 'date of birth', personal: 'personal detail', id: 'ID number',
  consent: 'consent statement', name: 'your name', surname: 'your surname',
}

const describe = (counts: Record<string, number>) =>
  Object.entries(counts)
    .map(([k, n]) => {
      const label = KIND_LABEL[k] ?? k
      return n > 1 ? `${label} (${n}×)` : label
    })
    .join(', ')

const btn: React.CSSProperties = {
  padding: '0.45rem 0.9rem', borderRadius: 6, border: '1px solid #2a2a2a',
  background: '#1f1f1f', color: '#fff', cursor: 'pointer',
}

export default function Cv() {
  const [text, setText] = useState('')
  const [savedText, setSavedText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [removedNote, setRemovedNote] = useState('')
  const [savedNote, setSavedNote] = useState('')
  const [findings, setFindings] = useState<Record<string, number> | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    getCV().then(({ cv_text }) => {
      setText(cv_text || '')
      setSavedText(cv_text || '')
    }).catch(() => {})
  }, [])

  useEffect(() => {
    if (text === savedText) return
    const handler = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = '' }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [text, savedText])

  const detail = (err: any, fallback: string) => {
    const d = err.response?.data?.detail
    return typeof d === 'string' ? d : fallback
  }

  const handleFile = async (file: File) => {
    setError(''); setRemovedNote(''); setSavedNote('')
    setBusy(true)
    try {
      const res = await extractCV(file)
      setText(res.text)
    } catch (err: any) {
      setError(detail(err, 'Upload failed'))
    } finally {
      setBusy(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const handleRedact = async () => {
    setError(''); setRemovedNote(''); setSavedNote('')
    setBusy(true)
    try {
      const res = await redactCV(text)
      setText(res.text)
      const d = describe(res.removed)
      setRemovedNote(d ? `Removed: ${d}` : 'Nothing to remove')
    } catch (err: any) {
      setError(detail(err, 'Could not remove private info'))
    } finally {
      setBusy(false)
    }
  }

  const doSave = async (confirm: boolean) => {
    setError(''); setRemovedNote(''); setSavedNote('')
    setBusy(true)
    try {
      const res = await saveCV(text, confirm)
      setSavedText(res.cv_text || '')
      setFindings(null)
      setSavedNote('Saved correctly')
    } catch (err: any) {
      const f = err.response?.status === 409 ? err.response?.data?.detail?.findings : null
      if (f) setFindings(f)
      else { setFindings(null); setError(detail(err, 'Save failed')) }
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ padding: '2rem', maxWidth: 800, margin: '0 auto' }}>
      <h2 style={{ marginTop: 0 }}>CV</h2>
      <p style={{ fontSize: '0.9rem', opacity: 0.7, marginTop: 0 }}>Used by agent mode.</p>
      <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', marginBottom: '0.75rem' }}>
        <button style={btn} disabled={busy} onClick={() => fileRef.current?.click()}>Upload file</button>
        <input ref={fileRef} type="file" accept=".pdf,.docx,.txt" style={{ display: 'none' }}
               onChange={e => { const f = e.target.files?.[0]; if (f) handleFile(f) }} />
        <button style={btn} disabled={busy} onClick={handleRedact}>Remove private info</button>
        <button style={{ ...btn, background: '#3b82f6', border: 'none' }} disabled={busy}
                onClick={() => doSave(false)}>Save CV</button>
        {savedNote && (
          <span style={{ alignSelf: 'center', fontSize: '0.9rem' }}>
            <span style={{ color: '#4ade80' }}>✓ {savedNote}</span>
          </span>
        )}
      </div>
      <textarea
        value={text}
        onChange={e => { setText(e.target.value); setSavedNote('') }}
        rows={26}
        style={{ width: '100%', boxSizing: 'border-box', resize: 'vertical', fontSize: '0.85rem', background: '#141414', color: '#eee', border: '1px solid #2a2a2a', borderRadius: 6, padding: '0.75rem' }}
      />
      <div style={{ marginTop: '0.5rem', fontSize: '0.85rem', minHeight: '1.2rem' }}>
        {removedNote && <div style={{ opacity: 0.7 }}>{removedNote}</div>}
        {error && <div style={{ color: '#f87171' }}>{error}</div>}
      </div>

      {findings && (
        <div role="dialog" aria-modal="true"
             style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', zIndex: 50, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <div style={{ background: '#141414', border: '1px solid #2a2a2a', borderRadius: 8, padding: '1.25rem', maxWidth: 440 }}>
            <h3 style={{ marginTop: 0 }}>Private info detected</h3>
            <p style={{ fontSize: '0.9rem', lineHeight: 1.4 }}>
              Your CV still contains: {describe(findings)}. It will be sent to external AI models that may keep it. Save anyway?
            </p>
            <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'flex-end' }}>
              <button style={btn} onClick={() => setFindings(null)}>Go back</button>
              <button style={{ ...btn, background: '#3b82f6', border: 'none' }} disabled={busy}
                      onClick={() => doSave(true)}>Save anyway</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
