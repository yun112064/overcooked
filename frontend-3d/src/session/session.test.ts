import { AxiosError, AxiosHeaders } from 'axios'
import { describe, expect, it } from 'vitest'
import { shouldRetry } from './session'

function httpError(status: number): AxiosError {
  return new AxiosError(String(status), 'ERR_BAD_RESPONSE', undefined, undefined, {
    status, statusText: '', headers: {}, config: { headers: new AxiosHeaders() }, data: {},
  })
}

describe('shouldRetry', () => {
  it('4xx 是答案不是故障：不重試，畫面馬上能處理（找不到、還沒選家庭、算不出來）', () => {
    for (const status of [400, 401, 403, 404, 409, 422]) {
      expect(shouldRetry(0, httpError(status))).toBe(false)
    }
  })

  it('連不上、伺服器錯誤、逾時、太頻繁：最多再試兩次', () => {
    const offline = new AxiosError('Network Error', 'ERR_NETWORK')
    for (const error of [offline, httpError(500), httpError(503), httpError(408), httpError(429), new Error('其他例外')]) {
      expect(shouldRetry(0, error)).toBe(true)
      expect(shouldRetry(1, error)).toBe(true)
      expect(shouldRetry(2, error)).toBe(false)
    }
  })
})
