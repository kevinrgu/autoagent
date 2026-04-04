"""Preflight policy gate — rule-based diff checker for mutation validation."""

import re
from dataclasses import dataclass

FIXED_FILES = {"adapter.py", "contracts.py", "__init__.py"}

FORBIDDEN_PATTERNS = [
    r'\bimport\s+importlib\b',
    r'\bimport\s+ctypes\b',
    r'\bsys\.modules\b',
]


@dataclass
class PreflightResult:
    rejected: bool
    reason: str


def check_diff(diff_text: str) -> PreflightResult:
    """Check a unified diff for policy violations.

    Args:
        diff_text: Unified diff string to validate.

    Returns:
        PreflightResult with rejected=True and a reason if any rule is violated.
    """
    for line in diff_text.splitlines():
        # Check if any fixed file is being modified (appears in diff --git header)
        if line.startswith("diff --git"):
            for fixed_file in FIXED_FILES:
                if f"/{fixed_file}" in line or f" {fixed_file}" in line:
                    return PreflightResult(
                        rejected=True,
                        reason=f"modification of fixed file detected: {fixed_file}",
                    )

        # Check forbidden patterns only in added lines
        if line.startswith("+") and not line.startswith("+++"):
            for pattern in FORBIDDEN_PATTERNS:
                if re.search(pattern, line):
                    return PreflightResult(
                        rejected=True,
                        reason=f"forbidden pattern found: {pattern}",
                    )

    return PreflightResult(rejected=False, reason="")
