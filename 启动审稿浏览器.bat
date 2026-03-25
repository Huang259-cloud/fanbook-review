@echo off
:: 用调试模式启动 Edge，供脚本读取登录状态
start "" "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --remote-debugging-port=9223 --no-proxy-server --user-data-dir="%LOCALAPPDATA%\Microsoft\Edge\User Data"
echo Edge 已启动，请在浏览器中登录 Fanbook 审稿后台
echo 登录后回到此窗口按任意键关闭
pause >nul
