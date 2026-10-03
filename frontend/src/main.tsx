import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router'
import App from './App'
import { AuthGate } from './components/AuthGate'
import { ThemeProvider } from './lib/theme'
import '@fontsource/inter/400.css'
import '@fontsource/inter/500.css'
import '@fontsource/inter/600.css'
import '@fontsource/inter/700.css'
import './index.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ThemeProvider>
      <AuthGate>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </AuthGate>
    </ThemeProvider>
  </StrictMode>,
)
