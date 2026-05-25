"""Health checks: self-test, conflict detection, accuracy tracking."""

from pmb.health.self_test import SelfTestRunner, SelfTestResult
from pmb.health.conflicts import ConflictDetector, FactConflict
from pmb.health.adaptive import apply_adaptive_boost, adaptive_history

__all__ = [
    "SelfTestRunner",
    "SelfTestResult",
    "ConflictDetector",
    "FactConflict",
    "apply_adaptive_boost",
    "adaptive_history",
]
