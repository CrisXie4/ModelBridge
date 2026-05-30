; ===========================================================================
; ModelBridge — Inno Setup installer script
; ===========================================================================
; Builds setup.exe that installs ``mbridge.exe`` + dependencies, adds it
; to PATH, and registers an uninstaller. Mirrors the install UX of
; Python.org's own installer (per-user by default, per-machine on opt-in).
;
; How to build
; ------------
; 1. ``pyinstaller packaging/mbridge.spec --clean --noconfirm``
;    → produces ``dist/mbridge/`` containing ``mbridge.exe`` + DLLs.
; 2. ``"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" packaging/installer.iss``
;    → produces ``packaging/Output/ModelBridge-Setup-0.4.0.exe``.
;
; Tested with Inno Setup 6.2+.
; ===========================================================================

#define MyAppName        "ModelBridge"
#define MyAppVersion     "1.0.0"
#define MyAppPublisher   "ModelBridge Contributors"
#define MyAppURL         "https://github.com/CrisXie4/ModelBridge"
#define MyAppExeName     "mbridge.exe"

[Setup]
AppId={{B86F8D74-4D2C-4A8C-9F4E-9C4F9A6B91A0}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
LicenseFile=
OutputDir=Output
OutputBaseFilename=ModelBridge-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; PrivilegesRequired=lowest defaults to a per-user install (no UAC
; prompt). PrivilegesRequiredOverridesAllowed=dialog lets the user pick
; "Install for all users" at the first wizard page if they want a
; machine-wide install. This matches Python.org's installer behaviour.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

; Tell Windows that we modified the user/system PATH so other shells
; pick it up at next launch without a reboot.
ChangesEnvironment=yes

UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} {#MyAppVersion}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
; Simplified Chinese is a community-contributed file that some Inno
; Setup installs ship and some don't. Use #ifexist so the installer
; builds either way; when present we get an extra Chinese option in the
; language picker, when absent we silently fall back to English-only.
#ifexist AddBackslash(CompilerPath) + "Languages\ChineseSimplified.isl"
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
#endif

[Tasks]
Name: "addpath"; Description: "Add {#MyAppName} to PATH (so ``mbridge`` works in any terminal)"; GroupDescription: "Shell integration:"

[Files]
; Pull the entire PyInstaller dist/mbridge/ directory in. The exe lives
; at the top level; the rest are dlls / Python runtime files.
Source: "..\dist\mbridge\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName} Shell"; \
    Filename: "{cmd}"; \
    Parameters: "/k echo Type ``mbridge --help`` to get started. && cd /d %USERPROFILE%"; \
    WorkingDir: "%USERPROFILE%"; \
    Comment: "Open a command prompt with {#MyAppName} on PATH"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"

[Run]
; Post-install action: show the user the CLI works.
Filename: "{app}\{#MyAppExeName}"; Parameters: "version"; \
    Description: "Verify install (runs ``mbridge version``)"; \
    Flags: postinstall nowait skipifsilent runascurrentuser

[UninstallRun]
; Best-effort cleanup of the user's app-data dir is intentionally
; omitted — never delete ``~/.modelbridge`` automatically because it
; holds the user's api keys + budget history. The uninstaller only
; removes what it installed.

; ---------------------------------------------------------------------------
; PATH modification — uses the EnvAddPath / EnvRemovePath helpers below.
; The Check function ensures we only add once.
; ---------------------------------------------------------------------------

[Code]

const
  EnvironmentKeyAdmin = 'SYSTEM\CurrentControlSet\Control\Session Manager\Environment';
  EnvironmentKeyUser  = 'Environment';

function EnvironmentKey: string;
begin
  if IsAdminInstallMode then
    Result := EnvironmentKeyAdmin
  else
    Result := EnvironmentKeyUser;
end;

function EnvironmentRoot: Integer;
begin
  if IsAdminInstallMode then
    Result := HKEY_LOCAL_MACHINE
  else
    Result := HKEY_CURRENT_USER;
end;

function NeedsAddPath(Param: string): boolean;
var
  OrigPath: string;
begin
  if not RegQueryStringValue(EnvironmentRoot, EnvironmentKey, 'Path', OrigPath) then begin
    Result := True;
    exit;
  end;
  // Look for our path with surrounding ; — avoid false positives on
  // prefixes of unrelated entries. Param is expanded by Inno with the
  // {app} value.
  Result := Pos(';' + UpperCase(Param) + ';', ';' + UpperCase(OrigPath) + ';') = 0;
end;

procedure EnvAddPath(Path: string);
var
  Paths: string;
begin
  if not WizardIsTaskSelected('addpath') then
    exit;
  if not RegQueryStringValue(EnvironmentRoot, EnvironmentKey, 'Path', Paths) then
    Paths := '';
  if Pos(';' + UpperCase(Path) + ';', ';' + UpperCase(Paths) + ';') > 0 then
    exit;
  if (Paths <> '') and (Paths[Length(Paths)] <> ';') then
    Paths := Paths + ';';
  Paths := Paths + Path;
  if not RegWriteExpandStringValue(EnvironmentRoot, EnvironmentKey, 'Path', Paths) then
    Log(Format('Failed to set PATH=%s', [Paths]))
  else
    Log(Format('PATH updated: %s', [Paths]));
end;

procedure EnvRemovePath(Path: string);
var
  Paths: string;
  P: Integer;
begin
  if not RegQueryStringValue(EnvironmentRoot, EnvironmentKey, 'Path', Paths) then
    exit;
  P := Pos(';' + UpperCase(Path) + ';', ';' + UpperCase(Paths) + ';');
  if P = 0 then
    exit;
  // Delete substring; account for leading ; when P > 1.
  if P = 1 then
    Delete(Paths, 1, Length(Path) + 1)
  else
    Delete(Paths, P, Length(Path) + 1);
  RegWriteExpandStringValue(EnvironmentRoot, EnvironmentKey, 'Path', Paths);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    EnvAddPath(ExpandConstant('{app}'));
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    EnvRemovePath(ExpandConstant('{app}'));
end;
