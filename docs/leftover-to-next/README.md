# Leftover-to-Next — branch-safe implementation plan

**Status:** Planning and reviewable prototype only. **Not** revenue-ready or content-rights-approved. 2026-11-03.

## Scope, preservation, architecture
- The original repository serves SaaS Matrix articles plus an independent SlotBridge Korea Worker/D1 product. Do not modify `index.html`, `src/worker.js`, `wrangler.json`, `slotbridge/*`, `migrations/*`, or existing tests.
- Add the new website only under `/leftover-to-next/*`. Use dependency-free frontend, client-local pantry storage; add namespaced backend later only if approved.
- North-star: **weekly cooking sessions completed using existing pantry items**. Business outcome: reduce unnecessary purchasing while monetizing only the genuinely missing ingredients.
- Two entry journeys: (1) verified viral/trending meal → compare to pantry → buy only gaps → cook → confirm stock deduction, (2) fridge-first → foods to use soon → recipe matching → purchase gap → cook.
- Initial international English-language experience, US retail first if and only if official Instacart partner approval is secured; other countries always have a shopping-list-only fallback.
- The Pantry already supports importing Instagram/TikTok/YouTube recipe links, pantry matching, and cook-to-deduct; Instacart already provides recipe shopping pages. **These features alone are NOT a moat.**
- Core competitive hypothesis to test: current social food trends × use-soon stock priority × minimum additional buying × creator attribution × actionable illustrated cooking.

## Initial P0 features
1. Original demo dishes, explicitly not represented as live social trends.
2. Manual on-device stock by canonical ingredient, quantity, unit and user-entered reminder date.
3. Only exact-unit matching; display unmatched quantities explicitly, no fake unit conversion or food-safe verdict.
4. Ingredient coverage and use-soon relevance; recipe steps and timer; shop-only-what-is-missing export.
5. Stock deduction only after explicit completion; one-step undo.
6. Country preference and commerce **OFF until approval**.
7. No account, PII backend, paid APIs, social clipping, SEO indexing, affiliate links, or fabricated revenue in demo.

## Data model (target, not fully implemented)
`Recipe{id,title,locale,servings,ingredients:[{ingredientId,amount,unit,allergens}],steps:[{order,text,durationSec,licensedMedia?}],source:{url,creator,verifiedAt,rightsEvidence},trend:{claim,evidenceUrls,observedAt},reviewStatus}`
`PantryItem{ingredientId,quantity,unit,storageType,bestBeforeDate?,userNote?}`
`RetailOffer{country,provider,ingredientId,sku,deeplink,affiliateApproved,termsVersion,price?,stockCheckedAt?}`

## Safety and legal hard gates
- Never blindly scrape Instagram/YouTube/TikTok or copy a video, screenshot, caption or recipe body without rights. Original editorial recipe and appropriately licensed illustration required. Official video embeds/timestamps only when allowed; no downloading/cropping/rehosting.
- Do not declare perishable foods safe based only on best-before dates. Unknown storage/allergen information stays UNKNOWN.
- Without authorized grocery feed and partner contract, do not display merchant offers, claimed prices, retailer availability or affiliate sale links.
- Instacart developer program has API and separately attributed Impact affiliate process; production approval required. Walmart Creator publicly states US-residency conditions. Amazon Associates is per-region, approval not guaranteed.
- Any public affiliate links require clear proximate disclosure and sponsored attribution; no synthetic conversion reporting.
- Publish only independently reviewed trend/source claims, and retain `noindex` until release gates.

## Tickets and Codex prompts
Read [CODEX_TICKETS.md](CODEX_TICKETS.md). Use **one ticket per Codex task**, gate each, and do not merge or publicly deploy without operator approval.

## Primary source references
- https://thepantry.app/
- https://docs.instacart.com/developer_platform_api/get_started/recipe
- https://docs.instacart.com/developer_platform_api/guide/concepts/launch_activities/conversions_and_payments/
- https://developers.google.com/youtube/iframe_api_reference
- https://developers.google.com/youtube/terms/developer-policies
- https://www.foodsafety.gov/food-safety-charts/cold-food-storage-charts
- https://www.ftc.gov/business-guidance/resources/disclosures-101-social-media-influencers
- https://newsroom.pinterest.com/en-gb/news/spring-trend-report-2026/
