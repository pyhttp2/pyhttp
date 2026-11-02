; Inno Setup script for the pyhttp Windows installer.  Built by packaging/build.py:
;   ISCC.exe /DVERSION=0.1.7 /DSRC=..\dist\pyhttp-win /DOUT=..\dist packaging\pyhttp.iss
;   -> dist/pyhttp-<version>-windows-x64-setup.exe
;
; Installs per user (no administrator rights) into %LOCALAPPDATA%\Programs\pyhttp, adds a
; Start menu entry, an optional desktop icon and an uninstaller. The in-app updater runs
; this installer with /VERYSILENT; the [Run] entry below then starts pyhttp again.
; User data stays in %APPDATA%\pyhttp and is never touched by install or uninstall.

#ifndef VERSION
  #define VERSION "0.0.0"
#endif
#ifndef NUMVERSION
  #define NUMVERSION "0.0.0.0"
#endif
#ifndef SRC
  #define SRC "..\dist\pyhttp-win"
#endif
#ifndef OUT
  #define OUT "..\dist"
#endif

[Setup]
AppId={{8D4B1C0E-6E0B-4A1D-9D23-5C1F3B7A2E61}
AppName=pyhttp
AppVersion={#VERSION}
AppVerName=pyhttp {#VERSION}
AppPublisher=pyhttp Dev Team
AppPublisherURL=https://pyhttp.com
AppSupportURL=https://github.com/pyhttp2/pyhttp-releases/issues
AppUpdatesURL=https://github.com/pyhttp2/pyhttp-releases/releases
VersionInfoVersion={#NUMVERSION}
VersionInfoDescription=pyhttp installer
VersionInfoProductName=pyhttp
DefaultDirName={autopf}\pyhttp
DefaultGroupName=pyhttp
DisableProgramGroupPage=yes
DisableWelcomePage=no
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OUT}
OutputBaseFilename=pyhttp-{#VERSION}-windows-x64-setup
SetupIconFile=build\icon.ico
UninstallDisplayIcon={app}\pyhttp.exe
UninstallDisplayName=pyhttp
LicenseFile=..\LICENSE.md
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
ShowLanguageDialog=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; a clean program folder on every install, so files of an older version never linger
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#SRC}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\pyhttp"; Filename: "{app}\pyhttp.exe"; Comment: "Local HTTP client with a collection runner"
Name: "{group}\Uninstall pyhttp"; Filename: "{uninstallexe}"
Name: "{autodesktop}\pyhttp"; Filename: "{app}\pyhttp.exe"; Tasks: desktopicon

[Run]
; also runs after the quiet update (/VERYSILENT), which is how pyhttp comes back after updating itself
Filename: "{app}\pyhttp.exe"; Description: "{cm:LaunchProgram,pyhttp}"; Flags: nowait postinstall
