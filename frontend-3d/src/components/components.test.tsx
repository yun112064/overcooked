import { AxiosError, AxiosHeaders } from 'axios'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { Button } from './Button'
import { ConfirmDialog } from './ConfirmDialog'
import { Money } from './Money'
import { ErrorNotice, Notice } from './Notice'
import { NutritionBadge } from './NutritionBadge'
import { Panel } from './Panel'
import { Spinner } from './Spinner'
import { StatusChip } from './StatusChip'
import { UnknownValue } from './UnknownValue'
import { requestStatusInfo } from './requestStatus'

/** 只看畫出來的文字（去掉標籤），確認使用者讀到什麼。 */
const text = (node: React.ReactElement) => renderToStaticMarkup(node).replace(/<[^>]+>/g, '')

describe('UnknownValue', () => {
  it('預設寫「未知」，可以補充已知的部分', () => {
    expect(text(<UnknownValue />)).toBe('未知')
    expect(text(<UnknownValue detail="已知 NT$320" />)).toBe('未知（已知 NT$320）')
    expect(text(<UnknownValue label="未填" />)).toBe('未填')
  })
})

describe('Money', () => {
  it('已知的金額', () => {
    expect(text(<Money value="486.00" />)).toBe('NT$486')
    expect(text(<Money value="0" />)).toBe('NT$0')
  })

  it('未知的金額寫「未知」，絕不顯示成 0', () => {
    expect(text(<Money value={null} />)).toBe('未知')
    expect(text(<Money value={undefined} />)).toBe('未知')
    expect(text(<Money value={null} />)).not.toContain('0')
  })

  it('未知但有已知的部分時一併寫出來', () => {
    expect(text(<Money value={null} known="320" />)).toBe('未知（已知 NT$320）')
    expect(text(<Money value={null} known={null} />)).toBe('未知')
  })
})

describe('NutritionBadge', () => {
  it('通過：名稱、百分比與判定都寫成文字', () => {
    expect(text(<NutritionBadge nutrient="energy_kcal" ratio={0.96} />)).toBe('✓熱量96%通過')
    expect(text(<NutritionBadge nutrient="protein_g" ratio={1.04} />)).toBe('✓蛋白質104%通過')
  })

  it('熱量超過 110% 是超出、蛋白質低於 90% 是不足', () => {
    expect(text(<NutritionBadge nutrient="energy_kcal" ratio={1.18} />)).toBe('!熱量118%超出')
    expect(text(<NutritionBadge nutrient="protein_g" ratio={0.82} />)).toBe('!蛋白質82%不足')
  })

  it('算不出來：不顯示百分比，也不顯示成 0%', () => {
    expect(text(<NutritionBadge nutrient="energy_kcal" ratio={null} />)).toBe('?熱量計算不完整')
    expect(text(<NutritionBadge nutrient="protein_g" ratio={undefined} />)).toBe('?蛋白質計算不完整')
    expect(text(<NutritionBadge nutrient="protein_g" ratio={Number.NaN} />)).not.toContain('%')
  })
})

describe('StatusChip 與 requestStatusInfo', () => {
  it('問卷狀態的文字與舊前端一致', () => {
    expect(['draft', 'queued', 'running', 'awaiting_decision', 'result_ready', 'failed', 'confirmed']
      .map((status) => requestStatusInfo(status).label))
      .toEqual(['草稿', '排隊中', '生成中', '待決定', '已有結果', '失敗', '已採用'])
  })

  it('不認得的狀態照原樣顯示', () => {
    expect(requestStatusInfo('something_new')).toEqual({ label: 'something_new', tone: 'neutral' })
  })

  it('狀態寫成文字', () => {
    expect(text(<StatusChip tone="warn">待決定</StatusChip>)).toBe('待決定')
  })
})

describe('Notice 與 ErrorNotice', () => {
  it('錯誤會標成 alert，其餘不會', () => {
    expect(renderToStaticMarkup(<Notice tone="error">壞了</Notice>)).toContain('role="alert"')
    expect(renderToStaticMarkup(<Notice>說明</Notice>)).not.toContain('role=')
  })

  it('ErrorNotice 顯示後端給的訊息', () => {
    const error = new AxiosError('Request failed', 'ERR_BAD_REQUEST', undefined, undefined, {
      status: 409, statusText: 'Conflict', headers: {}, config: { headers: new AxiosHeaders() },
      data: { error: { code: 'VERSION_CONFLICT', message: '問卷已更新，請確認最新內容後再送出。' } },
    })
    expect(text(<ErrorNotice error={error} />)).toBe('問卷已更新，請確認最新內容後再送出。')
  })

  it('沒有後端訊息時用 fallback，並可附重試按鈕', () => {
    const html = renderToStaticMarkup(<ErrorNotice error={new Error('boom')} fallback="讀不到資料。" onRetry={() => {}} />)
    expect(html).toContain('讀不到資料。')
    expect(html).toContain('再試一次')
    expect(renderToStaticMarkup(<ErrorNotice error={new Error('boom')} />)).not.toContain('<button')
  })

  it('重試中按鈕停用並換字', () => {
    const html = renderToStaticMarkup(<ErrorNotice error={null} onRetry={() => {}} retrying />)
    expect(html).toContain('重試中…')
    expect(html).toContain('disabled')
  })
})

describe('Spinner', () => {
  it('一定有文字，並標成 status', () => {
    const html = renderToStaticMarkup(<Spinner />)
    expect(html).toContain('role="status"')
    expect(html).toContain('載入中…')
  })

  it('hideLabel 時文字只給讀屏', () => {
    expect(renderToStaticMarkup(<Spinner label="載入中" hideLabel />)).toContain('class="visually-hidden">載入中')
  })
})

describe('Button', () => {
  it('預設是 type="button"，不會誤送出表單', () => {
    expect(renderToStaticMarkup(<Button>好</Button>)).toContain('type="button"')
    expect(renderToStaticMarkup(<Button type="submit">送出</Button>)).toContain('type="submit"')
  })

  it('busy 時停用並標 aria-busy', () => {
    const html = renderToStaticMarkup(<Button busy>儲存中…</Button>)
    expect(html).toContain('disabled')
    expect(html).toContain('aria-busy="true"')
  })
})

describe('Panel', () => {
  it('是有名稱的對話框，關閉按鈕有說明文字', () => {
    const html = renderToStaticMarkup(<Panel title="收據明細" onClose={() => {}}>內容</Panel>)
    expect(html).toContain('role="dialog"')
    expect(html).toMatch(/aria-labelledby="([^"]+)".*<h2 id="\1">收據明細<\/h2>/s)
    expect(html).toContain('aria-label="關閉收據明細"')
    expect(html).toContain('data-no-swipe')
  })
})

describe('ConfirmDialog', () => {
  it('取消排在確認前面（一打開焦點落在取消），錯誤以文字顯示', () => {
    const html = renderToStaticMarkup(
      <ConfirmDialog title="要刪除小米嗎？" confirmLabel="刪除" tone="danger" error="刪除失敗，請稍後再試。" onConfirm={() => {}} onCancel={() => {}}>
        刪除後無法復原。
      </ConfirmDialog>,
    )
    expect(html).toContain('role="alertdialog"')
    expect(html.indexOf('取消')).toBeLessThan(html.indexOf('>刪除<'))
    expect(html).toMatch(/role="alert"[^>]*>刪除失敗，請稍後再試。/)
    expect(html).toContain('刪除後無法復原。')
  })

  it('送出中兩顆按鈕都停用', () => {
    const html = renderToStaticMarkup(
      <ConfirmDialog title="確定？" confirmLabel="確定" busy onConfirm={() => {}} onCancel={() => {}} />,
    )
    expect(html.match(/disabled=""/g)).toHaveLength(2)
  })
})
