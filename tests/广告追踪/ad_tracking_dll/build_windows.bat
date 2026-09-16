@echo off
setlocal

echo ========================================
echo Ad Tracking Engine - Windows DLL 构建
echo ========================================
echo.

where cargo >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 Cargo，请先安装 Rust。
    exit /b 1
)

where rc >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 rc.exe。
    echo 请安装 Visual Studio Build Tools 的 Windows SDK。
    exit /b 1
)

echo [1/3] 编译 Windows 版本资源...
if not exist target\release mkdir target\release
rc /nologo /fo target\release\version.res windows\version.rc
if errorlevel 1 exit /b 1

echo [2/3] 编译 Rust DLL...
cargo build --release
if errorlevel 1 exit /b 1

echo [3/3] 提示：
echo 当前 version.rc 已生成 Windows 文件版本资源。
echo 如需把 version.res 链接进 DLL，请使用 MSVC 链接器/构建流程。
echo.

echo 构建完成。
endlocal
