import { describe, expect, it } from 'vitest'
import { isActivePlan } from './isActivePlan'

describe('isActivePlan', () => {
  it('同一份問卷、已採用：有效', () => {
    expect(isActivePlan({ meal_request_id: 'r1', status: 'adopted' }, 'r1')).toBe(true)
  })

  it('同一份問卷、已被取代：無效', () => {
    expect(isActivePlan({ meal_request_id: 'r1', status: 'superseded' }, 'r1')).toBe(false)
  })

  it('後端沒帶 status：當作有效', () => {
    expect(isActivePlan({ meal_request_id: 'r1' }, 'r1')).toBe(true)
  })

  it('別份問卷的紀錄：無效', () => {
    expect(isActivePlan({ meal_request_id: 'r2', status: 'adopted' }, 'r1')).toBe(false)
  })
})
