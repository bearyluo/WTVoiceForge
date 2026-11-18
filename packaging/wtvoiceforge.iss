; WT Voice Forge 安装包
; 使用 Inno Setup 7 编译；卸载默认保留 data 文件夹。

#ifndef AppVersion
  #define AppVersion "0.9.21"
#endif
#ifndef OutputDir
  #define OutputDir "..\installer-output"
#endif
#ifndef ExeDir
  #define ExeDir ".."
#endif
#ifndef AppMode
  #define AppMode "onedir"
#endif

#define AppName "WT Voice Forge"
#define AppNameEn "WT Voice Forge"
#define AppExe "WTVoiceForge.exe"

[Setup]
AppId={{8D1B7B62-2A4B-4E0C-9D6E-3A1F6C7E9B21}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=WT Voice Forge
AppComments=本地战雷语音包制作器
DefaultDirName=D:\WTVoiceForge
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; 数据目录固定跟随程序目录，因此只支持当前用户安装，避免写入 Program Files 失败。
PrivilegesRequired=lowest
AllowNoIcons=yes
OutputDir={#OutputDir}
OutputBaseFilename=WTVoiceForge-Setup-{#AppVersion}
SetupIconFile=app.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
WizardStyle=modern
WizardImageFile=installer-banner.bmp
WizardSmallImageFile=installer-small.bmp
WizardImageStretch=no
WizardSizePercent=110
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
CloseApplications=yes
RestartApplications=no
AppMutex=Local\WTVoiceForge.Launcher
VersionInfoVersion={#AppVersion}.0
VersionInfoCompany=WT Voice Forge
VersionInfoDescription=WT Voice Forge 安装包
VersionInfoProductName=WT Voice Forge
VersionInfoProductVersion={#AppVersion}
LicenseFile={#ExeDir}\LICENSE
SetupLogging=yes

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："

[Files]
Source: "{#ExeDir}\WTVoiceForge.exe"; DestDir: "{app}"; Flags: ignoreversion
#if AppMode == "onedir"
Source: "{#ExeDir}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
#endif
Source: "{#ExeDir}\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ExeDir}\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ExeDir}\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ExeDir}\VGMSTREAM_COPYING"; DestDir: "{app}"; Flags: ignoreversion
Source: "app.ico"; DestDir: "{app}"; DestName: "app-{#AppVersion}.ico"; Flags: ignoreversion
Source: "app.ico"; DestDir: "{app}"; Flags: ignoreversion

[InstallDelete]
; 升级时清掉旧版按版本号命名的图标，避免 app-0.9.x.ico 越积越多。
Type: files; Name: "{app}\app-*.ico"
#if AppMode == "onedir"
; onedir 的运行时由新版整体替换，旧模块不保留。
Type: filesandordirs; Name: "{app}\_internal"
#endif

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; IconFilename: "{app}\app-{#AppVersion}.ico"
Name: "{group}\卸载{#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; IconFilename: "{app}\app-{#AppVersion}.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "启动 WT Voice Forge"; Flags: nowait postinstall skipifsilent

[Code]
var
  DeleteDataOnUninstall: Boolean;

procedure SaveDesktopShortcut(const LinkPath: String; Shell: Variant);
var
  Shortcut: Variant;
begin
  Shortcut := Shell.CreateShortcut(LinkPath);
  Shortcut.TargetPath := ExpandConstant('{app}\{#AppExe}');
  Shortcut.WorkingDirectory := ExpandConstant('{app}');
  Shortcut.IconLocation := ExpandConstant('{app}\app-{#AppVersion}.ico') + ',0';
  Shortcut.Description := '制作和管理战雷语音包';
  Shortcut.Save;
end;

function ShortcutIsCurrent(const LinkPath: String; Shell: Variant): Boolean;
var
  Shortcut: Variant;
begin
  Result := False;
  if not FileExists(LinkPath) then
    Exit;
  try
    Shortcut := Shell.CreateShortcut(LinkPath);
    Result :=
      (Uppercase(Shortcut.TargetPath) = Uppercase(ExpandConstant('{app}\{#AppExe}'))) and
      (Pos(
        Uppercase(ExpandConstant('{app}\app-{#AppVersion}.ico')),
        Uppercase(Shortcut.IconLocation)
      ) = 1);
  except
    Result := False;
  end;
end;

function RealDesktopPath(): String;
var
  Value: String;
begin
  Result := '';
  if not RegQueryStringValue(
    HKCU,
    'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders',
    'Desktop',
    Value
  ) then
    Exit;
  StringChangeEx(Value, '%USERPROFILE%', GetEnv('USERPROFILE'), True);
  StringChangeEx(Value, '%OneDrive%', GetEnv('OneDrive'), True);
  if Value <> '' then
    Result := Value;
end;

function PsQuote(const Value: String): String;
var
  Escaped: String;
begin
  Escaped := Value;
  StringChangeEx(Escaped, '''', '''''', True);
  Result := '''' + Escaped + '''';
end;

procedure EnsureDesktopShortcutWithPowerShell();
var
  ScriptPath: String;
  Script: String;
  PowerShell: String;
  ResultCode: Integer;
begin
  PowerShell := ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe');
  if not FileExists(PowerShell) then
    Exit;
  ScriptPath := ExpandConstant('{tmp}\wtvf-shortcut.ps1');
  { 桌面本身是 junction 时 WScript 无法直接保存；用 .NET 解析真实桌面目录。 }
  Script :=
    '$ErrorActionPreference = "Stop";' + #13#10 +
    '$desktop = [Environment]::GetFolderPath("Desktop");' + #13#10 +
    'try { $item = Get-Item -LiteralPath $desktop -Force; ' +
    'if ($item.Target) { $desktop = @($item.Target)[0] } } catch {}' + #13#10 +
    '$lnk = Join-Path $desktop "WT Voice Forge.lnk";' + #13#10 +
    '$shell = New-Object -ComObject WScript.Shell;' + #13#10 +
    '$s = $shell.CreateShortcut($lnk);' + #13#10 +
    '$s.TargetPath = ' + PsQuote(ExpandConstant('{app}\{#AppExe}')) + ';' + #13#10 +
    '$s.WorkingDirectory = ' + PsQuote(ExpandConstant('{app}')) + ';' + #13#10 +
    '$s.IconLocation = ' + PsQuote(ExpandConstant('{app}\app-{#AppVersion}.ico')) + ' + ",0";' + #13#10 +
    '$s.Description = "WT Voice Forge";' + #13#10 +
    '$s.Save();' + #13#10;
  if not SaveStringToFile(ScriptPath, Script, False) then
  begin
    Log('无法写入桌面快捷方式修复脚本');
    Exit;
  end;
  if Exec(PowerShell, '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + ScriptPath + '"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    if ResultCode <> 0 then
      Log('PowerShell 桌面快捷方式修复退出码：' + IntToStr(ResultCode));
  end
  else
    Log('无法启动 PowerShell 修复桌面快捷方式');
  DeleteFile(ScriptPath);
end;

procedure EnsureDesktopShortcut();
var
  Shell: Variant;
  LinkPath: String;
  Desktop: String;
begin
  if not WizardIsTaskSelected('desktopicon') then
    Exit;
  LinkPath := ExpandConstant('{userdesktop}\{#AppName}.lnk');
  { 某些 OneDrive/重定向桌面或 junction 会让 Inno 的 [Icons] 创建失败，
    这里用 WScript.Shell 再试一次；旧版快捷方式也在这里更新图标。 }
  try
    Shell := CreateOleObject('WScript.Shell');
  except
    Log('无法创建 WScript.Shell：' + GetExceptionMessage);
    Exit;
  end;
  if not ShortcutIsCurrent(LinkPath, Shell) then
  try
    SaveDesktopShortcut(LinkPath, Shell);
  except
    Log('桌面快捷方式创建失败：' + GetExceptionMessage);
  end;
  if ShortcutIsCurrent(LinkPath, Shell) then
    Exit;
  { 桌面是 junction 时 WScript 会报“不受信任的装入点”；
    从注册表取真实桌面目录再写一次。 }
  Desktop := RealDesktopPath();
  if (Desktop <> '') and DirExists(Desktop) then
  begin
    LinkPath := Desktop + '\{#AppName}.lnk';
    if not ShortcutIsCurrent(LinkPath, Shell) then
    try
      SaveDesktopShortcut(LinkPath, Shell);
    except
      Log('真实桌面路径创建失败：' + GetExceptionMessage);
    end;
    if ShortcutIsCurrent(LinkPath, Shell) then
      Exit;
  end;
  try
    Desktop := Shell.SpecialFolders('Desktop');
  except
    Desktop := '';
  end;
  if Desktop <> '' then
  begin
    LinkPath := Desktop + '\{#AppName}.lnk';
    if not ShortcutIsCurrent(LinkPath, Shell) then
    try
      SaveDesktopShortcut(LinkPath, Shell);
    except
      Log('备用桌面路径创建失败：' + GetExceptionMessage);
    end;
  end;
  if not ShortcutIsCurrent(ExpandConstant('{userdesktop}\{#AppName}.lnk'), Shell) then
    EnsureDesktopShortcutWithPowerShell();
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    EnsureDesktopShortcut();
end;

function InitializeUninstall(): Boolean;
var
  DataDir: String;
  Response: Integer;
  Tail: String;
begin
  Result := True;
  DeleteDataOnUninstall := False;
  DataDir := ExpandConstant('{app}\data');
  if not DirExists(DataDir) then
    Exit;

  Tail := Uppercase(GetCmdTail);

  { 软件内卸载传 /KEEPDATA 或 /DELETEAPPDATA；外部卸载仍给出选择。 }
  if Pos('/KEEPDATA', Tail) > 0 then
    Exit;
  if Pos('/DELETEAPPDATA', Tail) > 0 then
  begin
    DeleteDataOnUninstall := True;
    Exit;
  end;

  if UninstallSilent then
    Exit;

  Response := MsgBox(
    '卸载会删除程序文件。' + #13#10 + #13#10 +
    '是否同时删除 data 文件夹？' + #13#10 +
    '里面保存着项目、训练模型、缓存和导出包。' + #13#10 + #13#10 +
    '选择“是”会一并删除，且无法恢复。',
    mbConfirmation, MB_YESNO or MB_DEFBUTTON2);

  DeleteDataOnUninstall := Response = IDYES;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and DeleteDataOnUninstall then
    DelTree(ExpandConstant('{app}\data'), True, True, True);
end;
