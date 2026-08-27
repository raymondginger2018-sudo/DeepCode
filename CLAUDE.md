# CLAUDE.md

DeepCode is an open-source AI coding agent that generates code from research papers and text descriptions.

## Commands

- **Test**: `pytest -q` (run all tests quickly)
- **Lint**: `ruff check .` (check code style)
- **Format**: `ruff format .` (auto-format code)
- **Pre-commit**: `pre-commit run --all-files` (run all checks before push)
- **Single test**: `pytest -q tests/test_file.py -k test_name`

## CI Workflows

- **Linting and Formatting**: ruff check + ruff format (pre-commit)
- **Python CI**: pytest matrix 3.12/3.13/3.14 + Windows lifecycle + package
- **Security CI**: gitleaks + pip-audit + npm audit + cargo-audit
- **Desktop CI**: Tauri build (Node + Rust)

## PR Guidelines

- One module per PR, < 500 lines preferred
- New files with env-knob (default off) are easiest to merge
- Rebase on latest upstream/main before push
- All 4 CI workflows must be green before requesting review
- Security changes need @Zongwei9888 approval

## Repository

- Upstream: `HKUDS/DeepCode` (main)
- Fork: `raymondginger2018-sudo/DeepCode`
- Language: Python 3.12+
- Python 3.12 path: `C:\Users\raymo\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.12_qbz5n2kfra8p0\python.exe`