// Credits: who made what the app builds on, with licences (GET /api/credits). The same data writes CREDITS.md.
import { api, type CreditEntry } from '../api'
import { ErrorText, useAsync } from '../components/ui'

function Row({ e }: { e: CreditEntry }) {
  const unsure = e.confirmed === 'not confirmed'
  return (
    <tr>
      <td>{e.link ? <a href={e.link} target="_blank" rel="noreferrer">{e.name}</a> : e.name}</td>
      <td>{e.does}{e.note && <div className="muted small">{e.note}</div>}</td>
      <td>{e.author}</td>
      <td className={unsure ? 'credits-unsure' : undefined}>{e.licence}</td>
      <td>{e.attribution_required ? 'Yes' : 'No'}</td>
      <td>{e.in_your_assets ? 'Yes' : 'No'}</td>
    </tr>
  )
}

export default function CreditsPage() {
  const { data, error, loading } = useAsync(() => api.credits(), [])
  return (
    <div className="page-narrow credits">
      <h1 className="page-title">Credits</h1>
      <p className="muted">The projects this app is built on, and what you may do with what it makes. {data?.summary.not_legal_advice}</p>
      {loading && <div className="muted">Loading...</div>}
      {error != null && <ErrorText error={error} />}
      {data && (
        <>
          {[data.summary.passing_on, data.summary.before_sharing, data.summary.game_credit].map((s) => (
            <section key={s.title} className="card credits-summary">
              <h2 className="section-title">{s.title}</h2>
              <ul>{s.points.map((p) => <li key={p}>{p}</li>)}</ul>
            </section>
          ))}
          {data.groups.map((g) => (
            <section key={g.id} className="credits-group" id={g.id}>
              <h2 className="section-title">{g.title}</h2>
              <p className="muted small">{g.intro}</p>
              <table className="table credits-table">
                <thead>
                  <tr>
                    <th>Name</th><th>What it does here</th><th>Author</th><th>Licence (the terms)</th>
                    <th>Credit required</th><th>In your assets</th>
                  </tr>
                </thead>
                <tbody>{g.entries.map((e) => <Row key={e.name} e={e} />)}</tbody>
              </table>
            </section>
          ))}
        </>
      )}
    </div>
  )
}
