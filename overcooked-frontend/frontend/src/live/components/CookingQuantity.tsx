import { Box, Typography } from '@mui/material'
import { cookingQuantity } from '../api/cookingQuantity'

export default function CookingQuantity({ ingredient, name }: {
  ingredient: Parameters<typeof cookingQuantity>[0]; name: string
}) {
  const { label, detail } = cookingQuantity(ingredient)
  if (!detail) return <>{label}</>
  return <Box component="details" sx={{ display: 'inline-block', verticalAlign: 'top', maxWidth: '100%' }}>
    <Box component="summary" aria-label={`${name}用量 ${label}，展開明細`}
      sx={{ cursor: 'pointer', textDecoration: 'underline dotted', listStyle: 'none' }}>{label}</Box>
    <Typography component="span" variant="caption" sx={{ display: 'block', whiteSpace: 'normal', maxWidth: 280 }}>
      {detail}
    </Typography>
  </Box>
}
