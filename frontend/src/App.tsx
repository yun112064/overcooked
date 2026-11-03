import {
  Box, Button, CircularProgress, Container, CssBaseline, ThemeProvider, Typography, alpha,
} from '@mui/material'
import { QueryClient, QueryClientProvider, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, NavLink, Navigate, Outlet, RouterProvider, createBrowserRouter, useNavigate } from 'react-router'
import { api, errorStatus } from './api/client'
import type { Me } from './api/types'
import HouseholdPage from './pages/HouseholdPage'
import LoginPage from './pages/LoginPage'
import MealHistoryPage from './pages/MealHistoryPage'
import MealRequestEditor from './pages/MealRequestEditor'
import MealRequestsPage from './pages/MealRequestsPage'
import MembersPage from './pages/MembersPage'
import { BrandMark } from './components/ui'
import { theme, tokens } from './theme'

const NAV = [{ to: '/plan', label: '規劃餐食' }, { to: '/history', label: '已採用紀錄' }, { to: '/members', label: '家庭成員' }]

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
  if (me.isError) return <Typography sx={{ p: 3 }}>無法連線到伺服器，請稍後再試。</Typography>
  if (!me.data) return <Navigate to="/login" replace />
  if (requireHousehold && !me.data.current_household) return <Navigate to="/households" replace />
  return <Outlet />
}

function Layout() {
  const me = useMe()
  const client = useQueryClient()
  const navigate = useNavigate()
  const logout = useMutation({
    mutationFn: async () => api.post('/auth/logout'),
    onSettled: () => {
      client.clear()
      navigate('/login', { replace: true })
    },
  })
  const household = me.data?.current_household
  // 版面取自 t11-t20-demo：半透明和紙色頁首、明體品牌字、底線標示目前頁面
  return (
    <Box sx={{ minHeight: '100vh' }}>
      <Box component="header" sx={{
        position: 'sticky', top: 0, zIndex: 10, bgcolor: alpha(tokens.washi, 0.92), backdropFilter: 'blur(8px)',
        borderBottom: `1px solid ${tokens.line}`,
      }}>
        <Container maxWidth="lg" sx={{ display: 'flex', alignItems: 'center', gap: { xs: 1, sm: 3 }, minHeight: 64, flexWrap: 'wrap' }}>
          <Box component={Link} to={household ? '/plan' : '/households'}
            sx={{ display: 'flex', alignItems: 'center', gap: 1.25, color: 'inherit', textDecoration: 'none' }}>
            <BrandMark size={30} />
            <Typography sx={{ fontFamily: tokens.serif, fontSize: '1.15rem', fontWeight: 500, letterSpacing: '0.16em' }}>
              今天吃啥
            </Typography>
          </Box>
          {household && (
            <Box component="nav" sx={{ display: 'flex', gap: 0.5 }}>
              {NAV.map((n) => (
                <Button key={n.to} component={NavLink} to={n.to} color="inherit" sx={{
                  color: tokens.inkSoft, borderRadius: 0, px: 1.5, minHeight: 64, letterSpacing: '0.12em',
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
            <Button component={Link} to="/households" color="inherit"
              sx={{ color: tokens.inkSoft, maxWidth: 220, border: `1px solid ${tokens.line}`, bgcolor: tokens.paper,
                '&:hover': { bgcolor: tokens.paper, borderColor: tokens.lineStrong } }}>
              <Box component="span" sx={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{household.name}</Box>
            </Button>
          )}
          {me.data && (
            <Button color="inherit" onClick={() => logout.mutate()} disabled={logout.isPending} sx={{ color: tokens.inkSoft }}>
              登出
            </Button>
          )}
        </Container>
      </Box>
      <Container maxWidth="md" component="main" sx={{ py: { xs: 3, sm: 5 } }}>
        <Outlet />
      </Container>
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
