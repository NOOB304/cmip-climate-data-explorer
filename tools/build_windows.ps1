param(
    [switch]$SkipTests,
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Dist = Join-Path $Root "dist\CMIP Climate Explorer"
$Work = Join-Path $Root "build\pyinstaller"
$Version = (& $Python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])").Trim()

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Virtual environment was not found at $Python"
}

if (-not $SkipTests) {
    & $Python -m ruff check src tests tools
    if ($LASTEXITCODE -ne 0) { throw "Ruff failed" }
    & $Python -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "Pytest failed" }
}

foreach ($Path in @($Dist, $Work)) {
    if (Test-Path -LiteralPath $Path) {
        $Resolved = (Resolve-Path -LiteralPath $Path).Path
        if (-not $Resolved.StartsWith($Root, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to remove path outside workspace: $Resolved"
        }
        Remove-Item -LiteralPath $Resolved -Recurse -Force
    }
}

& $Python -m PyInstaller --noconfirm --workpath $Work (Join-Path $Root "packaging\cmip_explorer.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$Portable = Join-Path $Root "build\CMIP-Climate-Explorer-$Version-x64-portable.zip"
if (Test-Path -LiteralPath $Portable) { Remove-Item -LiteralPath $Portable -Force }
Compress-Archive -Path (Join-Path $Dist "*") -DestinationPath $Portable -CompressionLevel Optimal

if (-not $SkipInstaller) {
    $Compiler = @(
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    ) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if ($Compiler) {
        & $Compiler (Join-Path $Root "packaging\installer.iss")
        if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
    } else {
        Write-Warning "Inno Setup 6 was not found; installer compilation was skipped."
    }
}

$Artifacts = @($Portable)
$Installer = Join-Path $Root "build\installer\CMIP-Climate-Explorer-$Version-x64-Setup.exe"
if (Test-Path -LiteralPath $Installer) { $Artifacts += $Installer }
$ChecksumFile = Join-Path $Root "build\SHA256SUMS.txt"
$ChecksumLines = @()
foreach ($Artifact in $Artifacts) {
    $Hash = Get-FileHash -LiteralPath $Artifact -Algorithm SHA256
    $Hash
    $ChecksumLines += "$($Hash.Hash.ToLowerInvariant())  $([System.IO.Path]::GetFileName($Artifact))"
}
$ChecksumLines | Set-Content -LiteralPath $ChecksumFile -Encoding ascii
