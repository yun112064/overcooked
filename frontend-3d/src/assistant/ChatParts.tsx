// 對話的零件：訊息列表、泡泡、模型切換、輸入框。吉祥物的懸浮視窗與展開頁（/assistant）共用。
import { useId, useLayoutEffect, useRef, useState, type MouseEvent, type ReactNode, type RefObject } from 'react'
import type { AssistantEngine, RecipeSuggestion } from '../api/assistant'
import { Button } from '../components/Button'
import { Notice } from '../components/Notice'
import { Spinner } from '../components/Spinner'
import { MascotFigure } from '../shell/Mascot'
import { answerBadge, citedReferences, referenceByline, splitCitations } from './chatLogic'
import { ENGINE_LABEL, EXAMPLES, MAX_LENGTH, useAssistantChat, type Message } from './chatStore'
import styles from './ChatParts.module.css'

/** 離底部這麼近就當成「正在看最新的」，新內容進來時跟著捲 */
const STICK_PX = 80

// 點引用的小數字：只在對話框裡捲到那筆參考資料，不改網址（不然上一頁會多出一堆紀錄）
function jumpTo(event: MouseEvent, id: string) {
  event.preventDefault()
  const target = document.getElementById(id)
  target?.scrollIntoView({ block: 'nearest' })
  target?.focus({ preventScroll: true })
}

function References({ message, anchor }: { message: Message; anchor: string }) {
  const shown = citedReferences(message)
  if (!shown.length) return null
  return (
    <section className={styles.block}>
      <h3 className={styles.blockTitle}>參考資料</h3>
      <ol className={styles.refs}>
        {shown.map((r) => (
          <li key={r.n} id={`${anchor}-${r.n}`} tabIndex={-1}>
            <span className={styles.refNumber} aria-hidden="true">{r.n}</span>
            <div>
              <a href={r.url} target="_blank" rel="noopener noreferrer">
                <span className="visually-hidden">參考資料 {r.n}：</span>{r.title}
                <span aria-hidden="true"> ↗</span><span className="visually-hidden">（另開分頁）</span>
              </a>
              <p className={styles.byline}>{referenceByline(r)}</p>
            </div>
          </li>
        ))}
      </ol>
    </section>
  )
}

function RecipeCover({ recipe }: { recipe: RecipeSuggestion }) {
  const [failed, setFailed] = useState(false)
  // 沒有圖或圖載不到：放一塊素色，不留破圖
  if (!recipe.cover_url || failed) {
    return (
      <span className={`${styles.cover} ${styles.coverEmpty}`} aria-hidden="true">
        <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="5" /></svg>
      </span>
    )
  }
  return (
    <img
      className={styles.cover} src={`/api${recipe.cover_url}`} alt={recipe.dish_name} loading="lazy"
      onError={() => setFailed(true)}
    />
  )
}

/** 回答下方的食譜建議：菜取自食譜庫，以回答提到的食材為主要食材。 */
function RecipeSuggestionsBlock({ message, compact }: { message: Message; compact: boolean }) {
  const data = message.recipes
  if (!data) return null
  if (data === 'loading') {
    return <div className={styles.block}><Spinner className={styles.quiet} label="從食譜庫找用到這些食材的料理…" /></div>
  }
  if (!data.recipes.length) return null
  return (
    <section className={styles.block}>
      <h3 className={styles.recipeTitle}>可以試試這些料理</h3>
      <ul className={`${styles.recipes} ${compact ? '' : styles.recipesWide}`}>
        {data.recipes.map((r) => (
          <li key={r.recipe_id}>
            <RecipeCover recipe={r} />
            <div className={styles.recipeText}>
              <b>{r.dish_name}</b>
              {r.foods.length > 0 && (
                <span className={styles.foods}>{r.foods.map((f) => <i key={f}>含{f}</i>)}</span>
              )}
              <span className={styles.reason}>回答提到：{r.reason}</span>
              <a href={r.source_url} target="_blank" rel="noopener noreferrer">
                看做法（{r.source_name}）<span aria-hidden="true"> ↗</span><span className="visually-hidden">（另開分頁）</span>
              </a>
            </div>
          </li>
        ))}
      </ul>
      <p className={styles.fine}>
        依回答提到的食材，從我們的食譜庫挑選，僅供參考，不代表有療效。
        {data.household_filtered ? '已排除家人過敏與禁吃的食材。' : '選擇家庭後會排除家人過敏與禁吃的食材。'}
      </p>
    </section>
  )
}

function AssistantBubble({ message, scope, compact }: { message: Message; scope: string; compact: boolean }) {
  const chat = useAssistantChat()
  const anchor = `ref-${scope}-${message.id}`
  const badge = answerBadge(message.type)
  const streaming = message.status === 'streaming'
  return (
    <div className={styles.answerRow}>
      <MascotFigure className={styles.avatar} pose={streaming ? 'think' : 'still'} />
      <div className={styles.answer}>
        <span className="visually-hidden">飲食小幫手：</span>
        {badge && <span className={`${styles.badge} ${styles[badge.tone]}`}>{badge.label}</span>}
        {message.text ? (
          <div className={styles.text}>
            {splitCitations(message.text, message.references).map((part, i) => (part.kind === 'text' ? part.text : (
              <a
                key={i} href={`#${anchor}-${part.n}`} aria-label={`參考資料 ${part.n}`}
                className={`${styles.cite} ${part.joined ? styles.citeJoined : ''} ${part.followed ? styles.citeFollowed : ''}`}
                onClick={(event) => jumpTo(event, `${anchor}-${part.n}`)}
              >
                {part.joined ? `,${part.n}` : part.n}
              </a>
            )))}
            {streaming && <span className={styles.caret} aria-hidden="true" />}
          </div>
        ) : streaming ? <Spinner className={styles.quiet} label="查資料中…" /> : null}
        {message.status === 'stopped' && <p className={styles.fine}>（已停止）</p>}
        {message.error && (
          <Notice
            tone="error" className={message.text ? styles.gap : undefined}
            action={message.status === 'error' && (
              <Button disabled={chat.busy || !!chat.offlineText} onClick={() => chat.retry(message.id)}>再問一次</Button>
            )}
          >
            {message.error}
          </Notice>
        )}
        {/* 出處緊跟在回答下面，回答完成才列（生成到一半還不知道引用了哪幾筆） */}
        {message.status === 'done' && <References message={message} anchor={anchor} />}
        {message.status === 'done' && <RecipeSuggestionsBlock message={message} compact={compact} />}
        {/* 有兩種模型可選時標出這則回答是哪個生成的，方便比較 */}
        {chat.openaiAvailable && message.engine && !streaming && (
          <p className={`${styles.fine} ${styles.engineNote}`}>由 {ENGINE_LABEL[message.engine]} 生成</p>
        )}
      </div>
    </div>
  )
}

/** scope 讓同一則訊息在展開頁與懸浮視窗的參考資料錨點不重複。 */
export function Bubble({ message, scope, compact = false }: { message: Message; scope: string; compact?: boolean }) {
  if (message.role === 'user') {
    return <p className={styles.question}><span className="visually-hidden">你問：</span>{message.text}</p>
  }
  return <AssistantBubble message={message} scope={scope} compact={compact} />
}

type ChatLogProps = {
  scope: string
  /** 懸浮視窗用的緊湊版 */
  compact?: boolean
  /** 視窗縮小時這一塊是藏起來的；再打開時要重新捲到最新 */
  active?: boolean
  className?: string
}

/** 訊息列表：離線說明、還沒開始時的招呼與範例問題、一則則泡泡。自己會捲動，有新內容時跟到最下面。 */
export function ChatLog({ scope, compact = false, active = true, className }: ChatLogProps) {
  const chat = useAssistantChat()
  const list = useRef<HTMLDivElement>(null)
  const stick = useRef(true)
  const seenCount = useRef(0)
  const count = chat.messages.length

  useLayoutEffect(() => {
    const element = list.current
    if (!element || !active) return
    // 多了一則（剛送出問題）一定捲到底；回答生成中只有原本就在底部才跟著捲，讓人可以往回看
    if (count !== seenCount.current) {
      seenCount.current = count
      stick.current = true
    }
    if (stick.current) element.scrollTop = element.scrollHeight
  }, [chat.messages, count, active])

  const examples = (
    <div className={styles.examples}>
      {EXAMPLES.map((q) => (
        <button key={q} type="button" disabled={chat.busy || !!chat.offlineText} onClick={() => void chat.send(q)}>{q}</button>
      ))}
    </div>
  )

  return (
    <div
      ref={list} className={`${styles.log} ${compact ? styles.compact : ''} ${className ?? ''}`}
      // 生成中先不唸，完成後一次唸完整則回答
      aria-live="polite" aria-busy={chat.busy}
      onScroll={(event) => {
        const element = event.currentTarget
        stick.current = element.scrollHeight - element.scrollTop - element.clientHeight < STICK_PX
      }}
    >
      {chat.offlineText && <Notice tone="warn">{chat.offlineText}</Notice>}
      {count === 0 && (compact ? (
        <div>
          <p className={styles.helloTitle}>嗨！我是飲食小幫手</p>
          <p className={styles.helloText}>想知道哪種食物有什麼營養，都可以問我。可以這樣問：</p>
          {examples}
        </div>
      ) : (
        <div>
          <div className={styles.hello}>
            <MascotFigure className={styles.helloMascot} pose="wave" />
            <div className={styles.helloBubble}>
              <p className={styles.helloTitle}>嗨！我是飲食小幫手</p>
              <p className={styles.helloText}>想知道哪種食物有什麼營養，都可以問我。</p>
            </div>
          </div>
          <p className={styles.helloText}>可以這樣問：</p>
          {examples}
        </div>
      ))}
      {chat.messages.map((m) => <Bubble key={m.id} message={m} scope={scope} compact={compact} />)}
    </div>
  )
}

/** 回答模型切換：只有 OpenAI 版在線時才顯示；回答中不能切換。 */
export function EngineSwitch() {
  const chat = useAssistantChat()
  // 每個切換器自己一組 name，同時出現兩個也不會互相搶選取
  const name = useId()
  if (!chat.openaiAvailable) return null
  return (
    // 用真的 radio：方向鍵換選項、Tab 只停一次都是瀏覽器內建的，不用自己重做
    <div className={styles.engines} role="radiogroup" aria-label="回答模型">
      {(Object.keys(ENGINE_LABEL) as AssistantEngine[]).map((engine) => (
        <label key={engine}>
          <input
            type="radio" name={name} value={engine} checked={chat.engine === engine} disabled={chat.busy}
            onChange={() => chat.setEngine(engine)}
          />
          <span>{ENGINE_LABEL[engine]}</span>
        </label>
      ))}
    </div>
  )
}

type ComposerProps = {
  compact?: boolean
  /** 外面要把焦點移進輸入框時用 */
  inputRef?: RefObject<HTMLTextAreaElement | null>
  /** 輸入框下面的那行小字（免責說明） */
  children?: ReactNode
}

/** 問題輸入框與送出／停止按鈕。 */
export function Composer({ compact = false, inputRef, children }: ComposerProps) {
  const chat = useAssistantChat()
  const [input, setInput] = useState('')
  const ownRef = useRef<HTMLTextAreaElement>(null)
  const field = inputRef ?? ownRef
  const offline = !!chat.offlineText

  // 輸入框跟著內容長高（上限在 CSS），送出清空後縮回一行
  useLayoutEffect(() => {
    const element = field.current
    if (!element) return
    element.style.height = 'auto'
    element.style.height = `${element.scrollHeight}px`
  }, [field, input])

  const submit = () => {
    if (!input.trim() || chat.busy) return
    void chat.send(input)
    setInput('')
  }
  return (
    <form className={styles.composer} onSubmit={(event) => { event.preventDefault(); submit() }}>
      <div className={styles.inputRow}>
        <textarea
          ref={field} value={input} rows={1} maxLength={MAX_LENGTH} disabled={offline}
          aria-label="飲食問題" enterKeyHint="send"
          placeholder={offline ? '飲食小幫手目前離線' : compact ? '輸入飲食問題' : '輸入飲食問題，例如「吃鳳梨有什麼好處」'}
          onChange={(event) => setInput(event.target.value.slice(0, MAX_LENGTH))}
          onKeyDown={(event) => {
            // 注音選字時的 Enter 是確認選字，不是送出
            if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault()
              submit()
            }
          }}
        />
        {chat.busy ? (
          <button type="button" className={styles.stop} aria-label="停止回答" title="停止" onClick={chat.stop}>
            <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="7" y="7" width="10" height="10" rx="2" /></svg>
          </button>
        ) : (
          <button type="submit" className={styles.send} aria-label="送出問題" title="送出" disabled={!input.trim() || offline}>
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 19V6M6.5 11.5 12 6l5.5 5.5" /></svg>
          </button>
        )}
      </div>
      {children && <p className={styles.disclaimer}>{children}</p>}
    </form>
  )
}
