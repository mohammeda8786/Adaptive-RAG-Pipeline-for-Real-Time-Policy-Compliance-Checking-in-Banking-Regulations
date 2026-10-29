import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
import AuthPage from './AuthPage.jsx'

const path = window.location.pathname.replace(/\/+$/, '') || '/'
const authMode = path === '/login' ? 'login' : path === '/register' ? 'register' : null

createRoot(document.getElementById('root')).render(
  <StrictMode>
    {authMode ? <AuthPage mode={authMode} /> : <App />}
  </StrictMode>,
)
