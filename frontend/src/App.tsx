import {
  AppBar, Box, Button, CircularProgress, Container, CssBaseline, ThemeProvider, Toolbar, Typography, createTheme,
} from '@mui/material'
import { QueryClient, QueryClientProvider, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, Navigate, Outlet, RouterProvider, createBrowserRouter, useNavigate } from 'react-router'
import { api, errorStatus } from './api/client'
import type { Me } from './api/types'
import HouseholdPage from './pages/HouseholdPage'
import LoginPage from './pages/LoginPage'
import MealRequestEditor from './pages/MealRequestEditor'
import MealRequestsPage from './pages/MealRequestsPage'
import MembersPage from './pages/MembersPage'

const theme = createTheme({ palette: { primary: { main: '#2e7d32' } } })

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
  return (
    <>
      <AppBar position="static" elevation={0}>
        <Toolbar sx={{ gap: 1, flexWrap: 'wrap' }}>
          <Typography variant="h6" component={Link} to="/members" sx={{ color: 'inherit', textDecoration: 'none', flexGrow: 1 }}>
            今天吃啥
          </Typography>
          {me.data?.current_household && (
            <>
              <Button color="inherit" component={Link} to="/plan">規劃餐食</Button>
              <Button color="inherit" component={Link} to="/members">成員</Button>
              <Button color="inherit" component={Link} to="/households">{me.data.current_household.name}</Button>
            </>
          )}
          {me.data && <Button color="inherit" onClick={() => logout.mutate()} disabled={logout.isPending}>登出</Button>}
        </Toolbar>
      </AppBar>
      <Container maxWidth="md" sx={{ py: 3 }}>
        <Outlet />
      </Container>
    </>
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
