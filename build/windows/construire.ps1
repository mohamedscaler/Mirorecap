# Construit MiroRecap pour Windows : dist\windows\MiroRecap (Python autonome + code)
# puis l'installateur dist\windows\MiroRecap-Setup.exe (Inno Setup).
# Tourne sur un runner GitHub windows-latest (workflow windows.yml) ou un PC Windows 10/11 avec uv.
#
# Même principe que build/construire.sh (Mac) : un vrai Python (python-build-standalone, via uv)
# copié dans l'app, le code en fichiers .py à côté. Les mises à jour légères (code.zip)
# remplacent ce code sans toucher au moteur.
$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true
Set-Location (Join-Path $PSScriptRoot "..\..")

$VERSION = (Get-Content app\VERSION -Raw).Trim()
$DIST = "dist\windows"
$APP = "$DIST\MiroRecap"
Write-Host "▶ MiroRecap $VERSION pour Windows"

if (Test-Path $DIST) { Remove-Item -Recurse -Force $DIST }
New-Item -ItemType Directory -Force "$APP\app" | Out-Null

# 1. Python autonome (le même que celui géré par uv), copié tel quel dans l'app
$env:UV_PYTHON_PREFERENCE = "only-managed"
uv python install 3.12
$PY_SRC = Split-Path (uv python find 3.12)
Copy-Item -Recurse $PY_SRC "$APP\python"
$PY = Resolve-Path "$APP\python\python.exe"
Get-ChildItem "$APP\python" -Recurse -Filter EXTERNALLY-MANAGED | Remove-Item -Force   # autorise pip dans ce Python
& $PY -m ensurepip -q
& $PY -m pip install -q --no-cache-dir --upgrade pip
& $PY -m pip install -q --no-cache-dir -r app\requirements-windows.txt
& $PY -c "import sys, pystray, PIL, sounddevice, soundfile, anthropic, webview, faster_whisper, av, pyaudiowpatch, ctranslate2, clr; print('imports OK depuis', sys.prefix)"

# 2. Code de l'app
Copy-Item app\demarrage.py, app\MOTEUR, app\MOTEUR_WINDOWS, app\app.py, app\fenetre.py, app\mcp_serveur.py, app\VERSION, app\icone.png "$APP\app\"
Copy-Item -Recurse app\ui, app\plateforme "$APP\app\"

# 3. Icône (.ico) et MiroRecap.exe : copie de pythonw.exe (sans console) avec le nom et le logo
#    de l'app. C'est ce nom et ce logo qu'affichent la barre des tâches et le Gestionnaire des tâches.
& $PY -c "from PIL import Image; Image.open(r'app\icone.png').convert('RGBA').save(r'$DIST\MiroRecap.ico', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])"
Copy-Item "$APP\python\pythonw.exe" "$APP\python\MiroRecap.exe"
$RCEDIT = "$DIST\rcedit.exe"
Invoke-WebRequest "https://github.com/electron/rcedit/releases/download/v2.0.0/rcedit-x64.exe" -OutFile $RCEDIT
Write-Host "rcedit SHA256 : $((Get-FileHash $RCEDIT -Algorithm SHA256).Hash)"   # version figée, publiée par Electron
& $RCEDIT "$APP\python\MiroRecap.exe" --set-icon "$DIST\MiroRecap.ico" `
    --set-version-string FileDescription MiroRecap --set-version-string ProductName MiroRecap `
    --set-version-string OriginalFilename MiroRecap.exe --set-file-version "$VERSION.0.0" --set-product-version $VERSION
Remove-Item $RCEDIT

# 4. Nettoyage
Get-ChildItem $APP -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force

# 5. Installateur
$ISCC = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $ISCC)) {
    choco install innosetup -y --no-progress
}
& $ISCC /Q "/DVersion=$VERSION" build\windows\MiroRecap.iss
Get-Item "$DIST\MiroRecap-Setup.exe" | Format-Table Name, Length
