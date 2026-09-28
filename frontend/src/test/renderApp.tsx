import { render } from '@testing-library/react'
import { MemoryRouter } from 'react-router'

import App from '../App'
import { tokenStorage } from '../services/tokenStorage'

/** Render the full app at `path` as a signed-in user (mock `GET /auth/me` in the test). */
export function renderSignedIn(path: string) {
  tokenStorage.set('test-token')
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  )
}
