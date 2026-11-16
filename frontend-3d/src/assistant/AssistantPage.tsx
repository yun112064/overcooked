/**
 * 飲食小幫手的展開模式（/assistant）：和吉祥物視窗是同一段對話，只是攤開成一整頁。
 * 它沒有自己的房間，借客廳當背景，對話放在一張浮在前面的奶油色面板上。
 * 約定：預設匯出一個不收 props 的元件（App.tsx 以 lazy 載入）。
 */
import { useEffect, useId } from 'react'
import { useNavigate } from 'react-router'
import z0 from '../assets/rooms/living/z0.svg'
import z1 from '../assets/rooms/living/z1.svg'
import z2 from '../assets/rooms/living/z2.svg'
import z3 from '../assets/rooms/living/z3.svg'
import { Room } from '../camera/Room'
import type { Stop } from '../camera/framing'
import { Button } from '../components/Button'
import { SceneHud } from '../shell/SceneHud'
import { ChatLog, Composer, EngineSwitch } from './ChatParts'
import { settledId } from './chatLogic'
import { useAssistantChat } from './chatStore'
import styles from './AssistantPage.module.css'

const LAYERS = [z0, z1, z2, z3]
const STOPS: Stop[] = [{ id: 'tv', label: '電視牆', fx: 0.5 }]

export default function AssistantPage() {
  const chat = useAssistantChat()
  const titleId = useId()
  const { setLauncher } = chat
  const navigate = useNavigate()
  // 從房間點「展開」進來的就回那個房間；直接開網址進來（沒有上一頁）就回客廳
  const closePage = () => {
    if (((window.history.state as { idx?: number } | null)?.idx ?? 0) > 0) navigate(-1)
    else navigate('/today', { replace: true })
  }
  // 回答還在生成時 settledId 是 0；已讀只往前不往回
  const seen = Math.max(settledId(chat.messages), chat.launcher.seenId)

  // 在這一頁看到的回答都算已讀，回到房間後吉祥物才不會又說「回答好了」；小視窗也順便收起來
  useEffect(() => {
    setLauncher({ view: 'closed', seenId: seen, greeted: true })
  }, [setLauncher, seen])

  return (
    <Room
      name="飲食小幫手" layers={LAYERS} stops={STOPS}
      hud={(
        // 這一頁本身就是對話，不再放會跑的吉祥物
        <SceneHud assistant={false}>
          <div className={styles.scrim} aria-hidden="true" />
          <section className={styles.page} aria-labelledby={titleId} data-no-swipe>
            <header className={styles.header}>
              <div className={styles.heading}>
                <h1 id={titleId}>飲食小幫手</h1>
                <p>
                  問飲食與營養的問題，回答整理自健康醫療網與 Hello醫師的文章，並附上出處。不提供診斷、用藥或劑量建議；疾病相關的飲食問題請諮詢醫師或營養師。
                </p>
              </div>
              <div className={styles.tools}>
                <EngineSwitch />
                {chat.messages.length > 0 && <Button variant="ghost" onClick={chat.reset}>重新開始</Button>}
                <Button onClick={closePage}>關閉</Button>
              </div>
            </header>
            <ChatLog scope="page" className={styles.log} />
            <div className={styles.foot}>
              <Composer>回答僅供一般飲食參考，不能取代醫師或營養師的個別建議；身體不適請就醫，緊急情況請撥 119。</Composer>
            </div>
          </section>
        </SceneHud>
      )}
    />
  )
}
