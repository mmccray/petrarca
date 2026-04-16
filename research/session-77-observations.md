# Session 77 — Phase 1 Observations

**Date**: 2026-04-15
**Scope**: Observation phase before Phase 2. Examining entity-first Phase 1 in production.
**Method**: Direct DB inspection on server (`/opt/petrarca/data/petrarca.db`), 11 `knowledge_entities`, 2 voice-capture transcripts (`vt_1776272899_7705` Karl XII, `vt_1776274698_7861` Viking Paris).

## Summary

Phase 1 works. Entity-keyed captures produce rich, reviewable knowledge with excellent memory hooks. But observation surfaced **four concrete bugs** and **three design gaps** to resolve before Phase 2. All fixes are small — no rearchitecture required.

## What Works Well

### Question quality is genuinely good

The Claude-generated `rich_answer` + `memory_hook` payload is the strongest part of Phase 1. Representative examples from the 11 live entity items:

- **Karl XII memory hook**: "Karl XII took the throne in 1697, the same year Peter the Great embarked on his Grand Embassy to Western Europe — two young rulers whose collision would reshape the Baltic and define the 18th century."
- **Russian Campaign 1708 hook**: "Karl XII's Russian campaign (1708-1709) preceded Napoleon's equally disastrous Moscow invasion by exactly 104 years (1812), with both failing due to scorched earth tactics and brutal winters."
- **Charles the Fat hook**: "This was just 73 years after the Treaty of Verdun (843) split Charlemagne's empire — Charles the Fat briefly reunited it in 884, but his humiliation at Paris in 886 ensured it would never be whole again."
- **Abbo of Saint-Germain hook**: "Abbo wrote his account just two generations after the 843 Treaty of Verdun fractured the Carolingian Empire — the political weakness that allowed Vikings to penetrate so deep into Frankish territory."

These hooks exhibit the project's north-star "temporal hooks" principle (`memory/feedback_temporal_hooks.md`) — they anchor new knowledge to events the learner plausibly already knows. They are empirically better than many curriculum-path `rich_answer`s I reviewed in earlier sessions.

### Entity grouping is sensible

`VOICE_CAPTURE_ENTITY_PROMPT` produced 5 entities from the Karl XII transcript and 6 from the Viking Paris transcript. Distribution:

| Karl XII (5 entities, 15 facts) | Viking Paris (6 entities, 15 facts) |
|---|---|
| Karl XII of Sweden (6 facts) | Viking siege of Paris (5) |
| Great Northern War (3) | Count Odo (3) |
| Battle of Narva (2) | Emperor Charles the Fat (4) |
| Russian Campaign 1708-1709 (2) | Abbo of Saint-Germain (1) |
| Battle of Poltava (2) | Sigfred (1) |
| | Rollo (1) |

No over-splitting (every date did not become its own entity) and no under-splitting (one giant "Karl XII" with 15 facts). Minor events with a single mention still became their own entity (Abbo, Sigfred, Rollo) — debatable, but these ARE the chronicler identities, so having a dedicated card is actually valuable ("who wrote about this?" is a genuine retrieval target).

### Review-stream integration works transparently

All 11 entity items appear correctly in `generate_review_stream()`:
- `knowledge_weight=6.0` (between engaged=8 and mentioned=4, as intended)
- Score boost of +2 for date/event/person fact types
- 10 of 11 due at `-0.1d` (overdue by fractions of a day, created fresh)
- Karl XII already graded `knew` and rescheduled to +26.9d with stability 8.3d — FSRS path works identically to curriculum items

### Source excerpts are real quotes

Every `source_excerpt` I spot-checked appears verbatim in the transcript. The LLM is not confabulating quotes. This makes "About this card" provenance trustworthy when it surfaces.

### Wikidata linkage partially works

6 of 11 entity items got QIDs via the background resolver, including non-obvious matches:
- Battle of Narva → Q155726 ✓
- Battle of Poltava → Q152486 ✓
- Abbo of Saint-Germain → Q2622892 ✓ (Abbo Cernuus, correct person)
- Sigfred → Q67077076 ✓ (9th-century Danish king, LLM disambiguated past three distractors)
- Rollo → Q273773 ✓

## Bugs Found

### Bug 1: Double Wikidata resolution fire (HIGH)

**Symptom**: Every mention from the Karl XII and Viking Paris captures has exactly 2 rows in `entity_resolutions` — "Russia" resolved twice, "Paris" resolved twice, etc. 17 mentions → 34 resolution rows on one capture, 23 → 46 on the other.

**Root cause**: In the fallback path (`process_voice_capture()` → `_process_voice_capture_entity_path()` at `scripts/review_engine.py:4991`), the entity path fires its own `_resolve_voice_entities_background` thread at line 5402. Then control returns to `process_voice_capture()` which fires the resolution thread AGAIN at line 5026 with the same `entities_mentioned` list.

**Impact**: 2× Gemini extraction calls, 2× Wikidata search calls, 2× LLM disambiguation calls per entity-path capture. Also doubles `entity_resolutions` audit rows. The second resolution sometimes chooses a DIFFERENT QID than the first (e.g., "Burgundy" → Q530670 first, then Q1173 second — last write wins, depending on timing).

**Fix**: When the fallback case triggers the entity path, short-circuit the curriculum-path's resolution thread. Either skip the `if entities_mentioned` block at line 5021 when `entity_path_triggered`, or move the trigger to a single site that dispatches based on the path taken.

### Bug 2: `voice_transcripts.node_title` shows curriculum garbage (MEDIUM)

**Symptom**:
- `vt_1776272899_7705` (Karl XII capture) has `node_title='1693 Earthquake'`
- `vt_1776274698_7861` (Viking Paris) has `node_title='Charles Ives'`

Neither name has any relationship to the captured content.

**Root cause**: When the fallback routes to the entity path at `review_engine.py:4991`, the `entity_name` argument carries the curriculum-path's loose name match (the best available match from detected-entity-name scoring, regardless of relevance). The entity path then passes this to `_log_voice_transcript` at line 5392 as the fallback `node_title`.

**Impact**: Any UI that reads `voice_transcripts.node_title` (e.g., voice capture listing, admin screens) shows nonsensical labels. Doesn't affect knowledge_entities directly — these are independent — but it clutters the observability layer.

**Fix**: In `_process_voice_capture_entity_path`, override `node_title` to the primary entity from `entity_facts.keys()` (the first key grouped by Claude), not the curriculum-path's carry-over name.

### Bug 3: Wikidata search returns paintings for "Karl XII of Sweden" (HIGH — outside our code)

**Symptom**: `entity_resolutions` shows only 2 candidates for "Karl XII of Sweden": `Q119811370` (painting by Schröder) and `Q106357900` (painting by Ankarcrona). The actual historical person is Q52934 "Carl XII of Sweden" — not in the candidate list at all. Top score 0.537 < threshold 0.55, correctly returned `ambiguous`, no QID.

**Root cause**: Verified via direct `wbsearchentities` call. Searching "Karl XII of Sweden" on Wikidata returns only paintings. Searching "Charles XII of Sweden" returns Q52934 as the top hit. This is a Wikidata label/alias issue — Q52934's English label is "Carl XII of Sweden" (Swedish spelling), not "Karl XII". The "Karl" spelling isn't an alias.

**Impact**: Every capture using the "Karl" spelling will fail to resolve to the person. This will generalize to any historical figure with multiple European-language spellings whose Wikidata label is the less-common one.

**Fix options**:
1. **Resolver-level**: when only low-score candidates return AND they all match patterns suggesting artworks (labels beginning with "painting", descriptions containing "painting|statue|bust"), retry with canonicalized spelling variants ("Karl" → "Charles", "Frederick" → "Friedrich", etc.).
2. **LLM-disambig-level**: when candidates are weak, send the LLM a broader prompt that permits it to say "this is the wrong search result; the correct entity is Q52934" — but then we lose the hallucination guard. Too risky.
3. **Upstream**: add "Karl XII of Sweden" as an alias on Q52934 on Wikidata (days to propagate, but benefits everyone).

Recommended: option 1, with a hard-coded spelling table in limbic. Start with Swedish/German/French regnal name canonicalization.

### Bug 4: "Count Odo" resolved to "Fire Brigade Museum" (HIGH)

**Symptom**: "Count Odo" mentioned in Viking Paris capture — resolver chose **Q67389525 "Count Ödön Széchenyi Fire Brigade Museum"** in Istanbul. Correct answer is Q61097 "Odo of France" (king of West Francia 888-898).

**Root cause**: Scoring breakdown was `type=0.30 date=0.00 desc=0.61 coherence=1.00 rank=1.00 total=0.596`. The museum was the only candidate — resolver accepted it because the total crossed 0.55 threshold. No LLM disambiguation was run (status = "resolved" not "ambiguous" — single candidate, not a disambiguation case).

Date hint was empty because the Gemini extraction step didn't extract an 885-886 date for "Count Odo" specifically.

**Impact**: Real person mapped to wrong entity. `knowledge_entities` row `ent:count_odo` actually has QID Q67389525 pointing to a fire brigade museum. If the user later grades and the system deepens research on "Count Odo", it'll pull Fire Brigade Museum facts.

**Fix**: Make the resolver require LLM disambiguation (or reject outright) when `type_score < 0.5` AND `date_score < 0.5` simultaneously. Single-candidate-pass-through with no type or date match is unsafe. This specific case is also helped by fixing bug 3 (broader search that surfaces the actual Odo of France).

## Design Gaps

### Gap A: Entity cards have empty `follow_up_queries`

All 11 entity items have `follow_up_queries: []`. Curriculum items get 6 follow-ups generated by Gemini Flash in the main question pipeline (see `_generate_follow_up_queries` at `review_engine.py:1248`). But `generate_entity_question()` at `review_engine.py:1712` only calls `_key_fact_to_question` — which doesn't generate follow-ups.

**Impact**: The "sideways exploration" chips ("Also explore…") never appear for entity cards. This is where the north-star principle "Cards are mini-encyclopedias, follow-ups go sideways" should apply most — an entity card is exactly the kind of content that benefits from geography/counter-narratives/cross-period links.

**Fix**: Add a `_generate_follow_up_queries` call inside `generate_entity_question` after `_key_fact_to_question`. Pass `entity_name` as `node_title` and the accumulated `source_excerpt`s or `overall_summary` as `fact_ctx`.

### Gap B: `entity_type` defaults to "entity"

All 11 entity items have `entity_type = 'entity'` (from line 5237 fallback when no shared_entities type is known). That's a placeholder. Real types are available:
- from Wikidata (after resolution): P31 "instance of" → person/battle/monarch/etc.
- from the VOICE_CAPTURE_ENTITY_PROMPT fact types: each fact has `type: date|event|person|place|concept|cause|significance`

**Impact**: `domain_title` shown on review cards is always "Entity" (from `entity_type.title()`). Not a bad label, but we could do better: "Person", "Battle", "Event" would be richer visual cues.

**Fix**:
- Add `entity_type` to the `VOICE_CAPTURE_ENTITY_PROMPT` output — ask Claude to classify each top-level entity. Easy.
- After Wikidata resolution, enrich from P31. Slightly harder but gives "Battle" vs "Monarch" granularity.

### Gap C: Events + parentheticals break Wikidata search

Captures include entities like "Viking siege of Paris (885-886)", "Russian Campaign (1708-1709)", "Great Northern War (1700 attack)". All three came back `no_match` or unresolved because:
- "(885-886)" is treated as part of the search string by Wikidata; Q741443 "Siege of Paris (885-886)" IS a real Wikidata item but the search doesn't strip the qualifier.
- "Emperor Charles the Fat" has the honorific "Emperor" breaking the match (canonical Wikidata label is "Charles the Fat", Q150720).

**Fix**: Two options:
1. **Prompt-level**: add to VOICE_CAPTURE_ENTITY_PROMPT: "For `entities_mentioned`, use canonical Wikidata-style names — strip parenthetical dates and honorifics (no 'Emperor', 'King', 'Saint' prefixes unless they're part of the name)."
2. **Resolver-level**: have limbic's Wikidata search try a stripped variant if the original yields `no_match` or only weak (<0.5) candidates.

Prompt-level is cheaper and more reliable. Resolver-level is defense-in-depth.

## Implications for Phase 2

Before building Phase 2 entity-native question generation:

1. **Fix the four bugs above first.** They're small and directly improve data quality Phase 2 will build on. Rough effort: a few hours.
2. **Gap A (follow-ups) is essentially a Phase 2 precursor** — it's the only content source currently missing from entity cards. Do this alongside bug fixes.
3. **Gap B (entity_type) unlocks Phase 2's Wikidata property enrichment path.** Without `entity_type`, we can't decide which properties to pull (P22 father for persons, P580/P582 for reigns, P1366 for battles, etc.). Do this before Phase 2 proper.
4. **Gap C (canonical names) improves future resolution rate for all voice captures**, not just entity path.

## Phase 2 Design Revisions

Two revisions to the Phase 2 plan in `research/entity-first-architecture.md`:

**Revision 1**: Phase 2 should ONLY enrich existing facts, never generate new ones from Wikidata alone. The design doc flagged this as an open question; observation confirmed the user's stated preference ("books encode, system maintains"). Even Wikidata structural questions ("who succeeded X?") should be framed as enrichment — "you captured that X reigned from Y to Z; do you remember who succeeded?" — not as standalone Wikidata-sourced quizzes. This preserves the invariant that every card traces to something the user actually encountered.

**Revision 2**: Temporal neighbors should be scoped to entities the user has ALSO captured. A "meanwhile" context pulled from `shared_entities` globally will flood cards with nonspecific noise (every 9th-century capture gets "meanwhile, the Abbasid Caliphate ruled from Baghdad"). Scoping to `shared_entities` joined to `knowledge_entities OR knowledge_items` restricts context to the user's own graph — which aligns with the "temporal hook to things you know" principle.

## Recommended Session-77 Task Ordering

1. Fix Bug 1 (double resolution fire) — prevents further 2x cost accumulation.
2. Fix Bug 2 (node_title garbage) — 2-line change, makes observability readable.
3. Add `entity_type` to prompt output (Gap B). 
4. Add canonical-name instruction to prompt (Gap C).
5. Add `_generate_follow_up_queries` call inside `generate_entity_question` (Gap A).
6. THEN Phase 2: Wikidata property enrichment + co-occurrence + scoped temporal neighbors.

Bug 3 (Karl XII / Wikidata spellings) and Bug 4 (Fire Brigade Museum) are Priority 2 (Wikidata resolution quality) from the session prompt and deserve separate focus — they're resolver-level, not entity-path-level.
