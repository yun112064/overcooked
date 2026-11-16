import { renderToStaticMarkup } from 'react-dom/server'
import { MemoryRouter } from 'react-router'
import { describe, expect, it } from 'vitest'
import type { CatalogDish, KnowledgeTips, MealRequest, NutritionPreview } from '../../api/types'
import { DetailPage, ListPage, TrayPicks } from './CatalogPages'
import { NutritionPage, TrayResult } from './ResultPages'
import { TipsPanel } from './ResultPanels'
import { NO_FILTERS } from './bookLogic'
import type { MenuPreview } from './useMenuPreview'

const html = (node: React.ReactElement) => renderToStaticMarkup(node)
/** 只看畫出來的文字（去掉標籤），確認使用者讀到什麼。 */
const text = (node: React.ReactElement) => html(node).replace(/<[^>]+>/g, '')
const noop = () => {}

function dish(patch: Partial<CatalogDish> & { dish_name: string }): CatalogDish {
  return {
    standard_dish_id: `D-${patch.dish_name}`, cuisine: '台式', category: '主菜', staple_kind: null, meal_type: '正餐',
    main_ingredients: [], ingredients: [], conflicts: [], ...patch,
  }
}

const DISHES = [
  dish({ dish_name: '番茄炒蛋', ingredients: ['番茄', '雞蛋'] }),
  dish({ dish_name: '蒜蓉蒸蝦', conflicts: ['白蝦'], ingredients: ['白蝦', '蒜頭'] }),
  ...Array.from({ length: 21 }, (_, i) => dish({ dish_name: `菜${i + 1}` })),
]

function list(props: Partial<Parameters<typeof ListPage>[0]> = {}) {
  return (
    <ListPage
      loading={false} error={null} onRetry={noop} retrying={false}
      categories={[{ value: '主菜', count: 23 }]} inlineCategories={false} filters={NO_FILTERS}
      onToggleFilter={noop} onAllCategories={noop} onClearFilters={noop} onOpenFilters={noop}
      keyword="" onKeyword={noop} shown={DISHES} page={0} onPage={noop}
      chosen={['番茄炒蛋']} viewingId={null} onView={noop} onToggle={noop} readOnly={false}
      {...props}
    />
  )
}

describe('ListPage', () => {
  it('一頁只列 10 道，頁腳寫第幾頁', () => {
    const markup = html(list())
    expect(markup.match(/<li /g)).toHaveLength(10)
    expect(text(list())).toContain('符合 23 道・已選 1／10 道')
    expect(text(list())).toContain('第 1／3 頁')
    expect(text(list({ page: 2 }))).toContain('第 3／3 頁')
  })

  it('含不能吃的食材的菜：勾選框停用，原因寫成文字', () => {
    const markup = html(list())
    expect(markup).toMatch(/<input[^>]*aria-label="蒜蓉蒸蝦，夾進托盤"[^>]*disabled/)
    expect(markup).not.toMatch(/<input[^>]*aria-label="番茄炒蛋，夾進托盤"[^>]*disabled/)
    expect(text(list())).toContain('不能選：含白蝦')
  })

  it('已經放滿 10 道時，沒選的不能再勾，已選的還能拿掉', () => {
    const chosen = DISHES.slice(2, 12).map((d) => d.dish_name)
    const markup = html(list({ chosen }))
    expect(markup).toMatch(/<input[^>]*aria-label="番茄炒蛋，夾進托盤"[^>]*disabled/)
    expect(markup).not.toMatch(/<input[^>]*aria-label="菜1，夾進托盤"[^>]*disabled/)
  })

  it('書籤看不到的版面，頁首多一排類型按鈕；看得到就不重複', () => {
    expect(html(list())).not.toContain('aria-label="類型"')
    const markup = html(list({ inlineCategories: true, filters: { ...NO_FILTERS, category: ['主菜'] } }))
    expect(markup).toContain('aria-label="類型"')
    expect(markup).toMatch(/<button[^>]*aria-pressed="false"[^>]*>全部/)
    expect(markup).toMatch(/<button[^>]*aria-pressed="true"[^>]*>主菜/)
  })

  it('沒有符合的菜時有文字說明；有勾分類才出現「清除分類」', () => {
    expect(text(list({ shown: [] }))).toContain('沒有符合的菜')
    expect(text(list())).not.toContain('清除分類')
    expect(text(list({ filters: { ...NO_FILTERS, cuisine: ['台式'] } }))).toContain('清除分類')
  })
})

describe('ListPage（生成中或已採用）', () => {
  it('勾選框全部停用，而且用文字說明只能看', () => {
    const markup = html(list({ readOnly: true }))
    expect(markup.match(/<input[^>]*type="checkbox"[^>]*>/g)!.every((input) => input.includes('disabled'))).toBe(true)
    expect(text(list({ readOnly: true }))).toContain('只能看，不能改')
    expect(text(list())).not.toContain('只能看，不能改')
  })
})

describe('DetailPage', () => {
  const props = { chosenCount: 1, readOnly: false, onToggle: noop, stapleName: '白飯' }

  it('還沒點菜時是說明', () => {
    expect(text(<DetailPage dish={null} checked={false} {...props} />)).toContain('沒挑飯、麵類的菜時會配一份白飯')
  })

  it('列出食材；不假裝有單一道菜的營養數字', () => {
    const page = text(<DetailPage dish={DISHES[0]} checked={false} {...props} />)
    expect(page).toContain('番茄、雞蛋')
    expect(page).toContain('食譜庫的清單沒有單一道菜的營養數字')
    expect(page).not.toMatch(/\d+\s*kcal/)
  })

  it('含不能吃的食材：寫出是哪些，按鈕停用', () => {
    const markup = html(<DetailPage dish={DISHES[1]} checked={false} {...props} />)
    expect(markup).toContain('含過敏或不吃的食材：白蝦')
    expect(markup).toMatch(/<button[^>]*disabled[^>]*>夾進托盤/)
  })

  it('已採用或生成中（唯讀）不能改', () => {
    expect(html(<DetailPage dish={DISHES[0]} checked {...props} readOnly />)).toMatch(/<button[^>]*disabled/)
  })
})

describe('TrayPicks', () => {
  it('空的時候是提示，確定按鈕停用', () => {
    const markup = html(<TrayPicks chosen={[]} conflicted={new Set()} readOnly={false} disabled={false} saving={false} onRemove={noop} onConfirm={noop} />)
    expect(markup).toContain('勾選想吃的菜（最多 10 道）')
    expect(markup).toMatch(/<button[^>]*disabled[^>]*>確定（0 道）/)
  })

  it('列出已選的菜，每張卡都能拿出來', () => {
    const markup = html(<TrayPicks chosen={['番茄炒蛋', '菜1']} conflicted={new Set()} readOnly={false} disabled={false} saving={false} onRemove={noop} onConfirm={noop} />)
    expect(markup).toContain('aria-label="把番茄炒蛋拿出托盤"')
    expect(markup).toContain('確定（2 道）')
  })
})

/** 只填測試會用到的欄位。 */
function previewOf(patch: Record<string, unknown>): MenuPreview {
  return {
    available: true, selected: [], adopted: null, passed: false, showActions: false, canAdopt: false, canReject: false,
    adopting: false, rejecting: false, adopt: noop, reject: noop, adoptError: null, rejectError: null,
    menu: { data: undefined, isFetching: false, isError: false },
    nutrition: { data: undefined, isFetching: false, isError: false },
    tips: { data: undefined, isFetching: false, isError: false },
    ...patch,
  } as unknown as MenuPreview
}

const ROW = { draft: { staple: '白飯' }, items: [{}, {}] } as unknown as MealRequest

describe('NutritionPage', () => {
  const incomplete: NutritionPreview = {
    status: 'calculation_incomplete', status_message: '有食材缺少營養資料，無法計算。', dish_portions: null, staple: null,
    targets: { energy_kcal: '2000', protein_g: '80' }, input_issues: [], adoption_blocked: true, final_food_ids: [],
    nutrition_passed: false, nutrition: { totals: { energy_kcal: null, protein_g: '70' }, missing: ['energy_kcal'] },
    nutrition_all: {
      totals: { energy_kcal: null, protein_g: '70', sugars_g: null },
      known_partial: { energy_kcal: '1500' }, missing_by_nutrient: { energy_kcal: ['鱸魚片'] },
    },
  }

  it('熱量未知：寫「未知」與「計算不完整」，不顯示成 0', () => {
    const page = text(<NutritionPage row={ROW} preview={previewOf({ nutrition: { data: incomplete } })} tools={null} />)
    expect(page).toContain('整餐熱量未知')
    expect(page).toContain('熱量計算不完整')
    expect(page).toContain('未知（已知 1500 kcal，缺：鱸魚片）')
    expect(page).toContain('有食材缺少營養資料，無法計算。')
    expect(page).not.toMatch(/(^|[^\d.])0 kcal/)
    // 蛋白質 70 ÷ 80 = 87.5%，低於 90%
    expect(page).toContain('蛋白質88%不足')
  })

  it('通過：寫出兩項都通過與「符合採用條件。」', () => {
    const passed: NutritionPreview = {
      ...incomplete, status: 'nutrition_passed', adoption_blocked: false, nutrition_passed: true,
      nutrition: { totals: { energy_kcal: '2100', protein_g: '90' }, missing: [] }, nutrition_all: undefined,
    }
    const page = text(<NutritionPage row={ROW} preview={previewOf({ nutrition: { data: passed }, passed: true })} tools={null} />)
    expect(page).toContain('熱量105%通過')
    expect(page).toContain('蛋白質113%通過')
    expect(page).toContain('符合採用條件。')
  })
})

describe('TipsPanel', () => {
  const tips: KnowledgeTips = {
    status: 'available', dropped: [], not_found: [], knowledge_status: null, nutrition_status: null,
    items: [
      { ingredient_id: 'a', ingredient_name: '高麗菜', tip_zh: '高麗菜含有膳食纖維。', citations: [
        { knowledge_id: '1', source_url: 'https://example.com/a', source_label: '健康醫療網', conditions: null }] },
      { ingredient_id: 'b', ingredient_name: null, tip_zh: '第二則。', citations: [
        { knowledge_id: '2', source_url: 'javascript:alert(1)', source_label: 'Hello醫師', conditions: null }] },
    ],
  }
  const ready = { nutrition: { data: {} }, passed: true }

  it('每一則旁邊都寫來源；只有 http(s) 的網址變成連結', () => {
    const markup = html(<TipsPanel preview={previewOf({ ...ready, tips: { data: tips } })} onClose={noop} />)
    expect(markup).toContain('高麗菜含有膳食纖維。')
    expect(markup.match(/來源：/g)).toHaveLength(3)
    expect(markup).toContain('來源：健康醫療網')
    expect(markup).toContain('href="https://example.com/a"')
    expect(markup).toContain('來源：Hello醫師')
    expect(markup).not.toContain('javascript:')
  })

  it('營養沒通過時不提供；服務拿不到時寫原因', () => {
    expect(text(<TipsPanel preview={previewOf({ nutrition: { data: {} }, passed: false })} onClose={noop} />))
      .toContain('只針對通過營養計算的菜單提供')
    const unavailable = { ...tips, status: 'unavailable' as const, reason: 'KNOWLEDGE_UNAVAILABLE', items: [] }
    expect(text(<TipsPanel preview={previewOf({ ...ready, tips: { data: unavailable } })} onClose={noop} />))
      .toContain('食材知識服務暫時無法使用。')
  })
})

describe('TrayResult（已採用）', () => {
  it('採用後托盤上有明顯的「回客廳」，也能看紀錄', () => {
    const preview = { adopted: { id: 'p1', meal_date: '2026-11-16', meal_type: 'dinner' } } as unknown as MenuPreview
    const html = renderToStaticMarkup(<MemoryRouter><TrayResult preview={preview} locked={false} /></MemoryRouter>)
    expect(html).toMatch(/<a[^>]*class="[^"]*trayGo[^"]*"[^>]*href="\/today"[^>]*>回客廳<\/a>|<a[^>]*href="\/today"[^>]*class="[^"]*trayGo[^"]*"[^>]*>回客廳<\/a>/)
    expect(html).toContain('href="/records?tab=adopted&amp;date=2026-11-16"')
    expect(html).not.toContain('採用這份菜單')
  })
})
