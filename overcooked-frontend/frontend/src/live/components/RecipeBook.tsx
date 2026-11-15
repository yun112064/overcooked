import { Box, Button, Chip, Typography } from '@mui/material'
import { ImageOff, Utensils } from 'lucide-react'
import { useId, useState, type ReactNode } from 'react'

export type RecipeEntry = {
  id: string
  name: string
  category?: string | null
  specified?: boolean
  coverUrl?: string | null
  badges?: ReactNode
  note?: ReactNode
  ingredients: ReactNode
  steps?: ReactNode
}

/** 只呈現後端配方；切換菜色不會重新查詢、計算或修改用量。 */
export default function RecipeBook({ recipes }: { recipes: RecipeEntry[] }) {
  const id = useId()
  const [selected, setSelected] = useState(recipes[0]?.id)
  const recipe = recipes.find(item => item.id === selected) ?? recipes[0]
  if (!recipe) return null
  return <Box className="live-recipe-layout">
    <Box component="nav" className="live-recipe-list" aria-label="本次菜色">
      {recipes.map(item => <Button key={item.id} color="inherit" aria-pressed={recipe.id === item.id}
        aria-controls={`${id}-recipe`} onClick={() => setSelected(item.id)}>
        <Utensils size={19} aria-hidden="true" />
        <span>{item.name}{item.category && <small>{item.category}</small>}
          {item.specified && <small>指定菜色</small>}</span>
      </Button>)}
    </Box>
    <Box component="article" id={`${id}-recipe`} className="live-recipe-article" aria-label={recipe.name}>
      <Box className="live-recipe-heading">
        <Typography component="h2" variant="h6">{recipe.name}</Typography>
        {recipe.specified && <Chip size="small" label="指定菜色" />}
        {recipe.badges}
      </Box>
      <RecipePhoto key={`${recipe.id}:${recipe.coverUrl ?? ''}`} url={recipe.coverUrl} name={recipe.name} />
      {recipe.note && <Box className="live-recipe-note">{recipe.note}</Box>}
      <Box className="live-recipe-details">
        <Box component="section">
          <Typography component="h3" variant="subtitle1">全家合計食材用量</Typography>
          {recipe.ingredients}
        </Box>
        <Box component="section">
          <Typography component="h3" variant="subtitle1">烹調步驟</Typography>
          {recipe.steps ?? <Typography variant="body2" color="text.secondary">未提供烹調步驟。</Typography>}
        </Box>
      </Box>
    </Box>
  </Box>
}

function RecipePhoto({ url, name }: { url?: string | null; name: string }) {
  const [failed, setFailed] = useState(false)
  return url && !failed
    ? <Box component="img" className="live-recipe-photo" src={`/api${url}`} alt={name}
      loading="lazy" onError={() => setFailed(true)} />
    : <Box className="live-recipe-photo-missing"><ImageOff size={28} aria-hidden="true" />
      <Typography variant="body2">尚無料理照片</Typography></Box>
}
