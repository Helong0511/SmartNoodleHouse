@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "targetDir=E:\SmartNoodleHouse"
if not exist "%targetDir%" (
    echo Target directory does not exist: "%targetDir%"
    pause
    exit /b 1
)

echo Searching for Python cache directories under "%targetDir%"...
echo ============================================================

set count=0
for /d /r "%targetDir%" %%d in (__pycache__) do (
    if exist "%%d" (
        echo Removing: %%d
        rd /s /q "%%d"
        set /a count+=1
    )
)

for /d /r "%targetDir%" %%d in (.pytest_cache) do (
    if exist "%%d" (
        echo Removing: %%d
        rd /s /q "%%d"
        set /a count+=1
    )
)

echo ============================================================
echo Completed. Removed !count! cache directories.
