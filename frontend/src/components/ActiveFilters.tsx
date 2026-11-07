import type { ActiveFilters as Filters } from '../api/search'

function Row({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null
  return (
    <div style={{ marginBottom: '0.5rem' }}>
      <div style={{ fontSize: '0.8rem', opacity: 0.6, marginBottom: '0.25rem' }}>{title}</div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.3rem' }}>
        {items.map(it => (
          <span key={it} style={{ fontSize: '0.75rem', background: '#2a2a2a', padding: '0.15rem 0.5rem', borderRadius: 4 }}>
            {it}
          </span>
        ))}
      </div>
    </div>
  )
}

// The filters a search applied, the same for basic and agent mode (the API's `filters`).
export default function ActiveFilters({ filters }: { filters: Filters }) {
  if (filters.required.length === 0 && filters.preferred.length === 0) return null
  return (
    <div style={{ margin: '1rem 0', padding: '0.75rem 1rem', border: '1px solid #2a2a2a', borderRadius: 8 }}>
      <div style={{ fontSize: '0.85rem', opacity: 0.7, marginBottom: '0.4rem' }}>Active filters</div>
      <Row title="Required" items={filters.required} />
      <Row title="Preferred" items={filters.preferred} />
    </div>
  )
}
