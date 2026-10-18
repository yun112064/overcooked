import {
  Alert, Box, Button, Card, CardContent, Chip, CircularProgress, Stack, TextField, Typography,
} from '@mui/material'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { api, errorMessage } from '../api/client'
import type { IngredientRef } from '../api/types'

type RecognitionResult = {
  id: string
  status: 'succeeded' | 'no_result' | 'unavailable'
  message: string
  suggested_name: string | null
  suggested_ingredient_id: string | null
  retryable: boolean
  image_url: string
}

type Props = {
  mealRequestId: string
  onAdd: (ingredient: IngredientRef) => void
}

/** 拍照／上傳 → 最高信心蔬菜名稱回填可編輯文字框 → 選標準食材加入已有食材。 */
export default function RecognitionCapture({ mealRequestId, onAdd }: Props) {
  const [result, setResult] = useState<RecognitionResult | null>(null)
  const [name, setName] = useState('')
  const [query, setQuery] = useState('')

  useEffect(() => {
    const timer = setTimeout(() => setQuery(name.trim()), 250)
    return () => clearTimeout(timer)
  }, [name])

  const showResult = (r: RecognitionResult) => {
    setResult(r)
    setName(r.suggested_name ?? '')
  }

  const upload = useMutation({
    mutationFn: async (file: File) => {
      const form = new FormData()
      form.append('image', file)
      form.append('meal_request_id', mealRequestId)
      return (await api.post<RecognitionResult>('/recognitions', form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })).data
    },
    onSuccess: showResult,
  })
  const retry = useMutation({
    mutationFn: async () => (await api.post<RecognitionResult>(`/recognitions/${result!.id}/retry`)).data,
    onSuccess: showResult,
  })
  const confirm = useMutation({
    mutationFn: async (ingredient: IngredientRef) => {
      await api.post(`/recognitions/${result!.id}/confirm`, { name: name.trim() || ingredient.name, ingredient_id: ingredient.ingredient_id })
      return ingredient
    },
    onSuccess: (ingredient) => {
      onAdd(ingredient)
      setResult(null)
      setName('')
    },
  })
  const discard = useMutation({
    mutationFn: async () => api.delete(`/recognitions/${result!.id}`),
    onSuccess: () => { setResult(null); setName('') },
  })

  const matches = useQuery({
    queryKey: ['ingredients', query],
    queryFn: async () =>
      (await api.get<{ ingredients: IngredientRef[] }>('/ingredients', { params: { q: query, limit: 6 } })).data.ingredients,
    enabled: !!result && query.length > 0,
    staleTime: 60_000,
  })

  return (
    <Card variant="outlined">
      <CardContent>
        <Stack spacing={2}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, flexWrap: 'wrap' }}>
            <Button variant="outlined" component="label" disabled={upload.isPending}>
              拍照或上傳蔬菜照片
              <input hidden type="file" accept="image/jpeg,image/png,image/webp" capture="environment"
                onChange={(e) => {
                  const file = e.target.files?.[0]
                  e.target.value = ''
                  if (file) upload.mutate(file)
                }} />
            </Button>
            {upload.isPending && <CircularProgress size={24} />}
            <Typography variant="body2" color="text.secondary">只確認食材種類，不需要重量</Typography>
          </Box>
          {upload.isError && <Alert severity="error">{errorMessage(upload.error)}</Alert>}

          {result && (
            <Stack spacing={2}>
              <Box sx={{ display: 'flex', gap: 2, alignItems: 'flex-start', flexWrap: 'wrap' }}>
                <Box component="img" src={`/api${result.image_url}`} alt="上傳的照片"
                  sx={{ width: 96, height: 96, objectFit: 'cover', borderRadius: 1, border: 1, borderColor: 'divider' }} />
                <Alert severity={result.status === 'succeeded' ? 'info' : 'warning'} sx={{ flex: 1, minWidth: 200 }}
                  action={result.retryable
                    ? <Button color="inherit" size="small" disabled={retry.isPending} onClick={() => retry.mutate()}>重試</Button>
                    : undefined}>
                  {result.message}
                </Alert>
              </Box>
              {retry.isError && <Alert severity="error">{errorMessage(retry.error)}</Alert>}
              <TextField label="蔬菜名稱（可修改）" value={name} onChange={(e) => setName(e.target.value)} fullWidth />
              <Box>
                <Typography variant="body2" color="text.secondary" gutterBottom>
                  選擇對應的標準食材加入「家中已有」：
                </Typography>
                {matches.isFetching && <CircularProgress size={20} />}
                {query && matches.data?.length === 0 && (
                  <Typography variant="body2" color="text.secondary">查無符合的標準食材，請修改名稱。</Typography>
                )}
                <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap', rowGap: 1 }}>
                  {matches.data?.map((i) => (
                    <Chip key={i.ingredient_id} label={`加入：${i.name}`} color="primary" variant="outlined"
                      disabled={confirm.isPending} onClick={() => confirm.mutate(i)} />
                  ))}
                </Stack>
              </Box>
              {confirm.isError && <Alert severity="error">{errorMessage(confirm.error)}</Alert>}
              <Box>
                <Button size="small" color="inherit" disabled={discard.isPending} onClick={() => discard.mutate()}>
                  刪除這張照片
                </Button>
              </Box>
            </Stack>
          )}
        </Stack>
      </CardContent>
    </Card>
  )
}
