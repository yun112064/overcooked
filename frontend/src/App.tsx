import EventNoteOutlined from '@mui/icons-material/EventNoteOutlined'
import GroupOutlined from '@mui/icons-material/GroupOutlined'
import HistoryEduOutlined from '@mui/icons-material/HistoryEduOutlined'
import HomeOutlined from '@mui/icons-material/HomeOutlined'
import LogoutOutlined from '@mui/icons-material/LogoutOutlined'
import {
  BottomNavigation, BottomNavigationAction, Box, Button, CircularProgress, Container, CssBaseline, IconButton,
  Paper, ThemeProvider, Tooltip, Typography, alpha,
} from '@mui/material'
import { QueryClient, QueryClientProvider, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Link, NavLink, Navigate, Outlet, RouterProvider, createBrowserRouter, useLocation, useNavigate,
} from 'react-router'
import { api, errorStatus } from './api/client'
import type { Me } from './api/types'
import HouseholdPage from './pages/HouseholdPage'
import LoginPage from './pages/LoginPage'
import MealRequestsPage from './pages/MealRequestsPage'
import MembersPage from './pages/MembersPage'
import { Suspense, lazy } from 'react'

// 圖表（Recharts）與問卷編輯較大，用到才載入，首頁更快
const MealHistoryPage = lazy(() => import('./pages/MealHistoryPage'))
const MealRequestEditor = lazy(() => import('./pages/MealRequestEditor'))
import { BrandMark } from './components/ui'
import { theme, tokens } from './theme'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // 未登入（401）不重試，直接導回登入頁
      retry: (count, err) => errorStatus(err) !== 401 && errorStatus(err) !== 403 && count < 2,
      refetchOnWindowFocus: false,
    },
  },
})

function useMe() {
  return useQuery({
    queryKey: ['me'],
    queryFn: async () => {
      try {
        return (await api.get<Me>('/auth/me')).data
      } catch (err) {
        if (errorStatus(err) === 401) return null
        throw err
      }
    },
  })
}

function FullPageSpinner() {
  return <Box sx={{ display: 'grid', placeItems: 'center', minHeight: '60vh' }}><CircularProgress /></Box>
}

/** 需登入；requireHousehold 時另需已選擇家庭。 */
function Protected({ requireHousehold = false }: { requireHousehold?: boolean }) {
  const me = useMe()
  if (me.isPending) return <FullPageSpinner />
  if (me.isError) return <Typography sx={{ p: 3 }} color="text.secondary">無法連線到伺服器，請稍後再試。</Typography>
  if (!me.data) return <Navigate to="/login" replace />
  if (requireHousehold && !me.data.current_household) return <Navigate to="/households" replace />
  return <Outlet />
}

const NAV = [
  { to: '/plan', label: '規劃餐食', short: '規劃', icon: <EventNoteOutlined /> },
  { to: '/history', label: '已採用紀錄', short: '紀錄', icon: <HistoryEduOutlined /> },
  { to: '/members', label: '家庭成員', short: '成員', icon: <GroupOutlined /> },
]

function Layout() {
  const me = useMe()
  const client = useQueryClient()
  const navigate = useNavigate()
  const { pathname } = useLocation()
  const logout = useMutation({
    mutationFn: async () => api.post('/auth/logout'),
    onSettled: () => {
      client.clear()
      navigate('/login', { replace: true })
    },
  })
  const household = me.data?.current_household
  const current = [...NAV, { to: '/households' }].find((n) => pathname.startsWith(n.to))?.to ?? false
  return (
    <Box sx={{ minHeight: '100vh', pb: { xs: household ? 9 : 0, md: 0 } }}>
      <Box component="header" sx={{
        position: 'sticky', top: 0, zIndex: 10, bgcolor: alpha(tokens.washi, 0.92), backdropFilter: 'blur(8px)',
        borderBottom: `1px solid ${tokens.line}`,
      }}>
        <Container maxWidth="lg" sx={{ display: 'flex', alignItems: 'center', gap: 3, height: 64 }}>
          <Box component={Link} to={household ? '/plan' : '/households'}
            sx={{ display: 'flex', alignItems: 'center', gap: 1.25, color: 'inherit', textDecoration: 'none' }}>
            <BrandMark size={30} />
            <Typography sx={{ fontFamily: tokens.serif, fontSize: '1.15rem', fontWeight: 500, letterSpacing: '0.16em' }}>
              今天吃啥
            </Typography>
          </Box>
          {household && (
            <Box component="nav" sx={{ display: { xs: 'none', md: 'flex' }, gap: 0.5, ml: 2 }}>
              {NAV.map((n) => (
                <Button key={n.to} component={NavLink} to={n.to} color="inherit" sx={{
                  color: tokens.inkSoft, borderRadius: 0, px: 1.5, height: 64, letterSpacing: '0.12em',
                  borderBottom: '1.5px solid transparent',
                  '&.active': { color: tokens.ink, borderBottomColor: tokens.ink },
                  '&:hover': { bgcolor: 'transparent', color: tokens.ink },
                }}>
                  {n.label}
                </Button>
              ))}
            </Box>
          )}
          <Box sx={{ flexGrow: 1 }} />
          {household && (
            <Button component={Link} to="/households" color="inherit" startIcon={<HomeOutlined />}
              sx={{ display: { xs: 'none', sm: 'inline-flex' }, color: tokens.inkSoft, maxWidth: 220,
                border: `1px solid ${tokens.line}`, bgcolor: tokens.paper, '&:hover': { bgcolor: tokens.paper, borderColor: tokens.lineStrong } }}>
              <Box component="span" sx={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{household.name}</Box>
            </Button>
          )}
          {me.data && (
            <Tooltip title="登出">
              <IconButton onClick={() => logout.mutate()} disabled={logout.isPending} aria-label="登出"
                sx={{ color: tokens.inkSoft }}>
                <LogoutOutlined fontSize="small" />
              </IconButton>
            </Tooltip>
          )}
        </Container>
      </Box>
      <Container maxWidth="md" component="main" sx={{ py: { xs: 3, sm: 5 } }}>
        <Suspense fallback={<FullPageSpinner />}>
          <Outlet />
        </Suspense>
      </Container>
      {household && (
        <Paper square sx={{
          display: { md: 'none' }, position: 'fixed', bottom: 0, left: 0, right: 0, zIndex: 10,
          borderTop: `1px solid ${tokens.line}`, pb: 'env(safe-area-inset-bottom)',
        }}>
          <BottomNavigation showLabels value={current} sx={{ bgcolor: tokens.paper,
            '& .MuiBottomNavigationAction-root': { color: tokens.inkMuted, minWidth: 0 },
            '& .Mui-selected': { color: tokens.ink },
            '& .MuiBottomNavigationAction-label': { letterSpacing: '0.1em', fontSize: '0.72rem !important', mt: 0.25 } }}>
            {[...NAV, { to: '/households', short: '家庭', icon: <HomeOutlined /> }].map((n) => (
              <BottomNavigationAction key={n.to} value={n.to} label={n.short} icon={n.icon}
                component={Link} to={n.to} />
            ))}
          </BottomNavigation>
        </Paper>
      )}
    </Box>
  )
}

function LoginRoute() {
  const me = useMe()
  if (me.isPending) return <FullPageSpinner />
  if (me.data) return <Navigate to={me.data.current_household ? '/plan' : '/households'} replace />
  return <LoginPage />
}

const router = createBrowserRouter([
  { path: '/login', element: <LoginRoute /> },
  {
    element: <Protected />,
    children: [{
      element: <Layout />,
      children: [
        { path: '/households', element: <HouseholdPage /> },
        {
          element: <Protected requireHousehold />,
          children: [
            { path: '/members', element: <MembersPage /> },
            { path: '/plan', element: <MealRequestsPage /> },
            { path: '/plan/:id', element: <MealRequestEditor /> },
            { path: '/history', element: <MealHistoryPage /> },
          ],
        },
      ],
    }],
  },
  { path: '*', element: <Navigate to="/members" replace /> },
])

export default function App() {
  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </ThemeProvider>
  )
}
