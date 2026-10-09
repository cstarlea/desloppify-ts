"""The security cache key covers the package.json files the checks read."""

from __future__ import annotations

import os
from pathlib import Path

from desloppify.languages._framework.base.shared_phases_review import (
    _file_fingerprint,
    _package_manifests,
)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_nearest_manifest_per_file(tmp_path: Path):
    root = _write(tmp_path / "package.json", "{}")
    app = _write(tmp_path / "apps/web/package.json", "{}")
    files = ["apps/web/src/a.ts", "apps/web/src/b.ts", "lib/c.ts"]
    for name in files:
        _write(tmp_path / name, "")
    assert _package_manifests(tmp_path, files) == sorted([str(app), str(root)])


def test_manifest_change_changes_the_key(tmp_path: Path):
    manifest = _write(tmp_path / "package.json", '{"name": "app"}')
    _write(tmp_path / "src/env.ts", "process.env.NEXT_PUBLIC_TOKEN;\n")
    files = ["src/env.ts"]

    def key() -> str:
        return _file_fingerprint(
            scan_root=tmp_path, files=[*files, *_package_manifests(tmp_path, files)]
        )

    before = key()
    manifest.write_text('{"dependencies": {"next": "15"}}')
    stat = manifest.stat()
    os.utime(manifest, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    assert key() != before
