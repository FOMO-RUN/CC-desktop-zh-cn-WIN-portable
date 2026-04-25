#!/usr/bin/env python3
"""
Windows zh-CN portable patcher for CC Desktop.

The current Windows Claude Desktop is distributed as an MSIX package. Editing
the installed package in-place is brittle because Windows protects and signs
MSIX contents, so this script defaults to creating a patched runnable copy under
%LOCALAPPDATA%\\ClaudeZhCN\\Claude.

Examples:
    python cc_desktop_zh_cn_windows.py --launch
    python cc_desktop_zh_cn_windows.py --source C:\\path\\to\\Claude.msix --launch
    python cc_desktop_zh_cn_windows.py --source C:\\path\\to\\extracted\\app --launch
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any


LANG_CODE = "zh-CN"
ROOT = Path(__file__).resolve().parent
RESOURCES = ROOT / "resources"
COWORK_PORTABLE_ENV = "CZCOWORK"

FRONTEND_TRANSLATION = RESOURCES / "frontend-zh-CN.json"
DESKTOP_TRANSLATION = RESOURCES / "desktop-zh-CN.json"
STATSIG_TRANSLATION = RESOURCES / "statsig-zh-CN.json"

FRONTEND_I18N_REL = Path("resources/ion-dist/i18n")
FRONTEND_ASSETS_REL = Path("resources/ion-dist/assets/v1")
DESKTOP_RESOURCES_REL = Path("resources")

LATEST_MSIX_URL = "https://claude.ai/api/desktop/win32/x64/msix/latest/redirect"
DOWNLOAD_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/octet-stream,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

LANG_LIST_RE = re.compile(
    r'\["en-US","de-DE","fr-FR","ko-KR","ja-JP","es-419","es-ES","it-IT","hi-IN","pt-BR","id-ID"(.*?)\]'
)

COWORK_WINDOWS_STORE_TOKEN = b"process.windowsStore"
COWORK_PORTABLE_ENV_TOKEN = b"process.env.CZCOWORK"
if len(COWORK_WINDOWS_STORE_TOKEN) != len(COWORK_PORTABLE_ENV_TOKEN):
    raise RuntimeError("Cowork portable patch tokens must have the same length.")


def run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=check)


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def require_file(path: Path) -> None:
    if not path.exists():
        raise SystemExit(f"Missing required file: {path}")


def local_app_data() -> Path:
    value = os.environ.get("LOCALAPPDATA")
    if value:
        return Path(value)
    return Path.home() / "AppData/Local"


def roaming_app_data() -> Path:
    value = os.environ.get("APPDATA")
    if value:
        return Path(value)
    return Path.home() / "AppData/Roaming"


def default_target_dir() -> Path:
    return local_app_data() / "ClaudeZhCN" / "Claude"


def tool_root() -> Path:
    return local_app_data() / "ClaudeZhCN"


def launcher_path() -> Path:
    return tool_root() / "launch_claude_zh_cn.vbs"


def powershell_exe() -> str:
    return "powershell.exe"


def ps_single_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def normalize_version(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    parts = value.split(".")
    while len(parts) > 1 and parts[-1] == "0":
        parts.pop()
    return ".".join(parts)


def latest_msix_info() -> dict[str, str | None]:
    request = urllib.request.Request(LATEST_MSIX_URL, headers=DOWNLOAD_HEADERS)
    try:
        with urllib.request.urlopen(request) as response:
            url = response.geturl()
            size = response.headers.get("content-length")
    except Exception as exc:
        raise SystemExit(f"Could not check latest Claude version: {exc}") from exc

    match = re.search(r"/releases/win32/x64/([^/]+)/", url)
    version = match.group(1) if match else None
    return {"version": version, "url": url, "size": size}


def app_exe(app_dir: Path) -> Path | None:
    for name in ["Claude.exe", "claude.exe"]:
        exe = app_dir / name
        if exe.exists():
            return exe
    return None


def app_version(app_dir: Path) -> str | None:
    exe = app_exe(app_dir)
    if not exe:
        return None
    script = f"(Get-Item -LiteralPath {ps_single_quote(str(exe))}).VersionInfo.ProductVersion"
    result = run([powershell_exe(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], check=False)
    version = result.stdout.strip().splitlines()
    return version[0].strip() if version and version[0].strip() else None


def check_update(target_dir: Path) -> int:
    latest = latest_msix_info()
    local_version = app_version(target_dir.expanduser())
    latest_version = latest["version"]

    print(f"Latest Claude Desktop version: {latest_version or 'unknown'}")
    print(f"Local patched version: {local_version or 'not installed'}")

    if local_version and latest_version and normalize_version(local_version) == normalize_version(latest_version):
        print("Local patched Claude is already up to date.")
        return 0

    print("Update is available or local patched Claude is missing.")
    return 10


def find_appx_install_location() -> Path | None:
    script = (
        "Get-AppxPackage -Name Claude -ErrorAction SilentlyContinue | "
        "Sort-Object Version -Descending | "
        "Select-Object -First 1 -ExpandProperty InstallLocation"
    )
    try:
        result = run([powershell_exe(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], check=False)
    except OSError:
        return None

    for line in result.stdout.splitlines():
        value = line.strip()
        if not value:
            continue
        path = Path(value)
        if path.exists():
            return path
    return None


def normalize_app_dir(source: Path) -> Path:
    source = source.expanduser()
    if source.is_file() and source.name.lower() == "claude.exe":
        return source.parent
    if source.is_dir() and (source / "Claude.exe").exists():
        return source
    if source.is_dir() and (source / "claude.exe").exists():
        return source
    if source.is_dir() and (source / "app/Claude.exe").exists():
        return source / "app"
    if source.is_dir() and (source / "app/claude.exe").exists():
        return source / "app"
    if source.is_dir() and (source / FRONTEND_I18N_REL / "en-US.json").exists():
        return source
    raise SystemExit(f"Could not identify a Claude app directory from: {source}")


def find_source_app_dir() -> Path | None:
    appx_location = find_appx_install_location()
    if appx_location:
        try:
            return normalize_app_dir(appx_location)
        except SystemExit:
            pass

    candidates = [
        local_app_data() / "Programs/Claude",
        local_app_data() / "Programs/Claude/app",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Claude",
    ]
    for candidate in candidates:
        if candidate.exists():
            try:
                return normalize_app_dir(candidate)
            except SystemExit:
                continue
    return None


def user_data_paths() -> list[Path]:
    paths = [
        roaming_app_data() / "Claude",
        roaming_app_data() / "Claude-3p",
    ]

    packages = local_app_data() / "Packages"
    if packages.exists():
        package_patterns = ["Claude_*", "*Anthropic*Claude*"]
        for pattern in package_patterns:
            for package in packages.glob(pattern):
                paths.extend(
                    [
                        package / "LocalCache/Roaming/Claude",
                        package / "LocalCache/Roaming/Claude-3p",
                    ]
                )

    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path).lower()
        if key not in seen:
            unique.append(path)
            seen.add(key)
    return unique


def third_party_data_paths() -> list[Path]:
    paths = [roaming_app_data() / "Claude-3p"]

    packages = local_app_data() / "Packages"
    if packages.exists():
        package_patterns = ["Claude_*", "*Anthropic*Claude*"]
        for pattern in package_patterns:
            for package in packages.glob(pattern):
                paths.append(package / "LocalCache/Roaming/Claude-3p")

    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path).lower()
        if key not in seen:
            unique.append(path)
            seen.add(key)
    return unique


def primary_third_party_data_dir() -> Path:
    return third_party_data_paths()[0]


def third_party_config_library_dir(data_dir: Path | None = None) -> Path:
    return (data_dir or primary_third_party_data_dir()) / "configLibrary"


def third_party_config_meta_path(data_dir: Path | None = None) -> Path:
    return third_party_config_library_dir(data_dir) / "_meta.json"


def third_party_config_path(config_id: str, data_dir: Path | None = None) -> Path:
    return third_party_config_library_dir(data_dir) / f"{config_id}.json"


def claude_code_config_paths() -> list[Path]:
    claude_dir = Path.home() / ".claude"
    return [
        claude_dir / "settings.json",
        claude_dir / "settings.local.json",
        claude_dir / "config.json",
    ]


def format_size(size: int) -> str:
    value = float(size)
    for unit in ["B", "KB", "MB", "GB"]:
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def path_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    total = 0
    for child in path.rglob("*"):
        try:
            if child.is_file():
                total += child.stat().st_size
        except OSError:
            continue
    return total


def print_path_info(label: str, path: Path) -> None:
    if path.exists():
        print(f"[exists] {label}: {path} ({format_size(path_size(path))})")
    else:
        print(f"[missing] {label}: {path}")


def show_user_data(target_dir: Path) -> int:
    print("Claude zh-CN tool paths:")
    print_path_info("patched app", target_dir.expanduser())
    print_path_info("launcher", launcher_path())
    print_path_info("download cache", tool_root() / "downloads")
    print_path_info("user data backups", tool_root() / "user-data-backups")
    print()
    print("Claude user config/account data paths:")
    for path in user_data_paths():
        print_path_info("user data", path)
    print()
    print("Claude third-party inference data paths:")
    for path in third_party_data_paths():
        print_path_info("third-party data", path)
        print_path_info("third-party config library", third_party_config_library_dir(path))
    print()
    print("Config files:")
    for path in config_paths():
        print_path_info("config", path)
    print()
    print("Developer mode files:")
    for path in developer_settings_paths():
        print_path_info("developer settings", path)
    print()
    print("Claude Code local config files:")
    for path in claude_code_config_paths():
        print_path_info("Claude Code config", path)
    return 0


def shortcut_paths() -> dict[str, Path]:
    return {
        "desktop": Path.home() / "Desktop" / "Claude zh-CN.lnk",
        "start_menu": roaming_app_data() / "Microsoft/Windows/Start Menu/Programs/Claude zh-CN.lnk",
    }


def create_launcher(target_dir: Path) -> Path:
    exe = app_exe(target_dir.expanduser())
    if not exe:
        raise SystemExit(f"Cannot find patched Claude.exe in: {target_dir}")

    launcher = launcher_path()
    launcher.parent.mkdir(parents=True, exist_ok=True)
    exe_path = str(exe).replace('"', '""')
    working_dir = str(exe.parent).replace('"', '""')
    content = f'''Set shell = CreateObject("WScript.Shell")
Set env = shell.Environment("PROCESS")
env("{COWORK_PORTABLE_ENV}") = "1"
shell.CurrentDirectory = "{working_dir}"
shell.Run """" & "{exe_path}" & """", 1, False
'''
    launcher.write_text(content, encoding="utf-8")
    print(f"Created launcher: {launcher}")
    return launcher


def create_windows_shortcut(
    shortcut: Path,
    target: Path,
    description: str,
    *,
    arguments: str | None = None,
    working_directory: Path | None = None,
    icon: Path | None = None,
) -> None:
    shortcut.parent.mkdir(parents=True, exist_ok=True)
    script = f"""
$ErrorActionPreference = 'Stop'
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut({ps_single_quote(str(shortcut))})
$link.TargetPath = {ps_single_quote(str(target))}
$link.WorkingDirectory = {ps_single_quote(str(working_directory or target.parent))}
$link.IconLocation = {ps_single_quote(str(icon or target) + ',0')}
$link.Description = {ps_single_quote(description)}
{f"$link.Arguments = {ps_single_quote(arguments)}" if arguments else ""}
$link.Save()
"""
    result = run([powershell_exe(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], check=False)
    if result.returncode != 0:
        raise SystemExit(result.stdout.strip() or f"Failed to create shortcut: {shortcut}")


def create_shortcuts(target_dir: Path) -> int:
    exe = app_exe(target_dir.expanduser())
    if not exe:
        raise SystemExit(f"Cannot find patched Claude.exe in: {target_dir}")

    launcher = create_launcher(target_dir)
    wscript = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/wscript.exe"
    for label, shortcut in shortcut_paths().items():
        create_windows_shortcut(
            shortcut,
            wscript,
            "Claude Desktop zh-CN",
            arguments=f'"{launcher}"',
            working_directory=launcher.parent,
            icon=exe,
        )
        print(f"Created {label} shortcut: {shortcut}")
    return 0


def delete_if_exists(path: Path) -> bool:
    if not path.exists():
        return False
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    return True


def full_clean(target_dir: Path, yes: bool) -> int:
    targets = [
        ("patched app", target_dir.expanduser()),
        ("launcher", launcher_path()),
        ("download cache", tool_root() / "downloads"),
        ("user data backups", tool_root() / "user-data-backups"),
        ("desktop shortcut", shortcut_paths()["desktop"]),
        ("start menu shortcut", shortcut_paths()["start_menu"]),
    ]

    print("The following zh-CN tool files will be permanently deleted if they exist:")
    for label, path in targets:
        print_path_info(label, path)

    print()
    print("This does not delete Claude user config/account data. Use --clean-user-data for that.")
    if not yes:
        answer = input("Type DELETE to continue: ").strip()
        if answer != "DELETE":
            print("Cancelled.")
            return 0

    allowed_roots = [
        tool_root(),
        Path.home() / "Desktop",
        roaming_app_data() / "Microsoft/Windows/Start Menu/Programs",
    ]

    removed = 0
    for label, path in targets:
        if not path.exists():
            continue
        if not any(is_within(path, root) for root in allowed_roots):
            raise SystemExit(f"Refusing to delete path outside allowed roots: {path}")
        delete_if_exists(path)
        print(f"Deleted {label}: {path}")
        removed += 1

    print(f"Deleted {removed} item(s).")
    return 0


def is_within(path: Path, root: Path) -> bool:
    try:
        resolved = path.resolve()
        resolved_root = root.resolve()
    except OSError:
        resolved = path.absolute()
        resolved_root = root.absolute()
    return resolved == resolved_root or resolved_root in resolved.parents


def clean_user_data(yes: bool) -> int:
    existing = [p for p in user_data_paths() if p.exists()]
    if not existing:
        print("No Claude user config/account data paths were found.")
        return 0

    print("The following Claude user config/account data will be moved to a backup:")
    for path in existing:
        print(f"  {path} ({format_size(path_size(path))})")

    print()
    print("This will sign Claude out and reset local app state, but backups will be kept.")
    if not yes:
        answer = input("Type DELETE to continue: ").strip()
        if answer != "DELETE":
            print("Cancelled.")
            return 0

    allowed_roots = [roaming_app_data(), local_app_data() / "Packages"]
    backup_root = tool_root() / "user-data-backups" / dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_root.mkdir(parents=True, exist_ok=True)

    moved = 0
    for path in existing:
        if not any(is_within(path, root) for root in allowed_roots):
            raise SystemExit(f"Refusing to move path outside allowed app data roots: {path}")

        label = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(path).strip("\\/:"))
        destination = backup_root / label
        destination.parent.mkdir(parents=True, exist_ok=True)
        print(f"Moving {path} -> {destination}")
        shutil.move(str(path), str(destination))
        moved += 1

    print(f"Moved {moved} path(s) to: {backup_root}")
    print("Run Claude again to create a fresh user profile.")
    return 0


def download_latest_msix(download_dir: Path) -> Path:
    download_dir.mkdir(parents=True, exist_ok=True)
    target = download_dir / "Claude-latest.msix"
    tmp = target.with_suffix(target.suffix + ".tmp")
    print(f"Downloading latest Claude Desktop MSIX to: {target}")
    request = urllib.request.Request(LATEST_MSIX_URL, headers=DOWNLOAD_HEADERS)
    try:
        with urllib.request.urlopen(request) as response, tmp.open("wb") as f:
            shutil.copyfileobj(response, f)
    except Exception as exc:
        print(f"Python download failed: {exc}")
        print("Retrying download with PowerShell...")
        download_latest_msix_with_powershell(tmp)
    os.replace(tmp, target)
    return target


def download_latest_msix_with_powershell(target: Path) -> None:
    header_user_agent = DOWNLOAD_HEADERS["User-Agent"].replace("'", "''")
    script = f"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$headers = @{{ 'User-Agent' = '{header_user_agent}' }}
Invoke-WebRequest -Uri '{LATEST_MSIX_URL}' -OutFile '{target}' -Headers $headers
"""
    result = run([powershell_exe(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], check=False)
    if result.returncode != 0:
        raise SystemExit(result.stdout.strip() or "PowerShell download failed.")


def backup_existing_target(target: Path, dry_run: bool) -> Path | None:
    if not target.exists():
        return None
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = target.with_name(f"{target.name}.backup-before-zh-CN-{stamp}")
    if dry_run:
        print(f"[dry-run] Would move existing target {target} -> {backup}")
        return backup
    print(f"Backing up existing target: {backup}")
    shutil.move(str(target), str(backup))
    return backup


def copy_app_dir(source_app_dir: Path, target_dir: Path, dry_run: bool) -> None:
    backup_existing_target(target_dir, dry_run)
    if dry_run:
        print(f"[dry-run] Would copy {source_app_dir} -> {target_dir}")
        return
    print(f"Copying Claude app files: {source_app_dir} -> {target_dir}")
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_app_dir, target_dir)


def safe_extract_msix_app(msix: Path, target_dir: Path, dry_run: bool) -> None:
    backup_existing_target(target_dir, dry_run)
    if dry_run:
        print(f"[dry-run] Would extract app/ from {msix} -> {target_dir}")
        return

    print(f"Extracting app/ from MSIX: {msix} -> {target_dir}")
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    target_root = target_dir.resolve()
    with zipfile.ZipFile(msix) as archive:
        app_members = [m for m in archive.infolist() if m.filename.startswith("app/") and not m.is_dir()]
        if not app_members:
            raise SystemExit(f"MSIX does not contain app/ files: {msix}")

        for info in app_members:
            rel_posix = PurePosixPath(info.filename).relative_to("app")
            rel_path = Path(*rel_posix.parts)
            out_path = target_dir / rel_path
            resolved = out_path.resolve()
            if target_root not in [resolved, *resolved.parents]:
                raise SystemExit(f"Unsafe path in MSIX: {info.filename}")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as src, out_path.open("wb") as dst:
                shutil.copyfileobj(src, dst)


def remove_zst_sibling(path: Path) -> None:
    zst = path.with_name(path.name + ".zst")
    if zst.exists():
        zst.unlink()
        print(f"Removed stale compressed asset: {zst.name}")


def patch_language_whitelist(app_dir: Path) -> Path:
    assets_dir = app_dir / FRONTEND_ASSETS_REL
    candidates = sorted(assets_dir.glob("index-*.js"))
    if not candidates:
        raise SystemExit(f"Cannot find frontend index bundle in {assets_dir}")

    for path in candidates:
        text = path.read_text(encoding="utf-8")
        if '"zh-CN"' in text:
            remove_zst_sibling(path)
            print(f"Language whitelist already contains zh-CN: {path.name}")
            return path
        if LANG_LIST_RE.search(text):
            patched = LANG_LIST_RE.sub(
                '["en-US","de-DE","fr-FR","ko-KR","ja-JP","es-419","es-ES","it-IT","hi-IN","pt-BR","id-ID","zh-CN"]',
                text,
                count=1,
            )
            path.write_text(patched, encoding="utf-8")
            remove_zst_sibling(path)
            print(f"Patched language whitelist: {path.name}")
            return path

    raise SystemExit("Could not patch language whitelist. Claude's bundle format may have changed.")


def patch_hardcoded_frontend_strings(app_dir: Path) -> None:
    assets_dir = app_dir / FRONTEND_ASSETS_REL
    replacements = {
        '"New task"': '"新建任务"',
        '"New session"': '"新会话"',
        'label:"Cowork",ariaLabel:"Cowork"': 'label:"协作",ariaLabel:"协作"',
        'label:"Code",ariaLabel:"Code"': 'label:"代码",ariaLabel:"代码"',
        '"Projects"': '"项目"',
        '"Scheduled"': '"计划任务"',
        '"Customize"': '"自定义"',
        '"Drag to pin"': '"拖到此处固定"',
        '"Drop here"': '"拖到此处"',
        '"Let go"': '"松开"',
        '"Recents"': '"最近使用"',
        '"View all"': '"查看全部"',
    }
    patched_files = 0
    patched_strings = 0

    for path in sorted(assets_dir.glob("*.js")):
        text = path.read_text(encoding="utf-8")
        patched = text
        count = 0
        for source, target in replacements.items():
            occurrences = patched.count(source)
            if occurrences:
                patched = patched.replace(source, target)
                count += occurrences
        if patched != text:
            path.write_text(patched, encoding="utf-8")
            remove_zst_sibling(path)
            patched_files += 1
            patched_strings += count

    print(f"Patched hardcoded frontend strings: {patched_strings} replacements in {patched_files} files")


def merge_frontend_locale(app_dir: Path) -> tuple[int, int, int]:
    source = app_dir / FRONTEND_I18N_REL / "en-US.json"
    target = app_dir / FRONTEND_I18N_REL / "zh-CN.json"
    require_file(source)
    require_file(FRONTEND_TRANSLATION)

    en = load_json(source)
    zh_pack = load_json(FRONTEND_TRANSLATION)
    if not isinstance(en, dict) or not isinstance(zh_pack, dict):
        raise SystemExit("Unsupported frontend i18n JSON shape.")

    merged: dict[str, Any] = {}
    translated = 0
    fallback = 0
    for key, value in en.items():
        if key in zh_pack:
            merged[key] = zh_pack[key]
            if zh_pack[key] != value:
                translated += 1
        else:
            merged[key] = value
            fallback += 1

    save_json(target, merged)
    extra = len(set(zh_pack) - set(en))
    print(f"Installed frontend zh-CN: {translated} translated, {fallback} fallback, {extra} extra old keys ignored")
    return translated, fallback, extra


def install_desktop_locale(app_dir: Path) -> None:
    resources_dir = app_dir / DESKTOP_RESOURCES_REL
    require_file(DESKTOP_TRANSLATION)
    shutil.copy2(DESKTOP_TRANSLATION, resources_dir / "zh-CN.json")
    print("Installed desktop shell zh-CN resource")


def install_statsig_locale(app_dir: Path) -> None:
    statsig_dir = app_dir / FRONTEND_I18N_REL / "statsig"
    if not statsig_dir.exists():
        return
    target = statsig_dir / "zh-CN.json"
    if STATSIG_TRANSLATION.exists():
        shutil.copy2(STATSIG_TRANSLATION, target)
    elif (statsig_dir / "en-US.json").exists():
        shutil.copy2(statsig_dir / "en-US.json", target)
    print("Installed statsig zh-CN resource")


def config_paths() -> list[Path]:
    paths = [roaming_app_data() / "Claude/config.json"]
    packages = local_app_data() / "Packages"
    if packages.exists():
        for package in packages.glob("Claude_*"):
            paths.append(package / "LocalCache/Roaming/Claude/config.json")
        for package in packages.glob("*Anthropic*Claude*"):
            paths.append(package / "LocalCache/Roaming/Claude/config.json")

    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path).lower()
        if key not in seen:
            unique.append(path)
            seen.add(key)
    return unique


def developer_settings_paths() -> list[Path]:
    paths = [roaming_app_data() / "Claude/developer_settings.json"]
    packages = local_app_data() / "Packages"
    if packages.exists():
        for package in packages.glob("Claude_*"):
            paths.append(package / "LocalCache/Roaming/Claude/developer_settings.json")
        for package in packages.glob("*Anthropic*Claude*"):
            paths.append(package / "LocalCache/Roaming/Claude/developer_settings.json")

    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path).lower()
        if key not in seen:
            unique.append(path)
            seen.add(key)
    return unique


def backup_file(path: Path, reason: str) -> Path:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = f"{path.suffix}.bak-{reason}-{stamp}" if path.suffix else f".bak-{reason}-{stamp}"
    backup = path.with_suffix(suffix)
    shutil.copy2(path, backup)
    return backup


def load_json_dict(path: Path, *, backup_invalid: bool = False, label: str = "JSON") -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = load_json(path)
        if isinstance(data, dict):
            return data
        raise ValueError("top-level JSON value is not an object")
    except Exception as exc:
        if backup_invalid:
            backup = backup_file(path, "invalid")
            print(f"Existing {label} was not valid JSON; backed up to {backup}")
        else:
            print(f"Could not read {label}: {path} ({exc})")
        return {}


def mask_secret(value: str | None) -> str:
    if not value:
        return ""
    value = value.strip()
    if len(value) <= 8:
        return value[:2] + "***"
    return value[:4] + "***" + value[-4:]


def nonempty_string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def infer_gateway_auth_scheme(base_url: str, credential_name: str) -> str:
    try:
        host = urllib.parse.urlparse(base_url).hostname or ""
    except ValueError:
        host = ""
    if credential_name == "ANTHROPIC_API_KEY" and host.endswith("anthropic.com"):
        return "x-api-key"
    return "bearer"


def discover_local_claude_gateway_config() -> tuple[dict[str, Any] | None, list[str]]:
    messages: list[str] = []
    base_url: str | None = None
    base_source: str | None = None
    credential: str | None = None
    credential_name: str | None = None
    credential_source: str | None = None

    def take_base(value: Any, source: str) -> None:
        nonlocal base_url, base_source
        candidate = nonempty_string(value)
        if candidate and not base_url:
            base_url = candidate
            base_source = source

    def take_credential(value: Any, name: str, source: str) -> None:
        nonlocal credential, credential_name, credential_source
        candidate = nonempty_string(value)
        if candidate and not credential:
            credential = candidate
            credential_name = name
            credential_source = source

    for settings_path in [Path.home() / ".claude/settings.json", Path.home() / ".claude/settings.local.json"]:
        if not settings_path.exists():
            continue
        data = load_json_dict(settings_path, label="Claude Code settings")
        env = data.get("env")
        if not isinstance(env, dict):
            continue
        take_base(env.get("ANTHROPIC_BASE_URL"), f"{settings_path} env.ANTHROPIC_BASE_URL")
        take_credential(
            env.get("ANTHROPIC_AUTH_TOKEN"),
            "ANTHROPIC_AUTH_TOKEN",
            f"{settings_path} env.ANTHROPIC_AUTH_TOKEN",
        )
        take_credential(
            env.get("ANTHROPIC_API_KEY"),
            "ANTHROPIC_API_KEY",
            f"{settings_path} env.ANTHROPIC_API_KEY",
        )

    take_base(os.environ.get("ANTHROPIC_BASE_URL"), "environment ANTHROPIC_BASE_URL")
    take_credential(os.environ.get("ANTHROPIC_AUTH_TOKEN"), "ANTHROPIC_AUTH_TOKEN", "environment ANTHROPIC_AUTH_TOKEN")
    take_credential(os.environ.get("ANTHROPIC_API_KEY"), "ANTHROPIC_API_KEY", "environment ANTHROPIC_API_KEY")

    config_path = Path.home() / ".claude/config.json"
    if config_path.exists() and not credential:
        data = load_json_dict(config_path, label="Claude Code config")
        primary_api_key = nonempty_string(data.get("primaryApiKey"))
        if primary_api_key and len(primary_api_key) >= 8:
            take_credential(primary_api_key, "primaryApiKey", f"{config_path} primaryApiKey")

    if base_url:
        parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            messages.append(f"Found ANTHROPIC_BASE_URL, but it is not a valid http(s) URL: {base_source}")
            base_url = None
    else:
        messages.append("Missing ANTHROPIC_BASE_URL in ~/.claude/settings.json or environment variables.")

    if not credential:
        messages.append("Missing ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY in ~/.claude/settings.json or environment variables.")

    if not base_url or not credential or not credential_name:
        return None, messages

    return (
        {
            "base_url": base_url,
            "base_source": base_source,
            "credential": credential,
            "credential_name": credential_name,
            "credential_source": credential_source,
            "auth_scheme": infer_gateway_auth_scheme(base_url, credential_name),
        },
        messages,
    )


def third_party_write_targets() -> list[Path]:
    paths = third_party_data_paths()
    existing = [path for path in paths if path.exists() or third_party_config_library_dir(path).exists()]
    return existing or [primary_third_party_data_dir()]


def ensure_third_party_config_meta(data_dir: Path, dry_run: bool) -> tuple[str, Path]:
    library = third_party_config_library_dir(data_dir)
    meta_path = third_party_config_meta_path(data_dir)
    data = load_json_dict(meta_path, backup_invalid=True, label="Claude third-party config metadata")
    original = json.dumps(data, sort_keys=True, ensure_ascii=False)

    applied_id = nonempty_string(data.get("appliedId"))
    if not applied_id:
        applied_id = str(uuid.uuid4())

    entries = data.get("entries")
    if not isinstance(entries, list):
        entries = []

    normalized_entries: list[dict[str, Any]] = []
    has_applied = False
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        entry_id = nonempty_string(entry.get("id"))
        if not entry_id:
            continue
        name = nonempty_string(entry.get("name")) or "Default"
        normalized_entries.append({"id": entry_id, "name": name})
        if entry_id == applied_id:
            has_applied = True

    if not has_applied:
        normalized_entries.append({"id": applied_id, "name": "Default"})

    data["appliedId"] = applied_id
    data["entries"] = normalized_entries
    updated = json.dumps(data, sort_keys=True, ensure_ascii=False)

    if updated != original:
        if dry_run:
            print(f"[dry-run] Would update Claude third-party config metadata: {meta_path}")
        else:
            library.mkdir(parents=True, exist_ok=True)
            if meta_path.exists():
                backup = backup_file(meta_path, "before-third-party-config")
                print(f"Backed up Claude third-party config metadata: {backup}")
            save_json(meta_path, data)
            print(f"Updated Claude third-party config metadata: {meta_path}")

    return applied_id, third_party_config_path(applied_id, data_dir)


def apply_third_party_inference_config(dry_run: bool = False) -> int:
    discovered, messages = discover_local_claude_gateway_config()
    if not discovered:
        print("No local Claude Code gateway config was applied.")
        for message in messages:
            print(f"  {message}")
        print("Open Developer -> Configure Third-Party Inference and fill it manually, or add env values to ~/.claude/settings.json.")
        return 0

    print("Detected local Claude Code gateway config:")
    print(f"  Base URL: {discovered['base_url']}")
    print(f"  Credential: {discovered['credential_name']} = {mask_secret(discovered['credential'])}")
    print(f"  Auth scheme: {discovered['auth_scheme']}")

    for data_dir in third_party_write_targets():
        config_id, config_path = ensure_third_party_config_meta(data_dir, dry_run)
        current = load_json_dict(config_path, backup_invalid=True, label="Claude third-party config")
        updated = dict(current)
        updated.update(
            {
                "inferenceProvider": "gateway",
                "inferenceGatewayBaseUrl": discovered["base_url"],
                "inferenceGatewayApiKey": discovered["credential"],
                "inferenceGatewayAuthScheme": discovered["auth_scheme"],
                "disableDeploymentModeChooser": True,
            }
        )

        if updated == current:
            print(f"Claude third-party inference is already configured: {config_path}")
            continue

        if dry_run:
            print(f"[dry-run] Would apply Claude third-party inference config: {config_path}")
            continue

        if config_path.exists():
            backup = backup_file(config_path, "before-third-party-config")
            print(f"Backed up Claude third-party config: {backup}")
        save_json(config_path, updated)
        print(f"Applied Claude third-party inference config: {config_path} (id: {config_id})")

    return 0


def show_third_party_inference_config() -> int:
    discovered, messages = discover_local_claude_gateway_config()
    print("Local Claude Code gateway discovery:")
    if discovered:
        print(f"  Base URL: {discovered['base_url']}")
        print(f"  Credential: {discovered['credential_name']} = {mask_secret(discovered['credential'])}")
        print(f"  Auth scheme: {discovered['auth_scheme']}")
    else:
        print("  Not found.")
        for message in messages:
            print(f"  {message}")

    print()
    print("Claude Desktop third-party inference config:")
    for data_dir in third_party_data_paths():
        meta_path = third_party_config_meta_path(data_dir)
        print_path_info("third-party metadata", meta_path)
        meta = load_json_dict(meta_path, label="Claude third-party config metadata")
        applied_id = nonempty_string(meta.get("appliedId"))
        if not applied_id:
            continue
        config_path = third_party_config_path(applied_id, data_dir)
        print_path_info("applied third-party config", config_path)
        config = load_json_dict(config_path, label="Claude third-party config")
        if config:
            print(f"  inferenceProvider: {config.get('inferenceProvider') or 'not set'}")
            print(f"  inferenceGatewayBaseUrl: {config.get('inferenceGatewayBaseUrl') or 'not set'}")
            print(f"  inferenceGatewayApiKey: {mask_secret(nonempty_string(config.get('inferenceGatewayApiKey')))}")
            print(f"  inferenceGatewayAuthScheme: {config.get('inferenceGatewayAuthScheme') or 'not set'}")
            print(f"  disableDeploymentModeChooser: {config.get('disableDeploymentModeChooser')}")
    return 0


def asar_file_entries(header: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    entries: list[tuple[str, dict[str, Any]]] = []

    def walk(node: dict[str, Any], prefix: str = "") -> None:
        files = node.get("files")
        if isinstance(files, dict):
            for name, child in files.items():
                if isinstance(child, dict):
                    walk(child, f"{prefix}/{name}" if prefix else name)
            return
        if "offset" in node and "size" in node:
            entries.append((prefix, node))

    walk(header)
    return entries


def parse_asar(data: bytes) -> tuple[int, int, int, dict[str, Any]]:
    if len(data) < 16:
        raise ValueError("ASAR file is too small.")
    header_size = struct.unpack_from("<I", data, 4)[0]
    json_size = struct.unpack_from("<I", data, 12)[0]
    json_start = data.index(b'{"files"', 0, 64)
    json_end = json_start + json_size
    content_base = 8 + header_size
    header = json.loads(data[json_start:json_end].decode("utf-8"))
    if not isinstance(header, dict):
        raise ValueError("ASAR header is not a JSON object.")
    return json_start, json_end, content_base, header


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def asar_header_hash(data: bytes) -> str:
    json_start, json_end, _, _ = parse_asar(data)
    return sha256_hex(data[json_start:json_end])


def sha256_blocks(data: bytes, block_size: int) -> list[str]:
    if block_size <= 0:
        return [sha256_hex(data)]
    if not data:
        return [sha256_hex(data)]
    return [sha256_hex(data[index : index + block_size]) for index in range(0, len(data), block_size)]


def patch_asar_file_content_and_integrity(
    asar: Path,
    old_token: bytes,
    new_token: bytes,
) -> tuple[int, int, str, str]:
    data = bytearray(asar.read_bytes())
    json_start, json_end, content_base, header = parse_asar(bytes(data))
    old_header_hash = sha256_hex(bytes(data[json_start:json_end]))
    header_bytes = bytearray(data[json_start:json_end])

    patched_files = 0
    patched_tokens = 0
    for _, entry in asar_file_entries(header):
        try:
            offset = content_base + int(entry["offset"])
            size = int(entry["size"])
        except (KeyError, TypeError, ValueError):
            continue

        chunk = bytes(data[offset : offset + size])
        token_count = chunk.count(old_token)
        if token_count == 0:
            continue

        integrity = entry.get("integrity")
        if not isinstance(integrity, dict):
            raise SystemExit("Cannot patch ASAR because a target file has no integrity metadata.")
        old_hash = nonempty_string(integrity.get("hash"))
        if not old_hash:
            raise SystemExit("Cannot patch ASAR because a target file integrity hash is missing.")
        old_blocks = integrity.get("blocks")
        if not isinstance(old_blocks, list):
            old_blocks = []
        block_size = int(integrity.get("blockSize") or 4194304)

        patched_chunk = chunk.replace(old_token, new_token)
        data[offset : offset + size] = patched_chunk
        new_hash = sha256_hex(patched_chunk)
        new_blocks = sha256_blocks(patched_chunk, block_size)

        header_bytes = header_bytes.replace(old_hash.encode("ascii"), new_hash.encode("ascii"))
        for old_block, new_block in zip(old_blocks, new_blocks, strict=False):
            if isinstance(old_block, str):
                header_bytes = header_bytes.replace(old_block.encode("ascii"), new_block.encode("ascii"))

        patched_files += 1
        patched_tokens += token_count

    if patched_files:
        if len(header_bytes) != json_end - json_start:
            raise SystemExit("Refusing to write ASAR: integrity header size changed unexpectedly.")
        data[json_start:json_end] = header_bytes
        new_header_hash = sha256_hex(bytes(header_bytes))
        tmp = asar.with_suffix(asar.suffix + ".tmp")
        tmp.write_bytes(data)
        try:
            os.replace(tmp, asar)
        except PermissionError:
            try:
                asar.unlink()
                os.replace(tmp, asar)
            except Exception:
                if tmp.exists():
                    tmp.unlink()
                raise
    else:
        new_header_hash = old_header_hash

    return patched_files, patched_tokens, old_header_hash, new_header_hash


def backup_header_hashes(asar: Path) -> list[str]:
    hashes: list[str] = []
    for backup in sorted(asar.parent.glob(f"{asar.name}.bak-before-cowork-compat-*"), reverse=True):
        try:
            header_hash = asar_header_hash(backup.read_bytes())
        except Exception:
            continue
        if header_hash not in hashes:
            hashes.append(header_hash)
    return hashes


def patch_exe_asar_header_hash(app_dir: Path, expected_hash: str, old_hashes: list[str]) -> None:
    exe = app_exe(app_dir)
    if not exe:
        raise SystemExit(f"Cannot find Claude.exe in {app_dir}")

    data = exe.read_bytes()
    expected_token = expected_hash.encode("ascii")
    if expected_token in data:
        print(f"Claude.exe ASAR header hash is already current: {exe}")
        return

    for old_hash in old_hashes:
        old_token = old_hash.encode("ascii")
        if old_token not in data:
            continue

        backup = backup_file(exe, "before-cowork-compat")
        tmp = exe.with_suffix(exe.suffix + ".tmp")
        tmp.write_bytes(data.replace(old_token, expected_token, 1))
        try:
            os.replace(tmp, exe)
        except PermissionError:
            if tmp.exists():
                tmp.unlink()
            raise SystemExit(
                "Could not patch Claude.exe because Windows denied access. "
                "Close Claude completely, then run option 9 again."
            )
        print(f"Backed up Claude.exe: {backup}")
        print(f"Updated Claude.exe ASAR header hash: {exe}")
        return

    raise SystemExit(
        "Could not find the old ASAR header hash in Claude.exe. "
        "Rebuild the zh-CN copy from option 1, then run option 9 again if needed."
    )


def patch_cowork_portable_detection(app_dir: Path, dry_run: bool = False) -> int:
    asar = app_dir.expanduser() / "resources/app.asar"
    if not asar.exists():
        print(f"Claude app.asar was not found, skipping Cowork compatibility patch: {asar}")
        return 0

    data = asar.read_bytes()
    _, _, content_base, header = parse_asar(data)
    entries = asar_file_entries(header)
    token_count = 0
    portable_count = 0
    for _, entry in entries:
        try:
            offset = content_base + int(entry["offset"])
            size = int(entry["size"])
        except (KeyError, TypeError, ValueError):
            continue
        chunk = data[offset : offset + size]
        token_count += chunk.count(COWORK_WINDOWS_STORE_TOKEN)
        portable_count += chunk.count(COWORK_PORTABLE_ENV_TOKEN)

    if portable_count > 0 and token_count == 0:
        print(f"Cowork portable compatibility patch is already applied: {asar}")
        current_hash = asar_header_hash(data)
        patch_exe_asar_header_hash(app_dir, current_hash, backup_header_hashes(asar))
        create_launcher(app_dir)
        return 0

    if token_count == 0:
        print(f"Cowork MSIX detection token was not found, skipping patch: {asar}")
        create_launcher(app_dir)
        return 0

    if dry_run:
        print(f"[dry-run] Would patch Cowork MSIX detection in {asar} ({token_count} occurrence(s)).")
        return 0

    backup = backup_file(asar, "before-cowork-compat")
    try:
        patched_files, patched_tokens, old_header_hash, new_header_hash = patch_asar_file_content_and_integrity(
            asar,
            COWORK_WINDOWS_STORE_TOKEN,
            COWORK_PORTABLE_ENV_TOKEN,
        )
    except PermissionError:
        if not asar.exists() and backup.exists():
            shutil.copy2(backup, asar)
        raise SystemExit(
            "Could not patch app.asar because Windows denied access. "
            "Close Claude completely, then run option 9 again."
        )
    except Exception:
        if backup.exists():
            shutil.copy2(backup, asar)
        raise

    print(f"Backed up Claude app.asar: {backup}")
    print(
        f"Applied Cowork portable compatibility patch: {asar} "
        f"({patched_tokens} occurrence(s) in {patched_files} file(s))"
    )
    patch_exe_asar_header_hash(app_dir, new_header_hash, [old_header_hash, *backup_header_hashes(asar)])
    create_launcher(app_dir)
    return 0


def set_user_locale(dry_run: bool) -> None:
    for config in config_paths():
        if dry_run:
            print(f"[dry-run] Would set Claude config locale: {config}")
            continue

        data: dict[str, Any] = {}
        should_backup = False
        if config.exists():
            try:
                loaded = load_json(config)
                if not isinstance(loaded, dict):
                    raise ValueError("top-level JSON value is not an object")
                data = loaded
                if data.get("locale") == LANG_CODE:
                    print(f"Claude config locale is already {LANG_CODE}: {config}")
                    continue
                should_backup = True
            except Exception:
                backup = backup_file(config, "invalid")
                print(f"Existing config was not valid JSON; backed up to {backup}")
        if should_backup:
            backup = backup_file(config, "before-zh-CN")
            print(f"Backed up Claude config: {backup}")
        data["locale"] = LANG_CODE
        save_json(config, data)
        print(f"Set Claude config locale: {config}")


def enable_developer_mode(dry_run: bool) -> None:
    for settings in developer_settings_paths():
        if dry_run:
            print(f"[dry-run] Would enable Claude developer mode: {settings}")
            continue

        data: dict[str, Any] = {}
        should_backup = False
        if settings.exists():
            try:
                loaded = load_json(settings)
                if not isinstance(loaded, dict):
                    raise ValueError("top-level JSON value is not an object")
                data = loaded
                if data.get("allowDevTools") is True:
                    print(f"Claude developer mode is already enabled: {settings}")
                    continue
                should_backup = True
            except Exception:
                backup = backup_file(settings, "invalid")
                print(f"Existing developer settings were not valid JSON; backed up to {backup}")

        if should_backup:
            backup = backup_file(settings, "before-zh-CN")
            print(f"Backed up Claude developer settings: {backup}")
        data["allowDevTools"] = True
        save_json(settings, data)
        print(f"Enabled Claude developer mode: {settings}")


def apply_user_settings(target_dir: Path) -> int:
    set_user_locale(False)
    enable_developer_mode(False)
    apply_third_party_inference_config(False)
    patch_cowork_portable_detection(target_dir, False)
    try:
        create_shortcuts(target_dir)
    except SystemExit as exc:
        print(exc)
    return 0


def verify(app_dir: Path) -> None:
    frontend = app_dir / FRONTEND_I18N_REL / "zh-CN.json"
    data = load_json(frontend)
    values = [v for v in data.values() if isinstance(v, str)]
    chinese = sum(1 for v in values if re.search(r"[\u4e00-\u9fff]", v))
    print(f"Verified frontend zh-CN JSON: {chinese}/{len(values)} strings contain Chinese")

    desktop = app_dir / DESKTOP_RESOURCES_REL / "zh-CN.json"
    require_file(desktop)
    index_files = list((app_dir / FRONTEND_ASSETS_REL).glob("index-*.js"))
    if not any('"zh-CN"' in p.read_text(encoding="utf-8") for p in index_files):
        raise SystemExit("Verification failed: frontend language whitelist does not contain zh-CN")
    print("Verified frontend language whitelist contains zh-CN")


def launch(app_dir: Path) -> None:
    exe = app_exe(app_dir)
    if not exe:
        raise SystemExit(f"Cannot find Claude.exe in {app_dir}")
    print(f"Launching Claude: {exe}")
    env = os.environ.copy()
    env[COWORK_PORTABLE_ENV] = "1"
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        [str(exe)],
        cwd=str(app_dir),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=creationflags,
    )
    print("Claude was started in a separate process. This tool window can be closed or returned to the menu.")


def resolve_source(args: argparse.Namespace) -> Path:
    if args.source:
        return args.source.expanduser()

    if args.force_download:
        return download_latest_msix(local_app_data() / "ClaudeZhCN" / "downloads")

    app_dir = find_source_app_dir()
    if app_dir:
        return app_dir

    if args.download_msix:
        return download_latest_msix(local_app_data() / "ClaudeZhCN" / "downloads")

    raise SystemExit(
        "Claude Desktop was not found. Install Claude Desktop first, pass --source, "
        "or use --download-msix to build from the latest official MSIX."
    )


def prepare_app(args: argparse.Namespace) -> Path:
    source = resolve_source(args)
    source_was_explicit = args.source is not None

    if args.in_place:
        if source.suffix.lower() == ".msix":
            raise SystemExit("--in-place cannot be used with an MSIX file.")
        app_dir = normalize_app_dir(source)
        if args.dry_run:
            tmp_root = Path(tempfile.mkdtemp(prefix="claude-zh-cn-win-dry-run."))
            dry_target = tmp_root / "Claude"
            copy_app_dir(app_dir, dry_target, dry_run=False)
            return dry_target
        return app_dir

    target_dir = args.target_dir.expanduser()
    if args.dry_run:
        tmp_root = Path(tempfile.mkdtemp(prefix="claude-zh-cn-win-dry-run."))
        target_dir = tmp_root / "Claude"

    try:
        if source.suffix.lower() == ".msix":
            safe_extract_msix_app(source, target_dir, dry_run=False)
        else:
            copy_app_dir(normalize_app_dir(source), target_dir, dry_run=False)
    except OSError as exc:
        if args.download_msix and not source_was_explicit:
            print(f"Could not copy installed package files ({exc}). Falling back to latest official MSIX.")
            msix = download_latest_msix(local_app_data() / "ClaudeZhCN" / "downloads")
            safe_extract_msix_app(msix, target_dir, dry_run=False)
        else:
            raise
    return target_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Patch Claude Desktop for Windows with zh-CN language resources.")
    parser.add_argument("--source", type=Path, help="Claude app directory, package root, Claude.exe, or MSIX file")
    parser.add_argument("--app", type=Path, dest="source", help=argparse.SUPPRESS)
    parser.add_argument("--target-dir", type=Path, default=default_target_dir(), help="Patched runnable Claude directory")
    parser.add_argument("--download-msix", action="store_true", help="Download the latest official Windows MSIX if no source is found")
    parser.add_argument("--force-download", action="store_true", help="Always download the latest official Windows MSIX before patching")
    parser.add_argument("--check-update", action="store_true", help="Check whether the patched copy is already current")
    parser.add_argument("--show-user-data", action="store_true", help="Show Claude user config/account data paths")
    parser.add_argument("--show-third-party-inference", action="store_true", help="Show local Claude Code and Desktop third-party inference config")
    parser.add_argument("--apply-third-party-inference", action="store_true", help="Apply local Claude Code gateway settings to Claude Desktop third-party inference")
    parser.add_argument("--apply-cowork-compat", action="store_true", help="Patch portable Claude so Cowork does not require the MSIX launch path")
    parser.add_argument("--clean-user-data", action="store_true", help="Move Claude user config/account data to a timestamped backup")
    parser.add_argument("--create-shortcuts", action="store_true", help="Create Desktop and Start Menu shortcuts for the patched copy")
    parser.add_argument("--apply-user-settings", action="store_true", help="Set zh-CN locale, enable developer mode, and create shortcuts")
    parser.add_argument("--full-clean", action="store_true", help="Delete patched app, download cache, backups, and shortcuts")
    parser.add_argument("--yes", action="store_true", help="Skip confirmation prompts for destructive maintenance actions")
    parser.add_argument("--in-place", action="store_true", help="Patch the source app directory directly instead of creating a copy")
    parser.add_argument("--dry-run", action="store_true", help="Patch a temporary copy and do not update user config or target directory")
    parser.add_argument("--launch", action="store_true", help="Launch the patched Claude after installation")
    args = parser.parse_args()

    if args.check_update:
        return check_update(args.target_dir)
    if args.show_user_data:
        return show_user_data(args.target_dir)
    if args.show_third_party_inference:
        return show_third_party_inference_config()
    if args.apply_third_party_inference:
        return apply_third_party_inference_config(False)
    if args.apply_cowork_compat:
        return patch_cowork_portable_detection(args.target_dir, False)
    if args.clean_user_data:
        return clean_user_data(args.yes)
    if args.create_shortcuts:
        return create_shortcuts(args.target_dir)
    if args.apply_user_settings:
        return apply_user_settings(args.target_dir)
    if args.full_clean:
        return full_clean(args.target_dir, args.yes)

    require_file(FRONTEND_TRANSLATION)
    require_file(DESKTOP_TRANSLATION)

    app_dir = prepare_app(args)
    patch_language_whitelist(app_dir)
    patch_hardcoded_frontend_strings(app_dir)
    merge_frontend_locale(app_dir)
    install_desktop_locale(app_dir)
    install_statsig_locale(app_dir)
    patch_cowork_portable_detection(app_dir, args.dry_run)
    set_user_locale(args.dry_run)
    enable_developer_mode(args.dry_run)
    apply_third_party_inference_config(args.dry_run)
    verify(app_dir)
    create_shortcuts(app_dir)

    if args.launch and not args.dry_run:
        launch(app_dir)

    print(f"Done. Patched Claude is at: {app_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
