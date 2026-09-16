@echo off
chcp 65001 >nul
setlocal EnableExtensions

echo ================================================
echo Ad Tracking Engine 1.1.0 - 商业发布版加固构建
echo ================================================
echo.

where cargo >nul 2>nul
if errorlevel 1 (
  echo [错误] 未找到 Cargo，请先安装 Rust stable。
  exit /b 1
)

where rc >nul 2>nul
if errorlevel 1 (
  echo [错误] 未找到 rc.exe，请安装 Visual Studio Build Tools + Windows SDK。
  exit /b 1
)

echo [1/6] 检查格式...
cargo fmt -- --check
if errorlevel 1 exit /b 1

echo [2/6] 运行测试...
cargo test --all-targets
if errorlevel 1 exit /b 1

echo [3/6] 清理旧 Release 产物...
cargo clean

echo [4/6] 编译 Release 加固版 DLL...
cargo build --release --locked
if errorlevel 1 exit /b 1

if not exist "target\release\ad_tracking_engine.dll" (
  echo [错误] DLL 未生成。
  exit /b 1
)

echo [5/6] 检查禁止交付的调试/符号文件...
if exist "target\release\ad_tracking_engine.pdb" (
  echo [警告] 发现 PDB：target\release\ad_tracking_engine.pdb
  echo [错误] 商业发布包禁止携带 PDB。
  exit /b 2
)

echo [6/6] 计算 SHA-256...
if not exist "bin" mkdir "bin"
copy /Y "target\release\ad_tracking_engine.dll" "bin\ad_tracking_engine.dll" >nul
certutil -hashfile "bin\ad_tracking_engine.dll" SHA256 > "bin\ad_tracking_engine.dll.sha256"

echo.
echo ================================================
echo 构建完成
echo DLL: bin\ad_tracking_engine.dll
echo SHA: bin\ad_tracking_engine.dll.sha256
echo ================================================
echo.
echo 注意：不要把 target\debug、PDB、私钥、测试密钥交付给客户。
endlocal
