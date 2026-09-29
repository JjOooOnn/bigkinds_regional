param(
    [Parameter(Mandatory = $true)]
    [string]$PythonArchive,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[a-fA-F0-9]{64}$')]
    [string]$PythonArchiveSha256,
    [string]$BuildPython,
    [string]$OutputDirectory
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
# PowerShell 7 can pass its module paths to powershell.exe. Load the matching
# built-in utility module explicitly so Get-FileHash works in that case too.
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1') -ErrorAction Stop
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $projectRoot 'release' }
if (-not $BuildPython) { $BuildPython = Join-Path $projectRoot '.venv\Scripts\python.exe' }
$outputRoot = [IO.Path]::GetFullPath($OutputDirectory)
$archiveInput = (Resolve-Path -LiteralPath $PythonArchive).Path
$builder = (Resolve-Path -LiteralPath $BuildPython).Path
$actualHash = (Get-FileHash -LiteralPath $archiveInput -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualHash -ne $PythonArchiveSha256.ToLowerInvariant()) {
    throw 'Python ZIP SHA-256 mismatch. Obtain the expected hash from a trusted source.'
}

function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed (exit $LASTEXITCODE): $Executable $($Arguments -join ' ')"
    }
}

Invoke-Checked $builder @('-c', "import sys, struct; assert sys.platform == 'win32' and sys.version_info[:2] == (3, 12) and struct.calcsize('P') == 8, 'Build Python must be Windows x64 Python 3.12'")
$npm = (Get-Command npm.cmd -ErrorAction Stop).Source
$manifest = Get-Content -Raw -LiteralPath (Join-Path $projectRoot 'frontend\package.json') | ConvertFrom-Json
$version = [string]$manifest.version
if ($version -notmatch '^\d+\.\d+\.\d+$') { throw 'Invalid product version.' }
$archiveName = "bigkinds_regional-v$version-windows-x64.zip"
$archivePath = Join-Path $outputRoot $archiveName
if ((Test-Path -LiteralPath $archivePath) -or (Test-Path -LiteralPath "$archivePath.sha256")) {
    throw "Release already exists. Use a new version or output directory: $archivePath"
}

New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null
$stagingRoot = Join-Path $outputRoot ('.portable-' + [guid]::NewGuid().ToString('N'))
$bundle = Join-Path $stagingRoot 'bundle'
$runtime = Join-Path $bundle 'runtime'
$python = Join-Path $runtime 'python.exe'
$utf8 = New-Object System.Text.UTF8Encoding($false)
$savedEnvironment = @{}
foreach ($name in @('BIGKINDS_RUNTIME', 'BIGKINDS_DATA_DIR', 'PLAYWRIGHT_BROWSERS_PATH', 'PYTHONDONTWRITEBYTECODE', 'PYTHONUTF8', 'PATH')) {
    $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}

function Copy-BundleFile {
    param([string]$Source, [string]$RelativeDestination)
    $target = Join-Path $bundle $RelativeDestination
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
    Copy-Item -LiteralPath $Source -Destination $target
}

try {
    New-Item -ItemType Directory -Force -Path $runtime | Out-Null
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Compression.ZipFile]::ExtractToDirectory($archiveInput, $runtime)
    foreach ($required in @('python.exe', 'python312.dll', 'python312.zip', 'python312._pth', 'LICENSE.txt')) {
        if (-not (Test-Path -LiteralPath (Join-Path $runtime $required) -PathType Leaf)) {
            throw "Not a Python 3.12 embeddable ZIP: missing $required"
        }
    }
    # Explicit paths keep the host Python, user site-packages and PYTHONPATH out.
    [IO.File]::WriteAllText((Join-Path $runtime 'python312._pth'), "python312.zip`n.`nLib/site-packages`n..`nimport site`n", $utf8)
    $env:PYTHONDONTWRITEBYTECODE = '1'
    $env:PYTHONUTF8 = '1'
    Invoke-Checked $python @('-c', "import sys, struct; assert sys.platform == 'win32' and sys.version_info[:2] == (3, 12) and struct.calcsize('P') == 8, 'Runtime must be Windows x64 Python 3.12'")
    $pythonVersion = & $python -c 'import platform; print(platform.python_version())'
    if ($LASTEXITCODE -ne 0) { throw 'Unable to read Python version.' }

    # Fail if the locked native wheels are unavailable; never fall back to newer versions.
    Invoke-Checked $builder @('-m', 'pip', '--disable-pip-version-check', 'install', '--only-binary=:all:', '--no-compile',
        '--target', (Join-Path $runtime 'Lib\site-packages'), '-r', (Join-Path $projectRoot 'requirements.lock.txt'))

    Push-Location (Join-Path $projectRoot 'frontend')
    try {
        Invoke-Checked $npm @('ci')
        Invoke-Checked $npm @('run', 'build')
    }
    finally { Pop-Location }

    Copy-BundleFile (Join-Path $projectRoot 'run_web.py') 'run_web.py'
    Copy-BundleFile (Join-Path $PSScriptRoot 'portable_launcher.py') 'portable_launcher.py'
    Copy-BundleFile (Join-Path $PSScriptRoot 'portable_smoke.py') 'scripts\portable_smoke.py'
    Copy-BundleFile (Join-Path $projectRoot 'requirements.lock.txt') 'requirements.lock.txt'
    Copy-BundleFile (Join-Path $projectRoot 'frontend\package.json') 'frontend\package.json'
    Copy-BundleFile (Join-Path $projectRoot 'docs\setup-and-submission.md') '사용안내.md'
    # UTF-8 without BOM and CRLF are required by cmd.exe, including Korean paths.
    $batch = [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'start_portable.bat')) -replace '\r?\n', "`r`n"
    [IO.File]::WriteAllText((Join-Path $bundle '실행.bat'), $batch, $utf8)
    foreach ($directory in @('src', 'frontend\dist')) {
        $source = Join-Path $projectRoot $directory
        foreach ($file in Get-ChildItem -LiteralPath $source -Recurse -File) {
            $relative = $file.FullName.Substring($projectRoot.Length + 1)
            if ($relative -match '(^|[\\/])__pycache__([\\/]|$)' -or $file.Extension -in @('.pyc', '.pyo')) { continue }
            Copy-BundleFile $file.FullName $relative
        }
    }

    # Keep wheel metadata/licenses intact and copy licenses for bundled frontend dependencies.
    # Python handles npm's empty package key, which ConvertFrom-Json rejects in PS 5.1.
    $inventory = @'
import importlib.metadata as m
import json
from pathlib import Path
import shutil
import sys

frontend, destination = map(Path, sys.argv[1:])
lock = json.loads((frontend / 'package-lock.json').read_text(encoding='utf-8'))
for name, entry in lock['packages'].items():
    if not name.startswith('node_modules/') or entry.get('dev'):
        continue
    files = [p for p in (frontend / name).iterdir() if p.is_file() and p.name.upper().startswith(('LICENSE', 'LICENCE', 'COPYING', 'NOTICE'))]
    if not files:
        raise RuntimeError(f'Missing frontend license: {name}')
    target = destination / 'frontend' / name.removeprefix('node_modules/')
    target.mkdir(parents=True, exist_ok=True)
    for file in files:
        shutil.copy2(file, target / file.name)
packages = sorted(({'name': d.metadata['Name'], 'version': d.version, 'license': d.metadata.get('License-Expression') or d.metadata.get('License', '')} for d in m.distributions()), key=lambda p: p['name'].lower())
destination.mkdir(parents=True, exist_ok=True)
(destination / 'python-packages.json').write_text(json.dumps(packages, ensure_ascii=False, indent=2), encoding='utf-8')
'@
    $inventoryScript = Join-Path $stagingRoot 'package_inventory.py'
    [IO.File]::WriteAllText($inventoryScript, $inventory, $utf8)
    Invoke-Checked $python @($inventoryScript, (Join-Path $projectRoot 'frontend'), (Join-Path $bundle 'licenses'))

    $env:BIGKINDS_RUNTIME = 'local'
    $env:BIGKINDS_DATA_DIR = Join-Path $stagingRoot 'smoke-data'
    $env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $bundle 'browsers'
    Invoke-Checked $python @('-m', 'playwright', 'install', 'chromium')
    $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot"
    $smokeScript = Join-Path $bundle 'scripts\portable_smoke.py'
    Invoke-Checked $python @('-B', '-X', 'utf8', $smokeScript, '--root', $bundle)

    $releaseInfo = [ordered]@{
        product_version = $version
        platform = 'windows-x64'
        python_version = [string]$pythonVersion
        python_archive_sha256 = $actualHash
        requirements_sha256 = (Get-FileHash -LiteralPath (Join-Path $bundle 'requirements.lock.txt') -Algorithm SHA256).Hash.ToLowerInvariant()
        frontend_lock_sha256 = (Get-FileHash -LiteralPath (Join-Path $projectRoot 'frontend\package-lock.json') -Algorithm SHA256).Hash.ToLowerInvariant()
        built_at_utc = [DateTime]::UtcNow.ToString('o')
        smoke_passed = $true
    }
    [IO.File]::WriteAllText((Join-Path $bundle 'release-info.json'), ($releaseInfo | ConvertTo-Json), $utf8)
    $notices = @'
Third-party components
Python: runtime/LICENSE.txt
Python packages: licenses/python-packages.json; original licenses and notices remain
inside runtime/Lib/site-packages, including each package's .dist-info directory.
Playwright and its Node.js driver: runtime/Lib/site-packages/playwright.
Chromium/FFmpeg: original licenses and notices remain in browsers/.
Frontend runtime dependencies: licenses/frontend/.
'@
    [IO.File]::WriteAllText((Join-Path $bundle 'THIRD-PARTY-NOTICES.txt'), $notices, $utf8)

    # Read-only archive filtering prevents caches, private results and absolute cache links leaking.
    $tempArchive = Join-Path $stagingRoot $archiveName
    $zip = [IO.Compression.ZipFile]::Open($tempArchive, [IO.Compression.ZipArchiveMode]::Create)
    try {
        foreach ($file in Get-ChildItem -LiteralPath $bundle -File -Recurse -Force) {
            $relative = $file.FullName.Substring($bundle.Length + 1).Replace('\', '/')
            if ($relative -match '(^|/)(__pycache__|\.links)(/|$)' -or $file.Extension -in @('.pyc', '.pyo')) { continue }
            if ($relative -match '^(output|work|artifacts)/') { throw "Unexpected runtime data in bundle: $relative" }
            [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $file.FullName, $relative, [IO.Compression.CompressionLevel]::Optimal) | Out-Null
        }
    }
    finally { $zip.Dispose() }
    # Test the ZIP itself after relocation, without Python or Node on PATH.
    $extracted = Join-Path $stagingRoot '압축 해제 검증'
    [IO.Compression.ZipFile]::ExtractToDirectory($tempArchive, $extracted)
    Invoke-Checked (Join-Path $extracted 'runtime\python.exe') @('-B', '-X', 'utf8',
        (Join-Path $extracted 'scripts\portable_smoke.py'), '--root', $extracted)
    $zipHash = (Get-FileHash -LiteralPath $tempArchive -Algorithm SHA256).Hash.ToLowerInvariant()
    [IO.File]::WriteAllText((Join-Path $stagingRoot "$archiveName.sha256"), "$zipHash  $archiveName`r`n", [Text.Encoding]::ASCII)
    Move-Item -LiteralPath $tempArchive -Destination $archivePath
    Move-Item -LiteralPath (Join-Path $stagingRoot "$archiveName.sha256") -Destination "$archivePath.sha256"
    Write-Output "Portable ZIP: $archivePath"
    Write-Output "SHA-256: $zipHash"
}
finally {
    foreach ($name in $savedEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], 'Process')
    }
    # Only delete this invocation's unique staging directory, never the output root.
    $resolvedStage = [IO.Path]::GetFullPath($stagingRoot)
    if ((Split-Path -Parent $resolvedStage) -ne $outputRoot.TrimEnd('\') -or (Split-Path -Leaf $resolvedStage) -notmatch '^\.portable-[a-f0-9]{32}$') {
        throw "Refusing to clean unexpected staging path: $resolvedStage"
    }
    if (Test-Path -LiteralPath $resolvedStage) {
        Remove-Item -LiteralPath $resolvedStage -Recurse -Force
    }
}
