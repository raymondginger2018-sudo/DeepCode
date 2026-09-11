#!/usr/bin/env python3
"""Build the sync/config branch payload by copying live files into .dc-sync/"""

import os, json, re, shutil, sys

ROOT = os.path.dirname(os.path.abspath(__file__))  # .dc-sync/
LIVE = os.path.dirname(ROOT)  # F:/DEEPCODE/

EXCLUDE_DIRS = {
    'node_modules', '__pycache__', '.git', '.cache', 'dist', 'build',
    '.pytest_cache', '.ruff_cache', '.mypy_cache', '.venv',
    'data',  # cerebellum.db lives here
    '.idea', '.vscode',
}
EXCLUDE_FILES = {
    '*.db', '*.sqlite', '*.sqlite3', '*.bak*', '*.log', '*.pyc',
    '*.pyo', '*.webp', '*.png', '*.jpg', '*.jpeg', '*.gif', '*.ico',
    '*.woff', '*.woff2', '*.ttf', '*.exe', '*.dll', '.env*',
    # Known files with hardcoded secrets – must be excluded from sync
    'github-token.md',
    'start_pr_watch.vbs',
}

def should_exclude(name, is_dir):
    if is_dir and name in EXCLUDE_DIRS:
        return True
    if not is_dir:
        for pat in EXCLUDE_FILES:
            if pat.startswith('*.'):
                if name.endswith(pat[1:]):
                    return True
            elif pat.endswith('*') and name.startswith(pat[:-1]):
                return True
    return False

def copy_skills(src_sub, dst_sub):
    """Copy skills from LIVE/src_sub to ROOT/dst_sub"""
    src = os.path.join(LIVE, src_sub)
    dst = os.path.join(ROOT, dst_sub)
    if not os.path.isdir(src):
        print(f"  SKIP {src_sub}: not found")
        return
    
    count = 0
    for skill in os.listdir(src):
        skill_src = os.path.join(src, skill)
        skill_dst = os.path.join(dst, skill)
        if not os.path.isdir(skill_src):
            continue
        if skill == 'harmony-next':
            print(f"  SKIP {src_sub}/{skill}: large doc corpus, clone separately")
            continue
        
        os.makedirs(skill_dst, exist_ok=True)
        for root, dirs, files in os.walk(skill_src):
            # Filter dirs in-place
            dirs[:] = [d for d in dirs if not should_exclude(d, True)]
            for f in files:
                if should_exclude(f, False):
                    continue
                src_file = os.path.join(root, f)
                rel = os.path.relpath(src_file, skill_src)
                dst_file = os.path.join(skill_dst, rel)
                os.makedirs(os.path.dirname(dst_file), exist_ok=True)
                shutil.copy2(src_file, dst_file)
                count += 1
        print(f"  COPY {skill}: copied into {src_sub}/{skill}")
    print(f"  Total: {count} files copied for {src_sub}")

def copy_path(src_sub, dst_sub=None):
    """Copy a single path with exclusion rules"""
    if dst_sub is None:
        dst_sub = src_sub
    src = os.path.join(LIVE, src_sub)
    dst = os.path.join(ROOT, dst_sub)
    if not os.path.exists(src):
        print(f"  SKIP {src_sub}: not found")
        return 0
    
    if os.path.isfile(src):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        print(f"  COPY {src_sub}")
        return 1
    
    count = 0
    os.makedirs(dst, exist_ok=True)
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if not should_exclude(d, True)]
        for f in files:
            if should_exclude(f, False):
                continue
            src_file = os.path.join(root, f)
            rel = os.path.relpath(src_file, src)
            dst_file = os.path.join(dst, rel)
            os.makedirs(os.path.dirname(dst_file), exist_ok=True)
            shutil.copy2(src_file, dst_file)
            count += 1
    print(f"  COPY {src_sub}: {count} files")
    return count

def sanitize_mcp_json():
    """Copy .mcp.json with secrets replaced by placeholders"""
    src = os.path.join(LIVE, '.mcp.json')
    dst = os.path.join(ROOT, '.mcp.json')
    if not os.path.exists(src):
        print("  SKIP .mcp.json: not found")
        return
    
    with open(src, 'r') as f:
        content = f.read()
    
    # Replace known secrets with placeholders
    content = re.sub(r'(token=)[a-zA-Z0-9]+', r'\1${TUSHARE_TOKEN}', content)
    content = re.sub(r'(GITHUB_PERSONAL_ACCESS_TOKEN["\']?\s*:\s*["\'])[^"\']+', r'\1${GITHUB_PAT}', content)
    content = re.sub(r'(DEEPSEEK_API_KEY["\']?\s*:\s*["\'])[^"\']+', r'\1${DEEPSEEK_API_KEY}', content)
    content = re.sub(r'(api_key["\']?\s*:\s*["\'])[^"\']+', r'\1${API_KEY}', content)
    # Generic: catch any sk-... pattern
    content = re.sub(r'(sk-[a-zA-Z0-9]{16,})', r'${DEEPSEEK_API_KEY}', content)
    # Generic: catch any ghp_ pattern
    content = re.sub(r'(ghp_[a-zA-Z0-9]{20,})', r'${GITHUB_PAT}', content)
    
    with open(dst, 'w') as f:
        f.write(content)
    print(f"  COPY .mcp.json (sanitized): secrets replaced with placeholders")

def sanitize_settings_json():
    """Create settings.json.example with placeholders"""
    src = os.path.join(LIVE, '.deepcode', 'settings.json')
    dst = os.path.join(ROOT, '.deepcode', 'settings.example.json')
    if not os.path.exists(src):
        print("  SKIP settings.json: not found")
        return
    
    with open(src, 'r') as f:
        content = f.read()
    
    content = re.sub(r'(sk-[a-zA-Z0-9]{16,})', r'${DEEPSEEK_API_KEY}', content)
    content = re.sub(r'(ghp_[a-zA-Z0-9]{20,})', r'${GITHUB_PAT}', content)
    
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, 'w') as f:
        f.write(content)
    print(f"  COPY .deepcode/settings.example.json: secrets replaced with placeholders")

def main():
    print("Building DEEPCODE sync payload in .dc-sync/")
    print()
    
    # 1. Copy config templates (sanitized)
    print("── Config Templates ──")
    sanitize_mcp_json()
    sanitize_settings_json()
    
    # 2. Copy rules (exclude github-token.md with secrets)
    print("── Rules & Policies ──")
    copy_path('.deepcode/rules')
    copy_path('.deepcode/policies')
    
    # 3. Copy DSH mcp-servers
    print("── DSH MCP Servers ──")
    copy_path('.dsh/mcp-servers')
    
    # 4. Copy skills
    print("── Skills (.deepcode) ──")
    copy_skills('.deepcode/skills', '.deepcode/skills')
    
    print("── Skills (.dsh) ──")
    copy_skills('.dsh/skills', '.dsh/skills')
    
    print("── Skills (.claude) ──")
    copy_path('.claude/skills')
    
    # 5. Copy Claude config
    print("── Claude Config ──")
    copy_path('.claude/settings.json')
    copy_path('.claude/proven-config.json')
    copy_path('.claude/agents')
    copy_path('.claude/commands')
    copy_path('.claude/helpers')
    
    # 6. Copy root config files
    print("── Root Config ──")
    for f in ['AGENTS.md', 'CLAUDE.md', 'DEPLOY.md']:
        copy_path(f)
    
    # 7. Copy scripts & commands (EXCLUDE tools/ — too large)
    print("── Scripts ──")
    copy_path('scripts')
    copy_path('commands')
    
    print()
    print("Done! Ready to commit in .dc-sync/")
    
    # Final safety scan: fail if any real secrets leaked through
    import subprocess
    result = subprocess.run(
        ['rg', '-n', '--no-messages',
         '-e', r'sk-[a-zA-Z0-9]{16,}',
         '-e', r'ghp_[a-zA-Z0-9]{20,}',
         '-e', r'AKIA[0-9A-Z]{16}',
         '-e', r'1a41dc66',
         ROOT],
        capture_output=True, text=True
    )
    if result.stdout.strip():
        leaked = result.stdout.strip().split('\n')
        print(f"\n⚠️  SECURITY WARNING: {len(leaked)} file(s) contain secrets!")
        for l in leaked:
            print(f"  {l}")
        print("  These must be excluded before committing!")
        sys.exit(1)
    else:
        print("✓ Secret scan passed — no real secrets found")

if __name__ == '__main__':
    main()