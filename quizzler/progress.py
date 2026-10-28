#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Adaptive loop: turn result codes into local progress records and new guide versions.

    progress.py ingest 'QUIZZLER-ONBOARD {...}' [--guide G]   # or '-' for stdin
    progress.py report [--guide G]     # what the next version should target
    progress.py bump [--guide G]       # archive, bump data-version, retire mastered questions

Answers are classified as misconception (wrong + sure), gap (wrong, not sure),
fragile (right, not sure) or mastered (right + sure). Mastered questions are
retired in later versions; the others tell the author what to re-explain.
Records stay under quizzler/local/ and never enter version control.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import sys
from pathlib import Path

import guidelib as gl

LEVELS = ("misconception", "gap", "fragile", "mastered")


def progress_dir_for(guide_path: Path, guide_id: str, local_dir: Path = gl.LOCAL_DIR) -> Path:
    guide_path = guide_path.resolve()
    if guide_path.is_relative_to(local_dir.resolve()):
        return guide_path.parent
    safe = guide_id if gl.GUIDE_ID_RE.match(guide_id or "") else "guide"
    return local_dir / "progress" / safe


def default_guide() -> Path:
    local = gl.LOCAL_DIR / "guide.html"
    return local if local.is_file() else gl.ONBOARD_DIR / "guide.html"


def _load(path: Path) -> dict:
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def record_results(directory: Path, payload: dict, guide: gl.Guide | None) -> dict:
    """Grade a results payload and merge it into ``progress.json``/``progress.md``."""
    graded = gl.grade_answers(payload, guide)
    version = int(payload.get("version") or (guide.version if guide else 1))
    exercises = {}
    for name, rec in (payload.get("exercises") or {}).items():
        if isinstance(rec, dict):
            exercises[name] = {"ran": bool(rec.get("ran")), "matched": rec.get("matched"),
                               "prediction": str(rec.get("prediction", ""))[:2000]}
    findings = {}
    for fid, rec in (payload.get("findings") or {}).items():
        if isinstance(rec, dict):
            findings[fid] = {k: rec.get(k) for k in ("module", "response", "confidence", "reasoning", "state")}
    entry = {
        "version": version, "kind": payload.get("kind") or (guide.mode if guide else ""),
        "recorded_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "answers": graded, "exercises": exercises, "findings": findings,
        "comments": len(payload.get("comments") or []),
        "plan": {str(k): v for k, v in (payload.get("plan") or {}).items() if v in gl.INTENSITIES},
        "refresh": {str(k): v for k, v in (payload.get("refresh") or {}).items() if v in gl.INTENSITIES},
        "round": int(payload.get("round") or (guide.round if guide else 1) or 1),
    }
    progress_path = directory / "progress.json"
    progress = _load(progress_path)
    progress.setdefault("guide", payload.get("guide") or (guide.guide_id if guide else ""))
    history = [h for h in progress.get("history", []) if h.get("version") != version]
    history.append(entry)
    history.sort(key=lambda h: h["version"])
    progress["history"] = history
    retired = dict(progress.get("retired", {}))
    for qid, rec in graded.items():
        if rec["classification"] == "mastered":
            retired.setdefault(qid, f"v{version}:mastered")
    progress["retired"] = retired
    gl.write_json(progress_path, progress)
    (directory / "progress.md").write_text(render_markdown(progress, guide), encoding="utf-8")
    return {"progress": progress, "summary": summarize(entry)}


def summarize(entry: dict) -> dict:
    counts = {level: 0 for level in LEVELS}
    by_level = {level: [] for level in LEVELS}
    for qid, rec in entry["answers"].items():
        counts[rec["classification"]] += 1
        by_level[rec["classification"]].append(qid)
    missed = [n for n, r in entry["exercises"].items() if r["ran"] and not r["matched"]]
    responses: dict[str, int] = {}
    for rec in entry["findings"].values():
        key = rec.get("response") or "no-response"
        responses[key] = responses.get(key, 0) + 1
    return {"version": entry["version"], "counts": counts, "questions": by_level,
            "exercise_predictions_missed": missed, "finding_responses": responses,
            "reviewer_comments": entry["comments"]}


def render_markdown(progress: dict, guide: gl.Guide | None) -> str:
    lines = [f"# Progress: {progress.get('guide', '')}", "",
             "Local learner record. Git ignores this directory; do not copy it into tracked files.", ""]
    for entry in progress.get("history", []):
        summary = summarize(entry)
        counts = ", ".join(f"{k} {v}" for k, v in summary["counts"].items())
        lines += [f"## Version {entry['version']} ({entry['recorded_at']})", "", f"Answers: {counts}.", ""]
        if entry.get("plan"):
            lines += ["Study plan: " + ", ".join(f"{m} {i}" for m, i in entry["plan"].items()) + ".", ""]
        if entry["answers"]:
            lines += ["| Question | Module | Result | Confidence | Class |", "|---|---|---|---|---|"]
            for qid, rec in sorted(entry["answers"].items()):
                result = "right" if rec["correct"] else f"chose {rec['choice']}, answer {rec['answer']}"
                lines.append(f"| {qid} | {rec.get('module') or ''} | {result} | {rec['confidence']} | {rec['classification']} |")
            lines.append("")
        if entry["exercises"]:
            lines.append("Exercises: " + ", ".join(
                f"{n} ({'matched' if r['matched'] else 'missed' if r['ran'] else 'not run'})"
                for n, r in sorted(entry["exercises"].items())) + ".")
            lines.append("")
        if entry["findings"]:
            lines.append("Finding responses: " + ", ".join(
                f"{f} {r.get('response') or 'none'}/{r.get('state') or ''}" for f, r in sorted(entry["findings"].items())) + ".")
            lines.append("")
    if progress.get("retired"):
        lines += ["## Retired questions", ""]
        lines += [f"- {qid}: {why}" for qid, why in sorted(progress["retired"].items())]
        lines.append("")
    return "\n".join(lines)


def pending_work(guide: gl.Guide, plan: dict) -> list[tuple[str, str, str]]:
    """(module, written, wanted) for modules whose chosen intensity is deeper
    than what has been written, in study-plan order. Skipped modules never
    appear: they stay skeletons."""
    order = [row.module for row in guide.plan] or [m for m in guide.modules if m != guide.overview]
    recommended = {row.module: row.intensity for row in guide.plan}
    todo = []
    for module in order:
        wanted = plan.get(module) or recommended.get(module) or "standard"
        written = guide.written.get(module, "deep")
        if wanted != "skip" and gl.WRITTEN_RANK.get(written, 3) < gl.WRITTEN_RANK.get(wanted, 2):
            todo.append((module, written, wanted))
    return todo


def report(directory: Path, guide: gl.Guide | None) -> str:
    progress = _load(directory / "progress.json")
    if not progress.get("history"):
        if guide is not None and guide.plan:
            todo = pending_work(guide, {})
            lines = ["No results recorded yet. With the recommended plan, write next:"]
            lines += [f"  - {m}: {w} -> {t}" for m, w, t in todo] or ["  (nothing pending)"]
            return "\n".join(lines)
        return "No results recorded yet. Ingest a result code first."
    entry = progress["history"][-1]
    out = [f"Targets for the next version of {progress.get('guide')} (from v{entry['version']}):", ""]
    titles = guide.module_titles if guide else {}
    plan = entry.get("plan") or {}
    if plan:
        recommended = {row.module: row.intensity for row in guide.plan} if guide else {}
        out.append("Reader's study plan (write each module at this depth; keep skipped modules short and unquizzed):")
        for module, intensity in plan.items():
            note = "" if recommended.get(module) in (None, intensity) else f" (recommended {recommended[module]})"
            out.append(f"  - {module}: {intensity}{note}")
        out.append("")
    if guide is not None and guide.plan:
        todo = pending_work(guide, plan)
        if todo:
            out.append("Write next, in this order (set data-written when a layer is done; skipped modules stay skeletons):")
            out += [f"  - {m}: written {w}, reader wants {t}" for m, w, t in todo]
        else:
            out.append("Every module the reader kept is written to the chosen depth.")
        out.append("")
    modules: dict[str, list] = {}
    for qid, rec in entry["answers"].items():
        modules.setdefault(rec.get("module") or "(unknown)", []).append((qid, rec))
    order = {"misconception": 0, "gap": 1, "fragile": 2, "mastered": 3}
    for module, items in modules.items():
        out.append(f"Module {module}: {titles.get(module, '')}".rstrip(": "))
        for qid, rec in sorted(items, key=lambda i: order[i[1]["classification"]]):
            level = rec["classification"]
            action = {
                "misconception": "confront the wrong model directly; explain why the chosen answer is tempting and wrong",
                "gap": "teach from scratch with a smaller step, a diagram or an exercise",
                "fragile": "reinforce with a varied question; keep it",
                "mastered": "retire",
            }[level]
            detail = "" if rec["correct"] else f" (chose {rec['choice']}, answer {rec['answer']})"
            out.append(f"  - {level.upper()} {qid}{detail}: {rec['prompt'][:120]}")
            if rec.get("reasoning"):
                out.append(f"      reasoning: {rec['reasoning'][:300]}")
            out.append(f"      next: {action}")
        out.append("")
    refresh = entry.get("refresh") or {}
    if refresh:
        out.append("Refresh plan for the next round (the reader's choice per module):")
        out += [f"  - {m}: {i}" for m, i in refresh.items()]
        out.append("")
    rounds = sorted({h.get("round", 1) for h in progress["history"]})
    if len(rounds) > 1 or rounds[0] > 1:
        out.append("Across rounds (latest classification per round; use it for the final review and summary):")
        by_q: dict[str, dict[int, str]] = {}
        for h in progress["history"]:
            for qid, rec in h["answers"].items():
                by_q.setdefault(f"{rec.get('module') or '?'}/{qid}", {})[h.get("round", 1)] = rec["classification"]
        for key, per_round in sorted(by_q.items()):
            trail = " -> ".join(f"r{r}:{c}" for r, c in sorted(per_round.items()))
            out.append(f"  - {key}: {trail}")
        out.append("")
    missed = [n for n, r in entry["exercises"].items() if r["ran"] and not r["matched"]]
    unrun = [n for n, r in entry["exercises"].items() if not r["ran"]]
    if missed:
        out.append("Exercise predictions that missed (walk through the output line by line): " + ", ".join(missed))
    if unrun:
        out.append("Exercises not run: " + ", ".join(unrun))
    disputed = {f: r for f, r in entry["findings"].items() if r.get("response") in ("disagree", "unsure")}
    if disputed:
        out.append("Findings the reviewer disputed or was unsure about (re-check the evidence before keeping them):")
        for fid, rec in disputed.items():
            out.append(f"  - {fid}: {rec['response']} ({rec.get('confidence') or 'no confidence'}) {rec.get('reasoning') or ''}".rstrip())
    if progress.get("retired"):
        out.append("Retire in the next version: " + ", ".join(sorted(progress["retired"])))
    return "\n".join(out)


def bump(guide_path: Path, directory: Path, local_dir: Path = gl.LOCAL_DIR, new_round: bool = False) -> dict:
    """Archive the current guide, increment data-version, mark retired questions.

    With ``new_round`` the guide also moves to the next round (data-round);
    the finished round is archived as ``<guide>.round<N>.html`` for reference.
    """
    text = gl.read_text(guide_path)
    guide = gl.parse_guide(text)
    progress = _load(directory / "progress.json")
    version = guide.version
    if guide_path.resolve().is_relative_to(local_dir.resolve()):
        archive = guide_path.with_name(f"{guide_path.stem}.v{version}.html")
    else:
        archive = local_dir / "versions" / f"{guide.guide_id}.v{version}.html"
    archive.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(guide_path, archive)
    round_archive = None
    if new_round:
        round_archive = archive.with_name(f"{guide_path.stem if archive.parent == guide_path.parent else guide.guide_id}.round{guide.round}.html")
        shutil.copyfile(guide_path, round_archive)

    main_tag = re.search(r"<main\b[^>]*\bdata-guide\b[^>]*>", text)
    if not main_tag:
        raise SystemExit("guide has no <main data-guide> element")
    tag = main_tag.group(0)
    if new_round:
        if re.search(r'\bdata-round="\d+"', tag):
            tag = re.sub(r'\bdata-round="\d+"', f'data-round="{guide.round + 1}"', tag)
        else:
            tag = tag[:-1] + f' data-round="{guide.round + 1}">'
    if re.search(r'\bdata-version="\d+"', tag):
        new_tag = re.sub(r'\bdata-version="\d+"', f'data-version="{version + 1}"', tag)
    else:
        new_tag = tag[:-1] + f' data-version="{version + 1}">'
    text = text[:main_tag.start()] + new_tag + text[main_tag.end():]

    retired_now = []
    for qid, why in sorted(progress.get("retired", {}).items()):
        pattern = re.compile(r'<[a-zA-Z][^>]*\bdata-q="' + re.escape(qid) + r'"[^>]*>')
        match = pattern.search(text)
        if not match or "data-retired" in match.group(0):
            continue
        start_tag = match.group(0)
        insert_at = len(start_tag) - (2 if start_tag.endswith("/>") else 1)
        start_tag = start_tag[:insert_at] + f' data-retired="{why}"' + start_tag[insert_at:]
        text = text[:match.start()] + start_tag + text[match.end():]
        retired_now.append(qid)
    guide_path.write_text(text, encoding="utf-8")
    result = {"archived": str(archive), "version": version + 1, "retired": retired_now}
    if new_round:
        result.update(round=guide.round + 1, round_archive=str(round_archive))
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--guide", type=Path, help="guide the results belong to (default: local/guide.html or guide.html)")
    sub = parser.add_subparsers(dest="command", required=True)
    i = sub.add_parser("ingest")
    i.add_argument("code", nargs="?", default=None,
                   help="result code, or - for stdin (default: the server's autosaved results file)")
    sub.add_parser("report")
    b = sub.add_parser("bump")
    b.add_argument("--new-round", action="store_true", help="also start the next round (data-round + 1)")
    args = parser.parse_args(argv)

    guide_path = (args.guide or default_guide()).resolve()
    guide = gl.load_guide(guide_path)
    local_dir = gl.local_dir_for(guide_path)
    directory = progress_dir_for(guide_path, guide.guide_id, local_dir)
    if args.command == "ingest":
        if args.code is None:
            saved = gl.results_path_for(guide_path, guide.guide_id, local_dir)
            if not saved.is_file():
                raise SystemExit(f"no autosaved results at {saved}; paste the exported result code instead")
            code = "QUIZZLER-ONBOARD " + saved.read_text(encoding="utf-8")
        else:
            code = sys.stdin.read() if args.code == "-" else args.code
        kind, payload = gl.parse_result_code(code)
        if kind != "onboard":
            raise SystemExit("this is a review result code; use review.py ingest N")
        if payload.get("guide") != guide.guide_id:
            raise SystemExit(f"result code is for guide {payload.get('guide')!r}, not {guide.guide_id!r}; pass --guide")
        directory.mkdir(parents=True, exist_ok=True)
        if args.code is not None:
            gl.write_json(gl.results_path_for(guide_path, guide.guide_id, local_dir), payload)
        record = record_results(directory, payload, guide)
        print(json.dumps(record["summary"], indent=2))
        print(f"Recorded in {directory / 'progress.md'}")
    elif args.command == "report":
        print(report(directory, guide))
    elif args.command == "bump":
        print(json.dumps(bump(guide_path, directory, local_dir, new_round=args.new_round), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
