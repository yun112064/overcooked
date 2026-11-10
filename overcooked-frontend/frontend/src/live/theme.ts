import { alpha, createTheme } from '@mui/material'

/** 沿用原示範版的橘色、奶油白、淡紫；保留既有元件使用的 token 名稱。 */
export const tokens = {
  washi: '#fff8ef', paper: '#ffffff', sand: '#fce8cf',
  line: '#ebddd0', lineStrong: '#d7c9bd',
  ink: '#413d43', inkSoft: '#706761', inkMuted: '#706761',
  indigo: '#b44c0d', indigoDark: '#963d08',
  matcha: '#73549c', ochre: '#8c6428', brick: '#c85f19', amber: '#b7791f',
  indigoTint: '#fce8cf', matchaTint: '#f3eefa', amberTint: '#fff0d8', brickTint: '#fce8cf',
  serif: '"Noto Sans TC", "PingFang TC", "Microsoft JhengHei", sans-serif',
  sans: '"Noto Sans TC", "PingFang TC", "Microsoft JhengHei", sans-serif',
}
const t = tokens

export const theme = createTheme({
  palette: {
    mode: 'light',
    primary: { main: t.indigo, dark: t.indigoDark, contrastText: '#fff' },
    secondary: { main: t.matcha, contrastText: '#fff' },
    success: { main: '#477346' }, warning: { main: t.ochre },
    error: { main: '#a33224' }, info: { main: t.matcha },
    text: { primary: t.ink, secondary: t.inkSoft, disabled: t.inkMuted },
    divider: t.line, background: { default: t.washi, paper: t.paper },
  },
  shape: { borderRadius: 8 },
  typography: {
    fontFamily: t.sans,
    h1: { fontWeight: 700 }, h2: { fontWeight: 700 },
    h3: { fontWeight: 700, fontSize: '2rem', lineHeight: 1.5 },
    h4: { fontWeight: 700, fontSize: '1.75rem', lineHeight: 1.5 },
    h5: { fontWeight: 600, fontSize: '1.4rem' },
    h6: { fontWeight: 600, fontSize: '1.125rem' },
    subtitle1: { fontWeight: 600, fontSize: '1.05rem' },
    subtitle2: { fontWeight: 600 },
    body1: { lineHeight: 1.8 }, body2: { lineHeight: 1.8 },
    caption: { color: t.inkSoft, lineHeight: 1.7 },
    overline: { fontSize: '0.72rem', color: t.inkMuted, letterSpacing: '0.1em', lineHeight: 2 },
    button: { textTransform: 'none', fontWeight: 600, letterSpacing: '0.02em' },
  },
  components: {
    MuiCssBaseline: { styleOverrides: {
      body: { minWidth: 320, backgroundColor: t.washi, WebkitFontSmoothing: 'antialiased' },
      '::selection': { backgroundColor: alpha(t.matcha, 0.18) },
      ':focus-visible': { outline: `3px solid ${t.matcha}`, outlineOffset: 3 },
      '@keyframes rise-in': { from: { opacity: 0.4, transform: 'translateY(8px)' }, to: { opacity: 1, transform: 'none' } },
      '.rise-in > *': { animation: 'rise-in 300ms ease-out both' },
      '@media (prefers-reduced-motion: reduce)': { '.rise-in > *': { animation: 'none' } },
    } },
    MuiPaper: { defaultProps: { elevation: 0 }, styleOverrides: {
      root: { backgroundImage: 'none', borderRadius: 14 }, outlined: { borderColor: t.line },
    } },
    MuiCard: { defaultProps: { variant: 'outlined' }, styleOverrides: { root: {
      borderRadius: 14, borderColor: t.line, transition: 'border-color .2s, box-shadow .2s',
      '&:has(.MuiCardActionArea-root):hover': { borderColor: t.lineStrong, boxShadow: '0 6px 20px rgba(119,77,34,.06)' },
    } } },
    MuiCardContent: { styleOverrides: { root: { padding: 24, '&:last-child': { paddingBottom: 24 } } } },
    MuiButton: { defaultProps: { disableElevation: true }, styleOverrides: {
      root: { borderRadius: 8, minHeight: 43, padding: '9px 18px' },
      sizeLarge: { padding: '12px 24px', fontSize: '0.95rem' },
      sizeSmall: { minHeight: 36, padding: '5px 12px' },
      outlined: { borderColor: t.lineStrong },
    } },
    MuiIconButton: { styleOverrides: { root: { borderRadius: 8, minWidth: 44, minHeight: 44 } } },
    MuiChip: { styleOverrides: { root: { borderRadius: 7, fontWeight: 500 }, filled: { backgroundColor: t.matchaTint } } },
    MuiAlert: { styleOverrides: { root: { borderRadius: 10, border: '1px solid', borderColor: 'transparent', alignItems: 'flex-start' } } },
    MuiOutlinedInput: { styleOverrides: {
      root: { backgroundColor: '#fffdfa', '& .MuiOutlinedInput-notchedOutline': { borderColor: t.lineStrong } },
    } },
    MuiTableCell: { styleOverrides: {
      root: { borderColor: t.line, padding: '12px 14px' },
      head: { backgroundColor: '#fffdfa', color: t.inkSoft, fontSize: '0.78rem', fontWeight: 600, whiteSpace: 'nowrap' },
    } },
    MuiTabs: { styleOverrides: { indicator: { height: 3, borderRadius: 3, backgroundColor: t.matcha } } },
    MuiTab: { styleOverrides: { root: { minWidth: 0, paddingInline: 20, '&.Mui-selected': { color: t.matcha } } } },
    MuiStepIcon: { styleOverrides: { root: { '&.Mui-active': { color: t.indigo }, '&.Mui-completed': { color: t.matcha } } } },
    MuiDialog: { styleOverrides: { paper: { borderRadius: 16 } } },
    MuiDialogTitle: { styleOverrides: { root: { fontWeight: 600 } } },
    MuiLinearProgress: { styleOverrides: { root: { height: 5, borderRadius: 4, backgroundColor: t.matchaTint } } },
    MuiMenu: { styleOverrides: { paper: { border: `1px solid ${t.line}`, boxShadow: '0 8px 24px rgba(65,61,67,.08)' } } },
    MuiTooltip: { styleOverrides: { tooltip: { backgroundColor: t.ink } } },
    MuiLink: { defaultProps: { underline: 'hover' } },
  },
})
