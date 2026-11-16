/**
 * 冪等鍵（Idempotency-Key）：同一個命令重送時帶同一把，後端只會執行一次。
 * crypto.randomUUID 只在安全來源（https、localhost）才有；用區網 IP 連開發機測手機時退回自己組一把。
 */
export function newKey(): string {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}
