import { useSyncExternalStore } from 'react'

export type SearchMode = 'basic' | 'agent'

const KEY = 'search_mode'
const listeners = new Set<() => void>()

function getSnapshot(): SearchMode {
  const stored = localStorage.getItem(KEY)
  return stored === 'agent' || stored === 'advanced' ? 'agent' : 'basic'  // 'advanced' was the old name
}

function subscribe(cb: () => void) {
  listeners.add(cb)
  window.addEventListener('storage', cb)
  return () => {
    listeners.delete(cb)
    window.removeEventListener('storage', cb)
  }
}

function setSearchMode(m: SearchMode) {
  localStorage.setItem(KEY, m)
  listeners.forEach((l) => l())
}

export function useSearchMode() {
  const mode = useSyncExternalStore(subscribe, getSnapshot)
  return { mode, setMode: setSearchMode }
}
