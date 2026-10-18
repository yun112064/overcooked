import { zodResolver } from '@hookform/resolvers/zod'
import { Alert, Box, Button, Card, CardContent, Stack, Tab, Tabs, TextField, Typography } from '@mui/material'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { useNavigate } from 'react-router'
import { z } from 'zod'
import { api, errorMessage } from '../api/client'
import type { Me } from '../api/types'

const schema = z.object({
  email: z.string().trim().min(1, '請輸入 Email').email('Email 格式不正確'),
  password: z.string().min(8, '密碼至少 8 個字元').max(128, '密碼最多 128 個字元'),
})
type FormValues = z.infer<typeof schema>

export default function LoginPage() {
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { register, handleSubmit, formState: { errors } } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { email: '', password: '' },
  })

  const mutation = useMutation({
    mutationFn: async (values: FormValues) =>
      (await api.post<Me>(mode === 'login' ? '/auth/login' : '/auth/register', values)).data,
    onSuccess: (me) => {
      queryClient.setQueryData(['me'], me)
      navigate('/households', { replace: true })
    },
  })

  return (
    <Box sx={{ minHeight: '100vh', display: 'grid', placeItems: 'center', p: 2 }}>
      <Card sx={{ width: '100%', maxWidth: 420 }}>
        <CardContent>
          <Typography variant="h5" component="h1" gutterBottom>今天吃啥</Typography>
          <Tabs value={mode} onChange={(_, v) => { setMode(v); mutation.reset() }} sx={{ mb: 2 }}>
            <Tab value="login" label="登入" />
            <Tab value="register" label="註冊" />
          </Tabs>
          <Stack component="form" spacing={2} noValidate onSubmit={handleSubmit((v) => mutation.mutate(v))}>
            {mutation.isError && <Alert severity="error">{errorMessage(mutation.error)}</Alert>}
            <TextField
              label="Email" type="email" autoComplete="email"
              {...register('email')}
              error={!!errors.email} helperText={errors.email?.message}
            />
            <TextField
              label="密碼" type="password"
              autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
              {...register('password')}
              error={!!errors.password}
              helperText={errors.password?.message ?? (mode === 'register' ? '至少 8 個字元' : undefined)}
            />
            <Button type="submit" variant="contained" size="large" disabled={mutation.isPending}>
              {mode === 'login' ? '登入' : '建立帳號'}
            </Button>
          </Stack>
        </CardContent>
      </Card>
    </Box>
  )
}
