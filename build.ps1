# Build BatchCrop.exe from source
# Requirements: Python 3.10+, pip
#
# Usage (PowerShell):
#   .\build.ps1

$ErrorActionPreference = 'Stop'

Write-Host "Creating venv..." -ForegroundColor Cyan
python -m venv .build-venv
.\.build-venv\Scripts\Activate.ps1

Write-Host "Installing dependencies..." -ForegroundColor Cyan
python -m pip install --upgrade pip
python -m pip install pillow pyinstaller

Write-Host "Building BatchCrop.exe..." -ForegroundColor Cyan
python -m PyInstaller `
  --onefile `
  --windowed `
  --name BatchCrop `
  --hidden-import=PIL `
  --hidden-import=PIL._tkinter_finder `
  --collect-all=PIL `
  batchcrop.py

if (Test-Path ".\dist\BatchCrop.exe") {
    Write-Host "Build complete: .\dist\BatchCrop.exe" -ForegroundColor Green
} else {
    Write-Host "Build failed." -ForegroundColor Red
    exit 1
}
