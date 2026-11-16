import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Room, type HotspotItem } from './Room'
import type { Stop } from './framing'

// 景深設定讀 localStorage；這裡沒有瀏覽器，換成固定值
vi.mock('../depth/DepthProvider', () => ({ useDepth: () => ({ mode: 'static', setMode: () => {} }) }))

const STOPS: Stop[] = [
  { id: 'left', label: '左邊', fx: 0.2 },
  { id: 'right', label: '右邊', fx: 0.8 },
]
const HOTSPOTS: HotspotItem[] = [
  { id: 'a', label: '門牌', status: '上次進這家', stop: 'left' },
  { id: 'b', label: '新門牌', stop: 'left', disabled: true },
  { id: 'c', label: '內門', stop: 'right' },
]

/** 沒有瀏覽器：只給 Room 需要的畫面大小，看第一次畫出來的 HTML。 */
function render(width: number, height: number): string {
  vi.stubGlobal('window', { innerWidth: width, innerHeight: height })
  return renderToStaticMarkup(<Room name="玄關" layers={[]} stops={STOPS} hotspots={HOTSPOTS} />)
}

/** 物件列裡每顆按鈕的開頭標籤，照順序。 */
function chips(markup: string): string[] {
  const nav = /<nav[^>]*data-dock[^>]*>(.*?)<\/nav>/.exec(markup)?.[1] ?? ''
  return [...nav.matchAll(/<button[^>]*>/g)].map((m) => m[0])
}

afterEach(() => vi.unstubAllGlobals())

describe('Room 的物件列', () => {
  it('手機：目前這一段的物件標 aria-current，別段的不標', () => {
    const [a, b, c] = chips(render(390, 844))
    expect(a).toContain('aria-current="location"')
    expect(b).toContain('aria-current="location"')
    expect(c).not.toContain('aria-current')
  })

  it('不能按的物件標 aria-disabled（不用 disabled，焦點才不會掉）', () => {
    const [a, b] = chips(render(390, 844))
    expect(a).not.toContain('aria-disabled')
    expect(b).toContain('aria-disabled="true"')
    expect(b).not.toMatch(/\sdisabled(=|\s|>)/)
  })

  it('桌機：整個世界都看得到，清單視覺隱藏、沒有「目前這一段」可言', () => {
    const markup = render(1440, 900)
    expect(markup).toMatch(/<nav class="visually-hidden"[^>]*data-dock/)
    expect(chips(markup)).toHaveLength(3)
    expect(chips(markup).join('')).not.toContain('aria-current')
  })

  it('手機：清單是看得到的物件列', () => {
    const markup = render(390, 844)
    expect(markup).toContain('data-layout="phone"')
    expect(markup).not.toMatch(/<nav class="visually-hidden"/)
  })
})
