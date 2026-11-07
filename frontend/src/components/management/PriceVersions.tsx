import { useState } from 'react'
import { Button, MenuItem, Stack, TextField } from '@mui/material'
import { time } from '../../api/management'
import type { Collection, LegacyRelease, Publication } from '../../api/management'
import { useManagementRead } from './hooks'
import { ReadState } from './shared'

type Option = { id: string; label: string }

function VersionChoices({ label, value, change, locked, items, next, cursor, page, optional = false }: {
  label: string; value: string; change: (id: string) => void; locked: boolean
  items: Option[]; next: string | null; cursor?: string; page: (cursor?: string) => void
  optional?: boolean
}) {
  return <Stack spacing={0.5}>
    <TextField select required={!optional} size="small" label={label} value={value} disabled={locked}
      onChange={(event) => change(event.target.value)}>
      <MenuItem value="">{optional ? '建立首個價格版本' : '選擇版本'}</MenuItem>
      {value && !items.some((item) => item.id === value) && <MenuItem value={value}>{value}</MenuItem>}
      {items.map((item) => <MenuItem key={item.id} value={item.id}>{item.label}</MenuItem>)}
    </TextField>
    {!locked && <Stack direction="row">
      {next && <Button size="small" onClick={() => page(next)}>下一頁版本</Button>}
      {cursor && <Button size="small" onClick={() => page()}>第一頁版本</Button>}
    </Stack>}
  </Stack>
}

export function PriceReleaseSelect({ value, change, locked = false }: {
  value: string; change: (id: string) => void; locked?: boolean
}) {
  const [cursor, page] = useState<string>()
  const query = useManagementRead<Collection<LegacyRelease>>('/price-releases', { cursor })
  return <ReadState query={query}>{(data) => <VersionChoices label="食譜資料版本"
    value={value} change={change} locked={locked} cursor={cursor} page={page} next={data.next_cursor}
    items={data.items.map((item) => ({ id: item.release_id,
      label: item.dataset_version + ' · ' + item.release_id + (item.active ? '（使用中）' : '') }))} />}
  </ReadState>
}

export function PriceIngredientVersionSelect({ release, value, change, locked = false }: {
  release: string; value: string; change: (id: string) => void; locked?: boolean
}) {
  const [cursor, page] = useState<string>()
  const query = useManagementRead<Collection<Publication>>('/publications', {
    domain: 'ingredient', legacy_recipe_release_id: release, cursor,
  }, Boolean(release))
  if (!release) return <TextField size="small" label="食材發布版本" placeholder="請先選食譜資料版本" disabled />
  return <ReadState query={query}>{(data) => <VersionChoices label="食材發布版本"
    value={value} change={change} locked={locked} cursor={cursor} page={page} next={data.next_cursor}
    items={data.items.map((item) => ({ id: item.publication_id,
      label: time(item.created_at) + ' · ' + item.member_count + ' 筆 · ' + item.publication_id }))} />}
  </ReadState>
}

export function PriceBaseVersionSelect({ release, ingredient, value, change, locked = false }: {
  release: string; ingredient: string; value: string; change: (id: string) => void; locked?: boolean
}) {
  const [cursor, page] = useState<string>()
  const query = useManagementRead<Collection<Publication>>('/publications', {
    domain: 'price', legacy_recipe_release_id: release, ingredient_publication_id: ingredient, cursor,
  }, Boolean(release && ingredient))
  if (!release || !ingredient) return <TextField size="small" label="價格基礎發布版本" placeholder="請先選取食譜與食材版本" disabled />
  return <ReadState query={query}>{(data) => <VersionChoices label="價格基礎發布版本" optional
    value={value} change={change} locked={locked} cursor={cursor} page={page} next={data.next_cursor}
    items={data.items.map((item) => ({ id: item.publication_id,
      label: time(item.created_at) + ' · ' + item.member_count + ' 筆 · ' + item.publication_id }))} />}
  </ReadState>
}
