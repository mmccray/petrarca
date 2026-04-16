#!/usr/bin/env python3
"""Optimize FSRS-6 parameters from actual review history.

Reads review events from interaction_log, reconstructs per-card review
histories, runs py-fsrs Optimizer, and reports old vs new parameters.

Usage:
    python3 scripts/optimize_fsrs.py          # dry-run (compare only)
    python3 scripts/optimize_fsrs.py --apply  # update review_engine.py
"""
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from db import get_connection

try:
    from fsrs import Optimizer, ReviewLog, Rating, Scheduler
except ImportError:
    print("ERROR: py-fsrs not installed. Run: pip install py-fsrs")
    sys.exit(1)

# ── Score mapping (matches review_engine.py) ──────────────────────────────

SCORE_TO_RATING = {
    'knew':   Rating.Easy,
    'partly': Rating.Good,
    'missed': Rating.Again,
}


def extract_review_logs(conn) -> list[ReviewLog]:
    """Extract review history from interaction_log into py-fsrs ReviewLog format."""
    # Collect all grading events with standard scores
    rows = conn.execute("""
        SELECT item_id, score, created_at, response_ms
        FROM interaction_log
        WHERE score IN ('knew', 'partly', 'missed')
          AND event IN ('review_answer', 'review_result', 'review_quiz_result')
        ORDER BY item_id, created_at
    """).fetchall()

    # Group by item_id, deduplicate events within 60s of each other
    by_item: dict[str, list] = defaultdict(list)
    for r in rows:
        item_id = r['item_id']
        dt = datetime.fromisoformat(r['created_at']).replace(tzinfo=timezone.utc)
        score = r['score']
        duration = r['response_ms']

        # Deduplicate: skip if within 60s of the last event for this item
        if by_item[item_id]:
            last_dt = by_item[item_id][-1][0]
            if abs((dt - last_dt).total_seconds()) < 60:
                continue

        by_item[item_id].append((dt, score, duration))

    # Assign integer card_ids and build ReviewLog objects
    review_logs: list[ReviewLog] = []
    card_id_map: dict[str, int] = {}
    next_id = 1

    items_with_multi = 0
    for item_id, events in by_item.items():
        if item_id not in card_id_map:
            card_id_map[item_id] = next_id
            next_id += 1

        cid = card_id_map[item_id]
        if len(events) > 1:
            items_with_multi += 1

        for dt, score, duration_ms in events:
            rating = SCORE_TO_RATING.get(score)
            if rating is None:
                continue
            review_logs.append(ReviewLog(
                card_id=cid,
                rating=rating,
                review_datetime=dt,
                review_duration=duration_ms,
            ))

    print(f"Extracted {len(review_logs)} review events across {len(card_id_map)} cards")
    print(f"Cards with 2+ reviews: {items_with_multi}")
    return review_logs


def compute_current_loss(review_logs: list[ReviewLog]) -> float:
    """Compute loss using current default parameters."""
    scheduler = Scheduler(
        desired_retention=0.80,
        learning_steps=(),
        relearning_steps=(),
        maximum_interval=3650,
    )
    # Use the optimizer's internal loss computation with default params
    optimizer = Optimizer(review_logs)
    default_params = list(scheduler.parameters)
    return optimizer._compute_batch_loss(parameters=default_params)


def main():
    apply = '--apply' in sys.argv
    conn = get_connection(readonly=True)

    try:
        review_logs = extract_review_logs(conn)
    finally:
        conn.close()

    if len(review_logs) < 20:
        print(f"\nOnly {len(review_logs)} review events — too few for optimization.")
        print("Need at least 50+ reviews with repeat reviews on the same cards.")
        return

    print("\n=== Running FSRS Optimizer ===")
    optimizer = Optimizer(review_logs)

    # Current default parameters
    default_scheduler = Scheduler()
    default_params = list(default_scheduler.parameters)
    print(f"Default parameters ({len(default_params)} values):")
    print(f"  {[round(p, 4) for p in default_params]}")

    # Compute loss with defaults
    default_loss = optimizer._compute_batch_loss(parameters=default_params)
    print(f"\nDefault parameter loss: {default_loss:.6f}")

    # Optimize
    print("\nOptimizing... (this may take a minute)")
    try:
        optimal_params = optimizer.compute_optimal_parameters(verbose=True)
    except Exception as e:
        print(f"Optimization failed: {e}")
        print("This typically means insufficient data — need more cards with 2+ reviews.")
        return

    # Compute loss with optimized parameters
    optimal_loss = optimizer._compute_batch_loss(parameters=optimal_params)
    print(f"\nOptimal parameters ({len(optimal_params)} values):")
    print(f"  {[round(p, 4) for p in optimal_params]}")
    print(f"\nOptimal parameter loss: {optimal_loss:.6f}")

    improvement = ((default_loss - optimal_loss) / default_loss) * 100 if default_loss > 0 else 0
    print(f"\nImprovement: {improvement:.1f}%")

    if improvement < 5:
        print("\nImprovement < 5% — default parameters are adequate for now.")
        print("Re-run after accumulating more review data (200+ reviews).")
        if not apply:
            return

    # Show parameter comparison
    print("\n=== Parameter Comparison ===")
    param_names = [
        'w0 (initial stability Again)', 'w1 (initial stability Hard)',
        'w2 (initial stability Good)', 'w3 (initial stability Easy)',
        'w4 (difficulty init)', 'w5 (difficulty delta)',
        'w6 (stability power)', 'w7 (review penalty)',
        'w8 (failure stability)', 'w9 (failure difficulty)',
        'w10 (stability growth)', 'w11 (difficulty recovery)',
        'w12 (short-term stability)', 'w13 (long-term stability)',
        'w14 (difficulty mean reversion)', 'w15 (difficulty ceiling)',
        'w16 (forgetting curve)', 'w17 (forgetting floor)',
        'w18', 'w19', 'w20',
    ]
    for i, (name, d, o) in enumerate(zip(param_names, default_params, optimal_params)):
        marker = ' ***' if abs(o - d) / max(abs(d), 0.001) > 0.2 else ''
        print(f"  {name:40s}  default={d:8.4f}  optimal={o:8.4f}{marker}")

    if apply:
        # Write the parameters tuple to review_engine.py
        params_tuple = tuple(round(p, 6) for p in optimal_params)
        engine_path = Path(__file__).parent / 'review_engine.py'
        content = engine_path.read_text()

        # Find and replace the Scheduler initialization
        import re
        pattern = r'_fsrs_scheduler = _FsrsScheduler\([^)]+\)'
        replacement = f"""_fsrs_scheduler = _FsrsScheduler(
    desired_retention=0.80,
    learning_steps=(),
    relearning_steps=(),
    enable_fuzzing=True,
    maximum_interval=3650,
    parameters={params_tuple},
)"""
        new_content, count = re.subn(pattern, replacement, content, flags=re.DOTALL)
        if count == 0:
            print("\nERROR: Could not find _fsrs_scheduler definition in review_engine.py")
            return

        engine_path.write_text(new_content)
        print(f"\n=== Applied optimized parameters to review_engine.py ===")
        print(f"Improvement: {improvement:.1f}% (loss {default_loss:.6f} -> {optimal_loss:.6f})")
    else:
        print(f"\nDry run. To apply: python3 {__file__} --apply")


if __name__ == '__main__':
    main()
