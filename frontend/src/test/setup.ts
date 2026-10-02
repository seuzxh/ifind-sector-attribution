import { enableAutoUnmount } from '@vue/test-utils'
import { afterEach, vi } from 'vitest'

enableAutoUnmount(afterEach)
afterEach(() => {
  vi.restoreAllMocks()
})
