# Teste MiroRecap installé par MiroRecap-Setup.exe (runner GitHub windows-latest).
# 1. L'app lancée par l'installateur tourne : processus, API locale, fenêtre, journal sans erreur.
# 2. Tests pytest (tests\windows) avec le Python de l'app installée.
$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true
Set-Location (Join-Path $PSScriptRoot "..\..")

$INSTALLE = "$env:LOCALAPPDATA\Programs\MiroRecap"
$JOURNAL = "$env:LOCALAPPDATA\MiroRecap\app.log"
foreach ($f in "python\MiroRecap.exe", "python\python.exe", "app\demarrage.py", "app\plateforme\audio_windows.py") {
    if (-not (Test-Path "$INSTALLE\$f")) { throw "Fichier manquant après installation : $f" }
}
Write-Host "✓ Fichiers installés"
$raccourci = Get-ChildItem "$env:APPDATA\Microsoft\Windows\Start Menu\Programs" -Filter "MiroRecap.lnk" -Recurse
if (-not $raccourci) { throw "Raccourci du menu Démarrer absent" }
Write-Host "✓ Raccourci du menu Démarrer"

# --- 1. L'app démarre (lancée par l'installateur silencieux, comme après une mise à jour) ---
$port = $null
for ($i = 0; $i -lt 90 -and -not $port; $i++) {
    Start-Sleep 1
    $ids = (Get-Process MiroRecap -ErrorAction SilentlyContinue).Id
    if ($ids) {
        $port = (Get-NetTCPConnection -State Listen -LocalAddress 127.0.0.1 -ErrorAction SilentlyContinue |
                 Where-Object { $ids -contains $_.OwningProcess } | Select-Object -First 1).LocalPort
    }
}
if (Test-Path $JOURNAL) { Write-Host "----- app.log -----"; Get-Content $JOURNAL; Write-Host "-------------------" }
if (-not $port) { throw "MiroRecap ne s'est pas lancé (aucune API locale au bout de 90 s)" }
$page = Invoke-WebRequest "http://127.0.0.1:$port/" -UseBasicParsing
if ($page.StatusCode -ne 200 -or $page.Content -notmatch "MiroRecap") { throw "Interface non servie" }
Write-Host "✓ App lancée, interface servie sur le port $port"

# Premier lancement (rien de configuré) : la fenêtre des réglages s'ouvre (2e processus MiroRecap.exe)
for ($i = 0; $i -lt 30 -and @(Get-Process MiroRecap -ErrorAction SilentlyContinue).Count -lt 2; $i++) { Start-Sleep 1 }
Start-Sleep 8   # laisse WebView2 démarrer (une erreur ferait quitter la fenêtre)
$processus = @(Get-Process MiroRecap -ErrorAction SilentlyContinue)
$processus | Format-Table Id, ProcessName, MainWindowTitle, StartTime
if ($processus.Count -lt 2) { throw "La fenêtre de MiroRecap ne s'est pas ouverte" }
if (-not ($processus | Where-Object { $_.MainWindowTitle -eq "MiroRecap" })) { Write-Warning "Fenêtre sans titre visible (session sans bureau ?)" }
Write-Host "✓ Fenêtre ouverte"

# Deuxième lancement : pas de deuxième app, elle redemande la fenêtre à la première
$avant = @(Get-Process MiroRecap).Count
& "$INSTALLE\python\MiroRecap.exe" "$INSTALLE\app\demarrage.py"
Start-Sleep 10
$apiApres = (Get-NetTCPConnection -State Listen -LocalAddress 127.0.0.1 -ErrorAction SilentlyContinue |
             Where-Object { (Get-Process MiroRecap).Id -contains $_.OwningProcess }).Count
if ($apiApres -ne 1) { throw "Instance unique : $apiApres API locales au lieu d'une" }
Write-Host "✓ Instance unique ($avant processus avant, $(@(Get-Process MiroRecap).Count) après)"

if ((Get-Content $JOURNAL -Raw) -match "Traceback") { Get-Content $JOURNAL; throw "Erreur Python dans app.log" }
Write-Host "✓ Journal sans erreur"
Stop-Process -Name MiroRecap -Force
Start-Sleep 2

# --- 2. Tests pytest ---
$PY = "$INSTALLE\python\python.exe"
& $PY -m pip install -q pytest
$env:MIRORECAP_APP = "$INSTALLE\app"
& $PY -X utf8 -m pytest tests\windows -v -s --durations=5
