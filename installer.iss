; Inno Setup – نصب‌کننده AquaAudit (نصب برای کاربر جاری؛ بدون نیاز به دسترسی مدیر)
#define AppName "AquaAudit"
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{6F1B6C1E-4D55-4C0B-9B0E-0A9A0A0D1701}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=AquaAudit
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
OutputDir=installer_out
OutputBaseFilename=AquaAudit-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\AquaAudit.exe
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "ساخت میان‌بر روی دسکتاپ"; GroupDescription: "میان‌بر:"

[Files]
Source: "dist\AquaAudit\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\AquaAudit"; Filename: "{app}\AquaAudit.exe"
Name: "{autodesktop}\AquaAudit"; Filename: "{app}\AquaAudit.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\AquaAudit.exe"; Description: "اجرای AquaAudit"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; داده‌های کاربر در %LOCALAPPDATA%\AquaAudit می‌ماند و عمداً حذف نمی‌شود.
Type: filesandordirs; Name: "{app}"
