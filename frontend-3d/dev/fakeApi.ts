import type { IncomingMessage, ServerResponse } from 'node:http'
import type { Plugin } from 'vite'
import type { FakeRequest, FakeResponse } from './fake/core.ts'
import { handle } from './fake/router.ts'
import { createState } from './fake/seed.ts'

/**
 * 記憶體中的假後端，只在 `vite --mode fake` 啟用。
 * 資料全是合成的，重啟就歸零；不連任何資料庫或外部服務。
 * 這個檔案只負責把 HTTP 接到 dev/fake/router.ts 的 handle()；端點、種子資料與測試暗號見 dev/README.md。
 */

const SSE_GAP_MS = 140

async function readBody(req: IncomingMessage): Promise<Buffer> {
  const chunks: Buffer[] = []
  for await (const chunk of req) chunks.push(chunk as Buffer)
  return Buffer.concat(chunks)
}

/** 只取出需要的東西：文字欄位與上傳檔名（辨識只看檔名，不讀照片內容）。 */
export function parseMultipart(contentType: string, raw: Buffer): { fields: Record<string, string>; filename?: string } {
  const boundary = /boundary=(?:"([^"]+)"|([^;]+))/.exec(contentType)
  const fields: Record<string, string> = {}
  let filename: string | undefined
  if (!boundary) return { fields }
  for (const part of raw.toString('latin1').split(`--${boundary[1] ?? boundary[2]}`)) {
    const split = part.indexOf('\r\n\r\n')
    if (split === -1) continue
    const head = Buffer.from(part.slice(0, split), 'latin1').toString('utf-8')
    const name = /name="([^"]*)"/.exec(head)?.[1]
    const file = /filename="([^"]*)"/.exec(head)?.[1]
    if (file !== undefined) filename = file || 'upload'
    else if (name) fields[name] = Buffer.from(part.slice(split + 4).replace(/\r\n$/, ''), 'latin1').toString('utf-8')
  }
  return { fields, filename }
}

async function toRequest(req: IncomingMessage, url: URL): Promise<FakeRequest> {
  const headers: Record<string, string> = {}
  for (const [name, value] of Object.entries(req.headers)) {
    if (typeof value === 'string') headers[name.toLowerCase()] = value
  }
  const raw = await readBody(req)
  const contentType = headers['content-type'] ?? ''
  let body: unknown = {}
  let filename: string | undefined
  if (contentType.startsWith('multipart/form-data')) {
    const parsed = parseMultipart(contentType, raw)
    body = parsed.fields
    filename = parsed.filename
  } else if (raw.length) {
    try {
      body = JSON.parse(raw.toString('utf-8'))
    } catch {
      body = {}
    }
  }
  return {
    method: req.method ?? 'GET',
    path: url.pathname.slice('/api'.length),
    query: Object.fromEntries(url.searchParams),
    headers,
    body,
    filename,
    origin: headers.host ? `http://${headers.host}` : undefined,
  }
}

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

async function send(res: ServerResponse, response: FakeResponse): Promise<void> {
  res.statusCode = response.status
  if (response.sse) {
    // 與真後端相同的 SSE 格式；一段一段送，畫面才看得到逐字出現
    res.setHeader('Content-Type', 'text/event-stream; charset=utf-8')
    res.setHeader('Cache-Control', 'no-store')
    res.setHeader('X-Accel-Buffering', 'no')
    for (const event of response.sse) {
      if (res.destroyed) return // 使用者按了停止：不用再送
      res.write(`event: ${event.event}\ndata: ${JSON.stringify(event.data)}\n\n`)
      await wait(SSE_GAP_MS)
    }
    res.end()
    return
  }
  if (response.raw) {
    res.setHeader('Content-Type', response.raw.contentType)
    res.setHeader('Cache-Control', 'private, no-store')
    res.end(response.raw.text)
    return
  }
  res.setHeader('Content-Type', 'application/json; charset=utf-8')
  res.end(response.body === undefined ? '' : JSON.stringify(response.body))
}

const MARKET_PAGE = `<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>假超市</title></head>
<body style="font-family: system-ui, sans-serif; background: #f6efe0; color: #3c352b; padding: 32px; line-height: 1.7">
<h1 style="font-size: 20px">假超市（示意頁）</h1>
<p>這裡是假後端提供的示意頁。真的「一鍵購入」會帶著已採用餐單的缺料清單到模擬超市結帳；假後端不做結帳。</p>
<p>可以關掉這個分頁回到「今天吃啥」。</p></body></html>`

export function fakeApi(): Plugin {
  // 啟動時建立一次；重啟開發伺服器就回到種子資料
  const state = createState()
  return {
    name: 'fake-api',
    configureServer(server) {
      server.middlewares.use(async (req, res, next) => {
        const url = new URL(req.url ?? '/', 'http://localhost')
        if (url.pathname === '/__fake-market/checkout') {
          res.statusCode = 200
          res.setHeader('Content-Type', 'text/html; charset=utf-8')
          res.end(MARKET_PAGE)
          return
        }
        if (!url.pathname.startsWith('/api/')) return next()
        try {
          await send(res, handle(state, await toRequest(req, url)))
        } catch (err) {
          // 假後端自己的程式錯誤：照樣回統一的錯誤格式，方便在畫面上看出來
          res.statusCode = 500
          res.setHeader('Content-Type', 'application/json; charset=utf-8')
          res.end(JSON.stringify({ error: { code: 'FAKE_API_ERROR', message: `假 API 發生錯誤：${(err as Error).message}`, details: {} } }))
        }
      })
    },
  }
}
