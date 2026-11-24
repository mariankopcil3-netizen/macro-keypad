param(
  [string]$Port = 'COM5',
  [int]$Baud = 115200,
  [switch]$DryRun,
  [switch]$Sim,
  [string]$ReplayFile = ''
)

$ErrorActionPreference = 'Stop'

Add-Type -MemberDefinition '[DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);' -Name NativeMethods -Namespace Win32

$script:lastPos = $null

function Write-HostLine {
  param([string]$Text)
  $ts = Get-Date -Format 'HH:mm:ss'
  Write-Host "[$ts] $Text"
}

function Invoke-VolumeStep {
  param([int]$Steps)
  if ($Steps -eq 0) { return }
  $vk = if ($Steps -gt 0) { 0xAF } else { 0xAE }
  $count = [Math]::Min([Math]::Abs($Steps), 5)
  if ($DryRun) {
    Write-HostLine ("[dry] volume {0} x {1}" -f ($(if ($Steps -gt 0) { '+' } else { '-' })), $count)
    return
  }
  for ($i = 0; $i -lt $count; $i++) {
    [Win32.NativeMethods]::keybd_event([byte]$vk, 0, 0, [UIntPtr]::Zero)
    [Win32.NativeMethods]::keybd_event([byte]$vk, 0, 2, [UIntPtr]::Zero)
    Start-Sleep -Milliseconds 30
  }
  Write-HostLine ("volume {0} x {1}" -f ($(if ($Steps -gt 0) { '+' } else { '-' })), $count)
}

function Invoke-LaunchApp {
  param([string]$Name)
  $uri = "$Name" + ':'
  if ($DryRun) {
    Write-HostLine "[dry] launch $uri"
    return
  }
  try {
    Start-Process $uri
    Write-HostLine "launched $uri"
  } catch {
    Write-HostLine "FAILED to launch $uri ($($_.Exception.Message))"
  }
}

function Invoke-MuteToggle {
  if ($DryRun) {
    Write-HostLine '[dry] mute toggle'
    return
  }
  [Win32.NativeMethods]::keybd_event([byte]0xAD, 0, 0, [UIntPtr]::Zero)
  [Win32.NativeMethods]::keybd_event([byte]0xAD, 0, 2, [UIntPtr]::Zero)
  Write-HostLine 'mute toggled'
}

function Handle-Line {
  param([string]$Line)
  if ([string]::IsNullOrWhiteSpace($Line)) { return }
  Write-HostLine "in: $Line"

  if ($Line -match '^MACRO:app:(.+)$') {
    Invoke-LaunchApp -Name $Matches[1].Trim()
    return
  }
  if ($Line -match '^MACRO:') {
    Write-HostLine 'ignored macro (no mapping yet)'
    return
  }
  if ($Line -match '^ENC1:(-?\d+)$') {
    $pos = [int]$Matches[1]
    if ($null -ne $script:lastPos) {
      $delta = $pos - $script:lastPos
      Invoke-VolumeStep -Steps $delta
    }
    $script:lastPos = $pos
    return
  }
  if ($Line -eq 'ENC1_SW:1') {
    Invoke-MuteToggle
    return
  }
  if ($Line -match '^(KEY:|ENC2:|ENC2_SW:1|PROFILE:.*)$') {
    return
  }
}

function Invoke-SimMode {
  if ($ReplayFile) {
    Write-HostLine "sim replay: $ReplayFile"
    foreach ($line in (Get-Content -LiteralPath $ReplayFile)) {
      Handle-Line -Line $line
    }
    return
  }
  Write-HostLine 'sim mode: zadej radky protokolu (prazdny radek = konec)'
  while ($true) {
    $line = Read-Host '>'
    if ([string]::IsNullOrWhiteSpace($line)) { break }
    Handle-Line -Line $line
  }
}

if ($Sim) {
  Invoke-SimMode
  exit 0
}

try {
  $serial = New-Object System.IO.Ports.SerialPort($Port, $Baud, [System.IO.Ports.Parity]::None, 8, [System.IO.Ports.StopBits]::One)
  $serial.ReadTimeout = 500
  $serial.Open()
} catch {
  Write-HostLine "cannot open $Port : $($_.Exception.Message)"
  $names = [System.IO.Ports.SerialPort]::GetPortNames()
  Write-HostLine "available ports: $($names -join ', ')"
  exit 1
}

Write-HostLine ("KeypadHost running on {0} @ {1} baud (DryRun={2})" -f $Port, $Baud, $DryRun)
Write-HostLine 'Ctrl+C to stop'

try {
  while ($true) {
    try {
      $line = $serial.ReadLine()
      Handle-Line -Line $line
    } catch [System.TimeoutException] {
      continue
    }
  }
} finally {
  if ($serial) { $serial.Close() }
  Write-HostLine 'stopped'
}
