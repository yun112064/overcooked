/**
 * 冰箱便利貼的文字：物件列的短狀態，以及給讀屏的完整說明（便利貼上的字很小，讀屏要聽得到全部）。
 * 未知一律說「未知」，不說 0。
 */
import { withinTarget } from '../../api/nutrition'
import { formatMoney } from '../../components/formatMoney'
import { ratioText } from '../../components/nutritionVerdict'
import { dateParts } from './kitchenLogic'
import type { AdoptedNote, WeekNote } from './useFridgeNotes'

/** 物件列與便利貼共用的 id（Room 靠它讓被點到的那一張閃一下）。 */
export const NOTE_IDS = { adopted: 'note-adopted', week: 'note-week' } as const

export function dayText(date: string): string {
  const parts = dateParts(date)
  return parts ? `${parts.md} ${parts.weekday}` : date
}

/** 給物件列的短狀態。 */
export function adoptedStatus(note: AdoptedNote): string | undefined {
  if (note.status === 'ready') return dateParts(note.date)?.md ?? note.date
  if (note.status === 'empty') return '還沒有紀錄'
  return note.status === 'error' ? '讀不到' : undefined
}

export function weekStatus(note: WeekNote): string | undefined {
  if (note.status === 'ready') return `${note.meals} 餐`
  return note.status === 'error' ? '讀不到' : undefined
}

const ADOPTED_PENDING = {
  loading: '最近採用：讀取中',
  error: '最近採用：讀不到紀錄',
  empty: '還沒有採用過菜單',
} as const

export function adoptedLabel(note: AdoptedNote): string {
  const tail = '到冰箱門看已採用紀錄'
  if (note.status !== 'ready') return `${ADOPTED_PENDING[note.status]}。${tail}`
  const meals = note.lines?.map((line) => `${line.meal} ${line.dishes || '沒有菜名'}`).join('；')
  return `最近採用：${dayText(note.date)}${meals ? `，${meals}` : ''}。${tail}`
}

export function weekLabel(note: WeekNote): string {
  const tail = '到冰箱門看營養與採購紀錄'
  if (note.status !== 'ready') return `近 7 天：${note.status === 'loading' ? '讀取中' : '讀不到統計'}。${tail}`
  if (note.meals === 0) return `近 7 天沒有已採用的餐食。${tail}`
  const energy = note.energyReach === null
    ? '未知'
    : `${ratioText('energy_kcal', note.energyReach)}${withinTarget('energy_kcal', note.energyReach) ? '' : '，超出'}`
  const cost = note.averageCost === null ? '未知' : formatMoney(note.averageCost)
  const missing = note.unknownCostMeals > 0 ? `，${note.unknownCostMeals} 餐價格未知` : ''
  return `近 7 天：採用 ${note.meals} 餐，熱量達成 ${energy}，平均每餐按比例成本 ${cost}${missing}。${tail}`
}
