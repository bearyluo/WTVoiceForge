# Build the Windows installer and its uninstaller.
# Usage (from project root):
#   powershell -ExecutionPolicy Bypass -File packaging/build_installer.ps1
param(
    [string]$ExeDir = "D:\WTVoiceForge",
    [string]$OutDir = "D:\WTVoiceForge\installer-output",
    [string]$Iscc = "",
    [ValidateSet("onefile", "onedir")] [string]$AppMode = "onedir",
    [string]$SignTool = "",
    [string]$Certificate = "",
    [string]$CertificatePassword = "",
    [string]$TimestampUrl = "http://timestamp.digicert.com",
    [switch]$SkipSign
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$ExeDir = [System.IO.Path]::GetFullPath($ExeDir)
$Exe = Join-Path $ExeDir "WTVoiceForge.exe"
$Iss = Join-Path $Root "packaging\wtvoiceforge.iss"

if (-not (Test-Path $Exe)) {
    throw "Missing WTVoiceForge.exe. Run packaging/build_portable.ps1 first."
}
if ($AppMode -eq "onedir" -and -not (Test-Path (Join-Path $ExeDir "_internal"))) {
    throw "AppMode onedir requires $ExeDir\_internal. Build with -Mode onedir first."
}

if (-not $Iscc) {
    $candidates = @(
        "D:\WTVoiceForge\tools\InnoSetup7\ISCC.exe",
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 7\ISCC.exe"),
        (Join-Path $env:ProgramFiles "Inno Setup 7\ISCC.exe")
    )
    $Iscc = $candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
}
if (-not $Iscc -or -not (Test-Path $Iscc)) {
    throw "ISCC.exe not found. Install Inno Setup 7 or pass -Iscc."
}

$VersionLine = Select-String -Path (Join-Path $Root "app\config.py") -Pattern 'APP_VERSION\s*=\s*"([^"]+)"' | Select-Object -First 1
if (-not $VersionLine) { throw "Unable to read APP_VERSION from app/config.py" }
$Version = $VersionLine.Matches[0].Groups[1].Value

foreach ($required in @("README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "VGMSTREAM_COPYING")) {
    if (-not (Test-Path (Join-Path $ExeDir $required))) {
        throw "Missing $required in $ExeDir. Re-run packaging/build_portable.ps1."
    }
}

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
Write-Host "Version : $Version"
Write-Host "App dir : $ExeDir"
Write-Host "Compiler: $Iscc"
Write-Host "Output  : $OutDir"

$OutDirInno = $OutDir.Replace('\', '/')
& $Iscc "/DAppVersion=$Version" "/DExeDir=$ExeDir" "/DOutputDir=$OutDirInno" "/DAppMode=$AppMode" $Iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup build failed." }

$Setup = Join-Path $OutDir "WTVoiceForge-Setup-$Version.exe"
if (-not (Test-Path $Setup)) { throw "Missing output: $Setup" }

function Invoke-Sign([string]$Path) {
    if ($SkipSign -or -not $Certificate) {
        Write-Host "Signing: skipped (no certificate provided)"
        return
    }
    $tool = $SignTool
    if (-not $tool) {
        $candidates = @(
            (Get-Command signtool.exe -ErrorAction SilentlyContinue | Select-Object -First 1).Source,
            (Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin\10.0.26100.0\x64\signtool.exe"),
            (Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin\10.0.22621.0\x64\signtool.exe")
        )
        $tool = $candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
    }
    if (-not $tool -or -not (Test-Path $tool)) {
        throw "signtool.exe not found. Install the Windows SDK or pass -SignTool."
    }
    if (-not (Test-Path $Certificate)) { throw "Certificate not found: $Certificate" }
    $signArgs = @("sign", "/fd", "SHA256", "/tr", $TimestampUrl, "/td", "SHA256", "/f", $Certificate)
    if ($CertificatePassword) { $signArgs += @("/p", $CertificatePassword) }
    $signArgs += $Path
    & $tool @signArgs
    if ($LASTEXITCODE -ne 0) { throw "signtool failed for $Path" }
}
Invoke-Sign $Setup

$Hash = (Get-FileHash -Algorithm SHA256 $Setup).Hash.ToLower()
$HashFile = Join-Path $OutDir "SHA256SUMS.txt"
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($HashFile, "$Hash  WTVoiceForge-Setup-$Version.exe`n", $Utf8NoBom)

Write-Host ""
Write-Host "OK: $Setup"
Write-Host "SHA256: $Hash"
Write-Host "Uninstaller is generated inside the installed app folder (unins000.exe)."
