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

const REGIONS = new Intl.DisplayNames(['en'], { type: 'region' })

// A two-letter region code as its English name ("DE" → "Germany"); anything else as it is.
function countryName(value: string): string {
  const text = value.trim()
  if (!/^[A-Za-z]{2}$/.test(text)) return text
  try {
    return REGIONS.of(text.toUpperCase()) ?? text
  } catch {
    return text
  }
}

// The company's country, labelled so it does not read as a second job location; left out when
// unknown or the same as the job's country, the last part of its location ("Madrid, Spain", "Berlin, BE, DE").
function companyCountry(job: Job): string | null {
  if (!job.company.country?.trim()) return null
  const country = countryName(job.company.country)
  const jobCountry = job.location ? countryName(job.location.split(',').pop() ?? '') : ''
  return jobCountry.toLowerCase() === country.toLowerCase() ? null : `company in ${country}`
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
      {(typeof job.fit_score === 'number' || job.fit_rationale || job.fit_gaps || (job.trade_offs?.length ?? 0) > 0) && (
        <div style={{ marginTop: '0.4rem' }}>
          {typeof job.fit_score === 'number' && (
            <span style={{
              fontSize: '0.75rem', fontWeight: 700, padding: '0.15rem 0.5rem', borderRadius: 4,
              background: job.fit_score >= 70 ? '#16653440' : job.fit_score >= 40 ? '#78350f40' : '#7f1d1d40',
              color: job.fit_score >= 70 ? '#4ade80' : job.fit_score >= 40 ? '#fbbf24' : '#f87171',
            }}>
              Fit {job.fit_score}
            </span>
          )}
          {job.fit_rationale && (
            <div style={{ fontSize: '0.8rem', opacity: 0.75, marginTop: '0.3rem' }}>{job.fit_rationale}</div>
          )}
          {job.fit_gaps && (
            <div style={{ fontSize: '0.78rem', opacity: 0.55, marginTop: '0.2rem' }}>Gaps: {job.fit_gaps}</div>
          )}
          {job.trade_offs && job.trade_offs.length > 0 && (
            <div style={{ fontSize: '0.78rem', opacity: 0.55, marginTop: '0.2rem' }}>Trade-offs: {job.trade_offs.join('; ')}</div>
          )}
        </div>
      )}
      <div style={{ fontSize: '0.875rem', opacity: 0.5, marginTop: '0.2rem' }}>
        {[job.location, companyCountry(job), postedAgo(job.posted_at)].filter(Boolean).join(' · ')}
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
