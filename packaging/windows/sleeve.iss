; Inno Setup script for Sleeve. Build the app first (packaging\windows\build.ps1).
#define AppName "Sleeve"
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{6B0F4E2C-58A4-4F0B-9C55-2B1D7A3E9F41}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Jason Park
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=..\..\dist
OutputBaseFilename=Sleeve-{#AppVersion}-setup
SetupIconFile=..\icons\sleeve.ico
UninstallDisplayIcon={app}\Sleeve.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequiredOverridesAllowed=dialog
LicenseFile=..\..\LICENSE

[Files]
Source: "..\..\dist\Sleeve\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\Sleeve.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\Sleeve.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Run]
Filename: "{app}\Sleeve.exe"; Description: "Open {#AppName}"; Flags: nowait postinstall skipifsilent
