Session 77 — Entity-First Architecture Phase 2: Entity-Native Question Generation + Observation

## Context from Session 76

Phase 1 of the entity-first architecture shipped and is live. Voice captures about topics outside existing curricula now produce FSRS-scheduled `knowledge_entities` rows with rich cached questions. See:
- `research/entity-first-architecture.md` — 5-phase design doc and dependency audit
- `research/session-changelog.md` — Session 76 recap with all commits
- `memory/session76_entity_first_architecture.md` — session memory with validation details

**Current state:**
- `knowledge_entities` table: 11 rows from Karl XII + Viking Paris captures (5 + 6). All have cached_question, some auto-linked to Wikidata QIDs.
- Review stream mixes entity items with curriculum items — validated with real capture → grade → reschedule flow.
- Two blockers fixed along the way: `STRUCTURAL_ONLY=True` was live (reverted), Gemini API key now persists across server restarts via systemd `EnvironmentFile`.

## Observation Phase (Priority 0 — do this before coding)

Phase 1 ships but hasn't been used in real sessions yet. Before building Phase 2, **observe how it performs in actual review usage**:

1. **Open the app on the phone** (or run `curl -X POST :8090/curriculum/review/generate -d '{"limit":30}'`) and step through a real review session that includes entity items. Check:
   - Do entity cards feel natural or jarring next to curriculum cards?
   - Is the `domain_title` (e.g., "Entity", "Person") a good visual cue, or confusing?
   - Are the entity questions actually good, or are they generic/shallow compared to curriculum key_facts questions?
   - Does the stream interleave entity items reasonably, or do they cluster?

2. **Do a fresh novel-topic voice capture** about something you're actually curious about. Observe:
   - Does the `VOICE_CAPTURE_ENTITY_PROMPT` group facts by entity sensibly, or does it over-split (every date becomes an entity) or under-split (one giant "Topic X" entity with 20 facts)?
   - Are the `source_excerpt` quotes useful or noisy?
   - Do `confidence_tagged` values surface anywhere useful? (Currently they're stored but nothing consumes them.)

3. **Check the Wikidata linkage pipeline** for the Karl XII / Viking captures:
   - "Karl XII of Sweden" came back as `ambiguous` (confidence 0.54) — no QID assigned. Why? The resolver has an LLM disambiguation step — did it run? What were the top candidates?
   - "Great Northern War" wasn't in `entity_resolutions` at all — likely Claude dropped it from `entities_mentioned`. Is this a prompt issue or a capacity issue?
   - Check `SELECT status, COUNT(*) FROM entity_resolutions WHERE created_at > ...` for overall resolution quality on entity-path captures.

Write findings to a brief `research/session-77-observations.md` before starting Phase 2 code. The goal is to know what to actually build — don't front-run the user's judgment.

## Priority 1 — Phase 2: Entity-Native Question Generation

The current `generate_entity_question()` reuses `_key_fact_to_question()` with entity name/description substituted for node_title/description. This works but the questions are constrained to the facts extracted from the original voice capture. Phase 2 makes questions richer using the **entity graph**.

**What Phase 2 should add:**

1. **Wikidata property enrichment** — for entities with QIDs, pull structured properties and use them in question generation:
   - P22 (father), P25 (mother), P26 (spouse), P40 (child) for person relationships
   - P27 (country of citizenship), P19 (place of birth), P20 (place of death)
   - P569 (date of birth), P570 (date of death), P580/P582 (reign start/end)
   - P2962 (successor), P1365 (replaces) for rulers
   - Use limbic's `WikidataClient` (already imported) to fetch these.

2. **Temporal neighbors** — find other entities in `shared_entities` with overlapping date ranges (±50 year window — same logic as `_get_temporal_cross_references()` but over `shared_entities` instead of `curriculum_nodes`). Surface them as "meanwhile" context in the rich_answer.

3. **Co-occurrence in voice captures** — entities mentioned in the same voice capture are semantically linked. Query `voice_transcripts.llm_result` for entities co-occurring with this one. Great for memory hooks like "you also talked about X when discussing Y."

4. **Entity graph in prompts** — pass the enriched entity context (Wikidata props + temporal neighbors + co-occurring entities) into `_key_fact_to_question()`'s `_ENRICH_PROMPT` (currently takes node_description only). This should produce questions like "What was Rollo's relationship to William the Conqueror?" rather than just recitation of captured facts.

**Design question to settle in observation phase**: should Phase 2 generate NEW facts using Wikidata + LLM, or only enrich EXISTING voice-captured facts? The design principle "books encode, system maintains" suggests the latter — don't invent facts the user never encountered. But Wikidata relational questions ("who succeeded X?") are probably safe because they're structural.

## Priority 2 — Wikidata Resolution Quality

From Session 76 observations:
- Karl XII flagged `ambiguous` — the resolver has an LLM disambiguation step but returned confidence 0.54. Investigate whether the disambiguation is running and why it's not confident.
- Some entities (Great Northern War, Russian Campaign 1708-1709) never made it to `entity_resolutions`. The capture's `entities_mentioned` list came from Claude in the entity path prompt — likely Claude didn't include events. Fix the prompt to explicitly list events.
- Entity `entity_type` defaults to "entity" for freshly-created `knowledge_entities` rows (no type detection in the entity capture LLM call). Add `entity_type` to the prompt output or derive from Wikidata after resolution.

## Priority 3 — Entity Item UX Polish

Minor UX issues to address based on observation:

1. **Domain label**: Entity items show `domain_title = entity_type.title()` (e.g., "Entity", "Person"). Consider a better label — maybe the `wikidata_qid` or the `source_text` from voice capture ("From podcast: ...")? The `about-this-card` modal could show which voice capture the entity came from.

2. **Entity intro cards**: The existing `entity_intro` card type (in `_insert_entity_intro_cards()` in curriculum_db.py) briefs users on unknown entities before the first question. For entity-keyed knowledge_entities that ARE themselves entities, should we insert intro cards for them? Or is the first question already an intro?

3. **Client-side entity badge**: Phase 1 deliberately made no client changes — entity items render as `type:'review'`. Once the system is proven, consider a subtle entity badge or icon (distinct from 📖 for book, 🎙 for voice elicitation).

## Priority 4 — Phase 3 Preview (Unified Stream Scoring)

Currently entity items use fixed `knowledge_weight=6.0`. Phase 3 is designing proper stream scoring that treats entity items as first-class alongside curriculum items — not above, not below, but integrated by actual learning priority. This needs real usage data before designing. Do not start Phase 3 in this session unless observation makes it obvious.

## Key Files

- **Phase 1 code (recent)**:
  - `scripts/db.py` — `knowledge_entities` table (search for "entity-first")
  - `scripts/review_engine.py` — `VOICE_CAPTURE_ENTITY_PROMPT`, `_process_voice_capture_entity_path()`, `generate_entity_question()`, `_link_ke()` helper in `_resolve_voice_entities_background()`
  - `scripts/curriculum_db.py` — entity query block in `generate_review_stream()` (search for "knowledge_entities")

- **Phase 2 targets**:
  - `scripts/review_engine.py` — `_ENRICH_PROMPT` (~line 1278), `_key_fact_to_question()` (~line 1334), `generate_entity_question()` (~line 1728)
  - `research/entity-first-architecture.md` — Phase 2 section (lines ~230-245)
  - Wikidata client: `limbic.amygdala.wikidata.WikidataClient`
  - Entity co-occurrence: `voice_transcripts.llm_result` JSON

## Commits to Reference

- `0f83650` — Entity-first Phase 1 core (knowledge_entities + voice capture + review stream)
- `c975c7d` — Entity path fallback widened (when curriculum LLM returns no assessments)
- `a8b27b6` — Write-lock discipline in pregen

## North Star

Phase 1 made voice captures about novel topics work. Phase 2 makes those entity items *good* — rich enough that the user prefers reviewing entity cards over curriculum cards when the entity is genuinely interesting. If Phase 2 succeeds, Phase 3-5 become clearer (stream unification, curriculum as overlay, organic growth). If Phase 2 produces shallow questions, we need to rethink the entity context sources.

The observation phase matters most. Do not skip it.
