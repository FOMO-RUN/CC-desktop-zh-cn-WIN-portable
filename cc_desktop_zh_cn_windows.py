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
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    import winreg
except ImportError:  # pragma: no cover - Windows-only feature.
    winreg = None


LANG_CODE = "zh-CN"
ROOT = Path(__file__).resolve().parent
RESOURCES = ROOT / "resources"
COWORK_PORTABLE_ENV = "CZCOWORK"
CLAUDE_USER_DATA_DIR_ENV = "CLAUDE_USER_DATA_DIR"

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

COWORK_NAMESPACE_REPLACEMENTS: List[Tuple[bytes, bytes]] = [
    (b"cowork-vm-service", b"ccdesk-vm-service"),
    (b"cowork-vm-portabl", b"ccdesk-vm-service"),
    (b"cowork-vm-nat", b"ccdesk-vm-nat"),
    (b"cowork-vm-store", b"ccdesk-vm-store"),
]
for source_token, target_token in COWORK_NAMESPACE_REPLACEMENTS:
    if len(source_token) != len(target_token):
        raise RuntimeError("Cowork namespace replacement tokens must have the same length.")
COWORK_PORTABLE_PIPE_NAME = "ccdesk-vm-service"
OAUTH_PROTOCOL = "claude"
OAUTH_REG_PATH = rf"Software\Classes\{OAUTH_PROTOCOL}"
OAUTH_BACKUP_DIRNAME = "oauth-protocol-backups"

PORTABLE_USER_DATA_MIGRATION_MARKER = ".portable-user-data-migrated-v1.json"
PORTABLE_USER_DATA_MIGRATION_ITEMS = [
    "claude-code",
    "claude-code-sessions",
    "local-agent-mode-sessions",
    "configLibrary",
    "IndexedDB",
    "Local Storage",
    "Session Storage",
    "WebStorage",
    "blob_storage",
    "Network",
    "Cookies",
    "Cookies-journal",
    "Preferences",
    "Local State",
    "window-state.json",
    "claude_desktop_config.json",
    "config.json",
    "developer_settings.json",
    "git-worktrees.json",
    "title-gen",
    "pending-uploads",
]

USER_DATA_SYNC_ITEMS = [
    "configLibrary",
    "IndexedDB",
    "Local Storage",
    "Session Storage",
    "WebStorage",
    "blob_storage",
    "Network",
    "Cookies",
    "Cookies-journal",
    "Preferences",
    "Local State",
    "window-state.json",
    "claude_desktop_config.json",
    "config.json",
    "developer_settings.json",
    "git-worktrees.json",
    "title-gen",
    "pending-uploads",
]

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def run(cmd: List[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
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


def portable_user_data_dir() -> Path:
    return roaming_app_data() / "ClaudeZhCN-3p"


def legacy_portable_user_data_dirs() -> List[Path]:
    return [roaming_app_data() / "ClaudeZhCN"]


def official_user_data_dirs() -> List[Path]:
    paths = [
        roaming_app_data() / "Claude",
        roaming_app_data() / "Claude-3p",
    ]

    packages = local_app_data() / "Packages"
    if packages.exists():
        for pattern in ["Claude_*", "*Anthropic*Claude*"]:
            for package in packages.glob(pattern):
                paths.extend(
                    [
                        package / "LocalCache/Roaming/Claude",
                        package / "LocalCache/Roaming/Claude-3p",
                    ]
                )

    unique: List[Path] = []
    seen: Set[str] = set()
    for path in paths:
        key = str(path).lower()
        if key not in seen:
            unique.append(path)
            seen.add(key)
    return unique


def portable_user_data_migration_sources() -> List[Path]:
    paths = [
        *legacy_portable_user_data_dirs(),
    ]

    target = portable_user_data_dir().resolve()
    unique: List[Path] = []
    seen: Set[str] = set()
    for path in paths:
        try:
            if path.resolve() == target:
                continue
        except OSError:
            pass
        key = str(path).lower()
        if key not in seen:
            unique.append(path)
            seen.add(key)
    return unique


def powershell_exe() -> str:
    return "powershell.exe"


def ps_single_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def normalize_version(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    value = value.strip()
    parts = value.split(".")
    while len(parts) > 1 and parts[-1] == "0":
        parts.pop()
    return ".".join(parts)


def latest_msix_info() -> Dict[str, Optional[str]]:
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


def app_exe(app_dir: Path) -> Optional[Path]:
    for name in ["Claude.exe", "claude.exe"]:
        exe = app_dir / name
        if exe.exists():
            return exe
    return None


def app_version(app_dir: Path) -> Optional[str]:
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

    print(f"官方 Claude Desktop 最新版本: {latest_version or '未知'}")
    print(f"本地汉化绿色版版本: {local_version or '尚未安装'}")

    if local_version and latest_version and normalize_version(local_version) == normalize_version(latest_version):
        print("本地汉化绿色版已经是最新版本。")
        return 0

    print("检测到可更新版本，或本地汉化绿色版尚未生成。")
    return 10


def find_appx_install_location() -> Optional[Path]:
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


def find_source_app_dir() -> Optional[Path]:
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


def user_data_paths() -> List[Path]:
    paths = [
        portable_user_data_dir(),
        *legacy_portable_user_data_dirs(),
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

    unique: List[Path] = []
    seen: Set[str] = set()
    for path in paths:
        key = str(path).lower()
        if key not in seen:
            unique.append(path)
            seen.add(key)
    return unique


def third_party_data_paths() -> List[Path]:
    paths = [
        portable_user_data_dir(),
        *legacy_portable_user_data_dirs(),
        roaming_app_data() / "Claude-3p",
    ]

    packages = local_app_data() / "Packages"
    if packages.exists():
        package_patterns = ["Claude_*", "*Anthropic*Claude*"]
        for pattern in package_patterns:
            for package in packages.glob(pattern):
                paths.append(package / "LocalCache/Roaming/Claude-3p")

    unique: List[Path] = []
    seen: Set[str] = set()
    for path in paths:
        key = str(path).lower()
        if key not in seen:
            unique.append(path)
            seen.add(key)
    return unique


def primary_third_party_data_dir() -> Path:
    return third_party_data_paths()[0]


def third_party_config_library_dir(data_dir: Optional[Path] = None) -> Path:
    return (data_dir or primary_third_party_data_dir()) / "configLibrary"


def third_party_config_meta_path(data_dir: Optional[Path] = None) -> Path:
    return third_party_config_library_dir(data_dir) / "_meta.json"


def third_party_config_path(config_id: str, data_dir: Optional[Path] = None) -> Path:
    return third_party_config_library_dir(data_dir) / f"{config_id}.json"


def claude_code_config_paths() -> List[Path]:
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
        print(f"[存在] {label}: {path} ({format_size(path_size(path))})")
    else:
        print(f"[缺失] {label}: {path}")


def profile_score(path: Path) -> int:
    if not path.exists():
        return 0
    score = 0
    for name in PORTABLE_USER_DATA_MIGRATION_ITEMS:
        candidate = path / name
        if candidate.exists():
            score += 1
            if candidate.is_dir():
                try:
                    score += min(25, sum(1 for _ in candidate.rglob("*")))
                except OSError:
                    pass
    return score


def copy_file_long_path(source: Path, target: Path, overwrite: bool) -> bool:
    if target.exists() and not overwrite:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return True


def copy_tree_long_path(
    source: Path,
    target: Path,
    overwrite_files: bool,
    errors: Optional[List[str]] = None,
    *,
    excluded_names: Optional[Set[str]] = None,
) -> Tuple[int, int]:
    copied_files = 0
    skipped_files = 0
    excluded_names = excluded_names or set()
    if not source.exists():
        return copied_files, skipped_files

    if source.is_file():
        try:
            if copy_file_long_path(source, target, overwrite_files):
                copied_files += 1
            else:
                skipped_files += 1
        except OSError as exc:
            if errors is not None:
                errors.append(f"{source} -> {target}: {exc}")
        return copied_files, skipped_files

    target.mkdir(parents=True, exist_ok=True)
    try:
        with os.scandir(source) as entries:
            for entry in entries:
                if entry.name in excluded_names:
                    skipped_files += 1
                    continue
                child_source = source / entry.name
                child_target = target / entry.name
                try:
                    if entry.is_dir(follow_symlinks=False):
                        child_copied, child_skipped = copy_tree_long_path(
                            child_source,
                            child_target,
                            overwrite_files,
                            errors,
                            excluded_names=excluded_names,
                        )
                        copied_files += child_copied
                        skipped_files += child_skipped
                    elif entry.is_file(follow_symlinks=False):
                        if copy_file_long_path(child_source, child_target, overwrite_files):
                            copied_files += 1
                        else:
                            skipped_files += 1
                except OSError as exc:
                    if errors is not None:
                        errors.append(f"{child_source} -> {child_target}: {exc}")
    except OSError as exc:
        if errors is not None:
            errors.append(f"{source}: {exc}")
    return copied_files, skipped_files


def backup_path_to_tool(path: Path, reason: str) -> Optional[Path]:
    if not path.exists():
        return None
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_root = tool_root() / "user-data-backups" / f"{reason}-{stamp}"
    backup_root.mkdir(parents=True, exist_ok=True)
    label = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(path).strip("\\/:"))
    destination = unique_backup_path(backup_root / label)
    if path.is_dir():
        shutil.copytree(path, destination)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
    return destination


def copy_named_items(
    source_dir: Path,
    target_dir: Path,
    names: List[str],
    *,
    overwrite: bool,
    backup_existing: bool,
    reason: str,
    exclude_vm: bool = True,
) -> Tuple[int, int, List[str]]:
    errors: List[str] = []
    copied = 0
    skipped = 0
    excluded = {"vm_bundles"} if exclude_vm else set()
    for name in names:
        if name in excluded:
            continue
        source = source_dir / name
        target = target_dir / name
        if not source.exists():
            continue
        if backup_existing and target.exists():
            backup = backup_path_to_tool(target, reason)
            if backup:
                print(f"已备份即将覆盖的目标: {target} -> {backup}")
        if source.is_dir():
            child_copied, child_skipped = copy_tree_long_path(
                source,
                target,
                overwrite_files=overwrite,
                errors=errors,
                excluded_names=excluded,
            )
        else:
            child_copied, child_skipped = copy_tree_long_path(
                source,
                target,
                overwrite_files=overwrite,
                errors=errors,
                excluded_names=excluded,
            )
        copied += child_copied
        skipped += child_skipped
    return copied, skipped, errors


def ensure_portable_user_data_migrated() -> int:
    target_dir = portable_user_data_dir()
    marker = target_dir / PORTABLE_USER_DATA_MIGRATION_MARKER
    target_dir.mkdir(parents=True, exist_ok=True)
    if marker.exists():
        print(f"旧绿色版用户数据迁移已经检查过: {marker}")
        return 0

    candidates = [path for path in portable_user_data_migration_sources() if path.exists()]
    if not candidates:
        save_json(
            marker,
            {
                "checkedAt": dt.datetime.now().isoformat(timespec="seconds"),
                "source": None,
                "copiedFiles": 0,
                "skippedFiles": 0,
                "note": "No legacy source found.",
            },
        )
        print("没有找到旧绿色版用户数据来源。")
        return 0

    candidates.sort(key=lambda path: (profile_score(path), path.stat().st_mtime if path.exists() else 0), reverse=True)
    selected = candidates[0]
    print(f"选择的旧绿色版数据来源: {selected}")
    print(f"绿色版目标用户数据: {target_dir}")
    copied, skipped, errors = copy_named_items(
        selected,
        target_dir,
        PORTABLE_USER_DATA_MIGRATION_ITEMS,
        overwrite=False,
        backup_existing=False,
        reason="before-migration",
        exclude_vm=True,
    )
    if errors:
        print("迁移过程中遇到复制错误；本次不会写入完成标记，下次仍会重试:")
        for error in errors[:20]:
            print(f"  {error}")
        if len(errors) > 20:
            print(f"  ... {len(errors) - 20} more")
        return 1

    save_json(
        marker,
        {
            "checkedAt": dt.datetime.now().isoformat(timespec="seconds"),
            "source": str(selected),
            "target": str(target_dir),
            "copiedFiles": copied,
            "skippedFiles": skipped,
            "excluded": ["vm_bundles"],
        },
    )
    print(f"旧绿色版用户数据迁移检查完成：复制 {copied} 个文件，保留 {skipped} 个已有文件。")
    return 0


def show_user_data(target_dir: Path) -> int:
    print("Claude zh-CN 工具路径:")
    print_path_info("汉化版程序目录", target_dir.expanduser())
    print_path_info("启动器", launcher_path())
    print_path_info("下载缓存", tool_root() / "downloads")
    print_path_info("用户数据备份", tool_root() / "user-data-backups")
    for label, path in shortcut_paths().items():
        print_path_info(f"{label} 快捷方式", path)
    for label, path in claude_code_shortcut_paths().items():
        print_path_info(f"{label} 快捷方式", path)
    print()
    print("Claude 用户配置 / 账号数据路径:")
    for path in user_data_paths():
        print_path_info("用户数据", path)
    print()
    print("Claude API 模式数据路径:")
    for path in third_party_data_paths():
        print_path_info("API 模式数据", path)
        print_path_info("API 配置库", third_party_config_library_dir(path))
    print()
    print("配置文件:")
    for path in config_paths():
        print_path_info("config 配置", path)
    print()
    print("开发者模式文件:")
    for path in developer_settings_paths():
        print_path_info("developer_settings 开发者设置", path)
    print()
    print("Claude Code 本地配置文件:")
    for path in claude_code_config_paths():
        print_path_info("Claude Code 配置", path)
    return 0


def shortcut_paths() -> Dict[str, Path]:
    return {
        "桌面 Claude zh-CN": Path.home() / "Desktop" / "Claude zh-CN.lnk",
        "开始菜单 Claude zh-CN": roaming_app_data() / "Microsoft/Windows/Start Menu/Programs/Claude zh-CN.lnk",
    }


def legacy_shortcut_paths() -> Dict[str, Path]:
    return {
        "桌面旧版 WIN CC Desktop": Path.home() / "Desktop" / "WIN CC Desktop zh-CN Portable.lnk",
        "开始菜单旧版 WIN CC Desktop": roaming_app_data()
        / "Microsoft/Windows/Start Menu/Programs/WIN CC Desktop zh-CN Portable.lnk",
        "桌面旧版 CC Desktop": Path.home() / "Desktop" / "CC Desktop zh-CN Portable.lnk",
        "开始菜单旧版 CC Desktop": roaming_app_data()
        / "Microsoft/Windows/Start Menu/Programs/CC Desktop zh-CN Portable.lnk",
    }


def claude_code_shortcut_paths() -> Dict[str, Path]:
    return {
        "桌面 Claude Code": Path.home() / "Desktop" / "Claude Code.lnk",
        "开始菜单 Claude Code": roaming_app_data() / "Microsoft/Windows/Start Menu/Programs/Claude Code.lnk",
    }


def claude_code_command() -> Optional[Path]:
    command = shutil.which("claude")
    candidates = []
    if command:
        candidates.append(Path(command))
    candidates.extend(
        [
            Path.home() / ".local/bin/claude.exe",
            Path.home() / ".local/bin/claude.cmd",
            Path.home() / ".local/bin/claude.bat",
            local_app_data() / "Microsoft/WinGet/Links/claude.exe",
            local_app_data() / "Microsoft/WinGet/Links/claude.cmd",
            roaming_app_data() / "npm/claude.cmd",
            roaming_app_data() / "npm/claude.exe",
            roaming_app_data() / "npm/claude.bat",
        ]
    )

    seen: Set[str] = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        if candidate.exists() and candidate.suffix.lower() in {".exe", ".cmd", ".bat"}:
            return candidate
    return None


CLAUDE_CODE_WINGET_ID = "Anthropic.ClaudeCode"
CLAUDE_CODE_NPM_PACKAGE = "@anthropic-ai/claude-code"
CLAUDE_CODE_NATIVE_CMD_INSTALLER_URL = "https://claude.ai/install.cmd"


def command_output(cmd: List[str], timeout: int = 60) -> Tuple[int, str]:
    try:
        result = subprocess.run(
            cmd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=timeout,
        )
        return result.returncode, result.stdout.strip()
    except FileNotFoundError:
        return 127, ""
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout if isinstance(exc.stdout, str) else ""
        return 124, output.strip()


def claude_code_command_paths() -> List[Path]:
    candidates: List[Path] = []
    code, output = command_output(["where.exe", "claude"], timeout=15)
    if code == 0:
        candidates.extend(Path(line.strip()) for line in output.splitlines() if line.strip())
    command = shutil.which("claude")
    if command:
        candidates.append(Path(command))
    candidates.extend(
        [
            Path.home() / ".local/bin/claude.exe",
            Path.home() / ".local/bin/claude.cmd",
            Path.home() / ".local/bin/claude.bat",
            local_app_data() / "Microsoft/WinGet/Links/claude.exe",
            local_app_data() / "Microsoft/WinGet/Links/claude.cmd",
            roaming_app_data() / "npm/claude.cmd",
            roaming_app_data() / "npm/claude.exe",
            roaming_app_data() / "npm/claude.bat",
        ]
    )
    seen: Set[str] = set()
    existing: List[Path] = []
    for candidate in candidates:
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        if candidate.exists():
            existing.append(candidate)
    return existing


def claude_code_version() -> Optional[str]:
    command = claude_code_command()
    if not command:
        return None
    code, output = command_output([str(command), "--version"], timeout=20)
    if code == 0 and output:
        return output.splitlines()[0].strip()
    return None


def winget_available() -> bool:
    return shutil.which("winget") is not None


def node_major_version() -> Optional[int]:
    if shutil.which("node") is None:
        return None
    code, output = command_output(["node", "--version"], timeout=20)
    if code != 0:
        return None
    match = re.search(r"v?(\d+)", output)
    return int(match.group(1)) if match else None


def winget_claude_code_installed() -> bool:
    if not winget_available():
        return False
    code, output = command_output(
        ["winget", "list", "--id", CLAUDE_CODE_WINGET_ID, "--exact", "--accept-source-agreements"],
        timeout=45,
    )
    return code == 0 and CLAUDE_CODE_WINGET_ID.lower() in output.lower()


def npm_claude_code_installed() -> bool:
    if shutil.which("npm") is None:
        return False
    code, output = command_output(["npm", "list", "-g", CLAUDE_CODE_NPM_PACKAGE, "--depth=0", "--json"], timeout=45)
    if code not in {0, 1} or not output:
        return False
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return CLAUDE_CODE_NPM_PACKAGE.lower() in output.lower()
    dependencies = data.get("dependencies")
    return isinstance(dependencies, dict) and CLAUDE_CODE_NPM_PACKAGE in dependencies


def native_claude_code_paths() -> List[Path]:
    paths = [
        Path.home() / ".local/bin/claude.exe",
        Path.home() / ".local/bin/claude",
        Path.home() / ".local/share/claude",
    ]
    return [path for path in paths if path.exists()]


def detect_claude_code_install_methods() -> List[str]:
    methods: List[str] = []
    if winget_claude_code_installed():
        methods.append("winget")
    if npm_claude_code_installed():
        methods.append("npm")
    if native_claude_code_paths():
        methods.append("native")
    if claude_code_command_paths() and not methods:
        methods.append("path")
    return methods


def show_claude_code_status() -> int:
    print("Claude Code 安装状态:")
    version = claude_code_version()
    print(f"  版本: {version or '未检测到可运行的 claude 命令'}")
    methods = detect_claude_code_install_methods()
    print(f"  安装来源: {', '.join(methods) if methods else '未安装或不在 PATH 中'}")
    print(f"  WinGet 包: {'已安装' if 'winget' in methods else ('可用但未安装' if winget_available() else '未检测到 winget')}")
    print(f"  npm 全局包: {'已安装' if 'npm' in methods else ('未安装' if shutil.which('npm') else '未检测到 npm')}")
    native_paths = native_claude_code_paths()
    print(f"  原生安装文件: {'已检测到' if native_paths else '未检测到'}")
    for path in claude_code_command_paths():
        print_path_info("claude 命令", path)
    for path in native_paths:
        print_path_info("原生安装路径", path)
    print()
    print("建议:")
    if not methods:
        print("  未检测到 Claude Code。建议使用“安装/修复”，默认走官方 CMD 原生安装器。")
    elif len(methods) > 1:
        print("  检测到多个安装来源。建议保留一种来源，避免 PATH 中出现旧版本。")
    elif methods[0] == "winget":
        print("  当前像是 WinGet 安装。更新/卸载将使用 winget。")
    elif methods[0] == "npm":
        print("  当前像是 npm 全局安装。更新/卸载将使用 npm。")
    elif methods[0] == "native":
        print("  当前像是官方原生安装。通常会自动更新，也可手动运行 claude update。")
    else:
        print("  找到了 claude 命令，但无法确认来源。更新会优先尝试 claude update。")
    return 0


def run_visible(cmd: List[str], cwd: Optional[Path] = None) -> int:
    print("执行命令:")
    print("  " + " ".join(cmd))
    if cwd:
        print(f"  工作目录: {cwd}")
    try:
        return subprocess.call(cmd, cwd=str(cwd) if cwd else None)
    except FileNotFoundError:
        print(f"未找到命令: {cmd[0]}")
        return 127


def confirm_claude_code_install(method: str, code: int) -> bool:
    if code == 0:
        print(f"{method} 命令已结束，正在确认 claude 是否真的可用...")
    else:
        print(f"{method} 返回错误码 {code}，正在确认是否仍然已经装好...")

    command = claude_code_command()
    if not command:
        print(f"{method} 之后仍未找到 claude 命令，将继续尝试下一种安装方式。")
        return False

    version = claude_code_version()
    if not version:
        print(f"{method} 后找到了 claude 命令，但 claude --version 没有正常返回，将继续尝试下一种安装方式。")
        print_path_info("claude 命令", command)
        return False

    print(f"{method} 已确认可用: {version}")
    return True


def print_claude_code_network_hint() -> None:
    print(
        "提示: 如果上方出现 ECONNREFUSED、Failed to fetch version、Could not resolve host、"
        "SSL/TLS 或 timeout，多半是当前网络/代理连不到 Claude Code 官方发布源，"
        "不代表已经安装成功。脚本会继续尝试 npm 兜底。"
    )


def install_claude_code_native_cmd() -> int:
    if shutil.which("curl.exe") is None:
        print("未找到 curl.exe，跳过官方 CMD 原生安装器。")
        return 127

    with tempfile.TemporaryDirectory(prefix="claude-code-install-") as tmp:
        installer = Path(tmp) / "install.cmd"
        download_code = run_visible(
            [
                "curl.exe",
                "--fail",
                "--silent",
                "--show-error",
                "--location",
                "--url",
                CLAUDE_CODE_NATIVE_CMD_INSTALLER_URL,
                "--output",
                str(installer),
            ]
        )
        if download_code != 0:
            if download_code == 3:
                print(
                    "curl 在下载 install.cmd 前就拒绝了 URL。"
                    "这通常是命令引号解析或代理地址格式异常导致的；脚本已避免使用 cmd 组合命令，"
                    "如果仍出现此错误，请检查 HTTP_PROXY / HTTPS_PROXY 或 curl 配置。"
                )
            return download_code
        if not installer.exists() or installer.stat().st_size == 0:
            print("官方 CMD 安装器下载后为空，跳过执行。")
            return 1
        return run_visible(["cmd.exe", "/d", "/c", "install.cmd"], cwd=Path(tmp))


def install_claude_code() -> int:
    print("安装 / 修复 Claude Code")
    print("将优先使用官方 CMD 原生安装器，避免依赖 PowerShell 安装管道。")
    print("每一步都会在安装后重新检测 claude --version；只有真的可运行才算成功。")
    print("如果 CMD 原生安装器失败，会在可用时回退到 npm。")
    print()
    code = install_claude_code_native_cmd()
    if confirm_claude_code_install("官方 CMD 原生安装器", code):
        return show_claude_code_status()
    print_claude_code_network_hint()

    print("官方 CMD 原生安装器未完成，尝试 npm 安装方式。")
    if shutil.which("npm"):
        node_major = node_major_version()
        if node_major is not None and node_major < 18:
            print(f"检测到 Node.js {node_major}.x，Claude Code npm 安装需要 Node.js 18+，跳过 npm。")
            print("请升级 Node.js，或修复网络/代理后再使用官方 CMD 原生安装器。")
            return 1
        code = run_visible(["npm", "install", "-g", CLAUDE_CODE_NPM_PACKAGE])
        if confirm_claude_code_install("npm 全局安装", code):
            return show_claude_code_status()
    else:
        print("未检测到 npm，跳过 npm 安装方式。")

    print("Claude Code 安装失败。请确认网络/代理可访问 claude.ai、downloads.claude.ai 或 npm registry。")
    print("Windows 原生运行建议安装 Git for Windows；若使用 npm 方式，还需要 Node.js 18+。")
    return code or 1


def update_claude_code() -> int:
    print("更新 Claude Code")
    methods = detect_claude_code_install_methods()
    if not methods:
        print("未检测到 Claude Code，请先安装。")
        return 1
    status = 0
    if "winget" in methods:
        status = run_visible(["winget", "upgrade", "--id", CLAUDE_CODE_WINGET_ID, "--exact", "--accept-package-agreements", "--accept-source-agreements"])
    elif "npm" in methods:
        status = run_visible(["npm", "install", "-g", f"{CLAUDE_CODE_NPM_PACKAGE}@latest"])
    else:
        command = claude_code_command()
        if not command:
            print("找不到 claude 命令，无法执行更新。")
            return 1
        status = run_visible([str(command), "update"])
    if status == 0:
        show_claude_code_status()
    return status


def claude_code_config_paths_for_removal() -> List[Path]:
    return [
        Path.home() / ".claude",
        Path.home() / ".claude.json",
    ]


def remove_allowed_claude_code_path(path: Path, *, strict: bool = True) -> bool:
    allowed_roots = [
        Path.home() / ".local",
        Path.home() / ".claude",
        Path.home(),
        roaming_app_data() / "npm",
    ]
    if not any(is_within(path, root) for root in allowed_roots):
        raise SystemExit(f"Refusing to delete Claude Code path outside allowed roots: {path}")
    return delete_if_exists(path, strict=strict)


def uninstall_claude_code(yes: bool) -> int:
    print("完全卸载 Claude Code")
    show_claude_code_status()
    print("将按检测到的安装来源卸载程序。配置、会话、MCP 和授权数据会单独确认后再删除。")
    if not yes:
        answer = prompt_line("输入 UNINSTALL 继续卸载 Claude Code 程序: ")
        if answer != "UNINSTALL":
            print("已取消。")
            return 0

    methods = detect_claude_code_install_methods()
    statuses: List[int] = []
    if "winget" in methods:
        statuses.append(run_visible(["winget", "uninstall", "--id", CLAUDE_CODE_WINGET_ID, "--exact", "--accept-source-agreements"]))
    if "npm" in methods:
        statuses.append(run_visible(["npm", "uninstall", "-g", CLAUDE_CODE_NPM_PACKAGE]))
    if "native" in methods or not methods:
        for path in [Path.home() / ".local/bin/claude.exe", Path.home() / ".local/bin/claude", Path.home() / ".local/share/claude"]:
            if path.exists():
                remove_allowed_claude_code_path(path)
                print(f"已删除: {path}")

    leftovers = [path for path in claude_code_command_paths() if path.exists()]
    if leftovers:
        print("仍检测到 claude 命令残留，可能来自另一个安装来源:")
        for path in leftovers:
            print_path_info("残留 claude 命令", path)

    config_paths = [path for path in claude_code_config_paths_for_removal() if path.exists()]
    if config_paths:
        print()
        print("以下配置/状态数据会影响 Claude Code 会话、MCP、授权和项目偏好:")
        for path in config_paths:
            print_path_info("Claude Code 配置", path)
        delete_config = "DELETECONFIG" if yes else prompt_line("如需同时删除这些配置，输入 DELETECONFIG；直接回车则保留: ")
        if delete_config == "DELETECONFIG":
            for path in config_paths:
                if remove_allowed_claude_code_path(path, strict=False):
                    print(f"已删除: {path}")
                else:
                    print(f"未能完全删除，可能有只读或占用文件残留: {path}")
        else:
            print("已保留 Claude Code 配置/状态数据。")

    failed = [status for status in statuses if status not in {0, -1978335189}]
    return failed[0] if failed else 0


def create_launcher(target_dir: Path) -> Path:
    exe = app_exe(target_dir.expanduser())
    if not exe:
        raise SystemExit(f"Cannot find patched Claude.exe in: {target_dir}")

    launcher = launcher_path()
    launcher.parent.mkdir(parents=True, exist_ok=True)
    portable_user_data_dir().mkdir(parents=True, exist_ok=True)
    exe_path = str(exe).replace('"', '""')
    working_dir = str(exe.parent).replace('"', '""')
    svc_path = str(exe.parent / "resources" / "cowork-svc.exe").replace('"', '""')
    user_data_dir = str(portable_user_data_dir()).replace('"', '""')
    content = f'''Set shell = CreateObject("WScript.Shell")
Set env = shell.Environment("PROCESS")
env("{COWORK_PORTABLE_ENV}") = "1"
env("{CLAUDE_USER_DATA_DIR_ENV}") = "{user_data_dir}"
shell.CurrentDirectory = "{working_dir}"
Set fso = CreateObject("Scripting.FileSystemObject")
q = Chr(34)
exePath = "{exe_path}"
svcPath = "{svc_path}"
userDataDir = "{user_data_dir}"
pipePath = "\\\\.\\pipe\\{COWORK_PORTABLE_PIPE_NAME}"
If fso.FileExists(svcPath) Then
  If Not fso.FileExists(pipePath) Then
    On Error Resume Next
    shell.Run q & svcPath & q, 0, False
    On Error GoTo 0
  End If
  For i = 1 To 30
    If fso.FileExists(pipePath) Then Exit For
    WScript.Sleep 500
  Next
End If
command = q & exePath & q & " --user-data-dir=" & q & userDataDir & q
For i = 0 To WScript.Arguments.Count - 1
  command = command & " " & q & WScript.Arguments(i) & q
Next
shell.Run command, 1, False
'''
    launcher.write_text(content, encoding="utf-8")
    print(f"已创建 / 更新汉化版启动器: {launcher}")
    return launcher


def protocol_backup_dir() -> Path:
    return tool_root() / OAUTH_BACKUP_DIRNAME


def read_oauth_protocol_command() -> Optional[str]:
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{OAUTH_REG_PATH}\shell\open\command") as key:
            value, _ = winreg.QueryValueEx(key, "")
            return str(value)
    except OSError:
        return None


def backup_oauth_protocol(reason: str) -> Optional[Path]:
    current = read_oauth_protocol_command()
    if current is None:
        print(f"没有找到 HKCU 下的 {OAUTH_PROTOCOL}:// 回调处理器，无需备份。")
        return None
    protocol_backup_dir().mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = unique_backup_path(protocol_backup_dir() / f"{OAUTH_PROTOCOL}-protocol-{reason}-{stamp}.json")
    save_json(
        backup,
        {
            "createdAt": dt.datetime.now().isoformat(timespec="seconds"),
            "protocol": OAUTH_PROTOCOL,
            "command": current,
        },
    )
    print(f"已备份 {OAUTH_PROTOCOL}:// 回调处理器: {backup}")
    return backup


def set_oauth_protocol_to_launcher(target_dir: Path) -> int:
    if winreg is None:
        print("当前 Python 运行环境无法访问 Windows 注册表。")
        return 1
    launcher = create_launcher(target_dir)
    backup_oauth_protocol("before-zh-cn")
    win_root = os.environ.get("SystemRoot") or r"C:\Windows"
    wscript_path = Path(win_root) / "System32" / "wscript.exe"
    command = f'"{wscript_path}" "{launcher}" "%1"'
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, OAUTH_REG_PATH) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f"URL:{OAUTH_PROTOCOL}")
        winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{OAUTH_REG_PATH}\shell\open\command") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, command)
    print(f"已将 {OAUTH_PROTOCOL}:// 回调临时指向汉化版启动器:")
    print(f"  {command}")
    return 0


def latest_oauth_protocol_backup() -> Optional[Path]:
    backups = sorted(protocol_backup_dir().glob(f"{OAUTH_PROTOCOL}-protocol-*.json"), reverse=True)
    return backups[0] if backups else None


def restore_oauth_protocol(backup_path: Optional[Path] = None) -> int:
    if winreg is None:
        print("当前 Python 运行环境无法访问 Windows 注册表。")
        return 1
    backup = backup_path or latest_oauth_protocol_backup()
    if not backup or not backup.exists():
        print("没有找到可恢复的回调处理器备份。")
        return 1
    data = load_json_dict(backup, label="OAuth protocol backup")
    command = nonempty_string(data.get("command"))
    if not command:
        print(f"备份文件中没有有效命令: {backup}")
        return 1
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, OAUTH_REG_PATH) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f"URL:{OAUTH_PROTOCOL}")
        winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{OAUTH_REG_PATH}\shell\open\command") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, command)
    print(f"已从备份恢复 {OAUTH_PROTOCOL}:// 回调处理器: {backup}")
    print(f"  {command}")
    return 0


def show_oauth_protocol() -> int:
    print(f"当前 {OAUTH_PROTOCOL}:// 回调处理器:")
    command = read_oauth_protocol_command()
    print(f"  {command or 'HKCU 中未设置'}")
    backup = latest_oauth_protocol_backup()
    print(f"最近一次备份: {backup or '未找到'}")
    return 0


def oauth_login_prepare(target_dir: Path) -> int:
    print("汉化版 OAuth 登录修复")
    print("浏览器登录前请关闭官方 Claude，避免登录回调被错误窗口接走。")
    return set_oauth_protocol_to_launcher(target_dir)


def create_windows_shortcut(
    shortcut: Path,
    target: Path,
    description: str,
    *,
    arguments: Optional[str] = None,
    working_directory: Optional[Path] = None,
    icon: Optional[Path] = None,
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


def create_shortcuts(target_dir: Path, dry_run: bool = False) -> int:
    exe = app_exe(target_dir.expanduser())
    if not exe:
        raise SystemExit(f"Cannot find patched Claude.exe in: {target_dir}")

    if dry_run:
        launcher = launcher_path()
        print(f"[dry-run] Would create launcher: {launcher}")
        print(f"[dry-run] Would use portable user data: {portable_user_data_dir()}")
        wscript = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/wscript.exe"
        for label, shortcut in shortcut_paths().items():
            print(f"[dry-run] Would create {label} shortcut: {shortcut} -> {wscript} \"{launcher}\"")

        claude_code = claude_code_command()
        if not claude_code:
            print("未找到 Claude Code 命令，将跳过 Claude Code 快捷方式。")
            return 0

        cmd = Path(os.environ.get("ComSpec") or (Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/cmd.exe"))
        for label, shortcut in claude_code_shortcut_paths().items():
            print(f"[dry-run] Would create {label} shortcut: {shortcut} -> {cmd} /k \"{claude_code}\"")
        return 0

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
        print(f"已创建 {label} 快捷方式: {shortcut}")

    claude_code = claude_code_command()
    if not claude_code:
        print("未找到 Claude Code 命令，将跳过 Claude Code 快捷方式。")
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
        print(f"已创建 {label} 快捷方式: {shortcut}")
    return 0


def retry_remove_after_chmod(func: Any, path: str, exc_info: Any) -> None:
    try:
        os.chmod(path, 0o700)
        func(path)
    except Exception:
        raise


def delete_if_exists(path: Path, *, strict: bool = True) -> bool:
    if not path.exists():
        return False
    try:
        if path.is_dir():
            shutil.rmtree(path, onerror=retry_remove_after_chmod)
        else:
            try:
                path.unlink()
            except PermissionError:
                os.chmod(path, 0o700)
                path.unlink()
        return True
    except OSError as exc:
        if strict:
            raise
        print(f"警告：无法删除 {path}: {exc}")
        return False


def full_clean(target_dir: Path, yes: bool) -> int:
    targets = [
        ("patched app", target_dir.expanduser()),
        ("launcher", launcher_path()),
        ("download cache", tool_root() / "downloads"),
        *[(label, path) for label, path in shortcut_paths().items()],
        *[(label, path) for label, path in legacy_shortcut_paths().items()],
        *[(label, path) for label, path in claude_code_shortcut_paths().items()],
    ]

    print("The following zh-CN tool files will be permanently deleted if they exist:")
    for label, path in targets:
        print_path_info(label, path)

    print()
    print("这不会删除 Claude 用户配置 / 账号数据，也不会删除 user-data-backups。账号数据请使用 --clean-user-data 清理。")
    if not yes:
        answer = input("Type DELETE to continue: ").strip()
        if answer != "DELETE":
            print("已取消。")
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
        print("没有找到 Claude 用户配置 / 账号数据路径。")
        return 0

    print("以下 Claude 用户配置 / 账号数据将移动到备份目录:")
    for path in existing:
        print(f"  {path} ({format_size(path_size(path))})")

    print()
    print("这会退出 Claude 登录并重置本地应用状态，但会保留备份。")
    if not yes:
        answer = input("Type DELETE to continue: ").strip()
        if answer != "DELETE":
            print("已取消。")
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

    print(f"已移动 {moved} 个路径到备份目录: {backup_root}")
    print("Run Claude again to create a fresh user profile.")
    return 0


def download_latest_msix(download_dir: Path) -> Path:
    download_dir.mkdir(parents=True, exist_ok=True)
    target = download_dir / "Claude-latest.msix"
    tmp = target.with_suffix(target.suffix + ".tmp")
    print(f"正在下载最新 Claude Desktop MSIX 到: {target}")
    request = urllib.request.Request(LATEST_MSIX_URL, headers=DOWNLOAD_HEADERS)
    try:
        with urllib.request.urlopen(request) as response, tmp.open("wb") as f:
            shutil.copyfileobj(response, f)
    except Exception as exc:
        print(f"Python 下载失败: {exc}")
        print("Retrying download with PowerShell...")
        download_latest_msix_with_powershell(tmp)
    os.replace(tmp, target)
    if not validate_msix_archive(target, require_app=True):
        message = invalid_msix_message(target, "刚下载的文件不是有效的 Claude Desktop MSIX。")
        try:
            target.unlink()
            message += "\n已删除无效下载缓存；请检查网络/代理后重试。"
        except OSError:
            message += "\n无法删除无效下载缓存；请手动删除后重试。"
        raise SystemExit(message)
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
        raise SystemExit(result.stdout.strip() or "PowerShell 下载失败。")


def text_preview(path: Path, limit: int = 300) -> str:
    try:
        data = path.read_bytes()[:limit]
    except OSError:
        return ""
    text = data.decode("utf-8", errors="ignore")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def validate_msix_archive(path: Path, *, require_app: bool) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    if not zipfile.is_zipfile(path):
        return False
    try:
        with zipfile.ZipFile(path) as archive:
            if archive.testzip() is not None:
                return False
            if require_app and not any(info.filename.startswith("app/") and not info.is_dir() for info in archive.infolist()):
                return False
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile):
        return False
    return True


def invalid_msix_message(path: Path, headline: str) -> str:
    size = format_size(path_size(path)) if path.exists() else "0 B"
    preview = text_preview(path)
    lines = [
        headline,
        f"文件: {path}",
        f"大小: {size}",
        "",
        "这通常不是汉化脚本本身的问题，而是下载没有拿到真正的 MSIX 安装包。",
        "常见原因：网络不通、代理/VPN 拦截、TLS/证书问题、下载地址返回 HTML 错误页或重定向页。",
    ]
    if preview:
        lines.extend(["", f"文件开头看起来像: {preview}"])
    lines.extend(
        [
            "",
            "建议：",
            "  1. 检查网络、代理/VPN 或防火墙后重试。",
            "  2. 如果浏览器能下载官方 Claude Desktop MSIX，可用 --source 指向该 MSIX。",
            "  3. 不要把错误页改名成 .msix；MSIX 本质上必须是 ZIP 格式安装包。",
        ]
    )
    return "\n".join(lines)


def backup_existing_target(target: Path, dry_run: bool) -> Optional[Path]:
    if not target.exists():
        return None
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = unique_backup_path(target.with_name(f"{target.name}.backup-before-zh-CN-{stamp}"))
    if dry_run:
        print(f"[dry-run] Would move existing target {target} -> {backup}")
        return backup
    print(f"正在备份已有目标目录: {backup}")
    shutil.move(str(target), str(backup))
    return backup


def copy_app_dir(source_app_dir: Path, target_dir: Path, dry_run: bool) -> None:
    backup_existing_target(target_dir, dry_run)
    if dry_run:
        print(f"[dry-run] Would copy {source_app_dir} -> {target_dir}")
        return
    print(f"正在复制 Claude 应用文件: {source_app_dir} -> {target_dir}")
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_app_dir, target_dir)
    normalize_percent_encoded_paths(target_dir, dry_run=False)


def decoded_msix_part(part: str) -> str:
    decoded = urllib.parse.unquote(part)
    if not decoded or decoded in {".", ".."} or "/" in decoded or "\\" in decoded:
        raise SystemExit(f"Unsafe decoded MSIX path segment: {part!r}")
    return decoded


def merge_or_move_path(source: Path, target: Path) -> None:
    if source == target or not source.exists():
        return

    if source.is_dir():
        target.mkdir(parents=True, exist_ok=True)
        for child in list(source.iterdir()):
            merge_or_move_path(child, target / child.name)
        source.rmdir()
        return

    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.is_file() and source.stat().st_size == target.stat().st_size and file_sha256(source) == file_sha256(target):
            source.unlink()
            return
        duplicate = unique_backup_path(source.with_name(f"{source.name}.duplicate-before-path-decode"))
        source.rename(duplicate)
        print(f"Kept duplicate encoded file instead of overwriting: {duplicate}")
        return

    source.rename(target)


def normalize_percent_encoded_paths(root: Path, dry_run: bool = False) -> int:
    if not root.exists():
        return 0

    changed = 0
    paths = sorted(root.rglob("*"), key=lambda item: len(item.parts))
    for path in paths:
        if not path.exists():
            continue
        decoded_name = urllib.parse.unquote(path.name)
        if decoded_name == path.name:
            continue
        if not decoded_name or decoded_name in {".", ".."} or "/" in decoded_name or "\\" in decoded_name:
            raise SystemExit(f"Unsafe decoded path name: {path}")
        target = path.with_name(decoded_name)
        if dry_run:
            print(f"[dry-run] Would normalize encoded path: {path} -> {target}")
        else:
            merge_or_move_path(path, target)
            print(f"已修正编码路径: {path} -> {target}")
        changed += 1

    if changed:
        print(f"Normalized {changed} percent-encoded path(s).")
    return changed


def safe_extract_msix_app(msix: Path, target_dir: Path, dry_run: bool) -> None:
    if not dry_run and not validate_msix_archive(msix, require_app=True):
        raise SystemExit(invalid_msix_message(msix, "无法解包：这个文件不是有效的 Claude Desktop MSIX。"))
    backup_existing_target(target_dir, dry_run)
    if dry_run:
        print(f"[dry-run] Would extract app/ from {msix} -> {target_dir}")
        return

    print(f"正在从 MSIX 解包 app/: {msix} -> {target_dir}")
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    target_root = target_dir.resolve()
    try:
        archive = zipfile.ZipFile(msix)
    except zipfile.BadZipFile:
        raise SystemExit(invalid_msix_message(msix, "无法解包：这个文件不是有效的 ZIP/MSIX。")) from None
    with archive:
        app_members = [m for m in archive.infolist() if m.filename.startswith("app/") and not m.is_dir()]
        if not app_members:
            raise SystemExit(f"MSIX does not contain app/ files: {msix}")

        for info in app_members:
            rel_posix = PurePosixPath(info.filename).relative_to("app")
            rel_path = Path(*(decoded_msix_part(part) for part in rel_posix.parts))
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
            print(f"语言白名单已包含 zh-CN: {path.name}")
            return path
        if LANG_LIST_RE.search(text):
            patched = LANG_LIST_RE.sub(
                '["en-US","de-DE","fr-FR","ko-KR","ja-JP","es-419","es-ES","it-IT","hi-IN","pt-BR","id-ID","zh-CN"]',
                text,
                count=1,
            )
            path.write_text(patched, encoding="utf-8")
            remove_zst_sibling(path)
            print(f"已写入 zh-CN 语言白名单: {path.name}")
            return path

    raise SystemExit("Could not patch language whitelist. Claude's bundle format may have changed.")


def patch_hardcoded_frontend_strings(app_dir: Path) -> None:
    assets_dir = app_dir / FRONTEND_ASSETS_REL
    replacements = {
        '"New task"': '"新建任务"',
        '"New session"': '"新会话"',
        '"New session[新会话]"': '"新会话"',
        '"Search Ctrl+K"': '"搜索 Ctrl+K"',
        '"Collapse sidebar Ctrl+B"': '"收起侧边栏 Ctrl+B"',
        '"Chat session started"': '"聊天会话已开始"',
        'children:"Tasks"': 'children:"任务"',
        'children:"Active"': 'children:"活跃"',
        'children:"Archived"': 'children:"已归档"',
        'children:"All"': 'children:"全部"',
        'children:"Chat session started"': 'children:"聊天会话已开始"',
        'aria-label:"Search"': 'aria-label:"搜索"',
        'title:"Search"': 'title:"搜索"',
        'aria-label:"Collapse sidebar"': 'aria-label:"收起侧边栏"',
        'title:"Collapse sidebar"': 'title:"收起侧边栏"',
        'aria-label:"Back"': 'aria-label:"返回"',
        'title:"Back"': 'title:"返回"',
        '"Claude for Windows"': '"Claude Windows 版"',
        '"The fastest way to talk with Claude"': '"与 Claude 对话的最快方式"',
        '"Get started"': '"开始使用"',
        '"Sign In"': '"登录"',
        '"Continue with Google"': '"使用 Google 继续"',
        '"Continue with email"': '"使用邮箱继续"',
        '"Enter your email"': '"输入你的邮箱"',
        '"Write a message..."': '"输入消息..."',
        '"Write a message…"': '"输入消息..."',
        '"Legacy Model"': '"旧版模型"',
        '"By continuing, you acknowledge Anthropic’s Privacy Policy."': '"继续即表示你已知晓 Anthropic 的隐私政策。"',
        '"By continuing, you acknowledge Anthropic’s Privacy Policy(opens in a new tab)."': '"继续即表示你已知晓 Anthropic 的隐私政策。"',
        '"By continuing, you acknowledge Anthropic’s Privacy Policy (opens in a new tab)."': '"继续即表示你已知晓 Anthropic 的隐私政策。"',
        '"By continuing, you acknowledge Anthropic’s "': '"继续即表示你已知晓 Anthropic 的 "',
        '"By continuing, you acknowledge Anthropic\'s "': '"继续即表示你已知晓 Anthropic 的 "',
        '"You can change this later by signing out."': '"退出登陆后，你稍后可以更改此选择。"',
        '"Sign out"': '"退出登陆"',
        '"Sign Out"': '"退出登陆"',
        '"Or continue with Gateway"': '"或继续使用 API 模式使用"',
        '"Continue with Gateway"': '"继续使用 API 模式"',
        '"Avatar"': '"头像"',
        '"Instructions for Claude"': '"给 Claude 的指令"',
        '"Preferences"': '"偏好设置"',
        '"Local sessions"': '"本地会话"',
        '"Artifacts"': '"作品"',
        '"Artifact"': '"作品"',
        '"Browse skills"': '"浏览技能"',
        '"Create Skill"': '"创建技能"',
        '"Write skill instructions"': '"编写技能说明"',
        '"Upload skill"': '"上传技能"',
        '"Built-in skills"': '"内置技能"',
        '"Built-in Skills"': '"内置技能"',
        '"Create a scheduled task that can be run on demand or automatically on an interval."': '"创建一个计划任务，可按需手动运行，也可按固定间隔自动运行。"',
        '"Set up Cowork so Claude can work with your desktop, files, apps, and browser."': '"设置 Cowork，让 Claude 能使用你的桌面、文件、应用和浏览器工作。"',
        '"Consolidate and organize memory from your conversations so Claude can retain useful context."': '"整理并归纳对话记忆，让 Claude 保留有用的上下文。"',
        '"Provide relevant context from your workspace and conversations when Claude needs it."': '"在 Claude 需要时提供工作区和对话中的相关上下文。"',
        'children:"Browse skills"': 'children:"浏览技能"',
        'children:"Create Skill"': 'children:"创建技能"',
        'children:"Write skill instructions"': 'children:"编写技能说明"',
        'children:"Upload skill"': 'children:"上传技能"',
        'children:"Built-in skills"': 'children:"内置技能"',
        'children:"Built-in Skills"': 'children:"内置技能"',
        'children:"schedule"': 'children:"计划任务"',
        'children:"setup-cowork"': 'children:"设置 Cowork"',
        'children:"consolidate-memory"': 'children:"整理记忆"',
        'children:"context"': 'children:"上下文"',
        '"Pull requests"': '"拉取请求"',
        '"Running"': '"正在运行"',
        '"Ran"': '"已执行"',
        '"Run"': '"运行"',
        '"Request permissions"': '"执行前询问"',
        '"Accept edits"': '"自动应用编辑"',
        '"Plan mode"': '"仅计划"',
        '"Bypass permissions"': '"跳过确认"',
        '"Always allow in this project (local)"': '"在此项目中始终允许（本地）"',
        '"Allow once"': '"允许一次"',
        '"Reject"': '"拒绝"',
        'You’re running Claude through your organization’s own inference provider': '你正在通过组织自己的推理提供方运行 Claude',
        "You're running Claude through your organization's own inference provider": '你正在通过组织自己的推理提供方运行 Claude',
        'Your conversations are sent there, not to Anthropic, and are governed by your organization’s agreement with that provider.': '你的对话会发送到该提供方，而不是 Anthropic，并受你所在组织与该提供方协议约束。',
        "Your conversations are sent there, not to Anthropic, and are governed by your organization's agreement with that provider.": '你的对话会发送到该提供方，而不是 Anthropic，并受你所在组织与该提供方协议约束。',
        'Claude will keep these in mind across chats and Cowork within Anthropic’s guidelines. Learn more': 'Claude 会在聊天和协作中记住这些内容，并遵守 Anthropic 的使用准则。了解更多',
        "Claude will keep these in mind across chats and Cowork within Anthropic's guidelines. Learn more": 'Claude 会在聊天和协作中记住这些内容，并遵守 Anthropic 的使用准则。了解更多',
        'Get notified when Claude has finished a response. Useful for long-running tasks.': 'Claude 完成回复时通知你，适合耗时较长的任务。',
        'What Anthropic doesn’t see': 'Anthropic 看不到的内容',
        "What Anthropic doesn't see": 'Anthropic 看不到的内容',
        'Your prompts, Claude’s responses, or any conversation content': '你的提示词、Claude 的回复或任何对话内容',
        "Your prompts, Claude's responses, or any conversation content": '你的提示词、Claude 的回复或任何对话内容',
        'Your files, code, or workspace contents': '你的文件、代码或工作区内容',
        'Your identity or account details': '你的身份或账号详情',
        'What Anthropic may receive (configured by your organization)': 'Anthropic 可能收到的内容（由你的组织配置）',
        'Crash reports and error diagnostics, so we can fix bugs': '崩溃报告和错误诊断，用于修复问题',
        'Anonymous usage metrics including usage counts (not conversation content)': '匿名使用指标，包括使用次数统计（不含对话内容）',
        'Update-check requests, so the app can stay current': '更新检查请求，用于保持应用版本最新',
        'A diagnostic report, only if you explicitly choose “Send to Anthropic”': '诊断报告，仅在你明确选择“发送给 Anthropic”时发送',
        'Generate code, documents, and designs in a dedicated window alongside your conversation.': '在对话旁边的独立窗口中生成代码、文档和设计。',
        'Skills have moved to Customize.': '技能已移至“自定义”。',
        'Connectors have moved to Customize.': '连接器已移至“自定义”。',
        'Your organization hasn’t enabled any connectors': '你的组织尚未启用任何连接器',
        "Your organization hasn't enabled any connectors": '你的组织尚未启用任何连接器',
        'When Claude pushes changes to a branch, it automatically opens a pull request without asking first. Applies to remote sessions only.': '当 Claude 将更改推送到分支时，会自动创建拉取请求，不再事先询问。仅适用于远程会话。',
        'Inside project (.claude/worktrees)': '项目内（.claude/worktrees）',
        'Inside project (.claude/worktree)': '项目内（.claude/worktree）',
        '"Claude will keep these in mind across chats and Cowork within Anthropic\'s guidelines. Learn more"': '"Claude 会在聊天和协作中记住这些内容，并遵守 Anthropic 的使用准则。了解更多"',
        '"Claude will keep these in mind across chats and Cowork within Anthropic’s guidelines. Learn more"': '"Claude 会在聊天和协作中记住这些内容，并遵守 Anthropic 的使用准则。了解更多"',
        '"Get notified when Claude has finished a response. Useful for long-running tasks."': '"Claude 完成回复时通知你，适合耗时较长的任务。"',
        '"You’re running Claude through your organization’s own inference provider"': '"你正在通过组织自己的推理提供方运行 Claude"',
        '"You\'re running Claude through your organization\'s own inference provider"': '"你正在通过组织自己的推理提供方运行 Claude"',
        '"Your conversations are sent there, not to Anthropic, and are governed by your organization’s agreement with that provider."': '"你的对话会发送到该提供方，而不是 Anthropic，并受你所在组织与该提供方协议约束。"',
        '"Your conversations are sent there, not to Anthropic, and are governed by your organization\'s agreement with that provider."': '"你的对话会发送到该提供方，而不是 Anthropic，并受你所在组织与该提供方协议约束。"',
        '"What Anthropic doesn’t see"': '"Anthropic 看不到的内容"',
        '"What Anthropic doesn\'t see"': '"Anthropic 看不到的内容"',
        '"Your prompts, Claude’s responses, or any conversation content"': '"你的提示词、Claude 的回复或任何对话内容"',
        '"Your prompts, Claude\'s responses, or any conversation content"': '"你的提示词、Claude 的回复或任何对话内容"',
        '"Your files, code, or workspace contents"': '"你的文件、代码或工作区内容"',
        '"Your identity or account details"': '"你的身份或账号详情"',
        '"What Anthropic may receive (configured by your organization)"': '"Anthropic 可能收到的内容（由你的组织配置）"',
        '"Crash reports and error diagnostics, so we can fix bugs"': '"崩溃报告和错误诊断，用于修复问题"',
        '"Anonymous usage metrics including usage counts (not conversation content)"': '"匿名使用指标，包括使用次数统计（不含对话内容）"',
        '"Update-check requests, so the app can stay current"': '"更新检查请求，用于保持应用版本最新"',
        '"A diagnostic report, only if you explicitly choose “Send to Anthropic”"': '"诊断报告，仅在你明确选择“发送给 Anthropic”时发送"',
        '"Generate code, documents, and designs in a dedicated window alongside your conversation."': '"在对话旁边的独立窗口中生成代码、文档和设计。"',
        '"Skills have moved to Customize."': '"技能已移至“自定义”。"',
        '"Connectors have moved to Customize."': '"连接器已移至“自定义”。"',
        '"Your organization hasn’t enabled any connectors"': '"你的组织尚未启用任何连接器"',
        '"Your organization hasn\'t enabled any connectors"': '"你的组织尚未启用任何连接器"',
        '"When Claude pushes changes to a branch, it automatically opens a pull request without asking first. Applies to remote sessions only."': '"当 Claude 将更改推送到分支时，会自动创建拉取请求，不再事先询问。仅适用于远程会话。"',
        '"Inside project (.claude/worktrees)"': '"项目内（.claude/worktrees）"',
        '"Inside project (.claude/worktree)"': '"项目内（.claude/worktree）"',
        '"Privacy Policy"': '"隐私政策"',
        '"Privacy Policy."': '"隐私政策。"',
        '"Privacy Policy(opens in a new tab)"': '"隐私政策"',
        '"Privacy Policy (opens in a new tab)"': '"隐私政策"',
        '"Download Claude for Windows"': '"下载 Claude Windows 版"',
        "children:\"OR\"": "children:\"或\"",
        'children:"OR"': 'children:"或"',
        'label:"Cowork",ariaLabel:"Cowork"': 'label:"协作",ariaLabel:"协作"',
        'label:"Cowork[协作]",ariaLabel:"Cowork[协作]"': 'label:"协作",ariaLabel:"协作"',
        'label:"Code[代码]",ariaLabel:"Code[代码]"': 'label:"代码",ariaLabel:"代码"',
        'label:"Cowork[协作]"},code:{mode:"code",icon:"Code",label:"Code[代码]"': 'label:"协作"},code:{mode:"code",icon:"Code",label:"代码"',
        'label:"Cowork"},code:{mode:"code",icon:"Code",label:"Code"': 'label:"协作"},code:{mode:"code",icon:"Code",label:"代码"',
        '"Projects"': '"项目"',
        '"Scheduled"': '"计划任务"',
        '"Customize"': '"自定义"',
        '"Collaborate"': '"协作"',
        '"Code"': '"代码"',
        '"Drag to pin"': '"拖到此处固定"',
        '"Drop here"': '"拖到此处"',
        '"Let go"': '"松开"',
        '"Recents"': '"最近使用"',
        '"View all"': '"查看全部"',
        '"Pinned"': '"已固定"',
        'label:"Pinned"': 'label:"已固定"',
        '"New project"': '"新建项目"',
        '"新项目"': '"新建项目"',
        'Ge={yours:"Your projects",team:"Team",shared:"Shared with you"}': 'Ge={yours:"你的项目",team:"团队",shared:"与你共享"}',
        "es={yours:\"You don't have any projects yet.\",team:\"No team projects yet.\",shared:\"No projects have been shared with you.\"}": 'es={yours:"你还没有项目。",team:"暂无团队项目。",shared:"暂无与你共享的项目。"}',
        'as={recent:"Recent",created:"Created",alphabetical:"Alphabetical"}': 'as={recent:"最近",created:"创建时间",alphabetical:"字母顺序"}',
        'return"chatProject"===e.kind?"Chat project":"Space"': 'return"chatProject"===e.kind?"聊天项目":"空间"',
        'children:"No projects match your search."': 'children:"没有匹配搜索的项目。"',
        'placeholder:"Search projects"': 'placeholder:"搜索项目"',
        '"aria-label":"Sort by"': '"aria-label":"排序方式"',
        'children:"Shared"})': 'children:"已共享"})',
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
        'description:"Hosts your network firewall must allow, derived from your current settings. This list is read-only and updates as you make changes. Traffic is HTTPS on port 443 unless a custom port is specified (OTLP, gateway, or MCP server URLs)."': 'description:"根据当前设置推导出的网络防火墙放行主机列表。此列表只读，并会随配置变化更新。除非 OTLP、API 服务或 MCP 服务器 URL 指定了自定义端口，否则流量使用 443 端口的 HTTPS。"',
        'group:"Updates"': 'group:"更新"',
        'group:"Identity & models"': 'group:"身份与模型"',
        'group:"Bootstrap config URL"': 'group:"引导配置 URL"',
        'group:"Extensions"': 'group:"扩展"',
        'group:"MCP servers"': 'group:"MCP 服务器"',
        'group:"Anthropic telemetry"': 'group:"Anthropic 遥测"',
        'title:"Allow desktop extensions"': 'title:"允许桌面扩展"',
        'title:"Show extension directory"': 'title:"显示扩展目录"',
        'title:"Require signed extensions"': 'title:"要求扩展签名"',
        'title:"Allow user-added MCP servers"': 'title:"允许用户添加 MCP 服务器"',
        'title:"Allow Claude Code tab"': 'title:"允许 Claude Code 标签页"',
        'title:"Secure VM features"': 'title:"安全 VM 功能"',
        'title:"Require full VM sandbox"': 'title:"强制完整 VM 沙盒"',
        'title:"Allowed egress hosts"': 'title:"允许出站主机"',
        'title:"OpenTelemetry collector endpoint"': 'title:"OpenTelemetry 收集器端点"',
        'title:"OpenTelemetry exporter protocol"': 'title:"OpenTelemetry 导出协议"',
        'title:"OpenTelemetry exporter headers"': 'title:"OpenTelemetry 导出请求头"',
        'title:"Auto-update enforcement window"': 'title:"自动更新强制窗口"',
        'title:"Block auto-updates"': 'title:"阻止自动更新"',
        'title:"Skip login-mode chooser"': 'title:"直进 API 模式"',
        'title:"Required organization"': 'title:"限定组织"',
        'title:"Inference provider"': 'title:"推理提供方"',
        'title:"Gateway base URL"': 'title:"API 地址"',
        'title:"Gateway API key"': 'title:"API 密钥"',
        'title:"Gateway auth scheme"': 'title:"认证方式"',
        'title:"Gateway extra headers"': 'title:"额外请求头"',
        'title:"GCP project ID"': 'title:"GCP 项目 ID"',
        'title:"GCP region"': 'title:"GCP 区域"',
        'title:"GCP credentials file path"': 'title:"GCP 凭据文件路径"',
        'title:"Vertex OAuth client ID"': 'title:"Vertex OAuth 客户端 ID"',
        'title:"Vertex OAuth client secret"': 'title:"Vertex OAuth 客户端密钥"',
        'title:"Vertex OAuth scopes"': 'title:"Vertex OAuth 权限范围"',
        'title:"Vertex AI base URL"': 'title:"Vertex AI 基础 URL"',
        'title:"AWS region"': 'title:"AWS 区域"',
        'title:"AWS bearer token"': 'title:"AWS Bearer 访问令牌"',
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
        'title:"Managed MCP servers"': 'title:"托管 MCP 服务器"',
        'title:"Disabled built-in tools"': 'title:"停用内置工具"',
        'title:"Allowed workspace folders"': 'title:"允许的工作区文件夹"',
        'title:"Credential helper script"': 'title:"凭据辅助脚本"',
        'title:"Credential helper TTL"': 'title:"凭据辅助缓存时间"',
        'title:"Use bootstrap config"': 'title:"使用引导配置"',
        'title:"Bootstrap config URL"': 'title:"引导配置 URL"',
        'title:"Bootstrap OIDC parameters"': 'title:"引导 OIDC 参数"',
        'title:"Max tokens per window"': 'title:"每个窗口最大词元数[token]"',
        'title:"Token cap window"': 'title:"token上限窗口"',
        'title:"每个窗口最大令牌数"': 'title:"每个窗口最大词元数[token]"',
        'title:"令牌上限窗口"': 'title:"token上限窗口"',
        'description:"Permit users to install local desktop extensions (.dxt/.mcpb)."': 'description:"允许用户安装本地桌面扩展（.dxt/.mcpb）。"',
        'description:"Show the Anthropic extension directory in the connectors UI."': 'description:"在连接器界面中显示 Anthropic 扩展目录。"',
        'description:"Reject desktop extensions that are not signed by a trusted publisher."': 'description:"拒绝未由受信任发布者签名的桌面扩展。"',
        'description:"Permit users to add their own local (stdio) MCP servers via Developer settings. HTTP/SSE servers are managed separately. When false, only servers from the Managed MCP servers list and org-provisioned plugins are available."': 'description:"允许用户通过开发者设置添加自己的本地（stdio）MCP 服务器。HTTP/SSE 服务器会单独管理。关闭时，只有“托管 MCP 服务器”列表和组织预置插件中的服务器可用。"',
        'description:"Show the Code tab (terminal-based coding sessions). Sessions run on the host, not inside the VM."': 'description:"显示 Code 标签页（基于终端的编码会话）。会话在主机上运行，而不是在 VM 内运行。"',
        'description:"Forces the agent loop, file/web tools, and plugin-bundled MCPs to run inside the VM, disabling host-loop mode."': 'description:"强制代理循环、文件/网页工具和插件内置 MCP 在 VM 内运行，并停用主机循环模式。"',
        'description:"Base URL of an OpenTelemetry collector. When set, Cowork sessions export logs and metrics (prompts, tool calls, token counts) to this endpoint via OTLP. The endpoint host is automatically added to the session network allowlist."': 'description:"OpenTelemetry 收集器的基础 URL。设置后，Cowork 会话会通过 OTLP 将日志和指标（提示词、工具调用、token计数）导出到此端点。该端点主机会自动加入会话网络允许列表。"',
        'description:"OpenTelemetry 收集器的基础 URL。设置后，Cowork 会话会通过 OTLP 将日志和指标（提示词、工具调用、令牌计数）导出到此端点。该端点主机会自动加入会话网络允许列表。"': 'description:"OpenTelemetry 收集器的基础 URL。设置后，Cowork 会话会通过 OTLP 将日志和指标（提示词、工具调用、token计数）导出到此端点。该端点主机会自动加入会话网络允许列表。"',
        'description:"OTLP wire protocol used to reach the collector. Defaults to http/protobuf when otlpEndpoint is set."': 'description:"连接收集器所用的 OTLP 传输协议。设置 otlpEndpoint 时默认使用 http/protobuf。"',
        'description:"Headers sent with every OTLP request, as comma-separated key=value pairs (the standard OTEL_EXPORTER_OTLP_HEADERS format)."': 'description:"每个 OTLP 请求都会发送的请求头，以逗号分隔的 key=value 形式填写（标准 OTEL_EXPORTER_OTLP_HEADERS 格式）。"',
        'description:"When set, forces a pending update to install after this many hours regardless of user activity. When unset, the app uses a 72-hour window but defers installation while the user is active."': 'description:"设置后，待安装更新会在指定小时数后强制安装，不再考虑用户是否正在使用。未设置时，应用使用 72 小时窗口，并会在用户活跃时延后安装。"',
        'description:"Blocks the app from checking for and downloading updates from Anthropic. The app will stay on its installed version until updated by other means."': 'description:"阻止应用检查和下载来自 Anthropic 的更新。除非通过其他方式更新，否则应用会停留在当前安装版本。"',
        'description:"Skips the first-launch screen that asks the user to choose between Anthropic sign-in and the organization-managed provider. The app goes straight to the mode implied by this configuration (third-party when inferenceProvider is set), overriding any earlier user choice."': 'description:"隐藏首次启动时的账号登录/API 模式选择页，并直接进入当前配置指定的模式。设置 API 提供方后会直进 API 模式；关闭后会恢复账号登录入口。"',
        'description:"Restricts login to specific org UUID(s). Single UUID string or JSON array."': 'description:"将登录限制到指定组织 UUID。可填写单个 UUID 字符串或 JSON 数组。"',
        'description:"Full URL of the inference gateway endpoint."': 'description:"API 服务端点的完整 URL。"',
        'description:"Selects the inference backend. Setting this key activates third-party mode."': 'description:"选择推理后端。设置为 gateway 时会启用 API 模式配置。"',
        'description:"How to send the gateway credential. \'bearer\' (default) sends Authorization: Bearer. Set \'x-api-key\' only if your gateway requires the x-api-key header instead (e.g. api.anthropic.com). Set \'sso\' to obtain the credential via the gateway\'s own browser-based sign-in (RFC 8414 discovery at `<inferenceGatewayBaseUrl>/.well-known/oauth-authorization-server` + RFC 8628 device-code grant); inferenceGatewayApiKey and inferenceCredentialHelper are not required."': 'description:"API 凭据的发送方式。\'bearer\'（默认）会发送 Authorization: Bearer。只有当 API 服务要求使用 x-api-key 请求头时，才设置为 \'x-api-key\'（例如 api.anthropic.com）。设置为 \'sso\' 时，会通过 API 服务自己的浏览器登录获取凭据（RFC 8414 发现 `<inferenceGatewayBaseUrl>/.well-known/oauth-authorization-server` + RFC 8628 设备码授权）；无需 inferenceGatewayApiKey 和 inferenceCredentialHelper。"',
        'description:"Extra HTTP headers sent on every inference request. JSON array of \'Name: Value\' strings."': 'description:"每次推理请求都会发送的额外 HTTP 请求头。格式为由 \'Name: Value\' 字符串组成的 JSON 数组。"',
        'description:"GCP region for the Vertex AI endpoint."': 'description:"Vertex AI 端点所在的 GCP 区域。"',
        'description:"Absolute path to a service-account JSON or ADC file. No tilde or environment-variable expansion."': 'description:"服务账号 JSON 或 ADC 文件的绝对路径。不支持波浪号或环境变量展开。"',
        'description:"Client ID of a Desktop-app OAuth client created in your GCP project (APIs & Services → Credentials). When set together with the client secret, the app runs Sign in with Google and stores the resulting refresh token encrypted; `inferenceVertexCredentialsFile` is not needed."': 'description:"在 GCP 项目中创建的桌面应用 OAuth 客户端 ID（APIs & Services → Credentials）。与客户端密钥一起设置后，应用会运行“使用 Google 登录”，并加密保存得到的刷新令牌；不再需要 `inferenceVertexCredentialsFile`。"',
        'description:"Client secret for the Desktop-app OAuth client. Not confidential for installed apps per Google\'s docs — PKCE protects the flow."': 'description:"桌面应用 OAuth 客户端密钥。根据 Google 文档，已安装应用中的该密钥并非机密；PKCE 会保护登录流程。"',
        'description:"Space-separated OAuth scopes for the Google sign-in flow. Defaults to `openid email https://www.googleapis.com/auth/cloud-platform`. Narrow this if your Workspace\'s Context-Aware Access or reauth policy restricts `cloud-platform`."': 'description:"Google 登录流程使用的 OAuth 权限范围，用空格分隔。默认是 `openid email https://www.googleapis.com/auth/cloud-platform`。如果你的 Workspace 上下文感知访问或重新认证策略限制了 `cloud-platform`，请收窄此范围。"',
        'description:"Override the Vertex inference endpoint (e.g. a Private Service Connect address). Leave unset to use the public regional endpoint."': 'description:"覆盖 Vertex 推理端点（例如 Private Service Connect 地址）。留空则使用公开区域端点。"',
        'description:"AWS region for the Bedrock runtime endpoint."': 'description:"Bedrock 运行时端点所在的 AWS 区域。"',
        'description:"Override the Bedrock inference endpoint (e.g. a VPC interface endpoint or LLM gateway). Leave unset to use the public regional endpoint."': 'description:"覆盖 Bedrock 推理端点（例如 VPC 接口端点或 LLM API 代理）。留空则使用公开区域端点。"',
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
        'description:"Total input+output tokens permitted per window before further messages are refused. Unset = no cap."': 'description:"每个窗口允许的输入+输出总token数，超过后会拒绝后续消息。未设置表示无上限。"',
        'description:"Tumbling window length for the token cap. Max 720 hours (30 days). The counter resets at the end of each window."': 'description:"token上限的滚动窗口长度。最大 720 小时（30 天）。计数器会在每个窗口结束时重置。"',
        'description:"每个窗口允许的输入+输出总令牌数，超过后会拒绝后续消息。未设置表示无上限。"': 'description:"每个窗口允许的输入+输出总token数，超过后会拒绝后续消息。未设置表示无上限。"',
        'description:"令牌上限的滚动窗口长度。最大 720 小时（30 天）。计数器会在每个窗口结束时重置。"': 'description:"token上限的滚动窗口长度。最大 720 小时（30 天）。计数器会在每个窗口结束时重置。"',
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
        'reason:"Usage analytics help us prioritize improvements for third-party inference. Diagnostic-report uploads will also be blocked. No message content is included in either."': 'reason:"使用分析可帮助优先改进 API 模式体验。诊断报告上传也会被阻止。两者都不包含消息内容。"',
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
        'suffix:"tokens"': 'suffix:"token"',
        'suffix:"令牌"': 'suffix:"token"',
        'hint:"Bearer (default) sends Authorization: Bearer. x-api-key is for the Anthropic API directly — auto-selected when the URL is *.anthropic.com."': 'hint:"Bearer（默认）会发送 Authorization: Bearer。x-api-key 用于直连 Anthropic API；当 URL 为 *.anthropic.com 时会自动选择。"',
        'hint:"Extra headers sent to the gateway, one \'Name: Value\' per entry. For tenant routing, org IDs, etc."': 'hint:"发送到 API 服务的额外请求头，每项一个 \'Name: Value\'。可用于租户路由、组织 ID 等。"',
        'hint:"First entry is the picker default. Aliases like sonnet, opus accepted. Optional for gateway — when set, the picker shows exactly this list instead of /v1/models discovery. Turn on 1M context only for models your provider actually serves with the extended window."': 'hint:"第一项是选择器默认模型。支持 sonnet、opus 等别名。API 模式可不填；填写后，模型选择器会严格显示此列表，而不是通过 /v1/models 发现。只有在提供方实际支持扩展上下文窗口时，才开启 1M 上下文。"',
        'hint:"Tags telemetry events with your org so support can find them. Not used for auth."': 'hint:"给遥测事件打上组织标记，方便支持人员定位；不用于认证。"',
        'hint:"Go straight to this provider at launch — users won\'t see the option to sign in to Anthropic instead."': 'hint:"启动时直接进入此提供方；用户不会再看到改用 Anthropic 登录的选项。"',
        'hint:"GCP region where your Vertex AI Claude models are deployed."': 'hint:"部署 Vertex AI Claude 模型的 GCP 区域。"',
        'hint:"Absolute path to service-account JSON. Leave blank to fall back to ADC."': 'hint:"服务账号 JSON 的绝对路径。留空则回退到 ADC。"',
        'hint:"Desktop-app OAuth client ID — enables Sign in with Google instead of a credentials file."': 'hint:"桌面应用 OAuth 客户端 ID；用于通过 Google 登录代替凭据文件。"',
        'hint:"Secret for the Desktop-app OAuth client above."': 'hint:"上方桌面应用 OAuth 客户端的密钥。"',
        'hint:"Override the Google OAuth scopes (space-separated). Leave blank for the default."': 'hint:"覆盖 Google OAuth 权限范围，用空格分隔。留空则使用默认值。"',
        'hint:"PSC endpoint, if using one."': 'hint:"如使用 PSC，请填写其端点。"',
        'hint:"Overrides profile when both are set."': 'hint:"同时设置时会覆盖配置档。"',
        'hint:"For VPC endpoints or gateway proxies."': 'hint:"用于 VPC 端点或 API 代理。"',
        'hint:"Ignored if a bearer token is set."': 'hint:"如果已设置 Bearer 访问令牌，则忽略此项。"',
        'hint:"Folder with AWS config/credentials. Defaults to ~/.aws when no bearer token is set."': 'hint:"包含 AWS config/credentials 的文件夹。未设置 Bearer 访问令牌时默认使用 ~/.aws。"',
        'hint:"Absolute path to an executable that prints the credential."': 'hint:"可执行文件的绝对路径，该程序应输出凭据。"',
        'hint:"Runs tools inside an isolated VM instead of the host. Stronger isolation; slower file access and no host-process tools."': 'hint:"在隔离 VM 内运行工具，而不是在主机上运行。隔离更强，但文件访问更慢，且不能使用主机进程工具。"',
        'hint:"Domains Cowork\'s tools may reach during a turn. Also surfaced under Egress Requirements."': 'hint:"Cowork 工具在一次回合中允许访问的域名，也会显示在“出站网络要求”中。"',
        'hint:"Folders users may attach as a workspace. Leave unset for unrestricted access."': 'hint:"用户可作为工作区附加的文件夹。留空表示不限制。"',
        'hint:"Built-in tools removed from Cowork."': 'hint:"从 Cowork 中移除的内置工具。"',
        'hint:".dxt and .mcpb installs."': 'hint:".dxt 和 .mcpb 安装。"',
        'hint:"The in-app catalogue of installable extensions. Hide to allow sideload only."': 'hint:"应用内可安装扩展目录。隐藏后只允许侧载。"',
        'hint:"Local stdio servers added via the Developer settings. Remote servers come from the managed list above, or plugins mounted to a user\'s computer by an organization admin."': 'hint:"通过开发者设置添加的本地 stdio 服务器。远程服务器来自上方托管列表，或由组织管理员挂载到用户电脑上的插件。"',
        'hint:"Org-pushed remote MCP servers. May embed bearer tokens."': 'hint:"组织下发的远程 MCP 服务器。可能包含 Bearer 访问令牌。"',
        'hint:"Crash and performance reports to Anthropic."': 'hint:"发送给 Anthropic 的崩溃和性能报告。"',
        'hint:"Product-usage analytics and diagnostic-report uploads. No message content."': 'hint:"产品使用分析和诊断报告上传，不包含消息内容。"',
        'hint:"Favicon fetch and the artifact-preview iframe origin. Artifacts will not render."': 'hint:"网站图标获取和 Artifact 预览 iframe 源。禁用后 Artifact 不会渲染。"',
        'label:"Model ID"': 'label:"模型 ID"',
        'label:"Offer 1M-context variant"': 'label:"提供 1M 上下文变体"',
        'label:"Name"': 'label:"名称"',
        'label:"URL"': 'label:"URL"',
        'label:"Transport"': 'label:"传输方式"',
        'label:"OAuth"': 'label:"OAuth"',
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

    print(f"已处理前端硬编码中文文案: {patched_strings} 处替换，涉及 {patched_files} 个文件")


FIRST_RUN_FRONTEND_TRANSLATIONS = {
    "/aBLH2Kytu": "开始使用",
    "Ub+AGcdPg6": "登录",
    "qZjfHa3uMI": "下载 Claude Windows 版",
    "aKmabj9GL7": "与 Claude 对话的最快方式",
    "FJ6r9Eufij": "使用 Google 继续",
    "l6yCDglZqT": "使用邮箱继续",
    "WZnsSUyRsT": "输入你的邮箱",
    "1rdtC2xx7v": "继续即表示你已知晓 Anthropic 的 <privacyLink>隐私政策</privacyLink>，并同意偶尔接收推广邮件和通知。",
    "8qzCpf4q7/": "继续即表示你已知晓 Anthropic 的 <privacyLink>隐私政策</privacyLink>。",
    "8nsTrp2M6s": "退出登陆后，你稍后可以更改此选择。",
}


LOGIN_PAGE_PRELOAD_MARKER = "WIN_CC_ZH_CN_LOGIN_DOM_TRANSLATION_V12"
LOGIN_PAGE_PRELOAD_SNIPPET = r'''
;(()=>{const MARK="WIN_CC_ZH_CN_LOGIN_DOM_TRANSLATION_V12";if(globalThis[MARK])return;globalThis[MARK]=true;
const allowed=()=>true;
const map=new Map([
["Claude for Windows","Claude Windows 版"],
["for Windows","Windows 版"],
["The fastest way to talk with Claude","与 Claude 对话的最快方式"],
["Get started","开始使用"],
["Sign In","登录"],
["Continue with Google","使用 Google 继续"],
["Continue with email","使用邮箱继续"],
["Enter your email","输入你的邮箱"],
["Write a message...","输入消息..."],
["Write a message…","输入消息..."],
["Legacy Model","旧版模型"],
["OR","或"],
["By continuing, you acknowledge Anthropic's Privacy Policy.","继续即表示你已知晓 Anthropic 的隐私政策。"],
["By continuing, you acknowledge Anthropic’s Privacy Policy.","继续即表示你已知晓 Anthropic 的隐私政策。"],
["By continuing, you acknowledge Anthropic's Privacy Policy(opens in a new tab).","继续即表示你已知晓 Anthropic 的隐私政策。"],
["By continuing, you acknowledge Anthropic’s Privacy Policy(opens in a new tab).","继续即表示你已知晓 Anthropic 的隐私政策。"],
["By continuing, you acknowledge Anthropic's Privacy Policy (opens in a new tab).","继续即表示你已知晓 Anthropic 的隐私政策。"],
["By continuing, you acknowledge Anthropic’s Privacy Policy (opens in a new tab).","继续即表示你已知晓 Anthropic 的隐私政策。"],
["By continuing, you acknowledge Anthropic's ","继续即表示你已知晓 Anthropic 的 "],
["By continuing, you acknowledge Anthropic’s ","继续即表示你已知晓 Anthropic 的 "],
["Privacy Policy.","隐私政策。"],
["Privacy Policy","隐私政策"],
["Privacy Policy(opens in a new tab)","隐私政策"],
["Privacy Policy (opens in a new tab)","隐私政策"],
["You can change this later by signing out.","退出登陆后，你稍后可以更改此选择。"],
["Sign out","退出登陆"],
["Sign Out","退出登陆"],
["Or continue with Gateway","或继续使用 API 模式使用"],
["Continue with Gateway","继续使用 API 模式"],
["Always allow in this project (local)","在此项目中始终允许（本地）"],
["Allow once","允许一次"],
["Reject","拒绝"],
["Gateway","API 模式"],
["Anthropic-compatible","Anthropic 兼容"],
["Full URL of the inference gateway endpoint.","API 服务端点的完整 URL。"],
["Extra headers sent to the gateway. One value per header name. For tenant routing, org IDs, etc.","发送到 API 服务的额外请求头。每个请求头名称对应一个值，可用于租户路由、组织 ID 等。"],
["Bearer (default) sends Authorization: Bearer. x-api-key is for the Anthropic API directly — auto-selected when the URL is *.anthropic.com.","Bearer（默认）会发送 Authorization: Bearer。x-api-key 用于直连 Anthropic API；当 URL 为 *.anthropic.com 时会自动选择。"],
["Hide Anthropic sign-in","隐藏 Anthropic 登录入口"],
["Users see only this provider at the login screen — the option to sign in to Anthropic is hidden.","登录页只显示当前提供方；Anthropic 登录入口会被隐藏。"],
["Show the Code tab (terminal-based coding sessions). Sessions run on the host, not inside the VM.","显示 Code 标签页（终端式编码会话）。会话在主机上运行，不在 VM 内运行。"],
["Reject desktop extensions that are not signed by a trusted publisher.","拒绝未由受信任发布者签名的桌面扩展。"],
["desktop extensions that are not signed by a trusted publisher.","未由受信任发布者签名的桌面扩展。"],
["CORE (VM BUNDLE + CLAUDE CLI BINARY)","核心组件（VM 包 + Claude CLI）"],
["Core (VM bundle + Claude CLI binary)","核心组件（VM 包 + Claude CLI）"],
["AUTO-UPDATES","自动更新"],
["Auto-updates","自动更新"],
["ESSENTIAL TELEMETRY","必要遥测"],
["Essential telemetry","必要遥测"],
["NONESSENTIAL TELEMETRY","非必要遥测"],
["Nonessential telemetry","非必要遥测"],
["NONESSENTIAL SERVICES","非必要服务"],
["Nonessential services","非必要服务"],
["Desktop extensions (Python runtime)","桌面扩展（Python 运行时）"],
["Desktop extensions (PYTHON runtime)","桌面扩展（Python 运行时）"],
["Gateway base URL","API 地址"],
["Gateway API key","API 密钥"],
["Gateway auth scheme","认证方式"],
["Gateway extra headers","额外请求头"],
["Choose where Claude Desktop sends inference requests.","选择 Claude Desktop 发送推理请求的位置。"],
["Selects the inference backend. Setting this key activates third-party mode.","选择推理后端。设置为 gateway 时会启用 API 模式配置。"],
["First entry is the picker default. Aliases like sonnet, opus accepted. Optional for gateway — when set, the picker shows exactly this list instead of /v1/models discovery. Turn on 1M context only for models your provider actually serves with the extended window.","第一项是模型选择器默认值。支持 sonnet、opus 等别名。API 模式可不填；填写后将只显示此列表，而不通过 /v1/models 发现。只有提供方实际支持扩展上下文窗口时，才开启 1M 上下文。"],
["Tags telemetry events with your org so support can find them. Not used for auth.","给遥测事件标记组织，方便支持人员定位；不用于认证。"],
["Absolute path to an executable that prints the credential.","输出凭据的可执行文件绝对路径。"],
["Run credential helper","运行凭据辅助脚本"],
["Tool egress (Cowork sessions)","Cowork 工具出站"],
["User-added MCP (Python runtime)","用户添加的 MCP（Python 运行时）"],
["Managed MCP servers","托管 MCP 服务器"],
["MCP servers","MCP 服务器"],
["Extensions","扩展"],
["Require signed extensions","要求扩展签名"],
["Allow Claude Code tab","允许 Claude Code 标签页"]
]);
const replace=s=>{let v=s;for(const[a,b]of map)v=v.split(a).join(b);return v.replace(/for\s+Windows/g,"Windows 版")};
const walk=root=>{if(!allowed()||!root)return;try{
const tw=document.createTreeWalker(root,NodeFilter.SHOW_TEXT,{acceptNode:n=>{const p=n.parentElement;if(!p||["SCRIPT","STYLE","NOSCRIPT"].includes(p.tagName))return NodeFilter.FILTER_REJECT;return map.has(n.nodeValue.trim())||/for\s+Windows/.test(n.nodeValue)||[...map.keys()].some(k=>n.nodeValue.includes(k))?NodeFilter.FILTER_ACCEPT:NodeFilter.FILTER_SKIP}});
for(let n;n=tw.nextNode();)n.nodeValue=replace(n.nodeValue);
for(const el of root.querySelectorAll?.("input,textarea,button,[aria-label],[title]")||[])for(const attr of ["placeholder","aria-label","title","value"]){const v=el.getAttribute?.(attr);if(v&&[...map.keys()].some(k=>v.includes(k)))el.setAttribute(attr,replace(v))}
for(const h of root.querySelectorAll?.("h1,h2,[role=heading]")||[])if(/Claude/i.test(h.textContent||"")&&/Windows/i.test(h.textContent||""))h.textContent="Claude Windows 版";
}catch{}};
const run=()=>walk(document.body||document.documentElement);
addEventListener("DOMContentLoaded",run,{once:true});setTimeout(run,50);setTimeout(run,500);setTimeout(run,1500);
const watch=()=>{const r=document.documentElement||document.body;if(!r){setTimeout(watch,50);return}try{new MutationObserver(m=>{for(const x of m)for(const n of x.addedNodes)walk(n.nodeType===1?n:n.parentElement)}).observe(r,{childList:true,subtree:true});run()}catch{setTimeout(watch,200)}};
watch();
})();
'''


def patch_login_page_preload_translation(app_dir: Path, dry_run: bool = False) -> int:
    asar = app_dir.expanduser() / "resources/app.asar"
    if not asar.exists():
        print(f"未找到 Claude app.asar，跳过登录页运行时中文补丁: {asar}")
        return 0

    target_path = ".vite/build/mainView.js"

    def patcher(content: bytes) -> bytes:
        text = content.decode("utf-8")
        if LOGIN_PAGE_PRELOAD_MARKER in text:
            return content
        legacy_prefix = ';(()=>{const MARK="WIN_CC_ZH_CN_LOGIN_DOM_TRANSLATION_V'
        while legacy_prefix in text:
            start = text.find(legacy_prefix)
            end = text.find("\n})();", start)
            if end < 0:
                break
            text = text[:start] + text[end + len("\n})();") :]
        marker = "\n//# sourceMappingURL=mainView.js.map"
        if marker not in text:
            raise SystemExit("无法找到 mainView.js source map 标记，跳过登录页运行时中文补丁。")
        return text.replace(marker, LOGIN_PAGE_PRELOAD_SNIPPET + marker, 1).encode("utf-8")

    data = asar.read_bytes()
    if LOGIN_PAGE_PRELOAD_MARKER.encode("utf-8") in data:
        print(f"登录页运行时中文补丁已存在: {asar}")
        current_hash = asar_header_hash(data)
        if not dry_run:
            patch_exe_asar_header_hash(app_dir, current_hash, backup_header_hashes(asar), "before-login-dom-zh-CN")
        return 0

    if dry_run:
        print(f"[dry-run] 将向 {target_path} 注入登录页运行时中文补丁。")
        return 0

    backup = backup_file(asar, "before-login-dom-zh-CN")
    try:
        changed, old_header_hash, new_header_hash = patch_asar_file_bytes(asar, target_path, patcher)
    except PermissionError:
        if backup.exists():
            shutil.copy2(backup, asar)
        raise SystemExit(
            "无法注入登录页中文补丁，因为 Windows 拒绝访问 app.asar。"
            "请完全关闭 Claude 后再重新运行初始化或更新。"
        )
    except Exception:
        if backup.exists():
            shutil.copy2(backup, asar)
        raise

    if not changed:
        print(f"登录页运行时中文补丁已存在: {asar}")
        return 0

    print(f"已备份 Claude app.asar: {backup}")
    print("已注入运行时中文兜底补丁：处理登录页和部分硬编码设置页可见文案")
    patch_exe_asar_header_hash(
        app_dir,
        new_header_hash,
        [old_header_hash, *backup_header_hashes(asar)],
        "before-login-dom-zh-CN",
    )
    return 1


def patch_frontend_fallback_locale(app_dir: Path) -> int:
    source = app_dir / FRONTEND_I18N_REL / "en-US.json"
    data = load_json(source)
    if not isinstance(data, dict):
        raise SystemExit("Unsupported frontend en-US JSON shape.")

    changed = 0
    for key, value in FIRST_RUN_FRONTEND_TRANSLATIONS.items():
        if key in data and data[key] != value:
            data[key] = value
            changed += 1

    if changed:
        save_json(source, data)
    print(f"已处理登录前界面 fallback 中文文案: {changed} 条")
    return changed


def apply_locale_resources(app_dir: Path, dry_run: bool = False) -> int:
    app_dir = app_dir.expanduser()
    if not (app_dir / FRONTEND_I18N_REL / "en-US.json").exists():
        print(f"未找到 Claude 前端资源，跳过语言补丁: {app_dir}")
        return 0

    normalize_percent_encoded_paths(app_dir, dry_run)
    require_file(FRONTEND_TRANSLATION)
    require_file(DESKTOP_TRANSLATION)
    patch_language_whitelist(app_dir)
    patch_hardcoded_frontend_strings(app_dir)
    patch_frontend_fallback_locale(app_dir)
    merge_frontend_locale(app_dir)
    install_desktop_locale(app_dir)
    install_statsig_locale(app_dir)
    patch_hardcoded_desktop_menu_strings(app_dir, dry_run)
    patch_login_page_preload_translation(app_dir, dry_run)
    if not dry_run:
        clear_portable_frontend_cache()
    return 0


def merge_frontend_locale(app_dir: Path) -> Tuple[int, int, int]:
    source = app_dir / FRONTEND_I18N_REL / "en-US.json"
    target = app_dir / FRONTEND_I18N_REL / "zh-CN.json"
    require_file(source)
    require_file(FRONTEND_TRANSLATION)

    en = load_json(source)
    zh_pack = load_json(FRONTEND_TRANSLATION)
    if not isinstance(en, dict) or not isinstance(zh_pack, dict):
        raise SystemExit("Unsupported frontend i18n JSON shape.")

    merged: Dict[str, Any] = {}
    translated = 0
    fallback = 0
    for key, value in en.items():
        if key in FIRST_RUN_FRONTEND_TRANSLATIONS:
            merged[key] = FIRST_RUN_FRONTEND_TRANSLATIONS[key]
            if FIRST_RUN_FRONTEND_TRANSLATIONS[key] != value:
                translated += 1
        elif key in zh_pack:
            merged[key] = zh_pack[key]
            if zh_pack[key] != value:
                translated += 1
        else:
            merged[key] = value
            fallback += 1

    save_json(target, merged)
    extra = len(set(zh_pack) - set(en))
    print(f"已安装前端 zh-CN 资源: {translated} 条中文，{fallback} 条回退英文，忽略 {extra} 条旧键")
    return translated, fallback, extra


def install_desktop_locale(app_dir: Path) -> None:
    resources_dir = app_dir / DESKTOP_RESOURCES_REL
    require_file(DESKTOP_TRANSLATION)
    zh_pack = load_json(DESKTOP_TRANSLATION)
    if not isinstance(zh_pack, dict):
        raise SystemExit("Unsupported desktop zh-CN JSON shape.")

    zh_target = resources_dir / "zh-CN.json"
    save_json(zh_target, zh_pack)

    fallback = resources_dir / "en-US.json"
    changed = 0
    if fallback.exists():
        en = load_json(fallback)
        if isinstance(en, dict):
            for key, value in zh_pack.items():
                if key in en and en[key] != value:
                    en[key] = value
                    changed += 1
            if changed:
                save_json(fallback, en)

    print(f"已安装桌面外壳 zh-CN 资源，并处理 fallback 中文文案: {changed} 条")


def install_statsig_locale(app_dir: Path) -> None:
    statsig_dir = app_dir / FRONTEND_I18N_REL / "statsig"
    if not statsig_dir.exists():
        return
    target = statsig_dir / "zh-CN.json"
    if STATSIG_TRANSLATION.exists():
        shutil.copy2(STATSIG_TRANSLATION, target)
    elif (statsig_dir / "en-US.json").exists():
        shutil.copy2(statsig_dir / "en-US.json", target)
    print("已安装 statsig zh-CN 资源")


def clear_portable_frontend_cache() -> int:
    cache_names = [
        "Code Cache",
        "Cache",
        "GPUCache",
        "DawnCache",
        "Service Worker",
        "Session Storage",
        "Shared Dictionary",
        "blob_storage",
    ]
    removed = 0
    for name in cache_names:
        path = portable_user_data_dir() / name
        if not path.exists():
            continue
        try:
            delete_if_exists(path)
            removed += 1
            print(f"已清理绿色版前端缓存: {path}")
        except OSError as exc:
            print(f"警告：无法清理绿色版前端缓存 {path}: {exc}")
    return removed


def config_paths() -> List[Path]:
    paths = [
        portable_user_data_dir() / "config.json",
        roaming_app_data() / "Claude/config.json",
    ]
    packages = local_app_data() / "Packages"
    if packages.exists():
        for package in packages.glob("Claude_*"):
            paths.append(package / "LocalCache/Roaming/Claude/config.json")
        for package in packages.glob("*Anthropic*Claude*"):
            paths.append(package / "LocalCache/Roaming/Claude/config.json")

    unique: List[Path] = []
    seen: Set[str] = set()
    for path in paths:
        key = str(path).lower()
        if key not in seen:
            unique.append(path)
            seen.add(key)
    return unique


def developer_settings_paths() -> List[Path]:
    paths = [
        portable_user_data_dir() / "developer_settings.json",
        roaming_app_data() / "Claude/developer_settings.json",
    ]
    packages = local_app_data() / "Packages"
    if packages.exists():
        for package in packages.glob("Claude_*"):
            paths.append(package / "LocalCache/Roaming/Claude/developer_settings.json")
        for package in packages.glob("*Anthropic*Claude*"):
            paths.append(package / "LocalCache/Roaming/Claude/developer_settings.json")

    unique: List[Path] = []
    seen: Set[str] = set()
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


def load_json_dict(path: Path, *, backup_invalid: bool = False, label: str = "JSON") -> Dict[str, Any]:
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
            print(f"已有 {label} 不是有效 JSON，已备份到 {backup}")
        else:
            print(f"无法读取 {label}: {path} ({exc})")
        return {}


def mask_secret(value: Optional[str]) -> str:
    if not value:
        return ""
    value = value.strip()
    if len(value) <= 8:
        return value[:2] + "***"
    return value[:4] + "***" + value[-4:]


def nonempty_string(value: Any) -> Optional[str]:
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


def third_party_config_entries(data_dir: Path) -> List[Dict[str, Any]]:
    library = third_party_config_library_dir(data_dir)
    meta_path = third_party_config_meta_path(data_dir)
    if not library.exists():
        return []

    meta = load_json_dict(meta_path, label="Claude third-party config metadata")
    candidate_paths: List[Path] = []
    applied_id = nonempty_string(meta.get("appliedId"))
    if applied_id:
        candidate_paths.append(third_party_config_path(applied_id, data_dir))

    entries = meta.get("entries")
    names_by_id: Dict[str, str] = {}
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

    valid: List[Dict[str, Any]] = []
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


def discover_desktop_third_party_sources() -> Tuple[List[Dict[str, Any]], List[str]]:
    sources: List[Dict[str, Any]] = []
    messages: List[str] = []
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


def discover_local_claude_gateway_config() -> Tuple[Optional[Dict[str, Any]], List[str]]:
    messages: List[str] = []
    base_url: Optional[str] = None
    base_source: Optional[str] = None
    credential: Optional[str] = None
    credential_name: Optional[str] = None
    credential_source: Optional[str] = None

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


def gateway_models_endpoint_candidates(base_url: str) -> List[str]:
    parsed = urllib.parse.urlparse(base_url)
    normalized = base_url.rstrip("/")
    candidates = [normalized + "/v1/models"]
    if parsed.path.rstrip("/").endswith("/claude"):
        root_path = parsed.path.rstrip("/")[: -len("/claude")]
        root = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, root_path, "", "", "")).rstrip("/")
        candidates.append(root + "/v1/models")
    return list(dict.fromkeys(candidates))


def discover_gateway_models(base_url: str, credential: str, auth_scheme: str) -> Tuple[List[str], List[str]]:
    messages: List[str] = []
    headers = {
        "Accept": "application/json",
        "User-Agent": DOWNLOAD_HEADERS["User-Agent"],
    }
    if auth_scheme == "x-api-key":
        headers["x-api-key"] = credential
    elif auth_scheme != "sso":
        headers["Authorization"] = f"Bearer {credential}"

    for endpoint in gateway_models_endpoint_candidates(base_url):
        request = urllib.request.Request(endpoint, headers=headers)
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(request, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace").strip()
            if len(body) > 800:
                body = body[:800] + "..."
            messages.append(f"{endpoint}: HTTP {exc.code} {body or exc.reason}")
            continue
        except Exception as exc:
            messages.append(f"{endpoint}: {exc}")
            continue

        raw_models = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(raw_models, list):
            messages.append(f"{endpoint}: response has no model list")
            continue

        models: List[str] = []
        for item in raw_models:
            model_id: Optional[str] = None
            if isinstance(item, dict):
                model_id = nonempty_string(item.get("id")) or nonempty_string(item.get("name"))
            elif isinstance(item, str):
                model_id = nonempty_string(item)
            if model_id and re.search(r"(^|/)claude[-/]", model_id, flags=re.IGNORECASE):
                models.append(model_id)

        models = list(dict.fromkeys(models))
        if models:
            messages.append(f"{endpoint}: detected {len(models)} models")
            return models, messages
        messages.append(f"{endpoint}: model list is empty")

    return [], messages


def backup_third_party_library(data_dir: Path, reason: str) -> Optional[Path]:
    library = third_party_config_library_dir(data_dir)
    if not library.exists():
        return None
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_root = tool_root() / "user-data-backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    backup = unique_backup_path(backup_root / f"third-party-config-{reason}-{stamp}")
    shutil.copytree(library, backup)
    return backup


def third_party_write_targets() -> List[Path]:
    paths = third_party_data_paths()
    existing = [path for path in paths if path.exists() or third_party_config_library_dir(path).exists()]
    return existing or [primary_third_party_data_dir()]


def ensure_third_party_config_meta(data_dir: Path, dry_run: bool) -> Tuple[str, Path]:
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

    normalized_entries: List[Dict[str, Any]] = []
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
                print(f"已备份 Claude API 配置元数据: {backup}")
            save_json(meta_path, data)
            print(f"已更新 Claude API 配置元数据: {meta_path}")

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
            print(f"已备份 Claude API 配置: {backup}")
        save_json(config_path, updated)
        print(f"已启用直进 API 模式: {config_path}")


def set_deployment_mode_3p(
    data_dir: Path,
    dry_run: bool,
    reason: str = "before-enter-third-party-mode",
) -> bool:
    config_path = data_dir / "claude_desktop_config.json"
    current = load_json_dict(config_path, backup_invalid=True, label="Claude desktop config")
    if current.get("deploymentMode") == "3p":
        return False

    updated = dict(current)
    updated["deploymentMode"] = "3p"
    if dry_run:
        print(f"[dry-run] 将设置 Claude Desktop 主配置 deploymentMode=3p: {config_path}")
        return True

    if config_path.exists():
        backup = backup_file(config_path, reason)
        print(f"已备份 Claude Desktop 主配置: {backup}")
    save_json(config_path, updated)
    print(f"已设置 Claude Desktop 主配置 deploymentMode=3p: {config_path}")
    return True


def set_portable_deployment_mode_3p(dry_run: bool, reason: str = "before-enter-third-party-mode") -> bool:
    return set_deployment_mode_3p(portable_user_data_dir(), dry_run, reason)


def refresh_launcher_for_third_party_mode(dry_run: bool = False) -> None:
    target_dir = default_target_dir()
    if not app_exe(target_dir.expanduser()):
        print(f"未找到汉化版程序，跳过刷新启动器: {target_dir}")
        return
    if dry_run:
        print(f"[dry-run] 将刷新汉化版启动器并设置 {CLAUDE_USER_DATA_DIR_ENV}: {launcher_path()}")
        return
    create_launcher(target_dir)


def sync_desktop_third_party_library(source_data_dir: Path, target_data_dir: Path, dry_run: bool = False) -> int:
    source_library = third_party_config_library_dir(source_data_dir)
    target_library = third_party_config_library_dir(target_data_dir)
    if not source_library.exists():
        print(f"未找到来源 Desktop configLibrary 配置库: {source_library}")
        return 1

    if source_data_dir.resolve() == target_data_dir.resolve():
        print(f"来源和目标 API 配置库相同: {source_library}")
        print("已保留当前 API 配置；同步操作不会自动进入 API 模式。")
        if target_data_dir.resolve() == portable_user_data_dir().resolve():
            clear_portable_deployment_mode(dry_run)
            clear_disable_deployment_mode_chooser(target_data_dir, dry_run)
            if not dry_run:
                clear_portable_frontend_cache()
            refresh_launcher_for_third_party_mode(dry_run)
        return 0

    json_files = sorted(source_library.glob("*.json"))
    if not json_files:
        print(f"配置库中没有找到 JSON 配置文件: {source_library}")
        return 1

    print(f"来源 API 配置库: {source_library}")
    print(f"目标 API 配置库: {target_library}")
    if dry_run:
        print(f"[dry-run] 将同步 {len(json_files)} 个配置文件，并保留 Anthropic 账号登录入口。")
        return 0

    backup = backup_third_party_library(target_data_dir, "before-sync")
    if backup:
        print(f"已备份目标 API 配置库: {backup}")

    target_library.mkdir(parents=True, exist_ok=True)
    for source in json_files:
        target = target_library / source.name
        shutil.copy2(source, target)
        print(f"已同步配置文件: {target.name}")

    if target_data_dir.resolve() == portable_user_data_dir().resolve():
        clear_portable_deployment_mode(dry_run)
        clear_disable_deployment_mode_chooser(target_data_dir, dry_run)
        clear_portable_frontend_cache()
        refresh_launcher_for_third_party_mode(dry_run)
    print("已同步 API 配置，并保留 Anthropic 账号登录入口。需要直进 API 时，请单独选择“进入 API 模式”。")
    return 0

def apply_third_party_inference_config(dry_run: bool = False, force_mode: bool = False) -> int:
    discovered, messages = discover_local_claude_gateway_config()
    if not discovered:
        print("没有应用 Claude Code API 配置。")
        for message in messages:
            print(f"  {message}")
        print("可以在 Developer -> Configure Third-Party Inference[API 模式配置] 中手动填写，或把环境变量加入 ~/.claude/settings.json。")
        return 0

    print("检测到 Claude Code API 配置:")
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
        }
    )
    models, model_messages = discover_gateway_models(
        discovered["base_url"],
        discovered["credential"],
        discovered["auth_scheme"],
    )
    if models:
        updated["inferenceModels"] = models
        print(f"已写入 API 模型列表: {', '.join(models[:8])}{' ...' if len(models) > 8 else ''}")
    else:
        updated.pop("inferenceModels", None)
        print("未写入固定 API 模型列表：正常 API 会继续由 Claude Desktop 通过 /v1/models 自动发现。")
        for message in model_messages:
            print(f"  {message}")
    if force_mode:
        updated["disableDeploymentModeChooser"] = True
    else:
        updated.pop("disableDeploymentModeChooser", None)

    config_changed = updated != current
    if force_mode:
        mode_changed = set_portable_deployment_mode_3p(dry_run, "before-third-party-config")
    else:
        mode_changed = clear_portable_deployment_mode(dry_run)
    if not config_changed:
        print(f"Claude API 模式配置已是最新: {config_path}")
        if force_mode and mode_changed:
            print("已进入 API 模式。")
        elif not force_mode:
            clear_disable_deployment_mode_chooser(data_dir, dry_run)
            print("已保留 Anthropic 登录/模式选择入口。")
        refresh_launcher_for_third_party_mode(dry_run)
        if not force_mode and not dry_run:
            clear_portable_frontend_cache()
        return 0

    if dry_run:
        print(f"[dry-run] 将应用 Claude API 模式配置: {config_path}")
        return 0

    if config_path.exists():
        backup = backup_file(config_path, "before-third-party-config")
        print(f"已备份 Claude API 模式配置: {backup}")
    save_json(config_path, updated)
    print(f"已应用 Claude API 模式配置: {config_path} (id: {config_id})")
    if force_mode and mode_changed:
        print("已进入 API 模式。")
    elif not force_mode:
        clear_disable_deployment_mode_chooser(data_dir, dry_run)
        print("已预置 API 模式配置，并保留 Anthropic 账号登录入口。")
    refresh_launcher_for_third_party_mode(dry_run)
    if not force_mode:
        clear_portable_frontend_cache()

    return 0


def enter_third_party_mode(dry_run: bool = False) -> int:
    data_dir = primary_third_party_data_dir()
    entries = third_party_config_entries(data_dir)
    if not entries:
        print(f"绿色版没有可用的 API 配置: {third_party_config_library_dir(data_dir)}")
        print("请先同步 Desktop configLibrary 配置库，或从 Claude Code 生成 API 配置。")
        return 1

    meta_path = third_party_config_meta_path(data_dir)
    meta = load_json_dict(meta_path, backup_invalid=True, label="Claude third-party config metadata")
    original_meta = json.dumps(meta, sort_keys=True, ensure_ascii=False)
    applied_id = nonempty_string(meta.get("appliedId"))
    selected = next((entry for entry in entries if entry["id"] == applied_id), entries[0])

    existing_entries = meta.get("entries")
    names_by_id: Dict[str, str] = {}
    if isinstance(existing_entries, list):
        for entry in existing_entries:
            if not isinstance(entry, dict):
                continue
            entry_id = nonempty_string(entry.get("id"))
            if entry_id:
                names_by_id[entry_id] = nonempty_string(entry.get("name")) or entry_id

    normalized_entries: List[Dict[str, str]] = []
    seen_ids: Set[str] = set()
    for entry in entries:
        entry_id = entry["id"]
        normalized_entries.append({"id": entry_id, "name": names_by_id.get(entry_id) or entry["name"] or "Default"})
        seen_ids.add(entry_id)
    if selected["id"] not in seen_ids:
        normalized_entries.append({"id": selected["id"], "name": selected["name"] or "Default"})

    meta["appliedId"] = selected["id"]
    meta["entries"] = normalized_entries
    updated_meta = json.dumps(meta, sort_keys=True, ensure_ascii=False)

    config_path = selected["path"]
    current = load_json_dict(config_path, backup_invalid=True, label="Claude third-party config")
    updated = dict(current)
    updated["inferenceProvider"] = "gateway"
    updated["disableDeploymentModeChooser"] = True
    if not nonempty_string(updated.get("inferenceGatewayAuthScheme")):
        updated["inferenceGatewayAuthScheme"] = selected["auth_scheme"]

    meta_changed = updated_meta != original_meta
    config_changed = updated != current
    mode_changed = set_portable_deployment_mode_3p(dry_run, "before-enter-third-party-mode")
    if not meta_changed and not config_changed and not mode_changed:
        print(f"绿色版已处于 API 模式: {config_path}")
        refresh_launcher_for_third_party_mode(dry_run)
        return 0

    if dry_run:
        if meta_changed:
            print(f"[dry-run] 将把当前 API 配置设为: {selected['id']} ({meta_path})")
        if config_changed:
            print(f"[dry-run] 将启用 API 模式: {config_path}")
        refresh_launcher_for_third_party_mode(dry_run)
        return 0

    if meta_changed:
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        if meta_path.exists():
            backup = backup_file(meta_path, "before-enter-third-party-mode")
            print(f"已备份 Claude API 配置元数据: {backup}")
        save_json(meta_path, meta)
        print(f"已设置当前 API 配置: {selected['name']} ({selected['id']})")

    if config_changed:
        if config_path.exists():
            backup = backup_file(config_path, "before-enter-third-party-mode")
            print(f"已备份 Claude API 模式配置: {backup}")
        save_json(config_path, updated)
        print(f"已启用 API 模式: {config_path}")

    refresh_launcher_for_third_party_mode(dry_run)
    print("请完全关闭 Claude zh-CN 后重新启动，让模式切换生效。")
    return 0


def clear_portable_deployment_mode(dry_run: bool = False) -> bool:
    config_path = portable_user_data_dir() / "claude_desktop_config.json"
    current = load_json_dict(config_path, backup_invalid=True, label="Claude desktop config")
    if "deploymentMode" not in current:
        return False

    updated = dict(current)
    previous = updated.pop("deploymentMode", None)
    if dry_run:
        print(f"[dry-run] 将移除 Claude Desktop 主配置 deploymentMode={previous}: {config_path}")
        return True

    if config_path.exists():
        backup = backup_file(config_path, "before-exit-third-party-mode")
        print(f"已备份 Claude Desktop 主配置: {backup}")
    save_json(config_path, updated)
    print(f"已移除 Claude Desktop 主配置 deploymentMode={previous}: {config_path}")
    return True


def clear_disable_deployment_mode_chooser(data_dir: Path, dry_run: bool = False) -> int:
    changed = 0
    for entry in third_party_config_entries(data_dir):
        config_path = entry["path"]
        current = load_json_dict(config_path, backup_invalid=True, label="Claude third-party config")
        if "disableDeploymentModeChooser" not in current:
            continue

        updated = dict(current)
        previous = updated.pop("disableDeploymentModeChooser", None)
        changed += 1
        if dry_run:
            print(f"[dry-run] 将取消直进 API 模式 ({previous}): {config_path}")
            continue

        if config_path.exists():
            backup = backup_file(config_path, "before-exit-third-party-mode")
            print(f"已备份 Claude API 模式配置: {backup}")
        save_json(config_path, updated)
        print(f"已取消直进 API 模式并保留 API 配置: {config_path}")
    return changed


def restore_gateway_provider_markers(data_dir: Path, dry_run: bool = False) -> int:
    changed = 0
    for entry in third_party_config_entries(data_dir):
        config_path = entry["path"]
        current = load_json_dict(config_path, backup_invalid=True, label="Claude third-party config")
        if current.get("inferenceProvider") == "gateway":
            continue
        if not nonempty_string(current.get("inferenceGatewayBaseUrl")):
            continue

        updated = dict(current)
        updated["inferenceProvider"] = "gateway"
        changed += 1
        if dry_run:
            print(f"[dry-run] 将恢复 API 配置标记: {config_path}")
            continue

        if config_path.exists():
            backup = backup_file(config_path, "before-restore-gateway-provider")
            print(f"已备份 Claude API 模式配置: {backup}")
        save_json(config_path, updated)
        print(f"已恢复 API 配置标记: {config_path}")
    return changed


def exit_third_party_mode(dry_run: bool = False) -> int:
    data_dir = primary_third_party_data_dir()
    mode_changed = clear_portable_deployment_mode(dry_run)
    provider_changed = restore_gateway_provider_markers(data_dir, dry_run)
    chooser_changed = clear_disable_deployment_mode_chooser(data_dir, dry_run)

    if not mode_changed and provider_changed == 0 and chooser_changed == 0:
        print("绿色版未强制 API 模式。")
    else:
        print("已退出强制 API 模式，保留 API 配置以便登录页显示账号登录和 API 模式。")

    if not dry_run:
        clear_portable_frontend_cache()
    else:
        print("[dry-run] 将清理绿色版前端缓存。")
    refresh_launcher_for_third_party_mode(dry_run)
    print("请完全关闭 Claude zh-CN 后重新启动；下次应恢复 Anthropic 登录/模式选择入口。")
    return 0


def show_third_party_inference_config() -> int:
    desktop_sources, desktop_messages = discover_desktop_third_party_sources()
    print("Claude Desktop API configLibrary 配置库:")
    if desktop_sources:
        for index, source in enumerate(desktop_sources, start=1):
            print(f"  [{index}] {source['library']}")
            for entry in source["entries"]:
                print(
                    f"      - {entry['name']} ({entry['id']}): "
                    f"{entry['base_url']} / 认证={entry['auth_scheme']} / "
                    f"skipLoginChooser={entry['disable_chooser']}"
                )
    else:
        print("  未找到。")
        for message in desktop_messages:
            print(f"  {message}")

    print()
    discovered, messages = discover_local_claude_gateway_config()
    print("Claude Code API 配置检测:")
    if discovered:
        print(f"  API 地址[Base URL]: {discovered['base_url']}")
        print(f"  凭据[Credential]: {discovered['credential_name']} = {mask_secret(discovered['credential'])}")
        print(f"  认证方式[Auth scheme]: {discovered['auth_scheme']}")
    else:
        print("  未找到。")
        for message in messages:
            print(f"  {message}")

    print()
    print("Claude Desktop API 模式配置:")
    for data_dir in third_party_data_paths():
        meta_path = third_party_config_meta_path(data_dir)
        print_path_info("API 配置元数据", meta_path)
        meta = load_json_dict(meta_path, label="Claude third-party config metadata")
        applied_id = nonempty_string(meta.get("appliedId"))
        if not applied_id:
            continue
        config_path = third_party_config_path(applied_id, data_dir)
        print_path_info("当前应用的 API 配置", config_path)
        config = load_json_dict(config_path, label="Claude third-party config")
        if config:
            print(f"  inferenceProvider: {config.get('inferenceProvider') or '未设置'}")
            print(f"  inferenceGatewayBaseUrl: {config.get('inferenceGatewayBaseUrl') or '未设置'}")
            print(f"  inferenceGatewayApiKey: {mask_secret(nonempty_string(config.get('inferenceGatewayApiKey')))}")
            print(f"  inferenceGatewayAuthScheme: {config.get('inferenceGatewayAuthScheme') or '未设置'}")
            print(f"  disableDeploymentModeChooser: {config.get('disableDeploymentModeChooser')}")
    return 0


def check_third_party_sources() -> int:
    desktop_sources, _ = discover_desktop_third_party_sources()
    code_config, _ = discover_local_claude_gateway_config()
    if desktop_sources or code_config:
        print("检测到可复用的 API 模式配置。")
        if desktop_sources:
            print(f"  Desktop configLibrary 配置库: {len(desktop_sources)}")
        if code_config:
            print(f"  Claude Code API 地址: {code_config['base_url']}")
        return 0
    print("未检测到可复用的 API 模式配置。")
    return 10


def prompt_line(prompt: str) -> Optional[str]:
    try:
        return input(prompt).replace("\x00", "").strip()
    except EOFError:
        print()
        print("没有输入，已取消。")
        return None


def choose_desktop_third_party_source(sources: List[Dict[str, Any]]) -> Optional[Path]:
    if not sources:
        print("没有可同步的 Claude Desktop API 模式配置。")
        return None
    if len(sources) == 1:
        return sources[0]["data_dir"]

    print()
    print("请选择要同步的 Desktop configLibrary 配置库:")
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
    print("API 模式配置向导")
    print("你可以保持绿色版全新，也可以同步现有 Desktop API 配置，或从 Claude Code 生成 Desktop API 配置。")
    print("访问令牌、API key 等敏感值会在输出中打码。")
    print()
    show_third_party_inference_config()

    while True:
        print()
        print("1. 保持全新，不导入也不修改 API 配置")
        print("2. 同步现有 Claude Desktop API 配置到绿色版")
        print("3. 从 Claude Code 配置生成 Desktop API 配置")
        print("4. 进入 API 模式")
        print("5. 退出 API 模式，恢复 Anthropic 账号登录/模式选择")
        print("6. 重新显示检测到的配置")
        print("0. 返回")
        choice = prompt_line("请选择: ")
        if choice is None:
            return 0

        if choice == "0":
            return 0
        if choice == "1":
            print("已保持全新。没有导入 API 配置。")
            return 0
        if choice == "2":
            sources, _ = discover_desktop_third_party_sources()
            source_data_dir = choose_desktop_third_party_source(sources)
            if not source_data_dir:
                continue
            target_data_dir = primary_third_party_data_dir()
            print()
            print("这会复制 Desktop API 配置 JSON 文件；默认保留 Anthropic 账号登录入口。")
            print(f"来源: {third_party_config_library_dir(source_data_dir)}")
            print(f"目标: {third_party_config_library_dir(target_data_dir)}")
            answer = prompt_line("输入 SYNC 继续: ")
            if answer != "SYNC":
                print("已取消。")
                continue
            return sync_desktop_third_party_library(source_data_dir, target_data_dir, False)
        if choice == "3":
            discovered, messages = discover_local_claude_gateway_config()
            if not discovered:
                print("没有可转换的 Claude Code API 配置。")
                for message in messages:
                    print(f"  {message}")
                continue
            print()
            print("这会把 API 字段写入 Desktop configLibrary 配置库；默认保留 Anthropic 账号登录入口。")
            print(f"Base URL: {discovered['base_url']}")
            print(f"Credential: {discovered['credential_name']} = {mask_secret(discovered['credential'])}")
            answer = prompt_line("输入 APPLY 继续: ")
            if answer != "APPLY":
                print("已取消。")
                continue
            return apply_third_party_inference_config(False, force_mode=False)
        if choice == "4":
            return enter_third_party_mode(False)
        if choice == "5":
            return exit_third_party_mode(False)
        if choice == "6":
            print()
            show_third_party_inference_config()
            continue
        print("未知选项。")


def sync_candidate_dirs() -> List[Path]:
    paths = [
        portable_user_data_dir(),
        *legacy_portable_user_data_dirs(),
        *official_user_data_dirs(),
    ]
    unique: List[Path] = []
    seen: Set[str] = set()
    for path in paths:
        key = str(path).lower()
        if key not in seen:
            unique.append(path)
            seen.add(key)
    return unique


def data_dir_label(path: Path) -> str:
    portable = portable_user_data_dir()
    if str(path).lower() == str(portable).lower():
        return "绿色版主空间"
    if any(str(path).lower() == str(legacy).lower() for legacy in legacy_portable_user_data_dirs()):
        return "旧版绿色空间"
    if "packages" in str(path).lower():
        return "官方 MSIX 沙箱空间"
    if path.name.lower() in {"claude", "claude-3p"}:
        return "官方 Desktop 空间"
    return "Claude 数据空间"


def choose_data_dir(candidates: List[Path], title: str, *, require_exists: bool) -> Optional[Path]:
    shown = [path for path in candidates if path.exists() or not require_exists]
    if not shown:
        print("没有找到可选的数据空间。")
        return None
    print()
    print(title)
    for index, path in enumerate(shown, start=1):
        status = "exists" if path.exists() else "missing"
        size = format_size(path_size(path)) if path.exists() else "0 B"
        print(f"  {index}. [{status}] {data_dir_label(path)} - {path} ({size})")
    answer = prompt_line("输入编号，或输入 0 取消: ")
    if answer is None or answer == "0":
        return None
    try:
        choice = int(answer)
    except ValueError:
        print("无效选择。")
        return None
    if choice < 1 or choice > len(shown):
        print("无效选择。")
        return None
    return shown[choice - 1]


def sync_light_user_data(source_dir: Path, target_dir: Path, dry_run: bool = False) -> int:
    if not source_dir.exists():
        print(f"来源数据空间不存在: {source_dir}")
        return 1
    if source_dir.resolve() == target_dir.resolve():
        print(f"来源和目标相同: {source_dir}")
        return 0
    print("即将同步轻量用户数据。")
    print("会包含登录态、Local Storage、IndexedDB、API 配置、MCP/应用配置等。")
    print("不会复制 vm_bundles / Cowork VM 大文件。")
    print(f"来源: {source_dir}")
    print(f"目标: {target_dir}")
    if dry_run:
        return 0
    target_dir.mkdir(parents=True, exist_ok=True)
    copied, skipped, errors = copy_named_items(
        source_dir,
        target_dir,
        USER_DATA_SYNC_ITEMS,
        overwrite=True,
        backup_existing=True,
        reason="before-user-data-sync",
        exclude_vm=True,
    )
    if errors:
        print("同步过程中遇到复制错误:")
        for error in errors[:20]:
            print(f"  {error}")
        if len(errors) > 20:
            print(f"  ... {len(errors) - 20} more")
        return 1
    print(f"轻量用户数据同步完成：复制 {copied} 个文件，跳过 {skipped} 个文件。")
    return 0


def choose_config_library_source() -> Optional[Path]:
    sources = []
    for path in sync_candidate_dirs():
        library = third_party_config_library_dir(path)
        if library.exists():
            sources.append(path)
    return choose_data_dir(sources, "请选择 API configLibrary 配置库来源:", require_exists=True)


def import_sync_wizard() -> int:
    print("导入 / 同步配置")
    print("每次写入目标前都会备份。默认不会复制 Cowork / VM 大文件。")
    print()
    show_user_data(default_target_dir())

    while True:
        print()
        print("1. 扫描并显示可同步的数据空间")
        print("2. 官方 Desktop -> 绿色版（轻量用户数据，不复制 VM）")
        print("3. 绿色版 -> 官方 Desktop（轻量用户数据，不复制 VM）")
        print("4. 自选来源和目标同步轻量用户数据")
        print("5. 同步 API configLibrary 配置库到绿色版")
        print("6. 同步绿色版 API configLibrary 配置库到官方 Desktop")
        print("7. 从 Claude Code 生成绿色版 API 配置")
        print("0. 返回")
        choice = prompt_line("请选择: ")
        if choice is None or choice == "0":
            return 0
        if choice == "1":
            show_user_data(default_target_dir())
            continue
        if choice == "2":
            source = choose_data_dir(official_user_data_dirs(), "请选择官方 Desktop 来源空间:", require_exists=True)
            if not source:
                continue
            target = portable_user_data_dir()
            answer = prompt_line("输入 SYNC 确认同步到绿色版: ")
            if answer == "SYNC":
                return sync_light_user_data(source, target)
            print("已取消。")
            continue
        if choice == "3":
            source = portable_user_data_dir()
            if not source.exists():
                print(f"绿色版数据空间不存在: {source}")
                continue
            target = choose_data_dir(official_user_data_dirs(), "请选择官方 Desktop 目标空间:", require_exists=False)
            if not target:
                continue
            answer = prompt_line("输入 SYNC 确认同步到官方 Desktop: ")
            if answer == "SYNC":
                return sync_light_user_data(source, target)
            print("已取消。")
            continue
        if choice == "4":
            source = choose_data_dir(sync_candidate_dirs(), "请选择来源空间:", require_exists=True)
            if not source:
                continue
            target = choose_data_dir(sync_candidate_dirs(), "请选择目标空间:", require_exists=False)
            if not target:
                continue
            answer = prompt_line("输入 SYNC 确认同步: ")
            if answer == "SYNC":
                return sync_light_user_data(source, target)
            print("已取消。")
            continue
        if choice == "5":
            source = choose_config_library_source()
            if not source:
                continue
            target = portable_user_data_dir()
            answer = prompt_line("输入 SYNC 确认同步 API 配置到绿色版: ")
            if answer == "SYNC":
                return sync_desktop_third_party_library(source, target)
            print("已取消。")
            continue
        if choice == "6":
            source = portable_user_data_dir()
            if not third_party_config_library_dir(source).exists():
                print(f"绿色版 API 配置库不存在: {third_party_config_library_dir(source)}")
                continue
            target = choose_data_dir(official_user_data_dirs(), "请选择官方 Desktop 目标空间:", require_exists=False)
            if not target:
                continue
            answer = prompt_line("输入 SYNC 确认同步绿色版 API 配置到官方 Desktop: ")
            if answer == "SYNC":
                return sync_desktop_third_party_library(source, target)
            print("已取消。")
            continue
        if choice == "7":
            return apply_third_party_inference_config(False, force_mode=False)
        print("未知选项。")


def asar_file_entries(header: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    entries: List[Tuple[str, Dict[str, Any]]] = []

    def walk(node: Dict[str, Any], prefix: str = "") -> None:
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


def parse_asar(data: bytes) -> Tuple[int, int, int, Dict[str, Any]]:
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


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def asar_header_hash(data: bytes) -> str:
    json_start, json_end, _, _ = parse_asar(data)
    return sha256_hex(data[json_start:json_end])


def sha256_blocks(data: bytes, block_size: int) -> List[str]:
    if block_size <= 0:
        return [sha256_hex(data)]
    if not data:
        return [sha256_hex(data)]
    return [sha256_hex(data[index : index + block_size]) for index in range(0, len(data), block_size)]


def asar_header_prefix(header_json_size: int) -> bytes:
    padding_size = (4 - (header_json_size % 4)) % 4
    padded_json_size = header_json_size + padding_size
    return struct.pack("<IIII", 4, 8 + padded_json_size, 4 + padded_json_size, header_json_size)


def patch_asar_file_bytes(
    asar: Path,
    target_path: str,
    patcher: Any,
) -> Tuple[bool, str, str]:
    data = asar.read_bytes()
    json_start, json_end, content_base, header = parse_asar(data)
    old_header_hash = sha256_hex(data[json_start:json_end])
    entries = asar_file_entries(header)
    entry_by_path = {path: entry for path, entry in entries}
    target_entry = entry_by_path.get(target_path)
    if not target_entry:
        raise SystemExit(f"Cannot patch ASAR because {target_path} was not found.")

    original_chunks: Dict[str, bytes] = {}
    for path, entry in entries:
        try:
            offset = content_base + int(entry["offset"])
            size = int(entry["size"])
        except (KeyError, TypeError, ValueError):
            continue
        original_chunks[path] = data[offset : offset + size]

    original_target = original_chunks.get(target_path)
    if original_target is None:
        raise SystemExit(f"Cannot patch ASAR because {target_path} has no packed content.")

    patched_target = patcher(original_target)
    if patched_target == original_target:
        return False, old_header_hash, old_header_hash

    sorted_entries = sorted(
        ((path, entry) for path, entry in entries if path in original_chunks),
        key=lambda item: int(item[1]["offset"]),
    )
    offset = 0
    chunks: List[bytes] = []
    for path, entry in sorted_entries:
        chunk = patched_target if path == target_path else original_chunks[path]
        entry["offset"] = str(offset)
        entry["size"] = len(chunk)
        integrity = entry.get("integrity")
        if isinstance(integrity, dict):
            block_size = int(integrity.get("blockSize") or 4194304)
            integrity["algorithm"] = integrity.get("algorithm") or "SHA256"
            integrity["hash"] = sha256_hex(chunk)
            integrity["blockSize"] = block_size
            integrity["blocks"] = sha256_blocks(chunk, block_size)
        chunks.append(chunk)
        offset += len(chunk)

    header_json = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    prefix = asar_header_prefix(len(header_json))
    padding = b"\0" * ((4 - (len(header_json) % 4)) % 4)
    tmp = asar.with_suffix(asar.suffix + ".tmp")
    tmp.write_bytes(prefix + header_json + padding + b"".join(chunks))
    try:
        os.replace(tmp, asar)
    except PermissionError:
        if tmp.exists():
            tmp.unlink()
        raise

    return True, old_header_hash, sha256_hex(header_json)


def patch_asar_file_content_and_integrity(
    asar: Path,
    old_token: bytes,
    new_token: bytes,
) -> Tuple[int, int, str, str]:
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
        for old_block, new_block in zip(old_blocks, new_blocks):
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


def backup_header_hashes(asar: Path) -> List[str]:
    hashes: List[str] = []
    for backup in sorted(asar.parent.glob(f"{asar.name}.bak-*"), reverse=True):
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
    old_hashes: List[str],
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


def count_asar_tokens(asar: Path, tokens: List[bytes]) -> Dict[bytes, int]:
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

    menu_replacements: Dict[str, str] = {
        "Enable Main Process Debugger": "启用主进程调试器",
        "Record Performance Trace": "记录性能跟踪",
        "Write Main Process Heap Snapshot": "写入主进程堆快照",
        "Record Memory Trace (auto-stop)": "内存跟踪(自动停止)",
    }
    replacements: List[Tuple[bytes, bytes]] = []
    for source, target in menu_replacements.items():
        try:
            replacements.append((source.encode("utf-8"), padded_utf8_replacement(source, target)))
        except ValueError as exc:
            print(f"跳过过长的桌面菜单替换: {exc}")

    counts = count_asar_tokens(asar, [source for source, _ in replacements])
    total = sum(counts.values())
    if total == 0:
        print(f"Hardcoded desktop menu strings are already patched or not present: {asar}")
        return 0

    if dry_run:
        print(f"[dry-run] Would patch {total} hardcoded desktop menu string(s) in {asar}.")
        return 0

    backup = backup_file(asar, "before-desktop-menu-zh-CN")
    old_header_hashes: List[str] = []
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


def patch_binary_tokens(path: Path, replacements: List[Tuple[bytes, bytes]], reason: str, label: str, dry_run: bool = False) -> int:
    if not path.exists():
        print(f"{label} was not found, skipping: {path}")
        return 0

    data = path.read_bytes()
    old_total = sum(data.count(source) for source, _ in replacements)
    new_total = sum(data.count(target) for _, target in replacements)
    if old_total == 0:
        if new_total:
            print(f"{label} namespace is already patched: {path}")
        else:
            print(f"{label} namespace tokens were not found, skipping: {path}")
        return 0

    if dry_run:
        print(f"[dry-run] Would patch {old_total} {label} namespace token(s): {path}")
        return 0

    backup = backup_file(path, reason)
    patched = data
    for source, target in replacements:
        patched = patched.replace(source, target)

    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(patched)
        os.replace(tmp, path)
    except PermissionError:
        if tmp.exists():
            tmp.unlink()
        if backup.exists():
            shutil.copy2(backup, path)
        raise SystemExit(
            f"Could not patch {label} because Windows denied access. "
            "Close Claude completely, then run option 9 again."
        )
    except Exception:
        if tmp.exists():
            tmp.unlink()
        if backup.exists():
            shutil.copy2(backup, path)
        raise

    print(f"Backed up {label}: {backup}")
    print(f"Patched {label} namespace: {old_total} replacement(s)")
    return old_total


def patch_asar_namespace_tokens(app_dir: Path, dry_run: bool = False) -> int:
    asar = app_dir.expanduser() / "resources/app.asar"
    if not asar.exists():
        print(f"Claude app.asar was not found, skipping Cowork namespace patch: {asar}")
        return 0

    source_tokens = [source for source, _ in COWORK_NAMESPACE_REPLACEMENTS]
    target_tokens = [target for _, target in COWORK_NAMESPACE_REPLACEMENTS]
    source_counts = count_asar_tokens(asar, source_tokens)
    target_counts = count_asar_tokens(asar, target_tokens)
    source_total = sum(source_counts.values())
    target_total = sum(target_counts.values())

    if source_total == 0:
        if target_total:
            print(f"Cowork ASAR namespace is already patched: {asar}")
            if not dry_run:
                current_hash = asar_header_hash(asar.read_bytes())
                patch_exe_asar_header_hash(app_dir, current_hash, backup_header_hashes(asar), "before-cowork-namespace")
        else:
            print(f"Cowork namespace tokens were not found in ASAR, skipping: {asar}")
        return 0

    if dry_run:
        print(f"[dry-run] Would patch {source_total} Cowork namespace token(s) in {asar}.")
        return 0

    backup = backup_file(asar, "before-cowork-namespace")
    old_header_hashes: List[str] = []
    final_header_hash = asar_header_hash(asar.read_bytes())
    patched_total = 0
    patched_files_total = 0

    try:
        for source, target in COWORK_NAMESPACE_REPLACEMENTS:
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
            "Could not patch Cowork namespace because Windows denied access. "
            "Close Claude completely, then run option 9 again."
        )
    except Exception:
        if backup.exists():
            shutil.copy2(backup, asar)
        raise

    print(f"Backed up Claude app.asar: {backup}")
    print(
        f"Patched Cowork ASAR namespace: "
        f"{patched_total} replacement(s) in {patched_files_total} file patch(es)"
    )
    patch_exe_asar_header_hash(
        app_dir,
        final_header_hash,
        [*old_header_hashes, *backup_header_hashes(asar)],
        "before-cowork-namespace",
    )
    return patched_total


def patch_cowork_namespace(app_dir: Path, dry_run: bool = False) -> int:
    app_dir = app_dir.expanduser()
    patched = 0
    patched += patch_binary_tokens(
        app_dir / "resources/cowork-svc.exe",
        COWORK_NAMESPACE_REPLACEMENTS,
        "before-cowork-namespace",
        "cowork-svc.exe",
        dry_run,
    )
    patched += patch_asar_namespace_tokens(app_dir, dry_run)
    if not dry_run:
        create_launcher(app_dir)
    return patched


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
        if not dry_run:
            current_hash = asar_header_hash(data)
            patch_exe_asar_header_hash(app_dir, current_hash, backup_header_hashes(asar), "before-cowork-compat")
            create_launcher(app_dir)
        return 0

    if token_count == 0:
        print(f"Cowork MSIX detection token was not found, skipping patch: {asar}")
        if not dry_run:
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


def apply_cowork_compat(app_dir: Path, dry_run: bool = False) -> int:
    patch_cowork_portable_detection(app_dir, dry_run)
    patch_cowork_namespace(app_dir, dry_run)
    return 0


def bundle_runtime_file_names() -> List[str]:
    return [
        "rootfs.vhdx",
        "rootfs.vhdx.zst",
        "initrd",
        "initrd.zst",
        "vmlinuz",
        "vmlinuz.zst",
        "smol-bin.vhdx",
        "sessiondata.vhdx",
        ".rootfs.vhdx.origin",
        ".rootfs.vhdx.zst.origin",
        ".initrd.origin",
        ".initrd.zst.origin",
        ".vmlinuz.origin",
        ".vmlinuz.zst.origin",
    ]


def cowork_bundle_candidates() -> List[Path]:
    paths = [
        portable_user_data_dir() / "vm_bundles" / "claudevm.bundle",
        roaming_app_data() / "Claude-3p" / "vm_bundles" / "claudevm.bundle",
        local_app_data() / "Claude-3p" / "vm_bundles" / "claudevm.bundle",
    ]
    packages = local_app_data() / "Packages"
    if packages.exists():
        for package in packages.glob("Claude_*"):
            paths.append(package / "LocalCache/Roaming/Claude-3p/vm_bundles/claudevm.bundle")
            paths.append(package / "LocalCache/Local/Claude-3p/vm_bundles/claudevm.bundle")
    unique: List[Path] = []
    seen: Set[str] = set()
    for path in paths:
        key = str(path).lower()
        if key not in seen:
            unique.append(path)
            seen.add(key)
    return unique


def sync_bundle_runtime_files(src_bundle: Path, dst_bundle: Path, dry_run: bool = False) -> int:
    if not src_bundle.exists():
        print(f"VM runtime 来源不存在: {src_bundle}")
        return 0
    copied = 0
    for name in bundle_runtime_file_names():
        src = src_bundle / name
        dst = dst_bundle / name
        if not src.exists():
            continue
        needs_copy = not dst.exists()
        if dst.exists():
            try:
                needs_copy = src.stat().st_size != dst.stat().st_size
            except OSError:
                needs_copy = True
        if not needs_copy:
            continue
        if dry_run:
            print(f"[dry-run] Would sync VM runtime file: {src} -> {dst}")
            copied += 1
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1
        print(f"已同步 VM runtime 文件: {dst.name}")
    return copied


def repair_portable_cowork_runtime(dry_run: bool = False) -> int:
    target_bundle = portable_user_data_dir() / "vm_bundles" / "claudevm.bundle"
    source = next((candidate for candidate in cowork_bundle_candidates() if candidate.exists() and candidate != target_bundle), None)
    if not source:
        print("没有找到可复用的 Cowork VM runtime bundle。")
        for candidate in cowork_bundle_candidates():
            print(f"  {candidate}")
        return 0
    print(f"Cowork VM runtime 来源: {source}")
    print(f"Cowork VM runtime 目标: {target_bundle}")
    copied = sync_bundle_runtime_files(source, target_bundle, dry_run)
    print(f"Cowork VM runtime 修复完成：同步 {copied} 个文件。")
    return 0


def cleanup_cowork_residue(target: Optional[str] = None) -> int:
    target_label = target or "portable-safe"
    force_official = "$true" if target == "portable" else "$false"
    force_portable = "$true" if target == "official" else "$false"
    script = rf"""
$ErrorActionPreference = 'SilentlyContinue'
function Stop-MatchingProcess([string]$Name, [string]$Needle, [bool]$ForceIt) {{
  Get-CimInstance Win32_Process -Filter "Name = '$Name'" | Where-Object {{
    $ForceIt -or (($_.ExecutablePath + ' ' + $_.CommandLine).ToLowerInvariant().Contains($Needle.ToLowerInvariant()))
  }} | ForEach-Object {{
    Write-Host "正在停止 $Name PID=$($_.ProcessId) $($_.ExecutablePath)"
    Invoke-CimMethod -InputObject $_ -MethodName Terminate | Out-Null
  }}
}}
Stop-MatchingProcess 'cowork-svc.exe' '\claudezhcn\claude\resources\cowork-svc.exe' $true
if ({force_official}) {{
  Stop-MatchingProcess 'cowork-svc.exe' '\windowsapps\claude_' $true
}}
if ({force_portable}) {{
  Stop-MatchingProcess 'cowork-svc.exe' '\claudezhcn\claude\resources\cowork-svc.exe' $true
}}
Write-Host "清理目标: {target_label}"
"""
    result = run([powershell_exe(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], check=False)
    if result.stdout.strip():
        print(result.stdout.strip())
    return 0 if result.returncode == 0 else result.returncode


def cowork_repair_wizard(target_dir: Path) -> int:
    while True:
        print()
        print("Cowork / VM 修复")
        print("1. 重新应用绿色版 Cowork 兼容补丁并重建启动器")
        print("2. 修复绿色版 Cowork VM runtime bundle（不复制多份 Profile VM）")
        print("3. 清理绿色版 Cowork 残留进程")
        print("4. 修复官方 Claude MSIX Cowork 沙箱（高级）")
        print("5. 显示 Cowork / VM 路径和大小")
        print("0. 返回")
        choice = prompt_line("请选择: ")
        if choice is None or choice == "0":
            return 0
        if choice == "1":
            return apply_cowork_compat(target_dir, False)
        if choice == "2":
            return repair_portable_cowork_runtime(False)
        if choice == "3":
            return cleanup_cowork_residue()
        if choice == "4":
            answer = prompt_line("这个操作会触碰官方 MSIX 沙箱。输入 REPAIR 继续: ")
            if answer == "REPAIR":
                return sync_msix_cowork_compat(False)
            print("已取消。")
            continue
        if choice == "5":
            for bundle in cowork_bundle_candidates():
                print_path_info("Cowork VM bundle", bundle)
            continue
        print("未知选项。")


def sync_msix_cowork_compat(dry_run: bool = False) -> int:
    packages_dir = local_app_data() / "Packages"
    if not packages_dir.exists():
        print(f"未找到 MSIX 包目录，跳过官方 Cowork 修复: {packages_dir}")
        return 0

    msix_pkgs = sorted(packages_dir.glob("Claude_*"))
    if not msix_pkgs:
        print("没有找到官方 Claude MSIX 包目录。")
        return 0

    src_candidates = [
        portable_user_data_dir() / "vm_bundles" / "claudevm.bundle" / "smol-bin.vhdx",
        roaming_app_data() / "Claude-3p" / "vm_bundles" / "claudevm.bundle" / "smol-bin.vhdx",
    ]
    src = next((candidate for candidate in src_candidates if candidate.exists()), src_candidates[0])
    if not src.exists():
        print("未找到绿色版 smol-bin.vhdx，跳过官方 Cowork 修复。已检查:")
        for candidate in src_candidates:
            print(f"  {candidate}")
    try:
        result = run(
            [
                powershell_exe(),
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                "Get-Service -Name 'CoworkVMService' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Status",
            ],
            check=False,
        )
        status = result.stdout.strip().lower()
        if status and status != "running":
            if dry_run:
                print(f"[dry-run] Would start CoworkVMService (currently {status}).")
            else:
                start = run(
                    [
                        powershell_exe(),
                        "-NoProfile",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-Command",
                        "Start-Service -Name 'CoworkVMService' -ErrorAction Stop",
                    ],
                    check=False,
                )
                if start.returncode == 0:
                    print("已启动 CoworkVMService。")
                else:
                    print(f"警告：无法启动 CoworkVMService: {start.stdout.strip()}")
        elif status == "running":
            print("CoworkVMService 已在运行。")
        else:
            print("未找到 CoworkVMService。")
    except Exception as exc:
        print(f"警告：无法检查/启动 CoworkVMService: {exc}")

    if not src.exists():
        return 0

    for pkg in msix_pkgs:
        dst = (
            pkg
            / "LocalCache"
            / "Roaming"
            / "Claude-3p"
            / "vm_bundles"
            / "claudevm.bundle"
            / "smol-bin.vhdx"
        )
        needs_copy = not dst.exists()
        if dst.exists():
            try:
                needs_copy = src.stat().st_size != dst.stat().st_size or file_sha256(src) != file_sha256(dst)
            except OSError:
                needs_copy = True

        if not needs_copy:
            print(f"官方 MSIX 沙箱 smol-bin.vhdx 已是最新: {dst}")
            continue

        if dry_run:
            print(f"[dry-run] Would sync smol-bin.vhdx to official MSIX sandbox: {dst}")
            continue

        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        print(f"已同步 smol-bin.vhdx 到官方 MSIX 沙箱: {dst}")

    return 0


def set_user_locale(dry_run: bool) -> None:
    for config in config_paths():
        if dry_run:
            print(f"[dry-run] Would set Claude config locale: {config}")
            continue

        data: Dict[str, Any] = {}
        should_backup = False
        if config.exists():
            try:
                loaded = load_json(config)
                if not isinstance(loaded, dict):
                    raise ValueError("top-level JSON value is not an object")
                data = loaded
                if data.get("locale") == LANG_CODE:
                    print(f"Claude 配置语言已是 {LANG_CODE}: {config}")
                    continue
                should_backup = True
            except Exception:
                backup = backup_file(config, "invalid")
                print(f"已有配置不是有效 JSON，已备份到 {backup}")
        if should_backup:
            backup = backup_file(config, "before-zh-CN")
            print(f"已备份 Claude 配置: {backup}")
        data["locale"] = LANG_CODE
        save_json(config, data)
        print(f"已设置 Claude 配置语言: {config}")


def enable_developer_mode(dry_run: bool) -> None:
    for settings in developer_settings_paths():
        if dry_run:
            print(f"[dry-run] Would enable Claude developer mode: {settings}")
            continue

        data: Dict[str, Any] = {}
        should_backup = False
        if settings.exists():
            try:
                loaded = load_json(settings)
                if not isinstance(loaded, dict):
                    raise ValueError("top-level JSON value is not an object")
                data = loaded
                if data.get("allowDevTools") is True:
                    print(f"Claude 开发者模式已启用: {settings}")
                    continue
                should_backup = True
            except Exception:
                backup = backup_file(settings, "invalid")
                print(f"已有开发者设置不是有效 JSON，已备份到 {backup}")

        if should_backup:
            backup = backup_file(settings, "before-zh-CN")
            print(f"已备份 Claude 开发者设置: {backup}")
        data["allowDevTools"] = True
        save_json(settings, data)
        print(f"已启用 Claude 开发者模式: {settings}")


def apply_user_settings(target_dir: Path) -> int:
    set_user_locale(False)
    enable_developer_mode(False)
    apply_locale_resources(target_dir, False)
    apply_cowork_compat(target_dir, False)
    ensure_portable_user_data_migrated()
    try:
        create_shortcuts(target_dir)
    except SystemExit as exc:
        print(exc)
    return 0


def initialize_build_args(target_dir: Path) -> argparse.Namespace:
    return argparse.Namespace(
        source=None,
        target_dir=target_dir,
        download_msix=True,
        force_download=False,
        in_place=False,
        dry_run=False,
    )


def build_patched_app(args: argparse.Namespace) -> Path:
    app_dir = prepare_app(args)
    apply_locale_resources(app_dir, False)
    apply_cowork_compat(app_dir, False)
    set_user_locale(False)
    enable_developer_mode(False)
    ensure_portable_user_data_migrated()
    verify(app_dir)
    create_shortcuts(app_dir, False)
    return app_dir


def initialize_tool(target_dir: Path) -> int:
    print("正在初始化 WIN CC Desktop zh-CN Portable...")
    if app_exe(target_dir.expanduser()):
        apply_locale_resources(target_dir, False)
        apply_cowork_compat(target_dir, False)
        set_user_locale(False)
        enable_developer_mode(False)
        ensure_portable_user_data_migrated()
        create_shortcuts(target_dir)
    else:
        print(f"尚未生成汉化版程序: {target_dir}")
        print("首次安装将自动下载或复用官方 Claude Desktop，并创建中文绿色版。")
        build_patched_app(initialize_build_args(target_dir))
    apply_third_party_inference_config(False, force_mode=False)
    clear_portable_deployment_mode(False)
    clear_disable_deployment_mode_chooser(primary_third_party_data_dir(), False)
    show_oauth_protocol()
    print("初始化完成。")
    return 0


def verify(app_dir: Path) -> None:
    frontend = app_dir / FRONTEND_I18N_REL / "zh-CN.json"
    data = load_json(frontend)
    values = [v for v in data.values() if isinstance(v, str)]
    chinese = sum(1 for v in values if re.search(r"[\u4e00-\u9fff]", v))
    print(f"已验证前端 zh-CN JSON：{chinese}/{len(values)} 条文本包含中文")

    desktop = app_dir / DESKTOP_RESOURCES_REL / "zh-CN.json"
    require_file(desktop)
    index_files = list((app_dir / FRONTEND_ASSETS_REL).glob("index-*.js"))
    if not any('"zh-CN"' in p.read_text(encoding="utf-8") for p in index_files):
        raise SystemExit("Verification failed: frontend language whitelist does not contain zh-CN")
    print("已验证前端语言白名单包含 zh-CN")


def launch(app_dir: Path) -> None:
    exe = app_exe(app_dir)
    if not exe:
        raise SystemExit(f"Cannot find Claude.exe in {app_dir}")
    print(f"正在启动 Claude: {exe}")
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        launcher = create_launcher(app_dir)
        subprocess.Popen(
            ["wscript.exe", str(launcher)],
            cwd=str(launcher.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creationflags,
        )
    else:
        env = os.environ.copy()
        env[COWORK_PORTABLE_ENV] = "1"
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
    print("Claude 已在独立进程中启动。这个工具窗口可以关闭，也可以返回菜单。")


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
            print(f"无法复制本机已安装包文件（{exc}），将回退到最新官方 MSIX。")
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
    parser.add_argument("--initialize", action="store_true", help="Initialize user settings, migration, shortcuts, and diagnostics")
    parser.add_argument("--migrate-user-data", action="store_true", help="Migrate legacy portable user data into the portable zh-CN profile")
    parser.add_argument("--import-sync-wizard", action="store_true", help="Open import/sync config wizard")
    parser.add_argument("--show-third-party-inference", action="store_true", help="Show Claude Desktop and Claude Code third-party model inference config")
    parser.add_argument("--check-third-party-sources", action="store_true", help="Check whether reusable third-party model inference config exists")
    parser.add_argument("--third-party-wizard", action="store_true", help="Open the third-party model inference config wizard")
    parser.add_argument("--apply-third-party-inference", action="store_true", help="Generate Desktop gateway config from Claude Code settings")
    parser.add_argument("--enter-third-party-mode", action="store_true", help="Force the portable profile to enter third-party gateway/API mode")
    parser.add_argument("--exit-third-party-mode", action="store_true", help="Stop forcing third-party gateway/API mode and restore Anthropic sign-in/mode chooser")
    parser.add_argument("--show-oauth-protocol", action="store_true", help="Show current claude:// protocol handler")
    parser.add_argument("--prepare-oauth-login", action="store_true", help="Temporarily point claude:// OAuth callback to the zh-CN launcher")
    parser.add_argument("--restore-oauth-protocol", action="store_true", help="Restore claude:// protocol handler from the latest backup")
    parser.add_argument("--show-claude-code", action="store_true", help="Show Claude Code install method, version, and command paths")
    parser.add_argument("--install-claude-code", action="store_true", help="Install or repair Claude Code")
    parser.add_argument("--update-claude-code", action="store_true", help="Update Claude Code according to the detected install method")
    parser.add_argument("--uninstall-claude-code", action="store_true", help="Uninstall Claude Code and optionally remove its config")
    parser.add_argument("--apply-cowork-compat", action="store_true", help="Patch portable Claude so Cowork can coexist with the official MSIX version")
    parser.add_argument("--cowork-repair-wizard", action="store_true", help="Open Cowork / VM repair wizard")
    parser.add_argument("--repair-portable-cowork-runtime", action="store_true", help="Sync missing Cowork VM runtime files into the portable profile")
    parser.add_argument("--cleanup-cowork-residue", action="store_true", help="Clean portable Cowork residue processes")
    parser.add_argument("--sync-msix-cowork", action="store_true", help="Advanced: repair official MSIX Cowork sandbox data after portable usage")
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
    if args.initialize:
        return initialize_tool(args.target_dir)
    if args.show_user_data:
        return show_user_data(args.target_dir)
    if args.migrate_user_data:
        return ensure_portable_user_data_migrated()
    if args.import_sync_wizard:
        return import_sync_wizard()
    if args.show_third_party_inference:
        return show_third_party_inference_config()
    if args.check_third_party_sources:
        return check_third_party_sources()
    if args.third_party_wizard:
        return third_party_config_wizard()
    if args.apply_third_party_inference:
        return apply_third_party_inference_config(False, force_mode=False)
    if args.enter_third_party_mode:
        return enter_third_party_mode(False)
    if args.exit_third_party_mode:
        return exit_third_party_mode(False)
    if args.show_oauth_protocol:
        return show_oauth_protocol()
    if args.prepare_oauth_login:
        return oauth_login_prepare(args.target_dir)
    if args.restore_oauth_protocol:
        return restore_oauth_protocol()
    if args.show_claude_code:
        return show_claude_code_status()
    if args.install_claude_code:
        return install_claude_code()
    if args.update_claude_code:
        return update_claude_code()
    if args.uninstall_claude_code:
        return uninstall_claude_code(args.yes)
    if args.apply_cowork_compat:
        return apply_cowork_compat(args.target_dir, args.dry_run)
    if args.cowork_repair_wizard:
        return cowork_repair_wizard(args.target_dir)
    if args.repair_portable_cowork_runtime:
        return repair_portable_cowork_runtime(args.dry_run)
    if args.cleanup_cowork_residue:
        return cleanup_cowork_residue()
    if args.sync_msix_cowork:
        return sync_msix_cowork_compat(args.dry_run)
    if args.patch_desktop_menu:
        return patch_hardcoded_desktop_menu_strings(args.target_dir, False)
    if args.apply_locale:
        return apply_locale_resources(args.target_dir, False)
    if args.clean_user_data:
        return clean_user_data(args.yes)
    if args.create_shortcuts:
        return create_shortcuts(args.target_dir, args.dry_run)
    if args.apply_user_settings:
        return apply_user_settings(args.target_dir)
    if args.full_clean:
        return full_clean(args.target_dir, args.yes)

    require_file(FRONTEND_TRANSLATION)
    require_file(DESKTOP_TRANSLATION)

    if args.dry_run:
        app_dir = prepare_app(args)
        apply_locale_resources(app_dir, args.dry_run)
        apply_cowork_compat(app_dir, args.dry_run)
        set_user_locale(args.dry_run)
        enable_developer_mode(args.dry_run)
        verify(app_dir)
        create_shortcuts(app_dir, args.dry_run)
    else:
        app_dir = build_patched_app(args)

    if args.launch and not args.dry_run:
        launch(app_dir)

    print(f"完成。汉化版 Claude 位于: {app_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
