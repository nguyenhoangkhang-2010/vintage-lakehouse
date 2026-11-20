# Git workflow

## Branches

| Branch | Purpose | Rules |
|--------|---------|-------|
| `main` | Released, stable code. Every commit is a tagged release or a release merge | Protected. Changes arrive only by pull request from `develop` (or `hotfix/*`) |
| `develop` | Integration branch. Always green | Protected. Changes arrive only by pull request from short-lived branches |
| `<type>/<scope>` | One roadmap item | Created from `develop`, deleted after merge |

Branch types: `feature/`, `fix/`, `docs/`, `chore/`, `refactor/`, `test/`, `hotfix/`
(`hotfix/` branches start from `main` and merge into both `main` and `develop`).

Branch names are lowercase, hyphen-separated, and match a roadmap item
(for example `feature/time-model`).

## Commit messages

One line, [Conventional Commits](https://www.conventionalcommits.org/), imperative mood,
at most 72 characters, no trailing period:

```text
<type>(<optional scope>): <summary>
```

Types: `feat`, `fix`, `docs`, `test`, `refactor`, `perf`, `build`, `ci`, `chore`.

Examples:

```text
chore: scaffold project structure, tooling, docs and CI
feat(storage): add Observation model with validated timestamps
test(storage): add as_of invariant property test
```

One logical commit per branch is enough. If a branch has several commits, the pull request is
squash-merged and the pull request title (also a Conventional Commit line) becomes the
history entry on `develop`.

## Day-to-day loop

1. Update `develop`, then branch from it.
2. Implement one roadmap item with tests.
3. Run the local checks (`ruff check .`, `ruff format --check .`, `mypy`, `pytest`).
4. Commit with a one-line message.
5. Push the branch and open a pull request into `develop`. Fill in the template.
6. Wait for CI. Squash-merge. Delete the branch.
7. Start the next roadmap item from the updated `develop`.

Keep branches short-lived (ideally under a week) and rebase on `develop` before opening the PR
if it has moved.

## Releases

At a milestone, open a pull request from `develop` to `main`, merge it, then tag:

```text
git tag -a v0.1.0 -m "v0.1.0"
git push origin v0.1.0
```

Move the `Unreleased` section of `CHANGELOG.md` under the new version in the release PR.

## First-time setup

```powershell
git init -b main
git commit --allow-empty -m "chore: initialize repository"
git branch develop
git checkout -b chore/project-skeleton develop
git add -A
git commit -m "chore: scaffold project structure, tooling, docs and CI"
```

Then create an empty repository on GitHub (without README or license), and:

```powershell
git remote add origin <your-repo-url>
git push -u origin main develop
git push -u origin chore/project-skeleton
```

Open a pull request from `chore/project-skeleton` into `develop`.

## Recommended GitHub settings

- Protect `main` and `develop`: require a pull request and passing CI, block force pushes.
- Allow squash merging and delete head branches after merge.
- Install the local hooks once: `pre-commit install`. They block direct commits to `main` and
  `develop` and run `ruff` before each commit.
