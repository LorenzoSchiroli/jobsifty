import { useEffect, useState } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { me, UserResponse } from '../api/auth'
import { getPreferenceMemory, setPreferenceMemory } from '../api/preferences'
import { useSearchMode } from '../hooks/useSearchMode'

export default function Sidebar({ open, onToggle }: { open: boolean; onToggle: () => void }) {
  const navigate = useNavigate()
  const location = useLocation()
  const { mode, setMode } = useSearchMode()
  const [user, setUser] = useState<UserResponse | null>(null)
  const [memory, setMemory] = useState('')
  const [savedNote, setSavedNote] = useState('')

  useEffect(() => {
    me().then(setUser).catch(() => {})
    getPreferenceMemory().then(p => setMemory(p.memory_text || '')).catch(() => {})
  }, [])

  const logout = () => {
    localStorage.removeItem('access_token')
    navigate('/login')
  }

  const saveMemory = async () => {
    await setPreferenceMemory(memory)
    setSavedNote('Saved')
    setTimeout(() => setSavedNote(''), 1500)
  }

  const navLink = (path: string, label: string) => (
    <div
      onClick={() => navigate(path)}
      style={{
        cursor: 'pointer', padding: '0.4rem 0.6rem', borderRadius: 6,
        background: location.pathname === path ? '#2a2a2a' : 'transparent',
      }}
    >
      {label}
    </div>
  )

  return (
    <aside
      aria-hidden={!open}
      style={{
        position: 'fixed', top: 0, left: 0, zIndex: 30,
        width: 240, minWidth: 240, height: '100vh', borderRight: '1px solid #2a2a2a',
        padding: '1rem', display: 'flex', flexDirection: 'column', gap: '1rem', background: '#0f0f0f',
        overflowY: 'auto',
        transform: open ? 'translateX(0)' : 'translateX(-100%)',
        transition: 'transform 200ms ease',
      }}
      {...(!open ? ({ inert: '' } as unknown as Record<string, string>) : {})}
    >
      <button onClick={onToggle} aria-label="Collapse sidebar"
              style={{ alignSelf: 'flex-end', background: 'transparent', color: '#aaa', border: 'none', cursor: 'pointer', fontSize: '1.1rem' }}>
        ☰
      </button>

      <div style={{ fontWeight: 600 }}>{user?.username || '...'}</div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
        {navLink('/search', 'Search')}
        {navLink('/cv', 'CV')}
      </div>

      <div>
        <div style={{ fontSize: '0.8rem', opacity: 0.6, marginBottom: '0.4rem' }}>Search mode</div>
        <div style={{ display: 'flex', gap: '0.4rem' }}>
          {(['basic', 'advanced'] as const).map(m => (
            <button
              key={m}
              onClick={() => setMode(m)}
              style={{
                flex: 1, padding: '0.35rem', borderRadius: 6,
                background: mode === m ? '#3b82f6' : '#1f1f1f', color: '#fff', border: 'none',
                cursor: 'pointer', textTransform: 'capitalize',
              }}
            >
              {m}
            </button>
          ))}
        </div>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.3rem' }}>
        <div style={{ fontSize: '0.8rem', opacity: 0.6 }}>Preferences (learned)</div>
        <textarea
          value={memory}
          onChange={e => setMemory(e.target.value)}
          rows={6}
          style={{ resize: 'vertical', fontSize: '0.8rem', background: '#141414', color: '#eee', border: '1px solid #2a2a2a', borderRadius: 6, padding: '0.4rem' }}
        />
        <button onClick={saveMemory} style={{ padding: '0.35rem', borderRadius: 6, cursor: 'pointer' }}>
          Save preferences
        </button>
        {savedNote && <span style={{ fontSize: '0.75rem', opacity: 0.6 }}>{savedNote}</span>}
      </div>

      <div style={{ marginTop: 'auto', fontSize: '0.85rem', opacity: 0.6, cursor: 'pointer' }} onClick={logout}>
        Logout
      </div>
    </aside>
  )
}
