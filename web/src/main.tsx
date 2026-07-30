import { ClerkProvider } from '@clerk/react'
import { shadcn } from '@clerk/ui/themes'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import './index.css'
import App from './App.tsx'

const CLERK_PUBLISHABLE_KEY = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY

// Checked rather than asserted. `#root` is in index.html and its absence
// would mean the served HTML is not this app's, which is worth failing on
// loudly and immediately — `createRoot(null)` throws too, but from inside
// React with a message that says nothing about the missing element.
const rootElement = document.getElementById('root')
if (!rootElement) {
  throw new Error('index.html is missing its #root element — the app has nothing to mount into.')
}

createRoot(rootElement).render(
  <StrictMode>
    <ClerkProvider publishableKey={CLERK_PUBLISHABLE_KEY} afterSignOutUrl="/" appearance={{ theme: shadcn }}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </ClerkProvider>
  </StrictMode>,
)
