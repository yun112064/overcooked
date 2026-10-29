import {
  Alert, Box, Button, Card, CardContent, Chip, CircularProgress, Stack, TextField, Typography,
} from '@mui/material'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { api, errorMessage } from '../api/client'
import type { IngredientRef } from '../api/types'

type Suggestion = { name: string; ingredient_id: string | null }

type RecognitionResult = {
  id: string
  status: 'succeeded' | 'no_result' | 'unavailable'
  message: string
  suggestions: Suggestion[]
  retryable: boolean
  image_url: string
}

type Props = {
  mealRequestId: string
  onAdd: (ingredient: IngredientRef) => void
}

/** 拍照／上傳 → 每種辨識到的蔬菜各一個可編輯文字框 → 逐一選標準食材加入已有食材。 */
export default function RecognitionCapture({ mealRequestId, onAdd }: Props) {
  const [result, setResult] = useState<RecognitionResult | null>(null)

  const upload = useMutation({
    mutationFn: async (file: File) => {
      const form = new FormData()
      form.append('image', file)
      form.append('meal_request_id', mealRequestId)
      return (await api.post<RecognitionResult>('/recognitions', form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })).data
    },
    onSuccess: setResult,
  })
  const retry = useMutation({
    mutationFn: async () => (await api.post<RecognitionResult>(`/recognitions/${result!.id}/retry`)).data,
    onSuccess: setResult,
  })
  const discard = useMutation({
    mutationFn: async () => api.delete(`/recognitions/${result!.id}`),
    onSuccess: () => setResult(null),
  })

  // 沒有辨識結果時仍給一個空白框讓使用者手動輸入
  const rows: Suggestion[] = result?.suggestions.length ? result.suggestions : [{ name: '', ingredient_id: null }]

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
                  {result.suggestions.length > 1 && `（共 ${result.suggestions.length} 種）`}
                </Alert>
              </Box>
              {retry.isError && <Alert severity="error">{errorMessage(retry.error)}</Alert>}
              {rows.map((s, i) => (
                // key 含辨識 ID：重試後換成新結果時重建各列狀態
                <SuggestionRow key={`${result.id}-${result.status}-${i}`} recognitionId={result.id}
                  initialName={s.name} onAdd={onAdd} />
              ))}
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

function SuggestionRow({ recognitionId, initialName, onAdd }: {
  recognitionId: string
  initialName: string
  onAdd: (ingredient: IngredientRef) => void
}) {
  const [name, setName] = useState(initialName)
  const [query, setQuery] = useState(initialName.trim())
  const [done, setDone] = useState<{ added: IngredientRef } | 'skipped' | null>(null)

  useEffect(() => {
    const timer = setTimeout(() => setQuery(name.trim()), 250)
    return () => clearTimeout(timer)
  }, [name])

  const confirm = useMutation({
    mutationFn: async (ingredient: IngredientRef) => {
      await api.post(`/recognitions/${recognitionId}/confirm`, { name: name.trim() || ingredient.name, ingredient_id: ingredient.ingredient_id })
      return ingredient
    },
    onSuccess: (ingredient) => {
      onAdd(ingredient)
      setDone({ added: ingredient })
    },
  })

  const matches = useQuery({
    queryKey: ['ingredients', query],
    queryFn: async () =>
      (await api.get<{ ingredients: IngredientRef[] }>('/ingredients', { params: { q: query, limit: 6 } })).data.ingredients,
    enabled: !done && query.length > 0,
    staleTime: 60_000,
  })

  if (done) {
    return (
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        {done === 'skipped'
          ? <>
            <Chip label={`已略過：${name || '（空白）'}`} variant="outlined" />
            <Button size="small" color="inherit" onClick={() => setDone(null)}>改回</Button>
          </>
          // 加入後要移除，請在下方「家中已有的食材」刪除
          : <Chip label={`已加入：${done.added.name}`} color="success" variant="outlined" />}
      </Box>
    )
  }

  return (
    <Box sx={{ p: 1.5, border: 1, borderColor: 'divider', borderRadius: 1 }}>
      <Stack spacing={1}>
        <Box sx={{ display: 'flex', gap: 1, alignItems: 'center' }}>
          <TextField label="蔬菜名稱（可修改）" value={name} onChange={(e) => setName(e.target.value)} fullWidth size="small" />
          <Button size="small" color="inherit" onClick={() => setDone('skipped')}>略過</Button>
        </Box>
        <Typography variant="body2" color="text.secondary">選擇對應的標準食材加入「家中已有」：</Typography>
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
        {confirm.isError && <Alert severity="error">{errorMessage(confirm.error)}</Alert>}
      </Stack>
    </Box>
  )
}
