import { useState } from 'react'
import { Alert, Box, Button, Chip, MenuItem, Paper, Stack, TextField, Typography } from '@mui/material'
import { contentName, domains, managementError, proposalStates, time } from '../api/management'
import type { Capabilities, Collection, EditableDomain, Observation, Pins, Price, Proposal, ProposalState } from '../api/management'
import { LegacyCatalog, Publications } from '../components/management/Catalog'
import type { EditSelection } from '../components/management/Catalog'
import { blankContent } from '../components/management/content'
import { DataRegistry, Operations, PriceObservations } from '../components/management/Monitoring'
import type { PriceLookup } from '../components/management/Monitoring'
import { DraftEditor, ProposalDetail } from '../components/management/Proposal'
import { ReadState, ResultPage } from '../components/management/shared'
import { useManagementCommand, useManagementRead } from '../components/management/hooks'

type View = EditableDomain | 'operations' | 'registry'
const areas: Record<View, string> = {
  recipe: '食譜與 AI 候選', ingredient: '食材', price: '價格管理', operations: '任務與連線', registry: '資料清冊',
}

function Dashboard({ capabilities }: { capabilities: Capabilities }) {
  const permissions = capabilities.permissions
  const [selected, setSelected] = useState<View>()
  const visible = (Object.keys(areas) as View[]).filter((view) => permissions.includes(view + '.read'))
  const active = selected && visible.includes(selected) ? selected : visible[0]
  return <Stack spacing={3}>
    <Box>
      <Typography variant="h4" component="h1">資料管理</Typography>
      <Typography color="text.secondary">檢查來源、修訂候選、審核內容與追蹤資料發布。</Typography>
    </Box>
    <Stack component="nav" aria-label="管理分類" direction="row" spacing={1} useFlexGap sx={{ flexWrap: 'wrap' }}>
      {visible.map((view) => <Button key={view} onClick={() => setSelected(view)} variant={active === view ? 'contained' : 'outlined'}
        aria-pressed={active === view}>{areas[view]}</Button>)}
    </Stack>
    {!active ? <Alert severity="info">目前帳號沒有任何資料管理分類的查閱權限。</Alert> :
      active === 'operations' ? <Operations /> :
        active === 'registry' ? <DataRegistry /> : <DomainView key={active} domain={active} />}
  </Stack>
}

type DomainMode = 'proposals' | 'publications' | 'legacy' | 'observations'
type EditRequest = EditSelection & { proposal?: Proposal }
const modes: Record<DomainMode, string> = { proposals: '提案與審核', publications: '發布版本', legacy: '舊版目錄', observations: '原始價格觀察' }

function DomainView({ domain }: { domain: EditableDomain }) {
  const caps = useManagementRead<Capabilities>('/capabilities', { domain }, true, 30_000)
  return <ReadState query={caps}>{(data) => data.permissions.includes(domain + '.read') ?
    <DomainWorkspace domain={domain} caps={data} /> : <Alert severity="warning">目前帳號沒有此資料分類的查閱權限。</Alert>
  }</ReadState>
}

function DomainWorkspace({ domain, caps }: { domain: EditableDomain; caps: Capabilities }) {
  const [mode, setMode] = useState<DomainMode>('proposals')
  const [editor, setEditor] = useState<EditRequest>()
  const [id, setId] = useState('')
  const permissions = caps.permissions
  const canEdit = permissions.includes(domain + '.edit')
  const available = (Object.keys(modes) as DomainMode[]).filter((item) =>
    item === 'legacy' ? domain !== 'price' : item === 'observations' ? domain === 'price' : true)
  function open(input: EditRequest) {
    setEditor(input)
  }
  function showProposal(proposalId: string) {
    setEditor(undefined)
    setId(proposalId)
    setMode('proposals')
  }
  function fromObservation(filters: PriceLookup, record: Observation) {
    const draft = blankContent('price') as Price
    const provider = record.provider
    if (provider !== 'pxmart' && provider !== 'uniprosperity' && provider !== 'manual_evidence') return
    open({ initial: { ...draft, food_id: filters.food_id, snapshot_id: record.snapshot_id, provider,
      product_id: record.product_id ?? '', product_name: record.product_name ?? '' },
      context: { legacy_recipe_release_id: filters.recipe_release_id } })
  }
  if (editor && canEdit) return <DraftEditor domain={domain} {...editor} close={() => setEditor(undefined)} saved={showProposal} />
  return <Stack spacing={2}>
    <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: 'wrap' }}>
      {available.map((item) => <Button key={item} onClick={() => setMode(item)} variant={mode === item ? 'outlined' : 'text'}
        aria-pressed={mode === item}>{modes[item]}</Button>)}
    </Stack>
    {mode === 'legacy' && domain !== 'price' && <LegacyCatalog domain={domain} canEdit={canEdit} select={open} />}
    {mode === 'publications' && <Publications domain={domain} canEdit={canEdit} canActivate={permissions.includes('price.activate')} select={open} />}
    {mode === 'observations' && <PriceObservations draft={canEdit ? fromObservation : undefined} />}
    {mode === 'proposals' && <>
      {canEdit && <Stack direction="row" spacing={1}>
        <Button variant="outlined" onClick={() => open({ initial: blankContent(domain), context: {} })}>新增{domains[domain]}草稿</Button>
      </Stack>}
      {domain === 'recipe' && canEdit && <CandidateUpload onCreated={showProposal} />}
      {id ? <>
        <Button onClick={() => setId('')} sx={{ alignSelf: 'start' }}>返回提案列表</Button>
        <ProposalDetail key={id} id={id} permissions={permissions} actor={caps.user_id}
          edit={(proposal) => open({ initial: proposal.content, context: {}, proposal })} />
      </> : <ProposalList domain={domain} select={setId} />}
    </>}
  </Stack>
}

function ProposalList({ domain, select }: { domain: EditableDomain; select: (id: string) => void }) {
  const [state, setState] = useState<ProposalState | ''>('')
  const [cursor, setCursor] = useState<string>()
  const query = useManagementRead<Collection<Proposal>>('/proposals', { domain, state: state || undefined, cursor })
  return <Stack spacing={2}>
    <TextField select label="提案狀態" size="small" value={state} onChange={(event) => {
      setState(event.target.value as ProposalState | '')
      setCursor(undefined)
    }}>
      <MenuItem value="">全部狀態</MenuItem>
      {Object.entries(proposalStates).map(([status, label]) => <MenuItem key={status} value={status}>{label}</MenuItem>)}
    </TextField>
    <ReadState query={query}>{(data) => <ResultPage empty={!data.items.length} next={data.next_cursor} advance={setCursor}
      reset={cursor ? () => setCursor(undefined) : undefined}>
      {data.items.map((item) => <Paper key={item.proposal_id} variant="outlined" sx={{ p: 1.5 }}>
        <Stack spacing={1}>
          <Typography variant="subtitle1">{contentName(item.content)}</Typography>
          <Stack direction="row" spacing={1} useFlexGap sx={{ alignItems: 'center', flexWrap: 'wrap' }}>
            <Chip size="small" label={proposalStates[item.status]} />
            {item.content.sources.some((source) => source.origin_kind === 'ai_output') && <Chip label="AI 候選" size="small" />}
            <Typography variant="body2">第 {item.revision} 版 · {time(item.updated_at)}</Typography>
          </Stack>
          <Button onClick={() => select(item.proposal_id)} sx={{ alignSelf: 'start' }}>查看提案</Button>
        </Stack>
      </Paper>)}
    </ResultPage>}</ReadState>
  </Stack>
}

function CandidateUpload({ onCreated }: { onCreated: (id: string) => void }) {
  const [reason, setReason] = useState('')
  const [legacy, setLegacy] = useState('')
  const [selected, setSelected] = useState<{ name: string; contents: string }>()
  const [fileError, setFileError] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const pins = useManagementRead<Pins>('/draft-context', { domain: 'recipe', legacy_recipe_release_id: legacy.trim() || undefined }, expanded)
  const upload = useManagementCommand<{ proposal: Proposal }>('/recipe-candidates/fixtures', (data) => {
    setExpanded(false)
    setSelected(undefined)
    onCreated(data.proposal.proposal_id)
  })
  return <Stack spacing={1}>
    <Button onClick={() => setExpanded(!expanded)} sx={{ alignSelf: 'start' }}>{expanded ? '收合匯入' : '匯入 AI 候選樣本'}</Button>
    {expanded && <Paper variant="outlined" sx={{ p: 1.5 }}><Stack spacing={1.5}>
      <Alert severity="info">可匯入已備妥的 JSON 樣本，建立待查證的食譜草稿。外部模型執行停用。</Alert>
      <Button component="label" variant="outlined" sx={{ alignSelf: 'start' }}>選擇候選樣本
        <input type="file" accept=".json,application/json" hidden onChange={async (event) => {
          const file = event.target.files?.[0]
          event.target.value = ''
          if (!file) return
          try {
            if (file.size > 256 * 1024) throw new Error('fixture too large')
            const contents = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(await file.arrayBuffer())
            setSelected({ name: file.name, contents })
            setFileError(false)
          } catch {
            setSelected(undefined)
            setFileError(true)
          }
        }} />
      </Button>
      {selected && <Typography variant="body2">{selected.name}</Typography>}
      {fileError && <Alert severity="warning">請使用有效的 UTF-8 檔案，大小上限為 256 KiB。</Alert>}
      <TextField size="small" label="舊版食譜資料版本識別碼（選填）" value={legacy} onChange={(event) => setLegacy(event.target.value)} />
      <TextField size="small" label="匯入原因" value={reason} onChange={(event) => setReason(event.target.value)} />
      {pins.isError && <Alert severity="warning">{managementError(pins.error)}</Alert>}
      {upload.isError && <Alert severity="warning">{managementError(upload.error, 'AI 候選匯入介面目前未就緒。')}</Alert>}
      <Button onClick={() => {
        if (selected && pins.data) upload.mutate({ fixture_json: selected.contents, dependency_pins: pins.data, reason: reason.trim() })
      }} disabled={!selected || !reason.trim() || !pins.data || upload.isPending} variant="contained" sx={{ alignSelf: 'start' }}>建立候選草稿</Button>
    </Stack></Paper>}
  </Stack>
}

export default function ManagementPage() {
  const capabilities = useManagementRead<Capabilities>('/capabilities', undefined, true, 30_000)
  return <ReadState query={capabilities}>{(data) => <Dashboard capabilities={data} />}</ReadState>
}
