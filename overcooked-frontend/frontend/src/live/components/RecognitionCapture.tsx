import {
  Alert, Box, Button, Chip, CircularProgress, Stack, TextField, Typography,
} from '@mui/material'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { api, errorMessage } from '../api/client'
import type { IngredientRef } from '../api/types'
import { ImagePlus, ScanLine, Upload } from 'lucide-react'

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
  const [photo, setPhoto] = useState<{ file: File; url: string } | null>(null)
  const [photoError, setPhotoError] = useState('')
  const input = useRef<HTMLInputElement>(null)
  useEffect(() => () => { if (photo) URL.revokeObjectURL(photo.url) }, [photo])

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
    onSuccess: () => { setResult(null); setPhoto(null) },
  })

  // 沒有辨識結果時仍給一個空白框讓使用者手動輸入
  const rows: Suggestion[] = result?.suggestions.length ? result.suggestions : [{ name: '', ingredient_id: null }]

  const busy = upload.isPending || retry.isPending || discard.isPending
  return <Stack spacing={2}>
    <input ref={input} hidden type="file" accept="image/jpeg,image/png,image/webp" capture="environment"
      aria-label="選擇食材照片" disabled={busy}
      onChange={e => {
        const file = e.target.files?.[0]
        e.target.value = ''
        if (!file) return
        if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) {
          setPhotoError('請選擇 JPG、PNG 或 WebP 照片。')
          return
        }
        setPhoto({ file, url: URL.createObjectURL(file) })
        setResult(null)
        setPhotoError('')
        upload.reset()
        retry.reset()
        discard.reset()
      }} />
    {photo ? <Box className="live-photo-preview">
      <Box component="img" src={photo.url} alt="食材照片預覽" />
    </Box> : <Box className="live-upload-zone">
      <Box className="live-upload-symbol"><ImagePlus size={32} aria-hidden="true" /></Box>
      <Typography variant="subtitle1">拍下家裡的蔬菜</Typography>
      <Typography variant="body2" color="text.secondary">上傳照片，再確認辨識出的食材。</Typography>
      <Button variant="outlined" startIcon={<Upload size={18} />} disabled={busy} onClick={() => input.current?.click()}>
        上傳照片
      </Button>
      <Typography variant="caption">JPG、PNG、WebP</Typography>
    </Box>}
    {photo && <Box className="live-photo-actions">
      <Button variant="outlined" disabled={busy} onClick={() => input.current?.click()}>更換照片</Button>
      <Button variant="contained" startIcon={upload.isPending ? <CircularProgress size={18} color="inherit" /> : <ScanLine size={18} />}
        disabled={busy} onClick={() => upload.mutate(photo.file)}>{upload.isPending ? '辨識中…' : '開始辨識'}</Button>
      <Button color="inherit" disabled={busy} onClick={() => {
        if (result) discard.mutate()
        else { setPhoto(null); upload.reset() }
      }}>刪除照片</Button>
    </Box>}
    <Typography variant="caption">只確認食材種類，不需要重量；也可以直接手動搜尋。</Typography>
    {photoError && <Alert severity="error">{photoError}</Alert>}
    {upload.isError && <Alert severity="error">{errorMessage(upload.error)}</Alert>}
    {discard.isError && <Alert severity="error">{errorMessage(discard.error)}</Alert>}
    {result && <Box className="live-recognition-results">
      <Typography component="h3" variant="subtitle1">辨識結果</Typography>
      <Alert severity={result.status === 'succeeded' ? 'info' : 'warning'}
        action={result.retryable
          ? <Button color="inherit" size="small" disabled={busy} onClick={() => retry.mutate()}>重試</Button>
          : undefined}>
        {result.message}{result.suggestions.length > 1 && `（共 ${result.suggestions.length} 種）`}
      </Alert>
      {retry.isError && <Alert severity="error">{errorMessage(retry.error)}</Alert>}
      {rows.map((s, i) => <SuggestionRow key={`${result.id}-${result.status}-${i}`} recognitionId={result.id}
        initialName={s.name} onAdd={onAdd} />)}
    </Box>}
  </Stack>
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
