#!/usr/bin/env python3
"""
打包脚本 - 使用 PyInstaller 生成独立可执行文件
"""
import subprocess
import sys
import os
import shutil

# 打包配置
CONFIG = {
    'main_script': 'server_v2.py',
    'app_name': '服务器智能管理系统',
    'output_dir': 'dist/服务器智能管理系统',
    'icon': None,  # 如果有图标文件，填写路径如 'app.ico'
}

def clean():
    """清理之前的构建文件"""
    dirs_to_remove = ['build', 'dist', '__pycache__']
    for dir_name in dirs_to_remove:
        if os.path.exists(dir_name):
            print(f"清理 {dir_name}...")
            shutil.rmtree(dir_name)
    
    # 清理 .spec 文件
    for file in os.listdir('.'):
        if file.endswith('.spec'):
            print(f"删除 {file}...")
            os.remove(file)
    print("清理完成")

def build():
    """执行打包"""
    print("开始打包...")
    
    # 构建 PyInstaller 命令
    cmd = [
        sys.executable, '-m', 'PyInstaller',
        '--name', CONFIG['app_name'],
        '--onefile',  # 打包成单个可执行文件
        '--console',  # 显示控制台窗口（服务器应用需要）
        '--clean',
        '--noconfirm',
        # 添加数据文件
        '--add-data', 'templates;templates',
        '--add-data', 'config.json;.',
        '--add-data', 'servers.json;.',
        '--add-data', 'users.json;.',
        # 隐藏导入（确保依赖被包含）
        '--hidden-import', 'paramiko',
        '--hidden-import', 'paramiko.transport',
        '--hidden-import', 'paramiko.ssh_exception',
        '--hidden-import', 'paramiko.rsakey',
        '--hidden-import', 'paramiko.ed25519key',
        '--hidden-import', 'proxmoxer',
        '--hidden-import', 'proxmoxer.backends',
        '--hidden-import', 'proxmoxer.backends.https',
        '--hidden-import', 'proxmoxer.backends.openssh',
        '--hidden-import', 'proxmoxer.core',
        '--hidden-import', 'requests',
        '--hidden-import', 'urllib3',
        '--hidden-import', 'cryptography',
        '--hidden-import', 'cryptography.hazmat.backends',
        '--hidden-import', 'bcrypt',
        '--hidden-import', 'pynacl',
        '--hidden-import', 'idna',
        '--hidden-import', 'charset_normalizer',
        # 主脚本
        CONFIG['main_script']
    ]
    
    if CONFIG['icon'] and os.path.exists(CONFIG['icon']):
        cmd.extend(['--icon', CONFIG['icon']])
    
    print(f"执行命令: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=False)
    
    if result.returncode == 0:
        print("\n✅ 打包成功！")
        print(f"输出目录: {os.path.abspath('dist')}")
        print(f"可执行文件: dist/{CONFIG['app_name']}.exe")
    else:
        print("\n❌ 打包失败！")
        sys.exit(1)

def create_portable_package():
    """创建便携版打包（包含所有依赖）"""
    print("创建便携版打包...")
    
    # 创建输出目录结构
    output_dir = 'dist/服务器智能管理系统_便携版'
    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)
    
    os.makedirs(output_dir, exist_ok=True)
    
    # 复制可执行文件
    exe_source = f"dist/{CONFIG['app_name']}.exe"
    exe_dest = f"{output_dir}/启动服务器管理.exe"
    if os.path.exists(exe_source):
        shutil.copy2(exe_source, exe_dest)
    
    # 复制数据文件（确保内网环境有初始数据）
    data_files = ['config.json', 'servers.json', 'users.json']
    for file in data_files:
        if os.path.exists(file):
            shutil.copy2(file, output_dir)
    
    # 创建启动脚本
    with open(f'{output_dir}/一键启动.bat', 'w', encoding='utf-8') as f:
        f.write('@echo off\n')
        f.write('chcp 65001 >nul\n')
        f.write('title 服务器智能管理系统\n')
        f.write('echo 正在启动服务器智能管理系统...\n')
        f.write('echo 启动后请访问 http://localhost:8080\n')
        f.write('echo 内网访问地址: http://%%计算机名%%:8080 或 http://本机IP:8080\n')
        f.write('echo.\n')
        f.write(':: 延迟2秒后打开浏览器，确保服务已启动\n')
        f.write('start /min cmd /c "timeout /t 2 /nobreak >nul && start http://localhost:8080"\n')
        f.write('"%~dp0启动服务器管理.exe"\n')
        f.write('pause\n')
    
    # 创建使用说明
    with open(f'{output_dir}/使用说明.txt', 'w', encoding='utf-8') as f:
        f.write('服务器智能管理系统 - 便携版\n')
        f.write('=' * 40 + '\n\n')
        f.write('【启动方式】\n')
        f.write('方式1: 双击 "一键启动.bat"（推荐，自动打开浏览器）\n')
        f.write('方式2: 双击 "启动服务器管理.exe"\n\n')
        f.write('【访问地址】\n')
        f.write('本机访问: http://localhost:8080\n')
        f.write('内网访问: http://<服务器IP>:8080\n')
        f.write('         http://<服务器主机名>:8080\n\n')
        f.write('【默认账号】\n')
        f.write('用户名: admin\n')
        f.write('密码: 123456\n\n')
        f.write('【修改端口】\n')
        f.write('如需修改端口，创建批处理文件：\n')
        f.write('  set PORT=8081\n')
        f.write('  启动服务器管理.exe\n\n')
        f.write('【注意事项】\n')
        f.write('1. 首次运行会创建默认管理员账号\n')
        f.write('2. 数据保存在同级目录的 .json 文件中\n')
        f.write('3. 如需内网部署，将整个文件夹复制到内网机器即可\n')
        f.write('4. 确保目标机器端口 8080 未被占用\n')
        f.write('5. 建议首次登录后立即修改默认密码\n\n')
        f.write('【技术支持】\n')
        f.write('如遇问题，请检查防火墙设置或联系管理员\n')
    
    print(f"✅ 便携版打包完成: {os.path.abspath(output_dir)}")

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='打包服务器智能管理系统')
    parser.add_argument('--clean', action='store_true', help='清理构建文件')
    parser.add_argument('--build', action='store_true', help='执行打包')
    parser.add_argument('--portable', action='store_true', help='创建便携版')
    parser.add_argument('--all', action='store_true', help='执行完整流程')
    
    args = parser.parse_args()
    
    if args.all or (not args.clean and not args.build and not args.portable):
        clean()
        build()
        create_portable_package()
    else:
        if args.clean:
            clean()
        if args.build:
            build()
        if args.portable:
            create_portable_package()
