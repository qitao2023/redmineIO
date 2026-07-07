@echo off
chcp 65001 >nul
echo ========================================
echo   Redmine 日报工具 - 构建 .exe
echo ========================================
echo.

REM 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装 Python 3.9+
    pause
    exit /b 1
)

REM 检查 PyInstaller
python -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo [信息] 正在安装 PyInstaller...
    pip install pyinstaller
)

REM 检查依赖
python -c "import customtkinter" >nul 2>&1
if errorlevel 1 (
    echo [信息] 正在安装 customtkinter...
    pip install customtkinter
)

python -c "import redminelib" >nul 2>&1
if errorlevel 1 (
    echo [信息] 正在安装 python-redmine...
    pip install python-redmine
)

echo [构建] 开始打包...
echo.

pyinstaller redmine_report.spec --clean --noconfirm

echo.
if exist "dist\Redmine日报工具.exe" (
    echo ========================================
    echo   构建成功！
    echo   输出: dist\Redmine日报工具.exe
    echo ========================================
) else (
    echo [错误] 构建失败，请检查上方输出
)
pause
