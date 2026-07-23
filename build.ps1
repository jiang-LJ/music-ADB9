# 一键构建 + 打包便携 ZIP
# 用法:  powershell -File build.ps1

$ErrorActionPreference = "Stop"

Write-Host "=== 清理旧构建 ===" -ForegroundColor Cyan
Remove-Item -Recurse -Force build -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force dist -ErrorAction SilentlyContinue

Write-Host "=== PyInstaller 打包 ===" -ForegroundColor Cyan
pyinstaller MusicScanner.spec --noconfirm

if ($LASTEXITCODE -ne 0) {
    Write-Host "打包失败！" -ForegroundColor Red
    exit 1
}

Write-Host "=== 压缩便携 ZIP ===" -ForegroundColor Cyan
$zipName = "ABD9-便携版.zip"
Remove-Item $zipName -ErrorAction SilentlyContinue
Compress-Archive -Path dist\ABD9\* -DestinationPath $zipName -Force

$zipSize = (Get-ChildItem $zipName).Length
Write-Host "✅ 完成！" -ForegroundColor Green
Write-Host "   ZIP: $zipName ($([math]::Round($zipSize/1MB, 1)) MB)"
Write-Host "   EXE: dist\ABD9\ABD9.exe"
