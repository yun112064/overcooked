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
  inkMuted: '#958B7D',
  indigo: '#4A5D7E',
  indigoDark: '#36465F',
  matcha: '#6F7D5C',
  ochre: '#A87B3B',
  brick: '#A4553F',
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
      styleOverrides: { root: { borderColor: t.line, transition: 'border-color .2s' } },
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
    MuiIconButton: { styleOverrides: { root: { borderRadius: 4 } } },
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
