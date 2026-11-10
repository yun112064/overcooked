import { useQuery } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { api } from '../api/client'
import {
  AssistantError, RECIPE_ANSWER_TYPES, askAssistant, type AssistantEngine, type AssistantTurn, type AssistantType,
  type RecipeSuggestions,
} from '../api/assistant'
import { AssistantChatContext, type Message } from './chatStore'

type TeamStatus = Record<string, { status: 'online' | 'offline' | 'not_configured'; reason: string | null }>

const HISTORY_TURNS = 6
const ENGINE_KEY = 'assistant-engine'

function savedEngine(): AssistantEngine {
  try {
    return localStorage.getItem(ENGINE_KEY) === 'openai' ? 'openai' : 'local'
  } catch {
    return 'local'
  }
}

const OFFLINE_TEXT: Record<string, string> = {
  NOT_CONFIGURED: '飲食小幫手目前未啟用。',
  SERVICE_NOT_READY: '飲食小幫手還沒準備好（模型或資料庫未啟動）。',
  AUTH_FAILED: '飲食小幫手驗證失敗，請通知管理者。',
}

export default function AssistantChatProvider({ children }: { children: ReactNode }) {
  const [messages, setMessages] = useState<Message[]>([])
  const [busy, setBusy] = useState(false)
  const abortRef = useRef<AbortController | null>(null)
  const nextId = useRef(1)
  const messagesRef = useRef(messages)
  useEffect(() => { messagesRef.current = messages }, [messages])

  const status = useQuery({
    queryKey: ['team-health'],
    queryFn: async () => (await api.get<TeamStatus>('/health/team')).data,
    refetchInterval: 30_000,
  })
  // OpenAI 版有設定且在線才能選；否則一律用地端
  const openaiAvailable = status.data?.assistant_openai?.status === 'online'
  const [chosen, setChosen] = useState<AssistantEngine>(savedEngine)
  const engine: AssistantEngine = openaiAvailable ? chosen : 'local'
  const setEngine = useCallback((next: AssistantEngine) => {
    setChosen(next)
    try { localStorage.setItem(ENGINE_KEY, next) } catch { /* 只是記住選擇，失敗不影響 */ }
  }, [])
  const assistant = engine === 'openai' ? status.data?.assistant_openai : status.data?.assistant
  const offlineText = assistant && assistant.status !== 'online'
    ? OFFLINE_TEXT[assistant.reason ?? ''] ?? '飲食小幫手目前離線（團隊電腦未開機或服務未啟動），請稍後再試。'
    : null

  useEffect(() => () => abortRef.current?.abort(), [])

  const update = (id: number, patch: (m: Message) => Partial<Message>) =>
    setMessages((list) => list.map((m) => (m.id === id ? { ...m, ...patch(m) } : m)))

  const { refetch } = status
  const send = useCallback(async (text: string) => {
    const question = text.trim()
    if (!question || abortRef.current) return
    const history: AssistantTurn[] = messagesRef.current
      .filter((m) => m.status === 'done' && m.text)
      .slice(-HISTORY_TURNS)
      .map((m) => ({ role: m.role, content: m.text.slice(0, 2000) }))
    const userId = nextId.current++
    const botId = nextId.current++
    setMessages((list) => [...list,
      { id: userId, role: 'user', text: question, status: 'done' },
      { id: botId, role: 'assistant', text: '', status: 'streaming', engine }])
    setBusy(true)
    const controller = new AbortController()
    abortRef.current = controller
    let answer = ''
    const result: { type: AssistantType | null } = { type: null }
    try {
      await askAssistant(question, history, {
        onMeta: (meta) => update(botId, () => ({ type: meta.type })),
        onReferences: (refs) => update(botId, () => ({ references: refs })),
        onDelta: (delta) => { answer += delta; update(botId, (m) => ({ text: m.text + delta })) },
        onDone: (done) => {
          result.type = done.type
          update(botId, () => ({ status: 'done', type: done.type, citations: done.citations }))
        },
      }, controller.signal, engine)
    } catch (err) {
      if ((err as Error).name === 'AbortError') {
        update(botId, () => ({ status: 'stopped' }))
      } else {
        const message = err instanceof AssistantError ? err.message : '飲食小幫手暫時無法回答，請稍後再試。'
        update(botId, () => ({ status: 'error', error: message }))
        if (err instanceof AssistantError && err.code === 'ASSISTANT_UNAVAILABLE') void refetch()
      }
    } finally {
      setBusy(false)
      abortRef.current = null
    }
    // 回答完成後，依回答提到的食材從食譜庫找菜；醫療婉拒、緊急等回覆不找。失敗就不顯示，不影響回答
    const finished = result.type
    if (finished && RECIPE_ANSWER_TYPES.includes(finished) && answer.trim()) {
      update(botId, () => ({ recipes: 'loading' }))
      try {
        const { data } = await api.post<RecipeSuggestions>('/assistant/recipes', { answer: answer.slice(0, 8000), type: finished })
        update(botId, () => ({ recipes: data }))
      } catch {
        update(botId, () => ({ recipes: undefined }))
      }
    }
  }, [refetch, engine])

  const stop = useCallback(() => abortRef.current?.abort(), [])
  const reset = useCallback(() => { abortRef.current?.abort(); setMessages([]) }, [])

  const value = useMemo(() => ({ messages, busy, offlineText, send, stop, reset, engine, setEngine, openaiAvailable }),
    [messages, busy, offlineText, send, stop, reset, engine, setEngine, openaiAvailable])
  return <AssistantChatContext.Provider value={value}>{children}</AssistantChatContext.Provider>
}
