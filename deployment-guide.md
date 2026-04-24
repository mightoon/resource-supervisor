# 服务器智能管理系统 - 内网部署指南

## 📦 打包说明

本项目支持打包成**独立可执行文件**，可在没有 Python 环境的机器上直接运行。

## 🚀 一键打包步骤

### 1. 准备环境
- 确保已安装 Python 3.8 或更高版本
- 确保已安装项目依赖：`pip install -r requirements.txt`

### 2. 执行打包
双击运行项目根目录下的 **"一键打包.bat"**，等待打包完成。

打包过程包括：
- 安装 PyInstaller 打包工具
- 将 Python 代码编译为可执行文件
- 打包所有依赖（paramiko、proxmoxer、requests 等）
- 包含数据文件（config.json、servers.json、users.json）
- 创建便携版文件夹

### 3. 获取输出
打包完成后，会在 `dist/服务器智能管理系统_便携版/` 目录下生成：

```
服务器智能管理系统_便携版/
├── 启动服务器管理.exe    # 主程序可执行文件
├── 一键启动.bat         # 带浏览器自动打开的启动脚本
├── 使用说明.txt         # 使用说明文档
├── config.json          # 配置文件（初始）
├── servers.json         # 服务器数据（初始）
└── users.json           # 用户数据（初始）
```

## 🌐 内网部署

### 方式一：便携版部署（推荐）

1. **复制文件夹**
   将整个 `服务器智能管理系统_便携版` 文件夹复制到内网服务器的任意位置

2. **启动服务**
   在内网服务器上双击运行 `一键启动.bat`

3. **访问系统**
   - 启动后会自动打开浏览器访问 `http://localhost:8080`
   - 内网其他机器可通过以下地址访问：
     - `http://<服务器IP>:8080`
     - `http://<服务器主机名>:8080`

4. **修改端口（可选）**
   如需修改端口，创建 `设置端口.bat`：
   ```batch
   @echo off
   set PORT=8081
   启动服务器管理.exe
   pause
   ```

5. **默认账号**
   - 用户名：`admin`
   - 密码：`123456`

### 方式二：Docker 部署（高级）

如需 Docker 部署，可创建 Dockerfile：

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

COPY . .

EXPOSE 8080

CMD ["python", "server_v2.py"]
```

构建并运行：
```bash
docker build -t server-manager .
docker run -d -p 8080:8080 -v $(pwd)/data:/app/data server-manager
```

## ⚙️ 配置说明

### 修改端口号
如需修改默认端口（8080），可创建 `.env` 文件或在启动前设置环境变量：

```bash
set PORT=8081
```

### 数据持久化
所有数据保存在以下 JSON 文件中：
- `config.json` - 系统配置（模型设置等）
- `servers.json` - 服务器清单数据
- `users.json` - 用户信息

**重要**：升级或迁移时，请备份这些 JSON 文件！

## 🔒 安全建议

1. **修改默认密码**
   首次登录后请立即修改 admin 默认密码

2. **访问控制**
   - 建议在内网环境使用
   - 如需外网访问，请配置反向代理和 HTTPS

3. **数据备份**
   定期备份 `servers.json` 和 `users.json` 文件

## 🛠️ 常见问题

### Q1: 打包后程序无法启动？
- 检查 8080 端口是否被占用
- 检查是否有杀毒软件拦截
- 尝试以管理员身份运行

### Q2: 内网其他机器无法访问？
- 检查服务器防火墙设置，开放 8080 端口
- 检查是否绑定到 localhost，应绑定到 0.0.0.0

### Q3: 数据会丢失吗？
- 数据保存在同目录的 JSON 文件中
- 只要不删除这些文件，数据就会保留
- 建议定期备份

### Q4: 如何在 Linux/Mac 上打包？
```bash
# 安装 PyInstaller
pip install pyinstaller

# 执行打包
python build.py --all

# Linux 输出无后缀的可执行文件，Mac 输出 .app 包
```

## 📞 技术支持

如遇其他问题，请检查：
1. Python 版本是否 3.8+
2. 所有依赖是否正确安装
3. 数据文件是否有读写权限
