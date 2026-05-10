[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [switch]$Delete
)

$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$runtimeDir = Join-Path $projectRoot "runtime"
$logDir = Join-Path $runtimeDir "log"
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$archiveDir = Join-Path $logDir "legacy-$timestamp"

if (-not (Test-Path $runtimeDir)) {
    Write-Host "[OK] runtime folder does not exist."
    exit 0
}

New-Item -ItemType Directory -Force -Path $logDir | Out-Null
if (-not $Delete) {
    New-Item -ItemType Directory -Force -Path $archiveDir | Out-Null
}

$logDirFull = (Resolve-Path $logDir).Path.TrimEnd("\")
$runtimeDirFull = (Resolve-Path $runtimeDir).Path.TrimEnd("\")
$candidates = @(
    Get-ChildItem -Path $runtimeDir -Recurse -Force -File -ErrorAction SilentlyContinue |
        Where-Object {
            $fullName = $_.FullName
            $inLogDir = $fullName.StartsWith($logDirFull, [StringComparison]::OrdinalIgnoreCase)
            $isProcessLog = $_.Name.EndsWith(".log", [StringComparison]::OrdinalIgnoreCase)
            $isE2EObservation = $_.Name -like "youtube_bridge_e2e_*.jsonl"
            $isCanonicalTrace = $_.Name -eq "llm_trace.jsonl"
            (-not $inLogDir) -and (-not $isCanonicalTrace) -and ($isProcessLog -or $isE2EObservation)
        }
)

if ($candidates.Count -eq 0) {
    Write-Host "[OK] No scattered runtime process logs found."
    exit 0
}

$moved = 0
$deleted = 0
$skipped = 0

foreach ($file in $candidates) {
    try {
        $relative = $file.FullName.Substring($runtimeDirFull.Length).TrimStart("\")
        if ($Delete) {
            if ($PSCmdlet.ShouldProcess($file.FullName, "Delete runtime process log")) {
                Remove-Item -LiteralPath $file.FullName -Force
                Write-Host "[DELETE] $relative"
                $deleted += 1
            }
        } else {
            $target = Join-Path $archiveDir $relative
            $targetParent = Split-Path -Parent $target
            New-Item -ItemType Directory -Force -Path $targetParent | Out-Null
            if ($PSCmdlet.ShouldProcess($file.FullName, "Move runtime process log to $target")) {
                Move-Item -LiteralPath $file.FullName -Destination $target -Force
                Write-Host "[MOVE] $relative -> runtime\log\$(Split-Path -Leaf $archiveDir)\$relative"
                $moved += 1
            }
        }
    } catch {
        Write-Host "[SKIP] $($file.FullName): $($_.Exception.Message)"
        $skipped += 1
    }
}

if (-not $Delete -and $moved -eq 0) {
    Remove-Item -LiteralPath $archiveDir -Force -Recurse -ErrorAction SilentlyContinue
}

Write-Host "[DONE] moved=$moved deleted=$deleted skipped=$skipped"
exit 0
