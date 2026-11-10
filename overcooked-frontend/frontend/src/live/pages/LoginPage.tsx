import { zodResolver } from '@hookform/resolvers/zod'
import { Alert, Box, Button, IconButton, InputAdornment, Stack, Tab, Tabs, TextField, Typography } from '@mui/material'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ArrowRight, Eye, EyeOff } from 'lucide-react'
import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { useNavigate } from 'react-router'
import { z } from 'zod'
import { api, errorMessage } from '../api/client'
import type { Me } from '../api/types'
import { BrandMark } from '../components/ui'
import { tokens } from '../theme'

const schema = z.object({
  email: z.string().trim().min(1, '請輸入 Email').email('Email 格式不正確'),
  password: z.string().min(8, '密碼至少 8 個字元').max(128, '密碼最多 128 個字元'),
  confirmation: z.string(),
})
type FormValues = z.infer<typeof schema>

export default function LoginPage() {
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [visible, setVisible] = useState(false)
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { register, handleSubmit, setError, formState: { errors } } = useForm<FormValues>({
    resolver: zodResolver(schema), defaultValues: { email: '', password: '', confirmation: '' },
  })
  const mutation = useMutation({
    mutationFn: async ({ email, password }: FormValues) =>
      (await api.post<Me>(mode === 'login' ? '/auth/login' : '/auth/register', { email, password })).data,
    onSuccess: (me) => {
      queryClient.setQueryData(['me'], me)
      navigate(me.current_household ? '/home' : '/households', { replace: true })
    },
  })
  const submit = (values: FormValues) => {
    if (mode === 'register' && values.password !== values.confirmation) {
      setError('confirmation', { message: '兩次密碼不一致。' })
      return
    }
    mutation.mutate(values)
  }
  return (
    <Box className="live-auth">
      <Box className="live-auth-story">
        <Box className="live-brand"><BrandMark size={37} />今天吃啥</Box>
        <Box className="live-auth-copy">
          <Typography component="h1">安排餐食，輕輕鬆鬆</Typography>
          <Typography>點一點、按一按，找到適合的料理。</Typography>
        </Box>
        <Box component="img" className="live-auth-produce" src={`${import.meta.env.BASE_URL}produce-hero.webp`}
          alt="新鮮番茄、花椰菜、紅蘿蔔與彩椒" />
      </Box>
      <Box className="live-auth-form">
        <Box sx={{ width: '100%', maxWidth: 390 }}>
          <Tabs value={mode} onChange={(_, value) => { setMode(value); mutation.reset() }}
            aria-label="登入與註冊" variant="fullWidth" sx={{ borderBottom: `1px solid ${tokens.line}`, mb: 4 }}>
            <Tab value="login" label="登入" id="login-tab" aria-controls="login-form" disabled={mutation.isPending} />
            <Tab value="register" label="註冊" id="register-tab" aria-controls="login-form" disabled={mutation.isPending} />
          </Tabs>
          <Typography variant="h4" component="h2">{mode === 'login' ? '歡迎回來' : '建立你的帳號'}</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1, mb: 3 }}>
            {mode === 'login' ? '登入後，繼續為全家準備下一餐。' : '建立帳號，開始規劃全家的餐食。'}
          </Typography>
          <Stack id="login-form" role="tabpanel" aria-labelledby={mode === 'login' ? 'login-tab' : 'register-tab'}
            component="form" spacing={2.5} noValidate onSubmit={handleSubmit(submit)}>
            {mutation.isError && <Alert severity="error">{errorMessage(mutation.error)}</Alert>}
            <TextField label="Email" type="email" autoComplete="email" fullWidth disabled={mutation.isPending}
              {...register('email')} error={!!errors.email} helperText={errors.email?.message} />
            <TextField label="密碼" type={visible ? 'text' : 'password'} fullWidth disabled={mutation.isPending}
              autoComplete={mode === 'login' ? 'current-password' : 'new-password'} {...register('password')}
              error={!!errors.password} helperText={errors.password?.message ?? (mode === 'register' ? '至少 8 個字元' : undefined)}
              slotProps={{ input: { endAdornment: <InputAdornment position="end"><IconButton type="button"
                aria-label={visible ? '隱藏密碼' : '顯示密碼'} onClick={() => setVisible(!visible)} edge="end">
                {visible ? <EyeOff size={19} /> : <Eye size={19} />}
              </IconButton></InputAdornment> } }} />
            {mode === 'register' && <TextField label="確認密碼" type={visible ? 'text' : 'password'}
              autoComplete="new-password" fullWidth disabled={mutation.isPending} {...register('confirmation')}
              error={!!errors.confirmation} helperText={errors.confirmation?.message} />}
            <Button type="submit" variant="contained" size="large" disabled={mutation.isPending} endIcon={<ArrowRight size={18} />}>
              {mutation.isPending ? '處理中…' : mode === 'login' ? '登入' : '建立帳號'}
            </Button>
          </Stack>
        </Box>
      </Box>
    </Box>
  )
}
