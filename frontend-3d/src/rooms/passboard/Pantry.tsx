/**
 * 第 3 步「家裡現在有什麼」：拍照或上傳 → 辨識 → 每一樣逐項確認；也可以直接搜尋加入，或整步略過。
 * 流程與端點和舊前端的 RecognitionCapture、IngredientPicker 相同：
 * 辨識只給「名稱建議」，一定要由使用者選一個標準食材才會加入；不接受自由輸入的食材。
 */
import { useMutation, useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { api, errorMessage } from '../../api/client'
import type { IngredientRef } from '../../api/types'
import { Notice } from '../../components/Notice'
import { Spinner } from '../../components/Spinner'
import { Field, Tag } from './fields'
import styles from './Paper.module.css'

type Suggestion = { name: string; ingredient_id: string | null }

type RecognitionResult = {
  id: string
  status: 'succeeded' | 'no_result' | 'unavailable'
  message: string
  suggestions: Suggestion[]
  retryable: boolean
  image_url: string
}

/** 打字停 250ms 才查，免得每個字都送一次。 */
function useDebounced(value: string): string {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), 250)
    return () => window.clearTimeout(timer)
  }, [value])
  return debounced
}

type CaptureProps = {
  mealRequestId: string
  disabled: boolean
  onAdd: (ingredient: IngredientRef) => void
}

/** 相機框：拍照／上傳 → 每種辨識到的蔬菜各一列 → 逐一選標準食材加入。 */
export function RecognitionCapture({ mealRequestId, disabled, onAdd }: CaptureProps) {
  const [result, setResult] = useState<RecognitionResult | null>(null)

  const upload = useMutation({
    mutationFn: async (file: File) => {
      const form = new FormData()
      form.append('image', file)
      form.append('meal_request_id', mealRequestId)
      return (await api.post<RecognitionResult>('/recognitions', form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })).data
    },
    onSuccess: setResult,
  })
  const retry = useMutation({
    mutationFn: async (id: string) => (await api.post<RecognitionResult>(`/recognitions/${id}/retry`)).data,
    onSuccess: setResult,
  })
  const discard = useMutation({
    mutationFn: async (id: string) => api.delete(`/recognitions/${id}`),
    onSuccess: () => setResult(null),
  })

  // 沒有辨識結果時仍給一列空白，讓使用者自己打名稱
  const rows: Suggestion[] = result?.suggestions.length ? result.suggestions : [{ name: '', ingredient_id: null }]
  const off = disabled || upload.isPending

  return (
    <div className={styles.capture}>
      <div className={styles.captureTop}>
        <label className={`${styles.camera} ${off ? styles.cameraOff : ''}`}>
          <input
            type="file" accept="image/jpeg,image/png,image/webp" capture="environment"
            className="visually-hidden" disabled={off}
            onChange={(event) => {
              const file = event.target.files?.[0]
              // 清掉才可以連續選同一張
              event.target.value = ''
              if (file) upload.mutate(file)
            }}
          />
          <i aria-hidden="true" />
          <span>{upload.isPending ? '辨識中…' : '拍冰箱或檯面'}</span>
        </label>
        <p className={styles.fieldHint}>
          拍照或上傳蔬菜照片；辨識結果要逐項確認才會加入。只確認食材種類，不需要重量。
        </p>
      </div>
      {upload.isPending && <Spinner label="正在辨識照片…" />}
      {upload.isError && <Notice tone="error">{errorMessage(upload.error)}</Notice>}

      {result && (
        <div className={styles.stack}>
          <div className={styles.captureResult}>
            <img src={`/api${result.image_url}`} alt="上傳的照片" />
            <Notice
              tone={result.status === 'succeeded' ? 'info' : 'warn'}
              action={result.retryable && (
                <button type="button" className={styles.smallButton} disabled={retry.isPending} onClick={() => retry.mutate(result.id)}>
                  {retry.isPending ? '重試中…' : '重試辨識'}
                </button>
              )}
            >
              {result.message}{result.suggestions.length > 1 && `（共 ${result.suggestions.length} 種）`}
            </Notice>
          </div>
          {retry.isError && <Notice tone="error">{errorMessage(retry.error)}</Notice>}
          {rows.map((suggestion, i) => (
            // key 含辨識 ID 與狀態：重試後換成新結果時重建各列
            <SuggestionRow
              key={`${result.id}-${result.status}-${i}`} recognitionId={result.id}
              suggestion={suggestion} disabled={disabled} onAdd={onAdd}
            />
          ))}
          <div>
            <button type="button" className={styles.linkButton} disabled={discard.isPending} onClick={() => discard.mutate(result.id)}>
              {discard.isPending ? '刪除中…' : '刪除這張照片'}
            </button>
          </div>
          {discard.isError && <Notice tone="error">{errorMessage(discard.error)}</Notice>}
        </div>
      )}
    </div>
  )
}

function SuggestionRow({ recognitionId, suggestion, disabled, onAdd }: {
  recognitionId: string
  suggestion: Suggestion
  disabled: boolean
  onAdd: (ingredient: IngredientRef) => void
}) {
  const [name, setName] = useState(suggestion.name)
  const query = useDebounced(name).trim()
  const [done, setDone] = useState<{ added: IngredientRef } | 'skipped' | null>(null)
  // 辨識到名稱但對不到標準食材：這一筆不太確定，要使用者看過名稱
  const unsure = suggestion.name.trim() !== '' && suggestion.ingredient_id === null

  const confirm = useMutation({
    mutationFn: async (ingredient: IngredientRef) => {
      await api.post(`/recognitions/${recognitionId}/confirm`, {
        name: name.trim() || ingredient.name, ingredient_id: ingredient.ingredient_id,
      })
      return ingredient
    },
    onSuccess: (ingredient) => {
      onAdd(ingredient)
      setDone({ added: ingredient })
    },
  })

  const matches = useQuery({
    // key 帶上筆數：下面的搜尋（和成員的過敏／禁吃）用 ['ingredients', query] 存「全部符合的」，
    // 這裡只拿 6 筆；共用同一格快取的話，先查的那一邊會讓另一邊拿到形狀不對的清單
    queryKey: ['ingredients', query, { limit: 6 }],
    queryFn: async () =>
      (await api.get<{ ingredients: IngredientRef[] }>('/ingredients', { params: { q: query, limit: 6 } })).data.ingredients,
    enabled: !done && query.length > 0,
    staleTime: 60_000,
  })

  if (done === 'skipped') {
    return (
      <div className={styles.suggestionDone}>
        <Tag>已略過：{name || '（空白）'}</Tag>
        <button type="button" className={styles.linkButton} onClick={() => setDone(null)}>改回</button>
      </div>
    )
  }
  if (done) {
    // 加入後要移除，請在下方「家中已有的食材」刪除
    return <div className={styles.suggestionDone}><Tag tone="ok">已加入：{done.added.name ?? done.added.ingredient_id} ✓</Tag></div>
  }

  return (
    <div className={styles.suggestion}>
      <div className={styles.suggestionHead}>
        <Field label="蔬菜名稱（可修改）">
          {(a11y) => <input {...a11y} value={name} disabled={disabled} onChange={(event) => setName(event.target.value)} />}
        </Field>
        <button type="button" className={styles.smallButton} onClick={() => setDone('skipped')}>略過</button>
      </div>
      {unsure && <p className={styles.fieldHint}><Tag tone="unsure">？ 不太確定</Tag> 對不到標準食材，請確認名稱後從下面選一個。</p>}
      <p className={styles.fieldHint}>選擇對應的標準食材加入「家中已有」：</p>
      {matches.isFetching && <Spinner label="查詢中…" />}
      {matches.isError && <Notice tone="error">{errorMessage(matches.error)}</Notice>}
      {query && matches.data?.length === 0 && <p className={styles.fieldHint}>查無符合的標準食材，請修改名稱。</p>}
      {(matches.data?.length ?? 0) > 0 && (
        <div className={styles.chips}>
          {matches.data?.map((ingredient) => (
            <button
              key={ingredient.ingredient_id} type="button"
              // 辨識建議的那一個標出來，其餘是名稱相近的
              className={`${styles.chipButton} ${ingredient.ingredient_id === suggestion.ingredient_id ? styles.chipSuggested : ''}`}
              disabled={disabled || confirm.isPending} onClick={() => confirm.mutate(ingredient)}
            >
              加入：{ingredient.name ?? ingredient.ingredient_id}
            </button>
          ))}
        </div>
      )}
      {confirm.isError && <Notice tone="error">{errorMessage(confirm.error)}</Notice>}
    </div>
  )
}

type PickerProps = {
  label: string
  value: IngredientRef[]
  onChange: (value: IngredientRef[]) => void
  disabled: boolean
  error?: string
}

/** 從標準食材字典搜尋加入；不接受自由輸入，避免無法對映的食材。 */
export function IngredientPicker({ label, value, onChange, disabled, error }: PickerProps) {
  const [input, setInput] = useState('')
  const query = useDebounced(input).trim()

  const results = useQuery({
    queryKey: ['ingredients', query],
    queryFn: async () =>
      (await api.get<{ ingredients: IngredientRef[] }>('/ingredients', { params: { q: query } })).data.ingredients,
    enabled: query.length > 0,
    staleTime: 60_000,
  })
  const chosen = new Set(value.map((i) => i.ingredient_id))
  const options = (results.data ?? []).filter((i) => !chosen.has(i.ingredient_id))

  return (
    <div className={styles.stack}>
      {value.length > 0 ? (
        <ul className={styles.chips} aria-label="家中已有的食材">
          {value.map((ingredient) => {
            const name = ingredient.name ?? ingredient.ingredient_id
            return (
              <li key={ingredient.ingredient_id} className={styles.pickedChip}>
                {name}
                <button
                  type="button" aria-label={`移除${name}`} disabled={disabled}
                  onClick={() => onChange(value.filter((i) => i.ingredient_id !== ingredient.ingredient_id))}
                >
                  ✕
                </button>
              </li>
            )
          })}
        </ul>
      ) : (
        <p className={styles.fieldHint}>還沒有選；沒選的食材會按整餐需求量補買。</p>
      )}
      <Field label={label} error={error} hint={query ? undefined : '輸入名稱搜尋，例如肉類、高麗菜'}>
        {(a11y) => (
          <input
            {...a11y} type="search" value={input} disabled={disabled} autoComplete="off"
            onChange={(event) => setInput(event.target.value)}
          />
        )}
      </Field>
      {results.isFetching && <Spinner label="查詢中…" />}
      {results.isError && <Notice tone="error">{errorMessage(results.error)}</Notice>}
      {query && results.data && options.length === 0 && (
        <p className={styles.fieldHint}>{results.data.length === 0 ? '查無符合的標準食材' : '符合的食材都已經加入了'}</p>
      )}
      {options.length > 0 && (
        <div className={styles.chips}>
          {options.map((ingredient) => (
            <button
              key={ingredient.ingredient_id} type="button" className={styles.chipButton} disabled={disabled}
              onClick={() => onChange([...value, ingredient])}
            >
              ＋ {ingredient.name ?? ingredient.ingredient_id}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
