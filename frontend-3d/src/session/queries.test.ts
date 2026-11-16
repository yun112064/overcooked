import { describe, expect, it } from 'vitest'
import { localDate, mealRequestQuery, mealRequestsQuery, membersQuery } from './queries'

describe('共用查詢的 key 和舊前端一致', () => {
  it('問卷清單、單一問卷、成員', () => {
    expect(mealRequestsQuery.queryKey).toEqual(['meal-requests'])
    expect(mealRequestQuery('abc').queryKey).toEqual(['meal-request', 'abc'])
    expect(membersQuery.queryKey).toEqual(['members'])
  })
})

describe('localDate', () => {
  it('用本機時區的年月日，補零', () => {
    expect(localDate(new Date(2026, 0, 5, 9, 30))).toBe('2026-01-05')
    expect(localDate(new Date(2026, 10, 14, 23, 59))).toBe('2026-11-14')
  })

  it('凌晨也是當天（不會因為換算成 UTC 變成前一天）', () => {
    expect(localDate(new Date(2026, 10, 14, 0, 5))).toBe('2026-11-14')
  })
})
