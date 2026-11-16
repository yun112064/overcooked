import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import { CrashNotice } from './RouteError'
import { isChunkLoadError, legacyRecordsPath, retryOnce } from './routeHelpers'

describe('legacyRecordsPath', () => {
  it('舊網址帶的日期一起帶到冰箱門', () => {
    expect(legacyRecordsPath('adopted', '2026-11-03')).toBe('/records?tab=adopted&date=2026-11-03')
    expect(legacyRecordsPath('nutrition', '2026-10-29')).toBe('/records?tab=nutrition&date=2026-10-29')
  })

  it('沒有日期或格式不對就不帶（冰箱門會用今天）', () => {
    expect(legacyRecordsPath('adopted', null)).toBe('/records?tab=adopted')
    expect(legacyRecordsPath('adopted', '')).toBe('/records?tab=adopted')
    expect(legacyRecordsPath('nutrition', '昨天')).toBe('/records?tab=nutrition')
    expect(legacyRecordsPath('nutrition', '2026-11-03&tab=x')).toBe('/records?tab=nutrition')
  })
})

describe('isChunkLoadError', () => {
  it('認得各家瀏覽器「程式檔沒抓到」的訊息', () => {
    expect(isChunkLoadError(new TypeError('Failed to fetch dynamically imported module: https://x/assets/Dining-abc.js'))).toBe(true)
    expect(isChunkLoadError(new TypeError('error loading dynamically imported module'))).toBe(true)
    expect(isChunkLoadError(new TypeError('Importing a module script failed.'))).toBe(true)
    expect(isChunkLoadError(new Error('Unable to preload CSS for /assets/Dining-abc.css'))).toBe(true)
  })

  it('其他例外不算', () => {
    expect(isChunkLoadError(new Error('useRoom 必須在 <Room> 裡使用'))).toBe(false)
    expect(isChunkLoadError('字串')).toBe(false)
    expect(isChunkLoadError(null)).toBe(false)
  })
})

describe('retryOnce', () => {
  it('第一次就成功：只載一次', async () => {
    const load = vi.fn().mockResolvedValue('room')
    await expect(retryOnce(load, 0)).resolves.toBe('room')
    expect(load).toHaveBeenCalledTimes(1)
  })

  it('第一次失敗、第二次成功：使用者不會看到錯誤', async () => {
    const load = vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValue('room')
    await expect(retryOnce(load, 0)).resolves.toBe('room')
    expect(load).toHaveBeenCalledTimes(2)
  })

  it('兩次都失敗才往外丟，而且不會一直試下去', async () => {
    const load = vi.fn().mockRejectedValue(new Error('offline'))
    await expect(retryOnce(load, 0)).rejects.toThrow('offline')
    expect(load).toHaveBeenCalledTimes(2)
  })
})

describe('CrashNotice', () => {
  const text = (markup: string) => markup.replace(/<[^>]+>/g, '')

  it('程式檔沒載到：說明是載入問題，給重新整理', () => {
    const markup = renderToStaticMarkup(
      <CrashNotice error={new TypeError('Failed to fetch dynamically imported module: /assets/a.js')} home={<a href="/today">回客廳</a>} />,
    )
    expect(markup).toContain('role="alert"')
    expect(text(markup)).toContain('這個房間沒有載入成功')
    expect(markup).toMatch(/<button type="button"[^>]*>重新整理<\/button>/)
    expect(markup).toContain('<a href="/today">回客廳</a>')
  })

  it('其他例外：中文說明，不把程式的錯誤訊息丟給使用者', () => {
    const markup = renderToStaticMarkup(<CrashNotice error={new Error('Cannot read properties of undefined')} />)
    expect(text(markup)).toContain('這個畫面出了點問題')
    expect(text(markup)).not.toContain('Cannot read')
    // 沒有「回客廳」的連結（人已經在客廳）時，說明裡也不提它
    expect(text(markup)).not.toContain('回客廳')
  })
})
