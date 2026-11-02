; Windows installer for Auto Stock Trader (built by .github/workflows/build-apps.yml).
; The AppId never changes, so each new installer updates the existing install
; in place (same folder, same shortcuts) instead of adding a second copy.

#define AppVersion GetEnv("APP_VERSION")

[Setup]
AppId={{8B5D3F2A-6C1E-4F7B-9A2D-3E4C5B6A7D8E}
AppName=Auto Stock Trader
AppVersion={#AppVersion}
AppPublisher=Auto Stock Trader
DefaultDirName={autopf}\Auto Stock Trader
DisableDirPage=auto
DisableProgramGroupPage=yes
OutputDir=..\build\installer
OutputBaseFilename=AutoStockTrader-windows-setup
SetupIconFile=runner\resources\app_icon.ico
UninstallDisplayIcon={app}\auto_stock_trader.exe
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Closes the app if it's open, so the update can replace its files.
CloseApplications=force
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[InstallDelete]
; Clear the previous version's bundled data so no old files are left behind.
Type: filesandordirs; Name: "{app}\data"

[Files]
Source: "..\build\windows\x64\runner\Release\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Auto Stock Trader"; Filename: "{app}\auto_stock_trader.exe"
Name: "{autodesktop}\Auto Stock Trader"; Filename: "{app}\auto_stock_trader.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\auto_stock_trader.exe"; Description: "Open Auto Stock Trader"; Flags: nowait postinstall skipifsilent
