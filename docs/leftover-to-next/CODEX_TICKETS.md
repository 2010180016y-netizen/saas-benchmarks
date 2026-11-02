# Codex Execution Tickets — Leftover-to-Next

**Workflow:** one ticket at a time; audit code/diff, execute tests, explicit PASS/FAIL/BLOCKED; no unattended merge to main or public launch.

## Mandatory shared agent instruction (paste before EACH prompt)
```
You are a staff engineer + QA + privacy/rights reviewer working in 2010180016y-netizen/saas-benchmarks. Read docs/leftover-to-next/README.md and current code before changes. Preserve SaaS Matrix and SlotBridge routes, data, cron, migration and release guards. Make minimal file-scoped changes. All unauthorised social scraping, video downloads/reuploads, wholesale recipe copying, unapproved merchant checkout, fake trend badges, fake sales and safety assurances are prohibited. No secrets in git. Do not remove demo noindex/affiliate-off before explicit rights, legal and partner proof. Run relevant tests, record exact commands/results; mark unrun NOT_RUN. End with: files changed, tests, rights/commerce gates, unresolved dependencies, next ticket. Stop when scope complete.
```

| Ticket | Stage | Depends | Target result / acceptance |
|---|---|---|---|
| **LTN-00** Repo coexistence audit | P0 | none | inventory routes, tests, preserve main/SlotBridge |
| **LTN-01** Pantry & unit correctness | P0 | 00 | deterministic match, lot ordering, unit unknown, undo tests |
| **LTN-02** UX / mobile / cooking view | P0 | 01 | accessible 360px, timer, independent SVG/steps |
| **LTN-03** Servings + consolidated shopping | P0 | 01-02 | multiple meals, scale, no duplicate stock, export |
| **LTN-04** Source provenance CMS | P1 | 00 | draft/review/publish with rights evidence |
| **LTN-05** Social trend discovery pipeline | P1 | 04 | permissible source, evidence, review queue, no auto publish |
| **LTN-06** Original visuals + legal embed | P1 | 04-05 | IFrame time marker, official links, takedown, no clipping |
| **LTN-07** Country/merchant adapter | P1 | 03-04 + partner approval | US Instacart if eligible; else shopping export |
| **LTN-08** Food safety & allergens | P1 | 01,04 | reviewed storage rules, warnings, no unsafe recommendation |
| **LTN-09** Revenue / consent analytics | P1 | 07-08 | confirmed affiliate only; disclosures & privacy |
| **LTN-10** 7-day plan | P2 | 03,08 | minimize gaps and food waste, no double spending stock |
| **LTN-11** Household share | P2 | 10+security approval | optional auth, RLS/deletion/consent; default no login |
| **LTN-12** SEO/retention | P1 | 04,06,08,09 | original verified pages, no thin content; noindex gating |
| **LTN-13** Independent release audit | Gate | all in-scope | technical vs commercial status explicitly separated |

## Sequential prompts

### LTN-00
```
Inspect current branch, main SHA, static routes, worker routing, D1 migrations and existing tests. Verify which files are safe to add under leftover-to-next/. Run relevant baseline and newly added tests, document exact outcomes and preservation risks. No feature changes, merges or deploys.
```

### LTN-01
```
Implement/harden canonical pantry schema and deterministic pure tests: lots with expiry reminders, exact compatible units, missing quantities, invalid/zero input, duplicate items, localStorage corruption, explicit cook-to-deduct, one-click undo. Never infer spoilage/safety from a best-before date. Preserve existing external APIs untouched.
```

### LTN-02
```
Make a mobile-first, accessible single-screen discover→pantry→shopping→cook workflow. Add reviewable original SVG/CSS illustrations per step, timer, keyboard UX, reduced motion and clear unknown/error states. No copyrighted third-party media; measure mobile overflow and focus.
```

### LTN-03
```
Scale by servings and consolidate shopping across 1-3 chosen recipes without double-counting pantry availability. Reject unsupported unit conversions. Label output 'minimum missing ingredients', not 'cheapest', unless approved real-price data exists. Export copy/CSV and write tests for lots, serving fractions, duplicate ingredient across meals and incomplete stock.
```

### LTN-04
```
Create a source-provenance and content approval data model: original or licensed recipe, creator, verified source link, original step text, rights evidence, review date, regional trend evidence, status DRAFT→REVIEWED→PUBLISHED→RETIRED. Block any publishing missing required rights and safety review. Initial editorial queue can use static JSON; no unauthorized crawling.
```

### LTN-05
```
Create a permissioned trend discovery workflow. Use verified official trend reports and allowed platform APIs or manual operator sources only; distinguish 'popular claim' from source-observed data and show confidence/evidence. Pickle may monitor and report, never publish or auto engage. Deliver review queue, terms checklist and reproducible evidence log; avoid scraped videos/captions/images.
```

### LTN-06
```
Implement step-by-step media as own/licensed diagrams and platform-supported embeds. For YouTube, use official IFrame Player startSeconds/endSeconds only on verified timestamps; do not download/crop/rehost or obscure the player. For Instagram/TikTok use official allowed embeds or creator links. Test removal/unavailable source fallback and rights-based rendering.
```

### LTN-07
```
Create provider interface with country, access approval, matching SKU/unit/pack size, delivery area, optional verified price, allowed affiliate deep link and revocation. US Instacart optional and OFF until actual API key + production access + Impact approved. Use server-side secrets only; cache generated recipe URLs per provider docs; do not manufacture tracking params. Other countries export honest shopping list.
```

### LTN-08
```
Food safety review using foodsafety.gov and local authorities: distinguish quality and safety labels, safe storage caveats, allergens unknown, substitutions require review, spoilage reported→exclude recipe recommendation. Create 20 golden QA cases; incorrect/unknown data should fail safely and visibly.
```

### LTN-09
```
Add optional privacy-respecting analytics: cook completion, pantry item usage, outclick, confirmed affiliate payout (never infer from clicks or cart creation). Proximate affiliate disclosure and sponsored rel only for approved provider. Consent-based events only; no raw pantry histories to advertisers. No ads or commissions claimed during demo.
```

### LTN-10
```
Add weekly plan with use-soon stocks, serving-adjusted quantities, merged grocery gaps and mobile calendar. Recompute on meal changes; do not deduct inventory until confirmed cook; unknown/unsafe items never selected solely due to expiry score.
```

### LTN-11
```
Assess household sync need with actual user evidence. If approved, design auth, RLS, delete/export, scoped shared inventories, explicit invite and revocation, realistic storage costs and abuse tests. Otherwise BLOCKED and keep anonymous local-only mode.
```

### LTN-12
```
Once content-rights and commercial gates pass, add truthful Recipe structured data, canonical URLs, original metadata and indexation. Avoid scaled or duplicate affiliate pages. Measure real organic sessions, returning cooks and affiliate outcomes with consent. If external gates fail preserve noindex.
```

### LTN-13
```
Independently run Node tests, existing Python regression suites, exact-route Cloudflare checks, source-rights review, food-safety gate, approved merchant test and privacy/consent. Issue separate P0_STATIC_DEMO / P1_CONTENT_READY / P1_AFFILIATE_READY / PUBLIC_RELEASE verdicts and evidence. Do not modify SlotBridge release gates or merge/deploy without owner approval.
```

## Immediate next step
Start at **LTN-00**. Do not skip to LTN-07 or mark the project commercially ready based only on passing static tests.
