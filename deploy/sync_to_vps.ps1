param(
  [string]$Host = "119.91.223.210",
  [string]$User = "ubuntu",
  [string]$KeyPath = "$env:USERPROFILE\.ssh\codex_vps",
  [string]$TargetRoot = "/home/ubuntu/video-shopping"
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path "$PSScriptRoot\..").Path

Write-Host "[1/4] upload core files"
scp -i $KeyPath "$Root\requirements.txt" "$User@$Host`:$TargetRoot/requirements.txt"
scp -i $KeyPath "$Root\DEPLOY.md" "$User@$Host`:$TargetRoot/DEPLOY.md"

Write-Host "[2/4] upload app code"
scp -i $KeyPath "$Root\workflow_ui\app.py" "$User@$Host`:$TargetRoot/workflow_ui/app.py"
scp -i $KeyPath "$Root\workflow_ui\static\index.html" "$User@$Host`:$TargetRoot/workflow_ui/static/index.html"
scp -i $KeyPath "$Root\work\run_images_only.py" "$User@$Host`:$TargetRoot/work/run_images_only.py"
scp -i $KeyPath "$Root\scripts\build_video.py" "$User@$Host`:$TargetRoot/scripts/build_video.py"
scp -i $KeyPath "$Root\work\providers.json" "$User@$Host`:$TargetRoot/work/providers.json"

Write-Host "[3/4] upload refs and knowledge"
scp -i $KeyPath "$Root\work\refs\product_main_ref1.png" "$User@$Host`:$TargetRoot/work/refs/product_main_ref1.png"
scp -i $KeyPath "$Root\work\refs\model_fixed_board1.png" "$User@$Host`:$TargetRoot/work/refs/model_fixed_board1.png"
scp -i $KeyPath "$Root\workflow_ui\xhs_knowledge\*.md" "$User@$Host`:$TargetRoot/workflow_ui/xhs_knowledge/"

Write-Host "[4/4] upload deploy scripts and restart"
scp -i $KeyPath "$Root\deploy\*" "$User@$Host`:$TargetRoot/deploy/"
ssh -i $KeyPath "$User@$Host" "cd $TargetRoot && chmod +x deploy/*.sh && ./deploy/restart_and_check.sh"

Write-Host "done"

