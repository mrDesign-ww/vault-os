# Windows host helpers. Run the full hardened Vault OS runtime through WSL.
# Usage: powershell -File install-windows.ps1 -Fn <function> [-Arg1 x -Arg2 y]

function Ensure-Prereqs {
  foreach ($p in @('git','node','python')) {
    if (-not (Get-Command $p -ErrorAction SilentlyContinue)) {
      switch ($p) {
        'git'    { winget install --id Git.Git -e --source winget }
        'node'   { winget install --id OpenJS.NodeJS -e --source winget }
        'python' { winget install --id Python.Python.3.12 -e --source winget }
      }
    }
  }
  if (-not (Get-Command claude -ErrorAction SilentlyContinue)) { npm install -g '@anthropic-ai/claude-code' }
  Write-Host 'prereqs ok'
}

function Ensure-Obsidian {
  if (Get-Command obsidian -ErrorAction SilentlyContinue) { Write-Host 'Obsidian present' }
  else { winget install --id Obsidian.Obsidian -e --source winget }
}

function Ensure-Codex {
  # Codex desktop app; fall back to a note if unavailable via winget.
  try { winget install --id OpenAI.Codex -e --source winget } catch {
    Write-Host 'Install the Codex desktop app from OpenAI, then sign in.'
  }
}

function Make-Shortcut([string]$Vault, [string]$Name = 'Vault') {
  $desktop = [Environment]::GetFolderPath('Desktop')
  $lnk = Join-Path $desktop "$Name.lnk"
  $ws = New-Object -ComObject WScript.Shell
  $s = $ws.CreateShortcut($lnk)
  $s.TargetPath = "$env:ComSpec"
  $s.Arguments  = "/k cd /d `"$Vault`" && claude"
  $s.Save()
  Write-Host "Shortcut created: $lnk"
}

function Backup([string]$File) {
  if (Test-Path $File) {
    Copy-Item $File "$File.backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    Write-Host "backed up $File"
  }
}
