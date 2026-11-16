import { describe, expect, it } from 'vitest'
import { formatMoney } from './formatMoney'

describe('formatMoney', () => {
  it('取整到元並加千分位', () => {
    expect(formatMoney('486')).toBe('NT$486')
    expect(formatMoney('1840.00')).toBe('NT$1,840')
    expect(formatMoney('1234567.5')).toBe('NT$1,234,568')
    expect(formatMoney(99.4)).toBe('NT$99')
  })

  it('0 是已知的 0 元', () => {
    expect(formatMoney('0')).toBe('NT$0')
    expect(formatMoney('0.00')).toBe('NT$0')
    expect(formatMoney('-0.2')).toBe('NT$0')
  })

  it('後端的分數字串也能顯示', () => {
    expect(formatMoney('973/2')).toBe('NT$487')
  })

  it('未知不是 0：回傳 null 讓畫面顯示「未知」', () => {
    expect(formatMoney(null)).toBeNull()
    expect(formatMoney(undefined)).toBeNull()
    expect(formatMoney('')).toBeNull()
    expect(formatMoney('  ')).toBeNull()
    expect(formatMoney('abc')).toBeNull()
    expect(formatMoney(Number.NaN)).toBeNull()
  })

  it('負數把負號放在幣別前面', () => {
    expect(formatMoney('-120')).toBe('-NT$120')
  })
})
