@echo off
chcp 65001 >nul
echo ============================================
echo  梦幻花园 - 一键审稿
echo ============================================
echo.
echo 使用前请确认：
echo   1. Edge 浏览器已用"启动审稿浏览器.bat"打开
echo   2. Edge 中已登录 Fanbook 审稿后台
echo.

set /p TASK_URL=请粘贴活动链接（从浏览器地址栏复制）：
set /p THEME=请输入本期主题关键词（没有直接回车跳过）：

if "%THEME%"=="" (
    python fanbook_review.py --url "%TASK_URL%" --auto-submit --output result.csv
) else (
    python fanbook_review.py --url "%TASK_URL%" --theme "%THEME%" --auto-submit --output result.csv
)

echo.
echo 审稿完成，结果保存在 result.csv
pause
