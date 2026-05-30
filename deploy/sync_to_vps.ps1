param(
  [string]$Server = "119.91.223.210",
  [string]$User = "ubuntu",
  [string]$KeyPath = "$env:USERPROFILE\.ssh\codex_vps",
  [string]$TargetRoot = "/home/ubuntu/video-shopping"
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path "$PSScriptRoot\..").Path

Write-Host "[1/4] upload core files"
& scp -i $KeyPath "$Root\requirements.txt" "$User@$Server`:$TargetRoot/requirements.txt"
if ($LASTEXITCODE -ne 0) { throw "scp requirements.txt failed" }
& scp -i $KeyPath "$Root\DEPLOY.md" "$User@$Server`:$TargetRoot/DEPLOY.md"
if ($LASTEXITCODE -ne 0) { throw "scp DEPLOY.md failed" }

Write-Host "[2/4] upload app code"
& scp -i $KeyPath "$Root\workflow_ui\app.py" "$User@$Server`:$TargetRoot/workflow_ui/app.py"
if ($LASTEXITCODE -ne 0) { throw "scp app.py failed" }
& scp -i $KeyPath "$Root\workflow_ui\static\index.html" "$User@$Server`:$TargetRoot/workflow_ui/static/index.html"
if ($LASTEXITCODE -ne 0) { throw "scp static/index.html failed" }
& scp -i $KeyPath "$Root\work\run_images_only.py" "$User@$Server`:$TargetRoot/work/run_images_only.py"
if ($LASTEXITCODE -ne 0) { throw "scp run_images_only.py failed" }
& scp -i $KeyPath "$Root\scripts\build_video.py" "$User@$Server`:$TargetRoot/scripts/build_video.py"
if ($LASTEXITCODE -ne 0) { throw "scp build_video.py failed" }
& scp -i $KeyPath "$Root\work\providers.json" "$User@$Server`:$TargetRoot/work/providers.json"
if ($LASTEXITCODE -ne 0) { throw "scp providers.json failed" }

Write-Host "[3/4] upload refs and knowledge"
& scp -i $KeyPath "$Root\work\refs\product_main_ref1.png" "$User@$Server`:$TargetRoot/work/refs/product_main_ref1.png"
if ($LASTEXITCODE -ne 0) { throw "scp product_main_ref1.png failed" }
& scp -i $KeyPath "$Root\work\refs\model_fixed_board1.png" "$User@$Server`:$TargetRoot/work/refs/model_fixed_board1.png"
if ($LASTEXITCODE -ne 0) { throw "scp model_fixed_board1.png failed" }
& scp -i $KeyPath "$Root\workflow_ui\xhs_knowledge\*.md" "$User@$Server`:$TargetRoot/workflow_ui/xhs_knowledge/"
if ($LASTEXITCODE -ne 0) { throw "scp xhs_knowledge failed" }

Write-Host "[4/4] upload deploy scripts and restart"
& scp -i $KeyPath "$Root\deploy\*" "$User@$Server`:$TargetRoot/deploy/"
if ($LASTEXITCODE -ne 0) { throw "scp deploy files failed" }
& ssh -i $KeyPath "$User@$Server" "cd $TargetRoot && chmod +x deploy/*.sh && ./deploy/restart_and_check.sh"
if ($LASTEXITCODE -ne 0) { throw "remote restart_and_check failed" }

Write-Host "done"
