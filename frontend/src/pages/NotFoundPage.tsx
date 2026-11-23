// The page for an address the app does not have, and the error page of the routes: plain words and the two ways back.
import { isRouteErrorResponse, Link, useRouteError } from 'react-router'

export default function NotFoundPage() {
  const error = useRouteError()
  // No route error means the catch-all route matched: the address is unknown. A thrown error is a different thing.
  const missing = error === undefined || error === null || (isRouteErrorResponse(error) && error.status === 404)
  return (
    <div className="page not-found" data-testid="not-found">
      <h1>{missing ? 'That page does not exist' : 'Something went wrong on this page'}</h1>
      <p className="muted">{missing ? 'The address may be mistyped, or the asset or project may have been removed.' : 'Go back to your projects or the finished assets and try again.'}</p>
      <p>
        <Link className="btn primary" to="/">
          Projects
        </Link>{' '}
        <Link className="btn" to="/gallery">
          Finished
        </Link>
      </p>
    </div>
  )
}
