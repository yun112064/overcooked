/**
 * 全家福裡「畫上去的人」用到的純邏輯：選哪一種蠟筆人物、排在圖畫紙的哪裡、名字下面那一行寫什麼、
 * 全家的統計。都不碰畫面，方便測試。
 */
import type { Activity, IngredientRef, Member } from '../../api/types'

export type CrayonKind = 'man' | 'woman' | 'kid' | 'elder'

/** 12 歲以下畫成小孩、65 歲以上畫成長輩；只是畫風的分界，和營養規則的 19 歲無關。 */
export const KID_MAX_AGE = 12
export const ELDER_MIN_AGE = 65

type Figure = Pick<Member, 'equation_sex'> & { age_years: number | null | undefined }

function knownAge(age: number | null | undefined): number | null {
  return typeof age === 'number' && Number.isFinite(age) ? age : null
}

/** 依性別與年齡選蠟筆人物。年齡讀不到時不猜是小孩或長輩，只照性別畫大人。 */
export function crayonKind({ equation_sex, age_years }: Figure): CrayonKind {
  const age = knownAge(age_years)
  if (age !== null && age <= KID_MAX_AGE) return 'kid'
  if (age !== null && age >= ELDER_MIN_AGE) return 'elder'
  return equation_sex === 'F' ? 'woman' : 'man'
}

/** 人物相對於大人的大小：小孩矮一截、青少年略矮。 */
export function figureScale(age_years: number | null | undefined): number {
  const age = knownAge(age_years)
  if (age === null) return 1
  if (age <= KID_MAX_AGE) return 0.78
  if (age < 18) return 0.9
  return 1
}

// ---- 排位置（畫稿座標 1600×1000）----

/** 圖畫紙上可以站人的範圍：紙是 510–1090，左右各留一點邊。 */
const PAPER_LEFT = 520
const PAPER_RIGHT = 1080
const PAPER_CENTER = (PAPER_LEFT + PAPER_RIGHT) / 2
/** 一格最寬這麼多（一排五格剛好排滿）；人少時不拉開，往中間靠。 */
const SLOT_MAX = 112
/** 一排最多五格（4 位成員＋「畫上新成員」）；再多就排兩排（DESIGN.md 5.8）。 */
export const ONE_ROW_MAX = 5
/** 畫裡最多畫這麼多位成員；再多每格會小於手指點得到的大小，其餘的從名單與物件列進去。 */
export const MAX_DRAWN = 12
/** 蠟筆人物圖檔的寬高比（120×200）。 */
const FIGURE_ASPECT = 200 / 120

const ONE_ROW = { feet: 600, figure: 100 }
// 兩排：後排腳下要留得下三行字（手機上字不會跟著世界縮小），所以後排站得比較高
const BACK_ROW = { feet: 400, figure: 66 }
const FRONT_ROW = { feet: 600, figure: 66 }

export type Slot = {
  /** 這一格的水平中心 */
  x: number
  /** 腳踩的高度 */
  feet: number
  /** 這一格的寬度（可點範圍） */
  width: number
  /** 大人的人物寬度；實際還要乘 figureScale */
  figure: number
  row: 'single' | 'back' | 'front'
}

function rowSlots(count: number, slotWidth: number, row: Slot['row'], feet: number, figure: number): Slot[] {
  const start = PAPER_CENTER - (slotWidth * count) / 2
  return Array.from({ length: count }, (_, i) => ({
    x: Math.round((start + slotWidth * (i + 0.5)) * 10) / 10,
    feet, width: slotWidth, figure: Math.min(figure, slotWidth * 0.86), row,
  }))
}

/**
 * count 格（成員＋最後一格「畫上新成員」）在圖畫紙上的位置，照傳入的順序由左到右、後排先。
 * 五格以內一排；超過就分兩排，前排不比後排少。
 */
export function layoutPeople(count: number): Slot[] {
  if (count <= 0) return []
  if (count <= ONE_ROW_MAX) {
    return rowSlots(count, SLOT_MAX, 'single', ONE_ROW.feet, ONE_ROW.figure)
  }
  const back = Math.floor(count / 2)
  const front = count - back
  const slotWidth = Math.min(SLOT_MAX, (PAPER_RIGHT - PAPER_LEFT) / front)
  return [
    ...rowSlots(back, slotWidth, 'back', BACK_ROW.feet, BACK_ROW.figure),
    ...rowSlots(front, slotWidth, 'front', FRONT_ROW.feet, FRONT_ROW.figure),
  ]
}

/** 人物圖的寬與上緣（畫稿座標），讓腳剛好踩在這一格的 feet 上。 */
export function figureBox(slot: Slot, scale: number): { width: number; top: number } {
  const width = slot.figure * scale
  return { width, top: slot.feet - width * FIGURE_ASPECT }
}

// ---- 名字下面那一行 ----

/** 畫裡用的短寫法；完整說明（含 PAL）在面板裡用 ACTIVITY_LABELS。 */
export const ACTIVITY_SHORT: Record<Activity, string> = {
  sedentary: '活動量低',
  low_active: '活動量稍低',
  active: '活動量適度',
  highly_active: '活動量高',
}

export type SummaryPiece = { text: string; /** 沒有資料：用鉛筆底線標出來，不替使用者猜 */ missing: boolean }

type SummaryInput = { age_years: number | null | undefined; activity_category: Activity | null | undefined }

/** 人物名字下面的摘要：年齡、活動量；targetUnavailable 表示後端說每日目標算不出來。 */
export function memberSummary(member: SummaryInput, targetUnavailable = false): SummaryPiece[] {
  const age = knownAge(member.age_years)
  const activity = member.activity_category ? ACTIVITY_SHORT[member.activity_category] : undefined
  return [
    age === null ? { text: '年齡未填', missing: true } : { text: `${age} 歲`, missing: false },
    activity ? { text: activity, missing: false } : { text: '活動量未填', missing: true },
    ...(targetUnavailable ? [{ text: '目標算不出來', missing: true }] : []),
  ]
}

export function summaryText(pieces: readonly SummaryPiece[]): string {
  return pieces.map((p) => p.text).join('、')
}

/** 食材名稱；字典裡沒有名稱時顯示代碼（和舊前端的挑選器一致），不留空白。 */
export function ingredientLabel(ref: IngredientRef): string {
  return ref.name ?? ref.ingredient_id
}

// ---- 全家的統計 ----

/**
 * 食量加總（字串小數，最多兩位）。用整數的百分位相加，不經過浮點數。
 * 有任何一個讀不懂就回 null（未知），不當成 0 加進去。
 */
export function sumPortions(portions: readonly (string | null | undefined)[]): string | null {
  let hundredths = 0
  for (const portion of portions) {
    const match = /^(\d+)(?:\.(\d{1,2}))?$/.exec(portion?.trim() ?? '')
    if (!match) return null
    hundredths += Number(match[1]) * 100 + Number((match[2] ?? '').padEnd(2, '0'))
  }
  const whole = Math.floor(hundredths / 100)
  const rest = String(hundredths % 100).padStart(2, '0').replace(/0+$/, '')
  return rest ? `${whole}.${rest}` : String(whole)
}

export type FamilyStats = {
  count: number
  /** 全員一起用餐時的份數；算不出來是 null */
  portionTotal: string | null
  allergic: { people: number; ingredients: number }
  avoiding: { people: number; ingredients: number }
}

type StatsInput = Pick<Member, 'portion' | 'allergens' | 'avoided'>

function restriction(lists: readonly IngredientRef[][]): { people: number; ingredients: number } {
  return {
    people: lists.filter((list) => list.length > 0).length,
    ingredients: new Set(lists.flat().map((item) => item.ingredient_id)).size,
  }
}

/** 名單上方的四個數字（和舊前端成員頁的統計列相同）。 */
export function familyStats(members: readonly StatsInput[]): FamilyStats {
  return {
    count: members.length,
    portionTotal: sumPortions(members.map((m) => m.portion)),
    allergic: restriction(members.map((m) => m.allergens)),
    avoiding: restriction(members.map((m) => m.avoided)),
  }
}

/**
 * 把後端給的數字字串整理成好讀的樣子（去掉多餘的 0、加千分位），只做字串處理不四捨五入。
 * 不是數字就回 null，畫面上顯示「未知」。
 */
export function plainNumber(value: string | number | null | undefined): string | null {
  if (value === null || value === undefined) return null
  const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(String(value).trim())
  if (!match) return null
  const whole = match[2].replace(/^0+(?=\d)/, '').replace(/\B(?=(\d{3})+$)/g, ',')
  const fraction = (match[3] ?? '').replace(/0+$/, '')
  return `${match[1]}${whole}${fraction ? `.${fraction}` : ''}`
}
