/**
 * 飲食小幫手的對話狀態。包在房間的路由外面，換房間時對話不會消失
 * （對應舊前端的 AssistantChatProvider；端點、傳的內容與 query key 都相同）。
 *
 * 對話綁在「這個帳號＋這個家庭」上：換帳號或換家庭就從頭開始，
 * 因為食譜建議是依那個家庭的過敏與禁吃食材挑的，不能帶到別的家庭去。
 * 這樣不論它被掛在路由的哪一層（甚至 Router 外面）都不會把上一個人的對話留給下一個人。
 * 只需要在 QueryClientProvider 裡面；不需要 Router。
 *
 * 約定：預設匯出一個只收 children 的元件。
 */
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useRef, useState, type Dispatch, type ReactNode, type SetStateAction } from 'react'
import { api } from '../api/client'
import {
  AssistantError, RECIPE_ANSWER_TYPES, askAssistant, type AssistantEngine, type AssistantType, type RecipeSuggestions,
} from '../api/assistant'
import { useMe } from '../session/session'
import { buildHistory, offlineText as offlineTextFor, openaiOnline, type TeamStatus } from './chatLogic'
import { AssistantChatContext, type AssistantChat, type LauncherState, type Message } from './chatStore'

const ENGINE_KEY = 'assistant-engine'
const INITIAL_LAUNCHER: LauncherState = { view: 'closed', seenId: 0, greeted: false, phase: null }

function savedEngine(): AssistantEngine {
  try {
    return localStorage.getItem(ENGINE_KEY) === 'openai' ? 'openai' : 'local'
  } catch {
    return 'local'
  }
}

function patchMessage(setMessages: Dispatch<SetStateAction<Message[]>>, id: number, patch: (m: Message) => Partial<Message>) {
  setMessages((list) => list.map((m) => (m.id === id ? { ...m, ...patch(m) } : m)))
}

export default function AssistantProvider({ children }: { children: ReactNode }) {
  const me = useMe().data
  const signedIn = !!me
  /** 這段對話是誰的：帳號＋家庭 */
  const owner = `${me?.user.id ?? ''}:${me?.current_household?.id ?? ''}`
  const [messages, setMessages] = useState<Message[]>([])
  const [busy, setBusy] = useState(false)
  const [launcher, setLauncherState] = useState(INITIAL_LAUNCHER)
  const abortRef = useRef<AbortController | null>(null)
  const nextId = useRef(1)
  const messagesRef = useRef(messages)
  useEffect(() => { messagesRef.current = messages }, [messages])

  // 換了帳號或家庭：對話從頭開始（見檔頭）。在畫面更新前就清掉，不會先閃一下上一個家庭的對話；
  // 不用 key 重新掛載，是因為那樣連裡面的整個房間都會跟著重來
  const [shownFor, setShownFor] = useState(owner)
  if (shownFor !== owner) {
    setShownFor(owner)
    setMessages([])
    setLauncherState(INITIAL_LAUNCHER)
  }

  const status = useQuery({
    queryKey: ['team-health'],
    queryFn: async () => (await api.get<TeamStatus>('/health/team')).data,
    refetchInterval: 30_000,
    enabled: signedIn,
  })
  // OpenAI 版有設定且在線才能選；否則一律用地端
  const openaiAvailable = openaiOnline(status.data)
  const [chosen, setChosen] = useState<AssistantEngine>(savedEngine)
  const engine: AssistantEngine = openaiAvailable ? chosen : 'local'
  const setEngine = useCallback((next: AssistantEngine) => {
    setChosen(next)
    try { localStorage.setItem(ENGINE_KEY, next) } catch { /* 只是記住選擇，失敗不影響 */ }
  }, [])
  const offlineText = offlineTextFor(status.data, engine)

  // 離開屋子（登出、換家庭）時不要讓回答繼續在背景生成
  useEffect(() => () => abortRef.current?.abort(), [owner])

  const { refetch } = status
  const client = useQueryClient()
  /** drop：這次提問要一併拿掉的舊訊息（重問時是失敗的那一組），它們也不算前文 */
  const ask = useCallback(async (text: string, drop: readonly number[] = []) => {
    const question = text.trim()
    if (!question || abortRef.current) return
    const history = buildHistory(messagesRef.current.filter((m) => !drop.includes(m.id)))
    const userId = nextId.current++
    const botId = nextId.current++
    setMessages((list) => [...list.filter((m) => !drop.includes(m.id)),
      { id: userId, role: 'user', text: question, status: 'done' },
      { id: botId, role: 'assistant', text: '', status: 'streaming', engine }])
    setBusy(true)
    const controller = new AbortController()
    abortRef.current = controller
    let answer = ''
    const result: { type: AssistantType | null } = { type: null }
    try {
      await askAssistant(question, history, {
        onMeta: (meta) => patchMessage(setMessages, botId, () => ({ type: meta.type })),
        onReferences: (refs) => patchMessage(setMessages, botId, () => ({ references: refs })),
        onDelta: (delta) => { answer += delta; patchMessage(setMessages, botId, (m) => ({ text: m.text + delta })) },
        onDone: (done) => {
          result.type = done.type
          patchMessage(setMessages, botId, () => ({ status: 'done', type: done.type, citations: done.citations }))
        },
      }, controller.signal, engine)
    } catch (err) {
      if ((err as Error).name === 'AbortError') {
        patchMessage(setMessages, botId, () => ({ status: 'stopped' }))
      } else {
        const message = err instanceof AssistantError ? err.message : '飲食小幫手暫時無法回答，請稍後再試。'
        patchMessage(setMessages, botId, () => ({ status: 'error', error: message }))
        if (err instanceof AssistantError && err.code === 'ASSISTANT_UNAVAILABLE') void refetch()
        // 登入過期了：重新確認身分，進門的檢查會把人帶回大門（錯誤訊息仍留在對話裡）
        if (err instanceof AssistantError && err.code === 'UNAUTHENTICATED') void client.invalidateQueries({ queryKey: ['me'] })
      }
    } finally {
      setBusy(false)
      abortRef.current = null
    }
    // 回答完成後，依回答提到的食材從食譜庫找菜；醫療婉拒、緊急等回覆不找。失敗就不顯示，不影響回答
    const finished = result.type
    if (finished && RECIPE_ANSWER_TYPES.includes(finished) && answer.trim()) {
      patchMessage(setMessages, botId, () => ({ recipes: 'loading' }))
      try {
        const { data } = await api.post<RecipeSuggestions>('/assistant/recipes', { answer: answer.slice(0, 8000), type: finished })
        patchMessage(setMessages, botId, () => ({ recipes: data }))
      } catch {
        patchMessage(setMessages, botId, () => ({ recipes: undefined }))
      }
    }
  }, [refetch, client, engine])

  const send = useCallback((text: string) => ask(text), [ask])

  const retry = useCallback((id: number) => {
    const list = messagesRef.current
    const index = list.findIndex((m) => m.id === id)
    const failed = list[index]
    const question = list[index - 1]
    if (!failed || failed.status !== 'error' || question?.role !== 'user') return
    void ask(question.text, [question.id, failed.id])
  }, [ask])

  const stop = useCallback(() => abortRef.current?.abort(), [])
  // 清空後訊息編號繼續往上加，所以已讀的編號不用歸零
  const reset = useCallback(() => { abortRef.current?.abort(); setMessages([]) }, [])
  const setLauncher = useCallback((patch: Partial<LauncherState>) => setLauncherState((prev) => ({ ...prev, ...patch })), [])

  const value = useMemo<AssistantChat>(() => ({
    messages, busy, offlineText, send, retry, stop, reset, engine, setEngine, openaiAvailable, launcher, setLauncher,
  }), [messages, busy, offlineText, send, retry, stop, reset, engine, setEngine, openaiAvailable, launcher, setLauncher])
  return <AssistantChatContext.Provider value={value}>{children}</AssistantChatContext.Provider>
}
