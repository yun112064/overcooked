import { Box, Paper, Stack, Tooltip, Typography, type SxProps, type Theme } from '@mui/material'
import type { ReactNode } from 'react'
import { tokens } from '../theme'
import { initial, TONES, type Tone } from './visuals'

/** 圓角方塊裡放一個圖示；純裝飾（aria-hidden），意義由旁邊的文字表達。 */
export function IconTile({ icon, tone = 'ink', size = 40 }: { icon: ReactNode; tone?: Tone; size?: number }) {
  const c = TONES[tone]
  return (
    <Box component="span" aria-hidden sx={{
      width: size, height: size, borderRadius: '10px', flexShrink: 0, display: 'inline-grid', placeItems: 'center',
      bgcolor: c.bg, color: c.fg, '& svg': { fontSize: size * 0.55 },
    }}>{icon}</Box>
  )
}

const AVATAR_TONES: Tone[] = ['brick', 'indigo', 'matcha', 'amber']

/** 成員頭像；依順序輪替點綴色。 */
export function MemberAvatar({ name, index = 0, size = 40, ring }: { name: string; index?: number; size?: number; ring?: string }) {
  const c = TONES[AVATAR_TONES[index % AVATAR_TONES.length]]
  return (
    <Box aria-hidden sx={{
      width: size, height: size, borderRadius: '50%', flexShrink: 0, display: 'grid', placeItems: 'center',
      bgcolor: c.bg, color: c.fg, fontFamily: tokens.serif, fontSize: size * 0.42,
      ...(ring ? { boxShadow: `0 0 0 2px ${ring}` } : {}),
    }}>{initial(name)}</Box>
  )
}

/** 疊在一起的成員頭像（最多 5 個，其餘顯示 +N）；名字放在 Tooltip 與螢幕閱讀器文字。 */
export function AvatarStack({ names, ring = tokens.paper }: { names: string[]; ring?: string }) {
  const shown = names.slice(0, 5)
  return (
    <Tooltip title={names.join('、')}>
      <Box sx={{ display: 'flex', alignItems: 'center' }} aria-label={`成員：${names.join('、')}`} role="img">
        {shown.map((n, i) => (
          <Box key={n + i} sx={{ ml: i === 0 ? 0 : -1 }}><MemberAvatar name={n} index={i} size={32} ring={ring} /></Box>
        ))}
        {names.length > shown.length && (
          <Typography variant="caption" sx={{ ml: 1 }}>+{names.length - shown.length}</Typography>
        )}
      </Box>
    </Tooltip>
  )
}

/** 品牌印章：方框裡一個明體「食」。 */
export function BrandMark({ size = 32 }: { size?: number }) {
  return (
    <Box aria-hidden sx={{
      width: size, height: size, flexShrink: 0, display: 'grid', placeItems: 'center',
      border: `1.5px solid ${tokens.brick}`, color: tokens.brick, borderRadius: '3px',
      fontFamily: tokens.serif, fontWeight: 600, fontSize: size * 0.56, lineHeight: 1,
    }}>食</Box>
  )
}

/** 頁首：小字英文眉標、明體標題、說明與右側動作。 */
export function PageHeader({ eyebrow, title, description, action }: {
  eyebrow?: string; title: ReactNode; description?: ReactNode; action?: ReactNode
}) {
  return (
    <Box sx={{ display: 'flex', alignItems: { sm: 'flex-end' }, gap: 2, flexWrap: 'wrap', pb: 1 }}>
      <Box sx={{ flexGrow: 1, minWidth: 0 }}>
        {eyebrow && <Typography variant="overline" component="p">{eyebrow}</Typography>}
        <Typography variant="h4" component="h1">{title}</Typography>
        {description && (
          <Typography color="text.secondary" variant="body2" sx={{ mt: 1, maxWidth: 560 }}>{description}</Typography>
        )}
      </Box>
      {action && <Box sx={{ display: 'flex', gap: 1, flexShrink: 0 }}>{action}</Box>}
    </Box>
  )
}

/** 內容區塊：標題列＋細線＋內容，用在結果頁與紀錄頁的各段。 */
export function Section({ title, aside, children, sx }: {
  title: ReactNode; aside?: ReactNode; children: ReactNode; sx?: SxProps<Theme>
}) {
  return (
    <Paper variant="outlined" component="section" sx={{ p: { xs: 2, sm: 3 }, ...sx }}>
      <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 1, flexWrap: 'wrap', mb: 2,
        pb: 1.5, borderBottom: `1px solid ${tokens.line}` }}>
        <Typography variant="subtitle1" component="h2" sx={{ flexGrow: 1 }}>{title}</Typography>
        {aside && <Typography variant="caption">{aside}</Typography>}
      </Box>
      {children}
    </Paper>
  )
}

/** 空狀態：圖示、明體標題、一句說明與動作。 */
export function EmptyState({ icon, title, description, action }: {
  icon?: ReactNode; title: string; description?: string; action?: ReactNode
}) {
  return (
    <Stack spacing={1.5} sx={{
      alignItems: 'center', textAlign: 'center', py: { xs: 6, sm: 8 }, px: 3,
      border: `1px dashed ${tokens.lineStrong}`, borderRadius: 1,
    }}>
      {icon && <Box sx={{ color: tokens.inkMuted, '& svg': { fontSize: 40 } }}>{icon}</Box>}
      <Typography variant="h6" component="p">{title}</Typography>
      {description && <Typography variant="body2" color="text.secondary" sx={{ maxWidth: 420 }}>{description}</Typography>}
      {action && <Box sx={{ pt: 1 }}>{action}</Box>}
    </Stack>
  )
}

/** 摘要數字：小標籤、大數字、補充說明；tone 用來標示達標／偏離。 */
export function Stat({ label, value, unit, note, tone = 'default' }: {
  label: string; value: ReactNode; unit?: string; note?: ReactNode
  tone?: 'default' | 'good' | 'warn' | 'bad'
}) {
  const noteColor = { default: tokens.inkSoft, good: '#5E7B5A', warn: tokens.ochre, bad: tokens.brick }[tone]
  return (
    <Box sx={{ minWidth: 0 }}>
      <Typography variant="caption" component="p" sx={{ letterSpacing: '0.16em', color: tokens.inkMuted }}>{label}</Typography>
      <Typography component="p" sx={{ fontFamily: tokens.serif, fontSize: '1.6rem', lineHeight: 1.3, mt: 0.5 }}>
        {value}
        {unit && <Box component="span" sx={{ fontSize: '0.85rem', ml: 0.5, color: tokens.inkSoft }}>{unit}</Box>}
      </Typography>
      {note && <Typography variant="caption" component="p" sx={{ color: noteColor, mt: 0.25 }}>{note}</Typography>}
    </Box>
  )
}

/** 一排摘要數字，中間以細線分隔；手機改成兩欄。 */
export function StatRow({ children }: { children: ReactNode }) {
  return (
    <Box sx={{
      display: 'grid', gridTemplateColumns: { xs: 'repeat(2, minmax(0, 1fr))', sm: 'repeat(4, minmax(0, 1fr))' },
      gap: { xs: 2.5, sm: 0 },
      '& > *': { px: { sm: 2.5 } },
      '& > *:not(:first-of-type)': { borderLeft: { sm: `1px solid ${tokens.line}` } },
      '& > *:first-of-type': { pl: { sm: 0 } },
    }}>
      {children}
    </Box>
  )
}
