import { useState } from 'react'
import { Alert, Box, Button, Chip, Divider, FormControlLabel, MenuItem, Paper, Stack, Switch, TextField, Typography } from '@mui/material'
import { managementError, originNames } from '../../api/management'
import type { Content, EditableDomain, Ingredient, Line, Measurement, Price, Recipe, Source } from '../../api/management'
import { useManagementCommand } from './hooks'
import { newLine, unknownMeasurement } from './content'

type Update<T> = (value: T) => void
const nullable = (value: string) => value.trim() || null
const decimalPattern = '(0|[1-9][0-9]{0,15})(\\.[0-9]{0,5}[1-9])?'

function Field({ label, value, update, required = false, type, hint, multiline = false, readOnly = false }: {
  label: string; value: string | null; update?: Update<string>; required?: boolean; type?: 'decimal' | 'datetime-local'
  hint?: string; multiline?: boolean; readOnly?: boolean
}) {
  return <TextField fullWidth size="small" label={label} value={value ?? ''} required={required} multiline={multiline}
    type={type === 'datetime-local' ? type : 'text'} placeholder={required ? undefined : '未知'}
    helperText={hint} onChange={update ? (event) => update(event.target.value) : undefined}
    slotProps={{
      input: { readOnly },
      inputLabel: type === 'datetime-local' ? { shrink: true } : undefined,
      htmlInput: type === 'decimal' ? { inputMode: 'decimal', pattern: decimalPattern } :
        type === 'datetime-local' ? { step: 1 } : undefined,
    }} />
}

function Select<T extends string>({ label, value, choices, update }: {
  label: string; value: T; choices: Record<T, string>; update: Update<T>
}) {
  return <TextField select fullWidth size="small" label={label} value={value} onChange={(event) => update(event.target.value as T)}>
    {Object.entries<string>(choices).map(([id, name]) => <MenuItem key={id} value={id}>{name}</MenuItem>)}
  </TextField>
}

function SourceInputs({ domain, sources, append, remove }: {
  domain: EditableDomain; sources: Source[]; append: Update<Source>; remove: Update<string>
}) {
  const [file, setFile] = useState<File | null>(null)
  const [kind, setKind] = useState<'manual_entry' | 'supplied_document' | 'workbook'>('manual_entry')
  const [reason, setReason] = useState('')
  const upload = useManagementCommand<{ artifact_id: string; sha256: string; source_kind: Source['origin_kind'] }>('/artifacts', (data) => {
    append({ artifact_id: data.artifact_id, artifact_sha256: data.sha256, origin_kind: data.source_kind, source_policy_version_id: null })
    setFile(null)
  })
  function send() {
    if (!file) return
    const body = new FormData()
    body.append('domain', domain)
    body.append('source_kind', kind)
    body.append('reason', reason.trim())
    body.append('file', file)
    upload.mutate(body)
  }
  return <Stack spacing={1.5}>
    <Typography variant="h6">來源證據</Typography>
    {sources.length === 0 && <Alert severity="info">草稿需要至少一份來源證據。上傳後可將數量或重量連結到這份證據。</Alert>}
    {sources.map((source) => <Stack key={source.artifact_id} direction="row" spacing={1} sx={{ alignItems: 'center' }}>
      <Chip size="small" label={originNames[source.origin_kind]} />
      <Typography variant="body2" sx={{ overflowWrap: 'anywhere' }}>{source.artifact_id}</Typography>
      <Button size="small" onClick={() => remove(source.artifact_id)}>移出草稿</Button>
    </Stack>)}
    <Stack spacing={1} sx={{ maxWidth: 540 }}>
      <Select label="證據類型" value={kind} update={setKind}
        choices={{ manual_entry: '人工證據', supplied_document: '提供的文件', workbook: '工作簿' }} />
      <Button component="label" variant="outlined" sx={{ alignSelf: 'start' }}>
        選擇證據檔案
        <input type="file" accept=".txt,.json,.xlsx" hidden onChange={(event) => {
          setFile(event.target.files?.[0] ?? null)
          event.target.value = ''
        }} />
      </Button>
      {file && <Typography variant="body2">{file.name}</Typography>}
      <Field label="上傳理由" value={reason} update={setReason} />
      <Button onClick={send} disabled={!file || !reason.trim() || upload.isPending}>上傳證據</Button>
      {upload.isError && <Alert severity="error">{managementError(upload.error)}</Alert>}
    </Stack>
  </Stack>
}

function Evidence({ label, sources, ids, update }: { label: string; sources: Source[]; ids: string[]; update: Update<string[]> }) {
  return <TextField select fullWidth size="small" label={label} value={ids} onChange={(event) => {
    const values = event.target.value
    update(typeof values === 'string' ? values.split(',') : values)
  }} slotProps={{ select: { multiple: true } }}>
    {sources.map((source) => <MenuItem key={source.artifact_id} value={source.artifact_id}>
      {originNames[source.origin_kind]} {source.artifact_id}
    </MenuItem>)}
  </TextField>
}

function MeasurementInput({ label, data, sources, update }: { label: string; data: Measurement; sources: Source[]; update: Update<Measurement> }) {
  return <Stack spacing={1}>
    <Field label={label} type="decimal" value={data.value} update={(value) => {
      update(value ? { status: 'declared', value, unknown_reason: null, evidence_ids: [] } : unknownMeasurement())
    }} />
    {data.status === 'unknown' ? <Field label={label + '未知原因'} value={data.unknown_reason} required
      update={(unknown_reason) => update({ ...data, unknown_reason })} /> :
      <Evidence label={label + '證據'} sources={sources} ids={data.evidence_ids} update={(evidence_ids) => update({ ...data, evidence_ids })} />}
  </Stack>
}

function AllergenInput({ value, update }: { value: string[] | null; update: Update<string[] | null> }) {
  return <Stack spacing={1}>
    <FormControlLabel control={<Switch checked={value !== null} onChange={(_, checked) => update(checked ? [] : null)} />}
      label={value === null ? '過敏原未知' : '來源有明確聲明'} />
    {value !== null && <Field label="來源聲明的過敏原識別碼" multiline value={value.join('\n')}
      update={(input) => update(input.split('\n'))} hint="每行一個識別碼。僅在來源明確聲明沒有過敏原時留白。" />}
  </Stack>
}

function RecipeFields({ recipe, update }: { recipe: Recipe; update: Update<Recipe> }) {
  function editLine(index: number, changed: Partial<Line>) {
    update({ ...recipe, ingredients: recipe.ingredients.map((line, item) => item === index ? { ...line, ...changed } : line) })
  }
  return <Stack spacing={2}>
    {recipe.logical_recipe_id && <Field label="食譜識別碼" value={recipe.logical_recipe_id} readOnly />}
    <Field label="食譜名稱" value={recipe.name} required update={(name) => update({ ...recipe, name })} />
    <Field label="別名" multiline value={recipe.aliases.join('\n')} update={(text) => update({ ...recipe, aliases: text.split('\n') })} hint="每行一個別名。" />
    <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr' }, gap: 1.5 }}>
      <Field label="分類" value={recipe.category} required update={(category) => update({ ...recipe, category })} />
      <Field label="料理類型" value={recipe.cuisine} required update={(cuisine) => update({ ...recipe, cuisine })} />
      <Field label="餐別" value={recipe.meal_type} required update={(meal_type) => update({ ...recipe, meal_type })} />
      <Field label="標準菜色識別碼" value={recipe.standard_dish_id} update={(value) => update({ ...recipe, standard_dish_id: nullable(value) })} />
    </Box>
    <Typography variant="h6">份量</Typography>
    <Field label="來源份量原文" value={recipe.servings.raw_text} required
        update={(raw_text) => update({ ...recipe, servings: { ...recipe.servings, raw_text, evidence_ids: [] } })} />
    <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1}>
      <Select label="份量基準" value={recipe.servings.basis} choices={{ portions: '份', batch: '批' }} update={(basis) => update({
        ...recipe, servings: { ...recipe.servings, basis, portions_per_batch: basis === 'portions' ? null : recipe.servings.portions_per_batch, evidence_ids: [] },
      })} />
      <Field label="來源數量" type="decimal" value={recipe.servings.count}
        update={(value) => update({ ...recipe, servings: { ...recipe.servings, count: nullable(value), evidence_ids: [] } })} />
      {recipe.servings.basis === 'batch' && <Field label="每批份數" type="decimal" value={recipe.servings.portions_per_batch}
        update={(value) => update({ ...recipe, servings: { ...recipe.servings, portions_per_batch: nullable(value), evidence_ids: [] } })} />}
    </Stack>
    <Evidence label="份量證據" sources={recipe.sources} ids={recipe.servings.evidence_ids}
      update={(evidence_ids) => update({ ...recipe, servings: { ...recipe.servings, evidence_ids } })} />
    <Divider />
    <Typography variant="h6">食材明細</Typography>
    {recipe.ingredients.map((line, index) => <Paper key={line.line_id} variant="outlined" sx={{ p: 2 }}>
      <Stack spacing={1.5}>
        <Typography variant="subtitle1">食材 {index + 1}</Typography>
        <Field label="食材名稱" value={line.canonical_name} required update={(canonical_name) => editLine(index, { canonical_name })} />
        <Field label="來源原文" value={line.raw_text} required multiline update={(raw_text) => editLine(index, { raw_text })} />
        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1}>
          <Field label="Food ID" value={line.food_id} update={(value) => editLine(index, { food_id: nullable(value), ingredient_version_id: null })} />
          <Field label="食材版本識別碼" value={line.ingredient_version_id} update={(value) => editLine(index, { ingredient_version_id: nullable(value) })} />
          <Field label="來源單位" value={line.unit} update={(value) => editLine(index, { unit: nullable(value) })} />
        </Stack>
        <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr' }, gap: 1.5 }}>
          <MeasurementInput label="來源用量" data={line.amount} sources={recipe.sources} update={(amount) => editLine(index, { amount })} />
          <MeasurementInput label="整份食譜烹調前重量（g）" data={line.weight_g} sources={recipe.sources} update={(weight_g) => editLine(index, { weight_g })} />
        </Box>
        <Button onClick={() => update({ ...recipe, ingredients: recipe.ingredients.filter((_, item) => index !== item) })}>移除此食材</Button>
      </Stack>
    </Paper>)}
    <Button onClick={() => update({ ...recipe, ingredients: [...recipe.ingredients, newLine()] })}>新增食材列</Button>
    <Typography variant="h6">步驟</Typography>
    {recipe.steps.map((step, index) => <Stack key={step.step_no} direction="row" spacing={1} sx={{ alignItems: 'center' }}>
      <Field label={'步驟 ' + step.step_no} required multiline value={step.instruction}
        update={(instruction) => update({ ...recipe, steps: recipe.steps.map((entry, item) => item === index ? { ...entry, instruction } : entry) })} />
      <Button onClick={() => update({ ...recipe, steps: recipe.steps.filter((_, item) => item !== index)
        .map((entry, item) => ({ ...entry, step_no: item + 1 })) })}>移除</Button>
    </Stack>)}
    <Button onClick={() => update({ ...recipe, steps: [...recipe.steps, { step_no: recipe.steps.length + 1, instruction: '' }] })}>新增步驟</Button>
    <AllergenInput value={recipe.declared_allergen_ids} update={(declared_allergen_ids) => update({ ...recipe, declared_allergen_ids })} />
  </Stack>
}

function taipeiInput(utc: string | null) {
  if (!utc || Number.isNaN(Date.parse(utc))) return null
  return new Date(Date.parse(utc) + 8 * 3_600_000).toISOString().slice(0, 19)
}

function utcFromTaipei(value: string) {
  if (!value) return null
  const epoch = Date.parse(value + '+08:00')
  return Number.isNaN(epoch) ? null : new Date(epoch).toISOString().replace('.000Z', 'Z')
}

function PriceFields({ price, update }: { price: Price; update: Update<Price> }) {
  const correction = price.correction_kind === 'quote_correction'
  const updateCondition = (key: 'region_condition' | 'store_condition' | 'promotion_condition') =>
    (value: string) => update({ ...price, [key]: nullable(value) })
  return <Stack spacing={1.5}>
    <Select label="修訂類別" value={price.correction_kind} choices={{ product_binding: '商品對應', quote_correction: '人工價格修訂' }}
      update={(correction_kind) => update(correction_kind === 'product_binding' ? { ...price, correction_kind, price_twd: null, package_mass_g: null,
        observed_at: null, availability: 'unknown' } : { ...price, correction_kind, availability: 'in_stock' })} />
    <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr' }, gap: 1.5 }}>
      <Field label="Food ID" value={price.food_id} required update={(food_id) => update({ ...price, food_id })} />
      <Field label="食材版本識別碼" value={price.ingredient_version_id} required update={(ingredient_version_id) => update({ ...price, ingredient_version_id })} />
      <Select label="價格來源" value={price.provider} choices={{ pxmart: '全聯', uniprosperity: '統一生機', manual_evidence: '人工證據' }}
        update={(provider) => update({ ...price, provider })} />
      <Field label="商品識別碼" value={price.product_id} required update={(product_id) => update({ ...price, product_id })} />
      <Field label="商品名稱" value={price.product_name} required update={(product_name) => update({ ...price, product_name })} />
      <TextField fullWidth size="small" label="參考原始觀察編號" type="number" value={price.snapshot_id ?? ''}
        slotProps={{ htmlInput: { min: 1, max: Number.MAX_SAFE_INTEGER, step: 1 } }}
        onChange={(event) => update({ ...price, snapshot_id: event.target.value ? Number(event.target.value) : null })} />
      {correction && <>
        <Field label="人工核對的價格（NT$）" type="decimal" value={price.price_twd} required
          update={(value) => update({ ...price, price_twd: nullable(value) })} />
        <Field label="單包重量（g）" type="decimal" value={price.package_mass_g} required
          update={(value) => update({ ...price, package_mass_g: nullable(value) })} />
        <Field label="實際觀察時間（台北）" type="datetime-local" value={taipeiInput(price.observed_at)} required
          update={(value) => update({ ...price, observed_at: utcFromTaipei(value) })} />
      </>}
      <Field label="有效起日（台北）" type="datetime-local" value={taipeiInput(price.valid_from)} required={correction}
        update={(value) => update({ ...price, valid_from: utcFromTaipei(value) })} />
      <Field label="有效迄日（台北）" type="datetime-local" value={taipeiInput(price.valid_until)} required={correction}
        update={(value) => update({ ...price, valid_until: utcFromTaipei(value) })} />
      <Field label="適用地區" value={price.region_condition} update={updateCondition('region_condition')} />
      <Field label="適用店別" value={price.store_condition} update={updateCondition('store_condition')} />
      <Field label="促銷條件" value={price.promotion_condition} update={updateCondition('promotion_condition')} />
    </Box>
    {correction && <Alert severity="info">價格修訂代表有證據支持的有貨觀察。請先核對售價、單包公克數與效期。</Alert>}
  </Stack>
}

export function EditFields({ domain, content, update }: {
  domain: EditableDomain; content: Content; update: React.Dispatch<React.SetStateAction<Content>>
}) {
  function remove(artifactId: string) {
    const keep = (ids: string[]) => ids.filter((id) => id !== artifactId)
    update((current) => {
      const sources = current.sources.filter((source) => source.artifact_id !== artifactId)
      if (!('logical_recipe_id' in current)) return { ...current, sources }
      return { ...current, sources, servings: { ...current.servings, evidence_ids: keep(current.servings.evidence_ids) },
        ingredients: current.ingredients.map((line) => ({ ...line,
          amount: { ...line.amount, evidence_ids: keep(line.amount.evidence_ids) },
          weight_g: { ...line.weight_g, evidence_ids: keep(line.weight_g.evidence_ids) },
        })) }
    })
  }
  return <Stack spacing={2}>
    {'logical_recipe_id' in content ? <RecipeFields recipe={content} update={update} /> :
      'canonical_name' in content ? <IngredientFields content={content} update={update} /> :
        <PriceFields price={content} update={update} />}
    <Divider />
    <SourceInputs domain={domain} sources={content.sources}
      append={(source) => update((current) => ({ ...current, sources: [...current.sources, source] }))} remove={remove} />
  </Stack>
}

function IngredientFields({ content, update }: { content: Ingredient; update: Update<Ingredient> }) {
  return <Stack spacing={1.5}>
    {content.food_id && <Field label="Food ID" value={content.food_id} readOnly />}
    <Field label="食材名稱" value={content.canonical_name} required update={(canonical_name) => update({ ...content, canonical_name })} />
    <Field label="食材別名" multiline value={content.aliases.join('\n')} update={(input) => update({ ...content, aliases: input.split('\n') })}
      hint="每行一個別名。" />
    <AllergenInput value={content.declared_allergen_ids} update={(declared_allergen_ids) => update({ ...content, declared_allergen_ids })} />
  </Stack>
}
