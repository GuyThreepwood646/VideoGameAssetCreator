@echo off
rem Builds ..\VGAC.exe with the C# compiler that ships with Windows (nothing to install).
rem   build.cmd          builds VGAC.exe at the project root and prints its SHA-256
rem   build.cmd test     builds and runs the starter's tests (tests\StarterTests.cs tests\LauncherTests.cs tests\AfterwardsTests.cs) in temporary folders
setlocal
set CSC=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe
if not exist "%CSC%" set CSC=%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe
set REFS=/r:System.dll /r:System.Core.dll /r:System.Drawing.dll /r:System.Windows.Forms.dll /r:System.Web.Extensions.dll /r:System.Management.dll /r:System.IO.Compression.dll /r:System.IO.Compression.FileSystem.dll /r:bin\Microsoft.Web.WebView2.Core.dll /r:bin\Microsoft.Web.WebView2.WinForms.dll
cd /d "%~dp0"
if /i "%1"=="test" goto test
"%CSC%" /nologo /optimize+ /target:winexe /win32icon:icon.ico /out:..\VGAC.exe %REFS% src\*.cs
if errorlevel 1 exit /b 1
powershell -NoProfile -Command "(Get-FileHash ..\VGAC.exe -Algorithm SHA256).Hash.ToLower() + '  VGAC.exe'"
exit /b 0
:test
if not exist obj mkdir obj
"%CSC%" /nologo /target:exe /out:obj\StarterTests.exe %REFS% src\Core.cs src\SafeFs.cs src\Update.cs src\Repair.cs src\Comfy.cs src\Remove.cs src\Move.cs src\Setup.cs src\Logic.cs src\Native.cs src\Supervisor.cs src\RealHost.cs src\WebWindow.cs tests\StarterTests.cs tests\LauncherTests.cs tests\AfterwardsTests.cs
if errorlevel 1 exit /b 1
obj\StarterTests.exe
exit /b %errorlevel%
