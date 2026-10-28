# SPDX-License-Identifier: Apache-2.0
"""Shared helpers for the Quizzler guide engine.

Everything here is standard-library only so the server, the CI checks, and the
review tooling can run from a clean checkout without installing anything.
"""

from __future__ import annotations

import ast
import fnmatch
import html.parser
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

ONBOARD_DIR = Path(__file__).resolve().parent
APP_DIR = ONBOARD_DIR / "app"
LOCAL_DIR = ONBOARD_DIR / "local"
SHARED_EXERCISES = ONBOARD_DIR / "exercises"
EXPECTED_DIR = ONBOARD_DIR / "expected"
APP_FILES = frozenset({"guide.js", "guide.css", "layout_check.js"})

GUIDE_MODES = ("onboard", "review")
CONFIDENCE_LEVELS = ("sure", "unsure", "guess")
FINDING_RESPONSES = ("agree", "unsure", "disagree")
COMMENT_STATES = ("ready", "needs-another-look", "not-posting")
RATINGS = ("low", "medium", "high")
INTENSITIES = ("skip", "skim", "standard", "deep")
DEPTHS = ("standard", "deep")  # data-depth: shown at this intensity and above
# data-written on a module: how far it has been written. Missing means "deep"
# (fully written). A skeleton is an outline with no questions or exercises.
WRITTEN_LEVELS = ("skeleton", "skim", "standard", "deep")
WRITTEN_RANK = {level: rank for rank, level in enumerate(WRITTEN_LEVELS)}
RESULT_PREFIXES = {"QUIZZLER-ONBOARD": "onboard", "QUIZZLER-REVIEW": "review"}
PREVIEW_PREFIX = "QUIZZLER-PREVIEW"

SHA_RE = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")
EXERCISE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")
GUIDE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


def find_repo_root(start: Path = ONBOARD_DIR) -> Path:
    """Return the git top level containing ``start``, or two levels up."""
    try:
        out = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        return Path(out).resolve()
    except (OSError, subprocess.CalledProcessError):
        return Path(start).resolve().parents[1]


# --------------------------------------------------------------------------
# Path policy
# --------------------------------------------------------------------------

SOURCE_EXTENSIONS = frozenset("""
    .py .pyi .js .mjs .cjs .jsx .ts .tsx .vue .svelte .css .scss .less .html .htm
    .md .rst .txt .adoc .json .toml .yaml .yml .ini .cfg .conf .xml .csv .sql
    .sh .bash .zsh .ps1 .go .rs .java .kt .kts .scala .swift .c .h .cc .cpp .hpp
    .cs .rb .php .lua .r .jl .ex .exs .erl .hs .ml .proto .graphql .gql .tf .hcl
    .gradle .cmake .mk .dockerfile .svg .lock
""".split())
SOURCE_FILENAMES = frozenset({
    "Makefile", "Dockerfile", "Containerfile", "Justfile", "Procfile", "Gemfile",
    "Rakefile", "LICENSE", "LICENCE", "README", "CODEOWNERS", "CHANGELOG",
})
# Matched case-insensitively against every path component.
SECRET_PATTERNS = (
    "env.storage.*", "env.storage", "*.env", "*.pem", "*.key", "*.p12", "*.pfx",
    "*.jks", "*.keystore", "*.kdbx", "*.tfstate", "*.tfstate.*", "*.tfvars",
    "id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*", "*credential*", "*secret*",
    "*password*", "*passwd*", ".netrc", "_netrc", "*.ovpn", "htpasswd",
)
# Skipped when listing the outline; still readable if linked explicitly.
OUTLINE_SKIP_DIRS = frozenset({
    "node_modules", "__pycache__", "site-packages", "venv", "dist", "build",
    "target", "vendor", "coverage",
})
MAX_SOURCE_BYTES = 2_000_000


class PolicyError(Exception):
    """A path was refused. ``code`` is ``forbidden`` or ``not_found``."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def is_secret_name(name: str) -> bool:
    lowered = name.lower()
    return any(fnmatch.fnmatchcase(lowered, pattern) for pattern in SECRET_PATTERNS)


def is_source_name(name: str) -> bool:
    return name in SOURCE_FILENAMES or PurePosixPath(name).suffix.lower() in SOURCE_EXTENSIONS


class PathPolicy:
    """Decides which repository files the browser may read.

    Paths are repository-relative POSIX strings. A path is refused when it is
    absolute, contains ``..`` or a hidden component, names secret material
    (including ``env.storage.*``), lives under a denied prefix such as the
    guide's ``local/`` state, has a non-source extension, or resolves (through
    symlinks) outside the root or onto a refused path.
    """

    def __init__(self, root: Path, deny_prefixes: tuple[str, ...] = ()):
        self.root = Path(root).resolve()
        self.deny_prefixes = tuple(PurePosixPath(p) for p in deny_prefixes if p)

    def _check_parts(self, parts: tuple[str, ...], raw: str) -> None:
        if not parts:
            raise PolicyError("forbidden", "empty path")
        for part in parts:
            if part in ("", ".", ".."):
                raise PolicyError("forbidden", f"path traversal refused: {raw}")
            if part.startswith("."):
                raise PolicyError("forbidden", f"hidden path refused: {raw}")
            if is_secret_name(part):
                raise PolicyError("forbidden", f"secret-like path refused: {raw}")
        pure = PurePosixPath(*parts)
        for prefix in self.deny_prefixes:
            if pure == prefix or prefix in pure.parents:
                raise PolicyError("forbidden", f"local guide state refused: {raw}")
        if not is_source_name(parts[-1]):
            raise PolicyError("forbidden", f"file type not allowlisted: {raw}")

    def check_name(self, rel: str) -> str:
        """Validate a path by name only (it may not exist, e.g. a deleted file)."""
        if not isinstance(rel, str) or not rel or "\x00" in rel or "\\" in rel:
            raise PolicyError("forbidden", "invalid path")
        if rel.startswith("/") or re.match(r"^[A-Za-z]:", rel):
            raise PolicyError("forbidden", f"absolute path refused: {rel}")
        # Split by hand: PurePosixPath would silently drop "." components.
        parts = tuple(rel.split("/"))
        self._check_parts(parts, rel)
        return "/".join(parts)

    def check(self, rel: str) -> Path:
        """Validate a path and return the resolved file it points to."""
        clean = self.check_name(rel)
        candidate = self.root.joinpath(*clean.split("/"))
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            raise PolicyError("not_found", f"no such file: {rel}") from None
        if not resolved.is_relative_to(self.root):
            raise PolicyError("forbidden", f"path escapes the repository: {rel}")
        # A symlink may point somewhere inside the root that is itself refused.
        self._check_parts(resolved.relative_to(self.root).parts, rel)
        if not resolved.is_file():
            raise PolicyError("not_found", f"not a file: {rel}")
        if resolved.stat().st_size > MAX_SOURCE_BYTES:
            raise PolicyError("forbidden", f"file too large to display: {rel}")
        return resolved

    def allowed(self, rel: str) -> bool:
        try:
            self.check(rel)
        except PolicyError:
            return False
        return True

    def walk(self, limit: int = 5000) -> list[str]:
        """List allowed files for the source outline, skipping bulky directories."""
        found: list[str] = []
        for dirpath, dirnames, filenames in os.walk(self.root, followlinks=False):
            base = Path(dirpath).relative_to(self.root)
            keep = []
            for name in sorted(dirnames):
                rel = (base / name).as_posix()
                if name.startswith(".") or is_secret_name(name) or name in OUTLINE_SKIP_DIRS:
                    continue
                if any(PurePosixPath(rel) == p or p in PurePosixPath(rel).parents for p in self.deny_prefixes):
                    continue
                keep.append(name)
            dirnames[:] = keep
            for name in sorted(filenames):
                rel = (base / name).as_posix()
                if self.allowed(rel):
                    found.append(rel)
                    if len(found) >= limit:
                        return found
        return found


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


# --------------------------------------------------------------------------
# Literal anchors and symbols
# --------------------------------------------------------------------------

class AnchorError(Exception):
    pass


def literal_matches(text: str, ref: str) -> list[tuple[int, int]]:
    """Return 1-based (start_line, end_line) for every occurrence of ``ref``."""
    if not ref:
        return []
    matches = []
    start = text.find(ref)
    while start != -1:
        line = text.count("\n", 0, start) + 1
        matches.append((line, line + ref.count("\n")))
        start = text.find(ref, start + 1)
    return matches


def resolve_anchor(text: str, ref: str, occurrence: int | None = None) -> dict:
    """Resolve a literal anchor, insisting on uniqueness unless ``occurrence`` is given."""
    matches = literal_matches(text, ref)
    if not matches:
        raise AnchorError(f"literal not found: {ref!r}")
    if occurrence is None:
        if len(matches) > 1:
            lines = ", ".join(str(m[0]) for m in matches[:8])
            raise AnchorError(
                f"literal is ambiguous ({len(matches)} matches at lines {lines}); "
                f"make it longer or add data-occurrence: {ref!r}"
            )
        occurrence = 1
    if not 1 <= occurrence <= len(matches):
        raise AnchorError(f"data-occurrence={occurrence} but only {len(matches)} matches: {ref!r}")
    line, end = matches[occurrence - 1]
    return {"line": line, "end_line": end, "matches": len(matches)}


_GENERIC_SYMBOL_PATTERNS = [
    re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)", re.M),
    re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)", re.M),
    re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=", re.M),
    re.compile(r"^\s*(?:export\s+)?(?:interface|type|enum)\s+([A-Za-z_$][\w$]*)", re.M),
    re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)", re.M),
    re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:fn|struct|enum|trait|mod)\s+([A-Za-z_]\w*)", re.M),
    re.compile(r"^\s*def\s+(?:self\.)?([A-Za-z_]\w*[?!]?)", re.M),
    re.compile(r"^\s*(?:module|class)\s+([A-Z]\w*)", re.M),
]


def find_symbols(rel_path: str, text: str) -> list[dict]:
    """List symbol definitions with qualified names and 1-based lines."""
    symbols: list[dict] = []
    if rel_path.endswith((".py", ".pyi")):
        try:
            tree = ast.parse(text)
        except SyntaxError:
            tree = None
        if tree is not None:
            def visit(nodes, prefix):
                for node in nodes:
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        kind = "class" if isinstance(node, ast.ClassDef) else "function"
                        name = prefix + node.name
                        symbols.append({"name": name, "kind": kind, "line": node.lineno,
                                        "end_line": node.end_lineno or node.lineno})
                        if isinstance(node, ast.ClassDef):
                            visit(node.body, name + ".")
                    elif isinstance(node, (ast.Assign, ast.AnnAssign)) and not prefix:
                        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                        for target in targets:
                            if isinstance(target, ast.Name) and target.id.isupper():
                                symbols.append({"name": target.id, "kind": "constant", "line": node.lineno})
            visit(tree.body, "")
            return symbols
    seen = set()
    for pattern in _GENERIC_SYMBOL_PATTERNS:
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start(1)) + 1
            key = (match.group(1), line)
            if key not in seen:
                seen.add(key)
                symbols.append({"name": match.group(1), "kind": "symbol", "line": line})
    symbols.sort(key=lambda s: s["line"])
    return symbols


def lookup_symbol(symbols: list[dict], name: str) -> dict | None:
    for symbol in symbols:
        if symbol["name"] == name:
            return symbol
    if "." not in name:
        for symbol in symbols:
            if symbol["name"].rsplit(".", 1)[-1] == name:
                return symbol
    return None


def parse_line_range(spec: str | None) -> tuple[int, int] | None:
    if not spec:
        return None
    match = re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d+)\s*)?", spec)
    if not match:
        raise ValueError(f"bad line range: {spec!r}")
    start = int(match.group(1))
    end = int(match.group(2) or start)
    if start < 1 or end < start:
        raise ValueError(f"bad line range: {spec!r}")
    return start, end


# --------------------------------------------------------------------------
# Guide document contract
# --------------------------------------------------------------------------

VOID_TAGS = frozenset("area base br col embed hr img input link meta param source track wbr".split())


@dataclass
class SourceRef:
    path: str
    ref: str | None
    sym: str | None
    occurrence: int | None
    lines: str | None
    line: int  # line in the guide file, for error messages
    kind: str  # "ref", "sym", "finding", "lines", "path"
    keys: tuple[str, ...] = ()  # data-key literals: key lines within the anchored range


@dataclass
class Question:
    id: str
    module: str | None
    answer: str
    choices: list[str]
    prompt: str
    retired: str | None
    line: int


@dataclass
class Finding:
    id: str
    module: str | None
    path: str | None
    lines: str | None
    title: str
    draft: str
    line: int


@dataclass
class PlanRow:
    """One row of the overview's study plan: a module, its ratings and the
    recommended intensity (the reader can change it in the page)."""
    module: str
    importance: str
    complexity: str
    intensity: str
    line: int


@dataclass
class Listing:
    """A code listing (``figure.code-listing``) whose text is checked against source."""
    path: str | None
    lines: str | None
    exercise: str | None
    code: list[str]
    line: int
    keys: tuple[str, ...] = ()


@dataclass
class Guide:
    meta: dict = field(default_factory=dict)
    modules: list[str] = field(default_factory=list)
    module_titles: dict = field(default_factory=dict)
    refs: list[SourceRef] = field(default_factory=list)
    questions: list[Question] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    exercises: list[str] = field(default_factory=list)
    listings: list[Listing] = field(default_factory=list)
    plan: list[PlanRow] = field(default_factory=list)
    refresh: list[PlanRow] = field(default_factory=list)
    areas: list[tuple[str, int, str | None]] = field(default_factory=list)  # (path, line, module)
    overview: str | None = None
    final: str | None = None
    summary: str | None = None
    depths: list[tuple[str, int, str | None]] = field(default_factory=list)
    written: dict = field(default_factory=dict)
    exercise_modules: list[tuple[str, str | None]] = field(default_factory=list)
    assets: list[str] = field(default_factory=list)
    inline_scripts: list[int] = field(default_factory=list)
    has_main: bool = False

    @property
    def mode(self) -> str:
        return self.meta.get("mode", "")

    @property
    def guide_id(self) -> str:
        return self.meta.get("guide-id", "")

    @property
    def round(self) -> int:
        try:
            return int(self.meta.get("round", "1"))
        except ValueError:
            return 0

    @property
    def study_modules(self) -> list[str]:
        """Modules the plan covers: everything except overview/final/summary."""
        special = {self.overview, self.final, self.summary}
        return [m for m in self.modules if m not in special]

    @property
    def version(self) -> int:
        try:
            return int(self.meta.get("version", "1"))
        except ValueError:
            return 0

    def question(self, qid: str) -> Question | None:
        return next((q for q in self.questions if q.id == qid), None)


class _GuideParser(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.guide = Guide()
        self.stack: list[dict] = []

    def _ctx(self, key):
        for frame in reversed(self.stack):
            if frame.get(key) is not None:
                return frame[key]
        return None

    def handle_starttag(self, tag, attrs):
        self._start(tag, attrs, closed=tag in VOID_TAGS)

    def handle_startendtag(self, tag, attrs):
        self._start(tag, attrs, closed=True)

    def _start(self, tag, attrs, closed):
        a = {k: (v if v is not None else "") for k, v in attrs}
        line = self.getpos()[0]
        g = self.guide
        frame: dict = {"tag": tag}
        classes = a.get("class", "").split()
        if tag == "main" and "data-guide" in a:
            g.has_main = True
            g.meta = {k[5:]: v for k, v in a.items() if k.startswith("data-")}
        if tag == "link" and a.get("rel") == "stylesheet":
            g.assets.append(a.get("href", ""))
        if tag == "script":
            if a.get("src"):
                g.assets.append(a["src"])
            elif a.get("type", "") not in ("application/json", "application/ld+json"):
                g.inline_scripts.append(line)
        if "data-module" in a and tag == "section":
            frame["module"] = a["data-module"]
            if "data-overview" in a:
                g.overview = a["data-module"]
            if "data-final" in a:
                g.final = a["data-module"]
            if "data-summary" in a:
                g.summary = a["data-module"]
            g.written[a["data-module"]] = a.get("data-written", "deep")
            g.modules.append(a["data-module"])
            g.module_titles[a["data-module"]] = a.get("data-title", a["data-module"])
        module = frame.get("module") or self._ctx("module")
        if "data-q" in a:
            q = Question(a["data-q"], module, a.get("data-answer", ""), [], "", a.get("data-retired"), line)
            g.questions.append(q)
            frame["question"] = q
        question = self._ctx("question") if "question" not in frame else frame["question"]
        if question is not None and "data-choice" in a:
            question.choices.append(a["data-choice"])
        if question is not None and "prompt" in classes:
            frame["prompt"] = question
        if "data-finding" in a:
            f = Finding(a["data-finding"], module, a.get("data-src"), a.get("data-lines"), "", "", line)
            g.findings.append(f)
            frame["finding"] = f
        finding = frame.get("finding") or self._ctx("finding")
        if finding is not None and tag in ("h3", "h4") and not finding.title:
            frame["finding_title"] = finding
        if finding is not None and "comment-draft" in classes:
            frame["finding_draft"] = finding
        if "data-plan" in a:
            g.plan.append(PlanRow(a["data-plan"], a.get("data-importance", ""), a.get("data-complexity", ""),
                                  a.get("data-intensity", ""), line))
        if "data-area" in a:
            g.areas.append((a["data-area"], line, module))
        if "data-refresh" in a:
            g.refresh.append(PlanRow(a["data-refresh"], a.get("data-importance", ""), a.get("data-complexity", ""),
                                     a.get("data-intensity", ""), line))
        if "data-depth" in a:
            g.depths.append((a["data-depth"], line, module))
        if "data-exercise" in a:
            g.exercises.append(a["data-exercise"])
            g.exercise_modules.append((a["data-exercise"], module))
            frame["exercise"] = a["data-exercise"]
        if tag == "figure" and "code-listing" in classes:
            listing = Listing(a.get("data-src"), a.get("data-lines"), self._ctx("exercise"), [], line,
                              tuple(k.strip() for k in a.get("data-key", "").split("|") if k.strip()))
            g.listings.append(listing)
            frame["listing"] = listing
        listing = self._ctx("listing")
        if listing is not None and tag == "pre":
            frame["listing_pre"] = listing
            listing.code.append("")
        if listing is not None and tag == "span" and "l" in classes and self._ctx("listing_pre"):
            # One <span class="l"> per line; start a fresh line unless this is the first.
            if listing.code and listing.code[-1] != "" or getattr(listing, "_spans", False):
                listing.code.append("")
            listing._spans = True
        if "data-src" in a and tag != "figure":
            occurrence = a.get("data-occurrence")
            if "data-finding" in a:
                kind = "finding"
            elif "data-ref" in a:
                kind = "ref"
            elif "data-sym" in a:
                kind = "sym"
            elif "data-lines" in a:
                kind = "lines"
            else:
                kind = "path"
            g.refs.append(SourceRef(
                a["data-src"], a.get("data-ref"), a.get("data-sym"),
                int(occurrence) if occurrence and occurrence.isdigit() else None,
                a.get("data-lines"), line, kind,
                tuple(k.strip() for k in a.get("data-key", "").split("|") if k.strip()),
            ))
        if not closed:
            self.stack.append(frame)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i]["tag"] == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        listing = self._ctx("listing_pre")
        if listing is not None:
            if getattr(listing, "_spans", False):
                listing.code[-1] += data.replace("\n", "")
            else:
                parts = data.split("\n")
                listing.code[-1] += parts[0]
                listing.code.extend(parts[1:])
        prompt = self._ctx("prompt")
        if prompt is not None:
            prompt.prompt += data
        title = self._ctx("finding_title")
        if title is not None:
            title.title += data
        draft = self._ctx("finding_draft")
        if draft is not None:
            draft.draft += data


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def parse_guide(text: str) -> Guide:
    parser = _GuideParser()
    parser.feed(text)
    parser.close()
    guide = parser.guide
    for q in guide.questions:
        q.prompt = _squash(q.prompt)
    for f in guide.findings:
        f.title = _squash(f.title)
        f.draft = f.draft.strip()
    for listing in guide.listings:
        listing.__dict__.pop("_spans", None)
    return guide


def listing_drift(listing_lines: list[str], source_lines: list[str]) -> str | None:
    """Check that a listing's non-blank lines appear, in order, in the source.

    Listings may skip lines (setup boilerplate, an ``...`` marker), but every
    line they show must be real. Returns a message for the first line that is
    not found, or None.
    """
    wanted = [l.strip() for l in listing_lines if l.strip() and l.strip() not in ("...", "…")]
    have = [l.strip() for l in source_lines]
    pos = 0
    for line in wanted:
        try:
            pos = have.index(line, pos) + 1
        except ValueError:
            return f"listing line not found in source (in order): {line!r}"
    return None


EXERCISE_BODY_MARKER = "# --- exercise ---"


def exercise_listing(path: Path) -> dict:
    """Source to show for an exercise: the docstring (shown as the prompt) and
    everything above an optional ``# --- exercise ---`` marker are left out.
    Lines that print are flagged so the UI can highlight them."""
    text = read_text(path)
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    start = 0
    marker = next((i for i, l in enumerate(lines) if l.strip() == EXERCISE_BODY_MARKER), None)
    if marker is not None:
        start = marker + 1
    else:
        try:
            tree = ast.parse(text)
            if tree.body and isinstance(tree.body[0], ast.Expr) and isinstance(getattr(tree.body[0], "value", None), ast.Constant) \
                    and isinstance(tree.body[0].value.value, str):
                start = tree.body[0].end_lineno
        except SyntaxError:
            pass
    while start < len(lines) and not lines[start].strip():
        start += 1
    shown = lines[start:]
    return {
        "first_line": start + 1,
        "lines": shown,
        "prints": [i + start + 1 for i, l in enumerate(shown) if re.search(r"\bprint\(", l)],
    }


def load_guide(path: Path) -> Guide:
    return parse_guide(read_text(path))


def guide_exercise_dirs(guide_path: Path, onboard_dir: Path = ONBOARD_DIR) -> list[Path]:
    """Exercise lookup order: next to the guide first, then the shared set."""
    dirs = [guide_path.parent / "exercises"]
    shared = onboard_dir / "exercises"
    if shared not in dirs:
        dirs.append(shared)
    return dirs


def resolve_exercise(name: str, dirs: list[Path]) -> Path | None:
    if not EXERCISE_NAME_RE.match(name):
        return None
    for directory in dirs:
        candidate = directory / f"{name}.py"
        if candidate.is_file():
            return candidate.resolve()
    return None


# --------------------------------------------------------------------------
# Exercises
# --------------------------------------------------------------------------

def exercise_predict(path: Path) -> str:
    """Return the text after ``PREDICT:`` in the module docstring, or ''."""
    try:
        doc = ast.get_docstring(ast.parse(read_text(path))) or ""
    except SyntaxError:
        return ""
    match = re.search(r"^PREDICT:\s*(.*)", doc, re.S | re.M)
    return match.group(1).strip() if match else ""


def normalize_output(text: str) -> str:
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines) + "\n" if lines else ""


# Runs inside the child before exec'ing the exercise: apply resource limits so
# the parent never needs preexec_fn (unsafe in a threaded server).
_LAUNCHER = r"""
import os, sys
try:
    import resource
    cpu, mem, fsize = (int(v) for v in sys.argv[1:4])
    for limit, value in ((resource.RLIMIT_CPU, cpu), (resource.RLIMIT_FSIZE, fsize),
                         (resource.RLIMIT_CORE, 0)):
        resource.setrlimit(limit, (value, value))
    try:
        resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
    except (ValueError, OSError, AttributeError):
        pass
except ImportError:
    pass
os.execv(sys.executable, [sys.executable, "-s", "-B", sys.argv[4]])
"""


def run_exercise(path: Path, timeout: float = 10.0, max_output: int = 64_000,
                 memory_bytes: int = 1 << 30) -> dict:
    """Run a registered exercise in a constrained subprocess.

    Limits: wall-clock timeout, CPU seconds, address space, output file size,
    a scrubbed environment, a throwaway working directory, and no stdin. This
    is not a sandbox: the exercise can still use the network and read files
    the user can read, so only repository-reviewed exercises are run.
    """
    started = time.monotonic()
    work = Path(tempfile.mkdtemp(prefix="quizzler-ex-"))
    try:
        out_path, err_path = work / ".stdout", work / ".stderr"
        env = {
            "PATH": os.defpath, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TZ": "UTC",
            "PYTHONHASHSEED": "0", "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1",
            "HOME": str(work), "TMPDIR": str(work), "NO_PROXY": "*", "no_proxy": "*",
        }
        cmd = [sys.executable, "-I", "-c", _LAUNCHER,
               str(int(timeout) + 1), str(memory_bytes), str(max_output * 4), str(path)]
        with open(out_path, "wb") as out, open(err_path, "wb") as err:
            kwargs = {"start_new_session": True} if os.name == "posix" else {}
            proc = subprocess.Popen(cmd, cwd=work, env=env, stdin=subprocess.DEVNULL,
                                    stdout=out, stderr=err, **kwargs)
            timed_out = False
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                _kill(proc)
                proc.wait()
        stdout, out_trunc = _read_capped(out_path, max_output)
        stderr, err_trunc = _read_capped(err_path, max_output)
        return {
            "stdout": stdout, "stderr": stderr, "exit_code": proc.returncode,
            "timed_out": timed_out, "truncated": out_trunc or err_trunc,
            "duration_ms": int((time.monotonic() - started) * 1000),
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _kill(proc: subprocess.Popen) -> None:
    try:
        if os.name == "posix":
            import signal
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except (ProcessLookupError, PermissionError):
        pass


def _read_capped(path: Path, limit: int) -> tuple[str, bool]:
    with open(path, "rb") as handle:
        data = handle.read(limit + 1)
    return data[:limit].decode("utf-8", errors="replace"), len(data) > limit


# --------------------------------------------------------------------------
# Git diff helpers (shared by the server's Changes view and review.py)
# --------------------------------------------------------------------------

class GitError(Exception):
    pass


def git(cwd: Path, *args: str, check: bool = True) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(cwd), "-c", "core.quotepath=off", *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
    except OSError as exc:
        raise GitError(f"git unavailable: {exc}") from None
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args[:3])} failed: {proc.stderr.strip()}")
    return proc.stdout


def require_sha(value: str) -> str:
    if not isinstance(value, str) or not SHA_RE.match(value):
        raise GitError(f"not a full commit SHA: {value!r}")
    return value


def diff_changes(worktree: Path, base: str, head: str) -> list[dict]:
    """Changed files between two commits, with rename and binary detection."""
    base, head = require_sha(base), require_sha(head)
    status_tokens = git(worktree, "diff", "--no-color", "--find-renames", "--name-status", "-z", base, head).split("\0")
    changes: list[dict] = []
    i = 0
    while i < len(status_tokens) and status_tokens[i]:
        status = status_tokens[i]
        if status[0] in "RC":
            changes.append({"status": status[0], "old_path": status_tokens[i + 1], "path": status_tokens[i + 2]})
            i += 3
        else:
            changes.append({"status": status[0], "old_path": None, "path": status_tokens[i + 1]})
            i += 2
    stats: dict[str, tuple] = {}
    num_tokens = git(worktree, "diff", "--no-color", "--find-renames", "--numstat", "-z", base, head).split("\0")
    i = 0
    while i < len(num_tokens) and num_tokens[i]:
        added, deleted, path = num_tokens[i].split("\t", 2)
        if path == "":  # rename: old and new follow as separate tokens
            path = num_tokens[i + 2]
            i += 3
        else:
            i += 1
        stats[path] = (added, deleted)
    for change in changes:
        added, deleted = stats.get(change["path"], ("0", "0"))
        change["binary"] = added == "-"
        change["additions"] = 0 if added == "-" else int(added)
        change["deletions"] = 0 if deleted == "-" else int(deleted)
    return changes


def diff_file(worktree: Path, base: str, head: str, path: str, old_path: str | None = None,
              context: int = 5) -> str:
    base, head = require_sha(base), require_sha(head)
    paths = [old_path, path] if old_path and old_path != path else [path]
    return git(worktree, "diff", "--no-color", "--find-renames", f"-U{context}", base, head, "--", *paths)


HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", re.M)


def hunk_ranges(diff_text: str) -> list[tuple[int, int]]:
    """Head-side (start, end) line ranges covered by each hunk, context included."""
    ranges = []
    for match in HUNK_RE.finditer(diff_text):
        start, count = int(match.group(3)), int(match.group(4) or 1)
        if count:
            ranges.append((start, start + count - 1))
    return ranges


def changed_lines(worktree: Path, base: str, head: str, path: str) -> list[int]:
    """Head-side line numbers added or modified relative to ``base``."""
    text = diff_file(worktree, base, head, path, context=0)
    lines: list[int] = []
    for start, end in hunk_ranges(text):
        lines.extend(range(start, end + 1))
    return lines


def show_file(repo: Path, sha: str, path: str) -> str | None:
    require_sha(sha)
    proc = subprocess.run(["git", "-C", str(repo), "show", f"{sha}:{path}"],
                          capture_output=True)
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", errors="replace")


# --------------------------------------------------------------------------
# Result codes and adaptive classification
# --------------------------------------------------------------------------

def classify(correct: bool, confidence: str) -> str:
    """misconception (wrong+sure), gap (wrong otherwise), fragile (right but
    not sure), mastered (right+sure)."""
    if correct:
        return "mastered" if confidence == "sure" else "fragile"
    return "misconception" if confidence == "sure" else "gap"


def parse_result_code(text: str) -> tuple[str, dict]:
    text = text.strip()
    for prefix, kind in RESULT_PREFIXES.items():
        if text.startswith(prefix):
            body = text[len(prefix):].strip()
            try:
                payload = json.loads(body)
            except json.JSONDecodeError as exc:
                raise ValueError(f"result code JSON is invalid: {exc}") from None
            if not isinstance(payload, dict):
                raise ValueError("result code payload must be an object")
            return kind, payload
    raise ValueError(f"expected a result code starting with one of {', '.join(RESULT_PREFIXES)}")


def grade_answers(payload: dict, guide: Guide | None) -> dict[str, dict]:
    """Recompute correctness against the guide when available and classify."""
    graded = {}
    answers = payload.get("answers") or {}
    if not isinstance(answers, dict):
        return graded
    for qid, answer in answers.items():
        if not isinstance(answer, dict):
            continue
        choice = str(answer.get("choice", ""))
        # Confidence is optional; an unrated answer is treated as "not sure".
        confidence = answer.get("confidence") if answer.get("confidence") in CONFIDENCE_LEVELS else "unrated"
        question = guide.question(qid) if guide else None
        correct = (choice == question.answer) if question else bool(answer.get("correct"))
        graded[qid] = {
            "module": question.module if question else answer.get("module"),
            "prompt": question.prompt if question else "",
            "choice": choice,
            "answer": question.answer if question else None,
            "confidence": confidence,
            "correct": correct,
            "classification": classify(correct, confidence),
            "reasoning": str(answer.get("reasoning", ""))[:2000],
        }
    return graded


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def local_dir_for(guide_path: Path) -> Path:
    """The local/ state directory belonging to the onboarding dir a guide lives in."""
    for parent in Path(guide_path).resolve().parents:
        if (parent / "app" / "guide.js").is_file():
            return parent / "local"
    return LOCAL_DIR


def results_path_for(guide_path: Path, guide_id: str, local_dir: Path = LOCAL_DIR) -> Path:
    """Where browser progress for a guide is stored (always under local/)."""
    guide_path = guide_path.resolve()
    if guide_path.is_relative_to(local_dir.resolve()):
        return guide_path.parent / "results.json"
    safe = guide_id if GUIDE_ID_RE.match(guide_id or "") else "guide"
    return local_dir / "results" / f"{safe}.json"


# --------------------------------------------------------------------------
# Requests from the page to the Claude session
# --------------------------------------------------------------------------
#
# The page cannot talk to Claude directly. Instead the server records
# requests in a JSON queue next to the results file, and the session runs
# ``relay.py wait`` in the background: that command exits as soon as a
# request is pending, which wakes the session to handle it.

# write-modules: write the listed modules to the reader's chosen depths.
# help-module: rewrite one module around the reader's question.
# next-module: review mode, append the next module.
# final-review: write the end-of-round review.
# new-round: go back over missed content (refresher round).
# summary: the reader calls it a day; write the final report.
REQUEST_ACTIONS = ("write-modules", "help-module", "next-module", "final-review", "new-round", "summary")
MAX_HELP_CHARS = 2000
LISTENER_STALE_SECONDS = 15


def requests_path_for(guide_path: Path, guide_id: str, local_dir: Path | None = None) -> Path:
    results = results_path_for(guide_path, guide_id, local_dir or local_dir_for(guide_path))
    return results.with_name(f"{results.stem}.requests.json")


def listener_path(requests_path: Path) -> Path:
    """Heartbeat file touched by ``relay.py wait`` while a session listens."""
    return requests_path.with_name(requests_path.name.replace(".requests.json", ".listener"))


def listener_alive(requests_path: Path) -> bool:
    beat = listener_path(requests_path)
    try:
        return time.time() - beat.stat().st_mtime < LISTENER_STALE_SECONDS
    except OSError:
        return False


def update_requests(path: Path, change=None) -> list[dict]:
    """Read (and optionally modify) the request queue under an exclusive lock.

    ``change`` receives the list and may mutate it; the result is written
    back atomically. Returns the (updated) list.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_name(path.name + ".lock"), "a+") as lock:
        try:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX)
        except ImportError:  # Windows: single-user tool, accept the race
            pass
        try:
            items = json.loads(path.read_text(encoding="utf-8")).get("requests", []) if path.is_file() else []
        except (json.JSONDecodeError, AttributeError):
            items = []
        if change is not None:
            change(items)
            write_json(path, {"requests": items})
        return items
