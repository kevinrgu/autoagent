"""Tests for promotion gates and explore→exploit migration rules."""
from promotion import (
    MigrationConfig,
    MigrationPlan,
    PromotionGates,
    PromotionResult,
    check_promotion,
    compute_migration,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_index(entries: list[dict]) -> list[dict]:
    """Build a minimal archive index from shorthand dicts."""
    defaults = {
        "pool": "explore",
        "scores": {"smoke": 1.0, "spreadsheet": 0.80, "terminal": 0.40},
        "tree_hash": "abc",
        "generation": 0,
        "stale_generations": 0,
        "cross_domain_delta": 0.0,
    }
    return [{**defaults, **e} for e in entries]


# ---------------------------------------------------------------------------
# test_smoke_gate_pass
# ---------------------------------------------------------------------------

def test_smoke_gate_pass():
    """All gates met → promoted=True."""
    scores = {"smoke": 1.0, "spreadsheet": 0.85, "terminal": 0.45}
    best = {"smoke": 1.0, "spreadsheet": 0.85, "terminal": 0.45}
    gates = PromotionGates.defaults()

    result = check_promotion(scores, gates, best_scores=best)

    assert isinstance(result, PromotionResult)
    assert result.promoted is True


# ---------------------------------------------------------------------------
# test_smoke_gate_fail
# ---------------------------------------------------------------------------

def test_smoke_gate_fail():
    """smoke < 1.0 → promoted=False."""
    scores = {"smoke": 0.9, "spreadsheet": 0.85, "terminal": 0.45}
    best = {"smoke": 1.0, "spreadsheet": 0.85, "terminal": 0.45}
    gates = PromotionGates.defaults()

    result = check_promotion(scores, gates, best_scores=best)

    assert result.promoted is False
    assert "smoke" in result.reason.lower()


# ---------------------------------------------------------------------------
# test_regression_gate_fail
# ---------------------------------------------------------------------------

def test_regression_gate_fail():
    """spreadsheet regresses > 5% from best → promoted=False."""
    best = {"smoke": 1.0, "spreadsheet": 0.80, "terminal": 0.40}
    # 0.80 * (1 - 0.05) = 0.76; go below that
    scores = {"smoke": 1.0, "spreadsheet": 0.70, "terminal": 0.40}
    gates = PromotionGates.defaults()

    result = check_promotion(scores, gates, best_scores=best)

    assert result.promoted is False
    assert "spreadsheet" in result.reason.lower()


# ---------------------------------------------------------------------------
# test_migration_at_interval
# ---------------------------------------------------------------------------

def test_migration_at_interval():
    """At generation 3 (== interval), top-k explore entries get promoted to exploit."""
    config = MigrationConfig(
        interval_generations=3,
        explore_to_exploit_top_k=2,
        exploit_to_explore_bottom_k=5,
        cross_domain_fast_track_threshold=0.10,
    )
    index = _make_index([
        {"version": "xp1", "pool": "explore", "scores": {"smoke": 1.0, "spreadsheet": 0.90, "terminal": 0.50}},
        {"version": "xp2", "pool": "explore", "scores": {"smoke": 1.0, "spreadsheet": 0.70, "terminal": 0.30}},
        {"version": "xp3", "pool": "explore", "scores": {"smoke": 1.0, "spreadsheet": 0.80, "terminal": 0.40}},
        {"version": "ex1", "pool": "exploit", "scores": {"smoke": 1.0, "spreadsheet": 0.95, "terminal": 0.60}},
    ])

    plan = compute_migration(index, generation=3, config=config)

    assert isinstance(plan, MigrationPlan)
    # Top-2 explore by score should be promoted
    assert len(plan.promote_to_exploit) == 2
    assert "xp1" in plan.promote_to_exploit  # highest spreadsheet


# ---------------------------------------------------------------------------
# test_migration_not_at_interval
# ---------------------------------------------------------------------------

def test_migration_not_at_interval():
    """At generation 2 (not an interval), no migration happens."""
    config = MigrationConfig(interval_generations=3)
    index = _make_index([
        {"version": "xp1", "pool": "explore"},
        {"version": "ex1", "pool": "exploit"},
    ])

    plan = compute_migration(index, generation=2, config=config)

    assert plan.promote_to_exploit == []
    assert plan.demote_to_explore == []


# ---------------------------------------------------------------------------
# test_cross_domain_fast_track
# ---------------------------------------------------------------------------

def test_cross_domain_fast_track():
    """Entry with cross_domain_delta > 10% gets immediate shadow priority (fast-tracked)."""
    config = MigrationConfig(
        interval_generations=3,
        cross_domain_fast_track_threshold=0.10,
    )
    # Generation 1 — not an interval, but cross_domain_delta > threshold
    index = _make_index([
        {"version": "xp_novel", "pool": "explore", "cross_domain_delta": 0.15},
        {"version": "xp_norm", "pool": "explore", "cross_domain_delta": 0.02},
        {"version": "ex1", "pool": "exploit"},
    ])

    plan = compute_migration(index, generation=1, config=config)

    assert "xp_novel" in plan.promote_to_exploit
    assert "xp_norm" not in plan.promote_to_exploit
