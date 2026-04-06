
; Inno Setup bootstrap installer for the Teleop Flasher
; Downloads the latest repo ZIP from GitHub during install.
; Creates local working directories, installs Python dependencies, and generates a launcher BAT file.

#define AppName "Zebra Teleop Flasher"
#define AppVersion "1.0.0"
#define AppPublisher "Joshua Ayers"
#define AppId "{{D0E8D4E4-7B56-4B0B-9F4C-1D3A6E2F1C91}}"
#define GitHubOwner "JoshAyersSBT"
#define GitHubRepo "Zebra_SOL_Flasher"
#define GitHubBranch "main"
#define MainScript "teleop.py"
#define RequirementsFile "requirements.txt"
#define FallbackPipPackages "PyQt6 PyQt6-WebEngine bleak pyserial pygments"

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
Name: "{localappdata}\ZebraTeleopFlasher"
Name: "{localappdata}\ZebraTeleopFlasher\cache"
Name: "{localappdata}\ZebraTeleopFlasher\logs"

[Files]

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\launch_teleop.bat"; WorkingDir: "{app}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\launch_teleop.bat"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\launch_teleop.bat"; WorkingDir: "{app}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[Code]
const
  RepoZipName = 'repo.zip';
  RepoRootName = '{#GitHubRepo}-{#GitHubBranch}';
  GitHubZipUrl = 'https://github.com/{#GitHubOwner}/{#GitHubRepo}/archive/refs/heads/{#GitHubBranch}.zip';

var
  RequiredFiles: array[0..2] of string;
  SelectedPythonCmd: string;

procedure InitializeRequiredFiles;
begin
  RequiredFiles[0] := ExpandConstant('{app}\' + RepoRootName);
  RequiredFiles[1] := ExpandConstant('{app}\' + RepoRootName + '\{#MainScript}');
  RequiredFiles[2] := ExpandConstant('{app}\projects');
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
  Result := ExpandConstant('{tmp}\' + RepoZipName);
end;

function GetRepoExtractPath: string;
begin
  Result := ExpandConstant('{app}');
end;

function GetRequirementsPath: string;
begin
  Result := ExpandConstant('{app}\' + RepoRootName + '\{#RequirementsFile}');
end;

function FindPythonCommand(var PyCmd: string): Boolean;
var
  ResultCode: Integer;
begin
  Result := False;
  PyCmd := '';

  if Exec('cmd.exe', '/C python --version', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0) then
  begin
    PyCmd := 'python';
    Result := True;
    exit;
  end;

  if Exec('cmd.exe', '/C py --version', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0) then
  begin
    PyCmd := 'py';
    Result := True;
    exit;
  end;
end;

function RunCommand(const CommandLine, ErrorMessage: string): Boolean;
var
  ResultCode: Integer;
begin
  Result := False;
  Log('Running command: ' + CommandLine);

  if not Exec('cmd.exe', '/C ' + CommandLine, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    SuppressibleMsgBox(ErrorMessage + #13#10 + #13#10 + CommandLine, mbCriticalError, MB_OK, IDOK);
    exit;
  end;

  if ResultCode <> 0 then
  begin
    SuppressibleMsgBox(ErrorMessage + #13#10 + 'Exit code: ' + IntToStr(ResultCode), mbCriticalError, MB_OK, IDOK);
    exit;
  end;

  Result := True;
end;

function InstallPythonLibraries(const PyCmd: string): Boolean;
var
  ReqPath: string;
  Cmd: string;
begin
  Result := False;
  ReqPath := GetRequirementsPath;

  Cmd := PyCmd + ' -m ensurepip --upgrade';
  if not RunCommand(Cmd, 'Python was found, but pip could not be initialized.') then
    exit;

  Cmd := PyCmd + ' -m pip install --upgrade pip';
  if not RunCommand(Cmd, 'pip could not be upgraded.') then
    exit;

  if FileExists(ReqPath) then
  begin
    Cmd := PyCmd + ' -m pip install -r "' + ReqPath + '"';
    if not RunCommand(Cmd, 'The Python libraries from requirements.txt could not be installed.') then
      exit;
  end
  else
  begin
    Cmd := PyCmd + ' -m pip install {#FallbackPipPackages}';
    if not RunCommand(Cmd, 'The fallback Python libraries could not be installed.') then
      exit;
  end;

  Result := True;
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

  if not ForceDirectories(ExpandConstant('{app}')) then
  begin
    SuppressibleMsgBox('Setup could not create the application folder:' + #13#10 + ExpandConstant('{app}'), mbCriticalError, MB_OK, IDOK);
    exit;
  end;

  if not Exec(
    ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference = ''SilentlyContinue''; [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -UseBasicParsing -Uri ''' + GitHubZipUrl + ''' -OutFile ''' + ZipPath + '''"',
    '',
    SW_HIDE,
    ewWaitUntilTerminated,
    ResultCode
  ) then
  begin
    SuppressibleMsgBox('Setup could not download the Teleop Flasher files from GitHub.', mbCriticalError, MB_OK, IDOK);
    exit;
  end;

  if ResultCode <> 0 then
  begin
    SuppressibleMsgBox('GitHub download failed. Exit code: ' + IntToStr(ResultCode), mbCriticalError, MB_OK, IDOK);
    exit;
  end;

  if not FileExists(ZipPath) then
  begin
    SuppressibleMsgBox('GitHub download reported success, but the ZIP file was not created.' + #13#10 + ZipPath, mbCriticalError, MB_OK, IDOK);
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
    SuppressibleMsgBox('Setup downloaded the ZIP but could not extract it.', mbCriticalError, MB_OK, IDOK);
    exit;
  end;

  if ResultCode <> 0 then
  begin
    SuppressibleMsgBox('ZIP extraction failed. Exit code: ' + IntToStr(ResultCode), mbCriticalError, MB_OK, IDOK);
    exit;
  end;

  if not ForceDirectories(ExpandConstant('{app}\projects')) then
  begin
    SuppressibleMsgBox('Setup could not create the projects directory:' + #13#10 + ExpandConstant('{app}\projects'), mbCriticalError, MB_OK, IDOK);
    exit;
  end;

  if not ForceDirectories(ExpandConstant('{app}\logs')) then
  begin
    SuppressibleMsgBox('Setup could not create the logs directory:' + #13#10 + ExpandConstant('{app}\logs'), mbCriticalError, MB_OK, IDOK);
    exit;
  end;

  Result := True;
end;

function CreateLauncherBatch(const PyCmd: string): Boolean;
var
  BatchPath: string;
  S: string;
begin
  Result := False;
  BatchPath := ExpandConstant('{app}\launch_teleop.bat');

  S := '@echo off' + #13#10;
  S := S + 'setlocal' + #13#10;
  S := S + 'set LOG=%LOCALAPPDATA%\ZebraTeleopFlasher\logs\teleop_launch.log' + #13#10;
  S := S + 'if not exist "%LOCALAPPDATA%\ZebraTeleopFlasher\logs" mkdir "%LOCALAPPDATA%\ZebraTeleopFlasher\logs"' + #13#10;
  S := S + 'echo ==== %DATE% %TIME% launching {#AppName} ====>>"%LOG%"' + #13#10;
  S := S + 'cd /d "%~dp0{#GitHubRepo}-{#GitHubBranch}"' + #13#10;
  S := S + 'echo Working dir: %CD%' + #13#10;
  S := S + 'if not exist "{#MainScript}" (' + #13#10;
  S := S + '  echo ERROR: {#MainScript} not found in %CD%' + #13#10;
  S := S + '  echo ERROR: {#MainScript} not found in %CD%>>"%LOG%"' + #13#10;
  S := S + '  pause' + #13#10;
  S := S + '  exit /b 1' + #13#10;
  S := S + ')' + #13#10;
  S := S + 'set PYCMD=' + PyCmd + #13#10;
  S := S + 'echo Using Python command: %PYCMD%' + #13#10;
  S := S + 'echo Using Python command: %PYCMD%>>"%LOG%"' + #13#10;
  S := S + '%PYCMD% "{#MainScript}" 1>>"%LOG%" 2>&1' + #13#10;
  S := S + 'set ERR=%ERRORLEVEL%' + #13#10;
  S := S + 'echo Exit code: %ERR%>>"%LOG%"' + #13#10;
  S := S + 'if not "%ERR%"=="0" (' + #13#10;
  S := S + '  echo.' + #13#10;
  S := S + '  echo teleop.py exited with code %ERR%' + #13#10;
  S := S + '  echo See log: %LOG%' + #13#10;
  S := S + '  pause' + #13#10;
  S := S + '  exit /b %ERR%' + #13#10;
  S := S + ')' + #13#10;
  S := S + 'exit /b 0' + #13#10;

  if SaveStringToFile(BatchPath, S, False) then
    Result := True
  else
    SuppressibleMsgBox('Setup could not create the launcher batch file.' + #13#10 + BatchPath, mbCriticalError, MB_OK, IDOK);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Missing: string;
begin
  Result := True;

  if CurPageID = wpReady then
  begin
    if not FindPythonCommand(SelectedPythonCmd) then
    begin
      SuppressibleMsgBox('Python was not found on this system.' + #13#10 + #13#10 + 'This installer downloads the project from GitHub and installs Python libraries, so Python must already be installed.' + #13#10 + #13#10 + 'Install Python first, then run this setup again.', mbCriticalError, MB_OK, IDOK);
      Result := False;
      exit;
    end;

    if not DownloadAndExtractRepo then
    begin
      Result := False;
      exit;
    end;

    if not InstallPythonLibraries(SelectedPythonCmd) then
    begin
      Result := False;
      exit;
    end;

    if not CreateLauncherBatch(SelectedPythonCmd) then
    begin
      Result := False;
      exit;
    end;

    InitializeRequiredFiles;
    Missing := MissingRequiredFiles;
    if Missing <> '' then
    begin
      SuppressibleMsgBox('The GitHub download completed, but some required files or folders are missing:' + #13#10 + #13#10 + Missing + #13#10 + #13#10 + 'Please verify that the repository branch contains the expected files.', mbCriticalError, MB_OK, IDOK);
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
    ForceDirectories(ExpandConstant('{app}\projects'));
    ForceDirectories(ExpandConstant('{app}\logs'));
    InitializeRequiredFiles;
    Missing := MissingRequiredFiles;
    if Missing <> '' then
      MsgBox('Installation finished, but some required files or folders are missing:' + #13#10 + #13#10 + Missing, mbCriticalError, MB_OK);
  end;
end;
