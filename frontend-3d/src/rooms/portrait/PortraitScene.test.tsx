import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { AxiosError, AxiosHeaders } from 'axios'
import { renderToStaticMarkup } from 'react-dom/server'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Me, Member, MemberOptions } from '../../api/types'
import { membersQuery } from '../../session/queries'
import PortraitScene from './PortraitScene'
import { MAX_DRAWN } from './people'
import { memberOptionsQuery, memberTargetQuery } from './queries'

// 景深設定讀 localStorage；這裡沒有瀏覽器，換成固定值
vi.mock('../../depth/DepthProvider', () => ({ useDepth: () => ({ mode: 'static', setMode: () => {} }) }))

const OPTIONS: MemberOptions = {
  rule_version: 'r', portions: ['0.7', '0.85', '1', '1.15', '1.3'],
  activity_categories: ['sedentary', 'low_active', 'active', 'highly_active'],
  goals: ['general', 'maintain', 'muscle_gain', 'fat_loss'], adult_goals_min_age_years: 19, minor_goals: ['general'],
}
const ME: Me = { user: { id: 'u1', email: 'a@example.com' }, current_household: { id: 'h1', name: '林家廚房' } }

function member(id: string, name: string, sex: 'M' | 'F', age: number, extra: Partial<Member> = {}): Member {
  return {
    id, display_name: name, birth_date: '2000-01-01', age_years: age, equation_sex: sex, weight_kg: '60',
    activity_category: 'low_active', goal: 'general', portion: '1', allergens: [], avoided: [], version: 1,
    updated_at: '2026-11-01T00:00:00Z', ...extra,
  }
}
const FAMILY = [
  member('m1', '阿明', 'M', 42), member('m2', '小惠', 'F', 40), member('m3', '小安', 'M', 5),
  member('m4', '阿嬤', 'F', 78, { activity_category: 'sedentary', goal: 'fat_loss' }),
]

/** 後端回 422：這位成員的設定算不出每日目標。 */
function unprocessable(): AxiosError {
  return new AxiosError('422', 'ERR_BAD_REQUEST', undefined, undefined, {
    status: 422, statusText: 'Unprocessable Entity', headers: {}, config: { headers: new AxiosHeaders() },
    data: { error: { code: 'INVALID_INPUT', message: '每日熱量會低於 1200 大卡。', details: { field: 'goal' } } },
  })
}

type Setup = { members?: Member[]; width?: number; height?: number; failingTargets?: string[] }

/** 沒有瀏覽器：只給 Room 需要的畫面大小，看第一次畫出來的 HTML。 */
function render({ members, width = 390, height = 844, failingTargets = [] }: Setup): string {
  vi.stubGlobal('window', { innerWidth: width, innerHeight: height })
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  queryClient.setQueryData(['me'], ME)
  queryClient.setQueryData(memberOptionsQuery.queryKey, OPTIONS)
  if (members) queryClient.setQueryData(membersQuery.queryKey, members)
  for (const m of members ?? []) {
    if (!failingTargets.includes(m.id)) continue
    queryClient.getQueryCache().build(queryClient, { queryKey: memberTargetQuery(m).queryKey }).setState({
      status: 'error', fetchStatus: 'idle', error: unprocessable(), errorUpdatedAt: Date.now(), errorUpdateCount: 1,
    })
  }
  return renderToStaticMarkup(
    <QueryClientProvider client={queryClient}><MemoryRouter><PortraitScene /></MemoryRouter></QueryClientProvider>,
  )
}

const text = (markup: string) => markup.replace(/<[^>]+>/g, '')
/** 物件列（手機）或視覺隱藏的清單（桌機）裡每顆按鈕的文字。 */
function dock(markup: string): string[] {
  const nav = /<nav[^>]*aria-label="全家福裡可以操作的東西"[^>]*>(.*?)<\/nav>/.exec(markup)?.[1] ?? ''
  return [...nav.matchAll(/<button[^>]*>(.*?)<\/button>/g)].map((m) => text(m[1]))
}
const hotspotLabels = (markup: string) => [...markup.matchAll(/<button type="button" aria-label="([^"]+)"/g)].map((m) => m[1])

afterEach(() => vi.unstubAllGlobals())

describe('全家福場景', () => {
  it('每位成員是畫裡的一個人，名字下面寫年齡與活動量', () => {
    const markup = render({ members: FAMILY })
    const labels = hotspotLabels(markup)
    expect(labels).toContain('阿明，42 歲、活動量稍低。看資料')
    expect(labels).toContain('小安，5 歲、活動量稍低。看資料')
    expect(labels).toContain('畫上新成員')
    expect(labels).toContain('看全家名單與統計，目前 4 人')
    expect(labels).toContain('林家廚房的名牌，打開設定')
    // 男、女、小孩、長輩各用自己的蠟筆人物，再加一個虛線的新成員：五張不同的圖
    const figures = [...markup.matchAll(/<img class="[^"]*figure[^"]*"[^>]* src="([^"]+)"/g)].map((m) => m[1])
    expect(figures).toHaveLength(5)
    expect(new Set(figures).size).toBe(5)
  })

  it('手機：物件列列出每位成員、新成員、名單、設定（DESIGN 4.4）', () => {
    const markup = render({ members: FAMILY })
    expect(markup).toContain('data-layout="phone"')
    expect(dock(markup)).toEqual(['阿明', '小惠', '小安', '阿嬤', '＋ 新成員', '全家名單4 人', '設定林家廚房'])
  })

  it('桌機：同一份清單以視覺隱藏的方式提供給鍵盤與讀屏', () => {
    const markup = render({ members: FAMILY, width: 1440, height: 900 })
    expect(markup).toContain('data-layout="wide"')
    expect(markup).toMatch(/<nav class="visually-hidden"/)
    expect(dock(markup)).toHaveLength(7)
  })

  it('後端說每日目標算不出來的人：畫裡用鉛筆底線標出來，物件列也寫', () => {
    const markup = render({ members: FAMILY, failingTargets: ['m4'] })
    expect(hotspotLabels(markup)).toContain('阿嬤，78 歲、活動量低、目標算不出來。看資料')
    expect(markup).toMatch(/<span class="[^"]*pencil[^"]*">目標算不出來<\/span>/)
    expect(dock(markup)).toContain('阿嬤目標算不出來')
    // 其他人不受影響
    expect(dock(markup)).toContain('阿明')
  })

  it('四位以內排一排；五位以上排兩排，後排腳下補一筆草', () => {
    expect(render({ members: FAMILY })).not.toContain('<svg class="_mound')
    const six = [...FAMILY, member('m5', '阿公', 'M', 80), member('m6', '小妹', 'F', 9)]
    const markup = render({ members: six })
    // 6 位＋新成員＝7 格：後排 3、前排 4
    expect(markup.match(/<svg class="[^"]*mound/g)).toHaveLength(3)
  })

  it('還沒有成員：只有虛線人形與引導，沒有名單', () => {
    const markup = render({ members: [] })
    expect(text(markup)).toContain('先認識一下你的家人')
    expect(text(markup)).toContain('新增成員後，規劃餐食時就能選擇誰要用餐，系統會依每個人的需要分配份量。')
    expect(text(markup)).toContain('還沒有人，先畫上第一位吧')
    expect(hotspotLabels(markup)).toContain('畫上新成員')
    expect(dock(markup)).toEqual(['＋ 新成員', '設定林家廚房'])
  })

  it('成員還沒讀到：寫載入中，不先畫空的全家福，也不給新增', () => {
    const markup = render({})
    expect(text(markup)).toContain('正在看畫裡有誰…')
    expect(hotspotLabels(markup)).not.toContain('畫上新成員')
    expect(text(markup)).not.toContain('先認識一下你的家人')
    expect(dock(markup)).toEqual(['設定林家廚房'])
  })

  it('成員多到畫不下：畫裡說還有幾位在名單裡，物件列仍然每一位都有', () => {
    const many = Array.from({ length: MAX_DRAWN + 3 }, (_, i) => member(`x${i}`, `成員${i + 1}`, 'F', 30))
    const markup = render({ members: many })
    expect(text(markup)).toContain(`${MAX_DRAWN + 3} 個人・還有 3 位在名單裡`)
    expect(hotspotLabels(markup).filter((label) => label.endsWith('看資料'))).toHaveLength(MAX_DRAWN)
    expect(dock(markup).filter((label) => label.startsWith('成員'))).toHaveLength(MAX_DRAWN + 3)
  })

  it('導覽停在「家庭」分頁', () => {
    expect(render({ members: FAMILY })).toMatch(/<a[^>]*aria-current="page"[^>]*>.*?家庭/)
  })
})
