import { useState } from 'react'
import { Alert, Box, Button, Chip, Divider, MenuItem, Paper, Stack, TextField, Typography } from '@mui/material'
import { contentName, expectRevision, managementError, originNames, proposalStates, taskStates, time } from '../../api/management'
import type { Binding, Collection, Content, EditableDomain, Entity, Pins, Price, Proposal, Diff, ReviewScope, Verdict, Workflow } from '../../api/management'
import { EditFields } from './EditFields'
import { cleanLists } from './content'
import { PriceObservations } from './Monitoring'
import { PriceBaseVersionSelect, PriceIngredientVersionSelect, PriceReleaseSelect } from './PriceVersions'
import { ReadState } from './shared'
import { useManagementCommand, useManagementRead } from './hooks'

export function ContentPreview({ content }: { content: Content }) {
  return <Stack spacing={1}>
    <Typography variant="h6">{contentName(content)}</Typography>
    {'logical_recipe_id' in content && <>
      <Typography>{content.category} · {content.cuisine} · {content.meal_type}</Typography>
      <Typography>份量原文 {content.servings.raw_text} · 數量 {content.servings.count ?? '未知'} {content.servings.basis === 'batch' ? '批' : '份'}</Typography>
      {content.servings.basis === 'batch' && <Typography>每批份數 {content.servings.portions_per_batch ?? '未知'}</Typography>}
      <Typography sx={{ fontWeight: 'bold' }}>食材與整份烹調前重量</Typography>
      <Box component="ul" sx={{ pl: 3, m: 0 }}>
        {content.ingredients.map((line) => <li key={line.line_id}>
          {line.canonical_name} · {line.weight_g.value === null ? `重量未知（${line.weight_g.unknown_reason ?? '來源未提供'}）` : `${line.weight_g.value} g`}
          <Typography variant="body2" color="text.secondary">
            原文 {line.raw_text} · 用量 {line.amount.value ?? '未知'} {line.unit ?? ''} · Food ID {line.food_id ?? '待確認'}
          </Typography>
        </li>)}
      </Box>
      <Typography sx={{ fontWeight: 'bold' }}>做法</Typography>
      <Box component="ol" sx={{ pl: 3, m: 0 }}>{content.steps.map((step) => <li key={step.step_no}>{step.instruction}</li>)}</Box>
      <Typography variant="body2">營養對應待另外確認。</Typography>
    </>}
    {'aliases' in content && <>
      <Typography>別名 {content.aliases.join('、') || '無別名紀錄'}</Typography>
      <Typography>來源過敏原聲明 {content.declared_allergen_ids === null ? '未知' : content.declared_allergen_ids.join('、') || '明確聲明無過敏原'}</Typography>
    </>}
    {'price_twd' in content && <>
      <Typography>{content.correction_kind === 'quote_correction' ? '人工價格修訂' : '人工商品對應'} · {content.provider}</Typography>
      <Typography>Food ID {content.food_id} · 商品 {content.product_id} · 原始觀察編號 {content.snapshot_id ?? '未知'}</Typography>
      {content.replaces_version_id && <Typography sx={{ overflowWrap: 'anywhere' }}>替換的發布成員 {content.replaces_version_id}</Typography>}
      <Typography>人工價 {content.price_twd === null ? '未知' : `NT$ ${content.price_twd}`} · 單包重量 {content.package_mass_g === null ? '未知' : `${content.package_mass_g} g`}</Typography>
      <Typography>觀察時間 {time(content.observed_at)} · 有效起迄 {time(content.valid_from)} 至 {time(content.valid_until)}</Typography>
      <Typography>地區 {content.region_condition ?? '未知'} · 店別 {content.store_condition ?? '未知'} · 促銷 {content.promotion_condition ?? '未知'}</Typography>
    </>}
    <Typography sx={{ fontWeight: 'bold' }}>來源證據</Typography>
    {content.sources.map((source) => <Typography key={source.artifact_id} variant="body2" sx={{ overflowWrap: 'anywhere' }}>
      {originNames[source.origin_kind]} · {source.artifact_id} · SHA-256 {source.artifact_sha256}
    </Typography>)}
  </Stack>
}

export type ContextSelection = { base_publication_id?: string; ingredient_publication_id?: string; legacy_recipe_release_id?: string }

export function DraftEditor({ domain, initial, proposal, context, close, saved }: {
  domain: EditableDomain; initial: Content; proposal?: Proposal; context?: ContextSelection
  close: () => void; saved: (id: string) => void
}) {
  const [content, setContent] = useState(initial)
  const [reason, setReason] = useState('')
  const [ingredientPublication, setIngredientPublication] = useState(context?.ingredient_publication_id ?? proposal?.dependency_pins.ingredient_publication_id ?? '')
  const [legacyRelease, setLegacyRelease] = useState(context?.legacy_recipe_release_id ?? proposal?.dependency_pins.legacy_recipe_release_id ?? '')
  const [basePublication, setBasePublication] = useState(context?.base_publication_id ?? '')
  const binding = useManagementRead<Binding>('/bindings/price_policy', undefined, domain === 'price' && !proposal)
  const [checked, setChecked] = useState<ContextSelection>(context ?? {})
  const [repin, setRepin] = useState(false)
  const replacing = 'price_twd' in content && Boolean(content.replaces_version_id)
  const needsContext = !proposal || repin
  const contextMatches = (checked.ingredient_publication_id ?? '') === ingredientPublication.trim() &&
    (checked.legacy_recipe_release_id ?? '') === legacyRelease.trim() &&
    (checked.base_publication_id ?? '') === basePublication
  const missingPriceBase = domain === 'price' && !proposal && Boolean(
    binding.isPending || binding.isError || binding.data?.publication_id && !basePublication)
  const contextQuery = useManagementRead<Pins>('/draft-context', { domain, ...checked, proposal_id: proposal?.proposal_id },
    needsContext && contextMatches && (domain !== 'price' || Boolean(checked.ingredient_publication_id && checked.legacy_recipe_release_id)))
  const save = useManagementCommand<Proposal>(proposal ? `/proposals/${proposal.proposal_id}/revisions` : '/proposals', (next) => saved(next.proposal_id))
  const pins = needsContext ? (contextMatches ? contextQuery.data : undefined) : proposal?.dependency_pins
  function checkContext() {
    setRepin(Boolean(proposal))
    setChecked({ ...context, base_publication_id: basePublication || undefined, ingredient_publication_id: ingredientPublication.trim() || undefined,
      legacy_recipe_release_id: legacyRelease.trim() || undefined })
  }
  function submit(event: React.FormEvent) {
    event.preventDefault()
    if (!pins || !reason.trim() || !content.sources.length) return
    const values = cleanLists(content)
    save.mutate({
      domain, dependency_pins: pins, reason: reason.trim(),
      content: 'price_twd' in values ? { ...values, ingredient_publication_id: pins.ingredient_publication_id,
        legacy_recipe_release_id: pins.legacy_recipe_release_id } : values,
      ...(proposal ? expectRevision(proposal) : {}),
    })
  }
  return <Paper variant="outlined" sx={{ p: 2 }}>
    <Box component="form" onSubmit={submit}>
      <Stack spacing={2}>
        <Typography variant="h5">{proposal ? `新增修訂（目前第 ${proposal.revision} 版）` : '建立管理草稿'}</Typography>
        <Alert severity="info">儲存後可進行驗證與送審。每次修訂都會保留版本。</Alert>
        {context?.base_publication_id && <Typography variant="body2" sx={{ overflowWrap: 'anywhere' }}>基礎發布版本 {context.base_publication_id}</Typography>}
        {domain === 'recipe' && <>
          <TextField size="small" label="食材發布版本識別碼" required={Boolean(proposal?.dependency_pins.ingredient_publication_id)} value={ingredientPublication}
            onChange={(event) => {
              setIngredientPublication(event.target.value)
              if (proposal) setRepin(true)
            }} />
          <TextField size="small" label="舊版食譜資料版本識別碼" required={Boolean(proposal?.dependency_pins.legacy_recipe_release_id)} value={legacyRelease}
            slotProps={{ input: { readOnly: Boolean(context?.legacy_recipe_release_id) } }}
            onChange={(event) => {
              setLegacyRelease(event.target.value)
              if (proposal) setRepin(true)
            }} />
          <Button size="small" onClick={checkContext} sx={{ alignSelf: 'start' }}>核對選定的資料版本</Button>
        </>}
        {!proposal && domain === 'price' && <>
          <PriceReleaseSelect value={legacyRelease} locked={Boolean(context?.legacy_recipe_release_id)} change={(id) => {
            setLegacyRelease(id)
            setIngredientPublication('')
            setBasePublication('')
            setContent((current) => 'price_twd' in current ? { ...current, ingredient_version_id: '', food_id: '' } : current)
          }} />
          <PriceIngredientVersionSelect key={legacyRelease} release={legacyRelease} value={ingredientPublication} locked={replacing}
            change={(id) => {
              setIngredientPublication(id)
              setBasePublication('')
              setContent((current) => 'price_twd' in current ? { ...current, ingredient_version_id: '' } : current)
            }} />
          <PriceBaseVersionSelect key={legacyRelease + ingredientPublication} release={legacyRelease} ingredient={ingredientPublication}
            value={basePublication} change={setBasePublication} locked={replacing} />
          {binding.data?.publication_id && <Typography variant="body2" sx={{ overflowWrap: 'anywhere' }}>
            目前啟用價格版 {binding.data.publication_id}
          </Typography>}
          {missingPriceBase && <Alert severity="info">請選擇要新增內容的價格基礎版本。已啟用政策的後續版本須以目前價格版為基礎。</Alert>}
          {binding.isError && <Alert severity="error">{managementError(binding.error, '無法核對目前啟用的價格版本。')}</Alert>}
          <Button size="small" onClick={checkContext} disabled={!legacyRelease || !ingredientPublication}
            sx={{ alignSelf: 'start' }}>核對選定的資料版本</Button>
        </>}
        {needsContext && !contextMatches && <Alert severity="info">請先核對選定的資料版本，再儲存草稿。</Alert>}
        {needsContext && contextQuery.isError && <Alert severity="warning">{managementError(contextQuery.error, '無法核對依賴版本。')}</Alert>}
        {!proposal && !replacing && 'price_twd' in content && pins?.ingredient_publication_id &&
          <IngredientPicker key={pins.ingredient_publication_id} publication={pins.ingredient_publication_id} selected={content}
            apply={(entity) => setContent({ ...content, food_id: entity.logical_id, ingredient_version_id: entity.ref.version_id })} />}
        <EditFields domain={domain} content={content} update={setContent} />
        <TextField size="small" label="修改原因" value={reason} multiline required onChange={(event) => setReason(event.target.value)} />
        {save.isError && <Alert severity="error">{managementError(save.error)}</Alert>}
        <Stack direction="row" spacing={1}>
          <Button type="submit" variant="contained" disabled={!pins || missingPriceBase || !content.sources.length || !reason.trim() || save.isPending}>儲存草稿</Button>
          <Button onClick={close} disabled={save.isPending}>取消</Button>
        </Stack>
      </Stack>
    </Box>
  </Paper>
}

function IngredientPicker({ publication, selected, apply }: { publication: string; selected: Price; apply: (entity: Entity) => void }) {
  const [cursor, setCursor] = useState<string>()
  const entities = useManagementRead<Collection<Entity>>('/entities/ingredient', {
    view_kind: 'publication', view_id: publication, cursor,
  })
  return <ReadState query={entities}>{(data) => <Stack spacing={1}>
    <TextField select size="small" label="從指定發布版本選取食材" value={data.items.some((item) => item.ref.version_id === selected.ingredient_version_id)
      ? selected.ingredient_version_id : ''} onChange={(event) => {
      const entity = data.items.find((item) => item.ref.version_id === event.target.value)
      if (entity) apply(entity)
    }}>
      <MenuItem value="">請選擇</MenuItem>
      {data.items.map((item) => <MenuItem key={item.ref.version_id} value={item.ref.version_id}>
        {contentName(item.content)} · {item.logical_id}
      </MenuItem>)}
    </TextField>
    {data.next_cursor && <Button onClick={() => setCursor(data.next_cursor ?? undefined)}>下一頁食材</Button>}
    {cursor && <Button onClick={() => setCursor(undefined)}>第一頁食材</Button>}
  </Stack>}</ReadState>
}

const scopeNames = { content: '內容審核', source_rights: '來源權利審核' }
const verdictNames = { approve: '核准', request_changes: '要求修改', reject: '退件' }
type Action = 'validate' | 'submit' | 'review' | 'publish' | 'withdraw'
const labels: Record<Action, string> = { validate: '排入驗證', submit: '送審', review: '提交審核決定', publish: '建立發布版本', withdraw: '撤回提案' }

function ProposalActions({ proposal, workflow, permissions, actor }: { proposal: Proposal; workflow: Workflow; permissions: string[]; actor: string }) {
  const { proposal_id, domain, status } = proposal
  const base = `/proposals/${proposal_id}`
  const [reason, setReason] = useState('')
  const self = proposal.contributors.includes(actor)
  const validation = workflow.validation
  const scopes: ReviewScope[] = [
    ...(permissions.includes(domain + '.review') ? ['content' as const] : []),
    ...(permissions.includes('source.rights_review') ? ['source_rights' as const] : []),
  ]
  const [scope, setScope] = useState<ReviewScope>(scopes[0] ?? 'content')
  const [verdict, setVerdict] = useState<Verdict>('approve')
  const validate = useManagementCommand(base + '/validations')
  const submit = useManagementCommand(base + '/submit')
  const review = useManagementCommand(base + '/reviews')
  const publish = useManagementCommand('/publications')
  const withdraw = useManagementCommand(base + '/withdraw')
  const commands = { validate, submit, review, publish, withdraw }
  const isDraft = status === 'draft' || status === 'changes_requested'
  const allow: Record<Action, boolean> = {
    validate: isDraft && permissions.includes(domain + '.submit') && workflow.latest_task?.status !== 'queued' && workflow.latest_task?.status !== 'running',
    submit: isDraft && permissions.includes(domain + '.submit') && validation?.catalog_ready === true,
    review: status === 'submitted' && !self && scopes.length > 0 && validation?.catalog_ready === true,
    publish: status === 'approved' && !self && permissions.includes(domain + '.publish') && validation?.catalog_ready === true &&
      Boolean(workflow.approvals.content && workflow.approvals.source_rights),
    withdraw: ['draft', 'changes_requested', 'submitted', 'approved'].includes(status) && permissions.includes(domain + '.edit'),
  }
  const visibleActions = (Object.keys(allow) as Action[]).filter((action) => allow[action])
  const results = Object.entries(commands)
  const pending = results.some(([, mutation]) => mutation.isPending)
  function run(action: Action) {
    const fields = { ...expectRevision(proposal), reason: reason.trim() }
    if (action === 'review') {
      if (validation) review.mutate({ ...fields, validation_id: validation.validation_id,
        dependency_hash: proposal.dependency_hash, scope, verdict })
    } else if (action === 'publish') {
      if (validation) publish.mutate({
        reason: reason.trim(), domain, base_publication_id: proposal.dependency_pins.base_publication_id,
        proposal_refs: [{ proposal_id, revision: proposal.revision, content_hash: proposal.content_hash,
          validation_id: validation.validation_id, content_approval_id: workflow.approvals.content,
          source_rights_approval_id: workflow.approvals.source_rights }],
      })
    } else commands[action].mutate(fields)
  }
  return <Stack spacing={1.5}>
    <Divider />
    <Typography variant="h6">驗證與審核</Typography>
    {workflow.latest_task && <Typography>最近驗證任務 {taskStates[workflow.latest_task.status] ?? workflow.latest_task.status} · {workflow.latest_task.task_id}</Typography>}
    {(workflow.latest_task?.status === 'queued' || workflow.latest_task?.status === 'running') &&
      <Alert severity="info">驗證已排隊，等待隔離環境的純資料驗證執行器。此頁會更新驗證狀態。</Alert>}
    {validation === null ? <Typography color="text.secondary">目前版本尚無驗證結果。</Typography> :
      <Alert severity={validation.catalog_ready ? 'success' : 'warning'}>
        {validation.catalog_ready ? '上次目錄驗證通過' : '上次驗證有阻擋問題'} · {time(validation.evaluated_at)}
        {validation.issues.map((issue, index) => <Typography key={issue.code + index} variant="body2">{issue.message}{issue.json_pointer && `（${issue.json_pointer}）`}</Typography>)}
      </Alert>}
    <Typography variant="body2">內容審核 {workflow.approvals.content ? '已核准' : '待有效核准'} · 來源權利 {workflow.approvals.source_rights ? '已核准' : '待有效核准'}</Typography>
    {self && status === 'submitted' && <Alert severity="info">請由具備對應權限且未參與編輯的帳號審核。</Alert>}
    {visibleActions.length > 0 && <TextField size="small" label="本次操作原因" value={reason} onChange={(event) => setReason(event.target.value)} />}
    {allow.review && <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1}>
      <TextField select size="small" fullWidth label="審核範圍" value={scope} onChange={(event) => setScope(event.target.value as ReviewScope)}>
        {scopes.map((item) => <MenuItem key={item} value={item}>{scopeNames[item]}</MenuItem>)}
      </TextField>
      <TextField select size="small" fullWidth label="審核決定" value={verdict} onChange={(event) => setVerdict(event.target.value as Verdict)}>
        {(Object.keys(verdictNames) as Verdict[]).map((item) => <MenuItem key={item} value={item}>{verdictNames[item]}</MenuItem>)}
      </TextField>
    </Stack>}
    <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: 'wrap' }}>
      {visibleActions.map((action) => <Button key={action} onClick={() => run(action)} disabled={pending || !reason.trim()}
        variant={action === 'withdraw' ? 'outlined' : 'contained'} color={action === 'withdraw' ? 'warning' : 'primary'}>{labels[action]}</Button>)}
    </Stack>
    {results.map(([action, mutation]) => mutation.isError ?
      <Alert key={action} severity="error">{managementError(mutation.error)}</Alert> : null)}
  </Stack>
}

function Changes({ id, revision }: { id: string; revision: number }) {
  const [cursor, setCursor] = useState<string>()
  const diff = useManagementRead<Diff>(`/proposals/${id}/diff`, { base_revision: revision - 1, target_revision: revision, cursor })
  function value(input: unknown) {
    return input === null ? '未知／空值' : typeof input === 'string' ? input : JSON.stringify(input)
  }
  return <ReadState query={diff}>{(result) => <Stack spacing={1}>
    <Typography sx={{ fontWeight: 'bold' }}>第 {revision - 1} 版至第 {revision} 版的差異</Typography>
    {result.changes.length === 0 && <Typography>此範圍沒有欄位差異。</Typography>}
    {result.changes.map((entry) => <Box key={entry.json_pointer} sx={{ overflowWrap: 'anywhere' }}>
      <Typography variant="body2">{entry.json_pointer}</Typography>
      <Typography variant="body2">原值 {entry.op === 'add' ? '無此欄位' : value(entry.before)}</Typography>
      <Typography variant="body2">新值 {entry.op === 'remove' ? '已移除' : value(entry.after)}</Typography>
    </Box>)}
    {result.next_cursor && <Button onClick={() => setCursor(result.next_cursor ?? undefined)}>下一頁差異</Button>}
  </Stack>}</ReadState>
}

export function ProposalDetail({ id, permissions, actor, edit }: {
  id: string; permissions: string[]; actor: string; edit: (proposal: Proposal) => void
}) {
  const [showChanges, setShowChanges] = useState(false)
  const [showPrice, setShowPrice] = useState(false)
  const query = useManagementRead<Proposal>(`/proposals/${id}`, undefined, true, 12_000)
  const flow = useManagementRead<Workflow>(`/proposals/${id}/workflow`, undefined, true, 8_000)
  return <ReadState query={query}>{(proposal) => <Paper variant="outlined" sx={{ p: 2 }}>
    <Stack spacing={2}>
      <Stack direction="row" spacing={1} sx={{ alignItems: 'center', flexWrap: 'wrap' }}>
        <Chip label={proposalStates[proposal.status]} />
        <Typography variant="body2">第 {proposal.revision} 版 · {time(proposal.updated_at)}</Typography>
      </Stack>
      <ContentPreview content={proposal.content} />
      {proposal.domain === 'recipe' && permissions.includes('artifact.read') && <CandidateEvidence id={id} />}
      {'price_twd' in proposal.content && <>
        <Button onClick={() => setShowPrice(!showPrice)} sx={{ alignSelf: 'start' }}>
          {showPrice ? '收合原始觀察' : '比對同一食材的原始價格觀察'}
        </Button>
        {showPrice && <PriceObservations seed={{ recipe_release_id: proposal.content.legacy_recipe_release_id,
          food_id: proposal.content.food_id, as_of: new Date().toISOString() }} />}
      </>}
      <Stack direction="row" spacing={1}>
        {permissions.includes(proposal.domain + '.edit') && ['draft', 'changes_requested', 'rejected', 'approved'].includes(proposal.status) &&
          <Button variant="outlined" onClick={() => edit(proposal)}>編輯為新修訂</Button>}
        {proposal.revision > 1 && <Button onClick={() => setShowChanges(!showChanges)}>{showChanges ? '隱藏差異' : '檢視與上一版的差異'}</Button>}
      </Stack>
      {showChanges && proposal.revision > 1 && <Changes key={proposal.revision} id={id} revision={proposal.revision} />}
      <ReadState query={flow}>{(workflow) => workflow.revision !== proposal.revision || workflow.content_hash !== proposal.content_hash ?
        <Alert severity="info">正在更新此提案的驗證資料。</Alert> :
        <ProposalActions key={`${proposal.revision}-${proposal.status}`} proposal={proposal} workflow={workflow} permissions={permissions} actor={actor} />}
      </ReadState>
    </Stack>
  </Paper>}</ReadState>
}

type CandidateEvidenceResult = {
  source: { kind: 'ai_fixture' | 'supplied_document'; reference: string; observed_at?: string; source_version?: string;
    ai_metadata?: { model: string; prompt_version: string; rule_version: string } }
  duplicate_suggestions: { recipe_id: string; name: string; kind: string; reasons: string[]; proposal_id?: string }[]
  duplicate_suggestions_truncated: boolean
}
const duplicateReasons: Record<string, string> = {
  EXACT_SOURCE_SHA256: '來源檔相同', NORMALIZED_NAME: '規格化名稱相同',
  DECLARED_ALIAS: '聲明別名相符', SIMILAR_NAME: '名稱相似',
}

function CandidateEvidence({ id }: { id: string }) {
  const [open, setOpen] = useState(false)
  const query = useManagementRead<CandidateEvidenceResult>(`/recipe-candidates/${id}/fixture`)
  return <Stack spacing={1}>
    {query.data?.source.kind === 'ai_fixture' && <Chip label="AI 食譜候選" sx={{ alignSelf: 'start' }} />}
    <Button sx={{ alignSelf: 'start' }} onClick={() => setOpen(!open)}>{open ? '收合候選證據' : '查看候選證據與重複提示'}</Button>
    {open && <ReadState query={query} unavailable="此提案沒有候選匯入紀錄，或候選介面目前未就緒。">{(data) => <Stack spacing={1}>
      <Typography sx={{ overflowWrap: 'anywhere' }}>來源 {data.source.reference} · 觀察時間 {time(data.source.observed_at)}</Typography>
      {data.source.ai_metadata && <Typography variant="body2">
        樣本模型 {data.source.ai_metadata.model} · 提示版本 {data.source.ai_metadata.prompt_version} · 規則版本 {data.source.ai_metadata.rule_version}
      </Typography>}
      <Typography variant="subtitle2">匯入時的重複提示（待人工確認）</Typography>
      {!data.duplicate_suggestions.length && <Typography>匯入時沒有發現相似項目。</Typography>}
      {data.duplicate_suggestions.map((item) => <Typography key={item.kind + item.recipe_id + (item.proposal_id ?? '')} variant="body2">
        {item.name} · {item.recipe_id} · {item.reasons.map((code) => duplicateReasons[code] ?? code).join('、')}
      </Typography>)}
      {data.duplicate_suggestions_truncated && <Alert severity="info">已達提示筆數上限，可能另有相似項目。</Alert>}
    </Stack>}</ReadState>}
  </Stack>
}
