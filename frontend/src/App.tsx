import { Route, Routes, useLocation } from 'react-router'

import { ErrorBoundary } from './components/ErrorBoundary'

import { AppShell } from './components/layout/AppShell'
import { RedirectIfAuthenticated, RequireAdmin, RequireAuth } from './components/routing/RouteGuards'
import { ToastProvider } from './components/ui/Toast'
import { AuthProvider } from './contexts/AuthProvider'
import AdminPage from './pages/AdminPage'
import ComparePage from './pages/ComparePage'
import ChatPage from './pages/ChatPage'
import DashboardPage from './pages/DashboardPage'
import DocumentsPage from './pages/DocumentsPage'
import KnowledgeBaseDetailPage from './pages/KnowledgeBaseDetailPage'
import KnowledgeBasesPage from './pages/KnowledgeBasesPage'
import LoginPage from './pages/LoginPage'
import NotFoundPage from './pages/NotFoundPage'
import RegisterPage from './pages/RegisterPage'
import SearchPage from './pages/SearchPage'
import SettingsPage from './pages/SettingsPage'

/** Route tree. Wrapped in a router by main.tsx (BrowserRouter) or tests (MemoryRouter). */
export default function App() {
  const { pathname } = useLocation()
  return (
    <AuthProvider>
      <ToastProvider>
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
                <Route path="documents" element={<DocumentsPage />} />
                <Route path="documents/compare" element={<ComparePage />} />
                <Route path="search" element={<SearchPage />} />
                <Route path="settings" element={<SettingsPage />} />
                <Route element={<RequireAdmin />}>
                  <Route path="admin" element={<AdminPage />} />
                </Route>
              </Route>
            </Route>
            <Route path="*" element={<NotFoundPage />} />
          </Routes>
        </ErrorBoundary>
      </ToastProvider>
    </AuthProvider>
  )
}
