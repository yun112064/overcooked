import { describe, expect, it } from 'vitest'
import { nutritionVerdict, overallVerdict, percentText, ratioOf, ratioText, verdictText } from './nutritionVerdict'

describe('ratioOf', () => {
  it('精確字串相除', () => {
    expect(ratioOf('550', '500')).toBeCloseTo(1.1, 10)
    expect(ratioOf('1/2', '1')).toBe(0.5)
  })

  it('實際值是 0 仍然是已知的 0，不是未知', () => {
    expect(ratioOf('0', '500')).toBe(0)
  })

  it('任何一邊未知就是未知', () => {
    expect(ratioOf(null, '500')).toBeNull()
    expect(ratioOf('500', null)).toBeNull()
    expect(ratioOf(undefined, undefined)).toBeNull()
  })

  it('目標是 0、負數或不是數字時無法比較', () => {
    expect(ratioOf('500', '0')).toBeNull()
    expect(ratioOf('500', '-1')).toBeNull()
    expect(ratioOf('abc', '500')).toBeNull()
    expect(ratioOf(-5, 500)).toBeNull()
  })
})

describe('nutritionVerdict', () => {
  it('熱量：不超過目標 110% 就通過，低於不限', () => {
    expect(nutritionVerdict('energy_kcal', 1.1)).toBe('pass')
    expect(nutritionVerdict('energy_kcal', 0.4)).toBe('pass')
    expect(nutritionVerdict('energy_kcal', 1.1001)).toBe('fail')
  })

  it('蛋白質：不低於目標 90% 就通過，高於不限', () => {
    expect(nutritionVerdict('protein_g', 0.9)).toBe('pass')
    expect(nutritionVerdict('protein_g', 2.5)).toBe('pass')
    expect(nutritionVerdict('protein_g', 0.8999)).toBe('fail')
  })

  it('算不出來是第三態，不當成通過也不當成 0', () => {
    expect(nutritionVerdict('energy_kcal', null)).toBe('unknown')
    expect(nutritionVerdict('protein_g', undefined)).toBe('unknown')
    expect(nutritionVerdict('energy_kcal', Number.NaN)).toBe('unknown')
    expect(nutritionVerdict('protein_g', Number.POSITIVE_INFINITY)).toBe('unknown')
  })

  it('比例 0 是已知值：熱量通過、蛋白質不足', () => {
    expect(nutritionVerdict('energy_kcal', 0)).toBe('pass')
    expect(nutritionVerdict('protein_g', 0)).toBe('fail')
  })
})

describe('verdictText', () => {
  it('熱量只會超出、蛋白質只會不足', () => {
    expect(verdictText('energy_kcal', 'fail')).toBe('超出')
    expect(verdictText('protein_g', 'fail')).toBe('不足')
    expect(verdictText('energy_kcal', 'pass')).toBe('通過')
    expect(verdictText('protein_g', 'unknown')).toBe('計算不完整')
  })
})

describe('overallVerdict', () => {
  it('兩項都通過才算通過', () => {
    expect(overallVerdict(0.96, 1.04)).toBe('pass')
  })

  it('任何一項超出或不足就不通過', () => {
    expect(overallVerdict(1.2, 1.04)).toBe('fail')
    expect(overallVerdict(0.96, 0.5)).toBe('fail')
  })

  it('只要有一項算不出來就是計算不完整，即使另一項已經不通過', () => {
    expect(overallVerdict(null, 1.04)).toBe('unknown')
    expect(overallVerdict(1.5, null)).toBe('unknown')
    expect(overallVerdict(undefined, undefined)).toBe('unknown')
  })
})

describe('percentText', () => {
  it('四捨五入到整數百分比', () => {
    expect(percentText(0.957)).toBe('96%')
    expect(percentText(1.1)).toBe('110%')
    expect(percentText(0)).toBe('0%')
  })
})

describe('ratioText', () => {
  it('一般情況就是整數百分比', () => {
    expect(ratioText('energy_kcal', 0.957)).toBe('96%')
    expect(ratioText('energy_kcal', 1.1)).toBe('110%')
    expect(ratioText('protein_g', 0.9)).toBe('90%')
    expect(ratioText('protein_g', 1.04)).toBe('104%')
  })

  it('四捨五入後像是剛好在門檻、其實沒過時，不顯示成門檻值', () => {
    expect(ratioText('energy_kcal', 1.104)).toBe('110.4%')
    expect(ratioText('protein_g', 0.896)).toBe('89.6%')
    expect(ratioText('energy_kcal', 1.1001)).toBe('超過 110%')
    expect(ratioText('protein_g', 0.8999)).toBe('不到 90%')
  })
})
