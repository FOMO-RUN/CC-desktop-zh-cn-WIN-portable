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
    for label, path in shortcut_paths().items():
        print_path_info(f"{label} shortcut", path)
    for label, path in claude_code_shortcut_paths().items():
        print_path_info(f"{label} shortcut", path)
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


def claude_code_shortcut_paths() -> dict[str, Path]:
    return {
        "desktop Claude Code": Path.home() / "Desktop" / "Claude Code.lnk",
        "start menu Claude Code": roaming_app_data() / "Microsoft/Windows/Start Menu/Programs/Claude Code.lnk",
    }


def claude_code_command() -> Path | None:
    command = shutil.which("claude")
    candidates = []
    if command:
        candidates.append(Path(command))
    candidates.extend(
        [
            Path.home() / ".local/bin/claude.exe",
            Path.home() / ".local/bin/claude.cmd",
            Path.home() / ".local/bin/claude.bat",
            roaming_app_data() / "npm/claude.cmd",
            roaming_app_data() / "npm/claude.exe",
            roaming_app_data() / "npm/claude.bat",
        ]
    )

    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        if candidate.exists() and candidate.suffix.lower() in {".exe", ".cmd", ".bat"}:
            return candidate
    return None


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

    claude_code = claude_code_command()
    if not claude_code:
        print("Claude Code command was not found, skipping Claude Code shortcuts.")
        return 0

    cmd = Path(os.environ.get("ComSpec") or (Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/cmd.exe"))
    for label, shortcut in claude_code_shortcut_paths().items():
        create_windows_shortcut(
            shortcut,
            cmd,
            "Claude Code",
            arguments=f'/k ""{claude_code}""',
            working_directory=Path.home(),
            icon=claude_code,
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
        ("desktop shortcut", shortcut_paths()["desktop"]),
        ("start menu shortcut", shortcut_paths()["start_menu"]),
        ("desktop Claude Code shortcut", claude_code_shortcut_paths()["desktop Claude Code"]),
        ("start menu Claude Code shortcut", claude_code_shortcut_paths()["start menu Claude Code"]),
    ]

    print("The following zh-CN tool files will be permanently deleted if they exist:")
    for label, path in targets:
        print_path_info(label, path)

    print()
    print("This does not delete Claude user config/account data or user-data-backups. Use --clean-user-data for account data.")
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
    backup = unique_backup_path(target.with_name(f"{target.name}.backup-before-zh-CN-{stamp}"))
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
        '"New session"': '"New session[新会话]"',
        '"新会话"': '"New session[新会话]"',
        'label:"Cowork",ariaLabel:"Cowork"': 'label:"Cowork[协作]",ariaLabel:"Cowork[协作]"',
        'label:"协作",ariaLabel:"协作"': 'label:"Cowork[协作]",ariaLabel:"Cowork[协作]"',
        'label:"Code",ariaLabel:"Code"': 'label:"Code[代码]",ariaLabel:"Code[代码]"',
        'label:"代码",ariaLabel:"代码"': 'label:"Code[代码]",ariaLabel:"Code[代码]"',
        'label:"Cowork"},code:{mode:"code",icon:"Code",label:"Code"': 'label:"Cowork[协作]"},code:{mode:"code",icon:"Code",label:"Code[代码]"',
        '"Projects"': '"项目"',
        '"Scheduled"': '"计划任务"',
        '"Customize"': '"自定义"',
        '"Drag to pin"': '"拖到此处固定"',
        '"Drop here"': '"拖到此处"',
        '"Let go"': '"松开"',
        '"Recents"': '"最近使用"',
        '"View all"': '"查看全部"',
        '[["active","Active"],["archived","Archived"],["all","All"]]': '[["active","活跃"],["archived","已归档"],["all","全部"]]',
        'ei="Local",si="Cloud",ti="Remote Control",ni="All"': 'ei="本地",si="云端",ti="远程控制",ni="全部"',
        'ai=[["1","1d"],["3","3d"],["7","7d"],["30","30d"],["0","All"]]': 'ai=[["1","1天"],["3","3天"],["7","7天"],["30","30天"],["0","全部"]]',
        '[["date","Date"],..."code"===e?[["project","Project"]]:[],..."code"===e&&s?[["state","State"]]:[],["none","None"]]': '[["date","日期"],..."code"===e?[["project","项目"]]:[],..."code"===e&&s?[["state","状态"]]:[],["none","不分组"]]',
        'aria-label":M?"Filter (active)":"Filter"': 'aria-label":M?"筛选（已启用）":"筛选"',
        'label:"Status",options:Zr': 'label:"状态",options:Zr',
        'label:"Environment",options:C': 'label:"环境",options:C',
        'label:"Last activity",options:ai': 'label:"最后活动",options:ai',
        'label:"Group by",options:S': 'label:"分组",options:S',
        'children:"Clear filters"': 'children:"清除筛选"',
        '0===e.length?"All":1===e.length': '0===e.length?"全部":1===e.length',
        '`${e.length} selected`': '`${e.length} 个已选`',
        'children:"Project"}),t.jsx("span",{className:je("shrink-0 text-footnote max-w-[100px] truncate",r?"text-accent-100":"text-t6"),children:c})': 'children:"项目"}),t.jsx("span",{className:je("shrink-0 text-footnote max-w-[100px] truncate",r?"text-accent-100":"text-t6"),children:c})',
        'children:"All projects"}),a.map': 'children:"所有项目"}),a.map',
        'connection:{title:"Connection",description:"Choose where Claude Desktop sends inference requests."}': 'connection:{title:"连接",description:"选择 Claude Desktop 发送推理请求的位置。"}',
        'sandbox:{title:"Sandbox & workspace"}': 'sandbox:{title:"沙盒与工作区"}',
        'connectors:{title:"Connectors & extensions"}': 'connectors:{title:"连接器与扩展"}',
        'telemetry:{title:"Telemetry & updates"': 'telemetry:{title:"遥测与更新"',
        'limits:{title:"Usage limits"}': 'limits:{title:"使用限制"}',
        'plugins:{title:"Plugins & skills"': 'plugins:{title:"插件与技能"',
        'egress:{title:"Egress Requirements"': 'egress:{title:"出站网络要求"',
        'source:{title:"Source"}': 'source:{title:"来源"}',
        'banner:"Prompts, completions, and your data are never sent to Anthropic — telemetry covers crash and usage signals only."': 'banner:"提示词、补全内容和你的数据不会发送给 Anthropic；遥测只包含崩溃和使用情况信号。"',
        'banner:"Plugins and skills aren\'t set in this configuration. Mount plugin bundles to the folder below using your device-management tool and Cowork will load them at launch."': 'banner:"插件和技能不在此配置中直接设置。请用设备管理工具把插件包挂载到下面的文件夹，Cowork 会在启动时加载。"',
        'caption:"Drop plugin folders here. Read-only to the app."': 'caption:"将插件文件夹放在这里。应用内只读。"',
        'description:"Hosts your network firewall must allow, derived from your current settings. This list is read-only and updates as you make changes. Traffic is HTTPS on port 443 unless a custom port is specified (OTLP, gateway, or MCP server URLs)."': 'description:"根据当前设置推导出的网络防火墙放行主机列表。此列表只读，并会随配置变化更新。除非 OTLP、网关[gateway] 或 MCP[模型上下文协议] 服务器 URL 指定了自定义端口，否则流量使用 443 端口的 HTTPS。"',
        'group:"Updates"': 'group:"更新"',
        'group:"Identity & models"': 'group:"身份与模型"',
        'group:"Bootstrap config URL"': 'group:"引导配置 URL"',
        'group:"Extensions"': 'group:"扩展"',
        'group:"MCP servers"': 'group:"MCP[模型上下文协议] 服务器"',
        'group:"Anthropic telemetry"': 'group:"Anthropic 遥测"',
        'title:"Allow desktop extensions"': 'title:"允许桌面扩展"',
        'title:"Show extension directory"': 'title:"显示扩展目录"',
        'title:"Require signed extensions"': 'title:"要求扩展签名"',
        'title:"Allow user-added MCP servers"': 'title:"允许用户添加 MCP[模型上下文协议] 服务器"',
        'title:"Allow Claude Code tab"': 'title:"允许 Claude Code 标签页"',
        'title:"Secure VM features"': 'title:"安全 VM 功能"',
        'title:"Require full VM sandbox"': 'title:"强制完整 VM 沙盒"',
        'title:"Allowed egress hosts"': 'title:"允许出站主机"',
        'title:"OpenTelemetry collector endpoint"': 'title:"OpenTelemetry 收集器端点"',
        'title:"OpenTelemetry exporter protocol"': 'title:"OpenTelemetry 导出协议"',
        'title:"OpenTelemetry exporter headers"': 'title:"OpenTelemetry 导出请求头"',
        'title:"Auto-update enforcement window"': 'title:"自动更新强制窗口"',
        'title:"Block auto-updates"': 'title:"阻止自动更新"',
        'title:"Skip login-mode chooser"': 'title:"跳过登录模式选择"',
        'title:"Required organization"': 'title:"限定组织"',
        'title:"Inference provider"': 'title:"推理提供方"',
        'title:"Gateway base URL"': 'title:"网关基础 URL"',
        'title:"Gateway API key"': 'title:"网关 API 密钥"',
        'title:"Gateway auth scheme"': 'title:"网关认证方式"',
        'title:"Gateway extra headers"': 'title:"网关额外请求头"',
        'title:"GCP project ID"': 'title:"GCP 项目 ID"',
        'title:"GCP region"': 'title:"GCP 区域"',
        'title:"GCP credentials file path"': 'title:"GCP 凭据文件路径"',
        'title:"Vertex OAuth client ID"': 'title:"Vertex OAuth 客户端 ID"',
        'title:"Vertex OAuth client secret"': 'title:"Vertex OAuth 客户端密钥"',
        'title:"Vertex OAuth scopes"': 'title:"Vertex OAuth 权限范围"',
        'title:"Vertex AI base URL"': 'title:"Vertex AI 基础 URL"',
        'title:"AWS region"': 'title:"AWS 区域"',
        'title:"AWS bearer token"': 'title:"AWS Bearer[令牌认证] 访问令牌"',
        'title:"Bedrock base URL"': 'title:"Bedrock 基础 URL"',
        'title:"AWS profile name"': 'title:"AWS 配置档名称"',
        'title:"AWS config directory"': 'title:"AWS 配置目录"',
        'title:"Azure AI Foundry resource name"': 'title:"Azure AI Foundry 资源名称"',
        'title:"Azure AI Foundry API key"': 'title:"Azure AI Foundry API 密钥"',
        'title:"Model list"': 'title:"模型列表"',
        'title:"Organization UUID"': 'title:"组织 UUID"',
        'title:"Block essential telemetry"': 'title:"阻止必要遥测"',
        'title:"Block nonessential telemetry"': 'title:"阻止非必要遥测"',
        'title:"Block nonessential services"': 'title:"阻止非必要服务"',
        'title:"Managed MCP servers"': 'title:"托管 MCP[模型上下文协议] 服务器"',
        'title:"Disabled built-in tools"': 'title:"停用内置工具"',
        'title:"Allowed workspace folders"': 'title:"允许的工作区文件夹"',
        'title:"Credential helper script"': 'title:"凭据辅助脚本"',
        'title:"Credential helper TTL"': 'title:"凭据辅助缓存时间"',
        'title:"Use bootstrap config"': 'title:"使用引导配置"',
        'title:"Bootstrap config URL"': 'title:"引导配置 URL"',
        'title:"Bootstrap OIDC parameters"': 'title:"引导 OIDC 参数"',
        'title:"Max tokens per window"': 'title:"每个窗口最大词元数[token]"',
        'title:"Token cap window"': 'title:"词元[token]上限窗口"',
        'title:"每个窗口最大令牌数"': 'title:"每个窗口最大词元数[token]"',
        'title:"令牌上限窗口"': 'title:"词元[token]上限窗口"',
        'description:"Permit users to install local desktop extensions (.dxt/.mcpb)."': 'description:"允许用户安装本地桌面扩展（.dxt/.mcpb）。"',
        'description:"Show the Anthropic extension directory in the connectors UI."': 'description:"在连接器界面中显示 Anthropic 扩展目录。"',
        'description:"Reject desktop extensions that are not signed by a trusted publisher."': 'description:"拒绝未由受信任发布者签名的桌面扩展。"',
        'description:"Permit users to add their own local (stdio) MCP servers via Developer settings. HTTP/SSE servers are managed separately. When false, only servers from the Managed MCP servers list and org-provisioned plugins are available."': 'description:"允许用户通过开发者设置添加自己的本地（stdio）MCP[模型上下文协议] 服务器。HTTP/SSE 服务器会单独管理。关闭时，只有“托管 MCP[模型上下文协议] 服务器”列表和组织预置插件中的服务器可用。"',
        'description:"Show the Code tab (terminal-based coding sessions). Sessions run on the host, not inside the VM."': 'description:"显示 Code 标签页（基于终端的编码会话）。会话在主机上运行，而不是在 VM 内运行。"',
        'description:"Forces the agent loop, file/web tools, and plugin-bundled MCPs to run inside the VM, disabling host-loop mode."': 'description:"强制代理循环、文件/网页工具和插件内置 MCP[模型上下文协议] 在 VM 内运行，并停用主机循环模式。"',
        'description:"Base URL of an OpenTelemetry collector. When set, Cowork sessions export logs and metrics (prompts, tool calls, token counts) to this endpoint via OTLP. The endpoint host is automatically added to the session network allowlist."': 'description:"OpenTelemetry 收集器的基础 URL。设置后，Cowork 会话会通过 OTLP 将日志和指标（提示词、工具调用、词元[token]计数）导出到此端点。该端点主机会自动加入会话网络允许列表。"',
        'description:"OpenTelemetry 收集器的基础 URL。设置后，Cowork 会话会通过 OTLP 将日志和指标（提示词、工具调用、令牌计数）导出到此端点。该端点主机会自动加入会话网络允许列表。"': 'description:"OpenTelemetry 收集器的基础 URL。设置后，Cowork 会话会通过 OTLP 将日志和指标（提示词、工具调用、词元[token]计数）导出到此端点。该端点主机会自动加入会话网络允许列表。"',
        'description:"OTLP wire protocol used to reach the collector. Defaults to http/protobuf when otlpEndpoint is set."': 'description:"连接收集器所用的 OTLP 传输协议。设置 otlpEndpoint 时默认使用 http/protobuf。"',
        'description:"Headers sent with every OTLP request, as comma-separated key=value pairs (the standard OTEL_EXPORTER_OTLP_HEADERS format)."': 'description:"每个 OTLP 请求都会发送的请求头，以逗号分隔的 key=value 形式填写（标准 OTEL_EXPORTER_OTLP_HEADERS 格式）。"',
        'description:"When set, forces a pending update to install after this many hours regardless of user activity. When unset, the app uses a 72-hour window but defers installation while the user is active."': 'description:"设置后，待安装更新会在指定小时数后强制安装，不再考虑用户是否正在使用。未设置时，应用使用 72 小时窗口，并会在用户活跃时延后安装。"',
        'description:"Blocks the app from checking for and downloading updates from Anthropic. The app will stay on its installed version until updated by other means."': 'description:"阻止应用检查和下载来自 Anthropic 的更新。除非通过其他方式更新，否则应用会停留在当前安装版本。"',
        'description:"Skips the first-launch screen that asks the user to choose between Anthropic sign-in and the organization-managed provider. The app goes straight to the mode implied by this configuration (third-party when inferenceProvider is set), overriding any earlier user choice."': 'description:"跳过首次启动时让用户在 Anthropic 登录和组织托管提供方之间选择的页面。应用会直接进入此配置指定的模式（设置 inferenceProvider 时为第三方模式），并覆盖此前的用户选择。"',
        'description:"Restricts login to specific org UUID(s). Single UUID string or JSON array."': 'description:"将登录限制到指定组织 UUID。可填写单个 UUID 字符串或 JSON 数组。"',
        'description:"Full URL of the inference gateway endpoint."': 'description:"推理网关端点的完整 URL。"',
        'description:"Selects the inference backend. Setting this key activates third-party mode."': 'description:"选择推理后端。设置此项会启用第三方模式。"',
        'description:"How to send the gateway credential. \'bearer\' (default) sends Authorization: Bearer. Set \'x-api-key\' only if your gateway requires the x-api-key header instead (e.g. api.anthropic.com). Set \'sso\' to obtain the credential via the gateway\'s own browser-based sign-in (RFC 8414 discovery at `<inferenceGatewayBaseUrl>/.well-known/oauth-authorization-server` + RFC 8628 device-code grant); inferenceGatewayApiKey and inferenceCredentialHelper are not required."': 'description:"网关[gateway]凭据的发送方式。\'bearer\'（默认）会发送 Authorization: Bearer[令牌认证]。只有当网关[gateway]要求使用 x-api-key 请求头时才设置为 \'x-api-key\'（例如 api.anthropic.com）。设置为 \'sso\' 时，会通过网关[gateway]自己的浏览器登录获取凭据（RFC 8414 发现 `<inferenceGatewayBaseUrl>/.well-known/oauth-authorization-server` + RFC 8628 设备码授权）；无需 inferenceGatewayApiKey 和 inferenceCredentialHelper。"',
        'description:"Extra HTTP headers sent on every inference request. JSON array of \'Name: Value\' strings."': 'description:"每次推理请求都会发送的额外 HTTP 请求头。格式为由 \'Name: Value\' 字符串组成的 JSON 数组。"',
        'description:"GCP region for the Vertex AI endpoint."': 'description:"Vertex AI 端点所在的 GCP 区域。"',
        'description:"Absolute path to a service-account JSON or ADC file. No tilde or environment-variable expansion."': 'description:"服务账号 JSON 或 ADC 文件的绝对路径。不支持波浪号或环境变量展开。"',
        'description:"Client ID of a Desktop-app OAuth client created in your GCP project (APIs & Services → Credentials). When set together with the client secret, the app runs Sign in with Google and stores the resulting refresh token encrypted; `inferenceVertexCredentialsFile` is not needed."': 'description:"在 GCP 项目中创建的桌面应用 OAuth[开放授权] 客户端 ID（APIs & Services → Credentials）。与客户端密钥一起设置后，应用会运行“使用 Google 登录”，并加密保存得到的刷新令牌；不再需要 `inferenceVertexCredentialsFile`。"',
        'description:"Client secret for the Desktop-app OAuth client. Not confidential for installed apps per Google\'s docs — PKCE protects the flow."': 'description:"桌面应用 OAuth[开放授权] 客户端密钥。根据 Google 文档，已安装应用中的该密钥并非机密；PKCE 会保护登录流程。"',
        'description:"Space-separated OAuth scopes for the Google sign-in flow. Defaults to `openid email https://www.googleapis.com/auth/cloud-platform`. Narrow this if your Workspace\'s Context-Aware Access or reauth policy restricts `cloud-platform`."': 'description:"Google 登录流程使用的 OAuth[开放授权] 权限范围，用空格分隔。默认是 `openid email https://www.googleapis.com/auth/cloud-platform`。如果你的 Workspace 上下文感知访问或重新认证策略限制了 `cloud-platform`，请收窄此范围。"',
        'description:"Override the Vertex inference endpoint (e.g. a Private Service Connect address). Leave unset to use the public regional endpoint."': 'description:"覆盖 Vertex 推理端点（例如 Private Service Connect 地址）。留空则使用公开区域端点。"',
        'description:"AWS region for the Bedrock runtime endpoint."': 'description:"Bedrock 运行时端点所在的 AWS 区域。"',
        'description:"Override the Bedrock inference endpoint (e.g. a VPC interface endpoint or LLM gateway). Leave unset to use the public regional endpoint."': 'description:"覆盖 Bedrock 推理端点（例如 VPC 接口端点或 LLM 网关）。留空则使用公开区域端点。"',
        'description:"AWS named profile to use (from the AWS config/credentials files). Ignored when inferenceBedrockBearerToken is set."': 'description:"要使用的 AWS 命名配置档（来自 AWS config/credentials 文件）。设置 inferenceBedrockBearerToken 时会忽略此项。"',
        'description:"Absolute path to the directory containing AWS config and credentials files. Optional — defaults to the user\'s ~/.aws when inferenceBedrockBearerToken is not set. Copied into the sandbox at session start so the named profile can be resolved."': 'description:"包含 AWS config 和 credentials 文件的目录绝对路径。可选；未设置 inferenceBedrockBearerToken 时默认使用用户的 ~/.aws。会在会话开始时复制到沙盒内，以便解析命名配置档。"',
        'description:"Azure AI Foundry resource name used to construct the endpoint URL."': 'description:"用于构造端点 URL 的 Azure AI Foundry 资源名称。"',
        'description:"Stable identifier for this deployment, used to scope local storage and telemetry. Must be a UUID."': 'description:"此部署的稳定标识符，用于限定本地存储和遥测范围。必须是 UUID。"',
        'description:"Blocks crash and error reports (stack traces, app state at failure, device/OS info) and performance timing data sent to Anthropic. Used to investigate bugs and monitor responsiveness."': 'description:"阻止发送给 Anthropic 的崩溃和错误报告（堆栈跟踪、失败时应用状态、设备/系统信息）以及性能计时数据。这些数据用于排查问题和监控响应速度。"',
        'description:"Blocks product-usage analytics sent to Anthropic — feature usage, navigation patterns, UI actions."': 'description:"阻止发送给 Anthropic 的产品使用分析数据，包括功能使用、导航模式和 UI 操作。"',
        'description:"Blocks connector favicons (fetched from a third-party favicon service — leaks MCP hostnames) and the artifact-preview sandbox iframe. Connectors fall back to letter icons; artifacts do not render."': 'description:"阻止连接器网站图标（来自第三方 favicon 服务，可能泄露 MCP 主机名）和 Artifact 预览沙盒 iframe。连接器会退回到字母图标，Artifact 将不会渲染。"',
        'description:"JSON array of absolute paths the user may attach as workspace folders. A leading ~ expands to the per-user home directory. Unset means unrestricted."': 'description:"用户可作为工作区文件夹附加的绝对路径 JSON 数组。开头的 ~ 会展开为对应用户的主目录。未设置表示不限制。"',
        'description:"Absolute path to an executable that prints the inference credential to stdout. When set, the static inferenceGatewayApiKey / inferenceFoundryApiKey is optional."': 'description:"会将推理凭据输出到 stdout 的可执行文件绝对路径。设置后，静态 inferenceGatewayApiKey / inferenceFoundryApiKey 可不填。"',
        'description:"Helper output is cached for this many seconds. Default 3600. Re-runs at the next session start after expiry."': 'description:"辅助脚本输出会缓存指定秒数。默认 3600 秒。过期后会在下一次会话启动时重新运行。"',
        'description:"When set, the app fetches `bootstrapUrl` at launch and applies the response as a config overlay. When unset, `bootstrapUrl` is stored but not fetched."': 'description:"设置后，应用会在启动时获取 `bootstrapUrl`，并将响应作为配置覆盖层应用。未设置时，只保存 `bootstrapUrl`，不会获取。"',
        'description:"HTTPS endpoint fetched at app launch. The JSON response body overrides per-user provider config (project ID, region, base URL, model list, credential, OTLP endpoint) for the current user."': 'description:"应用启动时获取的 HTTPS 端点。JSON 响应体会覆盖当前用户的提供方配置（项目 ID、区域、基础 URL、模型列表、凭据、OTLP 端点）。"',
        'description:"JSON object: `clientId` (required), and either `issuer` (https URL — endpoints discovered via /.well-known/openid-configuration) or both `authorizationUrl` and `tokenUrl`. Optional: `scopes` (space-separated string), `redirectPort` (pin the loopback callback port for IdPs that require an exact redirect URI). When set, the app runs an authorization-code-with-PKCE flow in the system browser and sends the resulting access token as a Bearer header on the bootstrap request. When unset, the bootstrap request is unauthenticated."': 'description:"JSON 对象：`clientId`（必填），以及 `issuer`（https URL，通过 /.well-known/openid-configuration 发现端点）或同时填写 `authorizationUrl` 与 `tokenUrl`。可选：`scopes`（空格分隔字符串）、`redirectPort`（为要求精确回调 URI 的 IdP 固定 loopback 回调端口）。设置后，应用会在系统浏览器中运行带 PKCE 的授权码流程，并在引导请求中以 Bearer 请求头发送得到的访问令牌。未设置时，引导请求不带认证。"',
        'description:"Total input+output tokens permitted per window before further messages are refused. Unset = no cap."': 'description:"每个窗口允许的输入+输出总词元[token]数，超过后会拒绝后续消息。未设置表示无上限。"',
        'description:"Tumbling window length for the token cap. Max 720 hours (30 days). The counter resets at the end of each window."': 'description:"词元[token]上限的滚动窗口长度。最大 720 小时（30 天）。计数器会在每个窗口结束时重置。"',
        'description:"每个窗口允许的输入+输出总令牌数，超过后会拒绝后续消息。未设置表示无上限。"': 'description:"每个窗口允许的输入+输出总词元[token]数，超过后会拒绝后续消息。未设置表示无上限。"',
        'description:"令牌上限的滚动窗口长度。最大 720 小时（30 天）。计数器会在每个窗口结束时重置。"': 'description:"词元[token]上限的滚动窗口长度。最大 720 小时（30 天）。计数器会在每个窗口结束时重置。"',
        'hint:"HTTPS endpoint that returns a per-user JSON config overlay. Values from the response override local settings and become read-only."': 'hint:"返回每位用户 JSON 配置覆盖层的 HTTPS 端点。响应中的值会覆盖本地设置并变为只读。"',
        'hint:"JSON: clientId + issuer (or authorizationUrl + tokenUrl). When set, the bootstrap request sends a Bearer token from a browser sign-in."': 'hint:"JSON：clientId + issuer（或 authorizationUrl + tokenUrl）。设置后，引导请求会发送浏览器登录获得的 Bearer 令牌。"',
        'hint:"Fetch and apply the URL above at launch. While off, the URL is saved but ignored."': 'hint:"启动时获取并应用上方 URL。关闭时会保存 URL，但不会使用。"',
        'hint:"Stop Cowork from fetching updates. You\'ll need to push new versions yourself."': 'hint:"阻止 Cowork 获取更新。你需要自行分发新版本。"',
        'hint:"Hours before a downloaded update force-installs. Blank = 72-hour default."': 'hint:"已下载更新在多少小时后强制安装。留空表示默认 72 小时。"',
        'hint:"Where Cowork sends OpenTelemetry logs and metrics. Leave blank to disable."': 'hint:"Cowork 发送 OpenTelemetry 日志和指标的位置。留空表示停用。"',
        'hint:"grpc or http/protobuf."': 'hint:"grpc 或 http/protobuf。"',
        'hint:"Optional auth headers for the collector."': 'hint:"发送给收集器的可选认证请求头。"',
        'hint:"Per-user soft cap, counted client-side over the duration below. Not a server-enforced quota."': 'hint:"按用户设置的软上限，由客户端按下方时长统计。不是服务器强制配额。"',
        'reason:"The default host-native mode starts faster and works behind restricted networks. Shell commands run inside the VM; file tools run on the host with path-based access control. Enable this only if your security review requires the agent loop itself to run in the VM."': 'reason:"默认的主机原生模式启动更快，也能在受限网络中工作。Shell 命令在 VM 内运行；文件工具在主机上运行，并使用基于路径的访问控制。只有当安全审查要求代理循环本身也在 VM 内运行时，才启用此项。"',
        'reason:"Crash and error reports are how we diagnose failures specific to your inference setup. Support turnaround will be slower without them."': 'reason:"崩溃和错误报告可帮助诊断与你的推理配置有关的问题。关闭后，支持响应会更慢。"',
        'reason:"Usage analytics help us prioritize improvements for third-party inference. Diagnostic-report uploads will also be blocked. No message content is included in either."': 'reason:"使用分析可帮助优先改进第三方推理体验。诊断报告上传也会被阻止。两者都不包含消息内容。"',
        'reason:"This disables artifact previews and connector icons. Artifacts will not render in conversations."': 'reason:"这会停用 Artifact 预览和连接器图标。Artifact 不会在对话中渲染。"',
        'reason:"Security and compatibility fixes will not install automatically. Make sure IT has another distribution path."': 'reason:"安全和兼容性修复不会自动安装。请确认 IT 有其他分发渠道。"',
        'egressRequirementsLabel:"Desktop extensions (Python runtime)"': 'egressRequirementsLabel:"桌面扩展（Python 运行时）"',
        'egressRequirementsLabel:"User-added MCP (Python runtime)"': 'egressRequirementsLabel:"用户添加的 MCP（Python 运行时）"',
        'egressRequirementsLabel:"Tool egress (VM sandbox)"': 'egressRequirementsLabel:"工具出站（VM 沙盒）"',
        'egressRequirementsLabel:"Auto-updates"': 'egressRequirementsLabel:"自动更新"',
        'egressRequirementsLabel:"Essential telemetry"': 'egressRequirementsLabel:"必要遥测"',
        'egressRequirementsLabel:"Nonessential telemetry"': 'egressRequirementsLabel:"非必要遥测"',
        'egressRequirementsLabel:"Nonessential services"': 'egressRequirementsLabel:"非必要服务"',
        'egressRequirementsLabel:"Bootstrap config server"': 'egressRequirementsLabel:"引导配置服务器"',
        'egressRequirementsLabel:"Bootstrap sign-in (OIDC)"': 'egressRequirementsLabel:"引导登录（OIDC）"',
        'placeholder:"Absolute path"': 'placeholder:"绝对路径"',
        'suffix:"seconds"': 'suffix:"秒"',
        'suffix:"hours"': 'suffix:"小时"',
        'suffix:"tokens"': 'suffix:"词元[token]"',
        'suffix:"令牌"': 'suffix:"词元[token]"',
        'hint:"Bearer (default) sends Authorization: Bearer. x-api-key is for the Anthropic API directly — auto-selected when the URL is *.anthropic.com."': 'hint:"Bearer[令牌认证]（默认）会发送 Authorization: Bearer。x-api-key 用于直连 Anthropic API；当 URL 为 *.anthropic.com 时会自动选择。"',
        'hint:"Extra headers sent to the gateway, one \'Name: Value\' per entry. For tenant routing, org IDs, etc."': 'hint:"发送到网关的额外请求头，每项一个 \'Name: Value\'。可用于租户路由、组织 ID 等。"',
        'hint:"First entry is the picker default. Aliases like sonnet, opus accepted. Optional for gateway — when set, the picker shows exactly this list instead of /v1/models discovery. Turn on 1M context only for models your provider actually serves with the extended window."': 'hint:"第一项是选择器默认模型。支持 sonnet、opus 等别名。网关可不填；填写后，模型选择器会严格显示此列表，而不是通过 /v1/models 发现。只有在提供方实际支持扩展上下文窗口时，才开启 1M 上下文。"',
        'hint:"Tags telemetry events with your org so support can find them. Not used for auth."': 'hint:"给遥测事件打上组织标记，方便支持人员定位；不用于认证。"',
        'hint:"Go straight to this provider at launch — users won\'t see the option to sign in to Anthropic instead."': 'hint:"启动时直接进入此提供方；用户不会再看到改用 Anthropic 登录的选项。"',
        'hint:"GCP region where your Vertex AI Claude models are deployed."': 'hint:"部署 Vertex AI Claude 模型的 GCP 区域。"',
        'hint:"Absolute path to service-account JSON. Leave blank to fall back to ADC."': 'hint:"服务账号 JSON 的绝对路径。留空则回退到 ADC。"',
        'hint:"Desktop-app OAuth client ID — enables Sign in with Google instead of a credentials file."': 'hint:"桌面应用 OAuth[开放授权] 客户端 ID；用于通过 Google 登录代替凭据文件。"',
        'hint:"Secret for the Desktop-app OAuth client above."': 'hint:"上方桌面应用 OAuth[开放授权] 客户端的密钥。"',
        'hint:"Override the Google OAuth scopes (space-separated). Leave blank for the default."': 'hint:"覆盖 Google OAuth[开放授权] 权限范围，用空格分隔。留空则使用默认值。"',
        'hint:"PSC endpoint, if using one."': 'hint:"如使用 PSC，请填写其端点。"',
        'hint:"Overrides profile when both are set."': 'hint:"同时设置时会覆盖配置档。"',
        'hint:"For VPC endpoints or gateway proxies."': 'hint:"用于 VPC 端点或网关代理。"',
        'hint:"Ignored if a bearer token is set."': 'hint:"如果已设置 Bearer[令牌认证] 访问令牌，则忽略此项。"',
        'hint:"Folder with AWS config/credentials. Defaults to ~/.aws when no bearer token is set."': 'hint:"包含 AWS config/credentials 的文件夹。未设置 Bearer[令牌认证] 访问令牌时默认使用 ~/.aws。"',
        'hint:"Absolute path to an executable that prints the credential."': 'hint:"可执行文件的绝对路径，该程序应输出凭据。"',
        'hint:"Runs tools inside an isolated VM instead of the host. Stronger isolation; slower file access and no host-process tools."': 'hint:"在隔离 VM 内运行工具，而不是在主机上运行。隔离更强，但文件访问更慢，且不能使用主机进程工具。"',
        'hint:"Domains Cowork\'s tools may reach during a turn. Also surfaced under Egress Requirements."': 'hint:"Cowork 工具在一次回合中允许访问的域名，也会显示在“出站网络要求”中。"',
        'hint:"Folders users may attach as a workspace. Leave unset for unrestricted access."': 'hint:"用户可作为工作区附加的文件夹。留空表示不限制。"',
        'hint:"Built-in tools removed from Cowork."': 'hint:"从 Cowork 中移除的内置工具。"',
        'hint:".dxt and .mcpb installs."': 'hint:".dxt 和 .mcpb 安装。"',
        'hint:"The in-app catalogue of installable extensions. Hide to allow sideload only."': 'hint:"应用内可安装扩展目录。隐藏后只允许侧载。"',
        'hint:"Local stdio servers added via the Developer settings. Remote servers come from the managed list above, or plugins mounted to a user\'s computer by an organization admin."': 'hint:"通过开发者设置添加的本地 stdio 服务器。远程服务器来自上方托管列表，或由组织管理员挂载到用户电脑上的插件。"',
        'hint:"Org-pushed remote MCP servers. May embed bearer tokens."': 'hint:"组织下发的远程 MCP[模型上下文协议] 服务器。可能包含 Bearer[令牌认证] 访问令牌。"',
        'hint:"Crash and performance reports to Anthropic."': 'hint:"发送给 Anthropic 的崩溃和性能报告。"',
        'hint:"Product-usage analytics and diagnostic-report uploads. No message content."': 'hint:"产品使用分析和诊断报告上传，不包含消息内容。"',
        'hint:"Favicon fetch and the artifact-preview iframe origin. Artifacts will not render."': 'hint:"网站图标获取和 Artifact 预览 iframe 源。禁用后 Artifact 不会渲染。"',
        'label:"Model ID"': 'label:"模型 ID"',
        'label:"Offer 1M-context variant"': 'label:"提供 1M 上下文变体"',
        'label:"Name"': 'label:"名称"',
        'label:"URL"': 'label:"URL"',
        'label:"Transport"': 'label:"传输方式"',
        'label:"OAuth"': 'label:"OAuth[开放授权]"',
        'label:"Headers"': 'label:"请求头"',
        'label:"Headers helper script"': 'label:"请求头辅助脚本"',
        'label:"Helper cache TTL (sec)"': 'label:"辅助缓存时间（秒）"',
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


def apply_locale_resources(app_dir: Path, dry_run: bool = False) -> int:
    app_dir = app_dir.expanduser()
    if not (app_dir / FRONTEND_I18N_REL / "en-US.json").exists():
        print(f"Claude frontend resources were not found, skipping locale patch: {app_dir}")
        return 0

    require_file(FRONTEND_TRANSLATION)
    require_file(DESKTOP_TRANSLATION)
    patch_language_whitelist(app_dir)
    patch_hardcoded_frontend_strings(app_dir)
    merge_frontend_locale(app_dir)
    install_desktop_locale(app_dir)
    install_statsig_locale(app_dir)
    patch_hardcoded_desktop_menu_strings(app_dir, dry_run)
    return 0


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
    backup = unique_backup_path(path.with_suffix(suffix))
    shutil.copy2(path, backup)
    return backup


def unique_backup_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(1, 1000):
        candidate = path.with_name(f"{path.name}-{index}")
        if not candidate.exists():
            return candidate
    raise SystemExit(f"Could not create a unique backup path near: {path}")


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


def third_party_config_entries(data_dir: Path) -> list[dict[str, Any]]:
    library = third_party_config_library_dir(data_dir)
    meta_path = third_party_config_meta_path(data_dir)
    if not library.exists():
        return []

    meta = load_json_dict(meta_path, label="Claude third-party config metadata")
    candidate_paths: list[Path] = []
    applied_id = nonempty_string(meta.get("appliedId"))
    if applied_id:
        candidate_paths.append(third_party_config_path(applied_id, data_dir))

    entries = meta.get("entries")
    names_by_id: dict[str, str] = {}
    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            entry_id = nonempty_string(entry.get("id"))
            if not entry_id:
                continue
            names_by_id[entry_id] = nonempty_string(entry.get("name")) or entry_id
            entry_path = third_party_config_path(entry_id, data_dir)
            if entry_path not in candidate_paths:
                candidate_paths.append(entry_path)

    for config_path in sorted(library.glob("*.json")):
        if config_path.name == "_meta.json" or config_path in candidate_paths:
            continue
        candidate_paths.append(config_path)

    valid: list[dict[str, Any]] = []
    for config_path in candidate_paths:
        data = load_json_dict(config_path, label="Claude third-party config")
        base_url = nonempty_string(data.get("inferenceGatewayBaseUrl"))
        credential = nonempty_string(data.get("inferenceGatewayApiKey"))
        if not base_url or not credential:
            continue
        try:
            parsed = urllib.parse.urlparse(base_url)
        except ValueError:
            continue
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue

        config_id = config_path.stem
        valid.append(
            {
                "data_dir": data_dir,
                "library": library,
                "path": config_path,
                "id": config_id,
                "name": names_by_id.get(config_id, config_id),
                "base_url": base_url,
                "auth_scheme": nonempty_string(data.get("inferenceGatewayAuthScheme")) or "bearer",
                "disable_chooser": data.get("disableDeploymentModeChooser"),
            }
        )
    return valid


def discover_desktop_third_party_sources() -> tuple[list[dict[str, Any]], list[str]]:
    sources: list[dict[str, Any]] = []
    messages: list[str] = []
    for data_dir in third_party_data_paths():
        library = third_party_config_library_dir(data_dir)
        if not library.exists():
            messages.append(f"Desktop config library not found: {library}")
            continue
        entries = third_party_config_entries(data_dir)
        if entries:
            sources.append({"data_dir": data_dir, "library": library, "entries": entries})
        else:
            messages.append(f"No valid gateway config found in Desktop config library: {library}")
    return sources, messages


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


def backup_third_party_library(data_dir: Path, reason: str) -> Path | None:
    library = third_party_config_library_dir(data_dir)
    if not library.exists():
        return None
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_root = tool_root() / "user-data-backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    backup = unique_backup_path(backup_root / f"third-party-config-{reason}-{stamp}")
    shutil.copytree(library, backup)
    return backup


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


def set_disable_deployment_mode_chooser(data_dir: Path, dry_run: bool) -> None:
    for entry in third_party_config_entries(data_dir):
        config_path = entry["path"]
        current = load_json_dict(config_path, backup_invalid=True, label="Claude third-party config")
        if current.get("disableDeploymentModeChooser") is True:
            continue
        updated = dict(current)
        updated["disableDeploymentModeChooser"] = True
        if dry_run:
            print(f"[dry-run] Would enable skip login-mode chooser: {config_path}")
            continue
        if config_path.exists():
            backup = backup_file(config_path, "before-skip-login-mode-chooser")
            print(f"Backed up Claude third-party config: {backup}")
        save_json(config_path, updated)
        print(f"Enabled skip login-mode chooser: {config_path}")


def sync_desktop_third_party_library(source_data_dir: Path, target_data_dir: Path, dry_run: bool = False) -> int:
    source_library = third_party_config_library_dir(source_data_dir)
    target_library = third_party_config_library_dir(target_data_dir)
    if not source_library.exists():
        print(f"未找到来源 Desktop 配置库[configLibrary]: {source_library}")
        return 1

    if source_data_dir.resolve() == target_data_dir.resolve():
        print(f"来源和目标配置库[configLibrary]相同: {source_library}")
        set_disable_deployment_mode_chooser(target_data_dir, dry_run)
        return 0

    json_files = sorted(source_library.glob("*.json"))
    if not json_files:
        print(f"配置库中没有找到 JSON 配置文件: {source_library}")
        return 1

    print(f"来源配置库[configLibrary]: {source_library}")
    print(f"目标配置库[configLibrary]: {target_library}")
    if dry_run:
        print(f"[dry-run] 将同步 {len(json_files)} 个配置文件。")
        return 0

    backup = backup_third_party_library(target_data_dir, "before-sync")
    if backup:
        print(f"已备份目标配置库[configLibrary]: {backup}")

    target_library.mkdir(parents=True, exist_ok=True)
    for source in json_files:
        target = target_library / source.name
        shutil.copy2(source, target)
        print(f"已同步配置文件: {target.name}")

    set_disable_deployment_mode_chooser(target_data_dir, dry_run)
    return 0


def apply_third_party_inference_config(dry_run: bool = False) -> int:
    discovered, messages = discover_local_claude_gateway_config()
    if not discovered:
        print("没有应用 Claude Code gateway[网关] 配置。")
        for message in messages:
            print(f"  {message}")
        print("可以在 Developer -> Configure Third-Party Inference[第三方大模型推理] 中手动填写，或把环境变量加入 ~/.claude/settings.json。")
        return 0

    print("检测到 Claude Code gateway[网关] 配置:")
    print(f"  Base URL: {discovered['base_url']}")
    print(f"  凭据: {discovered['credential_name']} = {mask_secret(discovered['credential'])}")
    print(f"  认证方式: {discovered['auth_scheme']}")

    data_dir = primary_third_party_data_dir()
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
        print(f"Claude 第三方大模型推理配置已是最新: {config_path}")
        return 0

    if dry_run:
        print(f"[dry-run] 将应用 Claude 第三方大模型推理配置: {config_path}")
        return 0

    if config_path.exists():
        backup = backup_file(config_path, "before-third-party-config")
        print(f"已备份 Claude 第三方大模型推理配置: {backup}")
    save_json(config_path, updated)
    print(f"已应用 Claude 第三方大模型推理配置: {config_path} (id: {config_id})")

    return 0


def show_third_party_inference_config() -> int:
    desktop_sources, desktop_messages = discover_desktop_third_party_sources()
    print("Claude Desktop 第三方大模型推理配置库[configLibrary]:")
    if desktop_sources:
        for index, source in enumerate(desktop_sources, start=1):
            print(f"  [{index}] {source['library']}")
            for entry in source["entries"]:
                print(
                    f"      - {entry['name']} ({entry['id']}): "
                    f"{entry['base_url']} / auth={entry['auth_scheme']} / "
                    f"skipLoginChooser={entry['disable_chooser']}"
                )
    else:
        print("  Not found.")
        for message in desktop_messages:
            print(f"  {message}")

    print()
    discovered, messages = discover_local_claude_gateway_config()
    print("Claude Code gateway[网关] 配置检测:")
    if discovered:
        print(f"  Base URL: {discovered['base_url']}")
        print(f"  Credential: {discovered['credential_name']} = {mask_secret(discovered['credential'])}")
        print(f"  Auth scheme: {discovered['auth_scheme']}")
    else:
        print("  Not found.")
        for message in messages:
            print(f"  {message}")

    print()
    print("Claude Desktop 第三方大模型推理配置:")
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


def check_third_party_sources() -> int:
    desktop_sources, _ = discover_desktop_third_party_sources()
    code_config, _ = discover_local_claude_gateway_config()
    if desktop_sources or code_config:
        print("检测到可复用的第三方大模型推理配置。")
        if desktop_sources:
            print(f"  Desktop 配置库[configLibrary]: {len(desktop_sources)}")
        if code_config:
            print(f"  Claude Code gateway[网关]: {code_config['base_url']}")
        return 0
    print("未检测到可复用的第三方大模型推理配置。")
    return 10


def prompt_line(prompt: str) -> str | None:
    try:
        return input(prompt).replace("\x00", "").strip()
    except EOFError:
        print()
        print("No input was provided; cancelled.")
        return None


def choose_desktop_third_party_source(sources: list[dict[str, Any]]) -> Path | None:
    if not sources:
        print("没有可同步的 Claude Desktop 第三方大模型推理配置。")
        return None
    if len(sources) == 1:
        return sources[0]["data_dir"]

    print()
    print("请选择要同步的 Desktop 配置库[configLibrary]:")
    for index, source in enumerate(sources, start=1):
        entries = ", ".join(entry["name"] for entry in source["entries"])
        print(f"  {index}. {source['library']} ({entries})")
    answer = prompt_line("输入来源编号，或输入 0 取消: ")
    if answer is None:
        return None
    if answer == "0":
        return None
    try:
        choice = int(answer)
    except ValueError:
        print("无效选择。")
        return None
    if choice < 1 or choice > len(sources):
        print("无效选择。")
        return None
    return sources[choice - 1]["data_dir"]


def third_party_config_wizard() -> int:
    print("第三方大模型推理配置向导")
    print("你可以保持绿色版全新，也可以同步 Claude Desktop 配置库[configLibrary]，或从 Claude Code 生成配置。")
    print("访问令牌[token]、API key 等敏感值会在输出中打码。")
    print()
    show_third_party_inference_config()

    while True:
        print()
        print("1. 保持全新，不导入也不修改第三方大模型推理配置")
        print("2. 同步现有 Claude Desktop 配置库[configLibrary] 到绿色版")
        print("3. 从 Claude Code 配置生成 Desktop gateway[网关] 配置")
        print("4. 重新显示检测到的配置")
        print("0. 返回")
        choice = prompt_line("请选择: ")
        if choice is None:
            return 0

        if choice == "0":
            return 0
        if choice == "1":
            print("已保持全新。没有导入第三方大模型推理配置。")
            return 0
        if choice == "2":
            sources, _ = discover_desktop_third_party_sources()
            source_data_dir = choose_desktop_third_party_source(sources)
            if not source_data_dir:
                continue
            target_data_dir = primary_third_party_data_dir()
            print()
            print("这会复制配置库[configLibrary] JSON 文件，并启用跳过登录模式选择。")
            print(f"Source: {third_party_config_library_dir(source_data_dir)}")
            print(f"Target: {third_party_config_library_dir(target_data_dir)}")
            answer = prompt_line("输入 SYNC 继续: ")
            if answer != "SYNC":
                print("已取消。")
                continue
            return sync_desktop_third_party_library(source_data_dir, target_data_dir, False)
        if choice == "3":
            discovered, messages = discover_local_claude_gateway_config()
            if not discovered:
                print("没有可转换的 Claude Code gateway[网关] 配置。")
                for message in messages:
                    print(f"  {message}")
                continue
            print()
            print("这会把 gateway[网关] 字段写入 Desktop 配置库[configLibrary]，并启用跳过登录模式选择。")
            print(f"Base URL: {discovered['base_url']}")
            print(f"Credential: {discovered['credential_name']} = {mask_secret(discovered['credential'])}")
            answer = prompt_line("输入 APPLY 继续: ")
            if answer != "APPLY":
                print("已取消。")
                continue
            return apply_third_party_inference_config(False)
        if choice == "4":
            print()
            show_third_party_inference_config()
            continue
        print("未知选项。")


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
    if len(old_token) != len(new_token):
        raise ValueError("ASAR in-place token replacements must keep the same byte length.")

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


def patch_exe_asar_header_hash(
    app_dir: Path,
    expected_hash: str,
    old_hashes: list[str],
    reason: str = "before-asar-hash-update",
) -> None:
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

        backup = backup_file(exe, reason)
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


def padded_utf8_replacement(source: str, target: str) -> bytes:
    source_bytes = source.encode("utf-8")
    target_bytes = target.encode("utf-8")
    if len(target_bytes) > len(source_bytes):
        raise ValueError(f"Replacement is too long: {target!r} for {source!r}")
    return target_bytes + (b" " * (len(source_bytes) - len(target_bytes)))


def count_asar_tokens(asar: Path, tokens: list[bytes]) -> dict[bytes, int]:
    data = asar.read_bytes()
    _, _, content_base, header = parse_asar(data)
    counts = {token: 0 for token in tokens}
    for _, entry in asar_file_entries(header):
        try:
            offset = content_base + int(entry["offset"])
            size = int(entry["size"])
        except (KeyError, TypeError, ValueError):
            continue
        chunk = data[offset : offset + size]
        for token in tokens:
            counts[token] += chunk.count(token)
    return counts


def patch_hardcoded_desktop_menu_strings(app_dir: Path, dry_run: bool = False) -> int:
    asar = app_dir.expanduser() / "resources/app.asar"
    if not asar.exists():
        print(f"Claude app.asar was not found, skipping desktop menu string patch: {asar}")
        return 0

    menu_replacements: dict[str, str] = {
        "Enable Main Process Debugger": "启用主进程调试器",
        "Record Performance Trace": "记录性能跟踪",
        "Write Main Process Heap Snapshot": "写入主进程堆快照",
        "Record Memory Trace (auto-stop)": "内存跟踪(自动停止)",
    }
    replacements = [
        (source.encode("utf-8"), padded_utf8_replacement(source, target))
        for source, target in menu_replacements.items()
    ]

    counts = count_asar_tokens(asar, [source for source, _ in replacements])
    total = sum(counts.values())
    if total == 0:
        print(f"Hardcoded desktop menu strings are already patched or not present: {asar}")
        return 0

    if dry_run:
        print(f"[dry-run] Would patch {total} hardcoded desktop menu string(s) in {asar}.")
        return 0

    backup = backup_file(asar, "before-desktop-menu-zh-CN")
    old_header_hashes: list[str] = []
    final_header_hash = asar_header_hash(asar.read_bytes())
    patched_total = 0
    patched_files_total = 0

    try:
        for source, target in replacements:
            patched_files, patched_tokens, old_header_hash, new_header_hash = patch_asar_file_content_and_integrity(
                asar,
                source,
                target,
            )
            if patched_tokens:
                old_header_hashes.append(old_header_hash)
                final_header_hash = new_header_hash
                patched_total += patched_tokens
                patched_files_total += patched_files
    except PermissionError:
        if backup.exists():
            shutil.copy2(backup, asar)
        raise SystemExit(
            "Could not patch desktop menu strings because Windows denied access. "
            "Close Claude completely, then run the patch again."
        )
    except Exception:
        if backup.exists():
            shutil.copy2(backup, asar)
        raise

    print(f"Backed up Claude app.asar: {backup}")
    print(
        f"Patched hardcoded desktop menu strings: "
        f"{patched_total} replacement(s) in {patched_files_total} file patch(es)"
    )
    patch_exe_asar_header_hash(
        app_dir,
        final_header_hash,
        [*old_header_hashes, *backup_header_hashes(asar)],
        "before-desktop-menu-zh-CN",
    )
    return 0


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
        patch_exe_asar_header_hash(app_dir, current_hash, backup_header_hashes(asar), "before-cowork-compat")
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
    patch_exe_asar_header_hash(
        app_dir,
        new_header_hash,
        [old_header_hash, *backup_header_hashes(asar)],
        "before-cowork-compat",
    )
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
    apply_locale_resources(target_dir, False)
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
    parser.add_argument("--show-third-party-inference", action="store_true", help="Show Claude Desktop and Claude Code third-party model inference config")
    parser.add_argument("--check-third-party-sources", action="store_true", help="Check whether reusable third-party model inference config exists")
    parser.add_argument("--third-party-wizard", action="store_true", help="Open the third-party model inference config wizard")
    parser.add_argument("--apply-third-party-inference", action="store_true", help="Generate Desktop gateway config from Claude Code settings")
    parser.add_argument("--apply-cowork-compat", action="store_true", help="Patch portable Claude so Cowork does not require the MSIX launch path")
    parser.add_argument("--patch-desktop-menu", action="store_true", help="Patch hardcoded desktop menu strings into zh-CN")
    parser.add_argument("--apply-locale", action="store_true", help="Apply zh-CN locale resources to the patched copy without reinstalling")
    parser.add_argument("--clean-user-data", action="store_true", help="Move Claude user config/account data to a timestamped backup")
    parser.add_argument("--create-shortcuts", action="store_true", help="Create Desktop and Start Menu shortcuts for Claude zh-CN and Claude Code")
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
    if args.check_third_party_sources:
        return check_third_party_sources()
    if args.third_party_wizard:
        return third_party_config_wizard()
    if args.apply_third_party_inference:
        return apply_third_party_inference_config(False)
    if args.apply_cowork_compat:
        return patch_cowork_portable_detection(args.target_dir, False)
    if args.patch_desktop_menu:
        return patch_hardcoded_desktop_menu_strings(args.target_dir, False)
    if args.apply_locale:
        return apply_locale_resources(args.target_dir, False)
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
    apply_locale_resources(app_dir, args.dry_run)
    patch_cowork_portable_detection(app_dir, args.dry_run)
    set_user_locale(args.dry_run)
    enable_developer_mode(args.dry_run)
    verify(app_dir)
    create_shortcuts(app_dir)

    if args.launch and not args.dry_run:
        launch(app_dir)

    print(f"Done. Patched Claude is at: {app_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
