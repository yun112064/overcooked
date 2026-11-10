import { useState } from 'react'
import { BookOpen, ImageOff, Leaf } from 'lucide-react'
import type { Plan, Recipe } from '../domain/types.ts'
import { ingredientName, ingredients } from '../data/catalog.ts'
import { round } from '../domain/rules.ts'
import { knowledgeForRecipe } from '../domain/knowledge.ts'
import photoCatalog from '../data/recipe-photos.json'

export interface RecipePhoto {
  src: string
  author: string
  sourceUrl: string
  license: string
  licenseUrl: string
  alt: string
}

const fallbackPhotos: Record<string, RecipePhoto> = photoCatalog

function DishPhoto({ photo }: { photo?: RecipePhoto }) {
  const [failed, setFailed] = useState(false)
  if(!photo || failed) return <div className="recipe-photo-missing"><ImageOff size={28} aria-hidden="true" /><span>照片待補</span></div>
  const src = /^https?:\/\//.test(photo.src) ? photo.src : `${import.meta.env.BASE_URL}${photo.src}`
  return <figure className="recipe-photo">
    <img src={src} alt={photo.alt} loading="lazy" onError={() => setFailed(true)} />
    <figcaption><span>料理示意照片</span><span><a href={photo.sourceUrl} target="_blank" rel="noreferrer">{photo.author}</a> · <a href={photo.licenseUrl} target="_blank" rel="noreferrer">{photo.license}</a></span></figcaption>
  </figure>
}

export function RecipeDetail({ recipe, plan, photo }: { recipe: Recipe; plan: Plan; photo?: RecipePhoto }) {
  const knowledge = knowledgeForRecipe(recipe)
  const resolvedPhoto = photo ?? fallbackPhotos[recipe.id]
  return <article className="recipe-article">
    <div className="section-title compact"><h2>{recipe.name}</h2>{plan.requested.includes(recipe.id) && <span className="tag">指定菜色</span>}</div>
    <DishPhoto key={resolvedPhoto?.src ?? recipe.id} photo={resolvedPhoto} />
    <div className="recipe-details">
      <section><h3>全家合計食材用量</h3><dl className="recipe-ingredients">{recipe.ingredients.map(item =>
        <div key={item.ingredientId}><dt>{ingredientName(item.ingredientId)}</dt><dd>{round(item.amount * plan.totalN)} {ingredients.find(ingredient => ingredient.id === item.ingredientId)?.unit}</dd></div>
      )}</dl></section>
      <section><h3>烹調步驟</h3><ol className="directions">{recipe.steps.map((step, index) => <li key={index}>{step}</li>)}</ol></section>
    </div>
    {knowledge.length > 0 && <section className="ingredient-knowledge" aria-label="食材營養小知識">
      <h3><Leaf size={18} aria-hidden="true" />食材營養小知識</h3>
      <div className="knowledge-grid">{knowledge.map(record => <article className="knowledge-card" key={record.knowledgeId}>
        <h4>{record.ingredientName}</h4><p>{record.excerpt}</p>
        {record.evidenceType.startsWith('人體') && <p className="knowledge-study-context">{record.conditions.split('\n')[0]}</p>}
        <details><summary><BookOpen size={14} aria-hidden="true" />適用條件與來源</summary>
          <p className="knowledge-conditions">{record.conditions}</p>
          <a href={record.sourceUrl} target="_blank" rel="noreferrer">{record.sourceLabel}</a>
        </details>
      </article>)}</div>
    </section>}
    <p className="provenance">食譜來源：示範資料</p>
  </article>
}
