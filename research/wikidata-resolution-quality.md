# Wikidata Resolution Quality — Known Failure Modes & Fixes

Companion to `wikidata-deployment-guide.md`. That doc is the **runbook** for operating the resolver; this doc tracks **where it breaks and why**, and what we've hardened against.

Every entry here started as an observation in a session, turned into a resolver-level fix in `limbic/`, and left a test behind. Don't add entries unless there's a corresponding test.

## Architecture refresher

```
mention → WikidataResolver.resolve()
            ├─ existing_kb_lookup → kb_hit (short-circuit)
            ├─ client.search()   → raw candidates
            ├─ client.get_many() → entity payloads
            ├─ _score_candidate  → per-heuristic scores (type/date/desc/coherence/rank)
            └─ _decide           → resolved | ambiguous | no_match
```

All hardening lives in `limbic/hippocampus/wikidata_resolve.py` (not the low-level client). The client is a thin, cache-backed API wrapper — we keep it naive.

## Failure mode: non-label regnal spellings

**Reported**: Session 77 observation (Karl XII of Sweden). *Status*: hardened in Session 78.

`wbsearchentities` is keyed on Wikidata labels and aliases. When the capture uses a non-label spelling of a historical ruler's name, the search may return zero useful candidates — or worse, paintings/statues that happen to carry the searched spelling in their title.

Concrete example:
- Mention: `"Karl XII of Sweden"`
- Q52934 (the actual king) is labeled `"Carl XII of Sweden"` in English; `"Karl"` is not an alias.
- Search returns only Q119811370 and Q106357900 — two paintings titled "Karl XII of Sweden".

Neither painting is Q5 (human), so `type_score=0.30` for both. Top score 0.537 < threshold 0.55 → correctly flagged ambiguous, but no QID resolved.

### The fix

`_regnal_variants(mention)` in `wikidata_resolve.py` generates spelling variants for the first token of a regnal-looking mention. The resolver retries `client.search()` with each variant when:

1. Initial search returned zero candidates, OR
2. `type_hint='person'` but no returned candidate has `P31=Q5`.

**Regnal-shape gate**: variants only generate when the mention has a Roman numeral suffix (`II`, `III`, `XII`) OR an "of &lt;place&gt;" clause. This avoids rewriting normal modern names like "Karl Schmidt".

### The table

Each tuple in `REGNAL_NAME_VARIANTS` is a class of interchangeable regnal forenames. The resolver substitutes the mention's first token with each sibling in the matching class.

```python
REGNAL_NAME_VARIANTS: tuple[tuple[str, ...], ...] = (
    ("Karl", "Carl", "Charles", "Carolus", "Carlos"),
    ("Friedrich", "Frederick", "Frederik", "Frederic", "Federico"),
    ("Wilhelm", "William", "Willem", "Guillaume", "Guillermo"),
    ("Heinrich", "Henry", "Henri", "Enrico", "Enrique"),
    ("Ludwig", "Louis", "Ludovico", "Luis", "Lodewijk"),
    ("Philipp", "Philip", "Philippe", "Felipe", "Filippo"),
    ("Gustaf", "Gustav", "Gustavus", "Gustave"),
    ("Erik", "Eric", "Erich"),
    ("Pyotr", "Peter", "Pietro", "Pierre", "Pedro"),
    ("Johann", "Johannes", "John", "Giovanni", "Juan", "Jean"),
    ("Jakob", "James", "Jacob", "Giacomo", "Jaime"),
    ("Rudolf", "Rudolph", "Rodolfo"),
    ("Franz", "Francis", "Francesco", "François", "Francisco"),
    ("Leopold", "Leopoldo"),
    ("Maximilian", "Maximiliano"),
    ("Alexander", "Aleksandr", "Alessandro"),
    ("Nikolaus", "Nicholas", "Nikolai", "Niccolò"),
    ("Olaf", "Olav", "Olof"),
    ("Håkon", "Haakon", "Hakon"),
    ("Sigismund", "Zygmunt"),
)
```

**Principle for additions**: only add pairs where the substitution is genuinely used for the same historical person across sources. Translation pairs alone (e.g., "Jean" → "John" for any random Frenchman) don't qualify — the table would bloat the candidate set for common given names.

**Tests**: `tests/test_wikidata_resolve.py` covers:
- Variant generation for regnal-shaped mentions
- Non-regnal mentions produce no variants ("Karl Schmidt" unchanged)
- Retry on zero candidates
- Retry on type_hint mismatch with all candidates (paintings case)
- No retry when a valid human is already in the candidate set

### Expansion guidance

When you observe a new regnal-name failure:
1. Verify it's a spelling issue, not a Wikidata gap (the person must actually have a QID).
2. Add the variant class to `REGNAL_NAME_VARIANTS`.
3. Add a unit test in `test_wikidata_resolve.py` that asserts the new variant appears in `_regnal_variants()`'s output.
4. Update the table above.

## Failure mode: weak-match single-candidate pass-through

**Reported**: Session 77 observation (Count Odo → Fire Brigade Museum). *Status*: hardened in Session 78.

When `wbsearchentities` returns exactly one candidate, there's no second-best to compare against. The margin check becomes trivially satisfied (`second_total == 0.0`). If the total crosses `ABSOLUTE_THRESHOLD` on description/coherence/rank signals alone — despite structural signals saying "wrong entity" — the resolver silently commits.

Concrete example:
- Mention: `"Count Odo"` (type_hint=person, date_hint=880-900)
- Wikidata returns only Q67389525 "Count Ödön Széchenyi Fire Brigade Museum" (Istanbul, inception 1985)
- Scoring: `type=0.30, date=0.00, desc=0.61, coherence=1.00, rank=1.00, total=0.596` → passes threshold → resolved
- Result: `ent:count_odo.wikidata_qid = Q67389525` — a museum in Istanbul mapped to a 9th-century Frankish king.

The resolver had two concrete negative signals (type and date both < 0.5) and ignored them.

### The fix

`WikidataResolver._is_weak_structural_match(candidate, type_hint)`:
- Returns True iff `type_hint` was supplied AND `type_score < 0.5` AND `date_score < 0.5`.
- `type_score = 0.3` is a hard P31-mismatch; `type_score = 0.5` (neutral) doesn't trigger.
- `date_score < 0.5` requires both candidate and hint to have dates, and plausibility to drop below neutral — a real conflict, not missing data.

`_decide()` now requires `passes_abs AND passes_margin AND NOT weak_structural` before resolving. When weak_structural fires, status becomes `ambiguous` and the candidate is preserved for downstream LLM disambiguation.

**Important**: the rule only fires when both hints were provided. A bare mention with no hints has neutral scores and follows the original behavior. This keeps the rule conservative — it guards against confident wrong matches, not against cheap searches.

**Tests**: `tests/test_wikidata_resolve.py` covers:
- Single-candidate museum mismatch downgraded to ambiguous with type=person, date=880-900
- No downgrade when type_hint is missing (neutral type_score)
- No downgrade when type_score=1.0 (hard P31 hit)

### What this doesn't fix

The rule only catches false-positive *resolves*. It doesn't:
- Surface the correct entity (Q61097 Odo of France) — that requires a broader search, not implemented yet.
- Catch wrong matches when hints aren't supplied — upstream (Gemini extraction) should always populate type and date hints when context allows.
- Catch wrong matches when both hints happen to coincidentally match a wrong candidate (rare).

Downstream LLM disambiguation (already implemented in `review_engine.py`'s `_resolve_voice_entities_background`) now gets a shot at rejecting the candidate or choosing `null`. The hallucination guard in `validate_chosen_qid` still applies — the LLM can't invent a QID that wasn't in the candidate set.

## Backfill — when a rule ships

After fixing a failure mode:
1. Identify existing resolutions that would flip under the new rule. Typically a small set — query `entity_resolutions` for the pattern.
2. Delete those rows and re-run resolution from the original capture.
3. Update `knowledge_entities.wikidata_qid` where the re-resolution changed outcome.
4. If a re-resolution flipped a `resolved` → `no_match` / `ambiguous`, also nullify `shared_entities.wikidata_qid` for that entity_id.

See `scripts/reprocess_voice_with_qids.py` for the existing end-to-end reprocessing path.
