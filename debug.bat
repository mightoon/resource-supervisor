@echo off
cd /d "d:\cuda\cb_resource-management"
echo 当前目录: %cd%
echo.
echo Python路径:
python --version
echo.
echo 按任意键启动服务器...
pause
python server.py
echo.
echo 服务器已退出，退出码: %errorlevel%
pause
