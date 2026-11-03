// Build deterministic ER diagrams from the read-only PostgreSQL catalog snapshot.
import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';

const packageRoot = process.argv[2];
if (!packageRoot) throw new Error('Pass the bundled node_modules path as the first argument.');
const require = createRequire(path.join(packageRoot, '_erd_loader.cjs'));
const { instance } = require('@viz-js/viz');
const sharp = require('sharp');
const viz = await instance();
const out = path.resolve(process.argv[3] || 'docs/1102_V3/T01-1103-V2');
const catalog = JSON.parse(await fs.readFile(path.join(out, 'live-database-catalog.json'), 'utf8'));
const esc = (s) => String(s).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
const names = {
  recipe_data_releases:'食譜版本', source_archive:'原始來源', recipes:'食譜', recipe_steps:'料理步驟',
  recipe_ingredients:'配方食材列', ingredient_aliases:'標準食材與別名', allergens:'過敏原字典',
  nutrition_imports:'營養匯入批次', food_nutrition:'營養觀測', nutrition_sources:'營養原始來源',
  nutrition_source_links:'觀測與來源關聯', nutrition_food_mappings:'食材營養對照', t04_cooking_receipts:'烹調計算收據',
  conversion_factor_estimates:'換算與估值', price_collection_runs:'查價批次', price_quote_snapshots:'報價快照',
  retailer_product_mappings:'零售商品對照', users:'使用者', user_sessions:'登入工作階段',
  households:'家庭', household_memberships:'帳號家庭歸屬', household_members:'家庭成員',
  meal_requests:'餐食問卷', meal_request_revisions:'問卷版本', recognitions:'照片辨識',
  background_jobs:'背景工作', generation_results:'生成結果', meal_result_decisions:'結果決策',
  adopted_meal_plans:'已採用餐食', budget_months:'月額度', usage_reservations:'費用預留與結算',
  knowledge_crawl_requests:'待補知識食材', alembic_version:'Schema 遷移版本',
};
const groups = {
  '食譜與食材': ['recipe_data_releases','source_archive','recipes','recipe_steps','recipe_ingredients','ingredient_aliases','allergens'],
  '營養與估值': ['nutrition_imports','food_nutrition','nutrition_sources','nutrition_source_links','nutrition_food_mappings','t04_cooking_receipts','conversion_factor_estimates'],
  '價格': ['price_collection_runs','price_quote_snapshots','retailer_product_mappings'],
  '帳號與家庭': ['users','user_sessions','households','household_memberships','household_members'],
  '問卷與辨識': ['meal_requests','meal_request_revisions','recognitions'],
  '工作與餐食': ['background_jobs','generation_results','meal_result_decisions','adopted_meal_plans','knowledge_crawl_requests'],
  '額度與結構': ['budget_months','usage_reservations','alembic_version'],
};
const colors = ['#dcecff','#e1f0e7','#fff0d2','#e9e6fa','#e4eff2','#f7e5e4','#e8ebef'];
const primary = new Map();
const unique = new Map();
const edges = [];
for (const c of catalog.constraints) {
  if (c.kind === 'p' || c.kind === 'u') {
    const fields = c.definition.match(/\(([^)]+)\)/)?.[1].split(',').map(x=>x.trim()) ?? [];
    if (c.kind === 'p') primary.set(c.table,new Set(fields));
    if (!unique.has(c.table)) unique.set(c.table,[]);
    unique.get(c.table).push(fields);
  }
  if (c.kind === 'f') {
    const match = c.definition.match(/FOREIGN KEY \(([^)]+)\) REFERENCES [^(]+\(([^)]+)\)/);
    if (match) edges.push({ child:c.table, parent:c.referenced_table,
      childFields:match[1].split(',').map(x=>x.trim()), parentFields:match[2].split(',').map(x=>x.trim()), name:c.name });
  }
}
const focus = {
  recipe_data_releases:['release_id','dataset_version','source_sha256','source_kind','active'],
  source_archive:['raw_id','recipe_release_id','source_name','source_url','source_recipe_id'],
  recipes:['recipe_id','raw_id','recipe_release_id','standard_dish_id','recipe_name','original_servings','category','is_eligible'],
  recipe_steps:['step_id','recipe_id','step_no','instruction_clean','review_status'],
  recipe_ingredients:['recipe_ingredient_id','recipe_id','food_id','sequence','weight_g','source_weight_g','weight_status'],
  ingredient_aliases:['food_id','canonical_name','aliases','review_status'],
  allergens:['allergen_id','allergen_name','reference'],
  nutrition_imports:['import_id','source_files','imported_at'],
  food_nutrition:['nutrition_id','import_id','food_id','cooking_state','energy_kcal','protein_g'],
  nutrition_sources:['source_id','import_id','provider','source_record_id','source_url'],
  nutrition_source_links:['nutrition_id','source_id','included_in_calculation'],
  nutrition_food_mappings:['mapping_id','recipe_release_id','import_id','food_id','nutrition_id','source_id','approval_status'],
  t04_cooking_receipts:['receipt_id','recipe_release_id','nutrition_import_id','nutrition_id','nutrition_source_id','is_estimated'],
  conversion_factor_estimates:['id','kind','food_id','source','status'],
  price_collection_runs:['id','provider','started_at','status'],
  price_quote_snapshots:['id','run_id','canonical_name','price','currency','valid_until'],
  retailer_product_mappings:['id','provider','canonical_name','product_id','active'],
};
function groupColor(table) {
  return colors[Object.values(groups).findIndex(x=>x.includes(table))] || '#eeeeee';
}
function columns(table, compact) {
  const all = catalog.columns.filter(c=>c.table===table);
  if (!compact) return all;
  const fks = edges.filter(e=>e.child===table).flatMap(e=>e.childFields);
  const keep = new Set(focus[table] ?? [...(primary.get(table)||[]), ...fks, ...all.slice(0,3).map(c=>c.column)]);
  return all.filter(c=>keep.has(c.column));
}
function node(table, compact) {
  const count = catalog.tables.find(t=>t.table===table).rows;
  const rows = columns(table,compact).map(c=>{
    const pk = primary.get(table)?.has(c.column);
    const fk = edges.some(e=>e.child===table && e.childFields.includes(c.column));
    const flags = [pk?'PK':'',fk?'FK':''].filter(Boolean).join('/');
    const type = c.udt.replace('varchar','text');
    return `<TR><TD ALIGN="LEFT" WIDTH="42"><FONT COLOR="${pk?'#996c10':'#2563a2'}">${flags}</FONT></TD><TD PORT="p_${c.column}" ALIGN="LEFT">${esc(c.column)}</TD><TD ALIGN="LEFT"><FONT COLOR="#64748b" POINT-SIZE="10">${esc(type)}${c.nullable?' ?':''}</FONT></TD></TR>`;
  }).join('');
  return `"${table}" [label=<<TABLE BORDER="1" COLOR="#8593a3" CELLBORDER="0" CELLSPACING="0" CELLPADDING="5"><TR><TD COLSPAN="3" BGCOLOR="${groupColor(table)}" ALIGN="LEFT"><B>${table}</B><BR/><FONT POINT-SIZE="11">${names[table]}　${count} 筆</FONT></TD></TR>${rows}</TABLE>>];`;
}
function graph(tables, compact, title, mainChain=false) {
  const set = new Set(tables);
  const output = ['digraph Database {',
    'graph [rankdir=LR, bgcolor="white", pad="0.4", nodesep="0.48", ranksep="1.0", splines=spline, fontname="Arial", fontsize=22, labelloc=t, labeljust=l, concentrate=false];',
    `label="${title}\\nPK 主鍵　FK 外鍵　? 可空白　數字為實際筆數；端點 1 與 N 表示關係上限";`,
    'node [shape=plain, fontname="Arial", fontsize=12];',
    'edge [color="#8b98a8", penwidth=1.1, arrowsize=0.6, fontname="Arial", fontsize=10, dir=both];'];
  output.push(...tables.map(t=>node(t,compact)));
  const renderedEdges = [];
  for (const e of edges) {
    if (!set.has(e.parent) || !set.has(e.child)) continue;
    if (mainChain && (e.parent===e.child || (e.parent==='recipe_data_releases' && !['source_archive','ingredient_aliases','allergens'].includes(e.child)))) continue;
    const childCols=columns(e.child,compact).map(c=>c.column);
    const parentCols=columns(e.parent,compact).map(c=>c.column);
    const from='';
    const to='';
    const one=(unique.get(e.child)||[]).some(fields=>fields.length===e.childFields.length && fields.every(c=>e.childFields.includes(c)));
    output.push(`"${e.parent}"${from} -> "${e.child}"${to} [arrowtail=tee, arrowhead=${one?'tee':'crow'}, tooltip="${esc(e.name)}"];`);
    renderedEdges.push(e.name);
  }
  output.push('}');
  return {dot:output.join('\n'),tables:tables.length,foreignKeys:renderedEdges.length};
}
async function save(name,tables,compact,title,chain=false,png=true) {
  const data=graph(tables,compact,title,chain);
  await fs.writeFile(path.join(out,name+'.dot'),data.dot);
  const svg=manualSvg(tables,compact,title,chain);
  await fs.writeFile(path.join(out,name+'.svg'),svg);
  if (png) await sharp(Buffer.from(svg),{density:120,limitInputPixels:false}).png().toFile(path.join(out,name+'.png'));
  return {file:name,...data,dot:undefined};
}
function manualSvg(tables,compact,title,chain) {
  const width=compact?390:450, gap=110, rowH=27, headerH=68, margin=46;
  const entries=tables.map(table=>({table,fields:columns(table,compact)}));
  const selected=edges.filter(e=>tables.includes(e.parent)&&tables.includes(e.child)
    &&!(chain&&(e.parent===e.child||(e.parent==='recipe_data_releases'&&!['source_archive','ingredient_aliases','allergens'].includes(e.child)))));
  const layoutDot=['digraph Layout {','graph [rankdir=LR,ranksep=1.0,nodesep=0.65,splines=ortho];',
    'node [shape=box,label="",fixedsize=true];', 'edge [arrowhead=none];',
    ...entries.map(e=>`"${e.table}" [width=${width/72},height=${(headerH+rowH*e.fields.length)/72}];`),
    ...selected.map((e,i)=>`"${e.parent}" -> "${e.child}" [id="e${i}"];`),'}'].join('\n');
  const layout=JSON.parse(viz.renderString(layoutDot,{format:'json',engine:'dot'}));
  const [minX,minY,maxX,maxY]=layout.bb.split(',').map(Number);
  const xPos=x=>margin+x-minX,yPos=y=>140+maxY-y;
  const positions=new Map();
  for (const n of layout.objects) {
    const [x,y]=n.pos.split(',').map(Number);
    positions.set(n.name,{x:xPos(x)-width/2,y:yPos(y)-Number(n.height)*36});
  }
  const nodeMap=new Map(entries.map(e=>[e.table,{...e,...positions.get(e.table),h:headerH+rowH*e.fields.length}]));
  const canvasW=maxX-minX+2*margin;
  const canvasH=maxY-minY+200;
  const draw=[];
  draw.push(`<svg xmlns="http://www.w3.org/2000/svg" width="${canvasW}" height="${canvasH}" viewBox="0 0 ${canvasW} ${canvasH}" role="img" aria-label="${esc(title)}">`);
  draw.push(`<defs><marker id="one" markerWidth="12" markerHeight="14" refX="2" refY="7" orient="auto-start-reverse"><path d="M2 1 V13" fill="none" stroke="#667789" stroke-width="1.5"/></marker><marker id="many" markerWidth="13" markerHeight="15" refX="11" refY="7.5" orient="auto"><path d="M1 1 L11 7.5 L1 14 M1 7.5 H11" fill="none" stroke="#667789" stroke-width="1.5"/></marker></defs>`);
  draw.push(`<rect width="100%" height="100%" fill="#fff"/><g font-family="Microsoft JhengHei,Arial,sans-serif" fill="#172b42"><text x="46" y="46" font-size="27" font-weight="700">${esc(title)}</text><text x="46" y="79" font-size="15" fill="#52677c">資料庫：${catalog.database}　｜　PK 主鍵、FK 外鍵、? 可空白　｜　1 與 N 表示關係上限</text><text x="46" y="106" font-size="15" fill="#52677c">標題筆數為本次唯讀查詢的實際資料；${compact?'僅列重點欄位，完整欄位另見總圖。':'此圖包含全部 33 張表及全部實際外鍵。'}</text>`);
  for (const edgeLayout of layout.edges||[]) {
    const e=selected[Number(edgeLayout.id.slice(1))];
    const p=nodeMap.get(e.parent),c=nodeMap.get(e.child);
    const points=edgeLayout._draw_.find(d=>d.op==='b')?.points;
    if (!points) continue;
    const [px,py]=[xPos(points[0][0]),yPos(points[0][1])];
    const last=points.at(-1),[tx,cy]=[xPos(last[0]),yPos(last[1])];
    let route=`M${px},${py}`;
    for (let i=1;i<points.length;i+=3) route+=' C'+points.slice(i,i+3).map(pt=>`${xPos(pt[0])},${yPos(pt[1])}`).join(' ');
    const one=(unique.get(e.child)||[]).some(fields=>fields.length===e.childFields.length&&fields.every(f=>e.childFields.includes(f)));
    draw.push(`<g><title>${esc(e.parent+'.'+e.parentFields.join(',')+' → '+e.child+'.'+e.childFields.join(','))}</title><path d="${route}" fill="none" stroke="#9cabbc" stroke-width="1.4" marker-start="url(#one)" marker-end="url(#${one?'one':'many'})"/><text x="${px+8}" y="${py-8}" font-size="12" fill="#51687d">1</text><text x="${tx-17}" y="${cy-8}" font-size="12" fill="#51687d">${one?'1':'N'}</text></g>`);
  }
  for (const n of nodeMap.values()) {
    const count=catalog.tables.find(t=>t.table===n.table).rows;
    draw.push(`<g><rect x="${n.x}" y="${n.y}" width="${width}" height="${n.h}" rx="2" fill="#fff" stroke="#8b9aab"/><rect x="${n.x+1}" y="${n.y+1}" width="${width-2}" height="${headerH-1}" fill="${groupColor(n.table)}"/><text x="${n.x+13}" y="${n.y+25}" font-size="16" font-weight="700">${n.table}</text><text x="${n.x+13}" y="${n.y+50}" font-size="14">${names[n.table]}　${count} 筆</text>`);
    n.fields.forEach((c,i)=>{
      const y=n.y+headerH+rowH*i;
      if (i%2===1) draw.push(`<rect x="${n.x+1}" y="${y}" width="${width-2}" height="${rowH}" fill="#f6f8fb"/>`);
      const pk=primary.get(n.table)?.has(c.column),fk=edges.some(e=>e.child===n.table&&e.childFields.includes(c.column));
      const flag=[pk?'PK':'',fk?'FK':''].filter(Boolean).join('/');
      draw.push(`<text x="${n.x+10}" y="${y+19}" font-size="12" font-weight="700" fill="${pk?'#91620b':'#2563a2'}">${flag}</text><text x="${n.x+63}" y="${y+19}" font-family="Consolas,monospace" font-size="13">${esc(c.column)}</text><text x="${n.x+width-11}" y="${y+19}" text-anchor="end" font-size="11" fill="#62758b">${esc(c.udt)}${c.nullable?' ?':''}</text>`);
    });
    draw.push('</g>');
  }
  draw.push('</g></svg>');
  return draw.join('\n');
}
const checks=[];
checks.push(await save('erd-t01',groups['食譜與食材'],true,'目前 T01 食譜與食材關係（主要外鍵）',true));
checks.push(await save('erd-nutrition-prices', [...groups['營養與估值'], ...groups['價格'],'ingredient_aliases','recipe_data_releases'], true,'目前營養、價格與食材關係'));
checks.push(await save('erd-runtime', [...groups['帳號與家庭'],...groups['問卷與辨識'],...groups['工作與餐食'],...groups['額度與結構']], true,'目前帳號、問卷、工作與餐食紀錄關係'));
checks.push(await save('erd-all',catalog.tables.map(t=>t.table),false,'目前完整 PostgreSQL 結構（全部欄位與實際外鍵）',false,false));
await fs.writeFile(path.join(out,'diagram-validation.json'),JSON.stringify({database:catalog.database,actualTables:catalog.tables.length,actualForeignKeys:edges.length,diagrams:checks},null,2)+'\n');
console.log(JSON.stringify(checks));
