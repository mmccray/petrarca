#!/usr/bin/env python3
"""Backfill Wikidata entity resolutions for historical voice transcripts.

All 10 voice_capture + voice_capture_entity transcripts predate the Wikidata
entity resolution pipeline. They have entities_mentioned in their llm_result
but no entity_resolutions audit trail. This script runs
_resolve_voice_entities_background() on each.

Rate-limited: 15s between transcripts (Gemini + Wikidata API limits).
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from db import get_connection
from review_engine import _resolve_voice_entities_background


def main():
    conn = get_connection(readonly=True)

    # Find voice_capture + voice_capture_entity transcripts with entities but no resolutions
    rows = conn.execute("""
        SELECT vt.id, vt.source, vt.transcript, vt.llm_result,
               vt.node_id, vt.domain_id
        FROM voice_transcripts vt
        WHERE vt.source IN ('voice_capture', 'voice_capture_entity')
        ORDER BY vt.created_at
    """).fetchall()
    conn.close()

    candidates = []
    for r in rows:
        if not r['llm_result']:
            continue
        try:
            lr = json.loads(r['llm_result'])
        except (json.JSONDecodeError, TypeError):
            continue
        entities = lr.get('entities_mentioned', [])
        if not entities:
            print(f"  SKIP {r['id']}: no entities_mentioned")
            continue

        # Check if already resolved
        conn2 = get_connection(readonly=True)
        existing = conn2.execute(
            "SELECT COUNT(*) as cnt FROM entity_resolutions WHERE capture_id = ?",
            (f"voice:{r['id']}",)
        ).fetchone()['cnt']
        conn2.close()

        if existing > 0:
            print(f"  SKIP {r['id']}: already has {existing} resolutions")
            continue

        # Build node_domain_map from llm_result's node_assessments
        node_domain_map = {}
        for na in lr.get('node_assessments', []):
            nid = na.get('node_id')
            did = na.get('domain_id') or r['domain_id']
            if nid and did:
                node_domain_map[nid] = did

        candidates.append({
            'id': r['id'],
            'source': r['source'],
            'transcript': r['transcript'],
            'entities': entities,
            'node_domain_map': node_domain_map,
        })

    print(f"\n=== {len(candidates)} transcripts to process ===\n")
    if not candidates:
        print("Nothing to do.")
        return

    for i, c in enumerate(candidates):
        print(f"[{i+1}/{len(candidates)}] Processing {c['id']} "
              f"({c['source']}, {len(c['entities'])} entities)...")
        t0 = time.time()
        try:
            _resolve_voice_entities_background(
                entities_mentioned=c['entities'],
                transcript=c['transcript'],
                capture_id=c['id'],
                node_domain_map=c['node_domain_map'],
            )
        except Exception as e:
            print(f"  ERROR: {e}")
        elapsed = time.time() - t0
        print(f"  Done in {elapsed:.1f}s")

        if i < len(candidates) - 1:
            print(f"  Waiting 15s before next transcript...")
            time.sleep(15)

    # Summary
    conn3 = get_connection(readonly=True)
    total_resolutions = conn3.execute("SELECT COUNT(*) as cnt FROM entity_resolutions").fetchone()['cnt']
    voice_resolutions = conn3.execute(
        "SELECT COUNT(*) as cnt FROM entity_resolutions WHERE capture_id LIKE 'voice:%'"
    ).fetchone()['cnt']
    resolved_qids = conn3.execute(
        "SELECT COUNT(*) as cnt FROM entity_resolutions WHERE capture_id LIKE 'voice:%' AND status = 'resolved'"
    ).fetchone()['cnt']
    conn3.close()

    print(f"\n=== Summary ===")
    print(f"  Total entity_resolutions: {total_resolutions}")
    print(f"  Voice-capture resolutions: {voice_resolutions}")
    print(f"  Resolved (with QID): {resolved_qids}")


if __name__ == '__main__':
    main()
