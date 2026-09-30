import { Route, Routes, useLocation } from 'react-router'

import { ErrorBoundary } from './components/ErrorBoundary'

import { AppShell } from './components/layout/AppShell'
import { RedirectIfAuthenticated, RequireAdmin, RequireAuth } from './components/routing/RouteGuards'
import { AuthProvider } from './contexts/AuthProvider'
import AdminPage from './pages/AdminPage'
import ChatPage from './pages/ChatPage'
import DashboardPage from './pages/DashboardPage'
import KnowledgeBaseDetailPage from './pages/KnowledgeBaseDetailPage'
import KnowledgeBasesPage from './pages/KnowledgeBasesPage'
import LoginPage from './pages/LoginPage'
import NotFoundPage from './pages/NotFoundPage'
import RegisterPage from './pages/RegisterPage'

/** Route tree. Wrapped in a router by main.tsx (BrowserRouter) or tests (MemoryRouter). */
export default function App() {
  const { pathname } = useLocation()
  return (
    <AuthProvider>
      <ErrorBoundary resetKey={pathname}>
        <Routes>
          <Route element={<RedirectIfAuthenticated />}>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/register" element={<RegisterPage />} />
          </Route>
          <Route element={<RequireAuth />}>
            <Route element={<AppShell />}>
              <Route index element={<DashboardPage />} />
              <Route path="knowledge-bases" element={<KnowledgeBasesPage />} />
              <Route path="knowledge-bases/:kbId" element={<KnowledgeBaseDetailPage />} />
              <Route path="chat" element={<ChatPage />} />
              <Route path="chat/:conversationId" element={<ChatPage />} />
              <Route element={<RequireAdmin />}>
                <Route path="admin" element={<AdminPage />} />
              </Route>
            </Route>
          </Route>
          <Route path="*" element={<NotFoundPage />} />
        </Routes>
      </ErrorBoundary>
    </AuthProvider>
  )
}
