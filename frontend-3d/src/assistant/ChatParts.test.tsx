import type { ReactElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { AssistantReference, RecipeSuggestion } from '../api/assistant'
import { Bubble, ChatLog, Composer, EngineSwitch } from './ChatParts'
import { AssistantChatContext, type AssistantChat, type Message } from './chatStore'

const idle: AssistantChat = {
  messages: [], busy: false, offlineText: null, engine: 'local', openaiAvailable: false,
  send: async () => {}, retry: () => {}, stop: () => {}, reset: () => {}, setEngine: () => {},
  launcher: { view: 'closed', seenId: 0, greeted: false, phase: null }, setLauncher: () => {},
}

const html = (node: ReactElement, chat: Partial<AssistantChat> = {}) =>
  renderToStaticMarkup(<AssistantChatContext.Provider value={{ ...idle, ...chat }}>{node}</AssistantChatContext.Provider>)
/** 只看畫出來的文字（去掉標籤），確認使用者讀到什麼。 */
const text = (node: ReactElement, chat: Partial<AssistantChat> = {}) => html(node, chat).replace(/<[^>]+>/g, '')

const references: AssistantReference[] = [
  { n: 1, title: '地瓜的營養', url: 'https://example.com/a', source_name: '健康醫療網', author: '林營養師' },
  { n: 2, title: '沒被引用的文章', url: 'https://example.com/b', source_name: 'Hello醫師' },
]
const answer: Message = {
  id: 2, role: 'assistant', text: '地瓜有膳食纖維[1]。', status: 'done', type: 'answer', references, citations: [1], engine: 'local',
}
const recipe: RecipeSuggestion = {
  dish_name: '地瓜飯', recipe_id: 'r1', category: '主食', cuisine: '台式', source_name: '示範食譜站',
  source_url: 'https://example.com/recipe', foods: ['地瓜'], reason: '地瓜有膳食纖維', cover_url: null,
}

describe('Bubble', () => {
  it('回答完成後，出處緊跟在回答下面，只列有引用的那幾筆', () => {
    const out = html(<Bubble message={answer} scope="float" />)
    expect(out).toContain('參考資料')
    expect(out).toContain('地瓜的營養')
    expect(out).toContain('健康醫療網　作者：林營養師')
    expect(out).toContain('href="https://example.com/a"')
    expect(out).toContain('rel="noopener noreferrer"')
    expect(out).not.toContain('沒被引用的文章')
    // 回答裡的 [1] 連到同一則訊息、同一個視窗裡的那筆出處
    expect(out).toContain('href="#ref-float-2-1"')
    expect(out).toContain('id="ref-float-2-1"')
  })

  it('回答裡的引用是有名稱的連結，連續的顯示成「1,2」', () => {
    const out = html(<Bubble message={{ ...answer, text: '地瓜[1][2]。', citations: [1, 2] }} scope="page" />)
    expect(out).toMatch(/<a [^>]*href="#ref-page-2-1"[^>]*aria-label="參考資料 1"[^>]*>1<\/a>/)
    expect(out).toMatch(/<a [^>]*href="#ref-page-2-2"[^>]*aria-label="參考資料 2"[^>]*>,2<\/a>/)
    // 點了會把焦點移到那筆出處，所以出處要能接焦點
    expect(out).toMatch(/<li id="ref-page-2-2" tabindex="-1">/)
  })

  it('還在生成時不列出處，沒有內容時寫「查資料中…」', () => {
    expect(text(<Bubble message={{ ...answer, status: 'streaming' }} scope="page" />)).not.toContain('參考資料')
    expect(text(<Bubble message={{ ...answer, status: 'streaming', text: '' }} scope="page" />)).toContain('查資料中…')
  })

  it('失敗時用文字說明，並可以再問一次', () => {
    const failed: Message = { id: 2, role: 'assistant', text: '', status: 'error', error: '連線中斷，回答沒有完成，請再問一次。' }
    const out = html(<Bubble message={failed} scope="page" />)
    expect(out).toContain('role="alert"')
    expect(out).toContain('連線中斷，回答沒有完成，請再問一次。')
    expect(out).toContain('再問一次')
    expect(out).not.toContain('參考資料')
  })

  it('被停止的回答標「（已停止）」', () => {
    expect(text(<Bubble message={{ ...answer, status: 'stopped' }} scope="page" />)).toContain('（已停止）')
  })

  it('醫療婉拒、緊急等類型有標籤', () => {
    expect(text(<Bubble message={{ ...answer, type: 'declined_medical', citations: [] }} scope="page" />)).toContain('請諮詢醫師')
    expect(text(<Bubble message={{ ...answer, type: 'emergency', citations: [] }} scope="page" />)).toContain('緊急情況')
  })

  it('食譜建議：載入中、有菜、沒有菜', () => {
    expect(text(<Bubble message={{ ...answer, recipes: 'loading' }} scope="page" />)).toContain('從食譜庫找用到這些食材的料理…')
    const shown = text(<Bubble message={{ ...answer, recipes: { recipes: [recipe], household_filtered: true } }} scope="page" />)
    expect(shown).toContain('可以試試這些料理')
    expect(shown).toContain('地瓜飯')
    expect(shown).toContain('含地瓜')
    expect(shown).toContain('回答提到：地瓜有膳食纖維')
    expect(shown).toContain('看做法（示範食譜站）')
    expect(shown).toContain('依回答提到的食材，從我們的食譜庫挑選，僅供參考，不代表有療效。已排除家人過敏與禁吃的食材。')
    const unfiltered = text(<Bubble message={{ ...answer, recipes: { recipes: [recipe], household_filtered: false } }} scope="page" />)
    expect(unfiltered).toContain('選擇家庭後會排除家人過敏與禁吃的食材。')
    const none = text(<Bubble message={{ ...answer, recipes: { recipes: [], household_filtered: true } }} scope="page" />)
    expect(none).not.toContain('可以試試這些料理')
  })

  it('兩種模型都能選時才標出是哪個生成的', () => {
    expect(text(<Bubble message={answer} scope="page" />)).not.toContain('生成')
    expect(text(<Bubble message={answer} scope="page" />, { openaiAvailable: true })).toContain('由 地端 qwen 生成')
  })

  it('使用者的問題照原文顯示', () => {
    expect(text(<Bubble message={{ id: 1, role: 'user', text: '吃地瓜有什麼好處', status: 'done' }} scope="page" />))
      .toBe('你問：吃地瓜有什麼好處')
  })
})

describe('ChatLog', () => {
  it('還沒開始時打招呼並列出範例問題', () => {
    const out = text(<ChatLog scope="float" compact />)
    expect(out).toContain('嗨！我是飲食小幫手')
    expect(out).toContain('想知道哪種食物有什麼營養，都可以問我。可以這樣問：')
    expect(out).toContain('吃隔夜菜會致癌嗎')
  })

  it('離線時說明原因，範例問題不能按', () => {
    const out = html(<ChatLog scope="page" />, { offlineText: '飲食小幫手目前未啟用。' })
    expect(out).toContain('飲食小幫手目前未啟用。')
    expect(out.match(/<button[^>]*disabled/g)).toHaveLength(4)
  })

  it('有對話後不再打招呼', () => {
    const out = text(<ChatLog scope="page" />, { messages: [{ id: 1, role: 'user', text: '豆腐', status: 'done' }, answer] })
    expect(out).not.toContain('嗨！我是飲食小幫手')
    expect(out).toContain('地瓜有膳食纖維')
  })
})

describe('Composer', () => {
  it('輸入框有名稱、字數上限，空白時不能送出', () => {
    const out = html(<Composer>免責說明</Composer>)
    expect(out).toContain('aria-label="飲食問題"')
    expect(out).toContain('maxLength="500"')
    expect(out).toMatch(/<button[^>]*aria-label="送出問題"[^>]*disabled/)
    expect(out).toContain('免責說明')
  })

  it('回答中把送出換成停止', () => {
    const out = html(<Composer />, { busy: true })
    expect(out).toContain('aria-label="停止回答"')
    expect(out).not.toContain('送出問題')
  })

  it('離線時輸入框停用並說明', () => {
    const out = html(<Composer compact />, { offlineText: '離線' })
    expect(out).toContain('placeholder="飲食小幫手目前離線"')
    expect(out).toMatch(/<textarea[^>]*disabled/)
  })
})

describe('EngineSwitch', () => {
  it('只有 OpenAI 版在線時才出現；回答中不能切換', () => {
    expect(html(<EngineSwitch />)).toBe('')
    const out = html(<EngineSwitch />, { openaiAvailable: true, engine: 'openai', busy: true })
    expect(out).toContain('role="radiogroup"')
    expect(out).toContain('aria-label="回答模型"')
    expect(out).toContain('地端 qwen')
    // 真的 radio（鍵盤操作交給瀏覽器），選中的是目前的模型，回答中全部停用
    const radios = out.match(/<input[^>]*type="radio"[^>]*>/g) ?? []
    expect(radios).toHaveLength(2)
    expect(radios.every((radio) => radio.includes('disabled'))).toBe(true)
    const checked = radios.filter((radio) => radio.includes('checked'))
    expect(checked).toHaveLength(1)
    expect(checked[0]).toContain('value="openai"')
  })

  it('可以切換時選項沒有停用，同一組用同一個 name', () => {
    const out = html(<EngineSwitch />, { openaiAvailable: true })
    const radios = out.match(/<input[^>]*type="radio"[^>]*>/g) ?? []
    expect(radios).toHaveLength(2)
    expect(radios.some((radio) => radio.includes('disabled'))).toBe(false)
    const names = radios.map((radio) => /name="([^"]+)"/.exec(radio)?.[1])
    expect(names[0]).toBeTruthy()
    expect(names[0]).toBe(names[1])
    const checked = radios.filter((radio) => radio.includes('checked'))
    expect(checked).toHaveLength(1)
    expect(checked[0]).toContain('value="local"')
  })
})
