import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
// Self-hosted fonts (see the --font-* comment in index.css). Before index.css
// so its rules see the @font-face declarations.
import '@fontsource-variable/archivo/wdth.css'
import '@fontsource-variable/hanken-grotesk/wght.css'
import '@fontsource-variable/hanken-grotesk/wght-italic.css'
import '@fontsource/jetbrains-mono/400.css'
import '@fontsource/jetbrains-mono/500.css'
import './index.css'
import App from './App.tsx'
import { UnauthorizedError } from './api.ts'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 10_000,
      refetchOnWindowFocus: false,
      // Retrying a 401 just delays showing the login screen and hammers
      // the endpoint for no benefit — every other failure keeps the
      // library's default of up to 3 retries.
      retry: (failureCount, error) => !(error instanceof UnauthorizedError) && failureCount < 3,
    },
  },
})

// Noto Sans SC's stylesheets are ~400KB of @font-face rules (one per
// unicode-range slice, so only the glyphs a page uses are ever downloaded).
// Imported statically they'd sit in the render-blocking CSS. Loaded as their
// own chunk instead, after the app has started; CJK text renders in the
// system font until then.
void Promise.all([
  import('@fontsource/noto-sans-sc/400.css'),
  import('@fontsource/noto-sans-sc/500.css'),
  import('@fontsource/noto-sans-sc/600.css'),
  import('@fontsource/noto-sans-sc/700.css'),
])

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
)
