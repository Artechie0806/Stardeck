import { lazy, StrictMode, Suspense } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Route, Routes } from 'react-router'
import App from './App'
import { AuthGate } from './components/AuthGate'
import { ErrorBoundary } from './components/ErrorBoundary'
import { LogoMark } from './components/Logo'
import { ThemeProvider } from './lib/theme'
import '@fontsource/inter/400.css'
import '@fontsource/inter/500.css'
import '@fontsource/inter/600.css'
import '@fontsource/inter/700.css'
import './index.css'

// Shared links and the demo open without signing in, outside the app (and its sign-in screen).
const PublicViewPage = lazy(() => import('./pages/PublicViewPage').then((m) => ({ default: m.PublicViewPage })))
const DemoRedirect = lazy(() => import('./pages/PublicViewPage').then((m) => ({ default: m.DemoRedirect })))
const loading = <div className="app-loading"><LogoMark size={56} animate label="Loading Stardeck" /></div>

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ThemeProvider>
      <BrowserRouter>
        <Routes>
          <Route path="s/:token" element={<ErrorBoundary><Suspense fallback={loading}><PublicViewPage /></Suspense></ErrorBoundary>} />
          <Route path="demo" element={<Suspense fallback={loading}><DemoRedirect /></Suspense>} />
          <Route path="*" element={<AuthGate><App /></AuthGate>} />
        </Routes>
      </BrowserRouter>
    </ThemeProvider>
  </StrictMode>,
)
