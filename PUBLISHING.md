# Publishing Guide

This guide assumes Git and GitHub CLI are installed. On this machine, GitHub CLI
is installed with Scoop as `gh`.

## 1. Login to GitHub

Open a new PowerShell window so the GitHub CLI PATH is refreshed, then run:

```powershell
gh auth login --web --git-protocol https --hostname github.com
```

Choose GitHub.com and browser login. Since the default browser is already signed
in, GitHub should only ask for confirmation.

If `gh` is still not found in the current terminal, use the Scoop shim path:

```powershell
& "$env:USERPROFILE\scoop\shims\gh.exe" auth login --web --git-protocol https --hostname github.com
```

## 2. Initialize the local repository

```powershell
cd C:\Users\TC\Downloads\claude-desktop-zh-cn-main
git init
git branch -M main
git add .
git commit -m "Initial release"
```

If Git asks for your identity:

```powershell
git config --global user.name "Your Name"
git config --global user.email "your-email@example.com"
```

## 3. Create and push the public repo

Suggested repository name:

```text
CC-desktop-zh-cn-WIN-portable
```

Create and push:

```powershell
gh repo create CC-desktop-zh-cn-WIN-portable --public --source . --remote origin --push
```

Or with the Scoop shim path:

```powershell
& "$env:USERPROFILE\scoop\shims\gh.exe" repo create CC-desktop-zh-cn-WIN-portable --public --source . --remote origin --push
```

## 4. Before publishing

Run these checks:

```powershell
python -m py_compile .\cc_desktop_zh_cn_windows.py
$errors = $null
[System.Management.Automation.PSParser]::Tokenize((Get-Content -Raw -Encoding UTF8 .\cc_desktop_tool.ps1), [ref]$errors) > $null
if ($errors.Count -gt 0) { $errors | Format-List *; exit 1 }
[System.Management.Automation.PSParser]::Tokenize((Get-Content -Raw -Encoding UTF8 .\cc_desktop_tool_zh.ps1), [ref]$errors) > $null
if ($errors.Count -gt 0) { $errors | Format-List *; exit 1 }
```

To generate release assets similar to the GitHub release screenshot, run:

```powershell
.\build_release_assets.ps1
```

This creates:

- `WIN-CC-Desktop-zh-CN-Portable-v<version>.zip`
- `WIN-CC-Desktop-zh-CN-Portable-v<version>.zip.sha256`

The version is inferred from the first heading in `CHANGELOG.md`, or you can
override it with:

```powershell
.\build_release_assets.ps1 -Version 0.2.5
```

Make sure these are not committed:

- Official installers or MSIX files.
- Unpacked application binaries.
- `%LOCALAPPDATA%\ClaudeZhCN` runtime files.
- `%APPDATA%\Claude` or `%APPDATA%\Claude-3p` user data.
- `%USERPROFILE%\.claude` configuration, tokens, API keys, logs, or backups.
