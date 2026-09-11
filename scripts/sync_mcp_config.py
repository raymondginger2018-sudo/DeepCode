#!/usr/bin/env python3
"""
MCP configuration sync tool -- single source of truth: mcp_servers_canonical.json

Usage:
    python scripts/sync_mcp_config.py          # Sync to settings.json and .mcp.json
    python scripts/sync_mcp_config.py --check  # Check consistency only
"""
import json
import re
import sys
import os

_ENV_REF_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANONICAL_PATH = os.path.join(PROJECT_ROOT, "mcp_servers_canonical.json")
SETTINGS_PATH = os.path.join(PROJECT_ROOT, "settings.json")
MCP_JSON_PATH = os.path.join(PROJECT_ROOT, ".mcp.json")


def _resolve_env_refs(value):
    """Recursively resolve ${VAR} references from environment variables."""
    if isinstance(value, str):
        def _replace(match):
            name = match.group(1)
            val = os.environ.get(name)
            if val is None:
                print(f"  [WARN] Env var '${{{name}}}' not set, keeping as-is")
                return match.group(0)
            return val
        return _ENV_REF_PATTERN.sub(_replace, value)
    if isinstance(value, dict):
        return {k: _resolve_env_refs(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env_refs(item) for item in value]
    return value


def load_canonical():
    with open(CANONICAL_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    servers = data["mcpServers"]
    return _resolve_env_refs(servers)


def update_settings(mcp_servers):
    if not os.path.exists(SETTINGS_PATH):
        config = {"mcpServers": mcp_servers}
    else:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
        config["mcpServers"] = mcp_servers
    with open(SETTINGS_PATH, "w", encoding="utf-8", newline="\n") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    print(f"  [OK] Updated: {SETTINGS_PATH}")


def update_mcp_json(mcp_servers):
    config = {"mcpServers": mcp_servers}
    with open(MCP_JSON_PATH, "w", encoding="utf-8", newline="\n") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    print(f"  [OK] Updated: {MCP_JSON_PATH}")


def check_consistency(mcp_servers):
    issues = []
    for path, label in [(SETTINGS_PATH, "settings.json"), (MCP_JSON_PATH, ".mcp.json")]:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        actual = data.get("mcpServers", {})
        if actual != mcp_servers:
            issues.append(f"[X] {label}: out of sync with canonical")
        else:
            print(f"  [OK] {label}: consistent")
    return len(issues) == 0


def main():
    if not os.path.exists(CANONICAL_PATH):
        print(f"[X] Canonical file not found: {CANONICAL_PATH}")
        sys.exit(1)

    mcp_servers = load_canonical()
    print(f"[INFO] Canonical config: {len(mcp_servers)} MCP servers")

    if "--check" in sys.argv:
        if check_consistency(mcp_servers):
            print("\n[OK] All configs consistent")
        else:
            print("\n[X] Inconsistencies found, run sync")
            sys.exit(1)
    else:
        update_settings(mcp_servers)
        update_mcp_json(mcp_servers)
        print("\n[OK] Sync complete!")
        print("[HINT] Edit mcp_servers_canonical.json then run this script")


if __name__ == "__main__":
    main()
