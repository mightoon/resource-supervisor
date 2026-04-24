@echo off
chcp 65001 >nul
title 服务器智能管理系统 - 一键打包
echo ========================================
echo   服务器智能管理系统 - 一键打包工具
echo ========================================
echo.

:: 检查 Python 是否安装
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 Python，请先安装 Python 3.8+
    echo 下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [1/4] 检查 Python 环境...
python --version
echo.

:: 安装/升级 PyInstaller
echo [2/4] 安装打包工具 PyInstaller...
pip install pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 (
    echo [错误] PyInstaller 安装失败
    pause
    exit /b 1
)
echo.

:: 执行打包
echo [3/4] 开始打包应用（这可能需要几分钟）...
python build.py --all
if errorlevel 1 (
    echo [错误] 打包失败
    pause
    exit /b 1
)
echo.

:: 完成
echo [4/4] 打包完成！
echo.
echo ========================================
echo  输出目录: dist\服务器智能管理系统_便携版\
echo ========================================
echo.
echo 部署说明:
echo   1. 将整个"服务器智能管理系统_便携版"文件夹复制到目标机器
echo   2. 双击"一键启动.bat"即可运行
echo   3. 内网环境无需安装 Python 或任何依赖
echo.
pause
