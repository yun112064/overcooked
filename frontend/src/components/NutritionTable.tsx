import { Table, TableBody, TableCell, TableHead, TableRow, Typography } from '@mui/material'
import { amount, NUTRIENTS, nutrientText, type Nutrition } from '../api/nutrition'

/** 整餐營養表：標題註明整餐與份數 N；目標、估算、差距皆為整餐值。 */
export default function NutritionTable({ nutrition, title = '營養估算' }: { nutrition: Nutrition; title?: string }) {
  const n = nutrition.total_n
  return (
    <>
      <Typography variant="subtitle1">
        {title}（整餐{n !== null && n !== undefined ? `，共 ${Number(n)} 份` : ''}）
      </Typography>
      <Table size="small">
        <TableHead><TableRow><TableCell>項目</TableCell><TableCell>目標</TableCell><TableCell>估算</TableCell><TableCell>差距</TableCell></TableRow></TableHead>
        <TableBody>
          {NUTRIENTS.map(({ key, label, unit, required }) => (
            <TableRow key={key}>
              <TableCell>{label}</TableCell>
              <TableCell>{required ? amount(nutrition.target?.[key as 'energy_kcal' | 'protein_g'], unit) : '—'}</TableCell>
              <TableCell>{nutrientText(nutrition, key, unit)}</TableCell>
              <TableCell>{required ? amount(nutrition.gap?.[key as 'energy_kcal' | 'protein_g'], unit) : '—'}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </>
  )
}
