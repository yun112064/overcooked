export function isoDay(d: Date) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

export function historyDate(raw: string | null, now = new Date()) {
  if (raw && /^\d{4}-\d{2}-\d{2}$/.test(raw)) {
    const parsed = new Date(`${raw}T12:00:00`)
    if (!Number.isNaN(parsed.getTime()) && isoDay(parsed) === raw) return raw
  }
  return isoDay(now)
}

/** 月底換月時停在下一個月的最後一天，不溢出至再下一個月。 */
export function shiftMonth(date: string, offset: number) {
  const [year, month, day] = date.split('-').map(Number)
  const next = new Date(year, month - 1 + offset, 1, 12)
  next.setDate(Math.min(day, new Date(next.getFullYear(), next.getMonth() + 1, 0).getDate()))
  return isoDay(next)
}
