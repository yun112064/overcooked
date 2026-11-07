import { useState } from 'react'
import { Alert, Button, Chip, MenuItem, Paper, Stack, TextField, Typography } from '@mui/material'
import { channels, contentName, managementError, time } from '../../api/management'
import type { ActivationPlan, Binding, Collection, EditableDomain, Entity, LegacyDetail, LegacyRelease, LegacyRow, Measurement, Price, Publication, Recipe } from '../../api/management'
import { blankContent, unknownMeasurement } from './content'
import { ContentPreview } from './Proposal'
import type { ContextSelection } from './Proposal'
import { ReadState, ResultPage } from './shared'
import { useManagementCommand, useManagementRead } from './hooks'

export type EditSelection = { initial: Entity['content']; context: ContextSelection }

export function Publications({ domain, canEdit, canActivate, select }: {
  domain: EditableDomain; canEdit: boolean; canActivate: boolean; select: (input: EditSelection) => void
}) {
  const [cursor, setCursor] = useState<string>()
  const [publication, setPublication] = useState<string>()
  const list = useManagementRead<Collection<Publication>>('/publications', { domain, cursor })
  const binding = useManagementRead<Binding>('/bindings/' + channels[domain])
  return <Stack spacing={2}>
    <Alert severity="info">{domain === 'price'
      ? '啟用價格版本會影響新的成本計算。既有採用餐單保留當時的快照。'
      : '發布版本可供管理檢視。正式餐單生成尚未就緒。'}</Alert>
    <ReadState query={binding}>{(data) => <Typography color="text.secondary">
      目前啟用版本 {data.publication_id ?? '尚未啟用'} · 更新於 {time(data.updated_at)}
    </Typography>}</ReadState>
    <ReadState query={list}>{(data) => <ResultPage empty={!data.items.length} next={data.next_cursor} advance={setCursor}
      reset={cursor ? () => setCursor(undefined) : undefined}>
      {data.items.map((item) => <Paper key={item.publication_id} variant="outlined" sx={{ p: 1.5 }}>
        <Stack spacing={1}>
          <Typography variant="body2" sx={{ overflowWrap: 'anywhere' }}>{item.publication_id}</Typography>
          <Typography>{time(item.created_at)} · {item.member_count} 筆</Typography>
          <Stack direction="row" spacing={1}>
            <Chip size="small" label="已發布" />
            {binding.data && <Chip size="small" label={binding.data.publication_id === item.publication_id ? '已啟用' : '尚未啟用'} />}
          </Stack>
          <Button onClick={() => setPublication(item.publication_id)} sx={{ alignSelf: 'start' }}>檢視發布內容</Button>
        </Stack>
      </Paper>)}
    </ResultPage>}</ReadState>
    {publication && <>
      {domain === 'price' && canActivate && binding.data && binding.data.publication_id !== publication &&
        <PriceActivation key={publication + ':' + binding.data.revision} publication={publication} binding={binding.data} />}
      <PublicationContents key={publication} id={publication} domain={domain} canEdit={canEdit} select={select} />
    </>}
  </Stack>
}

function PriceActivation({ publication, binding }: { publication: string; binding: Binding }) {
  const [reason, setReason] = useState('')
  const [mode, setMode] = useState<'activate' | 'rollback'>('activate')
  const base = '/bindings/price_policy/'
  const plan = useManagementCommand<ActivationPlan>(base + 'plans')
  const activate = useManagementCommand<Binding>(base + 'activate')
  const rollback = useManagementCommand<Binding>(base + 'rollback')
  const command = mode === 'activate' ? activate : rollback
  const expected = { expected_binding_revision: binding.revision, reason: reason.trim() }
  const busy = plan.isPending || command.isPending
  return <Paper variant="outlined" sx={{ p: 2 }}><Stack spacing={1}>
    <Typography sx={{ overflowWrap: 'anywhere' }}>核對價格版本 {publication}</Typography>
    <TextField select size="small" label="價格版本操作" value={mode} disabled={busy} onChange={(event) => {
      setMode(event.target.value as typeof mode)
      plan.reset()
    }}>
      <MenuItem value="activate">啟用後續版本</MenuItem>
      <MenuItem value="rollback">回復先前啟用版本</MenuItem>
    </TextField>
    <TextField size="small" label="啟用或回復原因" value={reason} disabled={busy}
      onChange={(event) => { setReason(event.target.value); plan.reset() }} />
    <Button disabled={busy || !reason.trim()} onClick={() => plan.mutate({ ...expected, publication_id: publication })}>核對價格版本啟用計畫</Button>
    {plan.data && <Alert severity={plan.data.can_activate ? 'info' : 'warning'}>
      {plan.data.can_activate ? '版本核對完成，有效至 ' + time(plan.data.expires_at) : '版本尚未就緒'}
      {plan.data.issues.map((issue, index) => <Typography variant="body2" key={issue.code + index}>{issue.message}</Typography>)}
    </Alert>}
    {plan.data?.can_activate && <Button variant="contained" disabled={busy} onClick={() => {
      if (!plan.data) return
      command.mutate({ ...expected, plan_id: plan.data.plan_id, plan_hash: plan.data.plan_hash,
        ...(mode === 'activate' ? { publication_id: publication } : { target_publication_id: publication }) })
    }}>{mode === 'activate' ? '確認啟用價格版本' : '確認回復價格版本'}</Button>}
    {(plan.isError || command.isError) && <Alert severity="error">{managementError(plan.error ?? command.error)}</Alert>}
  </Stack></Paper>
}

function PublicationContents({ id, domain, canEdit, select }: {
  id: string; domain: EditableDomain; canEdit: boolean; select: (input: EditSelection) => void
}) {
  const [cursor, setCursor] = useState<string>()
  const [entity, setEntity] = useState<Entity>()
  const query = useManagementRead<Collection<Entity>>('/entities/' + domain, { view_kind: 'publication', view_id: id, cursor })
  return <Stack spacing={2}>
    <Typography sx={{ fontWeight: 'bold', overflowWrap: 'anywhere' }}>發布版本 {id}</Typography>
    <Typography color="text.secondary">這裡列出經新版審核的項目。歷史項目可從舊版目錄查閱。</Typography>
    <ReadState query={query}>{(data) => <>
      {domain === 'price' && canEdit && data.items.length > 0 && <Button onClick={() => {
        const price = data.items[0].content as Price
        select({ initial: blankContent('price'), context: { base_publication_id: id,
          legacy_recipe_release_id: price.legacy_recipe_release_id, ingredient_publication_id: price.ingredient_publication_id } })
      }}>新增至此價格版本</Button>}
      <ResultPage empty={!data.items.length} next={data.next_cursor} advance={setCursor}
      reset={cursor ? () => setCursor(undefined) : undefined}>
      {data.items.map((item) => <Button key={item.logical_id} variant="outlined" sx={{ justifyContent: 'start' }}
        onClick={() => setEntity(item)}>{contentName(item.content)} · {item.logical_id}</Button>)}
    </ResultPage></>}</ReadState>
    {entity && <Paper variant="outlined" sx={{ p: 2 }}>
      <Stack spacing={1}>
        <ContentPreview content={entity.content} />
        {canEdit && domain !== 'price' && <Button onClick={() => select({ initial: entity.content, context: { base_publication_id: id } })}>
          建立修訂草稿
        </Button>}
        {canEdit && domain === 'price' && <Button onClick={() => {
          const price = entity.content as Price
          select({ initial: { ...price, replaces_version_id: entity.ref.version_id, snapshot_id: null,
            price_twd: null, package_mass_g: null, observed_at: null,
            valid_from: null, valid_until: null, sources: [] }, context: { base_publication_id: id,
            legacy_recipe_release_id: price.legacy_recipe_release_id, ingredient_publication_id: price.ingredient_publication_id } })
        }}>建立明確替換草稿</Button>}
      </Stack>
    </Paper>}
  </Stack>
}

type LegacyDomain = Exclude<EditableDomain, 'price'>

export function LegacyCatalog({ domain, canEdit, select }: { domain: LegacyDomain; canEdit: boolean; select: (input: EditSelection) => void }) {
  const [chosen, setChosen] = useState('')
  const [cursor, setCursor] = useState<string>()
  const releases = useManagementRead<Collection<LegacyRelease>>('/legacy-catalog/' + domain + '/releases', { cursor })
  const selected = chosen || releases.data?.items.find((release) => release.active)?.release_id || ''
  return <Stack spacing={2}>
    <Alert severity="info">舊版內容保留原版本身分。建立修訂須提供來源證據並完成新版審核。</Alert>
    <ReadState query={releases}>{(data) => <Stack spacing={1}>
      {data.items.length ? <TextField select size="small" label="舊版資料版本" value={data.items.some((row) => row.release_id === selected) ? selected : ''}
        onChange={(event) => setChosen(event.target.value)}>
        <MenuItem value="">選擇版本</MenuItem>
        {data.items.map((release) => <MenuItem value={release.release_id} key={release.release_id}>
          {release.dataset_version} · {release.release_id}{release.active ? '（舊版使用中）' : ''}
        </MenuItem>)}
      </TextField> : <Typography color="text.secondary">沒有舊版資料版本。</Typography>}
      {data.next_cursor && <Button onClick={() => { setChosen(''); setCursor(data.next_cursor ?? undefined) }}>下一頁版本</Button>}
      {cursor && <Button onClick={() => { setChosen(''); setCursor(undefined) }}>第一頁版本</Button>}
    </Stack>}</ReadState>
    {selected && <LegacyRows key={selected} domain={domain} release={selected} canEdit={canEdit} select={select} />}
  </Stack>
}

function LegacyRows({ domain, release, canEdit, select }: {
  domain: LegacyDomain; release: string; canEdit: boolean; select: (input: EditSelection) => void
}) {
  const [cursor, setCursor] = useState<string>()
  const [logical, setLogical] = useState('')
  const base = '/legacy-catalog/' + domain + '/' + release
  const rows = useManagementRead<Collection<LegacyRow>>(base, { cursor })
  const detail = useManagementRead<LegacyDetail>(base + '/' + logical, undefined, Boolean(logical))
  return <Stack spacing={2}>
    <ReadState query={rows}>{(data) => <ResultPage empty={!data.items.length} next={data.next_cursor} advance={setCursor}
      reset={cursor ? () => setCursor(undefined) : undefined}>
      {data.items.map((item) => <Button key={item.logical_id} variant="outlined" sx={{ justifyContent: 'start' }}
        onClick={() => setLogical(item.logical_id)}>{item.name} · {item.logical_id}</Button>)}
    </ResultPage>}</ReadState>
    {logical && <ReadState query={detail}>{(data) => <Paper variant="outlined" sx={{ p: 2 }}>
      <Stack spacing={1}>
        <Typography variant="h6">{data.name}</Typography>
        <Chip label="舊版未經新版審核" size="small" sx={{ alignSelf: 'start' }} />
        <Typography>別名 {data.aliases.join('、') || '無別名紀錄'}</Typography>
        {domain === 'recipe' && <>
          <Typography>來源份量 {data.servings_raw || '未知'} · {data.original_servings ?? '未知'} {data.servings_basis === 'batch' ? '批' : '份'}</Typography>
          {data.ingredients?.map((line) => <Typography key={line.line_id} variant="body2">
            {line.canonical_name} · {line.raw_text} · 來源重量 {line.source_weight_g === null ? '未知' : `${line.source_weight_g} g`}
          </Typography>)}
        </>}
        {canEdit && (domain === 'ingredient' || ['portions', 'batch'].includes(data.servings_basis ?? '')) &&
          <Button onClick={() => select({ initial: fromLegacy(domain, data), context: { legacy_recipe_release_id: release } })}>
            建立修訂草稿
          </Button>}
      </Stack>
    </Paper>}</ReadState>}
  </Stack>
}

function fromLegacy(domain: LegacyDomain, detail: LegacyDetail): Entity['content'] {
  if (domain === 'ingredient') return {
    food_id: detail.logical_id, canonical_name: detail.name, aliases: detail.aliases, sources: [], declared_allergen_ids: null,
  }
  const recipe = blankContent('recipe') as Recipe
  const amount = (value: string | null): Measurement => value === null ? unknownMeasurement() :
    { status: 'declared', value, unknown_reason: null, evidence_ids: [] }
  return {
    ...recipe, logical_recipe_id: detail.logical_id, name: detail.name, aliases: detail.aliases,
    category: detail.category ?? '', cuisine: detail.cuisine ?? '', meal_type: detail.meal_type ?? '',
    standard_dish_id: detail.standard_dish_id ?? null,
    servings: { ...recipe.servings, count: detail.original_servings ?? null, raw_text: detail.servings_raw ?? '',
      basis: detail.servings_basis === 'batch' ? 'batch' : 'portions' },
    ingredients: detail.ingredients?.map((line) => ({
      line_id: line.line_id, food_id: line.food_id, ingredient_version_id: null, canonical_name: line.canonical_name,
      raw_text: line.raw_text, unit: line.source_unit, amount: amount(line.source_amount),
      weight_g: unknownMeasurement('請核對來源與整份食譜烹調前重量的定義'), weight_basis: 'whole_source_recipe_before_cooking',
    })) ?? [],
    steps: detail.steps ?? [],
  }
}
