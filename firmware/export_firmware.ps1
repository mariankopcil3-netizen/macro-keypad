# Copies the built firmware binary out of the PlatformIO build directory
# (C:/Temp/keypad_build - see platformio.ini) into firmware/release/,
# ready to attach to a GitHub release.
#
# Usage:  powershell -ExecutionPolicy Bypass -File firmware/export_firmware.ps1
#         powershell ... -Upload COM7    # flash the board first
param(
    [string]$Upload = ""
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$build = "C:\Temp\keypad_build\upesy_wroom"
$out = Join-Path $here "release"
Set-Location $here

if ($Upload) {
    & "C:\.platformio\penv\Scripts\pio.exe" run -t upload --upload-port $Upload
    if ($LASTEXITCODE -ne 0) { throw "flash failed" }
}
else {
    & "C:\.platformio\penv\Scripts\pio.exe" run
    if ($LASTEXITCODE -ne 0) { throw "build failed" }
}

New-Item -ItemType Directory -Force $out | Out-Null
Copy-Item "$build\firmware.bin" "$out\firmware.bin" -Force
Copy-Item "$build\firmware.elf" "$out\firmware.elf" -Force

$version = (Select-String -Path (Join-Path $here "src\main.cpp") `
    -Pattern '#define FIRMWARE_VERSION (\d+)').Matches[0].Groups[1].Value
$stamp = Get-Date -Format "yyyyMMdd"
$target = "$out\fintr-deck-firmware-v$version-$stamp.bin"
Copy-Item "$out\firmware.bin" $target -Force

$md5 = (Get-FileHash $target -Algorithm MD5).Hash.ToLower()
"$md5  $(Split-Path -Leaf $target)" | Set-Content "$target.md5"

Write-Output "exported:"
Write-Output "  $target"
Write-Output "  $target.md5"
