import { zodResolver } from '@hookform/resolvers/zod'
import {
  Alert, Button, Card, CardActionArea, CardContent, Chip, CircularProgress, Stack, TextField, Typography,
} from '@mui/material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useForm } from 'react-hook-form'
import { useNavigate } from 'react-router'
import { z } from 'zod'
import { api, errorMessage } from '../api/client'
import type { Household } from '../api/types'

const schema = z.object({ name: z.string().trim().min(1, '請輸入家庭名稱').max(100, '最多 100 個字') })
type FormValues = z.infer<typeof schema>

type HouseholdList = { households: Household[]; current_household_id: string | null }

export default function HouseholdPage() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const list = useQuery({
    queryKey: ['households'],
    queryFn: async () => (await api.get<HouseholdList>('/household')).data,
  })
  const { register, handleSubmit, reset, formState: { errors } } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { name: '' },
  })

  const afterChange = async () => {
    await queryClient.invalidateQueries({ queryKey: ['me'] })
    await queryClient.invalidateQueries({ queryKey: ['households'] })
    queryClient.removeQueries({ queryKey: ['members'] })
    navigate('/members')
  }

  const create = useMutation({
    mutationFn: async (values: FormValues) => (await api.post<Household>('/household', values)).data,
    onSuccess: async () => { reset(); await afterChange() },
  })
  const select = useMutation({
    mutationFn: async (id: string) => (await api.post<Household>('/household/select', { household_id: id })).data,
    onSuccess: afterChange,
  })

  return (
    <Stack spacing={3}>
      <Typography variant="h5" component="h1">選擇家庭</Typography>
      {list.isPending && <CircularProgress />}
      {list.isError && <Alert severity="error">{errorMessage(list.error)}</Alert>}
      {select.isError && <Alert severity="error">{errorMessage(select.error)}</Alert>}
      {list.data && list.data.households.length === 0 && (
        <Typography color="text.secondary">尚未建立家庭，請先在下方建立。</Typography>
      )}
      <Stack spacing={1}>
        {list.data?.households.map((h) => (
          <Card key={h.id} variant="outlined">
            <CardActionArea onClick={() => select.mutate(h.id)} disabled={select.isPending}>
              <CardContent sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                <Typography sx={{ flexGrow: 1 }}>{h.name}</Typography>
                {h.id === list.data.current_household_id && <Chip size="small" color="primary" label="目前家庭" />}
              </CardContent>
            </CardActionArea>
          </Card>
        ))}
      </Stack>

      <Card variant="outlined">
        <CardContent>
          <Typography variant="h6" component="h2" gutterBottom>建立新家庭</Typography>
          <Stack component="form" direction={{ xs: 'column', sm: 'row' }} spacing={2} noValidate
            onSubmit={handleSubmit((v) => create.mutate(v))}>
            <TextField label="家庭名稱" fullWidth {...register('name')}
              error={!!errors.name} helperText={errors.name?.message} />
            <Button type="submit" variant="contained" disabled={create.isPending} sx={{ flexShrink: 0 }}>
              建立
            </Button>
          </Stack>
          {create.isError && <Alert severity="error" sx={{ mt: 2 }}>{errorMessage(create.error)}</Alert>}
        </CardContent>
      </Card>
    </Stack>
  )
}
