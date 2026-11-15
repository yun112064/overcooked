import { Alert, Autocomplete, Box, Button, Stack, TextField, Typography } from '@mui/material'
import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { api, errorMessage } from '../api/client'
import type { IngredientRef } from '../api/types'
import { Plus } from 'lucide-react'

type Props = {
  label: string
  value: IngredientRef[]
  onChange: (value: IngredientRef[]) => void
  helperText?: string
  error?: boolean
  confirmBeforeAdd?: boolean
}

/** 從標準食材字典選擇；不接受自由輸入，避免無法對映的禁忌。 */
export default function IngredientPicker({ label, value, onChange, helperText, error, confirmBeforeAdd = false }: Props) {
  const [input, setInput] = useState('')
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<IngredientRef | null>(null)
  const [notice, setNotice] = useState('')

  useEffect(() => {
    const timer = setTimeout(() => setQuery(input.trim()), 250)
    return () => clearTimeout(timer)
  }, [input])

  const results = useQuery({
    queryKey: ['ingredients', query],
    queryFn: async () =>
      (await api.get<{ ingredients: IngredientRef[] }>('/ingredients', { params: { q: query } })).data.ingredients,
    enabled: query.length > 0,
    staleTime: 60_000,
  })

  if (confirmBeforeAdd) {
    const duplicate = selected && value.some(item => item.ingredient_id === selected.ingredient_id)
    return <Stack spacing={1}>
      <Box className="live-manual-entry">
        <Autocomplete
          options={results.data ?? []}
          value={selected}
          inputValue={input}
          onChange={(_, next) => {
            setSelected(next)
            setInput(next ? next.name ?? next.ingredient_id : '')
            setNotice('')
          }}
          onInputChange={(_, next, reason) => {
            if (reason === 'reset') return
            setInput(next)
            if (reason === 'input' || reason === 'clear') setSelected(null)
            setNotice('')
          }}
          getOptionLabel={item => item.name ?? item.ingredient_id}
          isOptionEqualToValue={(a, b) => a.ingredient_id === b.ingredient_id}
          filterOptions={items => items}
          loading={results.isFetching}
          noOptionsText={results.isError ? '搜尋失敗，請重試' : query ? '查無符合的標準食材' : '輸入名稱搜尋'}
          renderInput={params => <TextField {...params} label={label} error={error}
            helperText={helperText ?? '從搜尋結果選擇標準食材，再按「加入」。'} />}
        />
        <Button variant="outlined" startIcon={<Plus size={18} />} disabled={!selected || !!duplicate}
          onClick={() => {
            if (!selected || duplicate) return
            onChange([...value, selected])
            setNotice(`已加入：${selected.name ?? selected.ingredient_id}`)
            setSelected(null)
            setInput('')
            setQuery('')
          }}>加入</Button>
      </Box>
      {(duplicate || notice) && <Typography variant="body2" color="text.secondary" role="status">
        {duplicate ? `${selected?.name ?? selected?.ingredient_id}已在清單中。` : notice}
      </Typography>}
      {results.isError && query && <Alert severity="error" action={<Button color="inherit" size="small"
        onClick={() => { void results.refetch() }}>重試</Button>}>{errorMessage(results.error)}</Alert>}
    </Stack>
  }

  return (
    <Autocomplete
      multiple
      options={results.data ?? []}
      value={value}
      onChange={(_, next) => onChange(next)}
      inputValue={input}
      onInputChange={(_, next, reason) => { if (reason !== 'reset') setInput(next) }}
      getOptionLabel={(o) => o.name ?? o.ingredient_id}
      isOptionEqualToValue={(a, b) => a.ingredient_id === b.ingredient_id}
      filterOptions={(o) => o}
      loading={results.isFetching}
      noOptionsText={query ? '查無符合的標準食材' : '輸入名稱搜尋'}
      renderInput={(params) => (
        <TextField {...params} label={label} helperText={helperText} error={error} />
      )}
    />
  )
}
