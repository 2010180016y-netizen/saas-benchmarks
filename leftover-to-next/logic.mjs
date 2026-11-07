/* Leftover-to-Next: pure, dependency-free pantry matching and shopping calculations.
 * All values are explicit; unknown conversions never guessed. */
export const normalize = value => String(value ?? '').trim().toLowerCase().replace(/\s+/g, ' ');
export const safeQuantity = value => Math.max(0, Number.isFinite(Number(value)) ? Number(value) : 0);
export const unitsMatch = (a,b) => normalize(a) === normalize(b);
export const toDay = (iso) => {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(iso || ''))) return null;
  const d = new Date(`${iso}T00:00:00Z`);
  return Number.isNaN(d.getTime()) || d.toISOString().slice(0,10)!==iso ? null : d;
};
export function daysUntil(iso, today = new Date()) {
  const d = toDay(iso);
  if (!d) return null;
  const day = Date.UTC(today.getUTCFullYear(),today.getUTCMonth(),today.getUTCDate());
  return Math.round((d.getTime()-day)/86400000);
}
export function ingredientGap(required, pantry) {
  const matching = pantry.filter(item => normalize(item.id) === normalize(required.id));
  const convertible = matching.filter(item => unitsMatch(item.unit, required.unit));
  const available = convertible.reduce((sum,item) => sum + safeQuantity(item.quantity),0);
  const unknownUnit = matching.length > 0 && convertible.length===0;
  const missing = Math.max(0,safeQuantity(required.quantity)-available);
  return {...required, available, missing, unknownUnit, satisfied: missing <= 0 && !unknownUnit};
}
export function assessRecipe(recipe, pantry, today = new Date()) {
  const gaps = recipe.ingredients.map(x=>ingredientGap(x,pantry));
  const covered = gaps.filter(x=>x.satisfied).length;
  const expiring = new Set(pantry.filter(p=>{const d=daysUntil(p.bestBefore,today); return d!==null && d>=0 && d<=3 && safeQuantity(p.quantity)>0;}).map(x=>normalize(x.id)));
  const soonUsed = gaps.filter(x=>expiring.has(normalize(x.id)) && x.available>0).length;
  const mismatches = gaps.filter(x=>x.unknownUnit).length;
  const missingCount = gaps.filter(x=>x.missing>0).length;
  return {recipe, gaps, covered, missingCount, soonUsed, mismatches,
    score: covered*6 + soonUsed*8 - missingCount*4 - mismatches*5};
}
export function shoppingGaps(recipe, pantry) {
  return assessRecipe(recipe,pantry).gaps.filter(x=>x.missing>0 || x.unknownUnit).map(g=>({id:g.id,name:g.name, quantity:g.missing,unit:g.unit,requiresReview:g.unknownUnit}));
}
export function deductPantry(recipe, pantry) {
  const next = pantry.map(x=>({...x}));
  for(const ingredient of recipe.ingredients) {
    let remainder=safeQuantity(ingredient.quantity);
    const candidates = next.filter(item=>normalize(item.id)===normalize(ingredient.id) && unitsMatch(item.unit,ingredient.unit))
       .sort((a,b)=>(toDay(a.bestBefore)?.getTime() ?? Infinity)-(toDay(b.bestBefore)?.getTime() ?? Infinity));
    for (const item of candidates) {const used=Math.min(safeQuantity(item.quantity),remainder);item.quantity=Math.max(0,safeQuantity(item.quantity)-used);remainder-=used;if(remainder<=0)break;}
  }
  return next.filter(x=>safeQuantity(x.quantity)>0);
}
