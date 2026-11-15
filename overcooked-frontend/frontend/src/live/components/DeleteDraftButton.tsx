import { Alert, Button, Dialog, DialogActions, DialogContent, DialogContentText, DialogTitle } from '@mui/material'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Trash2 } from 'lucide-react'
import { useState } from 'react'
import { api, errorMessage } from '../api/client'
import { MEAL_LABELS, type MealRequest, type MealRequestSummary } from '../api/types'

class DraftChangedError extends Error {}

export default function DeleteDraftButton({ draft, disabled = false }: { draft: MealRequestSummary; disabled?: boolean }) {
  const [target, setTarget] = useState<MealRequestSummary | null>(null)
  const queryClient = useQueryClient()
  const remove = useMutation({
    mutationFn: async (target: MealRequestSummary) => {
      const latest = (await api.get<MealRequest>(`/meal-requests/${target.id}`)).data
      if (latest.status !== 'draft') throw new DraftChangedError('這份資料已不是草稿，請重新整理後再確認。')
      await api.delete(`/meal-requests/${target.id}`)
    },
    onSuccess: (_data, deleted) => {
      setTarget(null)
      queryClient.setQueryData<MealRequestSummary[]>(['meal-requests'], data => data?.filter(row => row.id !== deleted.id))
      queryClient.removeQueries({ queryKey: ['meal-request', deleted.id] })
      queryClient.removeQueries({ queryKey: ['recipe-catalog', deleted.id] })
      void queryClient.invalidateQueries({ queryKey: ['meal-requests'] })
    },
    onError: (_error, failed) => {
      void queryClient.invalidateQueries({ queryKey: ['meal-request', failed.id] })
    },
  })
  const close = () => {
    if (remove.isPending) return
    setTarget(null)
    if (remove.isError) void queryClient.invalidateQueries({ queryKey: ['meal-requests'] })
  }
  if (draft.status !== 'draft') return null
  return <>
    <Button color="inherit" size="small" startIcon={<Trash2 size={16} />} disabled={disabled || remove.isPending}
      onClick={() => { remove.reset(); setTarget(draft) }}>刪除草稿</Button>
    <Dialog open={!!target} onClose={close} fullWidth maxWidth="xs" aria-labelledby={`delete-draft-${draft.id}`}>
      <DialogTitle id={`delete-draft-${draft.id}`}>刪除這份草稿？</DialogTitle>
      <DialogContent>
        <DialogContentText>{target?.meal_date?.replaceAll('-', ' / ') ?? '未選日期'} · {target?.meal_type ? MEAL_LABELS[target.meal_type] : '未選餐別'} · {target?.mode === 'picker' ? '食譜庫選菜' : '餐食問卷'}</DialogContentText>
        <DialogContentText sx={{ mt: 1 }}>刪除後無法復原。</DialogContentText>
        {remove.isError && <Alert severity="error" sx={{ mt: 2 }}>{remove.error instanceof DraftChangedError ? remove.error.message : errorMessage(remove.error)}</Alert>}
      </DialogContent>
      <DialogActions>
        <Button color="inherit" onClick={close} disabled={remove.isPending}>保留草稿</Button>
        <Button color="error" variant="contained" disabled={remove.isPending || !target} onClick={() => { if (target) remove.mutate(target) }}>
          {remove.isPending ? '刪除中…' : '確認刪除'}
        </Button>
      </DialogActions>
    </Dialog>
  </>
}
