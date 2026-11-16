import { describe, expect, it } from 'vitest'
import { gate } from './gate'

const base = { loaded: false, failed: false, fetchedAfterMount: false, opened: false }

describe('gate（進單板時要畫什麼）', () => {
  it('還沒有資料：等', () => {
    expect(gate(base)).toBe('loading')
  })

  it('只有快取裡的舊資料：等這次重新讀到的，不拿舊版本開始編輯', () => {
    expect(gate({ ...base, loaded: true })).toBe('loading')
  })

  it('這次進來讀到了：打開問卷', () => {
    expect(gate({ ...base, loaded: true, fetchedAfterMount: true })).toBe('editor')
  })

  it('一開始就讀取失敗：錯誤畫面（沒有資料，或只有快取裡的舊資料都一樣）', () => {
    expect(gate({ ...base, failed: true, fetchedAfterMount: true })).toBe('error')
    expect(gate({ ...base, loaded: true, failed: true, fetchedAfterMount: true })).toBe('error')
  })

  it('問卷已經打開，背景重讀失敗：問卷留著，還沒儲存的回答不會不見', () => {
    expect(gate({ loaded: true, failed: true, fetchedAfterMount: true, opened: true })).toBe('editor')
  })

  it('問卷已經打開，背景正在重讀：問卷留著', () => {
    expect(gate({ loaded: true, failed: false, fetchedAfterMount: true, opened: true })).toBe('editor')
  })
})
