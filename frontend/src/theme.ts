import { alpha, createTheme } from '@mui/material'

/** C 日系簡約：米白和紙底、炭灰文字、靛藍主色；標題用明體，內文用黑體，圓角小、留白多。 */
export const tokens = {
  washi: '#F5F1EA', // 頁面底
  paper: '#FBF9F5', // 卡片
  sand: '#EDE6DA', // 次要底（標籤、側欄）
  line: '#E4DCCF', // 分隔線
  lineStrong: '#D3C8B6',
  ink: '#3F3A34', // 主要文字
  inkSoft: '#6B6358', // 次要文字
  inkMuted: '#6E6559', // 小字對比至少 4.5:1（最深的 sand 底上 4.62）
  indigo: '#4A5D7E',
  indigoDark: '#36465F',
  matcha: '#6F7D5C',
  ochre: '#8C6428', // 警示文字對比 5.2:1
  brick: '#A4553F',
  amber: '#B7791F', // 食物點綴：早餐、碳水
  // 點綴色的淺底（標籤、圖示方塊、圖表填色背景）
  indigoTint: '#E6EAF1',
  matchaTint: '#E9EDE3',
  amberTint: '#F6EBD9',
  brickTint: '#F3E3DD',
  serif: '"Noto Serif TC", "Songti TC", "PMingLiU", serif',
  sans: '"Noto Sans TC", "PingFang TC", "Microsoft JhengHei", system-ui, sans-serif',
}

const t = tokens

const ALERT_TONES = {
  success: { backgroundColor: '#EEF2EA', borderColor: '#D3DECB', color: '#3E5539' },
  info: { backgroundColor: '#ECEFF4', borderColor: '#D4DAE5', color: t.indigoDark },
  warning: { backgroundColor: '#F6EFE2', borderColor: '#E8D7B8', color: '#71501F' },
  error: { backgroundColor: '#F5E9E4', borderColor: '#E7CFC6', color: '#7A3A28' },
}

export const theme = createTheme({
  palette: {
    mode: 'light',
    primary: { main: t.indigo, dark: t.indigoDark, contrastText: '#fff' },
    secondary: { main: t.matcha, contrastText: '#fff' },
    success: { main: '#5E7B5A' },
    warning: { main: t.ochre },
    error: { main: t.brick },
    info: { main: t.indigo },
    text: { primary: t.ink, secondary: t.inkSoft, disabled: t.inkMuted },
    divider: t.line,
    background: { default: t.washi, paper: t.paper },
  },
  shape: { borderRadius: 4 },
  typography: {
    fontFamily: t.sans,
    h1: { fontFamily: t.serif, fontWeight: 500 },
    h2: { fontFamily: t.serif, fontWeight: 500 },
    h3: { fontFamily: t.serif, fontWeight: 500, fontSize: '2rem', letterSpacing: '0.06em' },
    h4: { fontFamily: t.serif, fontWeight: 500, fontSize: '1.75rem', letterSpacing: '0.06em' },
    h5: { fontFamily: t.serif, fontWeight: 500, fontSize: '1.5rem', letterSpacing: '0.06em' },
    h6: { fontFamily: t.serif, fontWeight: 500, fontSize: '1.125rem', letterSpacing: '0.04em' },
    subtitle1: { fontFamily: t.serif, fontWeight: 500, fontSize: '1.05rem', letterSpacing: '0.04em' },
    subtitle2: { fontWeight: 500, letterSpacing: '0.04em' },
    body1: { lineHeight: 1.8, letterSpacing: '0.02em' },
    body2: { lineHeight: 1.75, letterSpacing: '0.02em' },
    caption: { letterSpacing: '0.04em', color: t.inkSoft },
    overline: { letterSpacing: '0.24em', fontSize: '0.7rem', color: t.inkMuted, lineHeight: 2 },
    button: { textTransform: 'none', fontWeight: 500, letterSpacing: '0.08em' },
  },
  components: {
    MuiCssBaseline: {
      styleOverrides: {
        body: { backgroundColor: t.washi, WebkitFontSmoothing: 'antialiased' },
        '::selection': { backgroundColor: alpha(t.indigo, 0.18) },
        // 鍵盤操作時一定看得到焦點（滑鼠點擊不顯示）
        ':focus-visible': { outline: `2px solid ${alpha(t.indigo, 0.6)}`, outlineOffset: 2 },
        // 列表依序淡入；只用在每頁的主要清單，並尊重「減少動態」設定
        // 起點保留 40% 不透明：動畫被暫停（例如背景分頁）時內容仍看得到，不依賴動畫完成
        '@keyframes rise-in': { from: { opacity: 0.4, transform: 'translateY(8px)' }, to: { opacity: 1, transform: 'none' } },
        '.rise-in > *': { animation: 'rise-in 360ms cubic-bezier(0.2, 0.8, 0.2, 1) both' },
        ...Object.fromEntries(Array.from({ length: 12 }, (_, i) => [`.rise-in > *:nth-of-type(${i + 1})`, { animationDelay: `${i * 50}ms` }])),
        '@media (prefers-reduced-motion: reduce)': { '.rise-in > *': { animation: 'none' } },
      },
    },
    MuiPaper: {
      defaultProps: { elevation: 0 },
      styleOverrides: {
        root: { backgroundImage: 'none' },
        outlined: { borderColor: t.line },
      },
    },
    MuiCard: {
      defaultProps: { variant: 'outlined' },
      styleOverrides: {
        root: {
          borderColor: t.line, transition: 'border-color .2s, transform .2s, box-shadow .2s',
          // 可點擊的卡片：滑鼠移上去時邊框加深、微微浮起
          '&:has(.MuiCardActionArea-root):hover': { borderColor: t.lineStrong, transform: 'translateY(-1px)',
            boxShadow: '0 6px 18px rgba(63,58,52,0.06)' },
          '@media (prefers-reduced-motion: reduce)': { '&:has(.MuiCardActionArea-root):hover': { transform: 'none' } },
        },
      },
    },
    MuiCardActionArea: {
      styleOverrides: {
        root: { '&:hover .MuiCardActionArea-focusHighlight': { opacity: 0.03 } },
      },
    },
    MuiCardContent: { styleOverrides: { root: { padding: 20, '&:last-child': { paddingBottom: 20 } } } },
    MuiButton: {
      defaultProps: { disableElevation: true },
      styleOverrides: {
        root: { borderRadius: 4, padding: '7px 18px' },
        sizeLarge: { padding: '11px 28px', fontSize: '1rem' },
        sizeSmall: { padding: '4px 12px' },
        outlined: ({ ownerState }) => (ownerState.color === 'primary' || !ownerState.color
          ? { borderColor: alpha(t.indigo, 0.5), '&:hover': { borderColor: t.indigo, backgroundColor: alpha(t.indigo, 0.04) } }
          : { borderColor: t.lineStrong, '&:hover': { borderColor: t.ink, backgroundColor: alpha(t.ink, 0.03) } }),
        text: { '&:hover': { backgroundColor: alpha(t.ink, 0.04) } },
      },
    },
    // 觸控目標至少 44×44（小尺寸圖示按鈕也一樣）
    MuiIconButton: { styleOverrides: { root: { borderRadius: 4, minWidth: 44, minHeight: 44 } } },
    MuiChip: {
      styleOverrides: {
        root: { borderRadius: 3, fontWeight: 500, letterSpacing: '0.04em' },
        sizeSmall: { height: 24, fontSize: '0.75rem' },
        filled: ({ ownerState }) => (ownerState.color === 'default' || !ownerState.color
          ? { backgroundColor: t.sand, color: t.inkSoft } : {}),
        outlined: { backgroundColor: t.paper },
      },
    },
    MuiAlert: {
      styleOverrides: {
        root: ({ ownerState }) => ({
          borderRadius: 4, border: '1px solid', alignItems: 'flex-start', padding: '8px 16px',
          ...(ownerState.variant === 'standard' || !ownerState.variant ? ALERT_TONES[ownerState.severity ?? 'success'] : {}),
        }),
        message: { lineHeight: 1.75 },
      },
    },
    MuiOutlinedInput: {
      styleOverrides: {
        root: {
          backgroundColor: '#FFFDF9',
          '& .MuiOutlinedInput-notchedOutline': { borderColor: t.lineStrong },
          '&:hover .MuiOutlinedInput-notchedOutline': { borderColor: t.inkSoft },
          '&.Mui-focused .MuiOutlinedInput-notchedOutline': { borderWidth: 1, borderColor: t.indigo },
        },
      },
    },
    MuiInputLabel: { styleOverrides: { root: { letterSpacing: '0.04em' } } },
    MuiFormHelperText: { styleOverrides: { root: { marginLeft: 2, letterSpacing: '0.02em' } } },
    MuiTableCell: {
      styleOverrides: {
        root: { borderColor: t.line, padding: '10px 12px' },
        head: { color: t.inkMuted, fontSize: '0.75rem', fontWeight: 500, letterSpacing: '0.12em', whiteSpace: 'nowrap' },
      },
    },
    MuiTabs: {
      styleOverrides: { indicator: { height: 1.5, backgroundColor: t.ink } },
    },
    MuiTab: {
      styleOverrides: {
        root: { letterSpacing: '0.12em', minWidth: 0, paddingInline: 20, '&.Mui-selected': { color: t.ink } },
      },
    },
    MuiStepIcon: {
      styleOverrides: {
        root: {
          color: t.sand, '& .MuiStepIcon-text': { fill: t.inkSoft, fontWeight: 500 },
          '&.Mui-active': { color: t.indigo, '& .MuiStepIcon-text': { fill: '#fff' } },
          '&.Mui-completed': { color: t.matcha },
        },
      },
    },
    MuiStepLabel: { styleOverrides: { label: { letterSpacing: '0.06em', fontSize: '0.8rem' } } },
    MuiDialog: { styleOverrides: { paper: { borderRadius: 6, border: `1px solid ${t.line}` } } },
    MuiDialogTitle: { styleOverrides: { root: { fontFamily: t.serif, fontWeight: 500, letterSpacing: '0.06em' } } },
    MuiBackdrop: { styleOverrides: { root: { '&:not(.MuiBackdrop-invisible)': { backgroundColor: alpha(t.ink, 0.35) } } } },
    MuiLinearProgress: {
      styleOverrides: { root: { height: 2, borderRadius: 1, backgroundColor: t.sand } },
    },
    MuiDivider: { styleOverrides: { root: { borderColor: t.line } } },
    MuiMenu: { styleOverrides: { paper: { border: `1px solid ${t.line}`, boxShadow: '0 8px 24px rgba(63,58,52,0.08)' } } },
    MuiAutocomplete: { styleOverrides: { paper: { border: `1px solid ${t.line}`, boxShadow: '0 8px 24px rgba(63,58,52,0.08)' } } },
    MuiTooltip: {
      styleOverrides: { tooltip: { backgroundColor: t.ink, fontSize: '0.75rem', letterSpacing: '0.04em' } },
    },
    MuiLink: { defaultProps: { underline: 'hover' }, styleOverrides: { root: { color: t.indigo } } },
  },
})
