; Inno Setup script: Microscope Camera (app + USB driver) for 64-bit Windows 10/11.
; Built by build.ps1, which first produces build\dist\MicroscopeCamera, build\driver and build\thumbprint.txt.

#define AppName "Microscope Camera"
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#define FH FileOpen(AddBackslash(SourcePath) + "build\thumbprint.txt")
#define Thumb FileRead(FH)
#expr FileClose(FH)

[Setup]
AppId={{7C1E4B52-9A0D-4E7B-B7A4-3F2D8C6A1E90}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Microscope Camera (HDCE-10A)
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=output
OutputBaseFilename=MicroscopeCamera-Setup-{#AppVersion}
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\MicroscopeCamera.exe
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Tasks]
Name: desktopicon; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "build\dist\MicroscopeCamera\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "build\driver\*"; DestDir: "{app}\driver\package"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "build\microscope_camera.cer"; DestDir: "{app}\driver"; Flags: ignoreversion
Source: "install_driver.cmd"; DestDir: "{app}\driver"; Flags: ignoreversion
Source: "uninstall_driver.ps1"; DestDir: "{app}\driver"; Flags: ignoreversion
Source: "USER-GUIDE.txt"; DestDir: "{app}"; Flags: ignoreversion isreadme
Source: "THIRD-PARTY.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\MicroscopeCamera.exe"
Name: "{group}\Repair camera driver"; Filename: "{app}\driver\install_driver.cmd"; IconFilename: "{sys}\shell32.dll"; IconIndex: 21
Name: "{group}\User guide"; Filename: "{app}\USER-GUIDE.txt"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\MicroscopeCamera.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\MicroscopeCamera.exe"; Description: "Start {#AppName} now"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\driver\uninstall_driver.ps1"" -Thumbprint {#Thumb}"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveDriver"

[UninstallDelete]
Type: files; Name: "{app}\driver\driver_install.log"

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
var
  RC: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    WizardForm.StatusLabel.Caption := 'Installing the camera USB driver...';
    if not Exec(ExpandConstant('{cmd}'), '/c ""' + ExpandConstant('{app}\driver\install_driver.cmd') + '" /quiet"',
                ExpandConstant('{app}\driver'), SW_HIDE, ewWaitUntilTerminated, RC) or (RC <> 0) then
      MsgBox('The camera driver could not be installed (code ' + IntToStr(RC) + ').' + #13#10 +
             'The app is installed; try "Repair camera driver" from the Start menu.' + #13#10 +
             'Details: ' + ExpandConstant('{app}\driver\driver_install.log'), mbError, MB_OK);
  end;
end;
