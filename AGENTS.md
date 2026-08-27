# AGENTS.md

DeepCode is an open-source AI coding agent that transforms research papers and text descriptions into working code. It supports Paper2Code, Text2Web, and Text2Backend workflows.

## Repository

- **Upstream**: `HKUDS/DeepCode` (main branch)
- **Fork**: `raymondginger2018-sudo/DeepCode`
- **Language**: Python 3.12+
- **Test**: `pytest -q` (ruff check + format before commit)
- **CI**: Linting and Formatting / Python CI / Security CI / Desktop CI

## Contribution Workflow

1. **Small PRs only** — one module per PR, < 500 lines preferred
2. **New file first** — pure additions with env-knob (default off) are easiest to merge
3. **Rebase before push** — always rebase on latest upstream/main
4. **Pre-commit check** — run `pre-commit run --all-files` before pushing
5. **CI must pass** — all 4 workflows green before requesting review
6. **Evidence over claims** — each PR needs test results, not just "it works"

## Key Contacts

- Maintainer: @Zongwei9888 (Zongwei Li)
- Review style: test-merges on latest main, gives specific feedback