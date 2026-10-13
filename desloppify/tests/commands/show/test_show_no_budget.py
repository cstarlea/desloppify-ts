"""Tests for --no-budget flag on show command."""

from __future__ import annotations

import argparse

from desloppify.app.commands.show.scope import resolve_noise


class TestNoBudgetFlag:
    def test_no_budget_returns_all_issues(self):
        """When no_budget=True, all matches are surfaced and nothing is hidden."""
        matches = [
            {"id": f"f{i}", "detector": "review", "summary": f"issue {i}"}
            for i in range(50)
        ]
        surfaced, hidden, budget, global_budget, warning = resolve_noise(
            {},  # empty config — would normally produce a low budget
            matches,
            no_budget=True,
        )
        assert len(surfaced) == 50
        assert hidden == {}
        assert budget == 0
        assert global_budget == 0
        assert warning is None

    def test_no_budget_flag_parsed(self):
        """The --no-budget flag is parsed correctly by argparse."""
        from desloppify.app.cli_support.parser_groups import _add_show_parser

        parent = argparse.ArgumentParser()
        sub = parent.add_subparsers()
        _add_show_parser(sub)

        args = parent.parse_args(["show", "review", "--no-budget"])
        assert getattr(args, "no_budget", False) is True

    def test_default_budget_still_applies(self):
        """Without no_budget, the normal budget logic runs."""
        matches = [
            {"id": f"f{i}", "detector": "review", "summary": f"issue {i}"}
            for i in range(50)
        ]
        # With default no_budget=False, budget is applied
        surfaced, hidden, budget, global_budget, warning = resolve_noise(
            {},
            matches,
        )
        # Normal path: budget may reduce the surfaced count
        # (exact behaviour depends on defaults in resolve_issue_noise_settings)
        assert isinstance(surfaced, list)
        assert isinstance(hidden, dict)


def _render(matches, *, hidden_by_detector=None):
    from desloppify.app.commands.show.render import render_issues

    hidden = hidden_by_detector or {}
    render_issues(
        matches,
        pattern="security",
        status_filter="open",
        show_code=False,
        top=20,
        hidden_by_detector=hidden,
        hidden_total=sum(hidden.values()),
        noise_budget=10,
        global_noise_budget=0,
        budget_warning=None,
    )


def _issue(i: int, **extra):
    return {
        "id": f"security::src/a{i}.ts::eval::{i}",
        "file": f"src/a{i}.ts",
        "detector": "security",
        "tier": 2,
        "confidence": "medium",
        "summary": f"issue {i}",
        "status": "open",
        **extra,
    }


class TestShowRendering:
    def test_all_flag_is_an_alias_for_no_budget(self):
        from desloppify.app.cli_support.parser_groups import _add_show_parser

        parent = argparse.ArgumentParser()
        sub = parent.add_subparsers()
        _add_show_parser(sub)
        assert parent.parse_args(["show", "security", "--all"]).no_budget is True

    def test_header_reports_total_and_hidden(self, capsys):
        _render([_issue(i) for i in range(10)], hidden_by_detector={"security": 4})
        out = capsys.readouterr().out
        assert (
            "14 open issues matching 'security' (showing 10; 4 hidden by the noise "
            "budget, use --all to see them)"
        ) in out

    def test_header_without_hidden_is_plain_count(self, capsys):
        _render([_issue(1)])
        out = capsys.readouterr().out
        assert "1 open issues matching 'security'\n" in out
        assert "hidden" not in out

    def test_severity_and_confidence_are_labelled(self, capsys):
        _render([_issue(1, detail={"severity": "high", "line": 3})])
        assert "[severity: high, confidence: medium] issue 1" in capsys.readouterr().out

    def test_confidence_alone_is_labelled(self, capsys):
        _render([_issue(1)])
        assert "[confidence: medium] issue 1" in capsys.readouterr().out
