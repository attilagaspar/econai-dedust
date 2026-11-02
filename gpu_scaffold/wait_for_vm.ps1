# Keep trying to start an Azure VM until Azure has capacity, then notify.
#
#   powershell -ExecutionPolicy Bypass -File gpu_scaffold\wait_for_vm.ps1
#
# Retries only capacity errors. Anything else (expired login, missing VM,
# quota) stops the loop and notifies, because retrying cannot fix it.
# Once the VM starts it bills until stopped; this script never stops it.
param(
  [string]$ResourceGroup = 'DEDUST-RG',
  [string]$VmName = 'dedust-gpu',
  [double]$IntervalMinutes = 5,
  [double]$MaxHours = 12,
  [string]$NtfyTopic = ''      # optional: also push to https://ntfy.sh/<topic>
)

$capacityErrors = 'AllocationFailed|ZonalAllocationFailed|OverconstrainedAllocationRequest|OverconstrainedZonalAllocationRequest|SkuNotAvailable|AllocationTimedOut'

function Log($msg) { Write-Host "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $msg" }

function Notify($title, $msg) {
  Log "$title - $msg"
  if ($NtfyTopic) {
    try { Invoke-RestMethod -Method Post -Uri "https://ntfy.sh/$NtfyTopic" -Headers @{Title = $title} -Body $msg | Out-Null }
    catch { Log "ntfy failed: $_" }
  }
  try {
    Add-Type -AssemblyName System.Windows.Forms, System.Drawing
    $icon = New-Object System.Windows.Forms.NotifyIcon
    $icon.Icon = [System.Drawing.SystemIcons]::Information
    $icon.Visible = $true
    $icon.ShowBalloonTip(30000, $title, $msg, 'Info')
    1..3 | ForEach-Object { [console]::Beep(880, 300); Start-Sleep -Milliseconds 200 }
    Start-Sleep -Seconds 10
    $icon.Dispose()
  } catch { Log "desktop notification failed: $_" }
}

function PowerState {
  $s = & az vm get-instance-view -g $ResourceGroup -n $VmName `
    --query "instanceView.statuses[?starts_with(code,'PowerState/')].code | [0]" -o tsv 2>$null
  if ($s) { "$s".Trim() } else { '' }
}

$deadline = (Get-Date).AddHours($MaxHours)
$attempt = 0
while ($true) {
  if ((PowerState) -eq 'PowerState/running') {
    Notify "$VmName is running" "Azure VM $VmName is up (it bills until you stop it)."
    exit 0
  }
  $attempt++
  Log "attempt $attempt`: starting $VmName ..."
  $out = (& az vm start -g $ResourceGroup -n $VmName 2>&1 | Out-String)
  $code = $LASTEXITCODE
  if ($code -eq 0 -and (PowerState) -eq 'PowerState/running') {
    Notify "$VmName is running" "Started on attempt $attempt. It bills until you stop it."
    exit 0
  }
  if ($code -ne 0 -and $out -notmatch $capacityErrors) {
    Log $out
    Notify "$VmName start failed" "Not a capacity error, so retrying stopped. See the script's window."
    exit 1
  }
  if ((Get-Date).AddMinutes($IntervalMinutes) -gt $deadline) {
    Notify "$VmName gave up" "No capacity after $attempt attempts over $MaxHours hours."
    exit 2
  }
  Log "no capacity; next try in $IntervalMinutes min"
  Start-Sleep -Seconds ([int]($IntervalMinutes * 60))
}
