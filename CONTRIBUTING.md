# Contributing to DeepCode

Thank you for your interest in contributing to DeepCode!

## PR Workflow

1. **Small PRs only** — one module per PR, < 500 lines preferred.
2. **New file first** — pure additions with env-knob (default off) are easiest to merge.
3. **Rebase before push** — always rebase on latest upstream/main.
4. **Pre-commit check** — run `pre-commit run --all-files` before pushing.
5. **CI must pass** — all 4 workflows green before requesting review.
6. **Evidence over claims** — each PR needs test results, not just "it works".

## Security Changes

Changes that touch security, permissions, sandbox, or authentication require **additional approval**:

1. Add the `security` label to the PR automatically.
2. Request review from @Zongwei9888 explicitly.
3. In the PR description, include a **Security Considerations** section describing:
   - What attack surface is being changed
   - Why the change is safe (fail-safe by default)
   - What testing was done to verify security properties
4. The PR will not be merged without explicit security review approval.

## Code Style

- Python: ruff format + ruff check (see `.pre-commit-config.yaml`)
- Run `pre-commit run --all-files` before every push
- Tests: `pytest -q` for Python, ensure all CI workflows pass

## Questions

Open an issue or start a Discussion for questions before submitting a PR.