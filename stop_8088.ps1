param(
    [int]$Port = 8088,
    [string]$RepoRoot = $PSScriptRoot
)

$ErrorActionPreference = "SilentlyContinue"
$resolvedRoot = (Resolve-Path $RepoRoot).Path.TrimEnd("\")
$startBat = Join-Path $resolvedRoot "start.bat"
$hotReloadBat = Join-Path $resolvedRoot "startServerHotReload.bat"
$runServerPy = Join-Path $resolvedRoot "run_server.py"
$reloadPy = Join-Path $resolvedRoot "run_server_hot_reload.py"

Write-Host "[INFO] MemoriaCore cleanup for port $Port"
Write-Host "[INFO] Repo root: $resolvedRoot"

$allProcesses = @(Get-CimInstance Win32_Process)
$targets = [ordered]@{}
$reasons = @{}

function Add-Target {
    param(
        [int]$ProcessId,
        [string]$Reason
    )
    if ($ProcessId -le 0) {
        return
    }
    if (-not $targets.Contains($ProcessId)) {
        $targets[$ProcessId] = $true
        $reasons[$ProcessId] = @()
    }
    $reasons[$ProcessId] += $Reason
}

function Has-Text {
    param(
        [string]$Value,
        [string]$Needle
    )
    if ([string]::IsNullOrWhiteSpace($Value) -or [string]::IsNullOrWhiteSpace($Needle)) {
        return $false
    }
    return $Value.IndexOf($Needle, [StringComparison]::OrdinalIgnoreCase) -ge 0
}

$listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
foreach ($listener in $listeners) {
    Add-Target -ProcessId ([int]$listener.OwningProcess) -Reason "listener $($listener.LocalAddress):$($listener.LocalPort)"
}

foreach ($process in $allProcesses) {
    $commandLine = [string]$process.CommandLine
    if (Has-Text $commandLine $startBat) {
        Add-Target -ProcessId ([int]$process.ProcessId) -Reason "wrapper start.bat"
    }
    if (Has-Text $commandLine $hotReloadBat) {
        Add-Target -ProcessId ([int]$process.ProcessId) -Reason "wrapper startServerHotReload.bat"
    }
    if (Has-Text $commandLine $runServerPy) {
        Add-Target -ProcessId ([int]$process.ProcessId) -Reason "server run_server.py"
    }
    if (Has-Text $commandLine $reloadPy) {
        Add-Target -ProcessId ([int]$process.ProcessId) -Reason "launcher run_server_hot_reload.py"
    }
}

foreach ($targetPid in @($targets.Keys)) {
    $process = $allProcesses | Where-Object { [int]$_.ProcessId -eq [int]$targetPid } | Select-Object -First 1
    if (-not $process) {
        Write-Host "[STALE] PID=$targetPid was reported by TCP state but is not in process list; descendants will still be checked."
        continue
    }
    $parent = $allProcesses | Where-Object { [int]$_.ProcessId -eq [int]$process.ParentProcessId } | Select-Object -First 1
    if ($parent -and (
        (Has-Text -Value ([string]$parent.CommandLine) -Needle $runServerPy) -or
        (Has-Text -Value ([string]$parent.CommandLine) -Needle $reloadPy) -or
        (Has-Text -Value ([string]$parent.CommandLine) -Needle $startBat) -or
        (Has-Text -Value ([string]$parent.CommandLine) -Needle $hotReloadBat)
    )) {
        Add-Target -ProcessId ([int]$parent.ProcessId) -Reason "server parent of PID $targetPid"
    }
}

do {
    $added = $false
    foreach ($process in $allProcesses) {
        if ($targets.Contains([int]$process.ParentProcessId) -and -not $targets.Contains([int]$process.ProcessId)) {
            Add-Target -ProcessId ([int]$process.ProcessId) -Reason "child of PID $($process.ParentProcessId)"
            $added = $true
        }
    }
} while ($added)

if ($targets.Count -eq 0) {
    Write-Host "[OK] No MemoriaCore process tree or port $Port listener found."
} else {
    foreach ($targetPid in @($targets.Keys | Sort-Object -Descending)) {
        $process = $allProcesses | Where-Object { [int]$_.ProcessId -eq [int]$targetPid } | Select-Object -First 1
        $reasonText = (($reasons[$targetPid] | Select-Object -Unique) -join "; ")
        if ($process) {
            Write-Host "[KILL] PID=$targetPid Name=$($process.Name) Reason=$reasonText"
            if ($process.CommandLine) {
                Write-Host "       $($process.CommandLine)"
            }
        } else {
            Write-Host "[KILL] PID=$targetPid Reason=$reasonText (process metadata unavailable)"
        }
        & taskkill.exe /PID $targetPid /T /F
    }
}

Start-Sleep -Milliseconds 800
$remaining = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
if ($remaining) {
    foreach ($listener in $remaining) {
        $remainingPid = [int]$listener.OwningProcess
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$remainingPid"
        Write-Host "[REMAINING] PID=$remainingPid $($listener.LocalAddress):$($listener.LocalPort) is still LISTENING"
        if ($process -and $process.CommandLine) {
            Write-Host "       $($process.CommandLine)"
        }
        Write-Host "[KILL] Forcing remaining listener PID=$remainingPid"
        & taskkill.exe /PID $remainingPid /T /F
    }
}

Start-Sleep -Milliseconds 800
$remaining = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
if ($remaining) {
    foreach ($listener in $remaining) {
        Write-Host "[ERROR] PID=$($listener.OwningProcess) $($listener.LocalAddress):$($listener.LocalPort) is still LISTENING after forced cleanup."
    }
    exit 1
}

Write-Host "[OK] No LISTENING socket remains on port $Port."
exit 0
