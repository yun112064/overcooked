import RestartAltRounded from '@mui/icons-material/RestartAltRounded'
import { Alert, Box, Button, Chip, Stack, Typography } from '@mui/material'
import { useEffect, useRef } from 'react'
import { Bubble, Composer, EngineSwitch } from '../assistant/ChatParts'
import { EXAMPLES, useAssistantChat } from '../assistant/chatStore'
import AssistantMascot from '../components/AssistantMascot'
import { PageHeader } from '../components/ui'
import { tokens } from '../theme'

export default function AssistantPage() {
  const { messages, busy, offlineText, send, reset } = useAssistantChat()
  const endRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }) }, [messages])

  return (
    <Stack spacing={3}>
      <PageHeader eyebrow="Diet Q&A" title="飲食小幫手"
        description="問飲食與營養的問題，回答整理自健康醫療網與 Hello醫師的文章，並附上出處。不提供診斷、用藥或劑量建議；疾病相關的飲食問題請諮詢醫師或營養師。"
        action={(
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
            <EngineSwitch />
            {messages.length > 0 && (
              <Button startIcon={<RestartAltRounded />} onClick={reset} color="inherit" sx={{ color: tokens.inkSoft }}>
                重新開始
              </Button>
            )}
          </Box>
        )} />

      {offlineText && <Alert severity="warning">{offlineText}</Alert>}

      <Stack spacing={2} aria-live="polite">
        {messages.length === 0 && (
          <Box sx={{ py: 2 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mb: 2.5 }}>
              <AssistantMascot size={104} />
              <Box sx={{ position: 'relative', bgcolor: tokens.paper, border: `1px solid ${tokens.line}`, borderRadius: '14px',
                px: 2, py: 1.25, '&::before': { content: '""', position: 'absolute', left: -7, top: '50%', width: 12, height: 12,
                  bgcolor: tokens.paper, borderLeft: `1px solid ${tokens.line}`, borderBottom: `1px solid ${tokens.line}`,
                  transform: 'translateY(-50%) rotate(45deg)' } }}>
                <Typography sx={{ fontWeight: 500 }}>嗨！我是飲食小幫手</Typography>
                <Typography variant="body2" sx={{ color: tokens.inkSoft }}>想知道哪種食物有什麼營養，都可以問我。</Typography>
              </Box>
            </Box>
            <Typography variant="body2" sx={{ color: tokens.inkSoft, mb: 1.5 }}>可以這樣問：</Typography>
            <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1 }}>
              {EXAMPLES.map((q) => (
                <Chip key={q} label={q} variant="outlined" onClick={() => void send(q)} disabled={busy || !!offlineText}
                  sx={{ bgcolor: tokens.paper, borderColor: tokens.line }} />
              ))}
            </Box>
          </Box>
        )}
        {messages.map((m) => <Bubble key={m.id} message={m} scope="page" />)}
        {/* 捲到最新訊息時避開下方固定的輸入框 */}
        <Box ref={endRef} sx={{ scrollMarginBottom: { xs: 200, md: 150 } }} />
      </Stack>

      <Box sx={{ position: 'sticky', bottom: { xs: 56, md: 0 }, zIndex: 5, bgcolor: tokens.washi, pt: 1, pb: { xs: 1.5, md: 2 },
        boxShadow: `0 -12px 12px -4px ${tokens.washi}` }}>
        <Composer />
        <Typography variant="caption" component="p" sx={{ color: tokens.inkMuted, mt: 0.75, textAlign: 'center' }}>
          回答僅供一般飲食參考，不能取代醫師或營養師的個別建議；身體不適請就醫，緊急情況請撥 119。
        </Typography>
      </Box>
    </Stack>
  )
}
