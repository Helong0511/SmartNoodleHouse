@echo off
chcp 65001 >nul
setlocal
cd /d %~dp0

echo ========================================================================
echo 广告追踪 ID Engine —— Windows 生产级压力测试
echo ========================================================================

if not exist "target\release\ad_tracking_engine.dll" (
  echo [错误] 找不到 target\release\ad_tracking_engine.dll
  echo 请先运行 build_windows.bat 编译 DLL
  exit /b 2
)

set PYTHONPATH=%CD%\python
python tests\stress_test.py --count 100000 --workers 8
exit /b %ERRORLEVEL%
