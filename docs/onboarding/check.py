#!/usr/bin/env python3
"""Validate guides and exercises: the drift gate run in CI.

    python docs/onboarding/check.py                    # tracked guides + shared exercises
    python docs/onboarding/check.py --update-expected  # re-record expected/ outputs
    python docs/onboarding/check.py --review 42        # a generated review guide

Checks the guide contract, every ``data-src``/``data-ref`` literal anchor
(present and unambiguous), every ``data-sym`` symbol, finding line ranges,
exercise registration, and that each exercise still prints its recorded
``expected/<name>.out``. Exits non-zero on any error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import guidelib as gl


class Report:
    def __init__(self):
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, where: str, message: str):
        self.errors.append(f"ERROR {where}: {message}")

    def warn(self, where: str, message: str):
        self.warnings.append(f"WARN  {where}: {message}")


def _display(path: Path) -> str:
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return str(path)


def check_guide(guide_path: Path, repo_root: Path, report: Report, *,
                onboard_dir: Path = gl.ONBOARD_DIR) -> gl.Guide | None:
    name = _display(guide_path)
    try:
        guide = gl.load_guide(guide_path)
    except (OSError, UnicodeDecodeError) as exc:
        report.error(name, f"cannot read guide: {exc}")
        return None

    if not guide.has_main:
        report.error(name, "missing <main data-guide> element")
        return guide
    if guide.mode not in gl.GUIDE_MODES:
        report.error(name, f"data-mode must be one of {gl.GUIDE_MODES}")
    if not gl.GUIDE_ID_RE.match(guide.guide_id):
        report.error(name, "data-guide-id is missing or contains unsafe characters")
    if guide.version < 1:
        report.error(name, "data-version must be a positive integer")
    if guide.mode == "review":
        for key in ("pr", "head-sha"):
            if not guide.meta.get(key):
                report.error(name, f"review guides need data-{key}")
    for line in guide.inline_scripts:
        report.error(f"{name}:{line}", "inline <script> is blocked by the server CSP; use data attributes")

    # Assets must resolve to the shared engine relative to the guide.
    expected_assets = {(onboard_dir / "app" / f).resolve() for f in ("guide.css", "guide.js")}
    seen_assets = set()
    for href in guide.assets:
        target = (guide_path.parent / href).resolve()
        if target in expected_assets:
            seen_assets.add(target)
        else:
            report.error(name, f"asset {href!r} does not point at docs/onboarding/app/")
    for missing in sorted(expected_assets - seen_assets):
        report.error(name, f"guide does not load app/{missing.name}")

    for label, ids in (("module", guide.modules), ("question", [q.id for q in guide.questions]),
                       ("finding", [f.id for f in guide.findings])):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            report.error(name, f"duplicate {label} ids: {', '.join(dupes)}")
    if not guide.modules:
        report.error(name, "guide has no <section class=\"module\" data-module=...>")

    for q in guide.questions:
        where = f"{name}:{q.line}"
        if q.module is None:
            report.error(where, f"question {q.id} is outside any module")
        if len(q.choices) < 2:
            report.error(where, f"question {q.id} needs at least two data-choice options")
        elif q.answer not in q.choices:
            report.error(where, f"question {q.id} data-answer={q.answer!r} is not one of its choices")
        if not q.prompt:
            report.error(where, f"question {q.id} has no .prompt text")

    policy = gl.PathPolicy(repo_root)
    texts: dict[str, str | None] = {}

    def source(rel: str, where: str) -> str | None:
        if rel not in texts:
            try:
                texts[rel] = gl.read_text(policy.check(rel))
            except gl.PolicyError as exc:
                report.error(where, str(exc))
                texts[rel] = None
        return texts[rel]

    for ref in guide.refs:
        where = f"{name}:{ref.line}"
        text = source(ref.path, where)
        if text is None:
            continue
        if ref.ref is not None:
            try:
                gl.resolve_anchor(text, ref.ref, ref.occurrence)
            except gl.AnchorError as exc:
                report.error(where, f"{ref.path}: {exc}")
        if ref.sym:
            if gl.lookup_symbol(gl.find_symbols(ref.path, text), ref.sym) is None:
                report.error(where, f"{ref.path}: symbol {ref.sym!r} not found")
        if ref.lines:
            try:
                start, end = gl.parse_line_range(ref.lines)
            except ValueError as exc:
                report.error(where, str(exc))
                continue
            total = len(text.split("\n")) - (1 if text.endswith("\n") else 0)
            if end > total:
                report.error(where, f"{ref.path}: lines {ref.lines} past end of file ({total} lines)")

    for finding in guide.findings:
        where = f"{name}:{finding.line}"
        if finding.module is None:
            report.error(where, f"finding {finding.id} is outside any module")
        if not finding.path or not finding.lines:
            report.error(where, f"finding {finding.id} needs data-src and data-lines")
        if not finding.draft:
            report.warn(where, f"finding {finding.id} has no .comment-draft")

    dirs = gl.guide_exercise_dirs(guide_path, onboard_dir)
    for listing in guide.listings:
        where = f"{name}:{listing.line}"
        if listing.path:
            text = source(listing.path, where)
            if text is None:
                continue
            lines = text.split("\n")
            if listing.lines:
                try:
                    start, end = gl.parse_line_range(listing.lines)
                except ValueError as exc:
                    report.error(where, str(exc))
                    continue
                lines = lines[start - 1:end]
            label = listing.path
        elif listing.exercise:
            path = gl.resolve_exercise(listing.exercise, dirs)
            if path is None:
                continue  # reported below as a missing exercise
            lines = gl.read_text(path).split("\n")
            label = f"exercise {listing.exercise}"
        else:
            report.warn(where, "code listing has no data-src; it is not checked against the source and can drift")
            continue
        problem = gl.listing_drift(listing.code, lines)
        if problem:
            report.error(where, f"{label}: {problem}")

    for exercise in guide.exercises:
        if not gl.EXERCISE_NAME_RE.match(exercise):
            report.error(name, f"exercise name {exercise!r} is not [a-z0-9_]")
        elif gl.resolve_exercise(exercise, dirs) is None:
            report.error(name, f"exercise {exercise!r} not found in {', '.join(_display(d) for d in dirs)}")
    return guide


def check_exercises(exercise_dir: Path, expected_dir: Path, report: Report, *, update: bool = False,
                    timeout: float = 30.0) -> int:
    count = 0
    for path in sorted(exercise_dir.glob("*.py")):
        count += 1
        name = path.stem
        where = _display(path)
        if not gl.EXERCISE_NAME_RE.match(name):
            report.error(where, "exercise file names must be [a-z0-9_]")
            continue
        if not gl.exercise_predict(path):
            report.error(where, "missing a module docstring with a PREDICT: section")
        result = gl.run_exercise(path, timeout=timeout)
        if result["timed_out"] or result["exit_code"] != 0:
            detail = "timed out" if result["timed_out"] else f"exit code {result['exit_code']}"
            report.error(where, f"exercise failed ({detail}): {result['stderr'].strip()[-500:]}")
            continue
        actual = gl.normalize_output(result["stdout"])
        expected_path = expected_dir / f"{name}.out"
        if update:
            expected_path.parent.mkdir(parents=True, exist_ok=True)
            expected_path.write_text(actual, encoding="utf-8")
            continue
        if not expected_path.is_file():
            report.error(where, f"no recorded output at {_display(expected_path)} (run with --update-expected)")
            continue
        expected = gl.normalize_output(expected_path.read_text(encoding="utf-8"))
        if expected != actual:
            report.error(where, "output drifted from " + _display(expected_path) + "\n"
                         + _unified(expected, actual, f"expected/{name}.out", f"actual {name}"))
    for orphan in sorted(expected_dir.glob("*.out")):
        if not (exercise_dir / f"{orphan.stem}.py").is_file():
            report.warn(_display(orphan), "expected output has no matching exercise")
    return count


def _unified(a: str, b: str, left: str, right: str) -> str:
    import difflib
    return "".join(difflib.unified_diff(a.splitlines(True), b.splitlines(True), left, right))


def default_guides(onboard_dir: Path = gl.ONBOARD_DIR) -> list[Path]:
    return sorted(onboard_dir.glob("*.html")) + sorted((onboard_dir / "examples").glob("*.html"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--guide", type=Path, action="append", help="guide to check (repeatable)")
    parser.add_argument("--repo-root", type=Path, help="repository the anchors refer to")
    parser.add_argument("--review", type=int, metavar="N", help="check local/reviews/pr-N against its worktree")
    parser.add_argument("--no-exercises", action="store_true", help="skip running shared exercises")
    parser.add_argument("--update-expected", action="store_true", help="re-record expected/ outputs")
    parser.add_argument("--json", action="store_true", help="print a machine-readable report")
    args = parser.parse_args(argv)

    report = Report()
    guides = args.guide or []
    repo_root = args.repo_root or gl.find_repo_root()
    run_shared = not args.no_exercises
    if args.review is not None:
        review_dir = gl.LOCAL_DIR / "reviews" / f"pr-{args.review}"
        meta = json.loads((review_dir / "meta.json").read_text(encoding="utf-8"))
        repo_root = Path(meta["worktree"])
        guides = [review_dir / "guide.html"]
        run_shared = False
    elif not guides:
        guides = default_guides()

    checked = [check_guide(g, repo_root, report) for g in guides]
    exercises = 0
    if run_shared or args.update_expected:
        exercises = check_exercises(gl.SHARED_EXERCISES, gl.EXPECTED_DIR, report, update=args.update_expected)

    if args.json:
        print(json.dumps({"errors": report.errors, "warnings": report.warnings}, indent=2))
    else:
        for line in report.warnings + report.errors:
            print(line)
        refs = sum(len(g.refs) for g in checked if g)
        status = "FAILED" if report.errors else "OK"
        print(f"{status}: {len(guides)} guide(s), {refs} source reference(s), {exercises} exercise(s), "
              f"{len(report.errors)} error(s), {len(report.warnings)} warning(s)")
    return 1 if report.errors else 0


if __name__ == "__main__":
    sys.exit(main())
