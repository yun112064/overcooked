// 每頁都看得到的飲食小幫手：吉祥物在畫面下方走來走去，點它就能直接問問題。
import CloseRounded from '@mui/icons-material/CloseRounded'
import OpenInFullRounded from '@mui/icons-material/OpenInFullRounded'
import RestartAltRounded from '@mui/icons-material/RestartAltRounded'
import {
  Alert, Box, Chip, Grow, IconButton, Paper, Stack, Tooltip, Typography, useMediaQuery, useTheme,
} from '@mui/material'
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import AssistantMascot from '../components/AssistantMascot'
import { tokens } from '../theme'
import { Bubble, Composer, EngineSwitch } from './ChatParts'
import { EXAMPLES, useAssistantChat } from './chatStore'

const SIZE = 72
const EDGE = 12
const SPEED = 70 // px／秒

const randomBetween = (min: number, max: number) => min + Math.random() * (max - min)

export default function FloatingAssistant() {
  const chat = useAssistantChat()
  const navigate = useNavigate()
  const theme = useTheme()
  const mobile = useMediaQuery(theme.breakpoints.down('md'))
  const reducedMotion = useMediaQuery('(prefers-reduced-motion: reduce)')

  const rightmost = () => Math.max(EDGE, window.innerWidth - SIZE - EDGE)
  const [open, setOpen] = useState(false)
  const [x, setX] = useState(rightmost)
  const [duration, setDuration] = useState(0)
  const [facing, setFacing] = useState<1 | -1>(1)
  const [walking, setWalking] = useState(false)
  const [hint, setHint] = useState(true)
  const [seen, setSeen] = useState(0)
  const xRef = useRef(x)
  const listRef = useRef<HTMLDivElement | null>(null)

  const moveTo = (target: number, seconds: number) => {
    setFacing(target < xRef.current ? -1 : 1)
    xRef.current = target
    setDuration(seconds)
    setX(target)
  }

  // 一開始打招呼幾秒
  useEffect(() => {
    const t = window.setTimeout(() => setHint(false), 7000)
    return () => window.clearTimeout(t)
  }, [])

  // 閒晃：停一下、走到隨機位置；打開對話或使用者關閉動畫時就停在右下角（見下方 parked）
  useEffect(() => {
    if (open || reducedMotion) return
    let timer = 0
    const rest = () => {
      setWalking(false)
      timer = window.setTimeout(walk, randomBetween(3500, 8000))
    }
    const walk = () => {
      const target = randomBetween(EDGE, rightmost())
      const seconds = Math.min(9, Math.max(1.2, Math.abs(target - xRef.current) / SPEED))
      setWalking(true)
      moveTo(target, seconds)
      timer = window.setTimeout(rest, seconds * 1000)
    }
    timer = window.setTimeout(walk, 2500)
    return () => window.clearTimeout(timer)
  }, [open, reducedMotion])

  // 視窗縮小時不要走出畫面
  useEffect(() => {
    const onResize = () => { if (xRef.current > rightmost()) moveTo(rightmost(), 0) }
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  // 打開時捲到最新訊息
  useEffect(() => {
    const list = listRef.current
    if (open && list) list.scrollTop = list.scrollHeight
  }, [open, chat.messages])

  // 最後一則已完成（不在串流中）的訊息 id；比已讀的大就表示有新回答
  const last = chat.messages.at(-1)
  const settledId = last && last.status !== 'streaming' ? last.id : 0

  // 開關時記下已讀；關閉後吉祥物從右下角繼續閒晃
  const setPanel = (next: boolean) => {
    setSeen(settledId)
    setHint(false)
    if (!next) moveTo(rightmost(), 0)
    else setFacing(1)
    setOpen(next)
  }

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      setSeen(settledId)
      moveTo(rightmost(), 0)
      setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, settledId])

  const parked = open || reducedMotion
  const shownX = parked ? rightmost() : x
  const shownDuration = parked ? (reducedMotion ? 0 : 0.5) : duration
  const moving = walking && !parked
  const unread = !open && settledId > seen
  const bottom = mobile ? 'calc(64px + env(safe-area-inset-bottom))' : '16px'
  const bubbleText = chat.busy && !open ? '查資料中…' : unread ? '回答好了！' : hint && !open ? '有飲食問題問我！' : null

  return (
    <>
      <Grow in={open} style={{ transformOrigin: 'bottom right' }} unmountOnExit>
        <Paper role="dialog" aria-label="飲食小幫手" elevation={8} sx={{
          position: 'fixed', zIndex: 1250, right: { xs: 8, md: 16 }, left: { xs: 8, md: 'auto' },
          bottom: `calc(${bottom} + ${SIZE + 4}px)`, width: { md: 'min(560px, calc(100vw - 32px))' },
          height: { xs: `calc(100dvh - ${SIZE + 64 + 72}px)`, md: 'min(780px, calc(100vh - 120px))' },
          display: 'flex', flexDirection: 'column', overflow: 'hidden', borderRadius: '16px',
          bgcolor: tokens.washi, border: `1px solid ${tokens.line}`,
        }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, px: 1.5, py: 1, bgcolor: tokens.paper,
            borderBottom: `1px solid ${tokens.line}` }}>
            <AssistantMascot size={34} mood={chat.busy ? 'thinking' : 'idle'} />
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Typography sx={{ fontWeight: 500, lineHeight: 1.3 }}>飲食小幫手</Typography>
              <Typography variant="caption" sx={{ color: tokens.inkMuted }}>回答都附上出處</Typography>
            </Box>
            <EngineSwitch />
            {chat.messages.length > 0 && (
              <Tooltip title="重新開始">
                <IconButton size="small" onClick={chat.reset} aria-label="重新開始對話"><RestartAltRounded fontSize="small" /></IconButton>
              </Tooltip>
            )}
            <Tooltip title="開啟完整頁面">
              <IconButton size="small" aria-label="開啟完整頁面" onClick={() => { setPanel(false); navigate('/assistant') }}>
                <OpenInFullRounded fontSize="small" />
              </IconButton>
            </Tooltip>
            <Tooltip title="關閉">
              <IconButton size="small" onClick={() => setPanel(false)} aria-label="關閉飲食小幫手"><CloseRounded fontSize="small" /></IconButton>
            </Tooltip>
          </Box>

          <Stack ref={listRef} spacing={1.5} aria-live="polite" sx={{ flex: 1, overflowY: 'auto', p: 1.5, scrollBehavior: 'smooth' }}>
            {chat.offlineText && <Alert severity="warning">{chat.offlineText}</Alert>}
            {chat.messages.length === 0 && (
              <Box>
                <Typography sx={{ fontWeight: 500, mb: 0.5 }}>嗨！我是飲食小幫手</Typography>
                <Typography variant="body2" sx={{ color: tokens.inkSoft, mb: 1.5 }}>
                  想知道哪種食物有什麼營養，都可以問我。可以這樣問：
                </Typography>
                <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.75 }}>
                  {EXAMPLES.map((q) => (
                    <Chip key={q} size="small" label={q} variant="outlined" onClick={() => void chat.send(q)}
                      disabled={chat.busy || !!chat.offlineText} sx={{ bgcolor: tokens.paper, borderColor: tokens.line }} />
                  ))}
                </Box>
              </Box>
            )}
            {chat.messages.map((m) => <Bubble key={m.id} message={m} scope="float" compact />)}
          </Stack>

          <Box sx={{ p: 1.25, pt: 0.5 }}>
            <Composer compact autoFocus={!mobile} />
            <Typography variant="caption" component="p" sx={{ color: tokens.inkMuted, mt: 0.5, textAlign: 'center',
              fontSize: '0.68rem', lineHeight: 1.4 }}>
              僅供一般飲食參考，不能取代醫師或營養師；緊急情況請撥 119。
            </Typography>
          </Box>
        </Paper>
      </Grow>

      <Box sx={{
        position: 'fixed', zIndex: 1250, bottom, left: 0, width: SIZE, height: SIZE,
        transform: `translateX(${shownX}px)`, transition: `transform ${shownDuration}s linear`, pointerEvents: 'none',
      }}>
        {bubbleText && (
          <Box sx={{
            position: 'absolute', bottom: SIZE - 4, whiteSpace: 'nowrap', px: 1.25, py: 0.5, borderRadius: '12px',
            bgcolor: tokens.paper, border: `1px solid ${tokens.line}`, boxShadow: '0 4px 12px rgba(63,58,52,0.12)',
            fontSize: '0.8rem', color: tokens.ink,
            // 靠近右邊時泡泡往左長，避免超出畫面
            ...(shownX > SIZE ? { right: 6 } : { left: 6 }),
          }}>{bubbleText}</Box>
        )}
        <Box component="button" type="button" onClick={() => setPanel(!open)}
          aria-label={open ? '關閉飲食小幫手' : '開啟飲食小幫手，問飲食問題'} aria-expanded={open}
          sx={{
            pointerEvents: 'auto', position: 'relative', width: SIZE, height: SIZE, p: 0, border: 0, bgcolor: 'transparent',
            cursor: 'pointer', borderRadius: '50%', transition: 'transform 0.2s',
            '&:hover': { transform: 'scale(1.08)' },
            '&:focus-visible': { outline: `2px solid ${tokens.indigo}`, outlineOffset: 2 },
          }}>
          <AssistantMascot size={SIZE} mood={moving || chat.busy ? 'thinking' : 'idle'}
            sx={{ transform: `scaleX(${facing})`, transition: 'transform 0.2s' }} />
          {unread && (
            <Box component="span" sx={{ position: 'absolute', top: 6, right: 6, width: 14, height: 14, borderRadius: '50%',
              bgcolor: '#F97316', border: '2px solid #fff' }} />
          )}
        </Box>
      </Box>
    </>
  )
}
