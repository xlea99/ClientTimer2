; Inno Setup Script for Client Timer 2
; Requires Inno Setup 6.x — https://jrsoftware.org/isinfo.php
;
; Build: run `python release.py <version>` from the repo root. It bumps the
; version, builds, compiles this script with ISCC, and writes latest.json.
; Compiling this file by hand (GUI or ISCC) still works and uses whatever
; version.iss currently says.

#define MyAppName "Client Timer 2"
#define MyAppExeName "clienttimer2.exe"
#define MyAppPublisher "Alex Somheil"

; MyAppVersion lives in a generated file so the number is typed in exactly
; one place — ct/common/version.py. It used to be hardcoded here, which meant
; About and Add/Remove Programs could disagree, and the exe's filename (built
; from MyAppVersion below) could disagree with the URL in latest.json. That
; last one 404s the download for every user.
#include "version.iss"

; Paths relative to this .iss file
#define ProjectRoot ".."
#define DistDir ProjectRoot + "\dist\clienttimer2"
#define AssetsDir ProjectRoot + "\assets"

[Setup]
AppId={{B8F3A2D1-7E4C-4A9B-9D5F-2C1E8F6A3B7D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\ClientTimer2
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
LicenseFile=eula.txt
OutputDir={#ProjectRoot}\installer\output
OutputBaseFilename=ClientTimer2_Setup_{#MyAppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
SetupIconFile={#AssetsDir}\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}

; Don't let user change install dir — it must match PATHS.root
DisableDirPage=yes

; Minimum Windows 10
MinVersion=10.0

; NO AppMutex, deliberately. It was tried and removed: Inno checks the mutex
; during init, BEFORE Restart Manager runs, so a /SILENT install launched by
; the in-app updater hit a blocking "please close the app" dialog and hung
; behind a window the user may never see. Verified, not assumed.
;
; Restart Manager handles the same problem better. It closes the app itself
; instead of demanding the user do it, which is nicer interactively too, and
; it lets the updater launch Setup without a helper process, a temp batch
; file, or a guessed delay.
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"
Name: "startupicon"; Description: "Start with &Windows"; GroupDescription: "Additional options:"

[Files]
; Main exe
Source: "{#DistDir}\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

; PyInstaller internals
Source: "{#DistDir}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs

; Assets (installed next to exe, not inside _internal)
Source: "{#AssetsDir}\*"; DestDir: "{app}\assets"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Start Menu
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\assets\icon.ico"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"

; Desktop (optional)
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\assets\icon.ico"; Tasks: desktopicon

; Startup (optional)
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: startupicon

[Run]
; Interactive install: the usual "Launch Client Timer 2" tickbox.
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

; Silent install — how the in-app updater invokes Setup. `postinstall` entries
; NEVER fire under /SILENT, so without this line an auto-update would replace
; the app and then simply never reopen it.
Filename: "{app}\{#MyAppExeName}"; Flags: nowait; Check: WizardSilent

[InstallDelete]
; Clean slate on reinstall/upgrade — remove old _internal to avoid stale DLLs
Type: filesandordirs; Name: "{app}\_internal"
