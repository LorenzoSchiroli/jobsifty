// What the search bar understands, shown before the first search. Every example
// below is a phrase backend/search/query_parsing.py turns into a filter.
const TIPS: [string, string[]][] = [
  ['Level', ['junior', 'mid-level', 'senior', 'lead']],
  ['Work mode', ['remote', 'hybrid', 'on-site']],
  ['Contract', ['full-time', 'part-time', 'internship', 'freelance']],
  ['Country', ['in Germany', 'in the Netherlands', 'in France']],
  ['Language', ['German-speaking', 'fluent in French']],
  ['Company', ['startup', 'no consulting']],
  ['Posted', ['today', 'last 24 hours', 'this week', 'last 2 weeks']],
]

const chip: React.CSSProperties = {
  display: 'inline-block',
  padding: '0.1rem 0.5rem',
  margin: '0 0.35rem 0.35rem 0',
  border: '1px solid #333',
  borderRadius: 999,
  fontSize: '0.8rem',
  background: '#1a1a1a',
}

export default function SearchHelp() {
  return (
    <div style={{ marginTop: '1.5rem', fontSize: '0.9rem' }}>
      <p style={{ margin: '0 0 0.75rem', opacity: 0.8 }}>
        Describe the job in plain words, then add any of these to narrow it down:
      </p>

      <div style={{ display: 'grid', gridTemplateColumns: 'max-content 1fr', columnGap: '1rem', alignItems: 'baseline' }}>
        {TIPS.map(([label, examples]) => (
          <div key={label} style={{ display: 'contents' }}>
            <span style={{ opacity: 0.5 }}>{label}</span>
            <div>{examples.map(e => <span key={e} style={chip}>{e}</span>)}</div>
          </div>
        ))}
      </div>

      <p style={{ margin: '0.75rem 0 0.25rem', opacity: 0.5 }}>For example</p>
      <code style={{ display: 'block', padding: '0.5rem 0.75rem', background: '#1a1a1a', borderRadius: 6 }}>
        senior remote machine learning engineer in Germany, this week
      </code>

      <ul style={{ margin: '0.75rem 0 0', paddingLeft: '1.1rem', opacity: 0.6 }}>
        <li>You see jobs posted in the last 3 days unless you say otherwise.</li>
        <li>Matching jobs come first, but close ones still show up. Add <b>strictly</b> to see only exact matches.</li>
      </ul>
    </div>
  )
}
