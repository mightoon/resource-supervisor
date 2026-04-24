@echo off
cd /d "d:\cuda\cb_resource-management"
echo %date% %time% > server.log
echo Starting server... >> server.log
python server_minimal.py >> server.log 2>&1
echo Exit code: %errorlevel% >> server.log
