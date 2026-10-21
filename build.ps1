$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
python -m pip install -r requirements.txt 'pyinstaller>=6,<7'
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
python -c "from comtypes.client import GetModule; GetModule('UIAutomationCore.dll')"
if ($LASTEXITCODE -ne 0) { throw 'UI Automation initialization failed.' }
python -m PyInstaller --noconfirm --clean --windowed --onefile --name TIL --exclude-module numpy --hidden-import comtypes.gen.UIAutomationClient --hidden-import win32timezone --hidden-import winrt.windows.media.ocr --hidden-import winrt.windows.graphics.imaging --hidden-import winrt.windows.storage.streams --hidden-import winrt.windows.foundation.collections --hidden-import winrt.windows.system --hidden-import winrt.windows.graphics.directx --hidden-import winrt.windows.applicationmodel --hidden-import winrt.windows.globalization main.pyw
if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
Write-Output "Ready: $PSScriptRoot\dist\TIL.exe"
