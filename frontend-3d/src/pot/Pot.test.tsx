import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { Pot } from './Pot'
import { potFromSteps, type PotState } from './potState'

const html = (view: 'side' | 'top', state: PotState, label?: string) =>
  renderToStaticMarkup(<Pot view={view} state={state} label={label} />)
const src = (markup: string) => /<img[^>]*src="([^"]+)"/.exec(markup)?.[1]
const wisps = (markup: string) => (markup.match(/<i><\/i>/g) ?? []).length

describe('Pot', () => {
  it('俯視：每一步是不同的圖，0–5 步共六張', () => {
    const images = [0, 1, 2, 3, 4, 5].map((steps) => src(html('top', potFromSteps(steps))))
    expect(new Set(images).size).toBe(6)
    expect(images.every(Boolean)).toBe(true)
  })

  it('俯視：蓋鍋、完成、失敗各有自己的圖', () => {
    const images = (['cooking', 'ready', 'failed', 'empty', 'full'] as const).map((kind) => src(html('top', { kind })))
    expect(new Set(images).size).toBe(5)
  })

  it('側視：半滿不分步數都是同一張有料的圖，和空鍋不同', () => {
    const filled = [1, 2, 3, 4, 5].map((steps) => src(html('side', potFromSteps(steps))))
    expect(new Set(filled).size).toBe(1)
    expect(filled[0]).not.toBe(src(html('side', { kind: 'empty' })))
  })

  it('側視：開蓋和失敗的圖比較大，往上超出鍋身的框，鍋身不位移', () => {
    expect(html('side', { kind: 'empty' })).toContain('left:0%;top:0%;width:100%;height:100%')
    expect(html('side', { kind: 'ready' })).toContain('left:0%;top:-35.29%;width:125%;height:135.29%')
    expect(html('side', { kind: 'failed' })).toContain('left:0%;top:-11.76%;width:100%;height:111.76%')
  })

  it('只有生成中有蒸氣、失敗有黑煙', () => {
    for (const view of ['side', 'top'] as const) {
      expect(wisps(html(view, { kind: 'cooking' }))).toBe(3)
      expect(wisps(html(view, { kind: 'failed' }))).toBe(3)
      for (const kind of ['empty', 'full', 'ready'] as const) expect(wisps(html(view, { kind }))).toBe(0)
      expect(wisps(html(view, { kind: 'filling', steps: 2 }))).toBe(0)
    }
  })

  it('預設是裝飾；傳 label 才對讀屏說明', () => {
    expect(html('side', { kind: 'cooking' })).not.toContain('role="img"')
    expect(html('side', { kind: 'cooking' })).toContain('alt=""')
    expect(html('side', { kind: 'cooking' }, '生成中')).toContain('role="img" aria-label="生成中"')
  })
})
