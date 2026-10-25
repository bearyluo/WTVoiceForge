# Build a portable Windows app that does not require Python.
# Usage (from project root):
#   powershell -ExecutionPolicy Bypass -File packaging/build_portable.ps1 -OutDir D:\WTVoiceForge
param(
    [string]$OutDir = "",
    [string]$Python = "py -3.13",
    [ValidateSet("onefile", "onedir")] [string]$Mode = "onefile",
    [string]$SignTool = "",
    [string]$Certificate = "",
    [string]$CertificatePassword = "",
    [string]$TimestampUrl = "http://timestamp.digicert.com",
    [switch]$SkipSign
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not $OutDir) { $OutDir = Join-Path $Root "dist" }
$OutDir = [System.IO.Path]::GetFullPath($OutDir)
$WorkDir = Join-Path $OutDir "build"
$Log = Join-Path $OutDir "build.log"
$DistDir = if ($Mode -eq "onedir") { Join-Path $WorkDir "dist" } else { $OutDir }

New-Item -ItemType Directory -Force -Path $OutDir, $WorkDir | Out-Null

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
        if (-not $tool) {
            $sdk = Get-ChildItem (Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin") -Directory -ErrorAction SilentlyContinue |
                Sort-Object Name -Descending | Select-Object -First 1
            if ($sdk) { $tool = Join-Path $sdk.FullName "x64\signtool.exe" }
        }
    }
    if (-not $tool -or -not (Test-Path $tool)) {
        throw "signtool.exe not found. Install the Windows SDK or pass -SignTool."
    }
    if (-not (Test-Path $Certificate)) { throw "Certificate not found: $Certificate" }
    Write-Host "Signing: $Path"
    $signArgs = @("sign", "/fd", "SHA256", "/tr", $TimestampUrl, "/td", "SHA256", "/f", $Certificate)
    if ($CertificatePassword) { $signArgs += @("/p", $CertificatePassword) }
    $signArgs += $Path
    & $tool @signArgs
    if ($LASTEXITCODE -ne 0) { throw "signtool failed for $Path" }
}

$pythonParts = $Python -split "\s+"
$pythonExe = $pythonParts[0]
$pythonArgs = @()
if ($pythonParts.Length -gt 1) { $pythonArgs = $pythonParts[1..($pythonParts.Length - 1)] }

# The EXE is windowed: fail the build early if the desktop runtime is missing,
# otherwise the packaged app starts a server but cannot open its own window.
& $pythonExe @pythonArgs -c "import webview, clr; from webview.platforms import winforms; assert winforms._is_chromium()" 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "pywebview/pythonnet or WebView2 is unavailable for $Python. Install pywebview and pythonnet first."
}

Write-Host "Python : $Python"
Write-Host "Output : $OutDir"

$vendor = Join-Path $Root "vendor\vgmstream"
$vgmExe = Join-Path $vendor "vgmstream-cli.exe"
if (-not (Test-Path $vgmExe)) {
    Write-Host "Preparing vgmstream runtime..."
    & $pythonExe @pythonArgs (Join-Path $Root "packaging\fetch_vgmstream.py") --dest $vendor
    if ($LASTEXITCODE -ne 0) { throw "vgmstream preparation failed" }
}

$pyinstaller = @(
    "-m", "PyInstaller",
    "--noconfirm", "--clean", "--windowed",
    "--name", "WTVoiceForge",
    "--add-data", "$Root\web;web",
    "--distpath", $DistDir,
    "--workpath", $WorkDir,
    "--specpath", $WorkDir
)
if ($Mode -eq "onedir") {
    $pyinstaller += "--onedir"
    Write-Host "Mode   : onedir"
} else {
    $pyinstaller += "--onefile"
    Write-Host "Mode   : onefile"
}
$appIcon = Join-Path $Root "packaging\app.ico"
if (Test-Path $appIcon) {
    $pyinstaller += @("--icon", $appIcon)
    Write-Host "icon: $appIcon"
}
$versionFile = Join-Path $Root "packaging\version_info.txt"
if (Test-Path $versionFile) {
    $pyinstaller += @("--version-file", $versionFile)
    Write-Host "version info: $versionFile"
}
if (Test-Path $vgmExe) {
    $pyinstaller += @("--add-data", "$vendor;vendor/vgmstream")
    Write-Host "vgmstream: bundled"
} else {
    Write-Host "vgmstream: runtime download only"
}
$quickData = Join-Path $Root "app\data"
if (Test-Path $quickData) {
    $pyinstaller += @("--add-data", "$quickData;app/data")
    Write-Host "quick build text: bundled"
}
$rvcBridge = Join-Path $Root "app\engines\rvc_bridge"
if (Test-Path $rvcBridge) {
    $pyinstaller += @("--add-data", "$rvcBridge;app/engines/rvc_bridge")
    Write-Host "RVC bridge: bundled"
}
# Bundled WebView2 panel (pywebview / pythonnet)
$pyinstaller += @(
    "--collect-all", "webview",
    "--hidden-import", "webview.platforms.winforms",
    "--hidden-import", "clr"
)
$pyinstaller += @("$Root\app\launcher.py")

$ErrorActionPreference = "Continue"
& $pythonExe @pythonArgs @pyinstaller 2>&1 | Tee-Object -FilePath $Log
$buildCode = $LASTEXITCODE
$ErrorActionPreference = "Stop"
if ($buildCode -ne 0) {
    throw "PyInstaller build failed. See $Log"
}

if ($Mode -eq "onedir") {
    $builtDir = Join-Path $DistDir "WTVoiceForge"
    $builtExe = Join-Path $builtDir "WTVoiceForge.exe"
    if (-not (Test-Path $builtExe)) { throw "Missing output: $builtExe" }
    if (Test-Path (Join-Path $OutDir "_internal")) {
        Remove-Item (Join-Path $OutDir "_internal") -Recurse -Force
    }
    Copy-Item (Join-Path $builtDir "*") $OutDir -Recurse -Force
    Remove-Item $builtDir -Recurse -Force -ErrorAction SilentlyContinue
}

$exe = Join-Path $OutDir "WTVoiceForge.exe"
if (-not (Test-Path $exe)) { throw "Missing output: $exe" }
if ($Mode -eq "onedir" -and -not (Test-Path (Join-Path $OutDir "_internal"))) {
    throw "onedir build is missing _internal"
}
Invoke-Sign $exe

foreach ($doc in @("README.md", "THIRD_PARTY_NOTICES.md", "LICENSE")) {
    $source = Join-Path $Root $doc
    if (Test-Path $source) { Copy-Item $source $OutDir -Force }
}
if (Test-Path (Join-Path $vendor "COPYING")) {
    Copy-Item (Join-Path $vendor "COPYING") (Join-Path $OutDir "VGMSTREAM_COPYING") -Force
}

Write-Host ""
Write-Host "OK: $exe ($Mode)"
Write-Host "Data directory: $OutDir\data"
Write-Host "Double-click the exe to run. Python is not required."
if (-not $Certificate) {
    Write-Host "Code signing skipped: pass -Certificate (and -CertificatePassword) to sign."
}
