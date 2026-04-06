; Inno Setup bootstrap installer for the Teleop Flasher
; This installer downloads the latest project ZIP from GitHub during install.
; It does not require the repository to exist locally on the machine building the installer.

#define AppName "Zebra Teleop Flasher"
#define AppVersion "1.0.0"
#define AppPublisher "Joshua Ayers"
#define AppId "{{D0E8D4E4-7B56-4B0B-9F4C-1D3A6E2F1C91}}"
#define GitHubOwner "JoshAyersSBT"
#define GitHubRepo "Zebra_SOL_Flasher"
#define GitHubBranch "main"
#define MainScript "teleop.py"
#define PythonExeName "python"

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
OutputDir=installer_out
OutputBaseFilename=teleop_flasher_setup
PrivilegesRequired=admin
DisableProgramGroupPage=yes
ChangesAssociations=no
CloseApplications=yes
RestartApplications=no
UsePreviousAppDir=yes
SetupLogging=yes
CreateUninstallRegKey=yes
UninstallDisplayName={#AppName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Dirs]
Name: "{app}"
Name: "{app}\logs"
Name: "{app}\projects"
Name: "{app}\firmware"
Name: "{app}\downloads"
Name: "{localappdata}\ZebraTeleopFlasher"
Name: "{localappdata}\ZebraTeleopFlasher\cache"
Name: "{localappdata}\ZebraTeleopFlasher\logs"

[Files]
; Required helper for download support in Pascal code.

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{cmd}"; Parameters: "/C start """" /D ""{app}\{#GitHubRepo}-{#GitHubBranch}"" {#PythonExeName} ""{app}\{#GitHubRepo}-{#GitHubBranch}\{#MainScript}"""; WorkingDir: "{app}\{#GitHubRepo}-{#GitHubBranch}"; IconFilename: "{sys}\shell32.dll"; IconIndex: 2
Name: "{autodesktop}\{#AppName}"; Filename: "{cmd}"; Parameters: "/C start """" /D ""{app}\{#GitHubRepo}-{#GitHubBranch}"" {#PythonExeName} ""{app}\{#GitHubRepo}-{#GitHubBranch}\{#MainScript}"""; WorkingDir: "{app}\{#GitHubRepo}-{#GitHubBranch}"; IconFilename: "{sys}\shell32.dll"; IconIndex: 2; Tasks: desktopicon

[Run]
Filename: "{cmd}"; Parameters: "/C start """" /D ""{app}\{#GitHubRepo}-{#GitHubBranch}"" {#PythonExeName} ""{app}\{#GitHubRepo}-{#GitHubBranch}\{#MainScript}"""; WorkingDir: "{app}\{#GitHubRepo}-{#GitHubBranch}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[Code]
const
  RepoZipName = 'repo.zip';
  RepoRootName = '{#GitHubRepo}-{#GitHubBranch}';
  GitHubZipUrl = 'https://github.com/{#GitHubOwner}/{#GitHubRepo}/archive/refs/heads/{#GitHubBranch}.zip';

var
  DownloadPage: TDownloadWizardPage;
  RequiredFiles: array[0..3] of string;

procedure InitializeWizard;
begin
  DownloadPage := CreateDownloadPage(
    'Downloading project files',
    'Setup is downloading the latest Teleop Flasher files from GitHub.',
    nil
  );
end;

procedure InitializeRequiredFiles;
begin
  RequiredFiles[0] := ExpandConstant('{app}\' + RepoRootName);
  RequiredFiles[1] := ExpandConstant('{app}\' + RepoRootName + '\\{#MainScript}');
  RequiredFiles[2] := ExpandConstant('{app}\projects');
  RequiredFiles[3] := ExpandConstant('{app}\firmware');
end;

function MissingRequiredFiles: string;
var
  I: Integer;
  Missing: string;
begin
  Missing := '';
  for I := 0 to GetArrayLength(RequiredFiles) - 1 do
  begin
    if not FileOrDirExists(RequiredFiles[I]) then
    begin
      if Missing <> '' then
        Missing := Missing + #13#10;
      Missing := Missing + RequiredFiles[I];
    end;
  end;
  Result := Missing;
end;

function GetRepoZipPath: string;
begin
  Result := ExpandConstant('{app}\downloads\' + RepoZipName);
end;

function GetRepoExtractPath: string;
begin
  Result := ExpandConstant('{app}');
end;

function GetMainScriptPath: string;
begin
  Result := ExpandConstant('{app}\' + RepoRootName + '\\{#MainScript}');
end;

function IsPythonInstalled: Boolean;
var
  ResultCode: Integer;
begin
  Result := Exec('cmd.exe', '/C {#PythonExeName} --version', '', SW_HIDE,
    ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
end;

function DownloadAndExtractRepo: Boolean;
var
  ZipPath: string;
  ResultCode: Integer;
begin
  Result := False;
  ZipPath := GetRepoZipPath;

  if FileExists(ZipPath) then
    DeleteFile(ZipPath);

  if not Exec(
    ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest -Uri ''' + GitHubZipUrl + ''' -OutFile ''' + ZipPath + '''"',
    '',
    SW_HIDE,
    ewWaitUntilTerminated,
    ResultCode
  ) then
  begin
    SuppressibleMsgBox(
      'Setup could not download the Teleop Flasher files from GitHub.',
      mbCriticalError,
      MB_OK,
      IDOK
    );
    exit;
  end;

  if ResultCode <> 0 then
  begin
    SuppressibleMsgBox(
      'GitHub download failed. Exit code: ' + IntToStr(ResultCode),
      mbCriticalError,
      MB_OK,
      IDOK
    );
    exit;
  end;

  if DirExists(GetRepoExtractPath + '\' + RepoRootName) then
    DelTree(GetRepoExtractPath + '\' + RepoRootName, True, True, True);

  if not Exec(
    ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -LiteralPath ''' + ZipPath + ''' -DestinationPath ''' + GetRepoExtractPath + ''' -Force"',
    '',
    SW_HIDE,
    ewWaitUntilTerminated,
    ResultCode
  ) then
  begin
    SuppressibleMsgBox(
      'Setup downloaded the ZIP but could not extract it.',
      mbCriticalError,
      MB_OK,
      IDOK
    );
    exit;
  end;

  if ResultCode <> 0 then
  begin
    SuppressibleMsgBox(
      'ZIP extraction failed. Exit code: ' + IntToStr(ResultCode),
      mbCriticalError,
      MB_OK,
      IDOK
    );
    exit;
  end;

  Result := True;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Missing: string;
begin
  Result := True;

  if CurPageID = wpReady then
  begin
    if not IsPythonInstalled then
    begin
      SuppressibleMsgBox(
        'Python was not found on this system.' + #13#10 + #13#10 +
        'This installer currently downloads the project from GitHub, but it still needs Python installed to run teleop.py.' + #13#10 + #13#10 +
        'Install Python first, then run this setup again.',
        mbCriticalError,
        MB_OK,
        IDOK
      );
      Result := False;
      exit;
    end;

    if not DownloadAndExtractRepo then
    begin
      Result := False;
      exit;
    end;

    InitializeRequiredFiles;
    Missing := MissingRequiredFiles;
    if Missing <> '' then
    begin
      SuppressibleMsgBox(
        'The GitHub download completed, but some required files or folders are missing:' + #13#10 + #13#10 +
        Missing + #13#10 + #13#10 +
        'Please verify that the repository branch contains the expected files.',
        mbCriticalError,
        MB_OK,
        IDOK
      );
      Result := False;
      exit;
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Missing: string;
begin
  if CurStep = ssPostInstall then
  begin
    InitializeRequiredFiles;
    Missing := MissingRequiredFiles;
    if Missing <> '' then
    begin
      MsgBox(
        'Installation finished, but some required files or folders are missing:' + #13#10 + #13#10 +
        Missing,
        mbCriticalError,
        MB_OK
      );
    end;
  end;
end;
