@echo off
chcp 65001 >nul
echo ============================================
echo  梦幻花园审稿脚本 - 一键安装
echo ============================================
echo.

:: 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 Python，请先安装 Python 3.8 以上版本
    echo 下载地址: https://www.python.org/downloads/
    echo 安装时勾选 "Add Python to PATH"
    pause
    exit /b 1
)
echo [OK] Python 已安装

:: 安装依赖
echo.
echo 正在安装依赖包...
pip install -r requirements.txt -q
if errorlevel 1 (
    echo [错误] 依赖安装失败，请检查网络连接
    pause
    exit /b 1
)
echo [OK] 依赖安装完成

:: 检查 config.json
if not exist config.json (
    echo.
    echo [提示] 未找到 config.json，正在从模板创建...
    copy config.json.example config.json >nul
    echo [需要操作] 请用记事本打开 config.json，填入你的 API Key
    notepad config.json
) else (
    echo [OK] config.json 已存在
)

echo.
echo ============================================
echo  安装完成！
echo  使用方法请查看 README.md
echo ============================================
pause
