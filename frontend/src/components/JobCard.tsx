import { Job } from '../api/search'

const HOUR = 3_600_000
const DAY = 24 * HOUR

// "3 hours ago" under a day, "2 days ago" after. Some boards give only the day
// (stored as midnight UTC): those read "today" / "N days ago".
function postedAgo(postedAt: string | null): string | null {
  if (!postedAt) return null
  const posted = new Date(postedAt)
  if (Number.isNaN(posted.getTime())) return null
  const now = new Date()
  if (posted.getTime() % DAY === 0) {
    const today = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate())
    const days = Math.round((today - posted.getTime()) / DAY)
    return days <= 0 ? 'today' : `${days} day${days === 1 ? '' : 's'} ago`
  }
  const elapsed = Math.max(0, now.getTime() - posted.getTime())
  if (elapsed < HOUR) return 'under 1 hour ago'
  if (elapsed < DAY) {
    const hours = Math.floor(elapsed / HOUR)
    return `${hours} hour${hours === 1 ? '' : 's'} ago`
  }
  const days = Math.floor(elapsed / DAY)
  return `${days} day${days === 1 ? '' : 's'} ago`
}

export default function JobCard({ job }: { job: Job }) {
  const tags = [
    job.employment_type,
    job.location_type,
    job.seniority,
    ...job.languages_required,
  ].filter(Boolean) as string[]

  return (
    <a
      href={job.url}
      target="_blank"
      rel="noopener noreferrer"
      style={{
        display: 'block',
        padding: '1rem',
        border: '1px solid #2a2a2a',
        borderRadius: 8,
        textDecoration: 'none',
        color: 'inherit',
        background: '#141414',
      }}
    >
      <div style={{ fontWeight: 600 }}>{job.title} — {job.company.name}</div>
      {typeof job.fit_score === 'number' && (
        <div style={{ marginTop: '0.4rem' }}>
          <span style={{
            fontSize: '0.75rem', fontWeight: 700, padding: '0.15rem 0.5rem', borderRadius: 4,
            background: job.fit_score >= 70 ? '#16653440' : job.fit_score >= 40 ? '#78350f40' : '#7f1d1d40',
            color: job.fit_score >= 70 ? '#4ade80' : job.fit_score >= 40 ? '#fbbf24' : '#f87171',
          }}>
            Fit {job.fit_score}
          </span>
          {job.fit_rationale && (
            <div style={{ fontSize: '0.8rem', opacity: 0.75, marginTop: '0.3rem' }}>{job.fit_rationale}</div>
          )}
          {job.fit_gaps && (
            <div style={{ fontSize: '0.78rem', opacity: 0.55, marginTop: '0.2rem' }}>Gaps: {job.fit_gaps}</div>
          )}
        </div>
      )}
      <div style={{ fontSize: '0.875rem', opacity: 0.5, marginTop: '0.2rem' }}>
        {[job.location, job.company.country, postedAgo(job.posted_at)].filter(Boolean).join(' · ')}
      </div>
      {tags.length > 0 && (
        <div style={{ marginTop: '0.5rem', display: 'flex', flexWrap: 'wrap', gap: '0.3rem' }}>
          {tags.map(tag => (
            <span key={tag} style={{ fontSize: '0.75rem', background: '#2a2a2a', padding: '0.15rem 0.5rem', borderRadius: 4 }}>
              {tag}
            </span>
          ))}
        </div>
      )}
    </a>
  )
}
