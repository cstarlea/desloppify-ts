"""Tests for desloppify.languages.typescript.detectors.logs — tagged console.log detection."""

import importlib.util
from pathlib import Path

import pytest

from desloppify.languages.typescript.detectors.logs import TAG_EXTRACT_RE, detect_logs

needs_treesitter = pytest.mark.skipif(
    importlib.util.find_spec("tree_sitter_language_pack") is None,
    reason="dev guards are found in the syntax tree",
)


@pytest.fixture(autouse=True)
def _root(set_project_root):
    """Point PROJECT_ROOT at the tmp directory via RuntimeContext."""


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


def _detect(path: Path) -> tuple[list[dict], int]:
    result = detect_logs(path)
    return result.entries, result.population_size


# ── detect_logs ──────────────────────────────────────────────


class TestDetectLogs:
    def test_detects_tagged_console_log(self, tmp_path):
        """Finds console.log with [Tag] prefix."""

        _write(tmp_path, "debug.ts", "console.log('[Debug] something happened');\n")
        entries, total = _detect(tmp_path)
        assert len(entries) == 1
        assert entries[0]["tag"] == "Debug"
        assert total == 1

    @pytest.mark.parametrize("method", ["warn", "error", "info"])
    def test_intentional_levels_not_detected(self, tmp_path, method):
        """console.warn/error/info are deliberate output, not debug logs."""

        _write(tmp_path, "warn.ts", f"console.{method}('[Warning] something');\n")
        entries, _ = _detect(tmp_path)
        assert entries == []

    def test_detects_console_trace_tagged(self, tmp_path):
        _write(tmp_path, "trace.ts", "console.trace('[Perf] timing data');\n")
        entries, _ = _detect(tmp_path)
        assert len(entries) == 1

    def test_detects_console_debug_tagged(self, tmp_path):
        """Finds console.debug with [Tag] prefix."""

        _write(tmp_path, "dbg.ts", "console.debug('[Trace] step 1');\n")
        entries, _ = _detect(tmp_path)
        assert len(entries) == 1

    def test_no_tag_not_detected(self, tmp_path):
        """console.log without a [Tag] is not detected."""

        _write(tmp_path, "clean.ts", "console.log('normal message');\n")
        entries, _ = _detect(tmp_path)
        assert len(entries) == 0

    def test_multiple_tags_in_one_file(self, tmp_path):
        """Multiple tagged logs in the same file are all detected."""

        _write(
            tmp_path,
            "multi.ts",
            (
                "console.log('[Auth] login attempt');\n"
                "console.log('[Auth] login success');\n"
                "console.log('[Perf] render time');\n"
            ),
        )
        entries, _ = _detect(tmp_path)
        assert len(entries) == 3

    def test_deduplicates_same_line(self, tmp_path):
        """Same file+line is deduplicated (from pattern overlap)."""

        # A line that could match both patterns should still produce only 1 entry
        _write(tmp_path, "one.ts", "console.log('[Tag] message');\n")
        entries, _ = _detect(tmp_path)
        assert len(entries) == 1

    def test_returns_file_count(self, tmp_path):
        """detect_logs returns total file count."""

        _write(tmp_path, "a.ts", "const x = 1;\n")
        _write(tmp_path, "b.tsx", "const y = 2;\n")
        _, total = _detect(tmp_path)
        assert total == 2

    def test_empty_directory(self, tmp_path):
        """Empty directory returns no entries."""

        entries, total = _detect(tmp_path)
        assert entries == []
        assert total == 0

    def test_emoji_prefixed_tag(self, tmp_path):
        """Detects tags with emoji prefix like console.log('emoji [Tag] ...')."""

        # The pattern allows up to 4 characters before the [
        _write(tmp_path, "emoji.ts", "console.log('>> [Tag] message');\n")
        entries, _ = _detect(tmp_path)
        assert len(entries) == 1

    def test_tsx_files_included(self, tmp_path):
        """Both .ts and .tsx files are scanned."""

        _write(tmp_path, "comp.tsx", "console.log('[Render] component');\n")
        entries, total = _detect(tmp_path)
        assert len(entries) == 1
        assert total >= 1

    @pytest.mark.parametrize(
        "call",
        [
            r"console.log('\x1b[35m  Login URL for \x1b[1m%s\x1b[0m', address);",
            r"console.log(`\u001b[36m  ${url}\u001b[0m`);",
            r"console.log('\033[1;31mFAILED\033[0m');",
        ],
    )
    def test_ansi_colour_codes_are_not_tags(self, tmp_path, call):
        _write(tmp_path, "mailer.ts", call + "\n")
        entries, _ = _detect(tmp_path)
        assert entries == []

    def test_tag_after_ansi_code(self, tmp_path):
        _write(
            tmp_path, "a.ts", r"console.log(`\x1b[36m[Sync] ${n} rows\x1b[0m`);" + "\n"
        )
        entries, _ = _detect(tmp_path)
        assert [e["tag"] for e in entries] == ["Sync"]

    def test_template_tag_variable_resolves(self, tmp_path):
        _write(
            tmp_path,
            "a.ts",
            "const DEBUG_TAG = '[Cart]';\n"
            "console.log(`${DEBUG_TAG} added`);\n"
            "console.log(`${LOG_PREFIX} other`);\n",
        )
        entries, _ = _detect(tmp_path)
        assert [e["tag"] for e in entries] == ["Cart", "LOG_PREFIX"]

    def test_script_zone_is_skipped(self, tmp_path):
        _write(
            tmp_path, "scripts/build-icons.mjs", "console.log(`[icons] ${size} done`);\n"
        )
        _write(tmp_path, "src/app.ts", "console.log('[App] start');\n")
        entries, _ = _detect(tmp_path)
        assert [Path(e["file"]).name for e in entries] == ["app.ts"]

    @needs_treesitter
    @pytest.mark.parametrize(
        "source",
        [
            "if (process.env.NODE_ENV !== 'production') {\n  console.log('[Dev] hi');\n}\n",
            "if (isDev) console.log('[Dev] hi');\n",
            "import.meta.env.DEV && console.log('[Dev] hi');\n",
            "const f = () => {\n  if (isProduction) {\n    throw new Error('x');\n  }\n"
            "  console.log('[Dev] hi');\n};\n",
            "if (ready) {\n  go();\n} else if (__DEV__) {\n  console.debug('[Dev] hi');\n}\n",
        ],
    )
    def test_dev_guarded_logs_are_skipped(self, tmp_path, source):
        _write(tmp_path, "a.ts", source)
        entries, _ = _detect(tmp_path)
        assert entries == []

    def test_unrelated_guard_still_detected(self, tmp_path):
        _write(
            tmp_path,
            "a.ts",
            "if (ready) {\n  console.log('[App] ready');\n}\n"
            "function f() {\n  if (isDevice) return;\n  console.log('[App] after');\n}\n",
        )
        entries, _ = _detect(tmp_path)
        assert [e["line"] for e in entries] == [2, 6]


# ── TAG_EXTRACT_RE ───────────────────────────────────────────


class TestTagExtractRe:
    def test_extracts_tag(self):
        m = TAG_EXTRACT_RE.search("[MyTag] some text")
        assert m is not None
        assert m.group(1) == "MyTag"

    def test_extracts_first_tag(self):
        m = TAG_EXTRACT_RE.search("[First] and [Second]")
        assert m is not None
        assert m.group(1) == "First"

    def test_no_tag(self):
        m = TAG_EXTRACT_RE.search("no tags here")
        assert m is None
