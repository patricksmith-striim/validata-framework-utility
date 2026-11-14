"""The files an OP or UDF module's jar is built from, and the record of them kept beside the jar.

Kept byte-identical in both engine packages so either engine can be imported alone;
the root regression suite checks parity.

Both engines reuse a target/ jar only while nothing it was built from has changed. That is what
Maven reads to build it: every file under the module's src/main (java and resources), its
pom.xml, the parent poms it inherits from, and every source root its pom add-sources. A shared
library (OpenProcessorCommon, FieldCommon, SampleCommon) is compiled INTO the jar rather than
depended on, so an edit there changes the artifact exactly like an edit under src/.

src/test is deliberately EXCLUDED: tests are compiled but never packaged, so no edit there can
change the jar's bytes, and including it made a test-only edit trigger a full rebuild that could
not alter the artifact. Trade-off: the build's `-DskipTests` test COMPILE (which catches a
Java-16+ construct in a test before it breaks a Java-11 release build) no longer re-runs for a
test-only edit; it fires on the next rebuild from any main/pom change, and in CI.

Mtimes cannot see a DELETED file, so each build writes the names of its inputs beside the jar
(`<jar>.sources`), relative to the module dir. A jar with no record, or a recorded input that is
gone, is stale.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

_ADD_SOURCE_RE = re.compile(r"<source>\s*([^<]+?)\s*</source>")
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_PARENT_RE = re.compile(r"<parent>(.*?)</parent>", re.DOTALL)
_RELATIVE_PATH_RE = re.compile(r"<relativePath\s*/>|<relativePath>\s*([^<]*?)\s*</relativePath>")


def add_source_roots(module_dir: Path) -> list[Path]:
    """Extra source roots this module's pom pulls in via build-helper `add-source`.

    Read from the pom rather than hardcoded, so a new shared root is covered the moment
    a module add-sources it, and only for the modules that actually do.
    """
    try:
        text = (module_dir / "pom.xml").read_text()
    except OSError:
        return []
    roots = []
    for raw in _ADD_SOURCE_RE.findall(text):
        resolved = raw.replace("${project.basedir}", str(module_dir))
        if "${" in resolved:
            # An unresolved property would silently resolve to a non-existent dir and
            # scan nothing; skipping is equally wrong but at least not silent-by-design.
            continue
        path = Path(resolved)
        if path.is_dir():
            roots.append(path.resolve())
    return roots


def parent_poms(module_dir: Path) -> list[Path]:
    """The local poms this module's pom inherits from, nearest first, found as Maven finds them.

    A `<parent>` without `<relativePath>` means `../pom.xml`; a relativePath naming a directory
    means its pom.xml; an empty `<relativePath/>` means the parent comes from a repository, not
    the tree. An aggregator pom that only lists the module in `<modules>` is not a parent: a
    build in the module dir never reads it.
    """
    poms: list[Path] = []
    pom = module_dir / "pom.xml"
    while True:
        try:
            text = _COMMENT_RE.sub("", pom.read_text())
        except OSError:
            return poms
        block = _PARENT_RE.search(text)
        if block is None:
            return poms
        rel = _RELATIVE_PATH_RE.search(block.group(1))
        relative = "../pom.xml" if rel is None else (rel.group(1) or "")
        if not relative:
            return poms
        parent = pom.parent / relative
        if parent.is_dir():
            parent = parent / "pom.xml"
        if not parent.is_file():
            return poms
        parent = parent.resolve()
        if parent in poms or parent == pom.resolve():
            return poms
        poms.append(parent)
        pom = parent


def _files_under(directory: Path) -> list[Path]:
    return [p for p in directory.rglob("*") if p.is_file()] if directory.is_dir() else []


def inputs(module_dir: Path) -> list[Path]:
    """Every file the module's jar is built from (module docstring)."""
    srcs = _files_under(module_dir / "src" / "main")
    pom = module_dir / "pom.xml"
    if pom.exists():
        srcs.append(pom)
    srcs.extend(parent_poms(module_dir))
    for root in add_source_roots(module_dir):
        srcs.extend(_files_under(root))
    return srcs


def record_path(jar: Path) -> Path:
    return jar.with_name(jar.name + ".sources")


def write_record(jar: Path, module_dir: Path, srcs: list[Path]) -> None:
    names = sorted({os.path.relpath(p, module_dir) for p in srcs})
    record_path(jar).write_text("".join(n + "\n" for n in names))


def stale_trigger(jar: Path, module_dir: Path, srcs: list[Path]) -> Path | None:
    """The input that makes `jar` stale, or None when it is up to date.

    Returns the jar itself when it is missing, the record when there is none (what the jar was
    built from is unknown), a recorded input that is gone (a path that does not exist), or the
    newest input that is newer than the jar or missing from the record.
    """
    try:
        jar_mtime = jar.stat().st_mtime
    except FileNotFoundError:
        return jar
    record = record_path(jar)
    try:
        recorded = set(record.read_text().splitlines())
    except OSError:
        return record
    names = {p: os.path.relpath(p, module_dir) for p in srcs}
    removed = sorted(recorded - set(names.values()))
    if removed:
        return module_dir / removed[0]
    newer = [p for p in srcs if p.stat().st_mtime > jar_mtime or names[p] not in recorded]
    return max(newer, key=lambda p: p.stat().st_mtime) if newer else None


def is_removed(trigger: Path, module_dir: Path, srcs: list[Path]) -> bool:
    """Whether a trigger names a recorded input that is no longer an input. By exact name, not
    exists(): a case-only rename still "exists" on macOS."""
    return os.path.relpath(trigger, module_dir) not in {os.path.relpath(p, module_dir) for p in srcs}
