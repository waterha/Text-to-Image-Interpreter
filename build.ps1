$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
python -m pip install -r requirements.txt 'pyinstaller>=6,<7'
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
python -c "from comtypes.client import GetModule; GetModule('UIAutomationCore.dll')"
if ($LASTEXITCODE -ne 0) { throw 'UI Automation initialization failed.' }
python -m PyInstaller --noconfirm --clean --windowed --onefile --name TextLens --exclude-module numpy --hidden-import comtypes.gen.UIAutomationClient --hidden-import win32timezone --add-data "textlens/assets;textlens/assets" main.pyw
if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
Write-Output "Ready: $PSScriptRoot\dist\TextLens.exe"
