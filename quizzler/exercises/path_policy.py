"""Which paths may the browser read?

PREDICT: For each path below, the script prints "allow" or "deny" followed by
the reason the policy gives. Predict the verdict for every line before you run
it. Pay attention to the hidden-file rule, the env.storage rule, and the
local/ guide state rule.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# --- exercise ---

from guidelib import PathPolicy, PolicyError  # noqa: E402

policy = PathPolicy(Path("/repo"), deny_prefixes=("quizzler/local",))

CASES = [
    "src/app.py",
    "../etc/passwd",
    "src/./app.py",
    ".git/config",
    "config/.env",
    "deploy/env.storage.json",
    "certs/server.pem",
    "quizzler/local/guide.html",
    "quizzler/serve.py",
    "assets/logo.png",
    "Makefile",
]

for path in CASES:
    try:
        policy.check_name(path)
        print(f"allow {path}")
    except PolicyError as exc:
        reason = str(exc).split(":")[0]
        print(f"deny  {path}  ({reason})")
