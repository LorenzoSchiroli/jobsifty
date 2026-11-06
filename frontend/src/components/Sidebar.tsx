import { useEffect, useState } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import { me, UserResponse } from '../api/auth'

const icon = (d: string) => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
       strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d={d} />
  </svg>
)

const LINKS = [
  { to: '/search', label: 'Search', icon: icon('M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM21 21l-4.35-4.35') },
  { to: '/cv', label: 'CV', icon: icon('M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9zM14 3v6h6M8 13h8M8 17h5') },
  { to: '/preferences', label: 'Preferences', icon: icon('M4 6h16M4 12h16M4 18h16M9 4v4M15 10v4M7 16v4') },
]

export default function Sidebar({ open, onToggle }: { open: boolean; onToggle: () => void }) {
  const navigate = useNavigate()
  const [user, setUser] = useState<UserResponse | null>(null)

  useEffect(() => {
    me().then(setUser).catch(() => {})
  }, [])

  const logout = () => {
    localStorage.removeItem('access_token')
    navigate('/login')
  }

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
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ fontWeight: 600 }}>{user?.username || '...'}</div>
        <button onClick={onToggle} aria-label="Collapse sidebar"
                style={{ background: 'transparent', color: '#aaa', border: 'none', cursor: 'pointer', fontSize: '1.1rem', padding: '0.2rem 0.4rem' }}>
          ☰
        </button>
      </div>

      <nav style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
        {LINKS.map(l => (
          <NavLink key={l.to} to={l.to} className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}>
            {l.icon}
            {l.label}
          </NavLink>
        ))}
      </nav>

      <button onClick={logout}
              style={{ marginTop: 'auto', textAlign: 'left', background: 'transparent', border: 'none', color: '#a3a3a3', padding: '0.45rem 0.7rem', fontSize: '0.9rem' }}>
        Logout
      </button>
    </aside>
  )
}
