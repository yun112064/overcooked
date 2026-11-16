import { describe, expect, it } from 'vitest'
import { feedbackWeightText } from './previewText'

describe('feedbackWeightText', () => {
  it('DEMO 工作簿重量不寫成整份配方烹調前', () => {
    const text = feedbackWeightText({ whole_recipe_pre_cooking_grams: '120', weight_semantics: 'workbook_weight_g_unverified' })
    expect(text).toBe('工作簿重量（未核對是否為整份配方烹調前） 120 g')
    expect(feedbackWeightText({ whole_recipe_pre_cooking_grams: null })).toBe('整份配方烹調前 未知 g')
  })
})
