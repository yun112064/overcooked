import { zodResolver } from '@hookform/resolvers/zod'
import { Alert, Box, Button, Stack, Tab, Tabs, TextField, Typography } from '@mui/material'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { useNavigate } from 'react-router'
import { z } from 'zod'
import { api, errorMessage } from '../api/client'
import type { Me } from '../api/types'
import { BrandMark } from '../components/ui'
import { tokens } from '../theme'

const FEATURES = [
  ['依成員算營養', '每位家人的年齡、活動量與目標，合成這一餐該吃多少。'],
  ['用家裡有的食材', '拍照或勾選冰箱裡的食材，只補買缺的部分。'],
  ['守住預算', '依全聯、萬家福的實際售價估算成本，超出預算的菜單不能採用。'],
]

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
      navigate(me.current_household ? '/plan' : '/households', { replace: true })
    },
  })

  return (
    <Box sx={{ minHeight: '100vh', display: 'grid', gridTemplateColumns: { xs: '1fr', md: '1.1fr 1fr' } }}>
      <Box sx={{
        display: { xs: 'none', md: 'flex' }, flexDirection: 'column', justifyContent: 'space-between',
        bgcolor: tokens.sand, px: 8, py: 7, borderRight: `1px solid ${tokens.line}`,
      }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
          <BrandMark size={36} />
          <Typography sx={{ fontFamily: tokens.serif, fontSize: '1.25rem', letterSpacing: '0.2em' }}>今天吃啥</Typography>
        </Box>
        <Box sx={{ maxWidth: 440 }}>
          <Typography variant="overline" component="p">Family meal planner</Typography>
          <Typography component="p" sx={{ fontFamily: tokens.serif, fontSize: '2.4rem', lineHeight: 1.5, letterSpacing: '0.1em', mt: 1 }}>
            一家人的每一餐，<br />剛剛好。
          </Typography>
          <Stack spacing={3} sx={{ mt: 6 }}>
            {FEATURES.map(([title, body], i) => (
              <Box key={title} sx={{ display: 'flex', gap: 2.5 }}>
                <Typography sx={{ fontFamily: tokens.serif, color: tokens.brick, fontSize: '0.95rem', pt: 0.25 }}>
                  {String(i + 1).padStart(2, '0')}
                </Typography>
                <Box>
                  <Typography sx={{ fontWeight: 500, letterSpacing: '0.08em' }}>{title}</Typography>
                  <Typography variant="body2" color="text.secondary">{body}</Typography>
                </Box>
              </Box>
            ))}
          </Stack>
        </Box>
        <Typography variant="caption" sx={{ color: tokens.inkMuted }}>營養資料：衛福部食藥署食品營養成分資料庫</Typography>
      </Box>

      <Box sx={{ display: 'grid', placeItems: 'center', px: 3, py: 6 }}>
        <Box sx={{ width: '100%', maxWidth: 380 }}>
          <Box sx={{ display: { xs: 'flex', md: 'none' }, alignItems: 'center', gap: 1.5, mb: 5 }}>
            <BrandMark size={32} />
            <Typography sx={{ fontFamily: tokens.serif, fontSize: '1.2rem', letterSpacing: '0.2em' }}>今天吃啥</Typography>
          </Box>
          <Typography variant="h4" component="h1">{mode === 'login' ? '歡迎回來' : '建立帳號'}</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1, mb: 3 }}>
            {mode === 'login' ? '登入後繼續規劃你家的下一餐。' : '用 Email 註冊，馬上開始建立家庭與成員。'}
          </Typography>
          <Tabs value={mode} onChange={(_, v) => { setMode(v); mutation.reset() }}
            sx={{ mb: 3, borderBottom: `1px solid ${tokens.line}` }}>
            <Tab value="login" label="登入" />
            <Tab value="register" label="註冊" />
          </Tabs>
          <Stack component="form" spacing={2.5} noValidate onSubmit={handleSubmit((v) => mutation.mutate(v))}>
            {mutation.isError && <Alert severity="error">{errorMessage(mutation.error)}</Alert>}
            <TextField
              label="Email" type="email" autoComplete="email" fullWidth
              {...register('email')}
              error={!!errors.email} helperText={errors.email?.message}
            />
            <TextField
              label="密碼" type="password" fullWidth
              autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
              {...register('password')}
              error={!!errors.password}
              helperText={errors.password?.message ?? (mode === 'register' ? '至少 8 個字元' : undefined)}
            />
            <Button type="submit" variant="contained" size="large" disabled={mutation.isPending} sx={{ mt: 1 }}>
              {mutation.isPending ? '處理中…' : mode === 'login' ? '登入' : '建立帳號'}
            </Button>
          </Stack>
        </Box>
      </Box>
    </Box>
  )
}
