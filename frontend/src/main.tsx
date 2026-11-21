import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { createBrowserRouter, Navigate, RouterProvider } from 'react-router'
import './styles.css'
import './styles-tech.css'
import App from './App'
import { isMock } from './api'
import ProjectsPage from './pages/ProjectsPage'
import ProjectPage from './pages/ProjectPage'
import AssetPage from './pages/AssetPage'
import GalleryPage from './pages/GalleryPage'
import GlossaryPage from './pages/GlossaryPage'
import CreditsPage from './pages/CreditsPage'
import NotFoundPage from './pages/NotFoundPage'
import { HealthPage } from './HealthPanel'

async function boot() {
  if (isMock()) {
    const { installMock } = await import('./mock')
    installMock()
  }
  const router = createBrowserRouter([
    {
      path: '/',
      Component: App,
      children: [
        {
          // A page that throws, or an address nothing matches, shows the plain page inside the app, never the router's developer page.
          ErrorBoundary: NotFoundPage,
          children: [
            { index: true, Component: ProjectsPage },
            { path: 'projects/:pid', Component: ProjectPage },
            { path: 'assets/:aid/:stage?', Component: AssetPage },
            { path: 'gallery', Component: GalleryPage },
            { path: 'glossary', Component: GlossaryPage },
            { path: 'credits', Component: CreditsPage },
            { path: 'health', Component: HealthPage },
            // The words the nav uses ("Finished", "System") work as addresses too.
            { path: 'finished', element: <Navigate to="/gallery" replace /> },
            { path: 'system', element: <Navigate to="/health" replace /> },
            { path: '*', Component: NotFoundPage },
          ],
        },
      ],
    },
  ])
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <RouterProvider router={router} />
    </StrictMode>,
  )
}

void boot()
