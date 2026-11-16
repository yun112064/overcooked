import { zodResolver } from '@hookform/resolvers/zod'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { useNavigate } from 'react-router'
import { z } from 'zod'
import { api, errorMessage } from '../../api/client'
import type { Me } from '../../api/types'
import cardArt from '../../assets/rooms/door/nameplate-card.svg'
import z0 from '../../assets/rooms/door/z0.svg'
import z1 from '../../assets/rooms/door/z1.svg'
import z2 from '../../assets/rooms/door/z2.svg'
import z3 from '../../assets/rooms/door/z3.svg'
import { Placed, Room } from '../../camera/Room'
import type { Stop } from '../../camera/framing'
import { afterLogin } from '../../session/session'
import { Mascot } from '../../shell/Mascot'
import styles from './DoorScene.module.css'

const LAYERS = [z0, z1, z2, z3]
// 手機直式時把鏡頭拉近到門上的名牌卡，表單才夠大
const STOPS: Stop[] = [{ id: 'door', label: '大門', fx: 0.5, fy: 0.56, zoom: 1.2 }]

const schema = z.object({
  email: z.string().trim().min(1, '請輸入 Email').email('Email 格式不正確'),
  password: z.string().min(8, '密碼至少 8 個字元').max(128, '密碼最多 128 個字元'),
})
type FormValues = z.infer<typeof schema>
type Mode = 'login' | 'register'

/** 大門：門上的名牌卡就是登入表單，翻面是註冊（DESIGN.md 5.0）。 */
export default function DoorScene() {
  const [mode, setMode] = useState<Mode>('login')
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { register, handleSubmit, formState: { errors } } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { email: '', password: '' },
  })

  const submit = useMutation({
    mutationFn: async (values: FormValues) =>
      (await api.post<Me>(mode === 'login' ? '/auth/login' : '/auth/register', values)).data,
    onSuccess: (me) => {
      afterLogin(queryClient, me)
      navigate(me.current_household ? '/today' : '/households', { replace: true })
    },
  })

  const switchMode = (next: Mode) => {
    setMode(next)
    submit.reset()
  }

  return (
    <Room name="大門" layers={LAYERS} stops={STOPS} hud={<Mascot message="開門後就能問我" />}>
      <Placed
        x={800} y={318} k={0.7}
        className={`${styles.card} ${mode === 'register' ? styles.back : ''} ${submit.isError ? styles.shake : ''}`}
        style={{ backgroundImage: `url("${cardArt}")` }}
      >
        <form className={styles.form} noValidate onSubmit={handleSubmit((values) => submit.mutate(values))}>
          <h1 className={styles.title}>{mode === 'login' ? '今天吃啥' : '掛上新名牌'}</h1>
          <p className={styles.lead}>{mode === 'login' ? '登入後回家看看今天吃什麼。' : '用 Email 註冊，馬上建立你的家。'}</p>

          {submit.isError && <p className={styles.alert} role="alert">{errorMessage(submit.error)}</p>}

          <label className={styles.field}>
            <span>Email</span>
            <input
              type="email" autoComplete="email" inputMode="email" aria-invalid={!!errors.email}
              aria-describedby={errors.email ? 'door-email-error' : undefined} {...register('email')}
            />
            {errors.email && <em id="door-email-error">{errors.email.message}</em>}
          </label>
          <label className={styles.field}>
            <span>密碼</span>
            <input
              type="password" autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
              aria-invalid={!!errors.password}
              aria-describedby={errors.password ? 'door-password-error' : undefined} {...register('password')}
            />
            {errors.password
              ? <em id="door-password-error">{errors.password.message}</em>
              : mode === 'register' && <small>至少 8 個字元</small>}
          </label>

          <button type="submit" className={styles.submit} disabled={submit.isPending}>
            {submit.isPending ? '處理中…' : mode === 'login' ? '開門' : '建立帳號'}
          </button>
          <p className={styles.switch}>
            {mode === 'login' ? '還沒有帳號？' : '已經有帳號了？'}
            <button type="button" onClick={() => switchMode(mode === 'login' ? 'register' : 'login')}>
              {mode === 'login' ? '翻面註冊' : '翻回登入'}
            </button>
          </p>
        </form>
      </Placed>
    </Room>
  )
}
