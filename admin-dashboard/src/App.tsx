import { lazy, Suspense, useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { Alert, Box, Button, CircularProgress, Container, CssBaseline, Paper, Stack, TextField, ThemeProvider, Typography } from '@mui/material'
import { QueryClient, QueryClientProvider, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, errorMessage, errorStatus } from './api/client'
import { theme } from './theme'

const ManagementPage = lazy(() => import('./pages/ManagementPage'))
type Identity = { user: { id: string; email: string } }
const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: true }, mutations: { retry: false } } })

function Login({ signedIn }: { signedIn: (identity: Identity) => void }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const login = useMutation({
    mutationFn: async () => (await api.post<Identity>('/auth/login', { email: email.trim(), password })).data,
    onSuccess: signedIn,
  })
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!login.isPending) login.mutate()
  }
  return <Container maxWidth="xs" sx={{ py: { xs: 5, sm: 10 } }}>
    <Paper variant="outlined" sx={{ p: 3 }}>
      <Stack component="form" onSubmit={submit} spacing={3}>
        <Box>
          <Typography variant="h5" component="h1">管理員登入</Typography>
          <Typography color="text.secondary" sx={{ mt: 1 }}>使用已獲授權的帳號進入資料管理。</Typography>
        </Box>
        {login.isError && <Alert severity="error">{errorMessage(login.error, '登入失敗，請稍後重試。')}</Alert>}
        <TextField label="Email" type="email" autoComplete="username" required value={email}
          onChange={(event) => setEmail(event.target.value)} slotProps={{ htmlInput: { maxLength: 254 } }} />
        <TextField label="密碼" type="password" autoComplete="current-password" required value={password}
          onChange={(event) => setPassword(event.target.value)} slotProps={{ htmlInput: { maxLength: 128 } }} />
        <Button type="submit" variant="contained" disabled={login.isPending}>{login.isPending ? '登入中…' : '登入管理網站'}</Button>
      </Stack>
    </Paper>
  </Container>
}

function Session() {
  const client = useQueryClient()
  const me = useQuery({
    queryKey: ['admin-me'], refetchInterval: 60_000,
    queryFn: async ({ signal }) => {
      try { return (await api.get<Identity>('/auth/me', { signal })).data }
      catch (error) { if (errorStatus(error) === 401) return null; throw error }
    },
  })
  useEffect(() => {
    const interceptor = api.interceptors.response.use(undefined, (error: unknown) => {
      if (errorStatus(error) === 401) {
        // Unmount editors and discard cached data when a session expires.
        void client.cancelQueries()
        client.removeQueries({ queryKey: ['management'] })
        client.setQueryData(['admin-me'], null)
      }
      return Promise.reject(error)
    })
    return () => api.interceptors.response.eject(interceptor)
  }, [client])
  const logout = useMutation({
    mutationFn: async () => api.post('/auth/logout'),
    onSuccess: async () => {
      await client.cancelQueries()
      client.removeQueries({ queryKey: ['management'] })
      client.setQueryData(['admin-me'], null)
    },
  })
  if (me.isPending) return <Container sx={{ py: 6 }}><CircularProgress aria-label="確認登入狀態" /></Container>
  if (me.isError) return <Container sx={{ py: 6 }}><Alert severity="error"
    action={<Button onClick={() => void me.refetch()}>重試</Button>}>無法確認登入狀態，請稍後重試。</Alert></Container>
  if (!me.data) return <Login signedIn={(identity) => {
    client.removeQueries({ queryKey: ['management'] })
    client.setQueryData(['admin-me'], identity)
  }} />
  return <>
    <Box component="header" sx={{ borderBottom: 1, borderColor: 'divider', bgcolor: 'background.paper' }}>
      <Container maxWidth="xl" sx={{ py: 2 }}>
        <Stack direction="row" spacing={2} sx={{ alignItems: 'center', flexWrap: 'wrap', rowGap: 1 }}>
          <Typography sx={{ flexGrow: 1, fontWeight: 700 }}>Overcooked 管理中心</Typography>
          <Typography variant="body2" sx={{ overflowWrap: 'anywhere' }}>{me.data.user.email}</Typography>
          <Button onClick={() => logout.mutate()} disabled={logout.isPending}>登出</Button>
        </Stack>
      </Container>
    </Box>
    <Container maxWidth="xl" component="main" sx={{ py: { xs: 3, sm: 5 } }}>
      {logout.isError && <Alert severity="error" sx={{ mb: 2 }}>登出未完成，請重試。</Alert>}
      <Suspense fallback={<CircularProgress aria-label="載入管理功能" />}><ManagementPage /></Suspense>
    </Container>
  </>
}

export default function App() {
  return <ThemeProvider theme={theme}><CssBaseline /><QueryClientProvider client={queryClient}><Session /></QueryClientProvider></ThemeProvider>
}
