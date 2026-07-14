#define MyAppName "CMIP Climate Explorer"
#define MyAppVersion "0.5.7"
#define MyAppPublisher "CMIP Climate Explorer contributors"
#define MyAppExeName "CMIPClimateExplorer.exe"

[Setup]
AppId={{70B59C21-622F-4E5C-9E83-A383C89D7951}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={code:GetDefaultInstallDir}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
DisableDirPage=no
UsePreviousAppDir=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\build\installer
OutputBaseFilename=CMIP-Climate-Explorer-{#MyAppVersion}-x64-Setup
SetupIconFile=..\resources\windows\app-icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
LicenseFile=..\LICENSE

[Languages]
Name: "chinesesimp"; MessagesFile: "{#SourcePath}\languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "其他任务:"; Flags: unchecked

[Files]
Source: "..\dist\CMIP Climate Explorer\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\src\cmip_explorer\resources\fonts\OFL-1.1.txt"; DestDir: "{app}\licenses"; DestName: "Noto-Sans-CJK-OFL-1.1.txt"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent; Check: IsNotUpdateMode
Filename: "{app}\{#MyAppExeName}"; Flags: nowait; Check: IsUpdateMode

[Code]
function IsUpdateMode(): Boolean;
begin
  Result := CompareText(ExpandConstant('{param:UPDATE|0}'), '1') = 0;
end;

function IsNotUpdateMode(): Boolean;
begin
  Result := not IsUpdateMode();
end;

function IsStagedUpdate(): Boolean;
begin
  Result := CompareText(ExpandConstant('{param:STAGEDUPDATE|0}'), '1') = 0;
end;

function HasCommandLineSwitch(const SwitchName: String): Boolean;
var
  Index: Integer;
begin
  Result := False;
  for Index := 1 to ParamCount do
  begin
    if CompareText(ParamStr(Index), SwitchName) = 0 then
    begin
      Result := True;
      Exit;
    end;
  end;
end;

function InitializeSetup(): Boolean;
var
  BridgePath: String;
  BridgeScript: String;
  ErrorCode: Integer;
begin
  Result := True;

  { Stage updates until the desktop process has exited. This also upgrades
    versions that launched Setup immediately while their files were still open. }
  if (IsUpdateMode() and (not IsStagedUpdate())) or
     (HasCommandLineSwitch('/CLOSEAPPLICATIONS') and
      (not WizardSilent()) and
      (not IsUpdateMode())) then
  begin
    BridgePath := AddBackslash(GetTempDir()) + 'CMIPClimateExplorerUpdate.cmd';
    BridgeScript :=
      '@echo off' + #13#10 +
      'ping 127.0.0.1 -n 4 >nul' + #13#10 +
      '"' + ExpandConstant('{srcexe}') +
      '" /SP- /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS ' +
      '/FORCECLOSEAPPLICATIONS /UPDATE=1 /STAGEDUPDATE=1' + #13#10 +
      'del "%~f0"' + #13#10;
    if (not SaveStringToFile(BridgePath, BridgeScript, False)) or
       (not ShellExec(
         '',
         ExpandConstant('{cmd}'),
         '/D /C ""' + BridgePath + '""',
         '',
         SW_HIDE,
         ewNoWait,
         ErrorCode
       )) then
      MsgBox('无法启动后台更新程序。', mbError, MB_OK);
    Result := False;
    Exit;
  end;
end;

function GetDefaultInstallDir(Param: String): String;
begin
  if DirExists('D:\') then
    Result := 'D:\Programs\CMIP Climate Explorer'
  else
    Result := ExpandConstant('{userdocs}\CMIP Climate Explorer');
end;
