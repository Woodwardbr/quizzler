#!/usr/bin/env python3
"""PR review workflow for Quizzler. Nothing here writes to GitHub.

    review.py prepare 42 [--diff-base merge-base|base-tip|<sha>]
    review.py prepare 7 --local-base main --local-head my-branch   # no GitHub needed
    review.py status 42          # is the reviewed head still current?
    review.py ingest 42 'QUIZZLER-REVIEW {...}'   # or '-' to read stdin
    review.py preview 42         # SHA-bound QUIZZLER-PREVIEW payload
    review.py list
    review.py cleanup 42 [--yes] [--keep-state]

Review state lives in docs/onboarding/local/reviews/pr-<N>/ (ignored) and the
code under review is checked out in .claude/worktrees/pr-<N>/ (ignored).
Posting comments is deliberately not implemented: the agent shows the preview
and only posts after a separate, explicit user instruction.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import guidelib as gl


@dataclass
class PRInfo:
    number: int
    title: str
    url: str
    author: str
    base_ref: str
    head_ref: str
    base_sha: str
    head_sha: str
    adapter: str


class GitHubAdapter:
    """Fetch PR metadata with the gh CLI and the commits with git."""

    name = "github"

    def fetch(self, repo: Path, number: int) -> PRInfo:
        import subprocess
        fields = "number,title,url,author,baseRefName,headRefName,baseRefOid,headRefOid"
        try:
            proc = subprocess.run(["gh", "pr", "view", str(number), "--json", fields],
                                  cwd=repo, capture_output=True, text=True)
        except OSError:
            raise SystemExit("the gh CLI is required for GitHub PRs (or use --local-base/--local-head)") from None
        if proc.returncode != 0:
            raise SystemExit(f"gh pr view {number} failed: {proc.stderr.strip()}")
        data = json.loads(proc.stdout)
        ns = f"refs/quizzler/pr-{number}"
        gl.git(repo, "fetch", "--no-tags", "--quiet", "origin",
               f"+refs/pull/{number}/head:{ns}/head", f"+refs/heads/{data['baseRefName']}:{ns}/base")
        for sha in (data["baseRefOid"], data["headRefOid"]):
            gl.require_sha(sha)
            if gl.git(repo, "cat-file", "-t", sha, check=False).strip() != "commit":
                raise SystemExit(f"commit {sha} is not available after fetching; the PR may have moved, retry")
        return PRInfo(number, data["title"], data["url"], (data.get("author") or {}).get("login", ""),
                      data["baseRefName"], data["headRefName"], data["baseRefOid"], data["headRefOid"], self.name)


class LocalAdapter:
    """Review a local branch against another ref; same guide UI, no GitHub."""

    name = "local"

    def __init__(self, base_ref: str, head_ref: str):
        self.base_ref, self.head_ref = base_ref, head_ref

    def fetch(self, repo: Path, number: int) -> PRInfo:
        def rev(ref):
            sha = gl.git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False).strip()
            if not sha:
                raise SystemExit(f"unknown ref: {ref}")
            return sha
        return PRInfo(number, f"{self.head_ref} against {self.base_ref}", "", "",
                      self.base_ref, self.head_ref, rev(self.base_ref), rev(self.head_ref), self.name)


def review_dir(local_dir: Path, number: int) -> Path:
    return local_dir / "reviews" / f"pr-{number}"


def worktree_path(repo: Path, number: int) -> Path:
    return repo / ".claude" / "worktrees" / f"pr-{number}"


def load_meta(local_dir: Path, number: int) -> dict:
    path = review_dir(local_dir, number) / "meta.json"
    if not path.is_file():
        raise SystemExit(f"no review prepared for PR {number}")
    return json.loads(path.read_text(encoding="utf-8"))


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _ensure_ignored(repo: Path, rel: str) -> None:
    """Make sure the worktree is ignored without touching tracked files."""
    if _check_ignore(repo, rel):
        return
    common = Path(gl.git(repo, "rev-parse", "--git-common-dir").strip())
    if not common.is_absolute():
        common = repo / common
    exclude = common / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude.read_text(encoding="utf-8") if exclude.is_file() else ""
    if "/.claude/worktrees/" not in existing.split("\n"):
        with open(exclude, "a", encoding="utf-8") as handle:
            handle.write(("" if existing.endswith("\n") or not existing else "\n") + "/.claude/worktrees/\n")


def _check_ignore(repo: Path, rel: str) -> bool:
    import subprocess
    return subprocess.run(["git", "-C", str(repo), "check-ignore", "-q", rel]).returncode == 0


def _registered_worktrees(repo: Path) -> set[Path]:
    out = gl.git(repo, "worktree", "list", "--porcelain")
    return {Path(line[9:]).resolve() for line in out.splitlines() if line.startswith("worktree ")}


def prepare(repo: Path, number: int, adapter, local_dir: Path, diff_base: str = "merge-base") -> dict:
    repo = repo.resolve()
    info = adapter.fetch(repo, number)
    merge_base = gl.git(repo, "merge-base", info.base_sha, info.head_sha, check=False).strip() or None
    bases = {"base-tip": info.base_sha}
    if merge_base:
        bases["merge-base"] = merge_base
    if diff_base not in bases:
        sha = gl.git(repo, "rev-parse", "--verify", "--quiet", f"{diff_base}^{{commit}}", check=False).strip()
        if not sha:
            raise SystemExit(f"--diff-base must be merge-base, base-tip, or a commit (got {diff_base!r})")
        bases["custom"] = sha
        diff_base = "custom"

    wt = worktree_path(repo, number)
    rel = wt.relative_to(repo).as_posix()
    _ensure_ignored(repo, rel)
    if wt.exists():
        if wt.resolve() not in _registered_worktrees(repo):
            raise SystemExit(f"{wt} exists but is not a git worktree; remove it or run cleanup")
        if gl.git(wt, "status", "--porcelain").strip():
            raise SystemExit(f"{wt} has local modifications; resolve them or run cleanup first")
        gl.git(wt, "checkout", "--quiet", "--detach", info.head_sha)
    else:
        wt.parent.mkdir(parents=True, exist_ok=True)
        gl.git(repo, "worktree", "add", "--quiet", "--detach", str(wt), info.head_sha)

    rdir = review_dir(local_dir, number)
    rdir.mkdir(parents=True, exist_ok=True)
    meta_path = rdir / "meta.json"
    previous = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else None
    history = list(previous.get("previous_heads", [])) if previous else []
    if previous and previous.get("head_sha") != info.head_sha:
        history.append({"head_sha": previous["head_sha"], "replaced_at": _now()})
        results = rdir / "results.json"
        if results.is_file():
            results.rename(rdir / f"results.{previous['head_sha'][:12]}.json")
    meta = {
        **asdict(info), "pr": number, "bases": bases, "diff_base": diff_base,
        "worktree": str(wt), "prepared_at": _now(), "previous_heads": history,
        "stale_since": None,
    }
    gl.write_json(meta_path, meta)
    changes = gl.diff_changes(wt, bases[diff_base], info.head_sha)
    meta["changes"] = changes
    return meta


def status(repo: Path, number: int, adapter, local_dir: Path) -> dict:
    meta = load_meta(local_dir, number)
    info = adapter.fetch(repo.resolve(), number)
    wt = Path(meta["worktree"])
    wt_head = gl.git(wt, "rev-parse", "HEAD", check=False).strip() if wt.exists() else None
    stale = info.head_sha != meta["head_sha"] or wt_head != meta["head_sha"]
    if stale and not meta.get("stale_since"):
        meta["stale_since"] = _now()
        gl.write_json(review_dir(local_dir, number) / "meta.json", meta)
    return {"pr": number, "reviewed_head": meta["head_sha"], "current_head": info.head_sha,
            "worktree_head": wt_head, "stale": stale}


def ingest(local_dir: Path, number: int, code: str) -> dict:
    kind, payload = gl.parse_result_code(code)
    if kind != "review":
        raise SystemExit("this is an onboarding result code; use progress.py ingest")
    meta = load_meta(local_dir, number)
    if str(payload.get("pr", number)) != str(number):
        raise SystemExit(f"result code is for PR {payload.get('pr')}, not {number}")
    if payload.get("head") and payload["head"] != meta["head_sha"]:
        raise SystemExit("result code was produced against a different head SHA; refresh the review")
    rdir = review_dir(local_dir, number)
    guide_path = rdir / "guide.html"
    guide = gl.load_guide(guide_path) if guide_path.is_file() else None
    gl.write_json(rdir / "results.json", payload)
    import progress
    record = progress.record_results(rdir, payload, guide)
    return record


def _line_range(item: dict) -> tuple[int, int]:
    if item.get("lines"):
        return gl.parse_line_range(item["lines"])
    start = int(item.get("start") or 0)
    end = int(item.get("end") or start)
    if start < 1 or end < start:
        raise ValueError("comment has no valid line range")
    return start, end


def preview(repo: Path, local_dir: Path, number: int) -> dict:
    """Build the SHA-bound preview of everything marked ready. Never posts."""
    meta = load_meta(local_dir, number)
    rdir = review_dir(local_dir, number)
    results_path = rdir / "results.json"
    if not results_path.is_file():
        raise SystemExit("no results yet: finish a module in the guide (it autosaves) or ingest a result code")
    results = json.loads(results_path.read_text(encoding="utf-8"))
    head = meta["head_sha"]
    wt = Path(meta["worktree"])
    problems: list[str] = []
    if gl.git(wt, "rev-parse", "HEAD", check=False).strip() != head:
        problems.append("worktree HEAD no longer matches the reviewed head SHA; refresh before posting")

    items = []
    for fid, rec in sorted((results.get("findings") or {}).items()):
        items.append(("finding", fid, rec, rec.get("comment", "")))
    for rec in results.get("comments") or []:
        items.append(("reviewer", rec.get("id", ""), rec, rec.get("body", "")))

    base = meta["bases"][meta["diff_base"]]
    hunks_by_path: dict[str, list] = {}
    comments, held, skipped = [], [], []
    for source, cid, rec, body in items:
        st = rec.get("state")
        if st == "needs-another-look":
            held.append(cid)
            continue
        if st != "ready":
            skipped.append(cid)
            continue
        entry = {"id": cid, "source": source, "path": rec.get("path", ""), "body": body.strip()}
        try:
            start, end = _line_range(rec)
        except ValueError as exc:
            problems.append(f"{cid}: {exc}")
            continue
        entry.update(start_line=start, end_line=end, side="RIGHT", commit_id=head)
        if not entry["body"]:
            problems.append(f"{cid}: comment text is empty")
        text = gl.show_file(wt, head, entry["path"]) if entry["path"] else None
        if text is None:
            problems.append(f"{cid}: {entry['path'] or '(no path)'} does not exist at {head[:12]}")
            continue
        lines = text.split("\n")
        if text.endswith("\n"):
            lines.pop()
        if end > len(lines):
            problems.append(f"{cid}: lines {start}-{end} are past the end of {entry['path']} ({len(lines)} lines)")
            continue
        entry["snippet"] = "\n".join(lines[start - 1:end])
        if entry["path"] not in hunks_by_path:
            hunks_by_path[entry["path"]] = gl.hunk_ranges(gl.diff_file(wt, base, head, entry["path"], context=3))
        entry["in_diff"] = any(a <= start and end <= b for a, b in hunks_by_path[entry["path"]])
        if not entry["in_diff"]:
            problems.append(f"{cid}: {entry['path']}:{start}-{end} is outside the diff hunks; "
                            "GitHub only accepts line comments inside the diff (post as a general comment instead)")
        comments.append(entry)

    payload = {
        "pr": number, "url": meta.get("url", ""), "head_sha": head, "base_sha": meta.get("base_sha"),
        "diff_base": {"name": meta["diff_base"], "sha": base}, "comments": comments,
        "needs_another_look": held, "not_posting": skipped, "problems": problems,
    }
    gl.write_json(rdir / "preview.json", payload)
    return payload


def format_preview(payload: dict) -> str:
    out = [f"Preview for PR #{payload['pr']} at head {payload['head_sha']}",
           f"Diff base: {payload['diff_base']['name']} {payload['diff_base']['sha']}", ""]
    if not payload["comments"]:
        out.append("No comments are marked ready.")
    for c in payload["comments"]:
        span = f"{c['start_line']}" if c["start_line"] == c["end_line"] else f"{c['start_line']}-{c['end_line']}"
        flag = "" if c.get("in_diff") else "  [outside diff]"
        out += [f"--- {c['path']}:{span} ({c['source']} {c['id']}){flag}", c["body"], ""]
    if payload["needs_another_look"]:
        out.append("Held for another look: " + ", ".join(payload["needs_another_look"]))
    if payload["not_posting"]:
        out.append("Not posting: " + ", ".join(payload["not_posting"]))
    for problem in payload["problems"]:
        out.append(f"PROBLEM: {problem}")
    out += ["", "Nothing has been posted. Posting requires a separate, explicit instruction.", "",
            f"{gl.PREVIEW_PREFIX} {json.dumps(payload, separators=(',', ':'))}"]
    return "\n".join(out)


def list_reviews(local_dir: Path) -> list[dict]:
    rows = []
    for meta_path in sorted((local_dir / "reviews").glob("pr-*/meta.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        rows.append({"pr": meta.get("pr"), "title": meta.get("title"), "head": meta.get("head_sha", "")[:12],
                     "worktree_exists": Path(meta.get("worktree", "")).exists(),
                     "has_guide": (meta_path.parent / "guide.html").is_file(),
                     "stale_since": meta.get("stale_since")})
    return rows


def cleanup(repo: Path, local_dir: Path, number: int, *, yes: bool, keep_state: bool) -> list[str]:
    repo = repo.resolve()
    wt = worktree_path(repo, number)
    rdir = review_dir(local_dir, number)
    actions = []
    if wt.exists() or wt.resolve() in _registered_worktrees(repo):
        actions.append(f"remove worktree {wt}")
    refs = gl.git(repo, "for-each-ref", "--format=%(refname)", f"refs/quizzler/pr-{number}/").split()
    actions += [f"delete ref {ref}" for ref in refs]
    if rdir.exists() and not keep_state:
        actions.append(f"delete review state {rdir}")
    if not yes:
        return [f"would {a}" for a in actions] + (["re-run with --yes to apply"] if actions else [])
    if wt.resolve() in _registered_worktrees(repo):
        gl.git(repo, "worktree", "remove", "--force", str(wt))
    elif wt.exists():
        shutil.rmtree(wt)
    gl.git(repo, "worktree", "prune")
    for ref in refs:
        gl.git(repo, "update-ref", "-d", ref)
    if rdir.exists() and not keep_state:
        shutil.rmtree(rdir)
    return actions


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", type=Path, default=None, help="repository root (default: this checkout)")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("number", type=int)
    p.add_argument("--diff-base", default="merge-base")
    p.add_argument("--local-base")
    p.add_argument("--local-head")
    s = sub.add_parser("status")
    s.add_argument("number", type=int)
    s.add_argument("--local-base")
    s.add_argument("--local-head")
    i = sub.add_parser("ingest")
    i.add_argument("number", type=int)
    i.add_argument("code", help="result code, or - for stdin")
    v = sub.add_parser("preview")
    v.add_argument("number", type=int)
    sub.add_parser("list")
    c = sub.add_parser("cleanup")
    c.add_argument("number", type=int)
    c.add_argument("--yes", action="store_true", help="actually delete (default: dry run)")
    c.add_argument("--keep-state", action="store_true", help="keep local/reviews/pr-N")
    args = parser.parse_args(argv)

    repo = (args.repo or gl.find_repo_root()).resolve()
    local_dir = gl.LOCAL_DIR

    def adapter_for(a):
        if a.local_base or a.local_head:
            if not (a.local_base and a.local_head):
                raise SystemExit("--local-base and --local-head go together")
            return LocalAdapter(a.local_base, a.local_head)
        return GitHubAdapter()

    if args.command == "prepare":
        meta = prepare(repo, args.number, adapter_for(args), local_dir, args.diff_base)
        print(f"Prepared PR {meta['pr']}: {meta['title']}")
        print(f"  head      {meta['head_sha']}")
        print(f"  diff base {meta['diff_base']} {meta['bases'][meta['diff_base']]}")
        print(f"  worktree  {meta['worktree']}")
        print(f"  state     {review_dir(local_dir, args.number)}")
        print(f"  {len(meta['changes'])} changed file(s):")
        for ch in meta["changes"]:
            stats = "binary" if ch["binary"] else f"+{ch['additions']} -{ch['deletions']}"
            name = f"{ch['old_path']} -> {ch['path']}" if ch["old_path"] else ch["path"]
            print(f"    {ch['status']} {name} ({stats})")
    elif args.command == "status":
        print(json.dumps(status(repo, args.number, adapter_for(args), local_dir), indent=2))
    elif args.command == "ingest":
        code = sys.stdin.read() if args.code == "-" else args.code
        record = ingest(local_dir, args.number, code)
        print(json.dumps(record["summary"], indent=2))
    elif args.command == "preview":
        payload = preview(repo, local_dir, args.number)
        print(format_preview(payload))
        return 1 if payload["problems"] else 0
    elif args.command == "list":
        print(json.dumps(list_reviews(local_dir), indent=2))
    elif args.command == "cleanup":
        for line in cleanup(repo, local_dir, args.number, yes=args.yes, keep_state=args.keep_state):
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
