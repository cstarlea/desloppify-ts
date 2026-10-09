"""State suppression accounting and score-integrity regression tests."""

from __future__ import annotations

import pytest

from desloppify.state import (
    MergeScanOptions,
    empty_state,
    load_state,
    save_state,
    suppression_metrics,
)
from desloppify.state import merge_scan as _merge_scan


def merge_scan(state, current_issues, *args, **kwargs):
    options = kwargs.pop("options", None)
    if args:
        if len(args) != 1:
            raise TypeError(
                "merge_scan test helper accepts at most one positional option"
            )
        options = args[0]
    if options is None:
        options = MergeScanOptions(**kwargs)
    return _merge_scan(state, current_issues, options=options)


def _make_raw_issue(
    fid,
    *,
    detector="det",
    file="a.py",
    tier=3,
    confidence="medium",
    summary="s",
    status="open",
    lang=None,
    zone=None,
):
    """Build a minimal issue dict with explicit ID (bypasses rel())."""
    now = "2025-01-01T00:00:00+00:00"
    f = {
        "id": fid,
        "detector": detector,
        "file": file,
        "tier": tier,
        "confidence": confidence,
        "summary": summary,
        "detail": {},
        "status": status,
        "note": None,
        "first_seen": now,
        "last_seen": now,
        "resolved_at": None,
        "reopen_count": 0,
    }
    if lang:
        f["lang"] = lang
    if zone:
        f["zone"] = zone
    return f


class TestSuppressionAccounting:
    def test_merge_scan_records_ignored_metrics_in_history_and_diff(self):
        st = empty_state()
        issues = [
            _make_raw_issue("smells::a.py::x", detector="smells", file="a.py"),
            _make_raw_issue("smells::b.py::y", detector="smells", file="b.py"),
            _make_raw_issue("logs::c.py::z", detector="logs", file="c.py"),
        ]

        diff = merge_scan(
            st,
            issues,
            MergeScanOptions(lang="python", ignore=["smells::*"], force_resolve=True),
        )

        assert diff["ignored"] == 2
        assert diff["raw_issues"] == 3
        assert diff["suppressed_pct"] == pytest.approx(66.7, abs=0.1)

        hist = st["scan_history"][-1]
        assert hist["ignored"] == 2
        assert hist["raw_issues"] == 3
        assert hist["suppressed_pct"] == pytest.approx(66.7, abs=0.1)
        assert hist["ignore_patterns"] == 1

    def test_suppression_metrics_aggregates_recent_history(self):
        st = empty_state()
        merge_scan(
            st,
            [
                _make_raw_issue("smells::a.py::x", detector="smells", file="a.py"),
                _make_raw_issue("logs::b.py::x", detector="logs", file="b.py"),
            ],
            MergeScanOptions(lang="python", ignore=["smells::*"], force_resolve=True),
        )
        merge_scan(
            st,
            [
                _make_raw_issue("smells::a.py::x", detector="smells", file="a.py"),
                _make_raw_issue("logs::b.py::x", detector="logs", file="b.py"),
                _make_raw_issue("logs::c.py::x", detector="logs", file="c.py"),
            ],
            MergeScanOptions(lang="python", ignore=["smells::*"], force_resolve=True),
        )

        sup = suppression_metrics(st, window=5)
        assert sup["last_ignored"] == 1
        assert sup["last_raw_issues"] == 3
        assert sup["recent_scans"] == 2
        assert sup["recent_ignored"] == 2
        assert sup["recent_raw_issues"] == 5
        assert sup["recent_suppressed_pct"] == 40.0


class TestScoreAntiGaming:
    def test_scan_history_records_subjective_integrity_snapshot(self):
        st = empty_state()
        st["subjective_assessments"] = {
            "naming_quality": {"score": 95},
            "logic_clarity": {"score": 95},
        }
        merge_scan(
            st,
            [],
            MergeScanOptions(
                lang="python",
                potentials={"unused": 0},
                force_resolve=True,
                subjective_integrity_target=95.0,
            ),
        )

        hist = st["scan_history"][-1]
        assert hist["subjective_integrity"]["status"] == "disabled"
        assert hist["subjective_integrity"]["matched_count"] == 0
        assert hist["subjective_integrity"]["reset_count"] == 0

    def test_save_state_preserves_subjective_integrity_target(self, tmp_path):
        st = empty_state()
        st["subjective_assessments"] = {
            "naming_quality": {"score": 95},
            "logic_clarity": {"score": 95},
        }
        merge_scan(
            st,
            [],
            MergeScanOptions(
                lang="python",
                potentials={"unused": 0},
                force_resolve=True,
                subjective_integrity_target=95.0,
            ),
        )

        save_path = tmp_path / "state.json"
        save_state(st, save_path)
        reloaded = load_state(save_path)

        assert reloaded["subjective_integrity"]["status"] == "disabled"
        assert reloaded["subjective_integrity"]["target_score"] == 95.0
        # Scores are preserved — no penalty applied
        assert reloaded["dimension_scores"]["Naming quality"]["score"] == 95.0
        assert reloaded["dimension_scores"]["Logic clarity"]["score"] == 95.0

    def test_manual_fixed_does_not_improve_verified_until_scan_confirms(self):
        from desloppify.state import resolve_issues

        st = empty_state()
        issue = _make_raw_issue("unused::a.py::x", detector="unused", file="a.py")
        merge_scan(
            st,
            [issue],
            MergeScanOptions(
                lang="python", potentials={"unused": 1}, force_resolve=True
            ),
        )
        before_strict = st["strict_score"]
        before_verified = st["verified_strict_score"]

        resolve_issues(
            st,
            "unused::a.py::x",
            "fixed",
            note="removed symbol",
            attestation="I have actually fixed this and I am not gaming the score.",
        )
        # strict_score should improve (fixed is not a failure in strict mode)
        assert st["strict_score"] > before_strict
        # verified_strict_score should NOT improve (fixed still counts as failing)
        assert st["verified_strict_score"] == before_verified

        # Scan confirms absence — the issue keeps its "fixed" status and gains
        # scan-verified metadata, which is what verified_strict waits for.
        merge_scan(
            st,
            [],
            MergeScanOptions(
                lang="python", potentials={"unused": 1}, force_resolve=True
            ),
        )
        assert st["issues"]["unused::a.py::x"]["status"] == "fixed"
        assert st["verified_strict_score"] > before_verified
        attestation = st["issues"]["unused::a.py::x"].get("resolution_attestation", {})
        assert attestation.get("scan_verified") is True

    def test_ignore_pattern_suppresses_and_excludes_from_scoring(self):
        from desloppify.state import remove_ignored_issues

        st = empty_state()
        issue = _make_raw_issue("unused::a.py::x", detector="unused", file="a.py")
        merge_scan(
            st,
            [issue],
            MergeScanOptions(
                lang="python", potentials={"unused": 1}, force_resolve=True
            ),
        )
        strict_before = st["strict_score"]

        removed = remove_ignored_issues(st, "unused::*")
        assert removed == 1
        assert st["issues"]["unused::a.py::x"]["suppressed"] is True
        # Suppressed issues are invisible to scoring — score should improve
        assert st["strict_score"] >= strict_before

    def test_resolve_preserves_subjective_integrity_target(self):
        """resolve_issues() must not erase the subjective integrity target.

        Regression test for bounty S249 (@Tib-Gridello, verified by @xliry):
        _recompute_stats was called without subjective_integrity_target,
        silently resetting the anti-gaming protection to disabled.
        """
        from desloppify.state import resolve_issues

        st = empty_state()
        st["subjective_assessments"] = {
            "naming_quality": {"score": 95},
        }
        issue = _make_raw_issue("unused::a.py::x", detector="unused", file="a.py")
        merge_scan(
            st,
            [issue],
            MergeScanOptions(
                lang="python",
                potentials={"unused": 1},
                force_resolve=True,
                subjective_integrity_target=95.0,
            ),
        )
        assert st["subjective_integrity"]["target_score"] == 95.0

        resolve_issues(st, "unused::a.py::x", "fixed", note="done")
        assert st["subjective_integrity"]["target_score"] == 95.0
        assert st["subjective_integrity"]["status"] == "disabled"

    def test_remove_ignored_preserves_subjective_integrity_target(self):
        """remove_ignored_issues() must not erase the subjective integrity target.

        Regression test for bounty S249 (@Tib-Gridello, verified by @xliry):
        _recompute_stats was called without subjective_integrity_target,
        silently resetting the anti-gaming protection to disabled.
        """
        from desloppify.state import remove_ignored_issues

        st = empty_state()
        st["subjective_assessments"] = {
            "naming_quality": {"score": 95},
        }
        issue = _make_raw_issue("unused::a.py::x", detector="unused", file="a.py")
        merge_scan(
            st,
            [issue],
            MergeScanOptions(
                lang="python",
                potentials={"unused": 1},
                force_resolve=True,
                subjective_integrity_target=95.0,
            ),
        )
        assert st["subjective_integrity"]["target_score"] == 95.0

        remove_ignored_issues(st, "unused::*")
        assert st["subjective_integrity"]["target_score"] == 95.0
        assert st["subjective_integrity"]["status"] == "disabled"


class TestWontfixDebtTotals:
    """Debt totals follow strict: a scan-confirmed-gone wontfix is not debt."""

    def _wontfix_then_scan(self, *, still_present: int):
        from desloppify.state import resolve_issues

        st = empty_state()
        issues = [
            _make_raw_issue(
                f"orphaned::src/m{i}.ts", detector="orphaned", file=f"src/m{i}.ts"
            )
            for i in range(3)
        ]
        opts = MergeScanOptions(
            lang="typescript", potentials={"orphaned": 3}, force_resolve=True
        )
        merge_scan(st, issues, opts)
        resolve_issues(
            st,
            "orphaned",
            "wontfix",
            note="plugin entry points",
            attestation="I have actually reviewed this and I am not gaming the score.",
        )
        merge_scan(st, issues[:still_present], opts)
        return st

    def test_stats_exclude_scan_verified_wontfix(self):
        st = self._wontfix_then_scan(still_present=1)
        assert st["stats"]["wontfix"] == 3
        assert st["stats"]["wontfix_debt"] == 1
        assert st["stats"]["wontfix_debt_by_tier"] == {"3": 1}

    def test_debt_displays_match_strict(self):
        from desloppify.app.commands.status.render_structural import build_area_rows
        from desloppify.intelligence.narrative.dimensions import _analyze_debt

        st = self._wontfix_then_scan(still_present=0)
        assert st["stats"]["wontfix"] == 3
        assert st["stats"]["wontfix_debt"] == 0
        assert st["strict_score"] == st["overall_score"]

        debt = _analyze_debt(st["dimension_scores"], st["issues"], [])
        assert debt["wontfix_count"] == 0

        areas = [("src", list(st["issues"].values()))]
        assert build_area_rows(areas)[0][4] == "0"
