// 飲食小幫手的對話狀態：展開頁（/assistant）與每個房間底部的吉祥物視窗共用同一段對話。
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

/**
 * 吉祥物視窗的狀態。放在對話狀態旁邊而不是視窗元件裡，
 * 因為每換一個房間視窗元件都會重新掛載，放在裡面的話視窗會自己關掉、未讀也會算錯。
 */
export type LauncherState = {
  /** closed：只有吉祥物在跑；open：視窗打開；mini：縮成一條標題列 */
  view: 'closed' | 'open' | 'mini'
  /** 使用者已經看過的最後一則訊息 id；比它新的已完成回答算未讀 */
  seenId: number
  /** 打過招呼了（「有飲食問題問我！」每次進屋只說一次） */
  greeted: boolean
  /** 吉祥物停下來時跑到一圈的哪裡（毫秒）；換房間後從同一個位置接著站，視窗才不會跳走 */
  phase: number | null
}

export type AssistantChat = {
  messages: Message[]
  busy: boolean
  /** 服務離線時的說明；在線或狀態未知時為 null */
  offlineText: string | null
  send: (text: string) => Promise<void>
  /** 把失敗的那一題再問一次（原本的問題與失敗的回答會換成新的一組） */
  retry: (id: number) => void
  stop: () => void
  reset: () => void
  /** 目前選用的回答模型；openaiAvailable 為 false 時一律用地端 */
  engine: AssistantEngine
  setEngine: (engine: AssistantEngine) => void
  openaiAvailable: boolean
  launcher: LauncherState
  setLauncher: (patch: Partial<LauncherState>) => void
}

export const ENGINE_LABEL: Record<AssistantEngine, string> = { local: '地端 qwen', openai: 'OpenAI' }

export const EXAMPLES = ['吃地瓜有什麼好處', '我想增加蛋白質可以吃什麼', '豆腐有什麼營養價值', '吃隔夜菜會致癌嗎']
export const MAX_LENGTH = 500

export const AssistantChatContext = createContext<AssistantChat | null>(null)

export function useAssistantChat(): AssistantChat {
  const chat = useContext(AssistantChatContext)
  if (!chat) throw new Error('useAssistantChat 需要放在 AssistantProvider 裡')
  return chat
}
