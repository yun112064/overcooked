import { FOODS, RECIPES, RECIPE_SOURCE, NUTRIENT_KEYS, food, foodId, mainIngredients, recipe, unknownFoodIds } from './catalog.ts'
import { ApiFail, asRecord, badFields, invalidInput, newId, notFound, numOrNull } from './core.ts'
import type { FakeRequest, FakeResponse, FakeState, RecognitionRow, SseEvent } from './core.ts'
import { householdMembers, ownedRequest } from './requests.ts'

/** 飲食小幫手、蔬菜辨識、營養來源回饋：都是罐頭內容，不呼叫任何模型或外部服務。 */

// ---- 飲食小幫手（R14） ----

// 與 src/api/assistant.ts 的 AssistantReference 相同（那個檔案用到瀏覽器 API，這裡不直接匯入）
type Reference = {
  n: number; title: string; url: string; source_name: string; heading: string; author: string | null
  reviewers: Record<string, string>; content_provider: string | null; published_at: string | null
}

const SOURCE_A = '（假資料）示範健康網'
const SOURCE_B = '（假資料）示範醫師專欄'
const DISCLAIMER = '以上內容整理自參考資料，僅供一般飲食參考，不能取代醫師或營養師的建議。'

type Topic = { words: string[]; references: Omit<Reference, 'n'>[]; sentences: string[] }

const reference = (slug: string, title: string, heading: string, sourceName: string, publishedAt: string): Omit<Reference, 'n'> => ({
  title, heading, source_name: sourceName, url: `https://example.com/fake-assistant/${slug}`, author: '示範作者',
  reviewers: { 營養師: '示範營養師' }, content_provider: sourceName, published_at: publishedAt,
})

const TOPICS: Topic[] = [
  { words: ['蛋白質', '雞蛋', '豆腐', '雞胸'],
    references: [reference('protein-basics', '蛋白質怎麼吃', '每餐都要有蛋白質', SOURCE_A, '2026-03-12'),
      reference('tofu-and-egg', '豆腐與雞蛋', '常見的蛋白質來源', SOURCE_B, '2026-05-02')],
    sentences: ['蛋白質可以從豆腐、雞蛋、魚和肉類攝取，建議分散在三餐裡[1]。', '豆腐和雞蛋都是方便取得的蛋白質來源，可以搭配蔬菜一起吃[2]。'] },
  { words: ['高麗菜', '蔬菜', '菠菜', '青菜'],
    references: [reference('vegetables', '蔬菜的份量', '每餐至少一份蔬菜', SOURCE_A, '2026-02-20'),
      reference('cabbage', '高麗菜的營養', '膳食纖維與維生素 C', SOURCE_A, '2026-01-15')],
    sentences: ['每餐至少安排一份蔬菜，深綠色蔬菜可以多選[1]。', '高麗菜含有膳食纖維與維生素 C，快炒或汆燙都合適[2]。'] },
  { words: ['鈉', '鹽', '太鹹', '減鹹'],
    references: [reference('sodium', '減少鈉的攝取', '調味料是主要來源', SOURCE_B, '2026-04-18')],
    sentences: ['鈉主要來自醬油、鹽和加工食品，可以先從少放一點調味料開始[1]。', '用蔥、薑、蒜和檸檬提味，也能讓菜不鹹還是好吃[1]。'] },
  { words: ['纖維', '地瓜', '糙米', '全穀'],
    references: [reference('fiber', '膳食纖維從哪裡來', '全穀與根莖類', SOURCE_A, '2026-05-11'),
      reference('brown-rice', '糙米飯小知識', '保留麩皮', SOURCE_B, '2026-06-01')],
    sentences: ['全穀類和根莖類（例如地瓜）含有較多膳食纖維[1]。', '糙米保留了麩皮，纖維比白米多，可以和白米混著煮[2]。'] },
  { words: ['早餐'],
    references: [reference('breakfast', '早餐怎麼搭配', '主食加蛋白質', SOURCE_A, '2026-07-07')],
    sentences: ['早餐可以用主食搭配一份蛋白質，例如吐司加雞蛋[1]。', '再加一份水果或牛奶，營養會更均衡[1]。'] },
  { words: ['魚', '鮭魚', '鱸魚', '海鮮'],
    references: [reference('fish', '吃魚的好處', '常見的魚類', SOURCE_B, '2026-05-30')],
    sentences: ['魚類是優質的蛋白質來源，清蒸可以少用油[1]。', '鮭魚含有 Omega-3 脂肪酸，煎或烤都很合適[1]。'] },
]

const GREETING = /^(你好|您好|哈囉|嗨|hi|hello|早安|午安|晚安)[\s!！。～~]*$/i
const WHO = /(你是誰|你會什麼|你能做什麼|怎麼用)/
const EMERGENCY = /(胸痛|呼吸困難|昏倒|休克|吐血|失去意識|急救)/
// 「山藥」是食材不是藥
const MEDICAL = /((?<!山)藥|劑量|診斷|處方|治療|開刀|手術|病(?!毒)|症)/

function fixed(type: string, text: string): SseEvent[] {
  return [
    { event: 'meta', data: { type, rewritten: false } },
    { event: 'delta', data: { text } },
    { event: 'done', data: { type, citations: [], removed: {}, fallback: null, alias: null } },
  ]
}

/** POST /assistant/chat：與真後端相同的 SSE 事件順序（meta → references → delta… → done；或 error）。 */
export function assistantChat(request: FakeRequest): FakeResponse {
  const body = asRecord(request.body)
  const message = typeof body.message === 'string' ? body.message : ''
  if (!message || message.length > 500) {
    throw badFields([{ field: 'message', reason: 'string_too_short', message: '問題須為 1–500 字。' }])
  }
  if (!message.trim()) throw invalidInput('請輸入問題。', 'message', 'EMPTY')
  if (body.engine !== undefined && body.engine !== 'local' && body.engine !== 'openai') {
    throw badFields([{ field: 'engine', reason: 'literal_error', message: '模型選項不正確。' }])
  }
  const text = message.trim()
  // 暗號：看得到各種失敗樣式
  if (/offline/i.test(text)) {
    throw new ApiFail(503, 'ASSISTANT_UNAVAILABLE', '暫時連不到飲食小幫手（團隊電腦可能離線），請稍後再試。', { reason: 'UNREACHABLE' })
  }
  if (/ratelimit/i.test(text)) throw new ApiFail(429, 'RATE_LIMITED', '提問太頻繁，請稍等一分鐘再試。')
  if (/broken/i.test(text)) {
    return { status: 200, sse: [
      { event: 'meta', data: { type: 'answer', rewritten: false } },
      { event: 'references', data: [] },
      { event: 'delta', data: { text: '（假資料）回答到一半' } },
      { event: 'error', data: { code: 'ASSISTANT_STREAM_BROKEN' } },
    ] }
  }
  if (/quota/i.test(text)) {
    return { status: 200, sse: [{ event: 'error', data: { code: 'LLM_BUDGET_EXHAUSTED' } }] }
  }

  if (EMERGENCY.test(text)) {
    return { status: 200, sse: fixed('emergency', '你描述的情況可能需要緊急處理，請立即撥打 119 或儘快到急診就醫。我只能提供一般飲食資訊，無法處理緊急狀況。') }
  }
  if (GREETING.test(text) || WHO.test(text)) {
    return { status: 200, sse: fixed('greeting', '你好！我是飲食小幫手，可以回答飲食與營養相關的問題，例如「蛋白質怎麼吃」或「高麗菜有什麼營養」。') }
  }
  if (MEDICAL.test(text)) {
    return { status: 200, sse: fixed('declined_medical', '這個問題涉及診斷、用藥或治療，我不能回答，請諮詢醫師或營養師。如果想知道一般的飲食原則，可以換個方式問我。') }
  }
  const topic = TOPICS.find((t) => t.words.some((w) => text.includes(w)))
  if (!topic) {
    // 山藥不在資料裡：示範「改用同類食材回答」的 category_answer
    if (text.includes('山藥')) {
      const fiber = TOPICS[3]
      const references: Reference[] = fiber.references.map((r, index) => ({ ...r, n: index + 1 }))
      return { status: 200, sse: [
        { event: 'meta', data: { type: 'category_answer', rewritten: false,
          fallback: { food: '山藥', category: 'root_vegetables', label: '根莖類', reason: 'not_mentioned' } } },
        { event: 'references', data: references },
        { event: 'delta', data: { text: '參考資料裡沒有直接提到山藥，以下是同屬根莖類的資料：\n' } },
        ...fiber.sentences.map((sentence) => ({ event: 'delta', data: { text: sentence } })),
        { event: 'delta', data: { text: `\n\n${DISCLAIMER}` } },
        { event: 'done', data: { type: 'category_answer', citations: [1, 2], removed: {},
          fallback: { food: '山藥', category: 'root_vegetables', label: '根莖類', reason: 'not_mentioned' }, alias: null } },
      ] }
    }
    return { status: 200, sse: fixed('out_of_scope', '目前的參考資料中找不到能回答這個問題的內容。我只能回答飲食與營養相關的問題，可以換個問法再試試。') }
  }
  const references: Reference[] = topic.references.map((r, index) => ({ ...r, n: index + 1 }))
  const history = Array.isArray(body.history) ? body.history : []
  return { status: 200, sse: [
    { event: 'meta', data: { type: 'answer', rewritten: history.length > 0 } },
    { event: 'references', data: references },
    ...topic.sentences.map((sentence) => ({ event: 'delta', data: { text: sentence } })),
    { event: 'delta', data: { text: `\n\n${DISCLAIMER}` } },
    { event: 'done', data: { type: 'answer', citations: references.map((r) => r.n), removed: {}, fallback: null, alias: null } },
  ] }
}

/** POST /assistant/recipes：依回答提到的食材建議 1–3 道菜；已選家庭時排除全家過敏與禁吃食材。 */
export function assistantRecipes(state: FakeState, raw: unknown) {
  const body = asRecord(raw)
  const answer = typeof body.answer === 'string' ? body.answer : ''
  const type = typeof body.type === 'string' ? body.type : ''
  if (!answer || answer.length > 8000 || !type || type.length > 40) {
    throw badFields([{ field: 'answer', reason: 'string_too_short', message: '需要回答內容與類型。' }])
  }
  if (type !== 'answer' && type !== 'category_answer') return { recipes: [], household_filtered: false }
  const householdId = state.session?.householdId ?? null
  const excluded = new Set(householdId
    ? [...householdMembers(state, householdId).values()].flatMap((m) => [...m.allergen_ingredient_ids, ...m.avoided_ingredient_ids])
    : [])
  // 調味料不拿來推薦菜；名稱至少兩個字才比對
  const mentioned = FOODS.filter((f) => !f.seasoning && [f.name, ...f.aliases].some((n) => n.length >= 2 && answer.includes(n)))
  const recipes: { dish_name: string; recipe_id: string; category: string; cuisine: string; source_name: string
    source_url: string; foods: string[]; reason: string; cover_url: string | null }[] = []
  for (const r of RECIPES) {
    if (recipes.length >= 3) break
    if (r.lines.some((l) => excluded.has(l.food.id))) continue
    // 這道菜以它為主要食材：前兩樣用量最多的食材之一
    const top = [...r.lines].sort((a, b) => b.grams - a.grams).slice(0, 2).map((l) => l.food)
    const matched = mentioned.filter((f) => top.includes(f))
    if (!matched.length || recipes.some((x) => x.foods[0] === matched[0].name)) continue
    recipes.push({
      dish_name: r.name, recipe_id: r.recipe_id, category: r.category, cuisine: r.cuisine, ...RECIPE_SOURCE,
      foods: matched.map((f) => f.name), reason: `回答提到「${matched[0].name}」，這道菜以它為主要食材。`, cover_url: null,
    })
  }
  return { recipes, household_filtered: householdId !== null }
}

// ---- 蔬菜辨識 ----

const UNAVAILABLE_MESSAGE = '暫時連不到辨識服務（團隊電腦可能離線），照片已保留，可稍後重試或手動輸入。'

function detected(): RecognitionRow['suggestions'] {
  return [
    { name: '高麗菜', ingredient_id: foodId('高麗菜') },
    { name: '紅蘿蔔', ingredient_id: foodId('紅蘿蔔') },
    { name: '番茄', ingredient_id: foodId('番茄') },
    // 信心低、對不到標準食材的類別：真後端也只給名稱，ingredient_id 為 null，由使用者自己選
    { name: '疑似青花菜', ingredient_id: null },
  ]
}

export function recognitionView(row: RecognitionRow) {
  const message = row.status === 'succeeded' ? '請確認或修改辨識出的蔬菜名稱，逐一加入家中已有食材。'
    : row.status === 'no_result' ? '沒有辨識到蔬菜，請手動輸入名稱。' : UNAVAILABLE_MESSAGE
  return {
    id: row.id,
    status: row.status,
    message,
    suggestions: row.status === 'succeeded' ? row.suggestions : [],
    retryable: row.status === 'unavailable',
    confirmed: row.confirmed,
    image_url: `/recognitions/${row.id}/image`,
    meal_request_id: row.requestId,
  }
}

function ownedRecognition(state: FakeState, householdId: string, id: string): RecognitionRow {
  const row = state.recognitions.find((r) => r.id === id && r.householdId === householdId)
  if (!row) throw notFound('找不到此辨識紀錄。')
  return row
}

/** POST /recognitions（multipart）：檔名含 empty → 沒辨識到；含 offline → 服務不可用（可重試）。 */
export function createRecognition(state: FakeState, householdId: string, request: FakeRequest) {
  const fields = asRecord(request.body)
  if (!request.filename) {
    throw badFields([{ field: 'image', reason: 'missing', message: 'Field required' }])
  }
  const requestId = typeof fields.meal_request_id === 'string' && fields.meal_request_id ? fields.meal_request_id : null
  if (requestId && !state.requests.some((r) => r.id === requestId && r.householdId === householdId)) {
    throw invalidInput('找不到此問卷。', 'meal_request_id', 'UNKNOWN_MEAL_REQUEST')
  }
  const name = request.filename.toLowerCase()
  const offline = name.includes('offline') || state.team.recognition.status !== 'online'
  const row: RecognitionRow = {
    id: newId(state, 'recognition'), householdId, requestId,
    status: offline ? 'unavailable' : name.includes('empty') ? 'no_result' : 'succeeded',
    reason: offline ? 'UNREACHABLE' : null,
    suggestions: detected(), confirmed: [],
  }
  state.recognitions.push(row)
  return recognitionView(row)
}

export function recognitionRoutes(state: FakeState, householdId: string, id: string, action: string, request: FakeRequest): FakeResponse {
  const row = ownedRecognition(state, householdId, id)
  if (request.method === 'GET' && action === '') return { status: 200, body: recognitionView(row) }
  if (request.method === 'GET' && action === 'image') {
    return { status: 200, raw: { contentType: 'image/svg+xml', text: PLACEHOLDER_IMAGE } }
  }
  if (request.method === 'DELETE' && action === '') {
    state.recognitions.splice(state.recognitions.indexOf(row), 1)
    return { status: 204 }
  }
  if (request.method === 'POST' && action === 'retry') {
    if (row.status !== 'unavailable') {
      throw new ApiFail(409, 'VERSION_CONFLICT', '只有服務不可用的辨識可以重試。', { reason: 'NOT_RETRYABLE' })
    }
    // 重試一次就成功，方便看完整流程
    row.status = 'succeeded'
    row.reason = null
    return { status: 200, body: recognitionView(row) }
  }
  if (request.method === 'POST' && action === 'confirm') {
    const body = asRecord(request.body)
    const name = typeof body.name === 'string' ? body.name.trim() : ''
    const ingredientId = typeof body.ingredient_id === 'string' ? body.ingredient_id : ''
    if (!name || name.length > 100 || !ingredientId || ingredientId.length > 20) {
      throw badFields([{ field: !name ? 'name' : 'ingredient_id', reason: 'string_too_short', message: '需要名稱與標準食材。' }])
    }
    if (unknownFoodIds([ingredientId]).length) {
      throw invalidInput('請從標準食材清單選擇。', 'ingredient_id', 'UNKNOWN_INGREDIENT_ID')
    }
    // 同一標準食材重複確認時以最後一次為準
    row.confirmed = [...row.confirmed.filter((c) => c.ingredient_id !== ingredientId), { name, ingredient_id: ingredientId }]
    return { status: 200, body: recognitionView(row) }
  }
  throw notFound()
}

const PLACEHOLDER_IMAGE = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 240">'
  + '<rect width="320" height="240" fill="#efe6d2"/><circle cx="110" cy="130" r="52" fill="#8fb26b"/>'
  + '<circle cx="200" cy="140" r="34" fill="#e2893f"/><circle cx="245" cy="95" r="26" fill="#d2543d"/>'
  + '<text x="160" y="225" font-size="14" text-anchor="middle" fill="#5b5347">假資料：上傳的照片示意</text></svg>'

// ---- T04 營養來源回饋 ----

function per100View(id: string): Record<string, string | null> {
  const f = food(id)!
  const map: Record<string, string | null> = {}
  for (const key of NUTRIENT_KEYS) map[key] = numOrNull(f.per100[key])
  return map
}

/** POST /meal-requests/{id}/t04-data：列出每樣食材的營養來源（都是待核對，不代表營養計算通過）。 */
export function t04Data(state: FakeState, householdId: string, requestId: string, raw: unknown) {
  const body = asRecord(raw)
  const version = Number(body.request_version)
  const recipeIds = Array.isArray(body.recipe_ids) ? [...new Set(body.recipe_ids.map(String))] : []
  if (!Number.isInteger(version) || version < 1 || recipeIds.length < 1 || recipeIds.length > 10) {
    throw badFields([{ field: 'recipe_ids', reason: 'too_short', message: '請提供問卷版本與 1 到 10 道菜。' }])
  }
  const row = ownedRequest(state, householdId, requestId, '找不到這份問卷。')
  if (row.version !== version) throw new ApiFail(409, 'VERSION_CONFLICT', '問卷已更新，請重新檢查營養資料。')
  const recipes = recipeIds.map((id) => recipe(id))
  if (recipes.some((r) => !r)) {
    throw new ApiFail(422, 'RECIPE_NUTRITION_RELEASE_MISMATCH', '食譜或營養來源版本無法使用，請重新確認。')
  }
  const ingredients = recipes.flatMap((r) => r!.lines.map((line, index) => {
    const f = line.food
    const official = {
      source_id: `SRC-FAKE-${f.id}`, provider: 'TFDA', sample_id: `FAKE-${f.id}`, name: `${f.name}（示範樣品）`,
      source_url: `https://example.com/fake-nutrition/${f.id}`, per_100g: per100View(f.id), state: '生', cooking_method: null,
      preparation: null, included_in_original_aggregate: true, binding_kind: 'original_food_link',
      review_status: 'pending_review', confirmation_required: false, confirmation_question: null as string | null,
      match_evidence: null, value_basis: 'source_per_100g',
    }
    // 幾種固定的狀態，讓四種標籤都看得到
    const tag = mainIngredients([f.name])[0]
    const sources = f.name === '咖哩塊' ? [] // 尚缺來源
      : f.name === '鱸魚片' ? [{ ...official, binding_kind: 'additional_source_candidate', confirmation_required: true,
        confirmation_question: '這份樣品是「鱸魚（生）」，和食譜的鱸魚片規格是否相同？' }]
        : f.name === '海帶芽' ? [{ ...official, per_100g: { ...official.per_100g, energy_kcal: null, protein_g: null } }]
          : tag === '海鮮' ? [official, { ...official, source_id: `SRC-FAKE-${f.id}-B`, name: `${f.name}（示範樣品 B）`, included_in_original_aggregate: false }]
            : [official]
    const usable = sources.filter((s) => s.per_100g.energy_kcal !== null && s.per_100g.protein_g !== null
      && s.included_in_original_aggregate !== false)
    const confirmed = usable.filter((s) => !s.confirmation_required)
    const status = confirmed.length ? 'source_available_pending_review' : usable.length ? 'source_candidate_needs_confirmation'
      : sources.length ? 'source_values_incomplete' : 'source_missing'
    return {
      recipe_id: r!.recipe_id, line_id: `${r!.recipe_id}-L${index + 1}`, food_id: f.id, name: f.name,
      // 整份食譜（示範食譜一律 4 人份）烹調前的克數
      whole_recipe_pre_cooking_grams: String(Math.round(line.grams * 4 * 10) / 10), weight_basis: 'raw', status, sources,
      required_values_available: confirmed.length > 0, candidate_values_available: usable.length > 0, nutrition_totals: null,
      blockers: ['source_selection_and_state_review_required', 'cooking_evidence_not_validated'],
    }
  }))
  const summary: Record<string, number> = {}
  for (const line of ingredients) summary[line.status] = (summary[line.status] ?? 0) + 1
  return {
    status: 'data_review_required',
    adoptable: false,
    nutrition_totals: null,
    message: '來源資料已列出；尚未完成樣品、生熟及烹調核對，不代表營養計算通過。',
    versions: { recipe_release_id: 'fake-recipes-1', nutrition_import_id: 'fake-nutrition-1', mapping_version: 'fake-mapping-1' },
    recipes: recipes.map((r) => ({ recipe_id: r!.recipe_id, name: r!.name, servings_raw: '4 人份', servings_basis: 'portion' })),
    summary,
    ingredients,
    input_version: row.version,
  }
}
