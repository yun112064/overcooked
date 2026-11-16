/**
 * 金額的顯示。後端的金額是精確的十進位字串（也可能是 a/b 分數）；null 代表未知，不是 0。
 * 畫面上取整到元（和舊前端一樣），加上千分位：NT$1,840。
 *
 * 「整包花費」和「按比例成本」是兩個不同的數字，各自格式化、各自顯示，這裡不提供相加。
 */
import { toNumber } from '../api/nutrition'

/** 未知或不是數字回傳 null，由呼叫的地方顯示「未知」；不要把 null 當成 0。 */
export function formatMoney(value: string | number | null | undefined): string | null {
  if (typeof value === 'string' && value.trim() === '') return null
  const n = typeof value === 'number' ? value : toNumber(value)
  if (n === null || !Number.isFinite(n)) return null
  // || 0：把 -0 變回 0，免得顯示成「-NT$0」
  const rounded = Math.round(n) || 0
  return `${rounded < 0 ? '-' : ''}NT$${Math.abs(rounded).toLocaleString('en-US')}`
}
