import { Job } from '../api/search'

export default function JobCard({ job }: { job: Job }) {
  const tags = [
    job.employment_type,
    job.location_type,
    job.seniority,
    ...job.languages_required,
  ].filter(Boolean) as string[]

  const scrapedAt = new Date(job.created_at)
  const scrapedLabel = Number.isNaN(scrapedAt.getTime())
    ? null
    : new Intl.DateTimeFormat(undefined, {
        dateStyle: 'medium',
      }).format(scrapedAt)
  const locationLabel = [job.location, job.company.country].filter(Boolean).join(' · ')
  const displaySource = job.source?.split(':').pop() || null
  const sourceAndDate = [displaySource, scrapedLabel].filter(Boolean).join(' – ')

  const handleClick = (_e: React.MouseEvent<HTMLAnchorElement>) => {
    // Hand the selected job to the Tailorer extension. The content script
    // (content/frontend_bridge.js) is the only listener; it reads the auth token
    // and stores {token, activeJob} as the single source of truth for the panel.
    window.postMessage(
      { type: 'tailorer_link', job_id: job.id, job_title: `${job.title} — ${job.company.name}` },
      '*',
    )
  }

  return (
    <a
      href={job.url}
      target="_blank"
      rel="noopener noreferrer"
      onClick={handleClick}
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
      <div>
        <div>
          <div style={{ fontWeight: 600 }}>{job.title}</div>
          <div style={{ fontSize: '0.875rem', opacity: 0.7, marginTop: '0.15rem' }}>
            {job.company.name}
          </div>
        </div>
      </div>
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
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: '1rem', fontSize: '0.875rem', opacity: 0.5, marginTop: '0.2rem' }}>
        <span>{locationLabel}</span>
        {sourceAndDate && <span style={{ fontSize: '0.75rem', whiteSpace: 'nowrap' }}>{sourceAndDate}</span>}
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
