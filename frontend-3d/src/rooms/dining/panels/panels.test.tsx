import type { ReactElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { MemoryRouter } from 'react-router'
import { describe, expect, it } from 'vitest'
import type { MealResult, RetryState } from '../../../api/types'
import { adoptionOf, resultFlags, type Adoption } from '../tableModel'
import { dish, mealResult, person, priceItem } from '../testData'
import { DishDetail, StapleDetail } from './DishPanel'
import { FamilyDetail, PersonDetail } from './NutritionPanels'
import { ReceiptDetail } from './ReceiptPanel'
import { StatusDetail } from './StatusPanel'
import { TipsDetail } from './TipsPanel'

const html = (node: ReactElement) => renderToStaticMarkup(<MemoryRouter>{node}</MemoryRouter>)
/** 只看畫出來的文字（去掉標籤），確認使用者讀到什麼。 */
const text = (node: ReactElement) => html(node).replace(/<[^>]+>/g, '')
const failedRetry: RetryState = {
  job_id: 'j', status: 'failed', stage: null, attempts: 2, error: { code: 'X', message: '來源沒有回應' }, produced_result_version: null, created_at: '',
}

describe('收據明細', () => {
  it('兩種金額分開列、不相加；總額未知時寫未知並附已知部分', () => {
    const out = text(<ReceiptDetail prices={mealResult().prices} />)
    expect(out).toContain('要買的整包')
    expect(out).toContain('這餐吃掉')
    expect(out).toContain('未知（已知 NT$380）')
    expect(out).toContain('未知（已知 NT$212）')
    expect(out).toContain('不相加')
    // 380 + 212 的和不該出現在任何地方；未知也不寫成 0
    expect(out).not.toContain('592')
    expect(out).not.toContain('NT$0')
  })

  it('查無價格的項目用虛線框列出並寫原因，包裝寫待確認', () => {
    const markup = html(<ReceiptDetail prices={mealResult().prices} />)
    expect(markup).toMatch(/<li class="[^"]*itemUnknown[^"]*">/)
    const out = markup.replace(/<[^>]+>/g, '')
    expect(out).toContain('鱸魚片')
    expect(out).toContain('查無價格・尚無價格資料')
    expect(out).toContain('包裝：待確認')
    expect(out).toContain('價格不完整，還不能比')
    expect(out).toContain('價格未知：鱸魚片')
    expect(out).not.toContain('預算內')
  })

  it('有價格的項目：包裝、來源與查詢時間', () => {
    const markup = html(<ReceiptDetail prices={mealResult().prices} />)
    expect(markup).toContain('1 顆約 600 g × 1')
    expect(markup).toContain('NT$45')
    expect(markup).toContain('NT$28')
    expect(markup).toContain('<a href="https://example.org/p" target="_blank" rel="noreferrer">示範超市</a>')
    expect(markup).toContain('查詢')
  })

  const complete = { ...mealResult().prices, items: [mealResult().prices.items[0]], purchase_total: '45', cost_total: '27.5', price_complete: true }

  it('預算內與超出預算各有文字，而且寫明比的是這餐吃掉的那一個金額', () => {
    const ok = text(<ReceiptDetail prices={{ ...complete, budget_status: 'OK' }} />)
    expect(ok).toContain('吃掉的在預算內')
    expect(ok).toContain('預算比的是「這餐吃掉」的用量成本，不是「要買的整包」。')
    expect(text(<ReceiptDetail prices={{ ...complete, budget_status: 'BUDGET_EXCEEDED' }} />)).toContain('吃掉的超出預算')
  })

  it('沒有預算（後端這時的狀態也是 OK）：不寫預算，也不說預算內', () => {
    const out = text(<ReceiptDetail prices={{ ...complete, budget: null, budget_status: 'OK' }} />)
    expect(out).not.toContain('預算')
    expect(out).toContain('NT$45')
  })

  it('缺量是容量時寫毫升', () => {
    const prices = mealResult().prices
    expect(text(<ReceiptDetail prices={{ ...prices, items: [priceItem('鮮奶', { shortage_grams: null, shortage_ml: '250' })] }} />)).toContain('缺 250 mL')
  })

  it('用量未定、不用補買、還算不出來', () => {
    const prices = mealResult().prices
    expect(text(<ReceiptDetail prices={{ ...prices, unquantified_items: [{ food_id: 'x', name: '香菜' }] }} />))
      .toContain('用量未定，整餐成本未知：香菜')
    expect(text(<ReceiptDetail prices={{ ...prices, items: [], purchase_total: '0', cost_total: '0', budget_status: 'OK' }} />))
      .toContain('已知用量的食材無需補買（成本 0 元）')
    const pending = text(<ReceiptDetail prices={{ ...prices, status: 'unavailable', items: [] }} />)
    expect(pending).toContain('目前缺少可用的食材用量，無法計算缺料價格')
    expect(pending).not.toContain('NT$')
  })
})

describe('營養小知識', () => {
  const knowledge = mealResult().knowledge
  const base = { retry: null, readOnly: false, retryPending: false, onRetry: () => {} }

  it('卡片正面有食材名、說明與出處，可以點原文', () => {
    const markup = html(<TipsDetail knowledge={knowledge} state="available" {...base} />)
    expect(markup).toContain('高麗菜含有維生素 C。')
    expect(markup).toContain('資料來源：健康醫療網〈高麗菜怎麼吃〉')
    expect(markup).toContain('<a href="https://example.org/k1" target="_blank" rel="noreferrer">原文</a>')
    expect(markup).toContain('1 / 2')
    expect(markup).toContain('aria-label="下一張"')
  })

  it('沒有顯示用出處時列出知識編號與版本；沒有食材名用編號', () => {
    const second = { ...knowledge, items: [knowledge.items[1]] }
    const out = text(<TipsDetail knowledge={second} state="available" {...base} />)
    expect(out).toContain('i2')
    expect(out).toContain('來源：k2（v1）')
  })

  it('暫時拿不到：寫原因、說明不影響採用，可以重試時才有按鈕', () => {
    const unavailable: MealResult['knowledge'] = { status: 'unavailable', retry_available: true, items: [] }
    const out = text(<TipsDetail knowledge={unavailable} state="unavailable" {...base} />)
    expect(out).toContain('營養知識服務暫時無法使用')
    expect(out).toContain('菜單、份量與採用資格不受影響')
    expect(out).toContain('只重試小知識')
    expect(text(<TipsDetail knowledge={{ ...unavailable, reason: 'BUDGET_EXHAUSTED' }} state="unavailable" {...base} />))
      .toContain('本月外部服務額度不足')
    expect(text(<TipsDetail knowledge={{ ...unavailable, retry_available: false }} state="unavailable" {...base} />))
      .not.toContain('只重試小知識')
  })

  it('重試中按鈕停用；上次失敗寫出原因', () => {
    const unavailable: MealResult['knowledge'] = { status: 'unavailable', retry_available: true, items: [] }
    const retrying = html(<TipsDetail knowledge={unavailable} state="retrying" {...base} />)
    expect(retrying).toContain('小知識重試中…')
    expect(retrying).toMatch(/<button[^>]*disabled=""[^>]*>重試中…<\/button>/)
    expect(text(<TipsDetail knowledge={unavailable} state="unavailable" {...base} retry={failedRetry} />)).toContain('上次重試失敗：來源沒有回應')
  })

  it('已採用的快照不提供重試', () => {
    const unavailable: MealResult['knowledge'] = { status: 'unavailable', retry_available: true, items: [] }
    expect(html(<TipsDetail knowledge={unavailable} state="unavailable" {...base} readOnly />)).not.toContain('<button')
  })

  it('這桌沒有小知識：不給重試，也不拿別的內容充數', () => {
    const markup = html(<TipsDetail knowledge={{ status: 'not_found', retry_available: true, items: [] }} state="not_found" {...base} />)
    expect(markup).toContain('查不到對應的小知識')
    expect(markup).not.toContain('<button')
  })
})

describe('這桌的狀況', () => {
  const versionTo = (v: number) => `/plan/r1/result/${v}`
  /** adoption 不給時：還沒採用是 none；已採用就當作採用的是這一版。latestVersion 是版本清單裡最新的那一版。 */
  const props = (r: MealResult, adoption: Adoption = adoptionOf(r, { result_version: r.result_version }), latestVersion?: number) => ({
    r, flags: resultFlags(r, latestVersion), recordsTo: '/records?tab=adopted&date=2026-11-14', mealLabel: '11/14 晚餐', adoption, versionTo,
    cooking: false, decidePending: false, retryPricePending: false, onDecide: () => {}, onRetryPrice: () => {}, onShowFamily: () => {},
  })
  const pending = (r: MealResult) => {
    r.eligibility = {
      ...r.eligibility, adoptable: false, price_exception_accepted: false,
      pending_decisions: [{ kind: 'continue_missing_nutrition', message: '要先看看嗎？' }, { kind: 'accept_missing_price', message: '缺價要繼續嗎？' }],
    }
    return r
  }

  it('可以採用（缺價只計已知部分），列出查不到價格的食材', () => {
    const out = text(<StatusDetail {...props(mealResult())} />)
    expect(out).toContain('符合採用條件（部分價格未知，成本只計已知部分）')
    expect(out).toContain('鱸魚片 查無價格')
    expect(out).toContain('第 2 版')
    expect(out).toContain('味噌豆腐湯 已依此結果的營養依據標示為高鈉。每人份達 800 mg。')
    expect(out).toContain('昨天（2026-11-13）與今天其他餐都沒有已採用紀錄')
  })

  it('不能採用時列出後端給的原因與待決定事項', () => {
    const r = mealResult()
    r.eligibility = {
      ...r.eligibility, adoptable: false, price_exception_accepted: false,
      blocks: [{ code: 'NUTRITION_INCOMPLETE', message: '缺少熱量或蛋白質的必要營養資料。' }],
      pending_decisions: [{ kind: 'continue_missing_nutrition', message: '要先看看嗎？' }, { kind: 'accept_missing_price', message: '缺價要繼續嗎？' }],
    }
    const out = text(<StatusDetail {...props(r)} />)
    expect(out).toContain('目前還不能採用')
    expect(out).toContain('缺少熱量或蛋白質的必要營養資料。')
    expect(out).toContain('繼續查看（仍不可採用）')
    expect(out).toContain('接受缺價例外並繼續')
    expect(out).not.toContain('符合採用條件')
  })

  it('問卷正在重新煮：決定與重試都停用', () => {
    const r = mealResult()
    r.eligibility = { ...r.eligibility, adoptable: false, pending_decisions: [{ kind: 'accept_missing_price', message: '缺價' }] }
    const markup = html(<StatusDetail {...props(r)} cooking />)
    expect(markup.match(/<button[^>]*disabled=""/g)).toHaveLength(3)
  })

  it('已採用：只能看，連到紀錄；高鈉不再說「仍可照常採用」', () => {
    const markup = html(<StatusDetail {...props(mealResult({ request_status: 'confirmed' }))} />)
    expect(markup).toContain('已採用並保存為已採用餐食紀錄（不代表實際已食用）')
    expect(markup).toContain('href="/records?tab=adopted&amp;date=2026-11-14"')
    expect(markup).not.toContain('仍可照常採用')
    expect(markup).not.toContain('重新查價')
  })

  it('採用的是別的版本時說清楚', () => {
    const r = mealResult({ request_status: 'confirmed' })
    const markup = html(<StatusDetail {...props(r, { kind: 'other', version: 1 })} />)
    expect(markup).toContain('href="/plan/r1/result/1"')
    const out = markup.replace(/<[^>]+>/g, '')
    expect(out).toContain('這份問卷採用的是第 1 版。現在看的第 2 版沒有被採用')
    expect(out).not.toContain('已採用並保存')
  })

  it('採用的是資料庫菜單：不說這一桌已採用，連到紀錄', () => {
    const markup = html(<StatusDetail {...props(mealResult({ request_status: 'confirmed' }), { kind: 'menu' })} />)
    expect(markup).toContain('這份問卷採用的是資料庫菜單，不是這一桌')
    expect(markup).toContain('href="/records?tab=adopted&amp;date=2026-11-14"')
    expect(markup).not.toContain('已採用並保存')
  })

  it('還不知道採用的是哪一份：不說這一桌已採用', () => {
    const out = text(<StatusDetail {...props(mealResult({ request_status: 'confirmed' }), { kind: 'unresolved' })} />)
    expect(out).toContain('這份問卷已經採用了')
    expect(out).toContain('採用的是不是這一桌，還沒有讀到')
    expect(out).not.toContain('已採用並保存')
  })

  it('已採用的問卷：待決定事項沒有按鈕（後端不擋，按了會真的寫入一筆決定）', () => {
    for (const adoption of [{ kind: 'other', version: 3 }, { kind: 'menu' }, { kind: 'unresolved' }, { kind: 'this' }] as Adoption[]) {
      const markup = html(<StatusDetail {...props(pending(mealResult({ request_status: 'confirmed' })), adoption)} />)
      expect(markup).not.toContain('<button')
      expect(markup).not.toContain('繼續查看（仍不可採用）')
      expect(markup).not.toContain('接受缺價例外並繼續')
      expect(markup).not.toContain('重新查價')
    }
  })

  it('已經被新版本取代的舊版：只能看，沒有決定與重新查價，連到最新版', () => {
    const markup = html(<StatusDetail {...props(pending(mealResult()), undefined, 3)} />)
    expect(markup).not.toContain('<button')
    expect(markup).toContain('href="/plan/r1/result/3"')
    const out = markup.replace(/<[^>]+>/g, '')
    expect(out).toContain('這是第 2 版，已經有比較新的第 3 版。舊的版本只能看')
    expect(out).not.toContain('目前還不能採用')
    expect(out).not.toContain('符合採用條件')
  })

  it('衍生版本、AI 回退說明、減鹹、指定菜重複', () => {
    const r = mealResult({
      derived_from_result_version: 1, price_retry: { from_result_version: 1, retried_at: '', price_source: 'x' },
      mode: 'ai_generated_menu', mode_label: 'AI 生成菜單', fallback_message: '已自動改由 AI 生成。',
      sodium_adjustment: { sodium_per_n_before: '1200', sodium_per_n_after: '900', budget_per_n: '800', seasoning_ratio: '0.5', within_budget: false },
      previous_day: { previous_date: '2026-11-13', has_records: true, excluded_dishes: [], specified_repeats: ['清蒸鱸魚'] },
    })
    const out = text(<StatusDetail {...props(r)} />)
    expect(out).toContain('由第 1 版重新查價')
    expect(out).toContain('已自動改由 AI 生成。')
    expect(out).toContain('已減為原本的 50%')
    expect(out).toContain('每人份鈉約 1200 → 900 mg')
    expect(out).toContain('調味料減到一半仍超過建議量')
    expect(out).toContain('清蒸鱸魚 與昨天或今天其他餐重複，因為是你指定的菜所以保留')
  })

  it('查價失敗與昨日衝突', () => {
    const r = mealResult({
      retries: { price: failedRetry, knowledge: null },
      previous_day_check: { status: 'conflict', history_changed: true, message: '有重複的菜，不能採用', conflicting_dishes: ['蒜炒高麗菜'] },
    })
    const out = text(<StatusDetail {...props(r)} />)
    expect(out).toContain('上次重新查價沒有完成：來源沒有回應')
    expect(out).toContain('有重複的菜，不能採用（重複：蒜炒高麗菜）')
  })
})

describe('個人營養', () => {
  const nutrition = mealResult().nutrition
  const render = (index: number) => text(<PersonDetail person={nutrition.per_person![index]} nutrition={nutrition} onShowFamily={() => {}} />)

  it('通過的人：兩個徽章、攝取量與占每日的比例', () => {
    const out = render(0)
    expect(out).toContain('熱量95%通過')
    expect(out).toContain('蛋白質108%通過')
    expect(out).toContain('這一餐約占每日熱量 38%')
    expect(out).toContain('建議約 40%')
    expect(out).toContain('760 kcal')
    expect(out).toContain('每日 2000 kcal・38%')
  })

  it('熱量超出', () => {
    expect(render(2)).toContain('熱量113%超出')
  })

  it('算不出來：寫計算不完整與未知（附已知部分），不寫 0', () => {
    const out = render(3)
    expect(out).toContain('熱量計算不完整')
    expect(out).toContain('未知（已知 500 kcal）')
    expect(out).not.toContain('0 kcal・')
    expect(out).not.toMatch(/(^|[^0-9.])0%/)
  })

  it('沒有資料的營養素寫未知，不畫長條', () => {
    const markup = html(<PersonDetail person={person('m', '阿明', { intake: { energy_kcal: '760', protein_g: '26' } })} nutrition={nutrition} onShowFamily={() => {}} />)
    // 七項裡只有熱量、蛋白質有值：兩條長條，其餘五項是未知
    expect(markup.match(/role="img"/g)).toHaveLength(2)
    expect(markup.replace(/<[^>]+>/g, '').match(/未知/g)!.length).toBeGreaterThanOrEqual(5)
  })

  it('只帶便當：判定看便當那一份，便當營養直接展開', () => {
    const out = render(1)
    expect(out).toContain('這一餐不在家吃，只帶便當。')
    expect(out).toContain('便當・午餐')
    expect(out).toContain('蛋白質83%不足')
    expect(out).toContain('便當（午餐）約占每日熱量 30%')
    expect(out).not.toContain('查看便當營養')
  })

  it('在家吃又帶便當：便當另列、預設收合', () => {
    const both = person('m', '小惠', {
      kinds: ['dining', 'bento'],
      bento: { n: '1', share: '0.2', meal_ratio: '0.3', meal: 'lunch', intake: { energy_kcal: '590', protein_g: '15' }, known_partial: {} },
    })
    const out = text(<PersonDetail person={both} nutrition={nutrition} onShowFamily={() => {}} />)
    expect(out).toContain('不計入這一餐')
    expect(out).toContain('查看便當營養')
  })
})

describe('全家營養', () => {
  it('整餐熱量、蛋白質對目標，未知的營養素附已知部分與缺值食材', () => {
    const out = text(<FamilyDetail nutrition={mealResult().nutrition} dishCount={3} stapleLabel="糙米飯" />)
    expect(out).toContain('3060 kcal')
    expect(out).toContain('目標 3000 kcal・+2%')
    expect(out).toContain('目標 90 g・+6%')
    expect(out).toContain('主食：糙米飯')
    expect(out).toContain('熱量102%通過')
    expect(out).toContain('未知（已知 12.0 g，缺：糙米飯）')
    expect(out).toContain('共 4 份')
  })

  it('整餐合計表：未知的格子用虛線的未知樣式，已知的數字照常；未知不寫成 0', () => {
    const nutrition = { ...mealResult().nutrition, energy_kcal: null, gap: { protein_g: '5' } }
    const markup = html(<FamilyDetail nutrition={nutrition} dishCount={3} stapleLabel={null} />)
    const table = /<table[^>]*>(.*?)<\/table>/.exec(markup)?.[1] ?? ''
    const row = (label: string) => [...(new RegExp(`<tr><th[^>]*>${label}</th>(.*?)</tr>`).exec(table)?.[1] ?? '').matchAll(/<td>(.*?)<\/td>/g)].map((m) => m[1])
    const unknown = /^<span class="[^"]*unknown[^"]*">未知/
    // 熱量：目標已知；估算與差距未知
    const energy = row('熱量')
    expect(energy[0]).not.toMatch(unknown)
    expect(energy[1]).toMatch(unknown)
    expect(energy[2]).toMatch(unknown)
    // 蛋白質三格都已知：一般文字
    for (const cell of row('蛋白質')) expect(cell).not.toContain('<span')
    // 糖：估算未知，已知的部分與缺值食材留在虛線樣式裡面
    const sugars = row('糖')[1]
    expect(sugars).toMatch(unknown)
    expect(sugars.replace(/<[^>]+>/g, '')).toBe('未知（已知 12.0 g，缺：糙米飯）')
    expect(table.replace(/<[^>]+>/g, '')).not.toMatch(/(^|[^0-9.])0(\.0)? (kcal|g|mg)/)
  })

  it('整餐熱量未知：寫未知與計算不完整，仍列出目標', () => {
    const nutrition = { ...mealResult().nutrition, energy_kcal: null }
    const out = text(<FamilyDetail nutrition={nutrition} dishCount={3} stapleLabel={null} />)
    expect(out).toContain('整餐熱量未知')
    expect(out).toContain('目標 3000 kcal')
    expect(out).toContain('熱量計算不完整')
  })

  it('用量沒通過驗證、有調整建議、沒有每人資料時各有說明', () => {
    const nutrition: MealResult['nutrition'] = {
      ...mealResult().nutrition, quantities_verified: false, per_person: [],
      suggestions: [{ code: 'NUTRITION_OUT_OF_RANGE', nutrient: 'energy_kcal', direction: 'decrease', needed_change: '30', ingredients: [] }],
    }
    const out = text(<FamilyDetail nutrition={nutrition} dishCount={3} stapleLabel={null} />)
    expect(out).toContain('尚未通過營養驗證')
    expect(out).toContain('調整建議（整餐，共 4 份；數值已由每份換算為整餐）')
    expect(out).toContain('熱量需減少約 120.0 kcal')
    expect(out).toContain('沒有每個人的資料')
  })
})

describe('菜的細節', () => {
  it('食材分組、整餐用量與做法', () => {
    const out = text(<DishDetail dish={mealResult().dishes[0]} index={0} readOnly={false} />)
    expect(out).toContain('第 1 道')
    expect(out).toContain('指定菜')
    expect(out).toContain('鱸魚片300 g')
    expect(out).toContain('調味')
    expect(out).toContain('鹽½ 小匙')
    expect(out).toContain('洗一洗')
  })

  it('高鈉寫出毫克；用量算不出來寫無法換算', () => {
    const salty = dish('味噌湯', {
      high_sodium: true, sodium_mg_per_serving: '960',
      ingredients: [{ ...mealResult().dishes[1].ingredients[0], display_amount: null, display_unit: null, display_grams: null }],
    })
    const out = text(<DishDetail dish={salty} index={2} readOnly={false} />)
    expect(out).toContain('高鈉・960 mg')
    expect(out).toContain('約占每日上限 2400 mg 的 40%')
    expect(out).toContain('無法換算')
    expect(text(<DishDetail dish={salty} index={2} readOnly />)).not.toContain('仍可照常採用')
  })

  it('主食：份量；算不出來時寫原因與不可採用', () => {
    const staple = mealResult().staple!
    expect(text(<StapleDetail staple={staple} />)).toContain('糙米飯 640 g')
    expect(text(<StapleDetail staple={{ ...staple, review_status: 'pending_review' }} />)).toContain('份量設定待營養審核')
    expect(text(<StapleDetail staple={{ ...staple, display_amount: null, unresolved_reason: 'staple_not_supported' }} />))
      .toContain('不在可選清單中，整餐營養無法計算；請改選清單中的主食後重新生成，不可採用')
  })
})

describe('這桌的狀況：其他版本', () => {
  const version = (v: number, adoptable: boolean) => ({
    result_version: v, request_version: 3, mode: 'database_menu' as const, mode_label: '資料庫菜單', created_at: '', adoptable,
  })
  const base = (r: MealResult, latestVersion: number, adoption: Adoption = { kind: 'none' }) => ({
    r, flags: resultFlags(r, latestVersion), recordsTo: '/records', mealLabel: null, adoption,
    cooking: false, decidePending: false, retryPricePending: false, onDecide: () => {}, onRetryPrice: () => {}, onShowFamily: () => {},
    versionTo: (v: number) => `/plan/r1/result/${v}`,
  })
  /** 版本清單每一列後面的小字（去掉標籤） */
  const rows = (markup: string) => [...markup.matchAll(/<li>(.*?)<\/li>/g)].map((m) => m[1].replace(/<[^>]+>/g, ''))

  it('有好幾版時列出來，現在看的那一版不是連結；只有最新的那一版寫能不能採用', () => {
    const markup = html(<StatusDetail {...base(mealResult(), 3)} versions={[version(3, true), version(2, true), version(1, false)]} />)
    expect(markup).toContain('href="/plan/r1/result/3"')
    expect(markup).toContain('href="/plan/r1/result/1"')
    // 版本清單裡現在看的這一版不是連結
    expect(rows(markup)).toEqual([
      '第 3 版資料庫菜單・可以採用',
      '第 2 版（現在看的）資料庫菜單・舊版，只能看',
      '第 1 版資料庫菜單・舊版，只能看',
    ])
  })

  it('最新的那一版後端說不能採用：寫還不能採用', () => {
    const markup = html(<StatusDetail {...base(mealResult(), 2)} versions={[version(2, false), version(1, true)]} />)
    expect(rows(markup)).toEqual(['第 2 版（現在看的）資料庫菜單・還不能採用', '第 1 版資料庫菜單・舊版，只能看'])
  })

  it('只有一版就不列；已採用時只標出被採用的那一版', () => {
    expect(text(<StatusDetail {...base(mealResult(), 2)} versions={[version(2, true)]} />)).not.toContain('其他版本')
    const confirmed = mealResult({ request_status: 'confirmed' })
    const adopted = html(<StatusDetail {...base(confirmed, 2, { kind: 'this' })} versions={[version(2, true), version(1, true)]} />)
    expect(rows(adopted)).toEqual(['第 2 版（現在看的）資料庫菜單・已採用', '第 1 版資料庫菜單'])
    // 採用的是資料庫菜單：沒有任何一版結果被採用
    const menu = html(<StatusDetail {...base(confirmed, 2, { kind: 'menu' })} versions={[version(2, true), version(1, true)]} />)
    expect(rows(menu)).toEqual(['第 2 版（現在看的）資料庫菜單', '第 1 版資料庫菜單'])
  })
})
