; Inno Setup Script for Client Timer 2
; Requires Inno Setup 6.x — https://jrsoftware.org/isinfo.php
;
; Build steps:
;   1. Run: pyinstaller clienttimer2.spec --noconfirm
;   2. Open this file in Inno Setup Compiler and click Build

#define MyAppName "Client Timer 2"
#define MyAppExeName "clienttimer2.exe"
#define MyAppVersion "2.3.0"
#define MyAppPublisher "Alex Somheil"

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
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[InstallDelete]
; Clean slate on reinstall/upgrade — remove old _internal to avoid stale DLLs
Type: filesandordirs; Name: "{app}\_internal"
