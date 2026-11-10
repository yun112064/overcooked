import type { Ingredient, Recipe, Scenario } from '../domain/types.ts'

export const mealLabels = { breakfast: '早餐', lunch: '午餐', dinner: '晚餐' } as const
export const goalLabels = { general: '一般餐食', maintain: '維持', muscle_gain: '增肌', fat_loss: '減脂' } as const
export const activityLabels = { sedentary: '低', low_active: '稍低', active: '適度', highly_active: '高' } as const
export const portionOptions = [0.7, 0.85, 1, 1.15, 1.3]
export const stapleLabels = { rice: '飯', noodles: '麵', bread: '麵包', none: '不需要主食' } as const
export const tasteOptions = ['清淡', '鹹香', '微辣', '酸甜']
export const scenarios: { value: Scenario; label: string }[] = [
  { value: 'normal', label: '正常流程' }, { value: 'service-failure', label: '服務失敗' },
  { value: 'no-solution', label: '無可行方案' }, { value: 'missing-price', label: '缺少價格' },
  { value: 'missing-nutrition', label: '缺少必要營養' }, { value: 'nutrition-range', label: '營養未達標' },
  { value: 'save-failure', label: '保存失敗' }, { value: 'recognition-failure', label: '辨識失敗' },
]
export const portionHelp = 'n 代表食量倍數。\n1n 是標準食譜的一位成人餐份；\n0.7n 為標準份量的 70%，\n1.3n 為 130%。\n此倍數不代表固定熱量。'
export const activityHelp = '低：大部分時間坐著，日常走動與身體活動很少。\n稍低：以坐著為主，有少量走動或輕度活動，例如散步、家務。\n適度：日常有較多走動，或經常從事中等程度的身體活動。\n高：日常活動量大，例如長時間從事體力工作，或有較多高強度運動。'

export const ingredients: Ingredient[] = [
  { id: 'tomato', name: '番茄', aliases: ['蕃茄'], unit: 'g', packageAmount: 300, packagePrice: 35 },
  { id: 'egg', name: '雞蛋', aliases: ['蛋'], unit: 'g', packageAmount: 300, packagePrice: 60 },
  { id: 'cabbage', name: '高麗菜', unit: 'g', packageAmount: 600, packagePrice: 50 },
  { id: 'chicken', name: '雞肉', aliases: ['雞腿肉', '雞胸肉'], unit: 'g', packageAmount: 500, packagePrice: 120 },
  { id: 'mushroom', name: '香菇', aliases: ['鮮香菇'], unit: 'g', packageAmount: 200, packagePrice: 60 },
  { id: 'ginger', name: '薑', aliases: ['生薑'], unit: 'g', packageAmount: 100, packagePrice: 20 },
  { id: 'garlic', name: '蒜頭', aliases: ['蒜'], unit: 'g', packageAmount: 100, packagePrice: 25 },
  { id: 'rice', name: '白米', aliases: ['米'], unit: 'g', packageAmount: 1000, packagePrice: 80 },
  { id: 'oil', name: '食用油', unit: 'ml', packageAmount: 500, packagePrice: 120 },
  { id: 'salt', name: '鹽', unit: 'g', packageAmount: 1000, packagePrice: 30 },
  { id: 'broccoli', name: '花椰菜', aliases: ['青花菜', '綠花椰菜'], unit: 'g', packageAmount: 300, packagePrice: 45 },
  { id: 'tofu', name: '豆腐', unit: 'g', packageAmount: 300, packagePrice: 30 },
  { id: 'carrot', name: '紅蘿蔔', aliases: ['胡蘿蔔'], unit: 'g', packageAmount: 300, packagePrice: 25 },
  { id: 'noodles', name: '麵條', unit: 'g', packageAmount: 500, packagePrice: 55 },
  { id: 'bread', name: '吐司', aliases: ['麵包'], unit: 'g', packageAmount: 400, packagePrice: 60 },
  { id: 'shrimp', name: '蝦', unit: 'g', packageAmount: 300, packagePrice: 150 },
  { id: 'peanut', name: '花生', unit: 'g', packageAmount: 200, packagePrice: 70 },
  { id: 'milk', name: '牛奶', unit: 'ml', packageAmount: 1000, packagePrice: 95 },
  { id: 'cilantro', name: '香菜', unit: 'g', packageAmount: 50, packagePrice: 20 },
]
export const ingredientName = (id: string) => ingredients.find(item => item.id === id)?.name ?? id
export const findIngredient = (value: string) => {
  const text = value.normalize('NFKC').trim()
  return ingredients.find(item => item.name === text || item.aliases?.includes(text))
}
const serving = (amount: number) => [{ label: '', amount, unit: 'g' as const }]
export const recipes: Recipe[] = [
  { id: 'eggs', name: '番茄炒蛋', kind: 'dish', source: 'database_menu', ingredients: [{ ingredientId: 'tomato', amount: 85 }, { ingredientId: 'egg', amount: 50 }, { ingredientId: 'oil', amount: 3 }, { ingredientId: 'salt', amount: 0.5 }], energyPerN: 145, proteinPerN: 8.5, servingPerN: serving(120), steps: ['番茄洗淨切塊，雞蛋打散。', '熱鍋加入部分食用油，將蛋液炒至凝固，盛起備用。', '炒軟番茄，放回雞蛋，加入鹽拌炒均勻至熟。'] },
  { id: 'cabbage', name: '清炒高麗菜', kind: 'dish', source: 'database_menu', ingredients: [{ ingredientId: 'cabbage', amount: 150 }, { ingredientId: 'garlic', amount: 4 }, { ingredientId: 'oil', amount: 3 }, { ingredientId: 'salt', amount: 0.5 }], energyPerN: 80, proteinPerN: 2.5, servingPerN: serving(120), steps: ['高麗菜洗淨切片，蒜頭切末。', '熱鍋加油，炒香蒜末。', '加入高麗菜拌炒至熟，加入鹽調味。'] },
  { id: 'mushroom-soup', name: '香菇雞湯', kind: 'dish', source: 'database_menu', ingredients: [{ ingredientId: 'chicken', amount: 100 }, { ingredientId: 'mushroom', amount: 25 }, { ingredientId: 'ginger', amount: 5 }, { ingredientId: 'salt', amount: 0.5 }], energyPerN: 190, proteinPerN: 15, servingPerN: [{ label: '湯', amount: 250, unit: 'ml' }, { label: '雞肉', amount: 80, unit: 'g' }, { label: '香菇', amount: 20, unit: 'g' }], steps: ['雞肉汆燙後瀝乾，香菇切塊，薑切片。', '鍋中加水，放入雞肉、香菇與薑片煮滾。', '轉小火煮至雞肉熟透，加入鹽調味。'] },
  { id: 'chicken-steak', name: '香煎雞胸', kind: 'dish', source: 'database_menu', ingredients: [{ ingredientId: 'chicken', amount: 110 }, { ingredientId: 'oil', amount: 3 }, { ingredientId: 'salt', amount: 0.5 }], energyPerN: 200, proteinPerN: 18, servingPerN: serving(90), steps: ['雞胸肉整理乾淨，抹上少量鹽。', '平底鍋加油，將雞肉煎至兩面上色。', '持續加熱至中心熟透後分切。'] },
  { id: 'broccoli', name: '蒜香花椰菜', kind: 'dish', source: 'database_menu', ingredients: [{ ingredientId: 'broccoli', amount: 120 }, { ingredientId: 'garlic', amount: 4 }, { ingredientId: 'oil', amount: 3 }], energyPerN: 80, proteinPerN: 4, servingPerN: serving(100), steps: ['花椰菜洗淨切小朵。', '以熱水汆燙後瀝乾。', '炒香蒜末，加入花椰菜拌炒至熟。'] },
  { id: 'clear-soup', name: '蔬菜豆腐湯', kind: 'dish', source: 'database_menu', ingredients: [{ ingredientId: 'tofu', amount: 70 }, { ingredientId: 'carrot', amount: 40 }, { ingredientId: 'salt', amount: 0.5 }], energyPerN: 135, proteinPerN: 4, servingPerN: [{ label: '湯', amount: 250, unit: 'ml' }, { label: '豆腐', amount: 60, unit: 'g' }, { label: '紅蘿蔔', amount: 30, unit: 'g' }], steps: ['豆腐切塊，紅蘿蔔切片。', '鍋中加水，將紅蘿蔔煮軟。', '加入豆腐煮熟，以鹽調味。'] },
  { id: 'ai-chicken', name: '香菇燴雞肉', kind: 'dish', source: 'ai_generated_menu', ingredients: [{ ingredientId: 'chicken', amount: 100 }, { ingredientId: 'mushroom', amount: 30 }, { ingredientId: 'oil', amount: 3 }], energyPerN: 200, proteinPerN: 18, servingPerN: serving(120), steps: ['雞肉切塊，香菇切片。', '熱鍋加油，放入雞肉與香菇。', '加入少量水，燴煮至雞肉熟透。'] },
  { id: 'ai-carrot', name: '清炒紅蘿蔔絲', kind: 'dish', source: 'ai_generated_menu', ingredients: [{ ingredientId: 'carrot', amount: 140 }, { ingredientId: 'oil', amount: 3 }], energyPerN: 80, proteinPerN: 4, servingPerN: serving(100), steps: ['紅蘿蔔洗淨切絲。', '熱鍋加油，放入紅蘿蔔絲。', '拌炒至熟。'] },
  { id: 'ai-tofu', name: '高麗菜豆腐湯', kind: 'dish', source: 'ai_generated_menu', ingredients: [{ ingredientId: 'tofu', amount: 70 }, { ingredientId: 'cabbage', amount: 80 }, { ingredientId: 'salt', amount: 0.5 }], energyPerN: 135, proteinPerN: 4, servingPerN: [{ label: '湯', amount: 250, unit: 'ml' }, { label: '豆腐', amount: 60, unit: 'g' }, { label: '高麗菜', amount: 60, unit: 'g' }], steps: ['豆腐切塊，高麗菜切片。', '鍋中加水，放入高麗菜煮軟。', '加入豆腐煮熟，以鹽調味。'] },
]
export const staples: Record<'rice' | 'noodles' | 'bread', Recipe> = {
  rice: { id: 'staple-rice', name: '白飯', kind: 'staple', source: 'database_menu', ingredients: [{ ingredientId: 'rice', amount: 75 }], energyPerN: 185, proteinPerN: 4, servingPerN: serving(180), steps: ['白米洗淨，瀝去洗米水。', '依米種及電鍋指示加水煮熟，燜好後拌鬆。'] },
  noodles: { id: 'staple-noodles', name: '麵', kind: 'staple', source: 'database_menu', ingredients: [{ ingredientId: 'noodles', amount: 75 }], energyPerN: 185, proteinPerN: 4, servingPerN: serving(180), steps: ['鍋中加水煮滾。', '放入麵條，依包裝指示煮熟後瀝乾。'] },
  bread: { id: 'staple-bread', name: '吐司', kind: 'staple', source: 'database_menu', ingredients: [{ ingredientId: 'bread', amount: 75 }], energyPerN: 185, proteinPerN: 4, servingPerN: serving(75), steps: ['依喜好加熱吐司後分裝。'] },
}
