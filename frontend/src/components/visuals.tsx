/** 共用的類別用色、餐別與營養素圖示、頭像字；只放常數與函式（元件放在 ui.tsx，讓 Fast Refresh 正常運作）。 */
import CookieOutlined from '@mui/icons-material/CookieOutlined'
import DarkModeOutlined from '@mui/icons-material/DarkModeOutlined'
import EggOutlined from '@mui/icons-material/EggOutlined'
import GrainOutlined from '@mui/icons-material/GrainOutlined'
import GrassOutlined from '@mui/icons-material/GrassOutlined'
import LightModeOutlined from '@mui/icons-material/LightModeOutlined'
import LocalFireDepartmentOutlined from '@mui/icons-material/LocalFireDepartmentOutlined'
import RiceBowlOutlined from '@mui/icons-material/RiceBowlOutlined'
import WaterDropOutlined from '@mui/icons-material/WaterDropOutlined'
import WbTwilightOutlined from '@mui/icons-material/WbTwilightOutlined'
import type { ReactNode } from 'react'
import { tokens } from '../theme'

/** 點綴色：類別用色（餐別、營養素），每種都有深色（圖示、文字）與淺底 */
export type Tone = 'indigo' | 'matcha' | 'amber' | 'brick' | 'ink'
export const TONES: Record<Tone, { fg: string; bg: string }> = {
  indigo: { fg: tokens.indigo, bg: tokens.indigoTint },
  matcha: { fg: tokens.matcha, bg: tokens.matchaTint },
  amber: { fg: tokens.amber, bg: tokens.amberTint },
  brick: { fg: tokens.brick, bg: tokens.brickTint },
  ink: { fg: tokens.inkSoft, bg: tokens.sand },
}

/** 餐別的圖示與用色：早餐琥珀、午餐抹茶、晚餐靛藍。 */
export const MEAL_VISUALS: Record<string, { icon: ReactNode; tone: Tone }> = {
  breakfast: { icon: <WbTwilightOutlined />, tone: 'amber' },
  lunch: { icon: <LightModeOutlined />, tone: 'matcha' },
  dinner: { icon: <DarkModeOutlined />, tone: 'indigo' },
}

/** 營養素的圖示與用色（與 api/nutrition NUTRIENTS 的 key 對應）。 */
export const NUTRIENT_VISUALS: Record<string, { icon: ReactNode; tone: Tone }> = {
  energy_kcal: { icon: <LocalFireDepartmentOutlined />, tone: 'brick' },
  protein_g: { icon: <EggOutlined />, tone: 'indigo' },
  carbohydrate_g: { icon: <RiceBowlOutlined />, tone: 'amber' },
  fat_g: { icon: <WaterDropOutlined />, tone: 'matcha' },
  sugars_g: { icon: <CookieOutlined />, tone: 'amber' },
  sodium_mg: { icon: <GrainOutlined />, tone: 'ink' },
  dietary_fiber_g: { icon: <GrassOutlined />, tone: 'matcha' },
}

/** 成員頭像字：中文名去掉姓氏取第二個字（林媽媽→媽、小宇→宇），其他取第一個字母。 */
export function initial(name: string) {
  const n = name.trim()
  return /^\p{Script=Han}/u.test(n) && n.length >= 2 ? n[1] : n.slice(0, 1).toUpperCase()
}
