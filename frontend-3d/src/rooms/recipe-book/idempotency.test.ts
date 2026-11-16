import { afterEach, describe, expect, it, vi } from 'vitest'
import { newKey } from './idempotency'

afterEach(() => { vi.unstubAllGlobals() })

describe('newKey', () => {
  it('安全來源：用 crypto.randomUUID', () => {
    vi.stubGlobal('crypto', { randomUUID: () => 'uuid-from-browser', getRandomValues: () => { throw new Error('不該用到') } })
    expect(newKey()).toBe('uuid-from-browser')
  })

  it('非安全來源（區網 IP 測手機）沒有 randomUUID：不丟例外，自己組一把 32 位十六進位', () => {
    let seed = 0
    vi.stubGlobal('crypto', {
      getRandomValues: (bytes: Uint8Array) => {
        for (let i = 0; i < bytes.length; i += 1) bytes[i] = (seed += 37) % 256
        return bytes
      },
    })
    const first = newKey()
    const second = newKey()
    expect(first).toMatch(/^[0-9a-f]{32}$/)
    expect(second).toMatch(/^[0-9a-f]{32}$/)
    expect(second).not.toBe(first)
  })
})
