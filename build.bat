@echo off
setlocal enabledelayedexpansion
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

echo [构建] 写入构建时间...
python -c "from datetime import datetime; open('redmine_report/build_time.py','w',encoding='utf-8').write('# Auto-generated\nBUILD_TIME = \"' + datetime.now().strftime('%%Y-%%m-%%d %%H:%%M:%%S') + '\"\n')"

echo [构建] 开始打包...
echo.

pyinstaller redmine_report.spec --clean --noconfirm

REM 获取当前日期时间
for /f "tokens=1,2 delims= " %%a in ('python -c "from datetime import datetime; print(datetime.now().strftime('%%Y%%m%%d_%%H%%M%%S'))"') do set BUILD_DT=%%a

echo.
if exist "dist\Redmine日报工具.exe" (
    ren "dist\Redmine日报工具.exe" "Redmine日报工具_!BUILD_DT!.exe"
    echo ========================================
    echo   构建成功！
    echo   输出: dist\Redmine日报工具_!BUILD_DT!.exe
    echo ========================================
) else (
    echo [错误] 构建失败，请检查上方输出
)
pause
