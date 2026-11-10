import { Autocomplete, TextField } from '@mui/material'
import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { IngredientRef } from '../api/types'

type Props = {
  label: string
  value: IngredientRef[]
  onChange: (value: IngredientRef[]) => void
  helperText?: string
  error?: boolean
}

/** 從標準食材字典選擇；不接受自由輸入，避免無法對映的禁忌。 */
export default function IngredientPicker({ label, value, onChange, helperText, error }: Props) {
  const [input, setInput] = useState('')
  const [query, setQuery] = useState('')

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
