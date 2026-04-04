"""Per-suite promotion gates and exploit/explore migration rules."""
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Gate definitions
# ---------------------------------------------------------------------------

@dataclass
class SuiteGate:
    """Promotion threshold for a single evaluation suite.

    Args:
        min_absolute: Minimum absolute score required to pass.
        max_regression_pct: Maximum allowed regression from best score (0.0 = 0%).
    """

    min_absolute: float
    max_regression_pct: float


@dataclass
class PromotionGates:
    """Collection of per-suite gates used to decide promotion.

    Args:
        gates: Mapping of suite name → SuiteGate.
    """

    gates: dict[str, SuiteGate]

    @classmethod
    def defaults(cls) -> "PromotionGates":
        """Return default gates: smoke(1.0, 0%), spreadsheet(0.80, 5%), terminal(0.40, 5%)."""
        return cls(
            gates={
                "smoke": SuiteGate(min_absolute=1.0, max_regression_pct=0.0),
                "spreadsheet": SuiteGate(min_absolute=0.80, max_regression_pct=0.05),
                "terminal": SuiteGate(min_absolute=0.40, max_regression_pct=0.05),
            }
        )


# ---------------------------------------------------------------------------
# Promotion result
# ---------------------------------------------------------------------------

@dataclass
class PromotionResult:
    """Result of a promotion gate check.

    Args:
        promoted: True if all gates passed.
        reason: Human-readable explanation (empty string when promoted=True).
    """

    promoted: bool
    reason: str = ""


def check_promotion(
    scores: dict[str, float],
    gates: PromotionGates,
    best_scores: dict[str, float],
) -> PromotionResult:
    """Check all promotion gates and return result.

    A candidate passes only if every defined suite gate is satisfied:
    - score >= gate.min_absolute
    - score >= best_score * (1 - gate.max_regression_pct)

    Args:
        scores: Current candidate scores per suite.
        gates: PromotionGates defining thresholds.
        best_scores: Historical best scores per suite for regression check.

    Returns:
        PromotionResult with promoted=True if all gates pass.
    """
    for suite, gate in gates.gates.items():
        current = scores.get(suite, 0.0)
        best = best_scores.get(suite, 0.0)

        if current < gate.min_absolute:
            return PromotionResult(
                promoted=False,
                reason=f"{suite} score {current:.3f} below minimum {gate.min_absolute:.3f}",
            )

        regression_floor = best * (1.0 - gate.max_regression_pct)
        if current < regression_floor:
            return PromotionResult(
                promoted=False,
                reason=(
                    f"{suite} score {current:.3f} regresses more than "
                    f"{gate.max_regression_pct*100:.0f}% from best {best:.3f}"
                ),
            )

    return PromotionResult(promoted=True)


# ---------------------------------------------------------------------------
# Migration config & plan
# ---------------------------------------------------------------------------

@dataclass
class MigrationConfig:
    """Configuration for periodic pool migration.

    Args:
        interval_generations: How many generations between migration runs.
        explore_to_exploit_top_k: Top-k explore entries (by score) promoted each interval.
        exploit_to_explore_bottom_k: Bottom-k exploit entries demoted each interval.
        cross_domain_fast_track_threshold: cross_domain_delta threshold for immediate promotion.
    """

    interval_generations: int = 3
    explore_to_exploit_top_k: int = 3
    exploit_to_explore_bottom_k: int = 5
    cross_domain_fast_track_threshold: float = 0.10


@dataclass
class MigrationPlan:
    """Versions to move between pools.

    Args:
        promote_to_exploit: Explore versions to move into exploit pool.
        demote_to_explore: Exploit versions to move into explore pool.
    """

    promote_to_exploit: list[str] = field(default_factory=list)
    demote_to_explore: list[str] = field(default_factory=list)


def _mean_score(scores: dict[str, float]) -> float:
    """Compute mean over all suite scores."""
    if not scores:
        return 0.0
    return sum(scores.values()) / len(scores)


def compute_migration(
    archive_index: list[dict],
    generation: int,
    config: MigrationConfig,
) -> MigrationPlan:
    """Determine which versions should move between exploit and explore pools.

    Rules:
    - Cross-domain fast track: any explore entry with cross_domain_delta above
      threshold is promoted immediately regardless of generation.
    - Interval migration: at multiples of interval_generations, top-k explore
      entries (by mean score) are promoted and bottom-k exploit entries are demoted.

    Args:
        archive_index: List of entry dicts from index.jsonl.
        generation: Current generation number.
        config: MigrationConfig thresholds.

    Returns:
        MigrationPlan listing versions to promote and demote.
    """
    plan = MigrationPlan()

    explore_entries = [e for e in archive_index if e.get("pool") == "explore"]
    exploit_entries = [e for e in archive_index if e.get("pool") == "exploit"]

    # Cross-domain fast track (always active, independent of interval)
    fast_tracked = set()
    for entry in explore_entries:
        delta = entry.get("cross_domain_delta", 0.0)
        if delta > config.cross_domain_fast_track_threshold:
            plan.promote_to_exploit.append(entry["version"])
            fast_tracked.add(entry["version"])

    # Interval-based migration
    if generation > 0 and generation % config.interval_generations == 0:
        # Promote top-k explore by mean score (skip already fast-tracked)
        eligible_explore = [e for e in explore_entries if e["version"] not in fast_tracked]
        eligible_explore.sort(key=lambda e: _mean_score(e.get("scores", {})), reverse=True)
        for entry in eligible_explore[: config.explore_to_exploit_top_k]:
            plan.promote_to_exploit.append(entry["version"])

        # Demote bottom-k exploit by mean score
        exploit_entries.sort(key=lambda e: _mean_score(e.get("scores", {})))
        for entry in exploit_entries[: config.exploit_to_explore_bottom_k]:
            plan.demote_to_explore.append(entry["version"])

    return plan
