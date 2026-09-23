; JCIAlert installer - Inno Setup 6
;
; Build it with:  make_installer.bat   (which runs the suites and build.bat first)
;
; ---------------------------------------------------------------------------
; WHY THIS INSTALLS PER-USER, NOT INTO PROGRAM FILES
; ---------------------------------------------------------------------------
; config.json lives BESIDE the exe (jci.py: CONFIG_PATH = HERE/config.json),
; and Program Files is not writable by a normal user. An install there looks
; perfect until the first time someone presses Save in Options - and then
; fails in a way that reads as "the settings don't stick", which this project
; has already spent a day on once.
;
; PrivilegesRequired=lowest makes {autopf} resolve to
; %LOCALAPPDATA%\Programs, which is writable, needs no admin, and raises no
; UAC prompt. On a managed office machine that is also the install most likely
; to be permitted at all.
;
; ---------------------------------------------------------------------------
; THE MUTEX IS NOT DECORATION
; ---------------------------------------------------------------------------
; JCIAlert.exe cannot be overwritten while it is running - Windows holds the
; file. AppMutex makes Inno detect the running copy by the same name jcitray
; uses and ask the user to close it, instead of failing halfway through with
; a file-in-use error and leaving a half-installed folder.
;
; ---------------------------------------------------------------------------
; WHAT AN UPGRADE MUST NOT TOUCH
; ---------------------------------------------------------------------------
; config.json  - the user's settings
; seen.json    - item ids and live story clusters; losing it re-alerts the
;                whole feed on the next poll
; news.csv / opened.json / logs\ - the history
; Only the exe and emiten.json are replaced. emiten.json IS ours to replace:
; it is the ticker registry, refreshed by build_aliases.py, not user data.

#define AppName      "JCIAlert"
#define AppPublisher "Gill"
#define ExeName      "JCIAlert.exe"
#define AppMutexName "Global\JCIAlertTray"
; Kept in step with jci.VERSION by test_installer.py - they drift otherwise.
#define AppVersion   "1.0.1"

[Setup]
AppId={{8F3C21E4-6B7A-4D59-9E2F-JCIALERT0001}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
VersionInfoVersion={#AppVersion}

DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=no
AllowNoIcons=yes

PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesInstallIn64BitMode=x64compatible

; Stops the "file in use" failure. See the header.
AppMutex={#AppMutexName}
CloseApplications=yes
RestartApplications=no

OutputDir=.\Output
OutputBaseFilename={#AppName}-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#AppName} {#AppVersion}
UninstallDisplayIcon={app}\{#ExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; \
    GroupDescription: "Shortcuts:"; Flags: unchecked
Name: "launch";     Description: "&Start JCIAlert when the installer closes"; \
    GroupDescription: "After installing:"

; NOTE: there is deliberately NO "start with Windows" task here. The app owns
; that setting (Options -> Advanced -> Start with Windows, which writes the
; HKCU Run key through jcistartup). A checkbox here would be a second source
; of truth for one setting, and the two would disagree the first time someone
; changed it in Options.

[Files]
; Replaced every time.
Source: "..\dist\{#ExeName}";        DestDir: "{app}"; Flags: ignoreversion
Source: "..\emiten.json";            DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md";              DestDir: "{app}"; Flags: ignoreversion isreadme

; Written ONLY if absent, so an upgrade keeps what the user configured.
Source: "..\config.json";            DestDir: "{app}"; Flags: onlyifdoesntexist
Source: "..\aliases_manual.json";    DestDir: "{app}"; Flags: onlyifdoesntexist

[Icons]
Name: "{group}\{#AppName}";           Filename: "{app}\{#ExeName}"
Name: "{group}\JCIAlert data folder"; Filename: "{app}"
Name: "{autodesktop}\{#AppName}";     Filename: "{app}\{#ExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#ExeName}"; Description: "Start {#AppName}"; \
    Flags: nowait postinstall skipifsilent; Tasks: launch

[UninstallDelete]
; Generated at runtime, so Inno does not know about them and would otherwise
; leave the folder behind. The user's DATA is not listed here on purpose -
; see UninstallRun below.
Type: files;          Name: "{app}\.writable"
Type: filesandordirs; Name: "{app}\__pycache__"

[Code]
// ---------------------------------------------------------------------------
// Uninstall: remove the autostart entry the APP wrote, and ask before
// deleting history. The Run key must go here because the app will not be
// around to remove it, and an entry pointing at a deleted exe is a broken
// login item the user has to hunt down in Task Manager.
// ---------------------------------------------------------------------------
const
  RunKey = 'Software\Microsoft\Windows\CurrentVersion\Run';

procedure CurUninstallStepChanged(CurStep: TUninstallStep);
var
  Keep: Integer;
begin
  if CurStep = usPostUninstall then
  begin
    if RegValueExists(HKEY_CURRENT_USER, RunKey, 'JCIAlert') then
      RegDeleteValue(HKEY_CURRENT_USER, RunKey, 'JCIAlert');

    Keep := MsgBox(
      'Keep your JCIAlert settings and history?' + #13#10 + #13#10 +
      'Yes  - keep config.json, seen.json, news.csv and logs.' +
      #13#10 +
      'No   - delete them. Reinstalling later will start from scratch, and '
      + 'the first run will be silent while it re-reads the feeds.',
      mbConfirmation, MB_YESNO);

    if Keep = IDNO then
    begin
      DeleteFile(ExpandConstant('{app}\config.json'));
      DeleteFile(ExpandConstant('{app}\seen.json'));
      DeleteFile(ExpandConstant('{app}\news.csv'));
      DeleteFile(ExpandConstant('{app}\news.csv.1'));
      DeleteFile(ExpandConstant('{app}\opened.json'));
      DeleteFile(ExpandConstant('{app}\aliases_manual.json'));
      DelTree(ExpandConstant('{app}\logs'), True, True, True);
      DelTree(ExpandConstant('{localappdata}\JCIAlert'), True, True, True);
    end;
  end;
end;

// ---------------------------------------------------------------------------
// Refuse an install into a folder the user cannot write to. config.json lives
// beside the exe, so a read-only target produces an app that runs and then
// cannot save a single setting - the failure is invisible until it matters.
// ---------------------------------------------------------------------------
function NextButtonClick(CurPageID: Integer): Boolean;
var
  Probe: String;
begin
  Result := True;
  if CurPageID = wpSelectDir then
  begin
    Probe := AddBackslash(WizardDirValue) + 'jcialert-write-test.tmp';
    ForceDirectories(WizardDirValue);
    if not SaveStringToFile(Probe, 'ok', False) then
    begin
      MsgBox('JCIAlert keeps its settings next to the program, and this '
        + 'folder is not writable by your account.' + #13#10 + #13#10
        + 'Choose a folder under your own user profile - the default is '
        + 'Local' + 'AppData\Programs\JCIAlert - or re-run this installer as '
        + 'an administrator.',
        mbError, MB_OK);
      Result := False;
    end
    else
      DeleteFile(Probe);
  end;
end;
