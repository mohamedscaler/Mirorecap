; Installateur Windows de MiroRecap (Inno Setup 6). Construit par build\windows\construire.ps1 :
;   ISCC /DVersion=6.14 build\windows\MiroRecap.iss
; Installation pour l'utilisateur seul, sans droits administrateur, dans
; %LOCALAPPDATA%\Programs\MiroRecap. Réglages, projets et comptes rendus ne sont jamais touchés.
; Mise à jour complète depuis l'app : MiroRecap-Setup.exe /VERYSILENT [/DISCRET=1]

#ifndef Version
  #define Version "0"
#endif

[Setup]
AppId={{8F3C2A51-6D2B-4E7A-9C1F-4B8E2D7A6C10}
AppName=MiroRecap
AppVersion={#Version}
AppVerName=MiroRecap {#Version}
AppPublisher=MiroRecap
AppPublisherURL=https://github.com/mohamedscaler/Mirorecap
DefaultDirName={localappdata}\Programs\MiroRecap
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir=..\..\dist\windows
OutputBaseFilename=MiroRecap-Setup
SetupIconFile=..\..\dist\windows\MiroRecap.ico
UninstallDisplayIcon={app}\python\MiroRecap.exe
UninstallDisplayName=MiroRecap
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=force
RestartApplications=no

[Languages]
Name: "fr"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "bureau"; Description: "Créer une icône sur le Bureau"; Flags: unchecked

[InstallDelete]
; Moteur et code remplacés en entier : pas de module périmé d'une ancienne version
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\app"

[Files]
Source: "..\..\dist\windows\MiroRecap\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\MiroRecap"; Filename: "{app}\python\MiroRecap.exe"; Parameters: """{app}\app\demarrage.py"""; WorkingDir: "{app}\app"; IconFilename: "{app}\python\MiroRecap.exe"; AppUserModelID: "fr.mirorecap.app"
Name: "{autodesktop}\MiroRecap"; Filename: "{app}\python\MiroRecap.exe"; Parameters: """{app}\app\demarrage.py"""; WorkingDir: "{app}\app"; IconFilename: "{app}\python\MiroRecap.exe"; AppUserModelID: "fr.mirorecap.app"; Tasks: bureau

[Registry]
; Lancement à l'ouverture de session : écrit par l'app elle-même, retiré à la désinstallation
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "MiroRecap"; Flags: uninsdeletevalue dontcreatekey

[Run]
Filename: "{app}\python\MiroRecap.exe"; Parameters: """{app}\app\demarrage.py"""; WorkingDir: "{app}\app"; Description: "Lancer MiroRecap"; Flags: nowait postinstall skipifsilent
; Mise à jour silencieuse lancée par l'app : on la relance (sans fenêtre si elle était automatique)
Filename: "{app}\python\MiroRecap.exe"; Parameters: """{app}\app\demarrage.py"" {code:ArgumentDiscret}"; WorkingDir: "{app}\app"; Flags: nowait; Check: WizardSilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM MiroRecap.exe"; Flags: runhidden; RunOnceId: "ArretMiroRecap"

[Code]
function ArgumentDiscret(Param: String): String;
begin
  if ExpandConstant('{param:DISCRET|0}') = '1' then
    Result := '--demarrage'
  else
    Result := '';
end;

{ L'app tourne peut-être (zone de notification) : on l'arrête avant de remplacer ses fichiers. }
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM MiroRecap.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Sleep(800);
  Result := '';
end;
