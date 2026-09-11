#!/usr/bin/env python3
"""Generate .mcp.json from mcp_servers_canonical.json (resolve env vars)."""
import json, re, os

ENV_REF = re.compile(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}')

def resolve(v):
    if isinstance(v, str):
        def rep(m):
            val = os.environ.get(m.group(1))
            return val if val is not None else m.group(0)
        return ENV_REF.sub(rep, v)
    if isinstance(v, dict):
        return {k: resolve(v) for k, v in v.items()}
    if isinstance(v, list):
        return [resolve(i) for i in v]
    return v

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(root, 'mcp_servers_canonical.json'), encoding='utf-8') as f:
    data = json.load(f)
servers = resolve(data['mcpServers'])
with open(os.path.join(root, '.mcp.json'), 'w', encoding='utf-8', newline='\n') as f:
    json.dump({'mcpServers': servers}, f, ensure_ascii=False, indent=2)
print(f'[OK] Generated .mcp.json with {len(servers)} servers')
