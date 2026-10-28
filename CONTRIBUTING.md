# Contributing to Quizzler

Thanks for helping. This page covers the licence, how to sign off your commits, how to run the checks, and our policy on AI-assisted contributions.

## Licence and sign-off

- Quizzler is licensed under the [Apache License 2.0](LICENSE). Your contributions are licensed the same way.
- We use the [Developer Certificate of Origin](DCO) (DCO) instead of a contributor licence agreement. **Every commit needs a sign-off**, which certifies that you have the right to submit the change:

  ```sh
  git commit -s -m "Describe the change"
  ```

  This adds `Signed-off-by: Your Name <you@example.com>`, using your `user.name` and `user.email`. Use your real name.
- Forgot one? Fix the latest commit with `git commit --amend -s --no-edit`. For several commits, use `git rebase --signoff main` and force-push your branch.
- You keep the copyright in your contributions. Do not remove existing licence headers.
- New source files start with `SPDX-License-Identifier: Apache-2.0` in a comment, as in the existing files.

## Workflow

1. Fork the repository and create a topic branch from `main`.
2. Make a focused change. Keep unrelated changes for separate pull requests.
3. Run the checks below.
4. Open a pull request against `main` and fill in the template.

For anything large, open an issue first so we can agree on the approach before you write it.

## Setup and checks

You need Python 3.10+ and Git. The PR flow also needs `gh`, and the layout gate needs Firefox. There are no packages to install.

CI runs these three checks, and so should you before pushing:

```sh
python -m unittest discover -s tests
python quizzler/check.py
python quizzler/check_layout.py --require-browser
```

- `check.py` is the drift gate. If you change a source file that a guide anchors to, it tells you which anchor moved. Update the guide in the same pull request.
- See [quizzler/README.md](quizzler/README.md) for the guide contract and the security model.

## AI-assisted contributions

AI tools are welcome. The rules are:

- **Disclose it.** Tick the AI box in the pull request template and say which tool you used and for what.
- **You are responsible for every line.** Understand it well enough to explain it and defend it in review.
- **A person submits the pull request and signs off the commits.** Unattended agents must not open pull requests, and an AI tool cannot give the DCO sign-off for you.
- **Check the licence.** Do not submit generated code that reproduces code under an incompatible licence.

## Reviewing other people's pull requests

`/review-pr <N>` runs exercises that import the pull request's code on your machine. Read the code under test before you run it, particularly for a first-time contributor.
