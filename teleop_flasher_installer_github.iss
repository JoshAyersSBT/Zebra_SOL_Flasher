
; Inno Setup bootstrap installer for the Teleop Flasher
; Downloads the latest repo ZIP from GitHub during install.
; Creates local working directories, installs Python dependencies,
; generates a launcher BAT file, and shows step-by-step install logs.

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
Name: "{localappdata}\ZebraTeleopFlasher\projects"

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
  InstallLogPage: TWizardPage;
  InstallLogMemo: TNewMemo;

procedure AddInstallLog(const Msg: string);
begin
  Log(Msg);
  if Assigned(InstallLogMemo) then
  begin
    InstallLogMemo.Lines.Add(Msg);
    InstallLogMemo.SelStart := Length(InstallLogMemo.Text);
  end;
  if WizardForm <> nil then
  begin
    WizardForm.StatusLabel.Caption := Msg;
    WizardForm.Update;
  end;
end;

procedure InitializeWizard;
begin
  InstallLogPage := CreateCustomPage(
    wpReady,
    'Installing components',
    'Setup is preparing the Teleop Flasher. Progress details are shown below.'
  );

  InstallLogMemo := TNewMemo.Create(InstallLogPage);
  InstallLogMemo.Parent := InstallLogPage.Surface;
  InstallLogMemo.Left := ScaleX(0);
  InstallLogMemo.Top := ScaleY(0);
  InstallLogMemo.Width := InstallLogPage.SurfaceWidth;
  InstallLogMemo.Height := InstallLogPage.SurfaceHeight;
  InstallLogMemo.ReadOnly := True;
  InstallLogMemo.ScrollBars := ssVertical;
  InstallLogMemo.WordWrap := False;
  InstallLogMemo.WantReturns := False;
  InstallLogMemo.Text := 'Waiting to start setup...' + #13#10;
end;

procedure InitializeRequiredFiles;
begin
  RequiredFiles[0] := ExpandConstant('{app}\' + RepoRootName);
  RequiredFiles[1] := ExpandConstant('{app}\' + RepoRootName + '\{#MainScript}');
  RequiredFiles[2] := ExpandConstant('{localappdata}\ZebraTeleopFlasher\projects');
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
  AddInstallLog('Checking for Python...');

  if Exec('cmd.exe', '/C python --version', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0) then
  begin
    PyCmd := 'python';
    AddInstallLog('Detected Python command: python');
    Result := True;
    exit;
  end;

  if Exec('cmd.exe', '/C py --version', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0) then
  begin
    PyCmd := 'py';
    AddInstallLog('Detected Python command: py');
    Result := True;
    exit;
  end;

  AddInstallLog('Python was not found.');
end;

function RunCommand(const CommandLine, StepName, ErrorMessage: string): Boolean;
var
  ResultCode: Integer;
begin
  Result := False;
  AddInstallLog('Running: ' + StepName);
  AddInstallLog('  ' + CommandLine);

  if not Exec('cmd.exe', '/C ' + CommandLine, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    AddInstallLog('FAILED to start: ' + StepName);
    SuppressibleMsgBox(ErrorMessage + #13#10 + #13#10 + CommandLine, mbCriticalError, MB_OK, IDOK);
    exit;
  end;

  if ResultCode <> 0 then
  begin
    AddInstallLog('FAILED: ' + StepName + ' (exit code ' + IntToStr(ResultCode) + ')');
    SuppressibleMsgBox(ErrorMessage + #13#10 + 'Exit code: ' + IntToStr(ResultCode), mbCriticalError, MB_OK, IDOK);
    exit;
  end;

  AddInstallLog('Completed: ' + StepName);
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
  if not RunCommand(Cmd, 'Initialize pip', 'Python was found, but pip could not be initialized.') then
    exit;

  Cmd := PyCmd + ' -m pip install --upgrade pip';
  if not RunCommand(Cmd, 'Upgrade pip', 'pip could not be upgraded.') then
    exit;

  if FileExists(ReqPath) then
  begin
    AddInstallLog('requirements.txt found. Installing project dependencies...');
    Cmd := PyCmd + ' -m pip install -r "' + ReqPath + '"';
    if not RunCommand(Cmd, 'Install requirements.txt', 'The Python libraries from requirements.txt could not be installed.') then
      exit;
  end
  else
  begin
    AddInstallLog('requirements.txt not found. Installing fallback packages...');
    Cmd := PyCmd + ' -m pip install {#FallbackPipPackages}';
    if not RunCommand(Cmd, 'Install fallback Python libraries', 'The fallback Python libraries could not be installed.') then
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

  AddInstallLog('Preparing install folders...');
  if FileExists(ZipPath) then
    DeleteFile(ZipPath);

  if not ForceDirectories(ExpandConstant('{app}')) then
  begin
    AddInstallLog('FAILED: could not create application folder.');
    SuppressibleMsgBox('Setup could not create the application folder:' + #13#10 + ExpandConstant('{app}'), mbCriticalError, MB_OK, IDOK);
    exit;
  end;

  AddInstallLog('Downloading project files from GitHub...');
  if not Exec(
    ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference = ''SilentlyContinue''; [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -UseBasicParsing -Uri ''' + GitHubZipUrl + ''' -OutFile ''' + ZipPath + '''"',
    '',
    SW_HIDE,
    ewWaitUntilTerminated,
    ResultCode
  ) then
  begin
    AddInstallLog('FAILED: could not start GitHub download.');
    SuppressibleMsgBox('Setup could not download the Teleop Flasher files from GitHub.', mbCriticalError, MB_OK, IDOK);
    exit;
  end;

  if ResultCode <> 0 then
  begin
    AddInstallLog('FAILED: GitHub download exit code ' + IntToStr(ResultCode));
    SuppressibleMsgBox('GitHub download failed. Exit code: ' + IntToStr(ResultCode), mbCriticalError, MB_OK, IDOK);
    exit;
  end;

  if not FileExists(ZipPath) then
  begin
    AddInstallLog('FAILED: repo ZIP was not created.');
    SuppressibleMsgBox('GitHub download reported success, but the ZIP file was not created.' + #13#10 + ZipPath, mbCriticalError, MB_OK, IDOK);
    exit;
  end;

  AddInstallLog('Download complete.');

  if DirExists(GetRepoExtractPath + '\' + RepoRootName) then
  begin
    AddInstallLog('Removing previous repo copy...');
    DelTree(GetRepoExtractPath + '\' + RepoRootName, True, True, True);
  end;

  AddInstallLog('Extracting project archive...');
  if not Exec(
    ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -LiteralPath ''' + ZipPath + ''' -DestinationPath ''' + GetRepoExtractPath + ''' -Force"',
    '',
    SW_HIDE,
    ewWaitUntilTerminated,
    ResultCode
  ) then
  begin
    AddInstallLog('FAILED: could not start archive extraction.');
    SuppressibleMsgBox('Setup downloaded the ZIP but could not extract it.', mbCriticalError, MB_OK, IDOK);
    exit;
  end;

  if ResultCode <> 0 then
  begin
    AddInstallLog('FAILED: archive extraction exit code ' + IntToStr(ResultCode));
    SuppressibleMsgBox('ZIP extraction failed. Exit code: ' + IntToStr(ResultCode), mbCriticalError, MB_OK, IDOK);
    exit;
  end;

  AddInstallLog('Archive extracted.');

  AddInstallLog('Creating writable user data folders...');
  if not ForceDirectories(ExpandConstant('{localappdata}\ZebraTeleopFlasher\projects')) then
  begin
    AddInstallLog('FAILED: could not create LocalAppData projects folder.');
    SuppressibleMsgBox('Setup could not create the projects directory:' + #13#10 + ExpandConstant('{localappdata}\ZebraTeleopFlasher\projects'), mbCriticalError, MB_OK, IDOK);
    exit;
  end;

  if not ForceDirectories(ExpandConstant('{localappdata}\ZebraTeleopFlasher\logs')) then
  begin
    AddInstallLog('FAILED: could not create LocalAppData logs folder.');
    SuppressibleMsgBox('Setup could not create the logs directory:' + #13#10 + ExpandConstant('{localappdata}\ZebraTeleopFlasher\logs'), mbCriticalError, MB_OK, IDOK);
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
  AddInstallLog('Creating launcher batch file...');

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
  begin
    AddInstallLog('Launcher created.');
    Result := True;
  end
  else
  begin
    AddInstallLog('FAILED: could not create launcher batch file.');
    SuppressibleMsgBox('Setup could not create the launcher batch file.' + #13#10 + BatchPath, mbCriticalError, MB_OK, IDOK);
  end;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Missing: string;
begin
  Result := True;

  if CurPageID = wpReady then
  begin
    WizardForm.NextButton.Enabled := False;
    WizardForm.BackButton.Enabled := False;
    WizardForm.CancelButton.Enabled := False;
    WizardForm.SelectDirPage.Hide;
    WizardForm.SelectTasksPage.Hide;

    AddInstallLog('Starting setup...');

    if not FindPythonCommand(SelectedPythonCmd) then
    begin
      SuppressibleMsgBox('Python was not found on this system.' + #13#10 + #13#10 + 'This installer downloads the project from GitHub and installs Python libraries, so Python must already be installed.' + #13#10 + #13#10 + 'Install Python first, then run this setup again.', mbCriticalError, MB_OK, IDOK);
      Result := False;
    end
    else if not DownloadAndExtractRepo then
    begin
      Result := False;
    end
    else if not InstallPythonLibraries(SelectedPythonCmd) then
    begin
      Result := False;
    end
    else if not CreateLauncherBatch(SelectedPythonCmd) then
    begin
      Result := False;
    end
    else
    begin
      InitializeRequiredFiles;
      Missing := MissingRequiredFiles;
      if Missing <> '' then
      begin
        AddInstallLog('FAILED: required files missing after setup.');
        SuppressibleMsgBox('The GitHub download completed, but some required files or folders are missing:' + #13#10 + #13#10 + Missing + #13#10 + #13#10 + 'Please verify that the repository branch contains the expected files.', mbCriticalError, MB_OK, IDOK);
        Result := False;
      end
      else
      begin
        AddInstallLog('Setup preparation complete.');
      end;
    end;

    WizardForm.CancelButton.Enabled := True;
    WizardForm.BackButton.Enabled := True;
    WizardForm.NextButton.Enabled := True;

    exit;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Missing: string;
begin
  if CurStep = ssInstall then
    AddInstallLog('Finalizing installation...')
  else if CurStep = ssPostInstall then
  begin
    AddInstallLog('Verifying installed files...');
    InitializeRequiredFiles;
    Missing := MissingRequiredFiles;
    if Missing <> '' then
      MsgBox('Installation finished, but some required files or folders are missing:' + #13#10 + #13#10 + Missing, mbCriticalError, MB_OK)
    else
      AddInstallLog('Installation complete.');
  end;
end;
