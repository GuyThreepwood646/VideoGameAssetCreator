import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { createBrowserRouter, RouterProvider } from 'react-router'
import './styles.css'
import App from './App'
import { isMock } from './api'
import ProjectsPage from './pages/ProjectsPage'
import ProjectPage from './pages/ProjectPage'
import AssetPage from './pages/AssetPage'
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
        { index: true, Component: ProjectsPage },
        { path: 'projects/:pid', Component: ProjectPage },
        { path: 'assets/:aid/:stage?', Component: AssetPage },
        { path: 'health', Component: HealthPage },
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
