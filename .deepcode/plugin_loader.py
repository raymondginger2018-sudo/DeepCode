#!/usr/bin/env python3
"""
Plugin Loader — Claude Code 风格热加载插件系统
═══════════════════════════════════════════
支持三种加载方式:
  1. 本地目录:   {path}/plugin_dir/
  2. ZIP 文件:   {path}/plugin.zip
  3. URL 下载:   https://.../plugin.zip

插件结构 (最小):
  plugin.zip
  ├── SKILL.md          ← 必需: skill 定义 (YAML frontmatter + 提示词)
  ├── plugin.json       ← 可选: 插件元数据
  └── *.py / *.js       ← 可选: 工具脚本

用法:
  python plugin_loader.py install ./my-plugin.zip
  python plugin_loader.py install https://example.com/plugin.zip
  python plugin_loader.py list
  python plugin_loader.py remove my-plugin
"""

import json
import shutil
import tempfile
import zipfile
import urllib.request
import sys
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional

SKILLS_DIR = Path(__file__).parent / "skills"
REGISTRY_PATH = Path(__file__).parent / "plugin_registry.json"

DEFAULT_PLUGIN_JSON = {
    "name": "",
    "version": "1.0.0",
    "description": "",
    "author": "",
    "entry": "SKILL.md",
    "dependencies": [],
}


def load_registry() -> dict:
    if REGISTRY_PATH.exists():
        return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    return {"plugins": {}}


def save_registry(reg: dict):
    REGISTRY_PATH.write_text(json.dumps(reg, indent=2, ensure_ascii=False), encoding="utf-8")


def _validate_plugin_structure(plugin_dir: Path) -> Optional[str]:
    """验证插件目录结构, 返回错误信息或 None"""
    skill_md = plugin_dir / "SKILL.md"
    if not skill_md.exists():
        return f"Missing SKILL.md in {plugin_dir}"
    return None


def install_from_dir(source_dir: Path, plugin_name: Optional[str] = None) -> bool:
    """从本地目录安装插件"""
    source_dir = Path(source_dir).resolve()
    if not source_dir.is_dir():
        print(f"[plugin_loader] Not a directory: {source_dir}")
        return False

    # 读取 plugin.json 获取名称
    plugin_json_path = source_dir / "plugin.json"
    if plugin_json_path.exists():
        meta = json.loads(plugin_json_path.read_text(encoding="utf-8"))
        name = plugin_name or meta.get("name", source_dir.name)
    else:
        name = plugin_name or source_dir.name

    err = _validate_plugin_structure(source_dir)
    if err:
        print(f"[plugin_loader] Invalid plugin: {err}")
        return False

    # 复制到 skills 目录
    dest = SKILLS_DIR / name
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(source_dir, dest)

    # 更新注册表
    reg = load_registry()
    reg["plugins"][name] = {
        "source": str(source_dir),
        "type": "directory",
        "installed_at": datetime.now().isoformat(),
        "version": meta.get("version", "1.0.0") if plugin_json_path.exists() else "1.0.0",
    }
    save_registry(reg)

    print(f"[plugin_loader] OK: Installed: {name} (from directory)")
    return True


def install_from_zip(zip_path: Path) -> bool:
    """从 ZIP 文件安装插件"""
    zip_path = Path(zip_path).resolve()
    if not zip_path.exists():
        print(f"[plugin_loader] ZIP not found: {zip_path}")
        return False

    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(tmp)

        tmp_path = Path(tmp)
        # 查找 SKILL.md (支持嵌套一层目录)
        skill_md = None
        for p in tmp_path.rglob("SKILL.md"):
            skill_md = p
            break

        if not skill_md:
            print("[plugin_loader] ZIP must contain SKILL.md")
            return False

        plugin_dir = skill_md.parent
        name = _get_plugin_name(plugin_dir, zip_path.stem)
        return install_from_dir(plugin_dir, name)


def install_from_url(url: str, plugin_name: Optional[str] = None) -> bool:
    """从 URL 下载并安装插件"""
    print(f"[plugin_loader] Downloading: {url}")
    try:
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            req = urllib.request.Request(url, headers={"User-Agent": "DeepCode-PluginLoader/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                shutil.copyfileobj(resp, tmp)
            tmp_path = Path(tmp.name)

        result = install_from_zip(tmp_path)
        tmp_path.unlink(missing_ok=True)
        return result
    except Exception as e:
        print(f"[plugin_loader] Download failed: {e}")
        return False


def _get_plugin_name(plugin_dir: Path, fallback: str) -> str:
    """从 plugin.json 读取名称"""
    pj = plugin_dir / "plugin.json"
    if pj.exists():
        meta = json.loads(pj.read_text(encoding="utf-8"))
        return meta.get("name", fallback)
    return fallback


def list_plugins() -> List[dict]:
    """列出所有已安装插件"""
    reg = load_registry()
    plugins = []
    for name, info in reg.get("plugins", {}).items():
        installed = info.get("installed_at", "?")
        ptype = info.get("type", "?")
        version = info.get("version", "?")
        source = info.get("source", "?")
        plugins.append({
            "name": name,
            "version": version,
            "type": ptype,
            "installed": installed[:10],
            "source": source,
        })
    # Also scan skills dir for unregistered plugins
    if SKILLS_DIR.exists():
        registered = set(reg.get("plugins", {}).keys())
        for d in SKILLS_DIR.iterdir():
            if d.is_dir() and d.name not in registered:
                if (d / "SKILL.md").exists():
                    plugins.append({
                        "name": d.name,
                        "version": "—",
                        "type": "unregistered",
                        "installed": "—",
                        "source": str(d),
                    })
    return plugins


def remove_plugin(name: str) -> bool:
    """卸载插件"""
    dest = SKILLS_DIR / name
    if dest.exists():
        shutil.rmtree(dest)
    reg = load_registry()
    if name in reg.get("plugins", {}):
        del reg["plugins"][name]
        save_registry(reg)
    print(f"[plugin_loader] OK: Removed: {name}")
    return True


# ── CLI 入口 ──
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: plugin_loader.py <install|list|remove> [path/url/name]")
        sys.exit(1)

    action = sys.argv[1]

    if action == "install":
        target = sys.argv[2] if len(sys.argv) > 2 else None
        if not target:
            print("Usage: plugin_loader.py install <path|url>")
            sys.exit(1)
        if target.startswith("http://") or target.startswith("https://"):
            install_from_url(target)
        elif target.endswith(".zip"):
            install_from_zip(Path(target))
        else:
            install_from_dir(Path(target))

    elif action == "list":
        plugins = list_plugins()
        if not plugins:
            print("No plugins installed")
        else:
            print(f"{'Name':<25} {'Version':<10} {'Type':<12} {'Installed':<12}")
            print("-" * 60)
            for p in plugins:
                print(f"{p['name']:<25} {p['version']:<10} {p['type']:<12} {p['installed']:<12}")

    elif action == "remove":
        name = sys.argv[2] if len(sys.argv) > 2 else None
        if not name:
            print("Usage: plugin_loader.py remove <name>")
            sys.exit(1)
        remove_plugin(name)

    else:
        print(f"Unknown action: {action}")
        sys.exit(1)
