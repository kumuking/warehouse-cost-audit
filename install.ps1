# 安装 warehouse-cost-audit 到 WorkBuddy / CodeBuddy skills 目录
# 用法：在解压或 clone 出来的目录里执行  .\install.ps1
$ErrorActionPreference = "Stop"

$src = $PSScriptRoot
$dst = Join-Path $env:USERPROFILE ".workbuddy\skills\warehouse-cost-audit"

if ($src.TrimEnd('\') -ieq $dst.TrimEnd('\')) {
    Write-Host "当前目录就是安装目标，无需复制。" -ForegroundColor Yellow
    exit 0
}

Write-Host "源目录: $src"
Write-Host "目标:   $dst"
New-Item -ItemType Directory -Force -Path $dst | Out-Null

robocopy $src $dst /E /XD .git __pycache__ .venv output .idea .vscode `
    /XF *.pyc /NFL /NDL /NJH /NJS /NP | Out-Null

if ($LASTEXITCODE -ge 8) {
    Write-Host "复制失败（robocopy 退出码 $LASTEXITCODE）" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "已安装到 $dst" -ForegroundColor Green
Write-Host "已有的 assets\rates\*.json 费率卡未被覆盖。"
Write-Host ""
Write-Host "下一步:"
Write-Host "  1) 填费率卡: $dst\assets\rates\（参考 _template_*.json）"
Write-Host "  2) 跑演示:   cd `"$dst`"; python examples/run_demo.py"
Write-Host "  3) 配凭证:   见 README 的「首次配置」"
