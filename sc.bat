@echo off
setlocal enabledelayedexpansion

REM 设置目标目录
set "targetDir=E:\SmartNoodleHouse"

REM 检查目录是否存在
if not exist "%targetDir%" (
    echo 错误：目录 "%targetDir%" 不存在！
    pause
    exit /b 1
)

echo 正在搜索并删除 %targetDir% 下的所有 __pycache__ 目录...
echo ==============================================

REM 计数器
set count=0

REM 遍历目标目录及其所有子目录
for /d /r "%targetDir%" %%d in (__pycache__) do (
    if exist "%%d" (
        echo 删除: %%d
        rd /s /q "%%d"
        set /a count+=1
    )
)

REM 遍历目标目录及其所有子目录
for /d /r "%targetDir%" %%d in (.pytest_cache) do (
    if exist "%%d" (
        echo 删除: %%d
        rd /s /q "%%d"
        set /a count+=1
    )
)


echo ==============================================
echo 完成！共删除了 !count! 个 __pycache__ 目录。
