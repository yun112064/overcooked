import type { AppData } from '../domain/types.ts'
import { appDataSchema } from '../domain/schemas.ts'

export const storageKey = 'today-eat-demo:v1'
export interface StoragePort { getItem(key: string): string | null; setItem(key: string, value: string): void }
export function readData(storage: StoragePort): AppData | null {
  const value = storage.getItem(storageKey)
  if(value === null) return null
  const parsed = appDataSchema.safeParse(JSON.parse(value))
  if(!parsed.success) throw new Error('本機資料格式無法讀取。原始資料已保留，請先備份瀏覽器儲存內容。')
  const result = parsed.data as AppData
  if(!result.households.some(household => household.id === result.activeHouseholdId)) throw new Error('找不到目前家庭，本機資料已保留。')
  return result
}
export function writeData(storage: StoragePort, data: AppData): void {
  storage.setItem(storageKey, JSON.stringify(appDataSchema.parse(data)))
}
