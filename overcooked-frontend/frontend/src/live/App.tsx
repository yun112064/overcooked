import ChatBubbleOutlineOutlined from '@mui/icons-material/ChatBubbleOutlineOutlined'
import EventNoteOutlined from '@mui/icons-material/EventNoteOutlined'
import GroupOutlined from '@mui/icons-material/GroupOutlined'
import HistoryEduOutlined from '@mui/icons-material/HistoryEduOutlined'
import HomeOutlined from '@mui/icons-material/HomeOutlined'
import LogoutOutlined from '@mui/icons-material/LogoutOutlined'
import MenuBookOutlined from '@mui/icons-material/MenuBookOutlined'
import {
  Box, Button, CircularProgress, Container, CssBaseline, IconButton, ThemeProvider, Tooltip, Typography,
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
import HomePage from './pages/HomePage'
import './live.css'
import { Suspense, lazy } from 'react'

// 圖表（Recharts）與問卷編輯較大，用到才載入，首頁更快
const MealHistoryPage = lazy(() => import('./pages/MealHistoryPage'))
const GenerationHistoryPage = lazy(() => import('./pages/GenerationHistoryPage'))
const MealRequestEditor = lazy(() => import('./pages/MealRequestEditor'))
const RecipePickerPage = lazy(() => import('./pages/RecipePickerPage'))
const AssistantPage = lazy(() => import('./pages/AssistantPage'))
import AssistantChatProvider from './assistant/AssistantChatProvider'
import FloatingAssistant from './assistant/FloatingAssistant'
import { BrandMark } from './components/ui'
import { theme } from './theme'

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
  { to: '/home', label: '首頁', short: '首頁', icon: <HomeOutlined /> },
  { to: '/plan', label: '規劃餐食', short: '規劃', icon: <EventNoteOutlined /> },
  { to: '/pick', label: '食譜庫選菜', short: '選菜', icon: <MenuBookOutlined /> },
  { to: '/history', label: '已採用紀錄', short: '紀錄', icon: <HistoryEduOutlined /> },
  { to: '/generation-history', label: '營養與採購', short: '採購紀錄', icon: <HistoryEduOutlined /> },
  { to: '/members', label: '家庭成員', short: '成員', icon: <GroupOutlined /> },
  { to: '/assistant', label: '飲食小幫手', short: '問答', icon: <ChatBubbleOutlineOutlined /> },
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
  return (
    <AssistantChatProvider>
      <Box className="live-shell">
        <Box component="header" className="live-header">
          <Box className="live-header-top">
            <Box component={Link} to={household ? '/home' : '/households'} className="live-brand">
              <BrandMark size={37} />今天吃啥
            </Box>
            <Box className="live-account">
              {household && <Button component={Link} to="/households" color="inherit" startIcon={<HomeOutlined />}
                sx={{ maxWidth: { xs: 145, sm: 240 } }}>
                <Box component="span" sx={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{household.name}</Box>
              </Button>}
              {me.data && <Tooltip title="登出"><IconButton onClick={() => logout.mutate()} disabled={logout.isPending} aria-label="登出">
                <LogoutOutlined fontSize="small" />
              </IconButton></Tooltip>}
            </Box>
          </Box>
          {household && <Box component="nav" className="live-nav" aria-label="主要導覽">
            {NAV.map(n => <Button key={n.to} component={NavLink} to={n.to} color="inherit" startIcon={n.icon}>
              {n.label}
            </Button>)}
          </Box>}
        </Box>
        <Container maxWidth="lg" component="main" className="live-main">
          <Suspense fallback={<FullPageSpinner />}><Outlet /></Suspense>
          <Box component="footer" className="live-footer"><span>今天吃啥 · 家庭餐食規劃</span><span>一起安排，好好吃飯。</span></Box>
        </Container>
        {!pathname.startsWith('/assistant') && <FloatingAssistant />}
      </Box>
    </AssistantChatProvider>
  )
}

function LoginRoute() {
  const me = useMe()
  if (me.isPending) return <FullPageSpinner />
  if (me.data) return <Navigate to={me.data.current_household ? '/home' : '/households'} replace />
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
            { path: '/home', element: <HomePage /> },
            { path: '/members', element: <MembersPage /> },
            { path: '/plan', element: <MealRequestsPage /> },
            { path: '/plan/:id', element: <MealRequestEditor /> },
            { path: '/pick', element: <RecipePickerPage /> },
            { path: '/pick/:id', element: <RecipePickerPage /> },
            { path: '/history', element: <MealHistoryPage /> },
            { path: '/generation-history', element: <GenerationHistoryPage /> },
            { path: '/assistant', element: <AssistantPage /> },
          ],
        },
      ],
    }],
  },
  { path: '*', element: <Navigate to="/home" replace /> },
])

export default function App() {
  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      {import.meta.env.VITE_DEMO_ENVIRONMENT === 'docker' && <Box role="status" sx={{
        bgcolor: '#f3eefa', color: '#73549c', px: 3, py: 1, textAlign: 'center', fontSize: 13,
      }}>獨立展示環境 · 帳號、食譜與營養皆為合成示範資料，操作只儲存在展示資料庫。</Box>}
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </ThemeProvider>
  )
}
