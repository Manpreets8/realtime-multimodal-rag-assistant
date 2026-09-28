import '@testing-library/jest-dom/vitest'
import { cleanup, configure } from '@testing-library/react'
import { afterEach } from 'vitest'

import { closeChatSocket } from '../services/chatSocket'

// findBy*/waitFor default to 1 s, which is too short when many test files run in parallel on a
// slower machine or CI runner. Assertions are unchanged; the element must still appear.
configure({ asyncUtilTimeout: 5000 })

// jsdom has no object URLs; image previews and downloads only need stable strings in tests.
if (!URL.createObjectURL) {
  let counter = 0
  URL.createObjectURL = () => `blob:test/${counter++}`
  URL.revokeObjectURL = () => undefined
}

// jsdom's WebSocket would try a real network connection. Without it, chat uses the REST
// fallback, which the fetch mocks cover; streaming tests install a fake WebSocket.
Reflect.deleteProperty(globalThis, 'WebSocket')

afterEach(() => {
  cleanup()
  closeChatSocket()
  localStorage.clear()
})
