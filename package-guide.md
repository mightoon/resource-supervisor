# 服务器智能管理系统 - 打包说明

## 📦 打包前准备

1. **安装 Python 3.8+**
   - 官网下载: https://www.python.org/downloads/
   - 安装时勾选 "Add Python to PATH"

2. **安装项目依赖**
   ```bash
   pip install -r requirements.txt
   ```

3. **测试运行**
   ```bash
   python server_v2.py
   ```
   确保能正常访问 http://localhost:8080

## 🚀 打包步骤

### 方式一：一键打包（推荐）

双击运行 **`一键打包.bat`**，等待打包完成即可。

打包过程约需 3-5 分钟，取决于机器性能。

### 方式二：命令行打包

```bash
# 执行完整打包流程
python build.py --all

# 或分步执行
python build.py --clean    # 清理旧文件
python build.py --build    # 执行打包
python build.py --portable # 创建便携版
```

## 📁 打包输出

打包完成后，在 `dist/` 目录下生成：

```
dist/
├── 服务器智能管理系统.exe           # 单文件版（不含数据）
└── 服务器智能管理系统_便携版/        # 便携版文件夹（推荐）
    ├── 启动服务器管理.exe
    ├── 一键启动.bat
    ├── 使用说明.txt
    ├── config.json
    ├── servers.json
    └── users.json
```

## 🌐 内网部署

### 部署步骤

1. **复制文件夹**
   将 `dist/服务器智能管理系统_便携版/` 整个文件夹复制到内网服务器

2. **运行服务**
   双击 `一键启动.bat`

3. **访问系统**
   - 本机: http://localhost:8080
   - 内网: http://服务器IP:8080

### 修改端口

如需修改默认端口（8080）：

```batch
set PORT=8081
启动服务器管理.exe
```

### 修改绑定地址

默认绑定所有网卡（0.0.0.0），如需仅本地访问：

```batch
set HOST=127.0.0.1
启动服务器管理.exe
```

## ⚠️ 常见问题

### Q1: 打包失败，提示缺少 PyInstaller？
```bash
pip install pyinstaller
```

### Q2: 打包后的 exe 文件很大？
这是正常现象，PyInstaller 会将 Python 解释器和所有依赖打包进去，文件大小约 15-30MB。

### Q3: 杀毒软件报毒？
PyInstaller 打包的程序可能被某些杀毒软件误报，请添加信任或白名单。

### Q4: 内网其他机器无法访问？
- 检查服务器防火墙是否开放 8080 端口
- 检查 HOST 是否设置为 0.0.0.0（默认）
- 尝试用 IP 地址访问而非主机名

### Q5: 如何减小打包体积？
可使用 UPX 压缩：
```bash
pip install upx-binary
python build.py --all
```

## 🔧 高级配置

### 修改打包配置

编辑 `build.py` 中的 `CONFIG` 字典：

```python
CONFIG = {
    'main_script': 'server_v2.py',      # 主程序
    'app_name': '服务器智能管理系统',    # 应用名称
    'output_dir': 'dist/服务器智能管理系统',
    'icon': 'app.ico',                   # 图标文件（可选）
}
```

### 添加隐藏导入

如果程序运行时提示缺少模块，在 `build.py` 中添加：

```python
'--hidden-import', '模块名',
```

## 📋 系统要求

- **操作系统**: Windows 7/10/11 (64位)
- **内存**: 最低 512MB，推荐 1GB+
- **磁盘**: 50MB 可用空间
- **网络**: 如需 SSH 功能，需确保网络连通

## 📝 更新日志

- v2.0: 支持打包成独立可执行文件
- 支持内网一键部署
- 支持自定义端口配置
