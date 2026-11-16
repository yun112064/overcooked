import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { MemoryRouter } from 'react-router'
import { describe, expect, it, vi } from 'vitest'
import type { Member, MemberOptions } from '../../api/types'
import { IngredientPicker } from './IngredientPicker'
import { MemberDetail } from './MemberDetail'
import { MemberForm } from './MemberForm'
import { backendToday } from './memberRules'
import { memberTargetQuery, teamStatusQuery, type MemberTarget, type TeamStatus } from './queries'
import { RosterPanel } from './RosterPanel'
import { SettingsPanel } from './SettingsPanel'

// 景深設定讀 localStorage；這裡沒有瀏覽器，換成固定值
vi.mock('../../depth/DepthProvider', () => ({ useDepth: () => ({ mode: 'static', setMode: () => {} }) }))

const noop = () => {}

function client(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } })
}

/** 畫出來的 HTML（沒有瀏覽器，只看第一次畫出的內容）。 */
function html(node: ReactElement, queryClient: QueryClient = client()): string {
  return renderToStaticMarkup(
    <QueryClientProvider client={queryClient}><MemoryRouter>{node}</MemoryRouter></QueryClientProvider>,
  )
}
const text = (markup: string) => markup.replace(/<[^>]+>/g, '')

const OPTIONS: MemberOptions = {
  rule_version: 'hpa-dri8-mvp-0.4.1',
  portions: ['0.7', '0.85', '1', '1.15', '1.3'],
  activity_categories: ['sedentary', 'low_active', 'active', 'highly_active'],
  goals: ['general', 'maintain', 'muscle_gain', 'fat_loss'],
  adult_goals_min_age_years: 19,
  minor_goals: ['general'],
}

const ming: Member = {
  id: 'm1', display_name: '阿明', birth_date: '1984-05-20', age_years: 42, equation_sex: 'M', weight_kg: '72',
  activity_category: 'low_active', goal: 'maintain', portion: '1.15',
  allergens: [], avoided: [{ ingredient_id: 'F9', name: '茄子' }], version: 2, updated_at: '2026-11-01T00:00:00Z',
}
const hui: Member = {
  ...ming, id: 'm2', display_name: '小惠', equation_sex: 'F', age_years: 40, weight_kg: '55', activity_category: 'active',
  goal: 'general', portion: '1', allergens: [{ ingredient_id: 'F1', name: '花生' }, { ingredient_id: 'F2', name: null }], avoided: [],
}

function yearsAgo(years: number): string {
  const today = backendToday()
  return `${Number(today.slice(0, 4)) - years}${today.slice(4)}`
}

describe('RosterPanel', () => {
  it('統計和舊前端的統計列相同', () => {
    const out = text(html(<RosterPanel members={[ming, hui]} unavailable={new Set()} onOpen={noop} onAdd={noop} onClose={noop} />))
    expect(out).toContain('成員2 人')
    expect(out).toContain('合計食量2.15 n全員一起用餐時的份數')
    expect(out).toContain('有過敏1 人共 2 種食材')
    expect(out).toContain('有不吃1 人共 1 種食材')
  })

  it('每位成員一顆按鈕；每日目標算不出來的人用鉛筆底線標出來', () => {
    const markup = html(<RosterPanel members={[ming, hui]} unavailable={new Set(['m2'])} onOpen={noop} onAdd={noop} onClose={noop} />)
    expect(text(markup)).toContain('阿明42 歲・活動量稍低')
    expect(text(markup)).toContain('小惠40 歲・活動量適度・目標算不出來')
    expect(markup).toMatch(/<span class="[^"]*pencil[^"]*">目標算不出來<\/span>/)
    expect(markup.match(/<button/g)?.length).toBe(1 /* 關閉 */ + 2 /* 成員 */ + 1 /* 新增 */)
  })

  it('食量讀不懂時合計寫未知，不寫 0', () => {
    const out = text(html(
      <RosterPanel members={[{ ...ming, portion: '' }]} unavailable={new Set()} onOpen={noop} onAdd={noop} onClose={noop} />,
    ))
    expect(out).toContain('合計食量未知')
  })
})

describe('MemberDetail', () => {
  const detail = (member: Member, queryClient?: QueryClient) =>
    html(<MemberDetail member={member} onEdit={noop} onDelete={noop} onClose={noop} />, queryClient)

  it('和舊前端的成員卡片同樣的內容', () => {
    const out = text(detail(hui))
    expect(out).toContain('小惠')
    expect(out).toContain('40 歲・女・55 公斤')
    expect(out).toContain('活動量：適度（PAL 1.7）')
    expect(out).toContain('飲食目標一般餐食')
    expect(out).toContain('食量1n')
    expect(out).toContain('過敏・花生')
    // 字典裡沒有名稱的食材顯示代碼
    expect(out).toContain('過敏・F2')
    expect(out).toContain('不吃沒有登記')
    expect(out).toContain('編輯')
    expect(out).toContain('刪除')
  })

  it('每日基準還在讀時寫出來，不先顯示數字', () => {
    const out = text(detail(ming))
    expect(out).toContain('正在算每日基準…')
    expect(out).not.toContain('大卡')
  })

  it('讀到每日基準：照後端的數字顯示', () => {
    const queryClient = client()
    const target: MemberTarget = {
      member_id: 'm1', rule_version: 'r', approved_for_adoption: true,
      daily_reference: { energy_kcal: '2139.0', protein_g: '79.2' },
    }
    queryClient.setQueryData(memberTargetQuery(ming).queryKey, target)
    const out = text(detail(ming, queryClient))
    expect(out).toContain('熱量2,139 大卡')
    expect(out).toContain('蛋白質79.2 公克')
  })

  it('後端沒給數字就是未知，不是 0', () => {
    const queryClient = client()
    const target: MemberTarget = {
      member_id: 'm1', rule_version: 'r', approved_for_adoption: true,
      daily_reference: { energy_kcal: null, protein_g: '79.2' },
    }
    queryClient.setQueryData(memberTargetQuery(ming).queryKey, target)
    const out = text(detail(ming, queryClient))
    expect(out).toContain('熱量未知')
    expect(out).not.toContain('0 大卡')
  })

  it('每日基準的快取跟著成員版本走：改過資料就不會沿用舊的數字', () => {
    expect(memberTargetQuery(ming).queryKey).not.toEqual(memberTargetQuery({ ...ming, version: 3 }).queryKey)
  })
})

describe('MemberForm', () => {
  const form = (member: Member | null) =>
    html(<MemberForm member={member} options={OPTIONS} onClose={noop} onSaved={noop} />)

  it('新增：九個欄位都在，選項來自後端', () => {
    const markup = form(null)
    const out = text(markup)
    expect(out).toContain('新增成員')
    for (const label of ['成員名稱', '出生日期', '公式性別', '體重（公斤）', '活動量', '飲食目標', '相對食量 n', '過敏食材', '禁吃食材']) {
      expect(out).toContain(label)
    }
    expect(out).toContain('1n（標準成人一份）')
    expect(out).toContain('稍低（PAL 1.5）')
    expect(out).toContain('減脂')
    expect(markup).toContain(`max="${backendToday()}"`)
    expect(out).toContain('儲存')
    expect(out).toContain('取消')
  })

  it('編輯：標題不同，帶入現有的食材', () => {
    const out = text(form(hui))
    expect(out).toContain('編輯成員')
    expect(out).toContain('花生')
  })

  it('未滿 19 歲：成人目標不能選，並說明原因', () => {
    const markup = form({ ...hui, birth_date: yearsAgo(5), age_years: 5 })
    expect(text(markup)).toContain('目前 5 歲')
    expect(text(markup)).toContain('未滿 19 歲只提供一般餐食')
    expect(markup).toMatch(/<option value="fat_loss" disabled="">/)
    expect(markup).toMatch(/<option value="maintain" disabled="">/)
    expect(markup).not.toMatch(/<option value="general" disabled/)
  })

  it('成年人：所有目標都能選', () => {
    const markup = form({ ...hui, birth_date: yearsAgo(40) })
    expect(text(markup)).toContain('目前 40 歲')
    expect(markup).not.toMatch(/<option value="[a-z_]+" disabled/)
    expect(text(markup)).not.toContain('只提供一般餐食')
  })

  it('所有輸入元件都有對應的標籤', () => {
    const markup = form(null)
    const ids = [...markup.matchAll(/<(?:input|select)[^>]* id="([^"]+)"/g)].map((m) => m[1])
    expect(ids).toHaveLength(9)
    for (const id of ids) expect(markup).toContain(`for="${id}"`)
  })
})

describe('IngredientPicker', () => {
  it('已選的食材各有一顆移除按鈕；還沒打字時提示怎麼搜尋', () => {
    const markup = html(
      <IngredientPicker
        label="過敏食材" onChange={noop}
        value={[{ ingredient_id: 'F1', name: '花生' }, { ingredient_id: 'F2', name: null }]}
      />,
    )
    expect(markup).toContain('aria-label="移除 花生"')
    expect(markup).toContain('aria-label="移除 F2"')
    expect(markup).toContain('role="combobox"')
    expect(text(markup)).toContain('輸入名稱搜尋')
  })

  it('錯誤以文字顯示，並和輸入框連在一起', () => {
    const markup = html(<IngredientPicker label="禁吃食材" value={[]} onChange={noop} error="找不到這個食材" />)
    expect(text(markup)).toContain('找不到這個食材')
    expect(markup).toContain('aria-invalid="true"')
    const hintId = /aria-describedby="([^"]+)"/.exec(markup)?.[1]
    expect(hintId).toBeTruthy()
    expect(markup).toContain(`id="${hintId}"`)
  })
})

describe('SettingsPanel', () => {
  const settings = (team?: TeamStatus) => {
    const queryClient = client()
    if (team) queryClient.setQueryData(teamStatusQuery.queryKey, team)
    return html(<SettingsPanel householdName="林家廚房" onClose={noop} />, queryClient)
  }

  it('家庭名稱、景深三段（目前的那一段選起來）、換家與登出', () => {
    const markup = settings()
    const out = text(markup)
    expect(out).toContain('目前的家林家廚房')
    expect(out).toContain('沉浸')
    expect(out).toContain('靜態景深')
    expect(out).toContain('關閉')
    expect(markup).toMatch(/<input type="radio" name="depth-mode" checked="" value="static"\/>/)
    expect(markup.match(/checked=""/g)).toHaveLength(1)
    expect(out).toContain('換一個家')
    expect(out).toContain('登出')
    expect(out).toContain('正在看服務狀態…')
  })

  it('服務狀態用和舊前端相同的名稱與字樣；知識離線時說明菜單照常生成', () => {
    const out = text(settings({
      recognition: { status: 'online', reason: null },
      knowledge: { status: 'offline', reason: 'UNREACHABLE' },
      assistant: { status: 'not_configured', reason: null },
    }))
    expect(out).toContain('蔬菜辨識在線')
    expect(out).toContain('營養知識離線')
    expect(out).toContain('飲食小幫手未設定')
    expect(out).toContain('菜單照常生成，知識說明之後可單獨重試。')
  })

  it('都在線時不多說；後端沒回小幫手那一項就不顯示', () => {
    const out = text(settings({ recognition: { status: 'online', reason: null }, knowledge: { status: 'online', reason: null } }))
    expect(out).not.toContain('菜單照常生成')
    expect(out).not.toContain('飲食小幫手')
  })
})
