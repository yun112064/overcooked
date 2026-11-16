// R14 飲食小幫手：POST /api/assistant/chat，以 fetch 讀 SSE 串流（axios 在瀏覽器不支援串流）。

export type AssistantType =
  | 'greeting' | 'emergency' | 'declined_medical' | 'out_of_scope' | 'answer' | 'category_answer'

export type AssistantReference = {
  n: number
  title: string
  url: string
  source_name: string
  heading?: string
  author?: string | null
  reviewers?: Record<string, string> | null
  content_provider?: string | null
  published_at?: string | null
}

export type AssistantTurn = { role: 'user' | 'assistant'; content: string }

export type StreamHandlers = {
  onMeta?: (meta: { type: AssistantType; rewritten: boolean; fallback?: { food: string; label: string } }) => void
  onReferences?: (refs: AssistantReference[]) => void
  onDelta?: (text: string) => void
  onDone?: (done: { type: AssistantType; citations: number[] }) => void
}

export class AssistantError extends Error {
  code: string
  reason?: string
  constructor(code: string, message: string, reason?: string) {
    super(message)
    this.code = code
    this.reason = reason
  }
}

const STREAM_ERRORS: Record<string, string> = {
  ASSISTANT_STREAM_BROKEN: '連線中斷，回答沒有完成，請再問一次。',
  ASSISTANT_FAILED: '飲食小幫手發生錯誤，請再問一次。',
  LLM_BUDGET_EXHAUSTED: '本月 AI 額度已用完，飲食小幫手暫停回答，請下個月再試或通知管理者。',
}

/** 把 SSE 文字切成事件；回傳完整事件與剩下未完成的部分。 */
export function parseSse(buffer: string): { events: { event: string; data: unknown }[]; rest: string } {
  const events: { event: string; data: unknown }[] = []
  const blocks = buffer.replace(/\r\n/g, '\n').split('\n\n')
  const rest = blocks.pop() ?? ''
  for (const block of blocks) {
    let event = 'message'
    const data: string[] = []
    for (const line of block.split('\n')) {
      if (line.startsWith('event:')) event = line.slice(6).trim()
      else if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''))
    }
    if (!data.length) continue
    try {
      events.push({ event, data: JSON.parse(data.join('\n')) })
    } catch {
      // 不是 JSON 的事件略過
    }
  }
  return { events, rest }
}

/** local：地端 qwen3.5；openai：OpenAI 版服務（回答經雲端額度控制呼叫 OpenAI） */
export type AssistantEngine = 'local' | 'openai'

export async function askAssistant(message: string, history: AssistantTurn[], handlers: StreamHandlers,
  signal?: AbortSignal, engine: AssistantEngine = 'local'): Promise<void> {
  let res: Response
  try {
    res = await fetch('/api/assistant/chat', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify({ message, history, engine }),
      signal,
    })
  } catch (err) {
    if ((err as Error).name === 'AbortError') throw err
    throw new AssistantError('NETWORK', '無法連線到伺服器，請稍後再試。')
  }
  if (!res.ok || !res.body) {
    let code = 'HTTP_' + res.status
    let text = '飲食小幫手暫時無法使用，請稍後再試。'
    let reason: string | undefined
    try {
      const body = await res.json()
      code = body?.error?.code ?? code
      text = body?.error?.message ?? text
      reason = body?.error?.details?.reason
    } catch {
      // 沒有 JSON 內容時用預設訊息
    }
    throw new AssistantError(code, text, reason)
  }
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''
  let finished = false
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    const parsed = parseSse(buffer + value)
    buffer = parsed.rest
    for (const { event, data } of parsed.events) {
      if (event === 'meta') handlers.onMeta?.(data as never)
      else if (event === 'references') handlers.onReferences?.(data as AssistantReference[])
      else if (event === 'delta') handlers.onDelta?.((data as { text: string }).text)
      else if (event === 'done') { finished = true; handlers.onDone?.(data as never) }
      else if (event === 'error') {
        const code = (data as { code?: string }).code ?? 'ASSISTANT_FAILED'
        throw new AssistantError(code, STREAM_ERRORS[code] ?? '飲食小幫手暫時無法回答（' + code + '），請稍後再試。')
      }
    }
  }
  if (!finished) throw new AssistantError('ASSISTANT_STREAM_BROKEN', STREAM_ERRORS.ASSISTANT_STREAM_BROKEN)
}

/** 回答下方的食譜建議：菜一律取自食譜庫，foods 是回答裡提到、這道菜以它為主要食材的食材。 */
export type RecipeSuggestion = {
  dish_name: string
  recipe_id: string
  category: string
  cuisine: string
  source_name: string
  source_url: string
  foods: string[]
  reason: string
  cover_url: string | null
}

export type RecipeSuggestions = { recipes: RecipeSuggestion[]; household_filtered: boolean }

export const RECIPE_ANSWER_TYPES: AssistantType[] = ['answer', 'category_answer']
