param(
    [string]$OutputDirectory
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $projectRoot "release"
}
$outputRoot = [System.IO.Path]::GetFullPath($OutputDirectory)
$manifest = Get-Content -Raw -LiteralPath (Join-Path $projectRoot "frontend\package.json") | ConvertFrom-Json
$version = [string]$manifest.version
if ($version -notmatch '^\d+\.\d+\.\d+$') {
    throw "frontend/package.json의 버전이 올바르지 않습니다: $version"
}

$archiveName = "bigkinds_regional-v$version-source.zip"
$archivePath = Join-Path $outputRoot $archiveName
$hashPath = "$archivePath.sha256"
$stagingRoot = Join-Path $outputRoot (".staging-" + [guid]::NewGuid().ToString("N"))

$rootFiles = @(
    ".gitignore",
    "README.md",
    "requirements.txt",
    "requirements.lock.txt",
    "pytest.ini",
    "main.py",
    "run_web.py",
    "start_web.bat"
)
$sourceDirectories = @("src", "frontend", "tests", "scripts")
$excludedDirectoryNames = @(".git", ".idea", ".venv", "node_modules", "dist", "output", "work", "artifacts", "release", "archive", "__pycache__", "coverage")
$excludedExtensions = @(".pyc", ".pyo", ".tsbuildinfo", ".zip", ".xlsx", ".xls", ".ico", ".hwp")

function Copy-ProjectFile {
    param([string]$RelativePath)

    $source = Join-Path $projectRoot $RelativePath
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "제출 필수 파일을 찾을 수 없습니다: $RelativePath"
    }
    $destination = Join-Path $stagingRoot $RelativePath
    $destinationDirectory = Split-Path -Parent $destination
    New-Item -ItemType Directory -Force -Path $destinationDirectory | Out-Null
    Copy-Item -LiteralPath $source -Destination $destination
}

New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null
New-Item -ItemType Directory -Force -Path $stagingRoot | Out-Null

try {
    foreach ($relativePath in $rootFiles) {
        Copy-ProjectFile $relativePath
    }

    foreach ($directory in $sourceDirectories) {
        $sourceRoot = Join-Path $projectRoot $directory
        Get-ChildItem -LiteralPath $sourceRoot -File -Recurse -Force | ForEach-Object {
            $relativePath = $_.FullName.Substring($projectRoot.Length).TrimStart('\')
            $segments = $relativePath -split '[\\/]'
            $hasExcludedDirectory = $false
            foreach ($segment in $segments[0..([Math]::Max(0, $segments.Count - 2))]) {
                if ($excludedDirectoryNames -contains $segment) {
                    $hasExcludedDirectory = $true
                    break
                }
            }
            if (-not $hasExcludedDirectory -and $excludedExtensions -notcontains $_.Extension.ToLowerInvariant()) {
                Copy-ProjectFile $relativePath
            }
        }
    }

    Copy-ProjectFile "docs\README.md"
    Copy-ProjectFile "docs\setup-and-submission.md"

    if (Test-Path -LiteralPath $archivePath) {
        Remove-Item -LiteralPath $archivePath -Force
    }
    if (Test-Path -LiteralPath $hashPath) {
        Remove-Item -LiteralPath $hashPath -Force
    }

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::CreateFromDirectory(
        $stagingRoot,
        $archivePath,
        [System.IO.Compression.CompressionLevel]::Optimal,
        $false
    )

    $archive = [System.IO.Compression.ZipFile]::OpenRead($archivePath)
    try {
        $forbidden = $archive.Entries | Where-Object {
            $entry = $_.FullName.Replace('\', '/')
            $entry -match '(^|/)(\.git|\.idea|\.venv|node_modules|dist|output|work|artifacts|release|archive|__pycache__)(/|$)' -or
            $entry -match '\.(pyc|pyo|tsbuildinfo|zip|xlsx?|ico|hwp)$'
        }
        if ($forbidden) {
            throw "제출 ZIP에 제외 대상이 포함되었습니다: $($forbidden.FullName -join ', ')"
        }
    }
    finally {
        $archive.Dispose()
    }

    $hash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    Set-Content -LiteralPath $hashPath -Encoding ascii -Value "$hash  $archiveName"
    Write-Output "제출 ZIP: $archivePath"
    Write-Output "SHA-256: $hash"
}
finally {
    if (Test-Path -LiteralPath $stagingRoot) {
        Remove-Item -LiteralPath $stagingRoot -Recurse -Force
    }
}
