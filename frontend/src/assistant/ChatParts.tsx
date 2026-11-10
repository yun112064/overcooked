// 對話泡泡與輸入框：完整頁面與浮動視窗共用。
import ArrowUpwardRounded from '@mui/icons-material/ArrowUpwardRounded'
import OpenInNewRounded from '@mui/icons-material/OpenInNewRounded'
import RestaurantMenuRounded from '@mui/icons-material/RestaurantMenuRounded'
import StopRounded from '@mui/icons-material/StopRounded'
import {
  Alert, Box, Chip, CircularProgress, IconButton, Link, Paper, Stack, TextField, ToggleButton, ToggleButtonGroup,
  Tooltip, Typography,
} from '@mui/material'
import { Fragment, useState, type ReactNode } from 'react'
import type { AssistantEngine, AssistantReference, AssistantType } from '../api/assistant'
import AssistantMascot from '../components/AssistantMascot'
import { tokens } from '../theme'
import { ENGINE_LABEL, MAX_LENGTH, useAssistantChat, type Message } from './chatStore'

const TYPE_LABEL: Partial<Record<AssistantType, { label: string; tone: 'info' | 'warning' | 'error' }>> = {
  category_answer: { label: '類別資訊', tone: 'info' },
  out_of_scope: { label: '超出範圍', tone: 'warning' },
  declined_medical: { label: '請諮詢醫師', tone: 'warning' },
  emergency: { label: '緊急情況', tone: 'error' },
}

function formatDate(value?: string | null) {
  if (!value) return null
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? null : `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}`
}

/** 把回答中的 [n] 換成可點的上標，連到下方參考資料。 */
function withCitations(text: string, anchor: string, refs: AssistantReference[] = []): ReactNode[] {
  const parts = text.split(/(\[\d{1,2}\])/g)
  let lastCite = false
  return parts.map((part, i) => {
    const m = /^\[(\d{1,2})\]$/.exec(part)
    if (!m) {
      if (part) lastCite = false
      return <Fragment key={i}>{part}</Fragment>
    }
    const n = Number(m[1])
    if (!refs.some((r) => r.n === n)) return <Fragment key={i}>{part}</Fragment>
    const comma = lastCite  // 連續的 [1][3] 顯示成 1,3
    lastCite = true
    return (
      <Box key={i} component="a" href={`#${anchor}-${n}`} aria-label={`參考資料 ${n}`} sx={{
        fontSize: '0.72em', verticalAlign: 'super', lineHeight: 0, color: tokens.indigo, textDecoration: 'none',
        px: 0.25, fontWeight: 500, '&:hover': { textDecoration: 'underline' },
      }}>{comma ? `,${n}` : n}</Box>
    )
  })
}

function References({ message, anchor }: { message: Message; anchor: string }) {
  const refs = message.references ?? []
  const cited = new Set(message.citations ?? [])
  const shown = cited.size ? refs.filter((r) => cited.has(r.n)) : []
  if (!shown.length) return null
  return (
    <Box sx={{ mt: 2, pt: 1.5, borderTop: `1px solid ${tokens.line}` }}>
      <Typography variant="caption" sx={{ color: tokens.inkMuted, letterSpacing: '0.1em' }}>參考資料</Typography>
      <Stack component="ol" spacing={1} sx={{ m: 0, mt: 0.75, pl: 0, listStyle: 'none' }}>
        {shown.map((r) => {
          // Hello醫師的 reviewers 含撰文、資料查核、醫學審稿；只顯示醫學審稿，沒有時顯示作者
          const reviewer = r.reviewers?.['醫學審稿'] ?? r.reviewers?.['審稿'] ?? ''
          const byline = [r.source_name, reviewer ? `醫學審稿：${reviewer}` : r.author ? `作者：${r.author}` : null,
            formatDate(r.published_at)].filter(Boolean).join('　')
          return (
            <Box component="li" key={r.n} id={`${anchor}-${r.n}`} sx={{ display: 'flex', gap: 1 }}>
              <Typography variant="body2" sx={{ color: tokens.indigo, fontWeight: 500, minWidth: 18 }}>{r.n}</Typography>
              <Box sx={{ minWidth: 0 }}>
                <Link href={r.url} target="_blank" rel="noopener noreferrer" variant="body2" underline="hover"
                  sx={{ color: tokens.ink, display: 'inline-flex', alignItems: 'center', gap: 0.5, overflowWrap: 'anywhere' }}>
                  {r.title}<OpenInNewRounded sx={{ fontSize: 14, color: tokens.inkMuted }} />
                </Link>
                <Typography variant="caption" component="p" sx={{ color: tokens.inkMuted }}>{byline}</Typography>
              </Box>
            </Box>
          )
        })}
      </Stack>
    </Box>
  )
}

const ORANGE = '#F97316'
const ORANGE_SOFT = '#FFF1E6'
const PURPLE_SOFT = '#F1ECFE'

/** 回答下方的食譜建議：菜取自食譜庫，以回答提到的食材為主要食材。 */
function RecipeSuggestionsBlock({ message, compact }: { message: Message; compact: boolean }) {
  const data = message.recipes
  if (!data) return null
  if (data === 'loading') {
    return (
      <Box sx={{ mt: 2, pt: 1.5, borderTop: `1px solid ${tokens.line}`, display: 'flex', alignItems: 'center', gap: 1,
        color: tokens.inkMuted }}>
        <CircularProgress size={14} /><Typography variant="caption">從食譜庫找用到這些食材的料理…</Typography>
      </Box>
    )
  }
  if (!data.recipes.length) return null
  return (
    <Box sx={{ mt: 2, pt: 1.5, borderTop: `1px solid ${tokens.line}` }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, mb: 1 }}>
        <RestaurantMenuRounded sx={{ fontSize: 18, color: ORANGE }} />
        <Typography variant="body2" sx={{ fontWeight: 500 }}>可以試試這些料理</Typography>
      </Box>
      <Box sx={{ display: 'grid', gap: 1.25,
        gridTemplateColumns: compact ? '1fr' : { xs: '1fr', sm: `repeat(${Math.min(data.recipes.length, 3)}, 1fr)` } }}>
        {data.recipes.map((r) => (
          <Box key={r.recipe_id} sx={{ display: 'flex', flexDirection: compact ? 'row' : { xs: 'row', sm: 'column' },
            gap: 1.25, p: 1.25, borderRadius: '12px', bgcolor: ORANGE_SOFT, border: '1px solid #FCD9BD', minWidth: 0 }}>
            {r.cover_url ? (
              <Box component="img" src={`/api${r.cover_url}`} alt={r.dish_name} loading="lazy"
                sx={{ width: compact ? 72 : { xs: 72, sm: '100%' }, height: compact ? 72 : { xs: 72, sm: 120 },
                  objectFit: 'cover', borderRadius: '8px', flexShrink: 0, bgcolor: PURPLE_SOFT }} />
            ) : (
              <Box sx={{ width: compact ? 72 : { xs: 72, sm: '100%' }, height: compact ? 72 : { xs: 72, sm: 120 },
                borderRadius: '8px', flexShrink: 0, bgcolor: PURPLE_SOFT, display: 'grid', placeItems: 'center' }}>
                <RestaurantMenuRounded sx={{ color: '#8B5CF6', fontSize: 30 }} />
              </Box>
            )}
            <Box sx={{ minWidth: 0, flex: 1, display: 'flex', flexDirection: 'column', gap: 0.5 }}>
              <Typography sx={{ fontWeight: 600, lineHeight: 1.35 }}>{r.dish_name}</Typography>
              <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
                {r.foods.map((f) => (
                  <Chip key={f} size="small" label={`含${f}`}
                    sx={{ height: 22, bgcolor: '#fff', color: '#B4470B', border: '1px solid #FCD9BD', fontSize: '0.75rem' }} />
                ))}
              </Box>
              <Typography variant="caption" sx={{ color: tokens.inkSoft, lineHeight: 1.5, display: '-webkit-box',
                WebkitLineClamp: 3, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                回答提到：{r.reason}
              </Typography>
              <Link href={r.source_url} target="_blank" rel="noopener noreferrer" variant="caption" underline="hover"
                sx={{ mt: 'auto', color: '#B4470B', display: 'inline-flex', alignItems: 'center', gap: 0.25 }}>
                看做法（{r.source_name}）<OpenInNewRounded sx={{ fontSize: 13 }} />
              </Link>
            </Box>
          </Box>
        ))}
      </Box>
      <Typography variant="caption" component="p" sx={{ color: tokens.inkMuted, mt: 1, lineHeight: 1.5 }}>
        依回答提到的食材，從我們的食譜庫挑選，僅供參考，不代表有療效。
        {data.household_filtered ? '已排除家人過敏與禁吃的食材。' : '選擇家庭後會排除家人過敏與禁吃的食材。'}
      </Typography>
    </Box>
  )
}

/** scope 讓同一則訊息在頁面與浮動視窗的參考資料錨點不重複。 */
export function Bubble({ message, scope, compact = false }: { message: Message; scope: string; compact?: boolean }) {
  if (message.role === 'user') {
    return (
      <Box sx={{ alignSelf: 'flex-end', maxWidth: '85%', bgcolor: tokens.indigo, color: '#fff', px: compact ? 1.5 : 2,
        py: compact ? 1 : 1.25, borderRadius: '14px 14px 4px 14px', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere',
        fontSize: compact ? '0.92rem' : undefined }}>
        {message.text}
      </Box>
    )
  }
  return <AssistantBubble message={message} scope={scope} compact={compact} />
}

function AssistantBubble({ message, scope, compact }: { message: Message; scope: string; compact: boolean }) {
  const { openaiAvailable } = useAssistantChat()
  const anchor = `ref-${scope}-${message.id}`
  const badge = message.type ? TYPE_LABEL[message.type] : undefined
  return (
    <Box sx={{ alignSelf: 'stretch', display: 'flex', alignItems: 'flex-start', gap: compact ? 0.75 : { xs: 1, sm: 1.5 } }}>
      <AssistantMascot size={compact ? 30 : 40} mood={message.status === 'streaming' ? 'thinking' : 'idle'}
        sx={{ mt: -0.5 }} />
      <Paper variant="outlined" sx={{ flex: 1, minWidth: 0, px: compact ? 1.5 : { xs: 2, sm: 2.5 }, py: compact ? 1.25 : 2,
        borderRadius: '4px 14px 14px 14px', borderColor: tokens.line, bgcolor: tokens.paper }}>
        {badge && <Chip size="small" color={badge.tone} variant="outlined" label={badge.label} sx={{ mb: 1 }} />}
        {message.text ? (
          <Typography component="div" sx={{ whiteSpace: 'pre-wrap', lineHeight: compact ? 1.75 : 1.85,
            overflowWrap: 'anywhere', fontSize: compact ? '0.92rem' : undefined }}>
            {withCitations(message.text, anchor, message.references)}
            {message.status === 'streaming' && <Box component="span" sx={{ display: 'inline-block', width: 8, height: '1em',
              ml: 0.5, verticalAlign: 'text-bottom', bgcolor: tokens.inkMuted, animation: 'blink 1s step-end infinite',
              '@keyframes blink': { '50%': { opacity: 0 } } }} />}
          </Typography>
        ) : message.status === 'streaming' ? (
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, color: tokens.inkMuted }}>
            <CircularProgress size={16} /><Typography variant="body2">查資料中…</Typography>
          </Box>
        ) : null}
        {message.status === 'stopped' && <Typography variant="caption" sx={{ color: tokens.inkMuted }}>（已停止）</Typography>}
        {message.error && <Alert severity="error" sx={{ mt: message.text ? 1.5 : 0 }}>{message.error}</Alert>}
        {message.status === 'done' && <References message={message} anchor={anchor} />}
        {message.status === 'done' && <RecipeSuggestionsBlock message={message} compact={compact} />}
        {/* 有兩種模型可選時標出這則回答是哪個生成的，方便比較 */}
        {openaiAvailable && message.engine && message.status !== 'streaming' && (
          <Typography variant="caption" component="p" sx={{ mt: 1, color: tokens.inkMuted, textAlign: 'right' }}>
            由 {ENGINE_LABEL[message.engine]} 生成
          </Typography>
        )}
      </Paper>
    </Box>
  )
}

/** 回答模型切換：只有 OpenAI 版在線時才顯示；回答中不能切換。 */
export function EngineSwitch({ size = 'small' }: { size?: 'small' | 'medium' }) {
  const chat = useAssistantChat()
  if (!chat.openaiAvailable) return null
  return (
    <ToggleButtonGroup exclusive size={size} value={chat.engine} disabled={chat.busy} aria-label="回答模型"
      onChange={(_, value: AssistantEngine | null) => { if (value) chat.setEngine(value) }}
      sx={{ bgcolor: tokens.paper, '& .MuiToggleButton-root': { px: 1.25, py: 0.25, fontSize: '0.75rem', lineHeight: 1.6,
        whiteSpace: 'nowrap', '&.Mui-selected': { bgcolor: '#F1ECFE', color: '#6D3FD9' } } }}>
      <ToggleButton value="local">{ENGINE_LABEL.local}</ToggleButton>
      <ToggleButton value="openai">{ENGINE_LABEL.openai}</ToggleButton>
    </ToggleButtonGroup>
  )
}

/** 問題輸入框與送出／停止按鈕。 */
export function Composer({ autoFocus = false, compact = false }: { autoFocus?: boolean; compact?: boolean }) {
  const chat = useAssistantChat()
  const [input, setInput] = useState('')
  const offline = !!chat.offlineText
  const submit = () => {
    if (!input.trim() || chat.busy) return
    void chat.send(input)
    setInput('')
  }
  return (
    <Box component="form" onSubmit={(e) => { e.preventDefault(); submit() }}>
      <Paper variant="outlined" sx={{ display: 'flex', alignItems: 'flex-end', gap: 1, p: compact ? 0.75 : 1,
        pl: compact ? 1.5 : 2, borderColor: tokens.lineStrong, bgcolor: tokens.paper,
        boxShadow: compact ? 'none' : '0 6px 24px rgba(63,58,52,0.08)' }}>
        <TextField value={input} onChange={(e) => setInput(e.target.value.slice(0, MAX_LENGTH))}
          placeholder={offline ? '飲食小幫手目前離線' : compact ? '輸入飲食問題' : '輸入飲食問題，例如「吃鳳梨有什麼好處」'}
          multiline maxRows={compact ? 4 : 5} fullWidth variant="standard" disabled={offline} autoFocus={autoFocus}
          slotProps={{ input: { disableUnderline: true }, htmlInput: { 'aria-label': '飲食問題', maxLength: MAX_LENGTH } }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submit() }
          }} sx={{ py: compact ? 0.5 : 0.75 }} />
        {chat.busy ? (
          <Tooltip title="停止">
            <IconButton onClick={chat.stop} aria-label="停止回答" size={compact ? 'small' : 'medium'}
              sx={{ bgcolor: tokens.sand }}>
              <StopRounded />
            </IconButton>
          </Tooltip>
        ) : (
          <Tooltip title="送出">
            <span>
              <IconButton type="submit" aria-label="送出問題" disabled={!input.trim() || offline}
                size={compact ? 'small' : 'medium'}
                sx={{ bgcolor: tokens.indigo, color: '#fff', '&:hover': { bgcolor: tokens.indigoDark },
                  '&.Mui-disabled': { bgcolor: tokens.sand, color: tokens.inkMuted } }}>
                <ArrowUpwardRounded />
              </IconButton>
            </span>
          </Tooltip>
        )}
      </Paper>
    </Box>
  )
}
