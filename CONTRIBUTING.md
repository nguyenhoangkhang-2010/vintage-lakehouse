# Contributing

The full branching and commit rules live in [docs/GIT_WORKFLOW.md](docs/GIT_WORKFLOW.md).
Short version:

1. Branch from `develop` using `<type>/<scope>` (for example `feature/time-model`).
2. Keep the branch small and short-lived. One roadmap item per branch.
3. Commit with a one-line [Conventional Commit](https://www.conventionalcommits.org/) message.
4. Open a pull request into `develop`. CI must pass (ruff, mypy, pytest).
5. Update the docs and the CHANGELOG when behaviour changes.

## Local checks

```powershell
ruff check .
ruff format --check .
mypy
pytest
```

## Definition of done for a branch

- Tests cover the new behaviour, including the failure cases.
- `ruff`, `mypy --strict` and `pytest` pass.
- Public functions have type hints and docstrings.
- Any measured number is reproducible with a documented command.
- New limitations are added to [docs/LIMITATIONS.md](docs/LIMITATIONS.md).
- `CHANGELOG.md` has an entry under `Unreleased`.
