#!/usr/bin/env python3
"""Measure E3 hypothesis: does collateral exposure improve subsequent recall?

Collateral exposure = when a position is visible on an aspect card but not the
one being actively tested. These anchor positions get 30% FSRS stability credit
(review_engine.py record_structural_answer).

Analysis approach:
1. Identify positions that have been actively graded (review_count > 0)
2. For each, check if they had collateral exposure BEFORE their first active grade
   (reconstructed from: other positions on the same card were graded earlier)
3. Compare first-grade knew% for exposed vs unexposed positions

Run on server: cd /opt/petrarca && python3 scripts/measure_collateral_exposure.py
"""
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DB_PATH = Path('/opt/petrarca/data/petrarca.db')
if not DB_PATH.exists():
    DB_PATH = Path('petrarca.db')
    if not DB_PATH.exists():
        print('ERROR: petrarca.db not found')
        sys.exit(1)

conn = sqlite3.connect(str(DB_PATH), timeout=30)
conn.row_factory = sqlite3.Row
conn.execute('PRAGMA journal_mode=WAL')
conn.execute('PRAGMA busy_timeout=30000')


def main():
    print('=' * 60)
    print('E3 Collateral Exposure Measurement')
    print('=' * 60)
    print()

    # ── 1. Overview: structural positions state ──
    total_positions = conn.execute(
        'SELECT COUNT(*) FROM structural_positions').fetchone()[0]
    graded_positions = conn.execute(
        'SELECT COUNT(*) FROM structural_positions WHERE review_count > 0').fetchone()[0]
    collateral_only = conn.execute(
        'SELECT COUNT(*) FROM structural_positions '
        'WHERE review_count = 0 AND stability_days > 1.0').fetchone()[0]
    untouched = conn.execute(
        'SELECT COUNT(*) FROM structural_positions '
        'WHERE review_count = 0 AND stability_days <= 1.0').fetchone()[0]

    print(f'Structural positions:  {total_positions}')
    print(f'  Actively graded:     {graded_positions}')
    print(f'  Collateral-only:     {collateral_only}  (stability > 1.0, never actively graded)')
    print(f'  Untouched:           {untouched}  (default stability, never graded)')
    print()

    # ── 2. Interaction log events ──
    grade_events = conn.execute(
        "SELECT COUNT(*) FROM interaction_log WHERE event = 'structural_grade'").fetchone()[0]
    collateral_events = conn.execute(
        "SELECT COUNT(*) FROM interaction_log WHERE event = 'collateral_exposure'").fetchone()[0]

    print(f'Interaction log events:')
    print(f'  structural_grade:    {grade_events}')
    print(f'  collateral_exposure: {collateral_events}')
    print()

    # ── 3. Per-card breakdown ──
    print('Per-card position state:')
    print(f'  {"Card ID":<40} {"Type":<10} {"Graded":<8} {"Collateral":<12} {"Untouched":<10}')
    print(f'  {"-"*40} {"-"*10} {"-"*8} {"-"*12} {"-"*10}')

    cards_with_activity = conn.execute('''
        SELECT sc.id, sc.card_type, sc.title,
               COUNT(*) as total,
               SUM(CASE WHEN sp.review_count > 0 THEN 1 ELSE 0 END) as graded,
               SUM(CASE WHEN sp.review_count = 0 AND sp.stability_days > 1.0 THEN 1 ELSE 0 END) as collateral,
               SUM(CASE WHEN sp.review_count = 0 AND sp.stability_days <= 1.0 THEN 1 ELSE 0 END) as untouched
        FROM structural_cards sc
        JOIN structural_positions sp ON sp.card_id = sc.id
        GROUP BY sc.id
        HAVING graded > 0 OR collateral > 0
        ORDER BY graded DESC, collateral DESC
    ''').fetchall()

    for row in cards_with_activity:
        card_id = row['id'][:38]
        print(f'  {card_id:<40} {row["card_type"]:<10} {row["graded"]:<8} {row["collateral"]:<12} {row["untouched"]:<10}')
    if not cards_with_activity:
        print('  (no cards with any review activity)')
    print()

    # ── 4. Collateral exposure detail ──
    # Positions that received collateral exposure (never actively graded but stability boosted)
    collateral_positions = conn.execute('''
        SELECT sp.id, sp.card_id, sp.question_text, sp.stability_days, sp.due_at,
               sc.card_type, sc.title as card_title
        FROM structural_positions sp
        JOIN structural_cards sc ON sc.id = sp.card_id
        WHERE sp.review_count = 0 AND sp.stability_days > 1.0
        ORDER BY sp.stability_days DESC
    ''').fetchall()

    if collateral_positions:
        print(f'Collateral-exposed positions ({len(collateral_positions)}):')
        for p in collateral_positions[:15]:
            due_str = datetime.fromtimestamp(p['due_at'] / 1000).strftime('%Y-%m-%d') if p['due_at'] else 'n/a'
            print(f'  stab={p["stability_days"]:.1f}d  due={due_str}  {p["question_text"][:50]}')
        if len(collateral_positions) > 15:
            print(f'  ... and {len(collateral_positions) - 15} more')
        print()

    # ── 5. Actively graded positions with score detail ──
    graded_detail = conn.execute('''
        SELECT sp.id, sp.card_id, sp.question_text, sp.stability_days,
               sp.review_count, sp.last_score,
               sc.card_type, sc.title as card_title
        FROM structural_positions sp
        JOIN structural_cards sc ON sc.id = sp.card_id
        WHERE sp.review_count > 0
        ORDER BY sp.last_reviewed_at DESC
    ''').fetchall()

    if graded_detail:
        print(f'Actively graded positions ({len(graded_detail)}):')
        for p in graded_detail:
            print(f'  [{p["last_score"]:>6}] reviews={p["review_count"]}  stab={p["stability_days"]:.1f}d  {p["question_text"][:50]}')
        print()

    # ── 6. E3 comparison (if enough data) ──
    MIN_SAMPLE = 20

    # To properly measure E3, we'd need positions that:
    # (a) had collateral exposure BEFORE first active grade → "exposed" group
    # (b) were first graded WITHOUT prior collateral exposure → "control" group
    # Then compare first-grade knew% between groups.
    #
    # With current logging, we can approximate:
    # - A position on a card that was graded multiple times likely had collateral
    #   exposure (other positions on the same card were tested first)
    # - A position on a card graded for the first time had no prior collateral

    # For now: report sample sizes and feasibility
    print('E3 Hypothesis Assessment:')
    print(f'  Actively graded positions:  {graded_positions}')
    print(f'  Collateral-only positions:  {collateral_only}')
    print()

    if graded_positions < MIN_SAMPLE:
        print(f'  ⚠ INSUFFICIENT DATA: Need ≥{MIN_SAMPLE} actively graded positions')
        print(f'    for meaningful comparison. Currently have {graded_positions}.')
        print(f'    At current review pace, estimate ~{max(1, (MIN_SAMPLE - graded_positions) // 3)} more')
        print(f'    review sessions needed.')
        print()
        print(f'  Current collateral exposure is building up silently:')
        print(f'    {collateral_only} positions have passive stability boosts.')
        print(f'    When these are eventually tested, we can compare their')
        print(f'    first-grade knew% against positions that had no prior exposure.')
    else:
        # Enough data — attempt the comparison
        # Group 1: positions on cards with multiple grade events (likely had collateral exposure)
        # Group 2: positions on cards graded only once
        knew_count = sum(1 for p in graded_detail if p['last_score'] == 'knew')
        missed_count = sum(1 for p in graded_detail if p['last_score'] == 'missed')
        knew_pct = knew_count / len(graded_detail) * 100 if graded_detail else 0

        print(f'  Overall knew rate: {knew_count}/{len(graded_detail)} = {knew_pct:.0f}%')
        print(f'  (Full comparison requires reconstructing per-session position history)')
        print(f'  Consider adding position-level collateral logging for precise measurement.')

    print()
    print('=' * 60)
    print('Done.')


if __name__ == '__main__':
    main()
    conn.close()
