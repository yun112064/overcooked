// 飲食小幫手的對話狀態：完整頁面與每頁浮動的吉祥物共用同一段對話。
import { createContext, useContext } from 'react'
import type { AssistantEngine, AssistantReference, AssistantType, RecipeSuggestions } from '../api/assistant'

export type Message = {
  id: number
  role: 'user' | 'assistant'
  text: string
  type?: AssistantType
  references?: AssistantReference[]
  citations?: number[]
  /** 回答完成後另外查的食譜建議；loading 時顯示載入中，查不到或失敗就不顯示 */
  recipes?: RecipeSuggestions | 'loading'
  status: 'streaming' | 'done' | 'error' | 'stopped'
  error?: string
  /** 這則回答用哪個模型生成（有 OpenAI 版時顯示，方便比較） */
  engine?: AssistantEngine
}

export type AssistantChat = {
  messages: Message[]
  busy: boolean
  /** 服務離線時的說明；在線或狀態未知時為 null */
  offlineText: string | null
  send: (text: string) => Promise<void>
  stop: () => void
  reset: () => void
  /** 目前選用的回答模型；openaiAvailable 為 false 時一律用地端 */
  engine: AssistantEngine
  setEngine: (engine: AssistantEngine) => void
  openaiAvailable: boolean
}

export const ENGINE_LABEL: Record<AssistantEngine, string> = { local: '地端 qwen', openai: 'OpenAI' }

export const EXAMPLES = ['吃地瓜有什麼好處', '我想增加蛋白質可以吃什麼', '豆腐有什麼營養價值', '吃隔夜菜會致癌嗎']
export const MAX_LENGTH = 500

export const AssistantChatContext = createContext<AssistantChat | null>(null)

export function useAssistantChat(): AssistantChat {
  const chat = useContext(AssistantChatContext)
  if (!chat) throw new Error('useAssistantChat 需要放在 AssistantChatProvider 裡')
  return chat
}
