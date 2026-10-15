---
description: Learn and review a pull request module by module with Quizzler (never posts without explicit permission)
argument-hint: "<PR number> [--diff-base merge-base|base-tip|<sha>]"
---

You are running Quizzler's PR review flow for: $ARGUMENTS

Read `docs/onboarding/README.md` for the guide contract. All state goes in `docs/onboarding/local/reviews/pr-<N>/` and the code under review is in `.claude/worktrees/pr-<N>/`. Both are ignored by Git.

## Writing style (required)

Readers skim, so structure every module for skimming:

1. **Open with key points.** Start each module with a `<div class="key-points">` that has a `<h3>Key points</h3>` heading and 2–4 bullets.
2. **Prefer lists to paragraphs.** Use bullets for facts and numbered lists for steps or flows. Keep any remaining paragraph to one or two sentences.
3. **Draw a diagram for every flow.** Each module has at least one inline SVG diagram of the path, state machine or structure it explains. Use a table for any comparison.
4. **Split modules into short `<h3>` sub-sections.** Each covers one idea.
5. **Make code look like code.**
   - Wrap identifiers, paths and commands in `<code>`.
   - Show any code longer than one line as `<figure class="code-listing" data-src="path" data-lines="A-B"><pre>…</pre></figure>`, copied exactly. `check.py` verifies it against the file, and the caption gets a "Show in file" button.
   - Never put code in a paragraph.
6. **Anchor anything the reader might want to see.** The code pane is open by default on wide screens and follows the anchor the reader is looking at. Without anchors it has nothing to show.
7. **Don't hand-copy exercise code.** The engine shows it from the file as step 1 ("Read the code"). Put setup boilerplate above a `# --- exercise ---` line to hide it.
8. **Use asides sparingly.** `<p class="note">` holds a short aside and `<div class="warning">` holds a trap or gotcha.

## Hard rule: no GitHub writes without explicit permission

Never post, edit, approve, request changes, or comment on GitHub as part of this flow. Finishing a review, marking findings "ready", or generating a preview is **not** permission to post. You may post only if all of these hold:
1. The user has seen the exact output of `review.py preview N`.
2. `review.py status N` shows the review is not stale.
3. The user then gives a separate, unambiguous instruction to post, such as "post these comments".

Before you post, show the exact text, file, lines and commit SHA one more time.

## Steps

1. **Prepare.** Run `python docs/onboarding/review.py prepare <N>` with the user's `--diff-base` if they gave one. This fetches the PR with `gh`, creates or refreshes the worktree at the exact head SHA, and records the SHAs in `meta.json`. If a review already exists and the head moved, tell the user that the earlier results were archived.
2. **Understand the change.** Read the diff and the surrounding call paths in the worktree, not in the main checkout. Divide the change into coherent modules, for example request/data flow, persistence, model behaviour, and UI/API. Use one module for a small PR. Write the module plan to `docs/onboarding/local/reviews/pr-<N>/plan.md`.
3. **Write module 1 only.** Create `docs/onboarding/local/reviews/pr-<N>/guide.html` with `data-mode="review"`, `data-guide-id="pr-<N>"`, `data-pr`, `data-head-sha` (from meta.json) and `data-version="1"`. Its asset links are `../../../app/guide.css` and `../../../app/guide.js`. The module includes:
   - Context, following the writing style above: key points, a before/after diagram of the changed flow, and bullets explaining what changed and why. Show changed code as a `code-listing` of the head lines.
   - Anchors into the head code (`data-src` + `data-ref`, `data-sym` or `data-lines`).
   - 2–3 questions that check whether the reviewer understands the behaviour change.
   - Exercises where useful, in `local/reviews/pr-<N>/exercises/`. Read any PR code before an exercise runs it.
   - Proposed findings inside `.findings`. Each needs a concrete failure scenario (the inputs or state that lead to the wrong behaviour), evidence anchored at the head SHA, the impact, and an editable `.comment-draft`. Say what is uncertain. Do not include style nits unless they matter. If you have no real findings, include none.
4. **Validate.** Run `python docs/onboarding/check.py --review <N>` and `python docs/onboarding/check_layout.py --guide docs/onboarding/local/reviews/pr-<N>/guide.html`, and fix what they report.
5. **Serve.** Start `python docs/onboarding/serve.py --review <N>` in the background and give the user the URL. Findings stay hidden until the module's questions are answered. The reviewer can agree, be unsure or disagree with each one, edit the comment drafts, and add their own line comments from the source pane.
6. **Loop.** When the user says the module is done, read `local/reviews/pr-<N>/results.json` (autosaved), or ingest the code they paste with `python docs/onboarding/review.py ingest <N> -`. Then:
   - Classify the answers and adapt. Clear up misconceptions before moving on, and use `python docs/onboarding/progress.py --guide docs/onboarding/local/reviews/pr-<N>/guide.html report`.
   - Take the reviewer's disagreements seriously. Re-check the evidence, and withdraw or correct a finding when they are right.
   - Run `progress.py --guide … bump`, then append the next module. Do not change earlier modules except to correct them. Validate and tell the user to reload.
7. **Final review.** After the last module, point the user to the Review tab, where findings and comments are grouped as ready, needs another look and not posting. Then run `python docs/onboarding/review.py preview <N>` and show its output, including any PROBLEM lines, for example comments outside the diff. Stop here.
8. **Posting** happens only under the hard rule above, and then only with `gh api` against the `commit_id` from the preview.

When the user is finished, offer `python docs/onboarding/review.py cleanup <N>`. It is a dry run without `--yes`.
