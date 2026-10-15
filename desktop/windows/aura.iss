; Windows installer for the AURA AI desktop app (Inno Setup 6).
;
; Built by the release workflow from the PyInstaller folder:
;   iscc /DAppVersion=1.0.0 /DSourceDir=<...\build\desktop\AURA AI> /DOutputDir=<...> aura.iss
;
; Installs for the current user only (no administrator password), into
; %LOCALAPPDATA%\Programs\AURA AI, with a Start menu entry and an optional
; desktop icon. Uninstalling removes the app but keeps your data in
; %USERPROFILE%\.aura — delete that folder yourself to remove everything.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\build\desktop\AURA AI"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\build\desktop"
#endif

#define AppName "AURA AI"
#define AppExe "AURA AI.exe"

[Setup]
AppId={{90B5F26E-A0EE-45BC-AE3A-9C138183EA37}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=AURA AI
AppPublisherURL=https://aura-ai-psi-self.vercel.app
AppSupportURL=https://github.com/udaysk-lab/AURA-AI-/issues
AppUpdatesURL=https://github.com/udaysk-lab/AURA-AI-/releases/latest
DefaultDirName={localappdata}\Programs\AURA AI
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
OutputDir={#OutputDir}
OutputBaseFilename=AURA-AI-Setup-Windows-x64
SetupIconFile=..\assets\aura.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
; An update closes a running AURA rather than failing to replace its files.
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; Files from the previous version that this one no longer ships.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\_internal"
