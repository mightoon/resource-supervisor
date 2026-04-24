import json
import os
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
import uuid
import re
import base64
import http.client

# 加载配置文件
def load_config():
    config_file = 'config.json'
    default_config = {
        "proxmox": {
            "host": "192.168.100.160",
            "user": "root@pam",
            "password": "xxx",
            "verify_ssl": False
        }
    }
    if os.path.exists(config_file):
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"加载配置文件失败: {e}，使用默认配置")
            return default_config
    return default_config

CONFIG = load_config()
PROXMOX_CONFIG = CONFIG.get('proxmox', {})
PROXMOX_HOST = PROXMOX_CONFIG.get('host', '192.168.100.160')
PROXMOX_USER = PROXMOX_CONFIG.get('user', 'root@pam')
PROXMOX_PASSWORD = PROXMOX_CONFIG.get('password', 'xxx')
PROXMOX_VERIFY_SSL = PROXMOX_CONFIG.get('verify_ssl', False)

# DeepSeek API 配置
DEEPSEEK_API_KEY = None
try:
    deepseek_config = CONFIG.get('deepseek', {})
    api_key_base64 = deepseek_config.get('api_key_base64', '')
    if api_key_base64:
        DEEPSEEK_API_KEY = base64.b64decode(api_key_base64).decode('utf-8').strip()
        print(f"DeepSeek API key 已加载，长度: {len(DEEPSEEK_API_KEY)}")
except Exception as e:
    print(f"警告: 无法解码 DeepSeek API key: {e}")

# 尝试导入 proxmoxer，如果失败则给出提示
try:
    from proxmoxer import ProxmoxAPI
    PROXMOX_AVAILABLE = True
except ImportError:
    PROXMOX_AVAILABLE = False
    print("警告: proxmoxer 模块未安装，Proxmox 功能不可用")
    print("请运行: pip install proxmoxer requests")

DATA_FILE = 'servers.json'
USERS_FILE = 'users.json'
sessions = {}

# 缓存SSH服务器的diskstats历史数据，用于计算IO速率
# 格式: {ip: {'read_sectors': int, 'write_sectors': int, 'timestamp': float}}
diskstats_cache = {}

def get_current_model_config():
    """获取当前选中的模型配置"""
    config = load_config()
    current_model_id = config.get('current_model', '')
    models = config.get('models', [])
    
    for model in models:
        if model.get('id') == current_model_id:
            # 解码api_key
            api_key = ''
            if model.get('api_key'):
                try:
                    api_key = base64.b64decode(model['api_key']).decode('utf-8').strip()
                except:
                    pass
            return {
                'id': model.get('id'),
                'name': model.get('name'),
                'model': model.get('model'),
                'base_url': model.get('base_url'),
                'api_key': api_key,
                'type': model.get('type', 'public')
            }
    
    # 默认使用deepseek配置（兼容旧版本）
    return {
        'id': 'default',
        'name': 'DeepSeek',
        'model': 'deepseek-chat',
        'base_url': 'https://api.deepseek.com',
        'api_key': DEEPSEEK_API_KEY or '',
        'type': 'public'
    }

def test_model_connection(base_url, model, api_key, model_type='public'):
    """测试模型连接是否可用
    
    通过发送一个简单的chat completion请求来验证模型可用性
    Args:
        base_url: API基础URL
        model: 模型名称
        api_key: API密钥（本地模型可为空）
        model_type: 模型类型，'public'或'local'
    返回: (success: bool, error_msg: str)
    """
    import ssl
    import urllib.request
    import urllib.error
    
    # 构建完整的API URL
    # DeepSeek原生格式: /chat/completions (base_url不含/v1)
    # OpenAI兼容格式: /v1/chat/completions (base_url含/v1) 或 /chat/completions
    base = base_url.rstrip('/')
    
    # 智能判断URL格式
    if 'deepseek' in base and '/v1' not in base:
        # DeepSeek原生API: https://api.deepseek.com/chat/completions
        chat_url = base + '/chat/completions'
    elif base.endswith('/v1'):
        # 用户已提供/v1后缀: http://host:port/v1/chat/completions
        chat_url = base + '/chat/completions'
    else:
        # OpenAI兼容标准格式: base_url + /v1/chat/completions
        chat_url = base + '/v1/chat/completions'
    
    print(f"[验证模型] URL: {chat_url}, Model: {model}, Type: {model_type}, API Key长度: {len(api_key) if api_key else 0}")
    
    # 检查API Key（仅公共模型需要）
    if model_type == 'public' and not api_key:
        return False, "公共模型需要配置 API Key"
    
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Hi"}],
        "stream": False,
        "max_tokens": 5
    }
    
    # 对于 Qwen 模型，根据版本使用不同方式关闭 thinking 模式
    model_lower = model.lower()
    if 'qwen' in model_lower:
        # Qwen3.5 使用 chat_template_kwargs
        if 'qwen3.5' in model_lower or 'qwen35' in model_lower:
            payload["chat_template_kwargs"] = {
                "enable_thinking": False,
                "thinking": False
            }
        # Qwen3 和 Qwen2.5 尝试使用顶层参数
        elif 'qwen3' in model_lower or 'qwen2.5' in model_lower or 'qwen25' in model_lower:
            payload["enable_thinking"] = False
            payload["thinking"] = False
    
    headers = {
        "Content-Type": "application/json"
    }
    
    # 只有提供了API Key才添加到请求头（本地模型可能没有）
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    
    data = json.dumps(payload).encode('utf-8')
    
    req = urllib.request.Request(
        chat_url,
        data=data,
        headers=headers,
        method='POST'
    )
    
    ssl_context = ssl.create_default_context()
    
    try:
        with urllib.request.urlopen(req, context=ssl_context, timeout=10) as response:
            print(f"[验证模型] 成功: HTTP {response.status}")
            return response.status == 200, None
    except urllib.error.HTTPError as e:
        error_msg = f"HTTP {e.code}: {e.reason}"
        try:
            body = e.read().decode('utf-8')
            print(f"[验证模型] HTTP错误: {error_msg}, 响应: {body}")
        except:
            print(f"[验证模型] HTTP错误: {error_msg}")
        if e.code == 401:
            error_msg = "API Key 无效或已过期"
        elif e.code == 404:
            error_msg = "模型不存在或URL路径错误"
        elif e.code == 429:
            error_msg = "请求过于频繁，请稍后再试"
        return False, error_msg
    except urllib.error.URLError as e:
        error_msg = f"无法连接到服务器: {e.reason}"
        print(f"[验证模型] {error_msg}")
        return False, error_msg
    except Exception as e:
        error_msg = str(e)
        print(f"[验证模型] 异常: {error_msg}")
        return False, error_msg

def deepseek_chat_stream(messages):
    """调用当前选中的模型API进行流式对话
    
    Args:
        messages: 消息列表，格式 [{"role": "system"/"user"/"assistant", "content": "..."}]
    
    Yields:
        流式返回的文本片段
    """
    # 获取当前模型配置
    model_config = get_current_model_config()
    api_key = model_config.get('api_key', '')
    
    if not api_key and model_config.get('type') == 'public':
        yield "[错误: API key 未配置]"
        return
    
    try:
        import ssl
        import urllib.request
        
        base_url = model_config.get('base_url', 'https://api.deepseek.com')
        base = base_url.rstrip('/')
        
        # 智能判断URL格式（与test_model_connection保持一致）
        if 'deepseek' in base and '/v1' not in base:
            # DeepSeek原生API
            chat_url = base + '/chat/completions'
        elif base.endswith('/v1'):
            # 用户已提供/v1后缀
            chat_url = base + '/chat/completions'
        else:
            # OpenAI兼容标准格式
            chat_url = base + '/v1/chat/completions'
        
        model_name = model_config.get('model', 'deepseek-chat')
        
        payload = {
            "model": model_name,
            "messages": messages,
            "stream": True,
            "temperature": 0.7
            # 不设置 max_tokens，让模型自行决定输出长度，避免截断
        }
        
        # 对于 Qwen 模型，根据版本使用不同方式关闭 thinking 模式
        model_name_lower = model_name.lower()
        if 'qwen' in model_name_lower:
            # Qwen3.5 使用 chat_template_kwargs
            if 'qwen3.5' in model_name_lower or 'qwen35' in model_name_lower:
                payload["chat_template_kwargs"] = {
                    "enable_thinking": False,
                    "thinking": False
                }
            # Qwen3 和 Qwen2.5 尝试使用顶层参数（某些部署方式支持）
            elif 'qwen3' in model_name_lower or 'qwen2.5' in model_name_lower or 'qwen25' in model_name_lower:
                # 尝试使用额外参数关闭 thinking（部分 vLLM/SGLang 部署支持）
                payload["enable_thinking"] = False
                payload["thinking"] = False
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }
        
        data = json.dumps(payload).encode('utf-8')
        
        req = urllib.request.Request(
            chat_url,
            data=data,
            headers=headers,
            method='POST'
        )
        
        # 创建SSL上下文（允许默认证书）
        ssl_context = ssl.create_default_context()
        
        with urllib.request.urlopen(req, context=ssl_context, timeout=60) as response:
            for line in response:
                line = line.decode('utf-8').strip()
                if line.startswith('data: '):
                    data_str = line[6:]  # 去掉 "data: " 前缀
                    if data_str == '[DONE]':
                        break
                    try:
                        chunk = json.loads(data_str)
                        delta = chunk.get('choices', [{}])[0].get('delta', {})
                        content = delta.get('content', '')
                        if content:
                            yield content
                    except json.JSONDecodeError:
                        continue
                    except Exception:
                        continue
                        
    except Exception as e:
        model_name = model_config.get('name', '未知模型')
        yield f"[{model_name} API 调用错误: {str(e)}]"

def build_diagnosis_prompt(perf_data, logs, server_info):
    """构建诊断提示词 - 深入分析日志中的错误和异常"""
    
    # 格式化性能数据（仅作参考）
    cpu = perf_data.get('cpu', 'N/A')
    mem = perf_data.get('mem', 'N/A')
    disk_io = perf_data.get('disk_io', 'N/A')
    load = perf_data.get('load', 'N/A')
    
    perf_summary = f"""CPU: {cpu}%, 内存: {mem}%, 磁盘IO: {disk_io} MB/s, 负载: {load}"""
    
    # 截断日志
    logs_preview = logs[:12000] if logs else "无法获取日志"
    if len(logs) > 12000:
        logs_preview += "\n... [日志已截断]"
    
    prompt = f"""你是一位专业的服务器运维专家。请深入分析以下系统日志，**重点关注错误、异常和警告**。

## 服务器信息
- 主机名: {server_info.get('hostname', 'Unknown')}
- IP地址: {server_info.get('ip', 'Unknown')}

## 性能指标（参考）
{perf_summary}

## 系统日志
```
{logs_preview}
```

## 分析要求

### 1. 问题识别
扫描日志，找出以下类型的问题：
- **错误 (ERROR/FATAL/CRITICAL)** - 系统/应用错误
- **警告 (WARNING)** - 潜在风险警告
- **服务异常** - 启动失败、崩溃、频繁重启
- **资源问题** - OOM、磁盘满、连接超时
- **安全问题** - 登录失败、权限错误、入侵尝试

### 2. 深度分析（针对每个发现的问题）
对于每个发现的问题，请按以下格式分析：

**问题X: [问题标题]**
- 现象: [具体发生了什么，影响范围]
- 原因: [为什么会发生，技术原因]
- 严重度: [高/中/低]
- 解决: [精炼的解决措施，用1-2句话描述，不要分点列举]
- 预防: [一句话概括如何避免再次发生]

注意：每个问题分析直接给出结论，不要有过渡性描述。

### 3. 整体总结（所有问题分析完后）
最后给出整体评估：
- 系统健康状态: [良好/一般/差]
- 优先处理: [列出需要优先处理的问题]
- 维护建议: [长期维护建议]

### 输出格式要求
- **必须使用简体中文（Chinese）回复，禁止输出英文**
- 使用 ## 作为一级标题、### 作为二级标题
- 每个问题使用 **粗体** 标记问题类型和关键信息
- 使用 - 标记列表项
- 如有命令或代码，使用 ``` 代码块

**重要提示**：
- **无论日志内容是什么语言，你的所有回复必须用简体中文**
- 不要描述正常运行的情况
- 如果确实没有问题，直接回复"日志分析未发现问题，系统运行正常"
- 分析问题要深入具体，不要泛泛而谈"""

    return prompt

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {'servers': []}

def get_proxmox_vm_node_mapping():
    """从 Proxmox API 获取虚拟机所属的物理节点映射 {vmid: node_name}"""
    mapping = {}
    if not PROXMOX_AVAILABLE:
        return mapping
    try:
        proxmox = get_proxmox_client()
        if not proxmox:
            return mapping
        
        # 获取所有节点
        nodes = proxmox.nodes.get()
        for node in nodes:
            node_name = node['node']
            # 获取该节点下的所有 QEMU 虚拟机
            try:
                qemu_vms = proxmox.nodes(node_name).qemu.get()
                for vm in qemu_vms:
                    vmid = str(vm.get('vmid'))
                    if vmid:
                        mapping[vmid] = node_name
            except:
                pass
            # 获取该节点下的所有 LXC 容器
            try:
                lxc_containers = proxmox.nodes(node_name).lxc.get()
                for ct in lxc_containers:
                    vmid = str(ct.get('vmid'))
                    if vmid:
                        mapping[vmid] = node_name
            except:
                pass
    except Exception as e:
        print(f"获取 Proxmox VM 映射失败: {e}")
    return mapping

def save_data(data):
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_users():
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    # 默认创建admin用户
    default_users = {
        'admin': {'password': '123456', 'role': 'admin'}
    }
    save_users(default_users)
    return default_users

def save_users(users):
    with open(USERS_FILE, 'w', encoding='utf-8') as f:
        json.dump(users, f, ensure_ascii=False, indent=2)

def get_used_gpus():
    """获取全局已使用的GPU（0-7编号）- 保留用于兼容性"""
    data = load_data()
    used = set()
    for s in data['servers']:
        for g in s.get('assigned_gpus', []):
            used.add(g)
    return used

def get_used_gpus_by_host(parent_host):
    """获取指定物理机上已被分配的GPU编号（1-based）"""
    data = load_data()
    used = set()
    for s in data['servers']:
        if s.get('parent_host') == parent_host:
            for g in s.get('assigned_gpus', []):
                used.add(g)
    return used

def get_physical_server_by_hostname(hostname):
    """根据主机名获取物理机信息"""
    data = load_data()
    for s in data['servers']:
        if s['type'] == 'physical' and s['hostname'] == hostname:
            return s
    return None

def verify_ssh_connection(ip, username, password, timeout=5):
    """验证SSH连接是否可用"""
    try:
        import socket
        # 首先尝试TCP连接到22端口
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((ip, 22))
        sock.close()
        if result != 0:
            return False, f"无法连接到 {ip}:22，请检查IP地址和网络连通性"
        
        # 尝试导入paramiko进行SSH验证
        try:
            import paramiko
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(ip, port=22, username=username, password=password, timeout=timeout)
            # 执行简单命令验证
            stdin, stdout, stderr = client.exec_command('echo "SSH连接测试成功"')
            output = stdout.read().decode().strip()
            client.close()
            if "SSH连接测试成功" in output:
                return True, None
            else:
                return False, "SSH连接验证失败，无法执行远程命令"
        except ImportError:
            # 如果没有paramiko，只检查端口连通性
            return True, None
        except paramiko.AuthenticationException:
            return False, "SSH认证失败，请检查用户名和密码"
        except Exception as e:
            return False, f"SSH连接错误: {str(e)}"
    except Exception as e:
        return False, f"连接测试失败: {str(e)}"

def get_ssh_performance(ip, username, password, timeout=10):
    """通过SSH获取服务器性能数据"""
    try:
        import paramiko
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(ip, port=22, username=username, password=password, timeout=timeout)
        
        # 批量获取性能数据的脚本
        script = '''
# 初始化默认值
CPU_USAGE=0
MEM_TOTAL=0
MEM_USED=0
MEM_USAGE=0
DISK_IO=0
READ_MB=0
WRITE_MB=0
LOAD_AVG=0

# CPU使用率（计算1秒间隔）
CPU_IDLE1=$(cat /proc/stat | grep "^cpu " | awk '{print $5}')
CPU_TOTAL1=$(cat /proc/stat | grep "^cpu " | awk '{sum=$2+$3+$4+$5+$6+$7+$8} END {print sum}')
sleep 1
CPU_IDLE2=$(cat /proc/stat | grep "^cpu " | awk '{print $5}')
CPU_TOTAL2=$(cat /proc/stat | grep "^cpu " | awk '{sum=$2+$3+$4+$5+$6+$7+$8} END {print sum}')

if [ -n "$CPU_TOTAL1" ] && [ -n "$CPU_TOTAL2" ] && [ "$CPU_TOTAL1" != "$CPU_TOTAL2" ]; then
    CPU_USAGE=$(echo "scale=2; 100 * (1 - ($CPU_IDLE2 - $CPU_IDLE1) / ($CPU_TOTAL2 - $CPU_TOTAL1))" | bc 2>/dev/null || echo 0)
fi

# 内存信息 (单位KB，转换为GB)
MEM_TOTAL_KB=$(cat /proc/meminfo | grep MemTotal | awk '{print $2}')
MEM_AVAILABLE_KB=$(cat /proc/meminfo | grep MemAvailable | awk '{print $2}')
if [ -n "$MEM_TOTAL_KB" ] && [ -n "$MEM_AVAILABLE_KB" ]; then
    MEM_USED_KB=$((MEM_TOTAL_KB - MEM_AVAILABLE_KB))
    MEM_USAGE=$(echo "scale=2; 100 * $MEM_USED_KB / $MEM_TOTAL_KB" | bc 2>/dev/null || echo 0)
    # 转换为GB
    MEM_TOTAL=$(echo "scale=2; $MEM_TOTAL_KB / 1024 / 1024" | bc 2>/dev/null || echo 0)
    MEM_USED=$(echo "scale=2; $MEM_USED_KB / 1024 / 1024" | bc 2>/dev/null || echo 0)
fi

# 磁盘IO - 获取当前diskstats累计值
# 获取主磁盘设备（通常是sda或nvme0n1）
DISK_DEV=$(lsblk -nd -o NAME 2>/dev/null | grep -E '^(sda|nvme0n1|vda|hda)' | head -1)
if [ -z "$DISK_DEV" ]; then
    DISK_DEV="sda"
fi

# 读取当前磁盘统计（累计扇区数）
DISK_STAT=$(cat /proc/diskstats | grep "$DISK_DEV" | head -1)
READ_SECTORS=$(echo "$DISK_STAT" | awk '{print $6}')
WRITE_SECTORS=$(echo "$DISK_STAT" | awk '{print $10}')

# 返回原始累计值，后端会计算速率
if [ -z "$READ_SECTORS" ]; then
    READ_SECTORS=0
fi
if [ -z "$WRITE_SECTORS" ]; then
    WRITE_SECTORS=0
fi

# 系统负载（作为运行状态的参考）
LOAD_AVG=$(uptime | awk -F'load average:' '{print $2}' | awk '{print $1}' | sed 's/,//')
if [ -z "$LOAD_AVG" ]; then
    LOAD_AVG=0
fi

# 输出JSON格式（返回累计扇区数，后端计算速率）
printf '{"cpu_usage": %s, "mem_total": %s, "mem_used": %s, "mem_usage": %s, "disk_io": 0, "disk_io_bytes": 0, "disk_read_sectors": %s, "disk_write_sectors": %s, "load_avg": %s}\n' "$CPU_USAGE" "$MEM_TOTAL" "$MEM_USED" "$MEM_USAGE" "$READ_SECTORS" "$WRITE_SECTORS" "$LOAD_AVG"
'''
        
        stdin, stdout, stderr = client.exec_command(script)
        output = stdout.read().decode().strip()
        error = stderr.read().decode().strip()
        client.close()
        
        if error:
            print(f"SSH获取性能数据错误: {error}")
        
        # 解析JSON结果
        import json
        import time
        perf_data = json.loads(output)
        
        # 计算磁盘IO速率（基于10分钟间隔或首次建立基准）
        current_time = time.time()
        read_sectors = int(perf_data.get('disk_read_sectors', 0))
        write_sectors = int(perf_data.get('disk_write_sectors', 0))
        
        if ip in diskstats_cache:
            # 有缓存，计算速率
            cached = diskstats_cache[ip]
            time_diff = current_time - cached['timestamp']
            
            if time_diff >= 30:  # 至少间隔30秒才计算
                read_diff = max(0, read_sectors - cached['read_sectors'])
                write_diff = max(0, write_sectors - cached['write_sectors'])
                
                # 计算字节/秒（每扇区512字节）
                disk_read_bytes = int(read_diff * 512 / time_diff)
                disk_write_bytes = int(write_diff * 512 / time_diff)
                disk_io_bytes = disk_read_bytes + disk_write_bytes
                
                perf_data['disk_io_bytes'] = disk_io_bytes
                perf_data['disk_read_bytes'] = disk_read_bytes
                perf_data['disk_write_bytes'] = disk_write_bytes
                perf_data['disk_io'] = round(disk_io_bytes / (1024 * 1024), 2)
                
                print(f"[SSH IO] {ip}: {disk_io_bytes} bytes/s (间隔 {time_diff:.0f}s)")
                
                # 更新缓存
                diskstats_cache[ip] = {
                    'read_sectors': read_sectors,
                    'write_sectors': write_sectors,
                    'timestamp': current_time
                }
            else:
                # 间隔太短，使用上次的IO值（或0）
                perf_data['disk_io_bytes'] = 0
                perf_data['disk_read_bytes'] = 0
                perf_data['disk_write_bytes'] = 0
                perf_data['disk_io'] = 0
                print(f"[SSH IO] {ip}: 间隔太短 ({time_diff:.0f}s < 30s)，使用缓存")
        else:
            # 首次建立基准
            diskstats_cache[ip] = {
                'read_sectors': read_sectors,
                'write_sectors': write_sectors,
                'timestamp': current_time
            }
            perf_data['disk_io_bytes'] = 0
            perf_data['disk_read_bytes'] = 0
            perf_data['disk_write_bytes'] = 0
            perf_data['disk_io'] = 0
            print(f"[SSH IO] {ip}: 首次建立基准， sectors={read_sectors}/{write_sectors}")
        
        return perf_data, None
    except ImportError:
        return None, "paramiko模块未安装"
    except Exception as e:
        return None, f"SSH获取性能数据失败: {str(e)}"

def get_ssh_logs(ip, username, password, lines=100, timeout=10):
    """通过SSH获取服务器近期日志"""
    try:
        import paramiko
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(ip, port=22, username=username, password=password, timeout=timeout)
        
        # 获取系统日志（优先使用journalctl，否则使用/var/log/syslog）
        script = '''
#!/bin/bash
if command -v journalctl &> /dev/null; then
    journalctl --no-pager --since "24 hours ago" -n {lines} 2>/dev/null || journalctl --no-pager -n {lines}
else
    tail -n {lines} /var/log/syslog 2>/dev/null || tail -n {lines} /var/log/messages 2>/dev/null || echo "无法获取日志"
fi
'''.format(lines=lines)
        
        stdin, stdout, stderr = client.exec_command(script)
        output = stdout.read().decode('utf-8', errors='ignore').strip()
        error = stderr.read().decode('utf-8', errors='ignore').strip()
        client.close()
        
        if error and not output:
            return None, f"SSH获取日志错误: {error}"
        
        # 截取日志内容（限制长度）
        max_length = 50000
        if len(output) > max_length:
            output = output[-max_length:]
            output = "[日志过长，仅显示最近部分]\n..." + output
            
        return output, None
    except ImportError:
        return None, "paramiko模块未安装"
    except Exception as e:
        return None, f"SSH获取日志失败: {str(e)}"

def get_proxmox_vm_performance(node_name, vmid, vm_type='qemu'):
    """通过Proxmox API获取虚拟机性能数据"""
    if not PROXMOX_AVAILABLE:
        return None, "Proxmox API 不可用"
    
    try:
        proxmox = get_proxmox_client()
        if not proxmox:
            return None, "Proxmox连接失败"
        
        # 获取虚拟机当前状态
        if vm_type == 'lxc':
            status = proxmox.nodes(node_name).lxc(vmid).status.current.get()
        else:
            status = proxmox.nodes(node_name).qemu(vmid).status.current.get()
        
        # 提取性能数据
        maxmem = float(status.get('maxmem', 1) or 1)
        mem = float(status.get('mem', 0) or 0)
        cpu = float(status.get('cpu', 0) or 0)  # CPU使用率（0-1之间）
        
        # 转换为百分比
        cpu_usage = round(cpu * 100, 2) if cpu else 0
        mem_usage = round(mem / maxmem * 100, 2) if maxmem else 0
        
        # 内存转换为GB
        mem_total_gb = round(maxmem / (1024**3), 2)
        mem_used_gb = round(mem / (1024**3), 2)
        
        # 获取磁盘IO数据（通过RRRR数据接口）
        disk_io_bytes = 0  # 总IO（字节/秒）
        disk_read_bytes = 0  # 读IO（字节/秒）
        disk_write_bytes = 0  # 写IO（字节/秒）
        try:
            # 使用rrddata接口获取原始RRD数据
            if vm_type == 'lxc':
                rrd_data = proxmox.nodes(node_name).lxc(vmid).rrddata.get(timeframe='hour', cf='AVERAGE')
            else:
                rrd_data = proxmox.nodes(node_name).qemu(vmid).rrddata.get(timeframe='hour', cf='AVERAGE')
            
            if rrd_data and len(rrd_data) > 0:
                # 获取最新的数据点（过滤掉None值）
                latest = None
                for data in reversed(rrd_data):
                    if data and (data.get('diskread') is not None or data.get('diskwrite') is not None):
                        latest = data
                        break
                
                if latest:
                    # 磁盘IO字段：diskread和diskwrite（单位是字节/秒）
                    disk_read_bytes = float(latest.get('diskread', 0) or 0)
                    disk_write_bytes = float(latest.get('diskwrite', 0) or 0)
                    disk_io_bytes = disk_read_bytes + disk_write_bytes
        except Exception as e:
            print(f"获取虚拟机磁盘IO失败: {e}")
        
        perf_data = {
            'cpu_usage': cpu_usage,
            'mem_total': mem_total_gb,
            'mem_used': mem_used_gb,
            'mem_usage': mem_usage,
            'disk_io': round(disk_io_bytes / (1024 * 1024), 2),  # MB/s（用于饼图百分比）
            'disk_io_bytes': disk_io_bytes,  # 字节/秒（用于前端动态单位）
            'disk_read_bytes': disk_read_bytes,  # 字节/秒
            'disk_write_bytes': disk_write_bytes,  # 字节/秒
            'status': status.get('status', 'unknown')
        }
        return perf_data, None
    except Exception as e:
        return None, f"Proxmox获取性能数据失败: {str(e)}"

def get_proxmox_node_performance(node_name):
    """通过Proxmox API获取物理节点性能数据"""
    if not PROXMOX_AVAILABLE:
        return None, "Proxmox API 不可用"
    
    try:
        proxmox = get_proxmox_client()
        if not proxmox:
            return None, "Proxmox连接失败"
        
        # 获取节点状态
        status = proxmox.nodes(node_name).status.get()
        
        # 提取性能数据
        memory = status.get('memory', {})
        cpu_info = status.get('cpuinfo', {})
        loadavg = status.get('loadavg', [0, 0, 0])
        
        mem_total = float(memory.get('total', 1) or 1)
        mem_used = float(memory.get('used', 0) or 0)
        mem_free = float(memory.get('free', 0) or 0)
        
        # 计算内存使用率
        mem_usage = round(mem_used / mem_total * 100, 2) if mem_total else 0
        cpu_cores = int(cpu_info.get('cpus', 1) or 1)
        load_avg = float(loadavg[0] if isinstance(loadavg, list) and len(loadavg) > 0 else 0)
        # 估算CPU使用率（基于负载）
        cpu_usage = round(min(load_avg / cpu_cores * 100, 100), 2) if cpu_cores else 0
        
        # 获取磁盘IO数据
        # 注意：Proxmox节点的rrddata没有diskread/diskwrite字段
        # 只能通过rrddata获取到iowait（IO等待时间），这不是吞吐量
        # 物理机磁盘IO暂时无法通过Proxmox API获取
        disk_io_bytes = 0
        disk_read_bytes = 0
        disk_write_bytes = 0
        
        perf_data = {
            'cpu_usage': cpu_usage,
            'mem_total': round(mem_total / (1024**3), 2),
            'mem_used': round(mem_used / (1024**3), 2),
            'mem_usage': mem_usage,
            'disk_io': round(disk_io_bytes / (1024 * 1024), 2),
            'disk_io_bytes': disk_io_bytes,
            'disk_read_bytes': disk_read_bytes,
            'disk_write_bytes': disk_write_bytes,
            'load_avg': load_avg
        }
        return perf_data, None
    except Exception as e:
        return None, f"Proxmox获取节点性能数据失败: {str(e)}"

def get_proxmox_logs(node_name, vmid=None, vm_type='qemu', lines=100):
    """通过Proxmox API获取服务器日志
    
    Args:
        node_name: Proxmox节点名称
        vmid: 虚拟机ID (如果是物理节点则为None)
        vm_type: 虚拟机类型 ('qemu' 或 'lxc')
        lines: 获取的日志行数
    
    Returns:
        (logs_str, error_str): 日志内容和错误信息
    """
    print(f"[DEBUG] get_proxmox_logs called: node={node_name}, vmid={vmid}, vm_type={vm_type}, lines={lines}")
    
    if not PROXMOX_AVAILABLE:
        print(f"[DEBUG] proxmoxer模块未安装")
        return None, "Proxmox API 不可用 (proxmoxer模块未安装)"
    
    try:
        proxmox = get_proxmox_client()
        if not proxmox:
            print(f"[DEBUG] Proxmox连接失败")
            return None, "Proxmox连接失败"
        
        print(f"[DEBUG] Proxmox客户端创建成功")
        logs = []
        
        if vmid is None:
            # 获取物理节点的系统日志
            print(f"[DEBUG] 获取物理节点 {node_name} 的syslog")
            try:
                syslog_entries = proxmox.nodes(node_name).syslog.get(limit=lines)
                print(f"[DEBUG] 获取到 {len(syslog_entries)} 条syslog记录")
                for entry in syslog_entries:
                    time_str = entry.get('time', '')
                    msg = entry.get('msg', '')
                    prio = entry.get('prio', '')
                    logs.append(f"[{time_str}] [{prio}] {msg}")
            except Exception as e:
                print(f"[DEBUG] 获取节点syslog失败: {str(e)}")
                return None, f"获取节点syslog失败: {str(e)}"
        else:
            # 获取虚拟机/容器的日志
            print(f"[DEBUG] 获取 {vm_type} 虚拟机/容器 {vmid} 的日志")
            try:
                if vm_type == 'lxc':
                    # LXC容器有专门的log端点
                    print(f"[DEBUG] 调用LXC log API: nodes/{node_name}/lxc/{vmid}/log")
                    log_entries = proxmox.nodes(node_name).lxc(vmid).log.get(limit=lines)
                    print(f"[DEBUG] 获取到 {len(log_entries)} 条LXC日志记录")
                    for entry in log_entries:
                        time_str = entry.get('t', entry.get('time', ''))
                        msg = entry.get('msg', '')
                        logs.append(f"[{time_str}] {msg}")
                else:
                    # QEMU虚拟机：获取任务历史作为日志，同时获取节点日志
                    print(f"[DEBUG] QEMU VM: 尝试获取任务历史和节点日志")
                    vm_id_str = str(vmid)
                    
                    # 1. 获取VM状态信息
                    try:
                        print(f"[DEBUG] 获取VM {vmid} 状态")
                        vm_status = proxmox.nodes(node_name).qemu(vmid).status.current.get()
                        print(f"[DEBUG] VM状态: {vm_status}")
                        status_info = f"VM状态: {vm_status.get('qmpstatus', vm_status.get('status', 'unknown'))}, "
                        status_info += f"CPU: {vm_status.get('cpu', 0):.2f}%, "
                        maxmem_gb = vm_status.get('maxmem', 0) / (1024**3)
                        mem_gb = vm_status.get('mem', 0) / (1024**3)
                        status_info += f"内存: {mem_gb:.2f}GB / {maxmem_gb:.2f}GB, "
                        status_info += f"磁盘读: {vm_status.get('diskread', 0)} bytes, 写: {vm_status.get('diskwrite', 0)} bytes"
                        logs.append(f"[当前状态] {status_info}")
                    except Exception as e:
                        print(f"[DEBUG] 获取VM状态失败: {str(e)}")
                        logs.append(f"[当前状态] 无法获取: {str(e)}")
                    
                    # 2. 获取VM配置信息
                    try:
                        print(f"[DEBUG] 获取VM {vmid} 配置")
                        vm_config = proxmox.nodes(node_name).qemu(vmid).config.get()
                        print(f"[DEBUG] VM配置获取成功")
                        cores = vm_config.get('cores', '未知')
                        memory = vm_config.get('memory', '未知')
                        logs.append(f"[VM配置] CPU核心数: {cores}, 内存: {memory}MB")
                    except Exception as e:
                        print(f"[DEBUG] 获取VM配置失败: {str(e)}")
                    
                    # 3. 获取VM任务历史（扩大搜索范围）
                    try:
                        print(f"[DEBUG] 获取VM {vmid} 任务历史")
                        # 获取更多任务记录
                        all_tasks = proxmox.nodes(node_name).tasks.get(limit=lines*5)
                        print(f"[DEBUG] 获取到 {len(all_tasks)} 条任务记录")
                        vm_tasks = []
                        for task in all_tasks:
                            # 多种方式匹配VM相关任务
                            task_id = task.get('id', '')
                            task_type = task.get('type', '')
                            task_user = task.get('user', '')
                            # 匹配 vmid、upid 中包含 vmid、或者类型包含 vm
                            if (vm_id_str in task_id or 
                                vm_id_str in task_type or 
                                f'qemu-{vm_id_str}' in str(task) or
                                (task_type and 'qm' in task_type.lower())):
                                vm_tasks.append(task)
                        
                        print(f"[DEBUG] 过滤后得到 {len(vm_tasks)} 条VM相关任务")
                        if vm_tasks:
                            logs.append(f"\n[VM任务历史 - 最近{len(vm_tasks)}条]")
                            for task in vm_tasks[:lines]:
                                start_time = task.get('starttime', '')
                                status = task.get('status', '')
                                task_type = task.get('type', '')
                                user = task.get('user', '')
                                task_id = task.get('id', '')
                                logs.append(f"[{start_time}] {task_type} (ID:{task_id}) 状态:{status} 用户:{user}")
                        else:
                            logs.append(f"\n[VM任务历史] 暂无VM {vmid} 相关任务记录")
                    except Exception as e:
                        print(f"[DEBUG] 获取任务历史失败: {str(e)}")
                        logs.append(f"[VM任务历史] 无法获取: {str(e)}")
                    
                    # 4. 获取VM所在节点的系统日志（只保留与当前VM相关的条目）
                    try:
                        print(f"[DEBUG] 获取节点 {node_name} 的系统日志并过滤VM {vmid} 相关条目")
                        syslog_entries = proxmox.nodes(node_name).syslog.get(limit=lines*3)
                        print(f"[DEBUG] 获取到 {len(syslog_entries)} 条节点syslog记录，开始过滤")
                        vm_syslog_entries = []
                        vm_id_str = str(vmid)
                        # 用于匹配其他VMID的正则，用于排除其他VM的日志
                        other_vm_patterns = []
                        for i in range(100, 1000):  # 假设VMID范围在100-999
                            if i != int(vmid):
                                other_vm_patterns.append(f' {i} ')
                                other_vm_patterns.append(f'/{i}/')
                                other_vm_patterns.append(f'VM {i}')
                                other_vm_patterns.append(f'vmid={i}')
                        
                        for entry in syslog_entries:
                            msg = entry.get('msg', '')
                            # 检查是否包含当前VMID
                            is_current_vm = (vm_id_str in msg or 
                                           f'VM {vm_id_str}' in msg or 
                                           f'vmid={vm_id_str}' in msg or
                                           f'qemu-{vm_id_str}' in msg or
                                           f'/{vm_id_str}/' in msg)
                            
                            # 如果包含当前VMID，则进一步检查是否也包含其他VMID（排除混合日志）
                            if is_current_vm:
                                has_other_vm = False
                                for pattern in other_vm_patterns:
                                    if pattern in msg:
                                        has_other_vm = True
                                        break
                                if not has_other_vm:
                                    vm_syslog_entries.append(entry)
                            # 也包含一些通用的非VM特定日志（如节点级别的警告/错误）
                            elif any(keyword in msg.lower() for keyword in ['error', 'fail', 'warning', 'critical', 'fatal']):
                                # 确保这条通用日志不包含任何其他VMID
                                has_any_vm = False
                                for i in range(100, 1000):
                                    if str(i) in msg and i != int(vmid):
                                        has_any_vm = True
                                        break
                                if not has_any_vm:
                                    vm_syslog_entries.append(entry)
                        
                        print(f"[DEBUG] 过滤后得到 {len(vm_syslog_entries)} 条VM {vmid} 相关的syslog记录")
                        if vm_syslog_entries:
                            logs.append(f"\n[VM系统日志 - 最近{min(len(vm_syslog_entries), lines)}条]")
                            for entry in vm_syslog_entries[:lines]:
                                time_str = entry.get('time', '')
                                msg = entry.get('msg', '')
                                prio = entry.get('prio', '')
                                logs.append(f"[{time_str}] [{prio}] {msg}")
                        else:
                            logs.append(f"\n[VM系统日志] 暂无VM {vmid} 相关的系统日志")
                    except Exception as e:
                        print(f"[DEBUG] 获取节点syslog失败: {str(e)}")
                        logs.append(f"[VM系统日志] 无法获取: {str(e)}")
                        
            except Exception as e:
                print(f"[DEBUG] 获取{'容器' if vm_type=='lxc' else '虚拟机'}日志失败: {str(e)}")
                return None, f"获取{'容器' if vm_type=='lxc' else '虚拟机'}日志失败: {str(e)}"
        
        # 组合日志内容
        if not logs:
            print(f"[DEBUG] 没有获取到日志记录")
            return "暂无日志记录", None
        
        log_text = '\n'.join(logs)
        print(f"[DEBUG] 日志总长度: {len(log_text)} 字符")
        
        # 截取日志内容（限制长度）
        max_length = 50000
        if len(log_text) > max_length:
            log_text = log_text[-max_length:]
            log_text = "[日志过长，仅显示最近部分]\n..." + log_text
        
        return log_text, None
        
    except Exception as e:
        print(f"[DEBUG] Proxmox获取日志失败: {str(e)}")
        return None, f"Proxmox获取日志失败: {str(e)}"

def get_proxmox_client():
    """创建 Proxmox API 客户端"""
    if not PROXMOX_AVAILABLE:
        return None
    try:
        proxmox = ProxmoxAPI(
            PROXMOX_HOST,
            user=PROXMOX_USER,
            password=PROXMOX_PASSWORD,
            verify_ssl=PROXMOX_VERIFY_SSL
        )
        return proxmox
    except Exception as e:
        print(f"Proxmox 连接失败: {e}")
        return None

def round_to_power_of_2(n):
    """将数字调整为最近的2的幂指数"""
    if n <= 0:
        return 1
    # 找到上下两个2的幂
    lower = 2 ** (n.bit_length() - 1)
    upper = 2 ** n.bit_length()
    # 返回最近的一个
    if n - lower < upper - n:
        return lower
    return upper

def get_vm_list(node_name):
    """从 Proxmox 获取指定节点下的虚拟机列表"""
    proxmox = get_proxmox_client()
    if not proxmox:
        return None, "Proxmox API 不可用"
    
    try:
        vm_list = []
        
        # 获取qemu虚拟机列表
        qemu_vms = proxmox.nodes(node_name).qemu.get()
        for vm in qemu_vms:
            vm_list.append({
                'vmid': vm.get('vmid'),
                'name': vm.get('name', f"VM-{vm.get('vmid')}"),
                'status': vm.get('status', 'unknown'),
                'type': 'qemu'
            })
        
        # 获取LXC容器列表
        lxc_containers = proxmox.nodes(node_name).lxc.get()
        for container in lxc_containers:
            vm_list.append({
                'vmid': container.get('vmid'),
                'name': container.get('name', f"CT-{container.get('vmid')}"),
                'status': container.get('status', 'unknown'),
                'type': 'lxc'
            })
        
        return vm_list, None
    except Exception as e:
        return None, f"获取虚拟机列表失败: {str(e)}"

def get_vm_info(node_name, vmid, vm_type='qemu'):
    """从 Proxmox 获取虚拟机详细信息"""
    proxmox = get_proxmox_client()
    if not proxmox:
        return None, "Proxmox API 不可用"
    
    try:
        # 获取虚拟机配置
        if vm_type == 'lxc':
            config = proxmox.nodes(node_name).lxc(vmid).config.get()
        else:
            config = proxmox.nodes(node_name).qemu(vmid).config.get()
        
        # 解析CPU信息
        cpu_cores = 0
        if 'cores' in config:
            cpu_cores = int(config.get('cores', 1))
        elif 'sockets' in config and 'cores' in config:
            sockets = int(config.get('sockets', 1))
            cores_per_socket = int(config.get('cores', 1))
            cpu_cores = sockets * cores_per_socket
        else:
            cpu_cores = int(config.get('cores', 1))
        
        # 解析内存信息 (单位是MB)
        memory_mb = int(config.get('memory', 512))
        memory_gb = round_to_power_of_2(round(memory_mb / 1024))
        if memory_gb == 0:
            memory_gb = 1
        
        # 解析磁盘信息
        disk_gb = 0
        for key, value in config.items():
            if key.startswith(('scsi', 'sata', 'ide', 'virtio', 'mp', 'rootfs')):
                # 解析类似 "local-lvm:32" 或 "size=32G" 的格式
                if isinstance(value, str):
                    if 'size=' in value:
                        size_str = value.split('size=')[1].split(',')[0]
                        if size_str.endswith('G'):
                            disk_gb += int(size_str[:-1])
                        elif size_str.endswith('M'):
                            disk_gb += round(int(size_str[:-1]) / 1024)
                    elif ':' in value and not value.startswith('file='):
                        parts = value.split(':')
                        if len(parts) >= 2 and parts[1].replace('.', '').isdigit():
                            disk_gb += int(parts[1])
        
        if disk_gb == 0:
            disk_gb = 32  # 默认值
        
        # 获取虚拟机状态以获取IP地址
        ip_address = ""
        try:
            if vm_type == 'qemu':
                # 尝试获取网络接口信息
                agent_info = proxmox.nodes(node_name).qemu(vmid).agent.get('network-get-interfaces')
                if agent_info and 'result' in agent_info:
                    for iface in agent_info['result']:
                        if iface.get('name') == 'lo':
                            continue
                        for addr in iface.get('ip-addresses', []):
                            if addr.get('ip-address-type') == 'ipv4':
                                ip = addr.get('ip-address')
                                if ip and not ip.startswith('127.'):
                                    ip_address = ip
                                    break
                        if ip_address:
                            break
        except:
            pass
        
        # 获取主机名
        hostname = config.get('name', config.get('hostname', f"vm-{vmid}"))
        
        vm_info = {
            'vmid': vmid,
            'hostname': hostname,
            'cpu': f"{cpu_cores}",
            'mem_value': memory_gb,
            'mem_unit': 'GB',
            'disk_value': disk_gb,
            'disk_unit': 'GB',
            'ip': ip_address,
            'type': vm_type,
            'gpu_count': 0  # 虚拟机默认没有直通GPU
        }
        
        return vm_info, None
    except Exception as e:
        return None, f"获取虚拟机信息失败: {str(e)}"

def get_node_info(node_name):
    """从 Proxmox 获取节点信息"""
    proxmox = get_proxmox_client()
    if not proxmox:
        return None, "Proxmox API 不可用"
    
    try:
        # 获取节点列表
        nodes = proxmox.nodes.get()
        target_node = None
        for node in nodes:
            if node['node'] == node_name:
                target_node = node
                break
        
        if not target_node:
            return None, f"找不到节点: {node_name}"
        
        # 获取节点状态
        node_status = proxmox.nodes(node_name).status.get()
        
        # 解析 CPU 信息
        cpu_info = node_status.get('cpuinfo', {})
        cpu_cores = cpu_info.get('cores', 0)
        cpu_sockets = cpu_info.get('sockets', 1)
        total_cores = cpu_cores * cpu_sockets
        
        # 解析内存信息 (从 bytes 转换为 GB，并调整为2的幂)
        memory_total = node_status.get('memory', {}).get('total', 0)
        memory_gb_raw = round(memory_total / (1024**3))
        memory_gb = round_to_power_of_2(memory_gb_raw)
        
        # 获取存储信息 - 尝试获取所有存储的总容量
        disk_gb = 0
        try:
            # 获取节点存储列表
            storage_list = proxmox.nodes(node_name).storage.get()
            for storage in storage_list:
                # 只计算本地存储 (local, local-lvm, zfs等)
                storage_type = storage.get('type', '')
                if storage_type in ['dir', 'lvmthin', 'lvm', 'zfspool', 'btrfs', 'ext4', 'xfs']:
                    total = storage.get('total', 0)
                    if total:
                        disk_gb += round(total / (1024**3))
            
            # 如果没有获取到存储信息，尝试使用 rootfs
            if disk_gb == 0:
                rootfs_total = node_status.get('rootfs', {}).get('total', 0)
                disk_gb = round(rootfs_total / (1024**3))
        except:
            # 回退到 rootfs
            rootfs_total = node_status.get('rootfs', {}).get('total', 0)
            disk_gb = round(rootfs_total / (1024**3))
        
        # 尝试获取 GPU 数量
        gpu_count = 0
        try:
            # 从 PCI 设备列表获取 GPU - 使用更精确的匹配
            pci_devices = proxmox.nodes(node_name).hardware.pci.get()
            
            # GPU 厂商ID映射
            gpu_vendors = {
                '10de': 'NVIDIA',    # NVIDIA
                '1022': 'AMD',       # AMD
                '1002': 'AMD',       # ATI/AMD
                '8086': 'Intel',     # Intel (但通常不是独立GPU)
            }
            
            # GPU 设备类代码 - 更精确的匹配
            # 0300 = VGA compatible controller
            # 0302 = 3D controller
            # 0380 = Display controller
            gpu_class_codes = ['0300', '0302', '0380']
            
            for device in pci_devices:
                device_name = device.get('device_name', '').lower()
                vendor_name = device.get('vendor_name', '').lower()
                class_name = device.get('class_name', '').lower()
                vendor_id = device.get('vendor_id', '').lower()
                class_code = device.get('class', '').lower()
                
                is_gpu = False
                
                # 方法1: 通过设备名称精确匹配（排除集成显卡）
                if 'nvidia' in device_name or 'geforce' in device_name or 'tesla' in device_name or 'quadro' in device_name:
                    is_gpu = True
                elif 'amd' in device_name and ('radeon' in device_name or 'mi' in device_name or 'instinct' in device_name):
                    is_gpu = True
                elif 'ati' in device_name and 'radeon' in device_name:
                    is_gpu = True
                    
                # 方法2: 通过厂商ID + 设备类代码匹配
                if not is_gpu and vendor_id in gpu_vendors:
                    # 检查是否是3D控制器或VGA控制器
                    if any(code in class_code for code in gpu_class_codes):
                        # 排除Intel集成显卡
                        if vendor_id != '8086':
                            is_gpu = True
                        # 对于Intel，只计算独立显卡（如Intel Arc）
                        elif 'arc' in device_name:
                            is_gpu = True
                
                # 方法3: 通过设备类名称匹配 - 但必须包含GPU相关关键词
                if not is_gpu:
                    # 必须是3D控制器
                    if '3d controller' in class_name:
                        # 排除USB控制器、声卡等
                        if any(keyword in device_name for keyword in ['nvidia', 'amd', 'radeon', 'ati', 'matrox']):
                            is_gpu = True
                
                if is_gpu:
                    gpu_count += 1
                    print(f"发现GPU: {device.get('device_name', 'Unknown')} (厂商: {vendor_name}, 类型: {class_name})")
                    
        except Exception as e:
            print(f"获取GPU信息失败: {e}")
            pass
        
        # 尝试获取 IP 地址
        ip_address = ""
        try:
            # 方法1: 尝试从网络接口获取
            network = proxmox.nodes(node_name).network.get()
            for iface in network:
                # 跳过回环接口
                if iface.get('iface') == 'lo':
                    continue
                # 获取IPv4地址
                if 'address' in iface:
                    addr = iface.get('address', '')
                    if addr and not addr.startswith('127.'):
                        ip_address = addr
                        break
                # 尝试从cidr解析
                cidr = iface.get('cidr', '')
                if cidr and '/' in cidr:
                    addr = cidr.split('/')[0]
                    if addr and not addr.startswith('127.'):
                        ip_address = addr
                        break
        except:
            pass
        
        # 如果还没有获取到IP，尝试通过DNS解析主机名
        if not ip_address:
            try:
                import socket
                ip_address = socket.gethostbyname(node_name)
                # 如果是回环地址，清空
                if ip_address.startswith('127.'):
                    ip_address = ""
            except:
                pass
        
        node_info = {
            'hostname': target_node['node'],
            'cpu': f"{total_cores}",
            'mem_value': memory_gb,
            'mem_unit': 'GB',
            'disk_value': disk_gb,
            'disk_unit': 'GB',
            'ip': ip_address,
            'gpu_count': gpu_count
        }
        
        return node_info, None
    except Exception as e:
        return None, f"获取节点信息失败: {str(e)}"

CSS = """
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #f5f7fa; min-height: 100vh; }
.header { background: linear-gradient(135deg, #2196F3 0%, #764ba2 100%); color: white; padding: 20px 40px; display: flex; justify-content: space-between; align-items: center; }
.header h1 { font-size: 24px; }
.user-menu { position: relative; display: inline-block; }
.user-menu-trigger { display: flex; align-items: center; gap: 8px; cursor: pointer; padding: 8px 16px; background: transparent; border-radius: 8px; transition: all 0.3s; }
.user-menu-trigger:hover { background: transparent; }
.user-menu-dropdown { display: none; position: absolute; right: 0; top: 100%; margin-top: 8px; background: white; border-radius: 8px; box-shadow: 0 4px 20px rgba(0,0,0,0.15); min-width: 150px; z-index: 1000; overflow: hidden; }
.user-menu-dropdown.active { display: block; }
.user-menu-item { display: block; padding: 12px 16px; color: #333; text-decoration: none; font-size: 14px; transition: all 0.2s; border-bottom: 1px solid #f0f0f0; }
.user-menu-item:last-child { border-bottom: none; }
.user-menu-item:hover { background: #f5f5f5; color: #667eea; }
.user-menu-item.admin-only { background: #e3f2fd; color: #1976d2; }
.user-menu-item.admin-only:hover { background: #bbdefb; }
.container { max-width: 1400px; margin: 0 auto; padding: 30px 40px; }
.btn-add { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; border: none; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-size: 13px; font-weight: 600; margin-right: 8px; }
.btn-add:hover { transform: translateY(-1px); box-shadow: 0 4px 15px rgba(102, 126, 234, 0.4); }
.btn-batch { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; border: none; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-size: 13px; font-weight: 600; margin-right: 8px; }
.btn-batch:hover { transform: translateY(-1px); box-shadow: 0 4px 15px rgba(102, 126, 234, 0.4); }
.btn-batch-delete { background: #f44336; color: white; border: none; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-size: 13px; font-weight: 600; margin-right: 8px; }
.btn-batch-delete:hover { background: #d32f2f; }
.btn-add-vm { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; border: none; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-size: 13px; font-weight: 600; margin-right: 8px; }
.btn-add-vm:hover { transform: translateY(-1px); box-shadow: 0 4px 15px rgba(102, 126, 234, 0.4); }
.btn-add-vm:disabled { background: #ccc; cursor: not-allowed; }
.btn-batch:disabled, .btn-batch-delete:disabled { background: #ccc; cursor: not-allowed; }
.vm-list { max-height: 400px; overflow-y: auto; }
.vm-item { padding: 15px; border: 2px solid #e0e0e0; border-radius: 10px; margin-bottom: 10px; cursor: pointer; transition: all 0.3s ease; }
.vm-item:hover { border-color: #667eea; background: #f5f7fa; }
.vm-item.selected { border-color: #4caf50; background: #e8f5e9; }
.vm-item.disabled { opacity: 0.5; cursor: not-allowed; background: #f5f5f5; }
.vm-item.disabled:hover { border-color: #e0e0e0; background: #f5f5f5; }
.vm-name { font-weight: 600; color: #333; margin-bottom: 5px; }
.vm-id { font-size: 12px; color: #888; }
.vm-status { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 12px; margin-left: 10px; }
.vm-status-running { background: #e8f5e9; color: #2e7d32; }
.vm-status-stopped { background: #ffebee; color: #c62828; }
.vm-added-badge { background: #9e9e9e; color: white; padding: 2px 8px; border-radius: 4px; font-size: 12px; margin-left: 10px; }
.table-container { background: white; border-radius: 15px; box-shadow: 0 4px 20px rgba(0, 0, 0, 0.08); overflow: hidden; }
table { width: 100%; border-collapse: collapse; }
th { padding: 18px 20px; text-align: left; font-weight: 600; color: #555; font-size: 14px; background: #f8f9fa; border-bottom: 2px solid #e9ecef; }
td { padding: 18px 20px; border-bottom: 1px solid #e9ecef; font-size: 14px; color: #444; }
tr:hover { background: #f8f9fa; }
tr.selected { background: #e3f2fd; }
.hostname { font-weight: 600; color: #667eea; }
.type-badge { display: inline-block; padding: 6px 14px; border-radius: 20px; font-size: 12px; font-weight: 600; }
.type-physical { background: #e3f2fd; color: #1976d2; }
.type-virtual { background: #f3e5f5; color: #7b1fa2; }
.gpu-tag { background: #e8f5e9; color: #2e7d32; padding: 2px 6px; border-radius: 4px; font-size: 10px; font-weight: 600; margin-right: 4px; margin-bottom: 3px; display: inline-block; white-space: nowrap; }
.vm-child { display: none; }
.vm-child.visible { display: table-row; }
.vm-child .hostname { padding-left: 4em !important; }  /* 缩进四格 */
.expand-col { width: 24px; text-align: center; padding: 0; }
.expand-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 20px;
    height: 20px;
    cursor: pointer;
    color: #666;
    font-size: 12px;
    user-select: none;
    transition: transform 0.2s ease;
}
.expand-btn.expanded { transform: rotate(90deg); }
.expand-btn:hover { color: #667eea; }
.physical-row { cursor: pointer; }
.physical-row:hover { background: #f5f7fa; }
/* 用途详情按钮和小窗样式 */
.detail-trigger {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    margin-left: 6px;
    padding: 0 4px;
    color: #667eea;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
    border-radius: 3px;
    transition: all 0.2s ease;
}
.detail-trigger:hover {
    background: #e8eaf6;
    color: #3949ab;
}
.detail-popup {
    display: none;
    position: fixed;
    background: white;
    border: 1px solid #e0e0e0;
    border-radius: 8px;
    padding: 12px 16px;
    max-width: 450px;
    max-height: 350px;
    overflow-y: auto;
    box-shadow: 0 4px 20px rgba(0,0,0,0.15);
    z-index: 1000;
    font-size: 13px;
    color: #555;
    line-height: 1.6;
    white-space: pre-wrap;
    word-wrap: break-word;
    font-family: 'Consolas', 'Monaco', 'Courier New', 'Microsoft YaHei', monospace;
}
.detail-popup.visible {
    display: block;
}
.detail-popup::before {
    content: '';
    position: absolute;
    top: -6px;
    left: 20px;
    width: 10px;
    height: 10px;
    background: white;
    border-left: 1px solid #e0e0e0;
    border-top: 1px solid #e0e0e0;
    transform: rotate(45deg);
}
.modal { display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.5); justify-content: center; align-items: center; z-index: 1000; backdrop-filter: blur(5px); }
.modal.active { display: flex; }
.modal-content { background: white; border-radius: 20px; width: 90%; max-width: 600px; max-height: 90vh; overflow-y: auto; box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3); }
.modal-header { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 25px 30px; display: flex; justify-content: space-between; align-items: center; }
.modal-body { padding: 30px; }
.form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 20px; }
.form-group { margin-bottom: 20px; }
label { display: block; margin-bottom: 8px; color: #555; font-weight: 500; font-size: 14px; }
input, select, textarea { width: 100%; padding: 14px; border: 2px solid #e0e0e0; border-radius: 10px; font-size: 14px; transition: all 0.3s ease; }
input:focus, select:focus, textarea:focus { outline: none; border-color: #667eea; box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1); }
textarea { resize: vertical; min-height: 80px; }
.gpu-selection { display: grid; grid-template-columns: repeat(8, 1fr); gap: 10px; margin-top: 10px; }
.gpu-checkbox { position: relative; }
.gpu-checkbox input { position: absolute; opacity: 0; }
.gpu-label { display: block; padding: 12px 5px; background: #f5f5f5; border: 2px solid #e0e0e0; border-radius: 8px; text-align: center; cursor: pointer; font-size: 11px; font-weight: 600; transition: all 0.3s ease; }
.gpu-checkbox input:checked + .gpu-label { background: #e8f5e9; border-color: #4caf50; color: #2e7d32; }
.gpu-checkbox input:disabled + .gpu-label { background: #eeeeee; border-color: #bdbdbd; color: #9e9e9e; cursor: not-allowed; opacity: 0.6; }
.modal-footer { padding: 20px 30px; border-top: 1px solid #e9ecef; display: flex; justify-content: flex-end; gap: 15px; }
.btn-cancel { padding: 12px 24px; background: #f5f5f5; color: #555; border: none; border-radius: 8px; cursor: pointer; font-size: 14px; font-weight: 600; }
.btn-cancel:hover { background: #e0e0e0; }
.btn-save { padding: 12px 30px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; border: none; border-radius: 8px; cursor: pointer; font-size: 14px; font-weight: 600; }
.btn-save:hover { transform: translateY(-2px); box-shadow: 0 8px 20px rgba(102, 126, 234, 0.3); }
.btn-verify { padding: 12px 24px; background: linear-gradient(135deg, #4caf50 0%, #388e3c 100%); color: white; border: none; border-radius: 8px; cursor: pointer; font-size: 14px; font-weight: 600; }
.btn-verify:hover { transform: translateY(-2px); box-shadow: 0 8px 20px rgba(76, 175, 80, 0.3); }
.btn-verify:disabled { background: #cccccc; cursor: not-allowed; transform: none; box-shadow: none; }
.verify-status { margin-left: 10px; font-size: 14px; font-weight: 600; }
.verify-status.success { color: #4caf50; }
.verify-status.error { color: #f44336; }
.empty-state { text-align: center; padding: 80px 40px; color: #888; }
.stats { display: flex; gap: 20px; margin-bottom: 25px; }
.stat-card { background: white; padding: 15px 25px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0, 0, 0, 0.05); }
.stat-label { font-size: 12px; color: #888; margin-bottom: 5px; }
.stat-value { font-size: 24px; font-weight: 700; color: #333; }

/* 性能监控面板样式 */
.performance-panel { background: white; padding: 25px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0, 0, 0, 0.05); margin-bottom: 25px; display: none; }
.performance-panel.active { display: block; }
.tab-label { display: none; padding: 12px 30px; background: linear-gradient(135deg, #2196F3 0%, #1976D2 100%); color: white; border-radius: 10px 10px 0 0; font-size: 18px; font-weight: 600; margin-bottom: 0; }
.tab-label.active { display: inline-block; }
.tab-label.inventory-tab { display: inline-block; }
.performance-header { display: flex; align-items: center; margin-bottom: 20px; padding-bottom: 15px; border-bottom: 1px solid #eee; }
.performance-hostname { font-size: 20px; font-weight: 700; color: #333; margin-right: 15px; }
.performance-status { padding: 5px 12px; border-radius: 15px; font-size: 12px; font-weight: 600; }
.performance-status.running { background: #e8f5e9; color: #2e7d32; }
.performance-status.stopped { background: #ffebee; color: #c62828; }
.performance-actions { margin-left: auto; display: flex; gap: 10px; }
.btn-refresh, .btn-clear { padding: 6px 14px; border: none; border-radius: 6px; font-size: 12px; font-weight: 600; cursor: pointer; transition: all 0.2s ease; }
.btn-refresh { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; }
.btn-refresh:hover { transform: translateY(-1px); box-shadow: 0 4px 12px rgba(102, 126, 234, 0.3); }
.btn-clear { background: #f5f5f5; color: #666; }
.btn-clear:hover { background: #e0e0e0; }
.btn-ai-diagnosis { padding: 8px 18px; border: none; border-radius: 6px; font-size: 13px; font-weight: 700; cursor: pointer; transition: all 0.2s ease; background: linear-gradient(135deg, #ff6b6b 0%, #ee5a24 100%); color: white; box-shadow: 0 2px 8px rgba(255, 107, 107, 0.3); }
.btn-ai-diagnosis:hover { transform: translateY(-1px); box-shadow: 0 4px 15px rgba(255, 107, 107, 0.4); }
.btn-ai-diagnosis:disabled { background: #ccc; cursor: not-allowed; box-shadow: none; }
.btn-separator { width: 1px; height: 24px; background: #ddd; margin: 0 8px; }
.ai-diagnosis-dialog { margin-top: 25px; padding: 20px; background: linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%); border-radius: 12px; border-left: 4px solid #ff6b6b; display: none; }
.ai-diagnosis-dialog.active { display: block; }
.ai-diagnosis-header { display: flex; align-items: center; margin-bottom: 15px; }
.ai-diagnosis-icon { width: 32px; height: 32px; background: linear-gradient(135deg, #ff6b6b 0%, #ee5a24 100%); border-radius: 50%; display: flex; align-items: center; justify-content: center; color: white; font-size: 16px; margin-right: 12px; }
.ai-diagnosis-title { font-size: 16px; font-weight: 700; color: #333; }
.ai-diagnosis-collapse { margin-left: auto; font-size: 13px; color: #888; cursor: pointer; transition: color 0.2s ease; user-select: none; }
.ai-diagnosis-collapse:hover { color: #ff6b6b; }
.ai-diagnosis-content { font-size: 14px; line-height: 1.8; color: #555; min-height: 60px; max-height: calc(14px * 1.8 * 10); overflow-y: auto; }
.ai-diagnosis-content .typing-cursor { display: inline-block; width: 2px; height: 16px; background: #ff6b6b; animation: blink 1s infinite; margin-left: 2px; }
@keyframes blink { 0%, 50% { opacity: 1; } 51%, 100% { opacity: 0; } }
.performance-charts { display: flex; justify-content: space-around; align-items: center; flex-wrap: wrap; gap: 30px; }
/* 模型管理页面样式 */
.models-container { max-width: 1000px; margin: 0 auto; padding: 30px; }
.models-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 30px; }
.models-header h2 { color: #333; font-size: 24px; }
.model-card { background: white; border-radius: 12px; padding: 20px; margin-bottom: 15px; box-shadow: 0 2px 10px rgba(0,0,0,0.08); display: flex; align-items: center; gap: 15px; transition: all 0.3s; }
.model-card:hover { box-shadow: 0 4px 20px rgba(0,0,0,0.12); }
.model-card.selected { border: 2px solid #667eea; background: #f8f9ff; }
.model-radio { width: 20px; height: 20px; cursor: pointer; }
.model-info { flex: 1; }
.model-name { font-weight: 600; font-size: 16px; color: #333; margin-bottom: 4px; }
.model-details { font-size: 13px; color: #666; }
.model-type { display: inline-block; padding: 4px 10px; border-radius: 4px; font-size: 12px; font-weight: 500; margin-left: 10px; }
.model-type-public { background: #e3f2fd; color: #1976d2; }
.model-type-local { background: #e8f5e9; color: #2e7d32; }
.model-actions { display: flex; gap: 10px; }
.btn-model-test { background: #4caf50; color: white; border: none; padding: 6px 12px; border-radius: 4px; cursor: pointer; font-size: 12px; }
.btn-model-test:hover { background: #45a049; }
.btn-model-delete { background: #f44336; color: white; border: none; padding: 6px 12px; border-radius: 4px; cursor: pointer; font-size: 12px; }
.btn-model-delete:hover { background: #d32f2f; }
.add-model-section { background: white; border-radius: 12px; padding: 25px; margin-top: 30px; box-shadow: 0 2px 10px rgba(0,0,0,0.08); }
.add-model-section h3 { margin-bottom: 20px; color: #333; }
.model-form-row { display: flex; gap: 20px; margin-bottom: 15px; flex-wrap: wrap; }
.model-form-group { flex: 1; min-width: 200px; }
.model-form-group label { display: block; margin-bottom: 6px; font-size: 14px; color: #555; font-weight: 500; }
.model-form-group input, .model-form-group select { width: 100%; padding: 10px 12px; border: 1px solid #ddd; border-radius: 6px; font-size: 14px; }
.model-form-group input:focus, .model-form-group select:focus { outline: none; border-color: #667eea; }
.btn-add-model { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; border: none; padding: 12px 24px; border-radius: 6px; cursor: pointer; font-size: 14px; font-weight: 600; }
.btn-add-model:hover { transform: translateY(-1px); box-shadow: 0 4px 15px rgba(102, 126, 234, 0.4); }
.model-tabs { display: flex; gap: 10px; margin-bottom: 20px; }
.model-tab { padding: 10px 20px; border: none; background: #f0f0f0; border-radius: 6px; cursor: pointer; font-size: 14px; }
.model-tab.active { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; }
.back-link { color: #667eea; text-decoration: none; font-size: 14px; display: inline-flex; align-items: center; gap: 5px; margin-bottom: 20px; }
.back-link:hover { text-decoration: underline; }
.chart-container { display: flex; flex-direction: column; align-items: center; }
.chart-label { margin-top: 10px; font-size: 14px; color: #666; font-weight: 500; }
.chart-value { margin-top: 5px; font-size: 12px; color: #888; }

/* SVG饼图样式 */
.pie-chart { width: 120px; height: 120px; }
.pie-chart circle { fill: none; stroke-width: 20; }
.pie-chart .pie-bg { stroke: #e9ecef; }
.pie-chart .pie-fill { stroke-linecap: round; transition: stroke-dasharray 0.5s ease; }
.pie-chart.cpu .pie-fill { stroke: #667eea; }
.pie-chart.mem .pie-fill { stroke: #764ba2; }
.pie-chart.disk .pie-fill { stroke: #4caf50; }

.toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 5px; flex-wrap: wrap; gap: 8px; }
.batch-actions { display: none; }
.batch-actions.active { display: flex; }
.checkbox-col { width: 50px; text-align: center; }
input[type="checkbox"] { width: 20px; height: 20px; cursor: pointer; }
.login-body { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); display: flex; justify-content: center; align-items: center; min-height: 100vh; }
.login-container { background: white; padding: 50px; border-radius: 20px; box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3); width: 100%; max-width: 420px; text-align: center; }
.login-container h1 { color: #333; margin-bottom: 10px; font-size: 28px; }
.login-subtitle { color: #666; margin-bottom: 35px; font-size: 14px; }
.login-form-group { margin-bottom: 25px; text-align: left; }
.login-form-group label { display: block; margin-bottom: 8px; color: #555; font-weight: 500; }
.login-form-group input, .login-form-group select { width: 100%; padding: 15px; border: 2px solid #e0e0e0; border-radius: 10px; font-size: 15px; }
.login-form-group input:focus, .login-form-group select:focus { outline: none; border-color: #667eea; }
.btn-login { width: 100%; padding: 16px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; border: none; border-radius: 10px; font-size: 16px; font-weight: 600; cursor: pointer; margin-top: 10px; transition: all 0.3s ease; }
.btn-login:hover { transform: translateY(-2px); box-shadow: 0 10px 30px rgba(102, 126, 234, 0.4); }
.error { background: #ffebee; color: #c62828; padding: 12px; border-radius: 8px; margin-bottom: 20px; font-size: 14px; }
.success { background: #e8f5e9; color: #2e7d32; padding: 12px; border-radius: 8px; margin-bottom: 20px; font-size: 14px; }
.hint { margin-top: 20px; color: #999; font-size: 12px; }
.switch-mode { margin-top: 20px; color: #666; font-size: 14px; }
.switch-mode a { color: #667eea; text-decoration: none; font-weight: 600; cursor: pointer; }
.switch-mode a:hover { text-decoration: underline; }
.role-badge { display: inline-block; padding: 4px 12px; border-radius: 12px; font-size: 12px; font-weight: 600; margin-left: 10px; }
.role-admin { background: #e3f2fd; color: #1976d2; }
.role-viewer { background: #fff3e0; color: #f57c00; }
.viewer-notice { background: #fff3e0; border-left: 4px solid #ff9800; padding: 15px 20px; margin-bottom: 20px; border-radius: 0 8px 8px 0; }
.viewer-notice p { color: #e65100; margin: 0; font-size: 14px; }
.btn-close { background: rgba(255, 255, 255, 0.2); border: none; color: white; width: 36px; height: 36px; border-radius: 50%; cursor: pointer; font-size: 20px; display: flex; align-items: center; justify-content: center; transition: all 0.3s ease; }
.btn-close:hover { background: rgba(255, 255, 255, 0.3); }
"""

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        print(format % args)

    def get_session(self):
        cookie = self.headers.get('Cookie', '')
        for part in cookie.split(';'):
            if 'session=' in part:
                sid = part.split('=')[1].strip()
                return sessions.get(sid)
        return None

    def send_html(self, html, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(html.encode('utf-8'))

    def send_json(self, data, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        session = self.get_session()

        if path in ('/', '/login'):
            if session:
                self.send_response(302)
                self.send_header('Location', '/dashboard')
                self.end_headers()
            else:
                self.render_login('', '')

        elif path == '/register':
            if session:
                self.send_response(302)
                self.send_header('Location', '/dashboard')
                self.end_headers()
            else:
                self.render_register('', '')

        elif path == '/dashboard':
            if not session:
                self.send_response(302)
                self.send_header('Location', '/login')
                self.end_headers()
            else:
                self.render_dashboard(session)

        elif path == '/models':
            # 模型管理页面（仅admin）
            if not session:
                self.send_response(302)
                self.send_header('Location', '/login')
                self.end_headers()
            elif session.get('role') != 'admin':
                self.send_response(403)
                self.send_html('<h1>Forbidden</h1><p>只有管理员可以访问模型管理。</p><a href="/dashboard">返回</a>')
            else:
                self.render_models_page(session)

        elif path == '/logout':
            cookie = self.headers.get('Cookie', '')
            for part in cookie.split(';'):
                if 'session=' in part:
                    sid = part.split('=')[1].strip()
                    sessions.pop(sid, None)
            self.send_response(302)
            self.send_header('Location', '/login')
            self.end_headers()

        elif path == '/delete':
            if not session:
                self.send_response(302)
                self.send_header('Location', '/login')
                self.end_headers()
            elif session.get('role') != 'admin':
                self.send_response(403)
                self.send_html('<h1>Forbidden</h1><p>Viewer cannot delete servers.</p><a href="/dashboard">Back</a>')
            else:
                query = urllib.parse.parse_qs(parsed.query)
                if 'id' in query:
                    sid = int(query['id'][0])
                    data = load_data()
                    data['servers'] = [s for s in data['servers'] if s['id'] != sid]
                    save_data(data)
                self.send_response(302)
                self.send_header('Location', '/dashboard')
                self.end_headers()
        
        elif path == '/edit':
            if not session:
                self.send_response(302)
                self.send_header('Location', '/login')
                self.end_headers()
            elif session.get('role') != 'admin':
                self.send_response(403)
                self.send_html('<h1>Forbidden</h1><p>Viewer cannot edit servers.</p><a href="/dashboard">Back</a>')
            else:
                query = urllib.parse.parse_qs(parsed.query)
                if 'id' in query:
                    sid = int(query['id'][0])
                    self.render_edit_form(sid)
                else:
                    self.send_response(302)
                    self.send_header('Location', '/dashboard')
                    self.end_headers()
        
        elif path == '/api/node_info':
            # API: 获取 Proxmox 节点信息
            if not session:
                self.send_json({'success': False, 'error': '未登录'})
            elif session.get('role') != 'admin':
                self.send_json({'success': False, 'error': '无权限'})
            else:
                query = urllib.parse.parse_qs(parsed.query)
                node_name = query.get('node', [''])[0]
                if not node_name:
                    self.send_json({'success': False, 'error': '请输入节点名'})
                else:
                    node_info, error = get_node_info(node_name)
                    if error:
                        self.send_json({'success': False, 'error': error})
                    else:
                        self.send_json({'success': True, 'info': node_info})
        
        elif path == '/api/vm_list':
            # API: 获取指定节点下的虚拟机列表
            if not session:
                self.send_json({'success': False, 'error': '未登录'})
            elif session.get('role') != 'admin':
                self.send_json({'success': False, 'error': '无权限'})
            else:
                query = urllib.parse.parse_qs(parsed.query)
                node_name = query.get('node', [''])[0]
                if not node_name:
                    self.send_json({'success': False, 'error': '请输入节点名'})
                else:
                    vm_list, error = get_vm_list(node_name)
                    if error:
                        self.send_json({'success': False, 'error': error})
                    else:
                        # 获取已添加的虚拟机ID列表
                        data = load_data()
                        added_vmids = [s.get('proxmox_vmid') for s in data['servers'] if s.get('proxmox_vmid')]
                        self.send_json({'success': True, 'vms': vm_list, 'added_vmids': added_vmids})
        
        elif path == '/api/host_gpu_info':
            # API: 获取物理机的GPU信息（总数和已分配）
            if not session:
                self.send_json({'success': False, 'error': '未登录'})
            elif session.get('role') != 'admin':
                self.send_json({'success': False, 'error': '无权限'})
            else:
                query = urllib.parse.parse_qs(parsed.query)
                hostname = query.get('hostname', [''])[0]
                if not hostname:
                    self.send_json({'success': False, 'error': '缺少主机名参数'})
                else:
                    physical_server = get_physical_server_by_hostname(hostname)
                    if not physical_server:
                        self.send_json({'success': False, 'error': f'找不到物理机: {hostname}'})
                    else:
                        gpu_count = physical_server.get('gpu_count', 0)
                        used_gpus = get_used_gpus_by_host(hostname)
                        self.send_json({
                            'success': True, 
                            'gpu_count': gpu_count,
                            'used_gpus': list(used_gpus)
                        })
        
        elif path == '/api/verify_ssh':
            # API: 验证SSH连接
            if not session:
                self.send_json({'success': False, 'error': '未登录'})
            elif session.get('role') != 'admin':
                self.send_json({'success': False, 'error': '无权限'})
            else:
                query = urllib.parse.parse_qs(parsed.query)
                ip = query.get('ip', [''])[0]
                username = query.get('username', [''])[0]
                password = query.get('password', [''])[0]
                if not ip or not username or not password:
                    self.send_json({'success': False, 'error': '缺少必要参数(IP、用户名或密码)'})
                else:
                    success, error = verify_ssh_connection(ip, username, password)
                    if success:
                        self.send_json({'success': True, 'message': 'SSH连接验证成功'})
                    else:
                        self.send_json({'success': False, 'error': error})
        
        elif path == '/api/vm_info':
            # API: 获取虚拟机详细信息
            if not session:
                self.send_json({'success': False, 'error': '未登录'})
            elif session.get('role') != 'admin':
                self.send_json({'success': False, 'error': '无权限'})
            else:
                query = urllib.parse.parse_qs(parsed.query)
                node_name = query.get('node', [''])[0]
                vmid = query.get('vmid', [''])[0]
                vm_type = query.get('type', ['qemu'])[0]
                if not node_name or not vmid:
                    self.send_json({'success': False, 'error': '缺少参数'})
                else:
                    vm_info, error = get_vm_info(node_name, vmid, vm_type)
                    if error:
                        self.send_json({'success': False, 'error': error})
                    else:
                        self.send_json({'success': True, 'info': vm_info})
        
        elif path == '/api/performance':
            # API: 获取服务器性能数据
            if not session:
                self.send_json({'success': False, 'error': '未登录'})
            else:
                query = urllib.parse.parse_qs(parsed.query)
                server_id = query.get('id', [''])[0]
                if not server_id:
                    self.send_json({'success': False, 'error': '缺少服务器ID'})
                else:
                    try:
                        server_id = int(server_id)
                    except ValueError:
                        self.send_json({'success': False, 'error': '无效的服务器ID'})
                        return
                    
                    data = load_data()
                    server = None
                    for s in data['servers']:
                        if s['id'] == server_id:
                            server = s
                            break
                    
                    if not server:
                        self.send_json({'success': False, 'error': '服务器不存在'})
                        return
                    
                    # 判断注册方式
                    reg_type = server.get('reg_type', 'manual')
                    is_auto = reg_type == 'auto' or server.get('proxmox_vmid') is not None
                    
                    perf_data = None
                    error = None
                    
                    if is_auto:
                        # 自动注册：通过Proxmox API获取
                        hostname = server['hostname']
                        if server['type'] == 'physical':
                            # 物理机
                            perf_data, error = get_proxmox_node_performance(hostname)
                        else:
                            # 虚拟机
                            vmid = server.get('proxmox_vmid')
                            if vmid:
                                # 通过parent_host找到节点名
                                node_name = server.get('parent_host', '')
                                if node_name:
                                    perf_data, error = get_proxmox_vm_performance(node_name, vmid, 'qemu')
                                else:
                                    error = '无法确定虚拟机所在节点'
                            else:
                                error = '虚拟机没有proxmox_vmid'
                    else:
                        # 手动注册：通过SSH获取
                        # 从服务器数据中获取SSH凭据
                        ssh_user = server.get('ssh_user')
                        ssh_password = server.get('ssh_password')
                        
                        if not ssh_user or not ssh_password:
                            # 如果没有存储凭据，返回错误
                            error = '没有存储SSH凭据，无法获取性能数据'
                        else:
                            ip = server['ip']
                            perf_data, error = get_ssh_performance(ip, ssh_user, ssh_password)
                    
                    if error or perf_data is None:
                        self.send_json({'success': False, 'error': error or '获取性能数据失败'})
                    else:
                        # 添加服务器基本信息
                        print(f"性能数据返回: {perf_data}")  # 调试输出
                        result = {
                            'success': True,
                            'hostname': server['hostname'],
                            'type': server['type'],
                            'status': perf_data.get('status', 'running'),
                            'performance': perf_data
                        }
                        self.send_json(result)
        
        elif path == '/api/server_logs':
            # API: 获取服务器日志（用于AI诊断）
            if not session:
                self.send_json({'success': False, 'error': '未登录'})
            else:
                query = urllib.parse.parse_qs(parsed.query)
                server_id = query.get('id', [''])[0]
                if not server_id:
                    self.send_json({'success': False, 'error': '缺少服务器ID'})
                else:
                    try:
                        server_id = int(server_id)
                    except ValueError:
                        self.send_json({'success': False, 'error': '无效的服务器ID'})
                        return
                    
                    data = load_data()
                    server = None
                    for s in data['servers']:
                        if s['id'] == server_id:
                            server = s
                            break
                    
                    if not server:
                        self.send_json({'success': False, 'error': '服务器不存在'})
                        return
                    
                    reg_type = server.get('reg_type', 'manual')
                    is_auto = reg_type == 'auto' or server.get('proxmox_vmid') is not None
                    
                    if is_auto:
                        # 自动注册的服务器：通过Proxmox API获取日志
                        node_name = server.get('hostname', '')
                        proxmox_vmid = server.get('proxmox_vmid')
                        server_type = server.get('type', 'physical')
                        
                        # 对于虚拟机，需要通过映射找到其所在的物理节点
                        if server_type == 'virtual' and proxmox_vmid:
                            # 获取VM到节点的映射
                            vm_mapping = get_proxmox_vm_node_mapping()
                            node_name = vm_mapping.get(str(proxmox_vmid), node_name)
                            vm_type = 'lxc' if 'lxc' in str(server.get('proxmox_type', '')).lower() else 'qemu'
                            logs, error = get_proxmox_logs(node_name, proxmox_vmid, vm_type, lines=100)
                        else:
                            # 物理节点日志
                            logs, error = get_proxmox_logs(node_name, None, 'qemu', lines=100)
                        
                        if error:
                            self.send_json({'success': False, 'error': error})
                        else:
                            self.send_json({
                                'success': True,
                                'logs': logs,
                                'hostname': server['hostname']
                            })
                    else:
                        # 手动注册：通过SSH获取日志
                        ssh_user = server.get('ssh_user')
                        ssh_password = server.get('ssh_password')
                        
                        if not ssh_user or not ssh_password:
                            self.send_json({'success': False, 'error': '没有存储SSH凭据，无法获取日志'})
                            return
                        
                        ip = server['ip']
                        logs, error = get_ssh_logs(ip, ssh_user, ssh_password, lines=100)
                        
                        if error:
                            self.send_json({'success': False, 'error': error})
                        else:
                            self.send_json({
                                'success': True,
                                'logs': logs,
                                'hostname': server['hostname']
                            })
        
        elif path == '/api/ai_diagnosis':
            # API: AI 流式诊断
            if not session:
                self.send_json({'success': False, 'error': '未登录'})
            else:
                query = urllib.parse.parse_qs(parsed.query)
                server_id = query.get('id', [''])[0]
                if not server_id:
                    self.send_json({'success': False, 'error': '缺少服务器ID'})
                else:
                    try:
                        server_id = int(server_id)
                    except ValueError:
                        self.send_json({'success': False, 'error': '无效的服务器ID'})
                        return
                    
                    data = load_data()
                    server = None
                    for s in data['servers']:
                        if s['id'] == server_id:
                            server = s
                            break
                    
                    if not server:
                        self.send_json({'success': False, 'error': '服务器不存在'})
                        return
                    
                    # 获取性能数据
                    reg_type = server.get('reg_type', 'manual')
                    is_auto = reg_type == 'auto' or server.get('proxmox_vmid') is not None
                    
                    perf_data = None
                    logs = None
                    error = None
                    
                    if is_auto:
                        # 自动注册：通过Proxmox API获取
                        hostname = server['hostname']
                        proxmox_vmid = server.get('proxmox_vmid')
                        
                        if server['type'] == 'physical':
                            perf_data, error = get_proxmox_node_performance(hostname)
                            # 物理节点日志
                            logs, log_error = get_proxmox_logs(hostname, None, 'qemu', lines=100)
                            if log_error:
                                logs = f"获取日志失败: {log_error}"
                        else:
                            # 虚拟机
                            if proxmox_vmid:
                                node_name = server.get('parent_host', '')
                                if node_name:
                                    perf_data, error = get_proxmox_vm_performance(node_name, proxmox_vmid, 'qemu')
                                    # 获取虚拟机日志
                                    vm_type = 'lxc' if 'lxc' in str(server.get('proxmox_type', '')).lower() else 'qemu'
                                    logs, log_error = get_proxmox_logs(node_name, proxmox_vmid, vm_type, lines=100)
                                    if log_error:
                                        logs = f"获取日志失败: {log_error}"
                                else:
                                    error = '无法确定虚拟机所在节点'
                                    logs = None
                            else:
                                error = '虚拟机没有proxmox_vmid'
                                logs = None
                    else:
                        # 手动注册：通过SSH获取
                        ssh_user = server.get('ssh_user')
                        ssh_password = server.get('ssh_password')
                        
                        if not ssh_user or not ssh_password:
                            error = '没有存储SSH凭据'
                        else:
                            ip = server['ip']
                            perf_data, perf_error = get_ssh_performance(ip, ssh_user, ssh_password)
                            if perf_error:
                                error = perf_error
                            else:
                                logs, log_error = get_ssh_logs(ip, ssh_user, ssh_password, lines=100)
                                if log_error:
                                    logs = f"获取日志失败: {log_error}"
                    
                    if error:
                        self.send_json({'success': False, 'error': error})
                        return
                    
                    # 构建服务器信息
                    server_info = {
                        'hostname': server['hostname'],
                        'ip': server['ip'],
                        'type': server['type']
                    }
                    
                    # 构建提示词
                    prompt = build_diagnosis_prompt(perf_data or {}, logs or "", server_info)
                    
                    # 设置流式响应头
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/plain; charset=utf-8')
                    self.send_header('Cache-Control', 'no-cache')
                    self.send_header('X-Accel-Buffering', 'no')
                    self.end_headers()
                    
                    # 调用 DeepSeek API 并流式输出
                    messages = [
                        {"role": "system", "content": "你是一位专业的服务器运维专家，擅长分析系统性能和日志。**无论输入是什么语言，你必须始终用简体中文（Chinese）回复**。请用中文给出专业但易懂的诊断报告。"},
                        {"role": "user", "content": prompt}
                    ]
                    
                    try:
                        for chunk in deepseek_chat_stream(messages):
                            self.wfile.write(chunk.encode('utf-8'))
                            self.wfile.flush()
                    except Exception as e:
                        self.wfile.write(f"\n[流式输出中断: {str(e)}]".encode('utf-8'))
        
        elif path == '/api/models':
            # API: 获取模型列表
            if not session:
                self.send_json({'success': False, 'error': '未登录'})
            else:
                config = load_config()
                models = config.get('models', [])
                current = config.get('current_model', '')
                self.send_json({'success': True, 'models': models, 'current_model': current})
        
        else:
            self.send_html('<h1>404</h1>', 404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        session = self.get_session()

        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length).decode('utf-8')
        form = urllib.parse.parse_qs(body)

        if path == '/login':
            user = form.get('username', [''])[0].strip()
            pwd = form.get('password', [''])[0].strip()
            users = load_users()
            
            if user in users and users[user]['password'] == pwd:
                sid = str(uuid.uuid4())
                sessions[sid] = {'username': user, 'role': users[user]['role']}
                self.send_response(302)
                self.send_header('Location', '/dashboard')
                self.send_header('Set-Cookie', f'session={sid}; Path=/')
                self.end_headers()
            else:
                self.render_login('<div class="error">用户名或密码错误</div>', '')

        elif path == '/register':
            user = form.get('username', [''])[0].strip()
            pwd = form.get('password', [''])[0].strip()
            role = form.get('role', ['viewer'])[0]
            users = load_users()
            
            if not user or not pwd:
                self.render_register('<div class="error">用户名和密码不能为空</div>', '')
            elif user in users:
                self.render_register('<div class="error">用户名已存在</div>', '')
            else:
                users[user] = {'password': pwd, 'role': role}
                save_users(users)
                self.render_login('<div class="success">注册成功，请登录</div>', '')

        elif path == '/add':
            try:
                if not session:
                    self.send_response(302)
                    self.send_header('Location', '/login')
                    self.end_headers()
                elif session.get('role') != 'admin':
                    self.send_response(403)
                    self.send_html('<h1>Forbidden</h1><p>Viewer cannot add servers.</p><a href="/dashboard">Back</a>')
                else:
                    gpus = []
                    for key in form:
                        if key.startswith('gpu_') and key != 'gpu_count':
                            gpus.append(int(key.replace('gpu_', '')))
                    
                    gpu_count_val = '0'
                    if 'gpu_count' in form and form['gpu_count']:
                        gpu_count_val = form['gpu_count'][0]
                    try:
                        gpu_count = int(gpu_count_val) if gpu_count_val else 0
                    except:
                        gpu_count = 0
                    
                    # 组合内存和磁盘显示
                    mem_value = form.get('mem_value', [''])[0]
                    mem_unit = form.get('mem_unit', ['GB'])[0]
                    mem_display = f"{mem_value}{mem_unit}" if mem_value else ''
                    
                    disk_value = form.get('disk_value', [''])[0]
                    disk_unit = form.get('disk_unit', ['TB'])[0]
                    disk_display = f"{disk_value}{disk_unit}" if disk_value else ''
                    
                    data = load_data()
                    # 生成新的唯一ID (最大ID + 1)
                    existing_ids = [s['id'] for s in data['servers']]
                    new_id = max(existing_ids) + 1 if existing_ids else 1
                    
                    # 获取Proxmox相关字段（虚拟机添加时会有）
                    parent_host = form.get('parent_host', [''])[0]
                    proxmox_vmid = form.get('proxmox_vmid', [''])[0]
                    
                    # 根据服务器类型确定GPU数量：物理机使用用户输入的值，虚拟机使用勾选的分配GPU数量
                    server_type = form.get('type', [''])[0]
                    if server_type == 'physical':
                        final_gpu_count = gpu_count  # 物理机：使用用户输入的GPU总数
                    else:
                        final_gpu_count = len(gpus)  # 虚拟机：使用实际勾选的分配GPU数量
                    
                    new_server = {
                        'id': new_id,
                        'hostname': form.get('hostname', [''])[0],
                        'type': server_type,
                        'purpose': form.get('purpose', [''])[0],
                        'purpose_detail': form.get('purpose_detail', [''])[0],
                        'ip': form.get('ip', [''])[0],
                        'cpu': form.get('cpu', [''])[0],
                        'mem': mem_display,
                        'disk': disk_display,
                        'gpu_count': final_gpu_count,
                        'assigned_gpus': gpus,
                        'user': form.get('user', [''])[0],
                        'reg_type': form.get('reg_type', ['manual'])[0],  # 注册方式: auto/manual
                        'ssh_verified': form.get('ssh_verified', ['false'])[0] == 'true'  # SSH验证状态
                    }
                    
                    # 添加手动注册特有的SSH凭据
                    ssh_user = form.get('ssh_user', [''])[0]
                    ssh_password = form.get('ssh_password', [''])[0]
                    if ssh_user and ssh_password:
                        new_server['ssh_user'] = ssh_user
                        new_server['ssh_password'] = ssh_password
                    
                    # 添加虚拟机特有的字段
                    if parent_host:
                        new_server['parent_host'] = parent_host
                    if proxmox_vmid:
                        new_server['proxmox_vmid'] = int(proxmox_vmid)
                    
                    data['servers'].append(new_server)
                    save_data(data)
                    
                    # 如果是虚拟机，找到父物理机ID用于展开
                    expand_param = ''
                    if server_type == 'virtual' and parent_host:
                        for s in data['servers']:
                            if s['type'] == 'physical' and s['hostname'] == parent_host:
                                expand_param = f'?expand={s["id"]}'
                                break
                    
                    self.send_response(302)
                    self.send_header('Location', f'/dashboard{expand_param}')
                    self.end_headers()
            except Exception as e:
                import traceback
                print(f"Error adding server: {e}")
                print(traceback.format_exc())
                self.send_response(500)
                self.send_html(f'<h1>Error</h1><p>{e}</p><a href="/dashboard">Back</a>')

        elif path == '/batch_delete':
            if not session:
                self.send_response(302)
                self.send_header('Location', '/login')
                self.end_headers()
            elif session.get('role') != 'admin':
                self.send_response(403)
                self.send_html('<h1>Forbidden</h1><p>Viewer cannot delete servers.</p><a href="/dashboard">Back</a>')
            else:
                ids = form.get('ids', [''])[0]
                if ids:
                    id_list = [int(x) for x in ids.split(',') if x]
                    data = load_data()
                    data['servers'] = [s for s in data['servers'] if s['id'] not in id_list]
                    save_data(data)
                self.send_response(302)
                self.send_header('Location', '/dashboard')
                self.end_headers()
        
        elif path == '/update':
            if not session:
                self.send_response(302)
                self.send_header('Location', '/login')
                self.end_headers()
            elif session.get('role') != 'admin':
                self.send_response(403)
                self.send_html('<h1>Forbidden</h1><p>Viewer cannot edit servers.</p><a href="/dashboard">Back</a>')
            else:
                try:
                    server_id = int(form.get('id', ['0'])[0])
                    gpus = []
                    for key in form:
                        if key.startswith('gpu_') and key != 'gpu_count':
                            gpus.append(int(key.replace('gpu_', '')))
                    
                    gpu_count_val = form.get('gpu_count', ['0'])[0]
                    try:
                        gpu_count = int(gpu_count_val) if gpu_count_val else 0
                    except:
                        gpu_count = 0
                    
                    # 组合内存和磁盘显示
                    mem_value = form.get('mem_value', [''])[0]
                    mem_unit = form.get('mem_unit', ['GB'])[0]
                    mem_display = f"{mem_value}{mem_unit}" if mem_value else ''
                    
                    disk_value = form.get('disk_value', [''])[0]
                    disk_unit = form.get('disk_unit', ['TB'])[0]
                    disk_display = f"{disk_value}{disk_unit}" if disk_value else ''
                    
                    data = load_data()
                    for s in data['servers']:
                        if s['id'] == server_id:
                            s['hostname'] = form.get('hostname', [''])[0]
                            s['type'] = form.get('type', [''])[0]
                            s['purpose'] = form.get('purpose', [''])[0]
                            s['purpose_detail'] = form.get('purpose_detail', [''])[0]
                            s['ip'] = form.get('ip', [''])[0]
                            s['cpu'] = form.get('cpu', [''])[0]
                            s['mem'] = mem_display
                            s['disk'] = disk_display
                            # 根据服务器类型确定GPU数量：物理机使用用户输入的值，虚拟机使用勾选的分配GPU数量
                            if s['type'] == 'physical':
                                s['gpu_count'] = gpu_count  # 物理机：使用用户输入的GPU总数
                            else:
                                s['gpu_count'] = len(gpus)  # 虚拟机：使用实际勾选的分配GPU数量
                            s['assigned_gpus'] = gpus
                            s['user'] = form.get('user', [''])[0]
                            break
                    save_data(data)
                    self.send_response(302)
                    self.send_header('Location', '/dashboard')
                    self.end_headers()
                except Exception as e:
                    import traceback
                    print(f"Error updating server: {e}")
                    print(traceback.format_exc())
                    self.send_response(500)
                    self.send_html(f'<h1>Error</h1><p>{e}</p><a href="/dashboard">Back</a>')

        # 模型管理API（使用JSON格式）
        elif path == '/api/models/add':
            if not session or session.get('role') != 'admin':
                self.send_json({'success': False, 'error': '无权限'})
            else:
                try:
                    import json
                    # 使用已经读取的 body，不要重复读取 rfile
                    if not body or not body.strip():
                        self.send_json({'success': False, 'error': '请求体为空'})
                        return
                    
                    data = json.loads(body)
                    
                    name = data.get('name', '').strip()
                    model = data.get('model', '').strip()
                    api_key = data.get('api_key', '').strip()
                    base_url = data.get('base_url', '').strip()
                    model_type = data.get('type', 'public')
                    
                    if not name or not model or not base_url:
                        self.send_json({'success': False, 'error': '缺少必要参数'})
                        return
                    
                    if model_type == 'public' and not api_key:
                        self.send_json({'success': False, 'error': '公共模型需要API Key'})
                        return
                    
                    # 先测试连接（添加模型时自动验证）
                    success, error_msg = test_model_connection(base_url, model, api_key, model_type)
                    if not success:
                        self.send_json({'success': False, 'error': f'模型验证失败：{error_msg}'})
                        return
                    
                    # 生成唯一ID
                    model_id = str(uuid.uuid4())[:8]
                    
                    # 读取当前配置
                    config = load_config()
                    if 'models' not in config:
                        config['models'] = []
                    
                    # 添加新模型（api_key用base64编码存储）
                    new_model = {
                        'id': model_id,
                        'name': name,
                        'model': model,
                        'api_key': base64.b64encode(api_key.encode()).decode() if api_key else '',
                        'base_url': base_url,
                        'type': model_type
                    }
                    config['models'].append(new_model)
                    
                    # 如果是第一个模型，设为当前模型
                    if len(config['models']) == 1:
                        config['current_model'] = model_id
                    
                    # 保存配置
                    with open('config.json', 'w', encoding='utf-8') as f:
                        json.dump(config, f, ensure_ascii=False, indent=2)
                    
                    self.send_json({'success': True, 'model_id': model_id, 'message': '模型添加成功并已通过验证'})
                except Exception as e:
                    self.send_json({'success': False, 'error': str(e)})

        elif path == '/api/models/delete':
            if not session or session.get('role') != 'admin':
                self.send_json({'success': False, 'error': '无权限'})
            else:
                try:
                    import json
                    # 使用已经读取的 body，不要重复读取 rfile
                    if not body or not body.strip():
                        self.send_json({'success': False, 'error': '请求体为空'})
                        return
                    
                    data = json.loads(body)
                    
                    model_id = data.get('model_id')
                    if not model_id:
                        self.send_json({'success': False, 'error': '缺少模型ID'})
                        return
                    
                    config = load_config()
                    models = config.get('models', [])
                    
                    # 查找并删除
                    for i, m in enumerate(models):
                        if m.get('id') == model_id:
                            models.pop(i)
                            break
                    
                    config['models'] = models
                    
                    # 如果删除的是当前模型，重置当前模型
                    if config.get('current_model') == model_id:
                        config['current_model'] = models[0]['id'] if models else ''
                    
                    with open('config.json', 'w', encoding='utf-8') as f:
                        json.dump(config, f, ensure_ascii=False, indent=2)
                    
                    self.send_json({'success': True})
                except Exception as e:
                    self.send_json({'success': False, 'error': str(e)})

        elif path == '/api/models/select':
            if not session or session.get('role') != 'admin':
                self.send_json({'success': False, 'error': '无权限'})
            else:
                try:
                    import json
                    # 使用已经读取的 body，不要重复读取 rfile
                    if not body or not body.strip():
                        self.send_json({'success': False, 'error': '请求体为空'})
                        return
                    
                    data = json.loads(body)
                    
                    model_id = data.get('model_id')
                    if not model_id:
                        self.send_json({'success': False, 'error': '缺少模型ID'})
                        return
                    
                    config = load_config()
                    models = config.get('models', [])
                    
                    # 验证模型存在
                    found = any(m.get('id') == model_id for m in models)
                    if not found:
                        self.send_json({'success': False, 'error': '模型不存在'})
                        return
                    
                    config['current_model'] = model_id
                    
                    with open('config.json', 'w', encoding='utf-8') as f:
                        json.dump(config, f, ensure_ascii=False, indent=2)
                    
                    self.send_json({'success': True})
                except Exception as e:
                    self.send_json({'success': False, 'error': str(e)})

        elif path == '/api/models/test':
            if not session or session.get('role') != 'admin':
                self.send_json({'success': False, 'error': '无权限'})
            else:
                try:
                    import json
                    # 使用已经读取的 body，不要重复读取 rfile
                    if not body or not body.strip():
                        self.send_json({'success': False, 'error': '请求体为空'})
                        return
                    
                    data = json.loads(body)
                    
                    model_id = data.get('model_id')
                    if not model_id:
                        self.send_json({'success': False, 'error': '缺少模型ID'})
                        return
                    
                    config = load_config()
                    models = config.get('models', [])
                    
                    # 查找模型
                    target_model = None
                    for m in models:
                        if m.get('id') == model_id:
                            target_model = m
                            break
                    
                    if not target_model:
                        self.send_json({'success': False, 'error': '模型不存在'})
                        return
                    
                    # 解码api_key
                    api_key = ''
                    raw_api_key = target_model.get('api_key', '')
                    print(f"[模型验证] 原始api_key(base64): {raw_api_key[:20]}...")
                    if raw_api_key:
                        try:
                            api_key = base64.b64decode(raw_api_key).decode('utf-8').strip()
                            print(f"[模型验证] 解码后api_key: {api_key[:15]}...{api_key[-4:]}")
                        except Exception as e:
                            print(f"[模型验证] api_key解码失败: {e}")
                            pass
                    
                    base_url = target_model.get('base_url', '')
                    model = target_model.get('model', '')
                    model_type = target_model.get('type', 'public')
                    print(f"[模型验证] base_url: {base_url}, model: {model}, type: {model_type}")
                    
                    # 测试调用
                    success, error_msg = test_model_connection(base_url, model, api_key, model_type)
                    
                    if success:
                        self.send_json({'success': True})
                    else:
                        self.send_json({'success': False, 'error': error_msg or '无法连接到模型API'})
                except Exception as e:
                    self.send_json({'success': False, 'error': str(e)})

    def render_login(self, error_msg, success_msg):
        msg = error_msg or success_msg or ''
        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>登录 - 服务器智能管理系统</title>
    <style>{CSS}</style>
</head>
<body class="login-body">
    <div class="login-container">
        <h1>服务器智能管理系统</h1>
        <p class="login-subtitle">Server Intelligent Management System</p>
        {msg}
        <form method="POST" action="/login">
            <div class="login-form-group">
                <label>用户名</label>
                <input type="text" name="username" placeholder="请输入用户名" required autofocus>
            </div>
            <div class="login-form-group">
                <label>密码</label>
                <input type="password" name="password" placeholder="请输入密码" required>
            </div>
            <button type="submit" class="btn-login">登 录</button>
        </form>
        <div class="switch-mode">
            还没有账号？<a href="/register">立即注册</a>
        </div>
    </div>
</body>
</html>"""
        self.send_html(html)

    def render_register(self, error_msg, success_msg):
        msg = error_msg or success_msg or ''
        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>注册 - 服务器智能管理系统</title>
    <style>{CSS}</style>
</head>
<body class="login-body">
    <div class="login-container">
        <h1>用户注册</h1>
        <p class="login-subtitle">Create New Account</p>
        {msg}
        <form method="POST" action="/register">
            <div class="login-form-group">
                <label>用户名</label>
                <input type="text" name="username" placeholder="请输入用户名" required autofocus>
            </div>
            <div class="login-form-group">
                <label>密码</label>
                <input type="password" name="password" placeholder="请输入密码" required>
            </div>
            <div class="login-form-group">
                <label>用户类型</label>
                <select name="role" required>
                    <option value="viewer">Viewer - 仅查看</option>
                    <option value="admin">Admin - 完全权限</option>
                </select>
            </div>
            <button type="submit" class="btn-login">注 册</button>
        </form>
        <div class="switch-mode">
            已有账号？<a href="/login">立即登录</a>
        </div>
    </div>
</body>
</html>"""
        self.send_html(html)

    def render_dashboard(self, session):
        data = load_data()
        servers = data['servers']
        
        # 计算GPU统计：物理机GPU总数 + 虚拟机分配的GPU数
        total_physical_gpus = 0
        total_assigned_gpus = 0
        for s in servers:
            if s['type'] == 'physical':
                total_physical_gpus += s.get('gpu_count', 0)
            else:
                total_assigned_gpus += len(s.get('assigned_gpus', []))
        
        used_gpus = get_used_gpus()  # 保留用于兼容性
        is_admin = session.get('role') == 'admin'
        username = session.get('username', '')
        role_display = '管理员' if is_admin else '访客'
        role_class = 'role-admin' if is_admin else 'role-viewer'

        # 构建物理机到虚拟机的映射（在if外定义，确保变量始终存在）
        physical_servers = [s for s in servers if s['type'] == 'physical']
        virtual_servers = [s for s in servers if s['type'] == 'virtual']
        rows = []

        if servers:
            
            # 从 Proxmox API 获取虚拟机节点映射
            proxmox_vm_mapping = get_proxmox_vm_node_mapping()
            
            # 构建物理机 hostname -> 服务器对象的映射
            physical_hostname_map = {ps['hostname']: ps for ps in physical_servers}
            # 构建 Proxmox 节点名 -> 物理机对象的映射（用于通过节点名找到物理机）
            node_to_physical = {}
            for ps in physical_servers:
                # 物理机的 hostname 可能和 Proxmox 节点名相同或不同
                # 我们尝试通过 proxmox_vmid 或者直接使用 hostname 来关联
                node_to_physical[ps['hostname']] = ps
            
            def render_server_row(s, is_physical=False, parent_id=None, has_children=False, parent_hostname=None, physical_children=None):
                """渲染单行服务器数据"""
                tcls = 'type-physical' if s['type'] == 'physical' else 'type-virtual'
                ttxt = '物理机' if s['type'] == 'physical' else '虚拟机'
                
                # GPU显示：物理机直接显示GPU编号，虚拟机显示 物理机名_GPU编号
                # 每行最多显示2个GPU标签
                def format_gpu_tags(gpu_list, tag_format):
                    if not gpu_list:
                        return ''
                    tags = [f'<span class="gpu-tag">{tag_format(g)}</span>' for g in gpu_list]
                    # 每2个标签一组，用<br>连接
                    lines = []
                    for i in range(0, len(tags), 2):
                        lines.append(''.join(tags[i:i+2]))
                    return '<br>'.join(lines)
                
                if is_physical:
                    # 物理机GPU显示逻辑
                    gpu_count = s.get('gpu_count', 0)
                    if gpu_count == 0:
                        gtags = '<span style="color:#888;font-weight:600;">无GPU</span>'
                    elif has_children and physical_children:
                        # 有GPU且有虚拟机，计算空闲GPU数
                        children = physical_children.get(s['id'], [])
                        assigned_gpu_count = 0
                        for vm in children:
                            assigned_gpu_count += len(vm.get('assigned_gpus', []))
                        free_gpu = gpu_count - assigned_gpu_count
                        if free_gpu == 0:
                            # GPU全部分配
                            gtags = '<span style="color:#888;font-weight:600;">GPU已全分配</span>'
                        else:
                            gtags = f'<span style="color:#2e7d32;font-weight:600;">尚有{free_gpu}卡空闲</span>'
                    else:
                        # 有GPU但没有虚拟机
                        gtags = '<span style="color:#2e7d32;font-weight:600;">物理机独享</span>'
                else:
                    # 虚拟机：获取父物理机名称
                    parent = parent_hostname or s.get('parent_host', '未知')
                    gtags = format_gpu_tags(s.get('assigned_gpus', []), lambda g: f'{parent}_GPU{g}')
                
                detail = s.get('purpose_detail', '').rstrip()  # 只去除末尾空白，保留开头的缩进
                
                # 获取注册方式（auto/manual），默认为manual
                reg_type = s.get('reg_type', 'manual')
                
                # 获取SSH验证状态，手动注册的服务器需要验证通过才能查看Server Insights
                ssh_verified = s.get('ssh_verified', False)
                
                # 只有自动注册的服务器，或手动注册且SSH验证通过的服务器才能点击
                can_click = (reg_type == 'auto') or (reg_type == 'manual' and ssh_verified)
                
                if can_click:
                    hostname_display = f'<span class="clickable-hostname" style="text-decoration: underline; cursor: pointer; color: #667eea;" onclick="showPerformance({s["id"]}, event)">{s["hostname"]}</span>'
                else:
                    hostname_display = f'<span style="color: #666;">{s["hostname"]}</span>'
                
                if is_physical:
                    # 物理机：折叠按钮单独一列，存储注册方式
                    expand_cell = f'<span class="expand-btn" data-pid="{s["id"]}" onclick="toggleVmList(event, {s["id"]})">▶</span>' if has_children else ''
                    row_class = 'physical-row'
                    data_attr = f'data-physical-id="{s["id"]}" data-reg-type="{reg_type}"'
                else:
                    # 虚拟机：缩进显示（2格），折叠列为空
                    expand_cell = ''
                    row_class = f'vm-child child-of-{parent_id}' if parent_id else ''
                    data_attr = f'data-parent-id="{parent_id}"' if parent_id else ''
                
                # 根据权限构建行内容
                checkbox_col = f'<td class="checkbox-col"><input type="checkbox" name="server_select" value="{s["id"]}" onchange="updateSelection()"></td>' if is_admin else ''
                
                # 用途列：如果有详情则显示详情按钮
                if detail:
                    # 对详情内容进行 JavaScript 字符串转义
                    detail_js = detail.replace('\\', '\\\\').replace("'", "\\'").replace('"', '\\"').replace('\n', '\\n').replace('\r', '')
                    purpose_cell = f'{s["purpose"]}<span class="detail-trigger" onclick="showDetail(event, \'{detail_js}\')">&gt;&gt;</span>'
                else:
                    purpose_cell = s['purpose']
                
                return f'''<tr data-id="{s['id']}" class="{row_class}" {data_attr}>
                    {checkbox_col}
                    <td class="expand-col">{expand_cell}</td>
                    <td class="hostname">{hostname_display}</td>
                    <td>{s['user']}</td>
                    <td><span class="type-badge {tcls}">{ttxt}</span></td>
                    <td>{purpose_cell}</td>
                    <td>{s['ip']}</td>
                    <td>{s['cpu']}</td>
                    <td>{s['mem']}</td>
                    <td>{s['disk']}</td>
                    <td>{s['gpu_count']}</td>
                    <td>{gtags}</td>
                </tr>'''
            
            # 确定每个虚拟机属于哪个物理机
            # vm_parent_map: {vm_id: physical_server_object}
            vm_parent_map = {}
            for vs in virtual_servers:
                parent = None
                # 方法1: 通过本地保存的 parent_host 字段
                if vs.get('parent_host'):
                    parent = physical_hostname_map.get(vs['parent_host'])
                
                # 方法2: 通过 Proxmox API 获取的节点映射
                if not parent and vs.get('proxmox_vmid'):
                    vmid_str = str(vs['proxmox_vmid'])
                    node_name = proxmox_vm_mapping.get(vmid_str)
                    if node_name and node_name in node_to_physical:
                        parent = node_to_physical[node_name]
                
                if parent:
                    vm_parent_map[vs['id']] = parent
            
            # 按物理机分组虚拟机
            physical_children = {}  # {physical_id: [vm_list]}
            for vm_id, parent in vm_parent_map.items():
                parent_id = parent['id']
                if parent_id not in physical_children:
                    physical_children[parent_id] = []
                # 找到虚拟机对象
                vm_obj = next((vs for vs in virtual_servers if vs['id'] == vm_id), None)
                if vm_obj:
                    physical_children[parent_id].append(vm_obj)
            
            # 渲染：先渲染所有物理机
            processed_vm_ids = set()
            for ps in physical_servers:
                has_children = ps['id'] in physical_children and len(physical_children[ps['id']]) > 0
                # 渲染物理机（带折叠按钮），传递physical_children用于计算空闲GPU
                rows.append(render_server_row(ps, is_physical=True, has_children=has_children, physical_children=physical_children))
                # 渲染属于该物理机的虚拟机（默认隐藏）
                children = physical_children.get(ps['id'], [])
                for vs in children:
                    if vs['id'] not in processed_vm_ids:
                        rows.append(render_server_row(vs, is_physical=False, parent_id=ps['id'], parent_hostname=ps['hostname']))
                        processed_vm_ids.add(vs['id'])
            
            # 渲染没有父物理机的虚拟机（独立的虚拟机）
            for vs in virtual_servers:
                if vs['id'] not in processed_vm_ids:
                    rows.append(render_server_row(vs, is_physical=False))
            
            table_rows = ''.join(rows)
        else:
            checkbox_header = '<th class="checkbox-col"><input type="checkbox" id="selectAll" onclick="toggleSelectAll()"></th>' if is_admin else ''
            # 空状态时的列数：复选框(可选) + 展开列 + 10个数据列
            empty_colspan = (1 if is_admin else 0) + 1 + 10
            table_rows = f'<tr><td colspan="{empty_colspan}"><div class="empty-state">暂无服务器记录</div></td></tr>'

        checkbox_header = '<th class="checkbox-col"><input type="checkbox" id="selectAll" onclick="toggleSelectAll()"></th>' if is_admin else ''
        viewer_notice = '<div class="viewer-notice"><p>您当前以访客身份登录，仅可查看服务器信息，无法进行添加、修改或删除操作。</p></div>' if not is_admin else ''
        
        add_button = '<button class="btn-add" id="btnRegisterPhysical" onclick="openNodeInputModal()" title="通过proxmox api自动添加物理机">自动注册物理机</button>' if is_admin else ''
        manual_add_button = '<button class="btn-add" id="btnManualAddPhysical" onclick="openManualAddModal()">手动注册物理机</button>' if is_admin else ''
        batch_buttons = '''
            <div class="batch-actions" id="batchActions">
                <button type="button" class="btn-batch" onclick="editSelected()">修改选中</button>
                <button type="button" class="btn-batch-delete" onclick="deleteSelected()">删除选中</button>
            </div>
        ''' if is_admin else ''
        auto_add_vm_button = '<button type="button" class="btn-add-vm" id="btnAutoAddVm" onclick="addVmToHost()" style="display:none;">自动添加虚拟机</button>' if is_admin else ''
        manual_add_vm_button = '<button type="button" class="btn-add-vm" id="btnManualAddVm" onclick="openManualAddVmModal()" style="display:none;">手动添加虚拟机</button>' if is_admin else ''

        gpu_sel = []
        for i in range(8):
            dis = 'disabled' if i in used_gpus else ''
            gpu_sel.append(f'<div class="gpu-checkbox"><input type="checkbox" id="g{i}" name="gpu_{i}" value="{i}" {dis} onchange="updateGpuCount()"><label for="g{i}" class="gpu-label">GPU{i}</label></div>')
        
        gpu_selection_html = ''.join(gpu_sel)
        
        # 构建添加模态框HTML - 分为两个弹窗：节点名输入和物理机信息填写
        # 获取已添加的虚拟机ID列表用于前端标记
        added_vmids = [s.get('proxmox_vmid') for s in servers if s.get('proxmox_vmid')]
        
        add_modal_html = f'''
        <!-- 节点名输入弹窗 -->
        <div class="modal" id="nodeInputModal">
            <div class="modal-content" style="max-width: 450px;">
                <div class="modal-header">
                    <h2>注册物理机</h2>
                    <button class="btn-close" onclick="closeNodeInputModal()">&times;</button>
                </div>
                <div class="modal-body">
                    <div class="form-group">
                        <label>Proxmox 节点名 *</label>
                        <input type="text" id="nodeNameInput" placeholder="例如: pve" required>
                        <p style="font-size: 12px; color: #888; margin-top: 8px;">请输入 Proxmox 集群中的节点名称，系统将自动获取该节点的硬件信息。</p>
                    </div>
                    <div id="nodeError" style="color: #c62828; font-size: 14px; margin-top: 10px; display: none;"></div>
                </div>
                <div class="modal-footer">
                    <button type="button" class="btn-cancel" onclick="closeNodeInputModal()">取消</button>
                    <button type="button" class="btn-save" onclick="fetchNodeInfo()">获取信息</button>
                </div>
            </div>
        </div>
        
        <!-- 添加物理机弹窗 -->
        <div class="modal" id="addModal">
            <div class="modal-content">
                <div class="modal-header">
                    <h2>添加物理机</h2>
                    <button class="btn-close" onclick="closeModal()">&times;</button>
                </div>
                <form method="POST" action="/add" id="addForm">
                    <input type="hidden" name="type" value="physical">
                    <input type="hidden" name="reg_type" value="auto">
                    <input type="hidden" name="ssh_verified" value="true">
                    <div class="modal-body">
                        <div class="form-row">
                            <div class="form-group">
                                <label>主机名 *</label>
                                <input type="text" name="hostname" id="physHostname" required>
                            </div>
                            <div class="form-group">
                                <label>IP地址 *</label>
                                <input type="text" name="ip" id="physIp" required>
                            </div>
                        </div>
                        <div class="form-row">
                            <div class="form-group">
                                <label>CPU *</label>
                                <input type="text" name="cpu" id="physCpu" required>
                            </div>
                            <div class="form-group">
                                <label>内存 *</label>
                                <div style="display:flex;gap:10px;">
                                    <input type="number" name="mem_value" id="physMemValue" min="1" style="flex:1;" required>
                                    <select name="mem_unit" id="physMemUnit" style="width:100px;">
                                        <option value="GB">GB</option>
                                        <option value="TB">TB</option>
                                    </select>
                                </div>
                            </div>
                        </div>
                        <div class="form-row">
                            <div class="form-group">
                                <label>磁盘 *</label>
                                <div style="display:flex;gap:10px;">
                                    <input type="number" name="disk_value" id="physDiskValue" min="1" style="flex:1;" required>
                                    <select name="disk_unit" id="physDiskUnit" style="width:100px;" onchange="convertDiskUnit()">
                                        <option value="GB">GB</option>
                                        <option value="TB">TB</option>
                                        <option value="PB">PB</option>
                                    </select>
                                </div>
                            </div>
                            <div class="form-group">
                                <label>GPU数量 *</label>
                                <input type="number" name="gpu_count" id="physGpuCount" min="0" max="8" value="0" required>
                            </div>
                        </div>
                        <div class="form-group">
                            <label>用途 *</label>
                            <input type="text" name="purpose" id="physPurpose" required>
                        </div>
                        <div class="form-group">
                            <label>用途详情</label>
                            <textarea name="purpose_detail" id="physPurposeDetail" placeholder="详细描述..."></textarea>
                        </div>
                        <div class="form-group">
                            <label>使用人 *</label>
                            <input type="text" name="user" id="physUser" required>
                        </div>
                    </div>
                    <div class="modal-footer">
                        <button type="button" class="btn-cancel" onclick="closeModal()">取消</button>
                        <button type="submit" class="btn-save">保存</button>
                    </div>
                </form>
            </div>
        </div>
        
        <!-- 手动添加物理机弹窗 -->
        <div class="modal" id="manualAddModal">
            <div class="modal-content">
                <div class="modal-header">
                    <h2>手动注册物理机</h2>
                    <button class="btn-close" onclick="closeManualAddModal()">&times;</button>
                </div>
                <form method="POST" action="/add" id="manualAddForm">
                    <input type="hidden" name="type" value="physical">
                    <input type="hidden" name="reg_type" value="manual">
                    <input type="hidden" name="ssh_verified" id="manualSshVerified" value="false">
                    <div class="modal-body">
                        <div class="form-row">
                            <div class="form-group">
                                <label>主机名 *</label>
                                <input type="text" name="hostname" id="manualHostname" required>
                            </div>
                            <div class="form-group">
                                <label>IP地址 *</label>
                                <input type="text" name="ip" id="manualIp" required>
                            </div>
                        </div>
                        <div class="form-row">
                            <div class="form-group">
                                <label>SSH用户名 *</label>
                                <input type="text" name="ssh_user" id="manualSshUser" required>
                            </div>
                            <div class="form-group">
                                <label>SSH密码 *</label>
                                <input type="password" name="ssh_password" id="manualSshPassword" required>
                            </div>
                        </div>
                        <div class="form-row">
                            <div class="form-group">
                                <label>CPU *</label>
                                <input type="text" name="cpu" id="manualCpu" required>
                            </div>
                            <div class="form-group">
                                <label>内存 *</label>
                                <div style="display:flex;gap:10px;">
                                    <input type="number" name="mem_value" id="manualMemValue" min="1" style="flex:1;" required>
                                    <select name="mem_unit" id="manualMemUnit" style="width:100px;">
                                        <option value="GB" selected>GB</option>
                                        <option value="TB">TB</option>
                                        <option value="PB">PB</option>
                                    </select>
                                </div>
                            </div>
                        </div>
                        <div class="form-row">
                            <div class="form-group">
                                <label>磁盘 *</label>
                                <div style="display:flex;gap:10px;">
                                    <input type="number" name="disk_value" id="manualDiskValue" min="1" style="flex:1;" required>
                                    <select name="disk_unit" id="manualDiskUnit" style="width:100px;">
                                        <option value="GB" selected>GB</option>
                                        <option value="TB">TB</option>
                                        <option value="PB">PB</option>
                                    </select>
                                </div>
                            </div>
                            <div class="form-group">
                                <label>GPU数量 *</label>
                                <input type="number" name="gpu_count" id="manualGpuCount" min="0" max="8" value="0" required>
                            </div>
                        </div>
                        <div class="form-group">
                            <label>用途 *</label>
                            <input type="text" name="purpose" id="manualPurpose" required>
                        </div>
                        <div class="form-group">
                            <label>用途详情</label>
                            <textarea name="purpose_detail" id="manualPurposeDetail" placeholder="详细描述..."></textarea>
                        </div>
                        <div class="form-group">
                            <label>使用人 *</label>
                            <input type="text" name="user" id="manualUser" required>
                        </div>
                    </div>
                    </div>
                    <div class="modal-footer" style="flex-direction: column; align-items: stretch; border-top: none;">
                        <div id="manualVerifyStatusArea" style="padding: 10px 0; text-align: center; min-height: 24px;"></div>
                        <div style="display: flex; justify-content: flex-end; gap: 15px; width: 100%;">
                            <button type="button" class="btn-cancel" onclick="closeManualAddModal()">取消</button>
                            <button type="button" class="btn-verify" id="manualVerifyBtn" onclick="verifyManualHost()">验证连接</button>
                            <button type="submit" class="btn-save" id="manualSaveBtn" onclick="return checkManualVerified()">保存</button>
                        </div>
                    </div>
                </form>
            </div>
        </div>
        
        <!-- 虚拟机列表选择弹窗 -->
        <div class="modal" id="vmListModal">
            <div class="modal-content" style="max-width: 600px;">
                <div class="modal-header">
                    <h2>选择虚拟机</h2>
                    <button class="btn-close" onclick="closeVmListModal()">&times;</button>
                </div>
                <div class="modal-body">
                    <p style="margin-bottom: 15px; color: #666;">请选择要添加到系统的虚拟机（灰色项表示已添加）：</p>
                    <div id="vmList" class="vm-list">
                        <!-- 虚拟机列表将在这里动态生成 -->
                    </div>
                    <div id="vmListError" style="color: #c62828; font-size: 14px; margin-top: 10px; display: none;"></div>
                </div>
                <div class="modal-footer">
                    <button type="button" class="btn-cancel" onclick="closeVmListModal()">取消</button>
                    <button type="button" class="btn-save" id="btnConfirmVm" onclick="confirmVmSelection()" disabled>确认选择</button>
                </div>
            </div>
        </div>
        
        <!-- 添加虚拟机信息弹窗 -->
        <div class="modal" id="addVmModal">
            <div class="modal-content">
                <div class="modal-header">
                    <h2>添加虚拟机</h2>
                    <button class="btn-close" onclick="closeAddVmModal()">&times;</button>
                </div>
                <form method="POST" action="/add" id="addVmForm">
                    <input type="hidden" name="type" value="virtual">
                    <input type="hidden" name="reg_type" value="auto">
                    <input type="hidden" name="ssh_verified" value="true">
                    <input type="hidden" name="parent_host" id="vmParentHost" value="">
                    <input type="hidden" name="proxmox_vmid" id="vmProxmoxVmid" value="">
                    <div class="modal-body">
                        <div class="form-row">
                            <div class="form-group">
                                <label>主机名 *</label>
                                <input type="text" name="hostname" id="vmHostname" required>
                            </div>
                            <div class="form-group">
                                <label>IP地址 *</label>
                                <input type="text" name="ip" id="vmIp" required>
                            </div>
                        </div>
                        <div class="form-row">
                            <div class="form-group">
                                <label>CPU *</label>
                                <input type="text" name="cpu" id="vmCpu" required>
                            </div>
                            <div class="form-group">
                                <label>内存 *</label>
                                <div style="display:flex;gap:10px;">
                                    <input type="number" name="mem_value" id="vmMemValue" min="1" style="flex:1;" required>
                                    <select name="mem_unit" id="vmMemUnit" style="width:100px;">
                                        <option value="GB">GB</option>
                                        <option value="TB">TB</option>
                                    </select>
                                </div>
                            </div>
                        </div>
                        <div class="form-row">
                            <div class="form-group">
                                <label>磁盘 *</label>
                                <div style="display:flex;gap:10px;">
                                    <input type="number" name="disk_value" id="vmDiskValue" min="1" style="flex:1;" required>
                                    <select name="disk_unit" id="vmDiskUnit" style="width:100px;">
                                        <option value="GB">GB</option>
                                        <option value="TB">TB</option>
                                        <option value="PB">PB</option>
                                    </select>
                                </div>
                            </div>
                            <div class="form-group">
                                <label>GPU数量 *</label>
                                <input type="number" name="gpu_count" id="vmGpuCount" min="0" max="8" value="0" required>
                            </div>
                        </div>
                        <div class="form-group">
                            <label>用途 *</label>
                            <input type="text" name="purpose" id="vmPurpose" required>
                        </div>
                        <div class="form-group">
                            <label>用途详情</label>
                            <textarea name="purpose_detail" id="vmPurposeDetail" placeholder="详细描述..."></textarea>
                        </div>
                        <div class="form-group">
                            <label>分配GPU</label>
                            <div id="vmGpuSelection">
                                <p style="color:#888;">请先选择物理机</p>
                            </div>
                        </div>
                        <div class="form-group">
                            <label>使用人 *</label>
                            <input type="text" name="user" id="vmUser" required>
                        </div>
                    </div>
                    <div class="modal-footer">
                        <button type="button" class="btn-cancel" onclick="closeAddVmModal()">取消</button>
                        <button type="submit" class="btn-save">保存</button>
                    </div>
                </form>
            </div>
        </div>
        
        <!-- 手动添加虚拟机弹窗 -->
        <div class="modal" id="manualAddVmModal">
            <div class="modal-content">
                <div class="modal-header">
                    <h2>手动添加虚拟机</h2>
                    <button class="btn-close" onclick="closeManualAddVmModal()">&times;</button>
                </div>
                <form method="POST" action="/add" id="manualAddVmForm">
                    <input type="hidden" name="type" value="virtual">
                    <input type="hidden" name="parent_host" id="manualVmParentHost" value="">
                    <input type="hidden" name="ssh_verified" id="manualVmSshVerified" value="false">
                    <div class="modal-body">
                        <div class="form-row">
                            <div class="form-group">
                                <label>主机名 *</label>
                                <input type="text" name="hostname" id="manualVmHostname" required>
                            </div>
                            <div class="form-group">
                                <label>IP地址 *</label>
                                <input type="text" name="ip" id="manualVmIp" required>
                            </div>
                        </div>
                        <div class="form-row">
                            <div class="form-group">
                                <label>SSH用户名 *</label>
                                <input type="text" name="ssh_user" id="manualVmSshUser" required>
                            </div>
                            <div class="form-group">
                                <label>SSH密码 *</label>
                                <input type="password" name="ssh_password" id="manualVmSshPassword" required>
                            </div>
                        </div>
                        <div class="form-row">
                            <div class="form-group">
                                <label>CPU *</label>
                                <input type="text" name="cpu" id="manualVmCpu" required>
                            </div>
                            <div class="form-group">
                                <label>内存 *</label>
                                <div style="display:flex;gap:10px;">
                                    <input type="number" name="mem_value" id="manualVmMemValue" min="1" style="flex:1;" required>
                                    <select name="mem_unit" id="manualVmMemUnit" style="width:100px;">
                                        <option value="GB" selected>GB</option>
                                        <option value="TB">TB</option>
                                        <option value="PB">PB</option>
                                    </select>
                                </div>
                            </div>
                        </div>
                        <div class="form-row">
                            <div class="form-group">
                                <label>磁盘 *</label>
                                <div style="display:flex;gap:10px;">
                                    <input type="number" name="disk_value" id="manualVmDiskValue" min="1" style="flex:1;" required>
                                    <select name="disk_unit" id="manualVmDiskUnit" style="width:100px;">
                                        <option value="GB" selected>GB</option>
                                        <option value="TB">TB</option>
                                        <option value="PB">PB</option>
                                    </select>
                                </div>
                            </div>
                            <div class="form-group">
                                <label>GPU数量 *</label>
                                <input type="number" name="gpu_count" id="manualVmGpuCount" min="0" max="8" value="0" required>
                            </div>
                        </div>
                        <div class="form-group">
                            <label>用途 *</label>
                            <input type="text" name="purpose" id="manualVmPurpose" required>
                        </div>
                        <div class="form-group">
                            <label>用途详情</label>
                            <textarea name="purpose_detail" id="manualVmPurposeDetail" placeholder="详细描述..."></textarea>
                        </div>
                        <div class="form-group">
                            <label>分配GPU</label>
                            <div id="manualVmGpuSelection">
                                <p style="color:#888;">请先选择物理机</p>
                            </div>
                        </div>
                        <div class="form-group">
                            <label>使用人 *</label>
                            <input type="text" name="user" id="manualVmUser" required>
                        </div>
                    </div>
                    </div>
                    <div class="modal-footer" style="flex-direction: column; align-items: stretch; border-top: none;">
                        <div id="manualVmVerifyStatusArea" style="padding: 10px 0; text-align: center; min-height: 24px;"></div>
                        <div style="display: flex; justify-content: flex-end; gap: 15px; width: 100%;">
                            <button type="button" class="btn-cancel" onclick="closeManualAddVmModal()">取消</button>
                            <button type="button" class="btn-verify" id="manualVmVerifyBtn" onclick="verifyManualVmHost()">验证连接</button>
                            <button type="submit" class="btn-save" id="manualVmSaveBtn" onclick="return checkManualVmVerified()">保存</button>
                        </div>
                    </div>
                </form>
            </div>
        </div>'''

        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>服务器智能管理系统 - 控制台</title>
    <style>{CSS}</style>
</head>
<body>
    <div class="header">
        <h1>服务器智能管理系统 <span class="role-badge {role_class}">{role_display}</span></h1>
        <div class="user-menu">
            <div class="user-menu-trigger" onclick="toggleUserMenu()">
                <span>{username}</span>
                <span>▼</span>
            </div>
            <div class="user-menu-dropdown" id="userMenuDropdown">
                {f'<a href="/models" class="user-menu-item admin-only">模型管理</a>' if session.get('role') == 'admin' else ''}
                <a href="/logout" class="user-menu-item">退出登录</a>
            </div>
        </div>
    </div>
    <div class="container">
        {viewer_notice}
        <div class="stats">
            <div class="stat-card">
                <div class="stat-label">物理机总数</div>
                <div class="stat-value">{len(physical_servers)}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">虚拟机总数</div>
                <div class="stat-value">{len(virtual_servers)}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">GPU已用/总数</div>
                <div class="stat-value">{total_assigned_gpus}/{total_physical_gpus}</div>
            </div>
        </div>
        
        <!-- 性能监控面板 -->
        <div class="tab-label" id="perfTabLabel">Server Insights</div>
        <div class="performance-panel" id="performancePanel">
            <div class="performance-header">
                <span class="performance-hostname" id="perfHostname">--</span>
                <span class="performance-status" id="perfStatus">--</span>
                <div class="performance-actions">
                    <button class="btn-ai-diagnosis" id="perfAiDiagnosisBtn" title="AI智能诊断">AI诊断</button>
                    <div class="btn-separator"></div>
                    <button class="btn-refresh" id="perfRefreshBtn" title="刷新性能数据">刷新</button>
                    <button class="btn-clear" id="perfClearBtn" title="关闭性能面板">关闭</button>
                </div>
            </div>
            <div class="performance-charts">
                <div class="chart-container">
                    <svg class="pie-chart cpu" viewBox="0 0 120 120">
                        <circle class="pie-bg" cx="60" cy="60" r="40"/>
                        <circle class="pie-fill" cx="60" cy="60" r="40" stroke-dasharray="0 251.2" transform="rotate(-90 60 60)"/>
                    </svg>
                    <div class="chart-label">CPU使用率</div>
                    <div class="chart-value" id="cpuValue">--%</div>
                </div>
                <div class="chart-container">
                    <svg class="pie-chart mem" viewBox="0 0 120 120">
                        <circle class="pie-bg" cx="60" cy="60" r="40"/>
                        <circle class="pie-fill" cx="60" cy="60" r="40" stroke-dasharray="0 251.2" transform="rotate(-90 60 60)"/>
                    </svg>
                    <div class="chart-label">内存使用率</div>
                    <div class="chart-value" id="memValue">--%</div>
                </div>
                <div class="chart-container">
                    <svg class="pie-chart disk" viewBox="0 0 120 120">
                        <circle class="pie-bg" cx="60" cy="60" r="40"/>
                        <circle class="pie-fill" cx="60" cy="60" r="40" stroke-dasharray="0 251.2" transform="rotate(-90 60 60)"/>
                    </svg>
                    <div class="chart-label">磁盘IO</div>
                    <div class="chart-value" id="diskValue">-- MB/s</div>
                </div>
            </div>
            <div class="ai-diagnosis-dialog" id="aiDiagnosisDialog">
                <div class="ai-diagnosis-header">
                    <div class="ai-diagnosis-icon">AI</div>
                    <div class="ai-diagnosis-title">智能诊断报告</div>
                    <span class="ai-diagnosis-collapse" id="aiDiagnosisCollapse"><< 收起</span>
                </div>
                <div class="ai-diagnosis-content" id="aiDiagnosisContent"></div>
            </div>
        </div>
        
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:5px;">
            <div class="tab-label inventory-tab">Inventory</div>
            <div style="display:flex;align-items:center;gap:30px;">
                <div style="display:flex;align-items:center;">
                    {batch_buttons}
                </div>
                <div style="display:flex;align-items:center;">
                    {auto_add_vm_button}
                    {manual_add_vm_button}
                    {manual_add_button}
                    {add_button}
                </div>
            </div>
        </div>
        <div class="table-container">
            <table>
                <thead>
                    <tr>
                        {checkbox_header}
                        <th class="expand-col"></th>
                        <th>主机名</th>
                        <th>使用人</th>
                        <th>类型</th>
                        <th>用途</th>
                        <th>IP地址</th>
                        <th>CPU</th>
                        <th>内存</th>
                        <th>磁盘</th>
                        <th>GPU</th>
                        <th>分配GPU</th>
                    </tr>
                </thead>
                <tbody>
                    {table_rows}
                </tbody>
            </table>
        </div>
    </div>
    {add_modal_html if is_admin else ''}
    <script>
        let selectedIds = [];
        
        // Admin 特有的弹窗函数
        function openModal() {{ 
            const modal = document.getElementById('addModal');
            if (modal) modal.classList.add('active'); 
        }}
        function closeModal() {{ 
            const modal = document.getElementById('addModal');
            if (modal) modal.classList.remove('active');
            // 重置表单
            const form = document.getElementById('addForm');
            if (form) form.reset();
        }}
        
        // 节点名输入弹窗
        function openNodeInputModal() {{ 
            const modal = document.getElementById('nodeInputModal');
            const input = document.getElementById('nodeNameInput');
            if (modal) modal.classList.add('active'); 
            if (input) input.focus();
        }}
        function closeNodeInputModal() {{ 
            const modal = document.getElementById('nodeInputModal');
            const errorDiv = document.getElementById('nodeError');
            const input = document.getElementById('nodeNameInput');
            if (modal) modal.classList.remove('active'); 
            if (errorDiv) errorDiv.style.display = 'none';
            if (input) input.value = '';
        }}
        
        // 动态绑定事件监听器（避免元素不存在时报错）
        document.addEventListener('DOMContentLoaded', function() {{
            const addModal = document.getElementById('addModal');
            if (addModal) {{
                addModal.addEventListener('click', (e) => {{ if (e.target === e.currentTarget) closeModal(); }});
            }}
            const nodeInputModal = document.getElementById('nodeInputModal');
            if (nodeInputModal) {{
                nodeInputModal.addEventListener('click', (e) => {{ 
                    if (e.target === e.currentTarget) closeNodeInputModal(); 
                }});
            }}
        }});
        
        // 手动添加物理机弹窗
        function openManualAddModal() {{
            const modal = document.getElementById('manualAddModal');
            const input = document.getElementById('manualHostname');
            if (modal) modal.classList.add('active');
            if (input) input.focus();
        }}
        
        // 获取节点信息（admin专用）
        async function fetchNodeInfo() {{
            const nodeNameInput = document.getElementById('nodeNameInput');
            const errorDiv = document.getElementById('nodeError');
            if (!nodeNameInput || !errorDiv) return;
            
            const nodeName = nodeNameInput.value.trim();
            
            if (!nodeName) {{
                errorDiv.textContent = '请输入节点名';
                errorDiv.style.display = 'block';
                return;
            }}
            
            try {{
                const response = await fetch('/api/node_info?node=' + encodeURIComponent(nodeName));
                const data = await response.json();
                
                if (data.success) {{
                    // 填充表单
                    const physHostname = document.getElementById('physHostname');
                    const physCpu = document.getElementById('physCpu');
                    const physMemValue = document.getElementById('physMemValue');
                    const physMemUnit = document.getElementById('physMemUnit');
                    const physDiskValue = document.getElementById('physDiskValue');
                    const physDiskUnit = document.getElementById('physDiskUnit');
                    const physIp = document.getElementById('physIp');
                    const physGpuCount = document.getElementById('physGpuCount');
                    
                    if (physHostname) physHostname.value = data.info.hostname || nodeName;
                    if (physCpu) physCpu.value = data.info.cpu || '';
                    if (physMemValue) physMemValue.value = data.info.mem_value || '';
                    if (physMemUnit) physMemUnit.value = data.info.mem_unit || 'GB';
                    
                    // 填充磁盘信息并初始化原始GB值
                    const diskValue = data.info.disk_value || '';
                    if (physDiskValue) physDiskValue.value = diskValue;
                    if (physDiskUnit) physDiskUnit.value = data.info.disk_unit || 'GB';
                    currentDiskValueGB = parseInt(diskValue) || 0;  // 保存原始GB值
                    
                    // 填充IP地址（如果API返回了）
                    if (data.info.ip && physIp) {{
                        physIp.value = data.info.ip;
                    }}
                    
                    // 填充GPU数量（如果API返回了）
                    if (data.info.gpu_count !== undefined && physGpuCount) {{
                        physGpuCount.value = data.info.gpu_count;
                    }}
                    
                    // 关闭节点输入弹窗，打开物理机添加弹窗
                    closeNodeInputModal();
                    openModal();
                }} else {{
                    errorDiv.textContent = data.error || '获取节点信息失败';
                    errorDiv.style.display = 'block';
                }}
            }} catch (err) {{
                errorDiv.textContent = '请求失败: ' + err.message;
                errorDiv.style.display = 'block';
            }}
        }}
        
        // 回车键提交（动态绑定）
        document.addEventListener('DOMContentLoaded', function() {{
            const nodeNameInput = document.getElementById('nodeNameInput');
            if (nodeNameInput) {{
                nodeNameInput.addEventListener('keypress', function(e) {{
                    if (e.key === 'Enter') {{
                        e.preventDefault();
                        fetchNodeInfo();
                    }}
                }});
            }}
        }});
        
        // 磁盘单位换算
        let currentDiskValueGB = 0;  // 存储原始的GB值
        
        function convertDiskUnit() {{
            const valueInput = document.getElementById('physDiskValue');
            const unitSelect = document.getElementById('physDiskUnit');
            const currentUnit = unitSelect.value;
            
            // 如果是第一次换算，先保存原始GB值
            if (currentDiskValueGB === 0 && valueInput.value) {{
                currentDiskValueGB = parseInt(valueInput.value);
            }}
            
            // 根据单位换算
            let convertedValue;
            if (currentUnit === 'GB') {{
                convertedValue = currentDiskValueGB;
            }} else if (currentUnit === 'TB') {{
                convertedValue = Math.round(currentDiskValueGB / 1024);
            }} else if (currentUnit === 'PB') {{
                convertedValue = Math.round(currentDiskValueGB / (1024 * 1024));
            }}
            
            // 确保最小值为1
            valueInput.value = Math.max(1, convertedValue);
        }}
        
        // 监听数值变化，更新原始GB值（动态绑定）
        document.addEventListener('DOMContentLoaded', function() {{
            const physDiskValue = document.getElementById('physDiskValue');
            if (physDiskValue) {{
                physDiskValue.addEventListener('change', function() {{
                    const unitSelect = document.getElementById('physDiskUnit');
                    if (!unitSelect) return;
                    const currentValue = parseInt(this.value) || 0;
                    
                    // 根据当前单位反算GB值
                    if (unitSelect.value === 'GB') {{
                        currentDiskValueGB = currentValue;
                    }} else if (unitSelect.value === 'TB') {{
                        currentDiskValueGB = currentValue * 1024;
                    }} else if (unitSelect.value === 'PB') {{
                        currentDiskValueGB = currentValue * 1024 * 1024;
                    }}
                }});
            }}
        }});
        
        function toggleSelectAll() {{
            const selectAll = document.getElementById('selectAll');
            if (!selectAll) return;
            const checkboxes = document.querySelectorAll('input[name="server_select"]');
            checkboxes.forEach(cb => {{
                cb.checked = selectAll.checked;
                const row = cb.closest('tr');
                if (selectAll.checked) row.classList.add('selected');
                else row.classList.remove('selected');
            }});
            updateSelection();
        }}
        
        // 页面加载完成后检查URL参数，如果有expand则自动展开对应的物理机
        window.addEventListener('DOMContentLoaded', function() {{
            const urlParams = new URLSearchParams(window.location.search);
            const expandId = urlParams.get('expand');
            if (expandId) {{
                const expandBtn = document.querySelector('.expand-btn[data-pid="' + expandId + '"]');
                if (expandBtn && !expandBtn.classList.contains('expanded')) {{
                    // 模拟点击展开
                    toggleVmList({{stopPropagation: function() {{}}}}, parseInt(expandId));
                }}
            }}
        }});
        
        function updateSelection() {{
            const checkboxes = document.querySelectorAll('input[name="server_select"]:checked');
            selectedIds = Array.from(checkboxes).map(cb => parseInt(cb.value));
            const batchActions = document.getElementById('batchActions');
            const btnAutoAddVm = document.getElementById('btnAutoAddVm');
            const btnManualAddVm = document.getElementById('btnManualAddVm');
            
            if (selectedIds.length > 0) {{
                if (batchActions) batchActions.classList.add('active');
            }} else {{
                if (batchActions) batchActions.classList.remove('active');
            }}
            
            // 检查选中的类型和注册方式，显示对应的添加虚拟机按钮
            let showAutoBtn = false;
            let showManualBtn = false;
            if (selectedIds.length === 1) {{
                const selectedRow = document.querySelector('tr[data-id="' + selectedIds[0] + '"]');
                if (selectedRow) {{
                    const typeBadge = selectedRow.querySelector('.type-badge');
                    if (typeBadge && typeBadge.textContent === '物理机') {{
                        const regType = selectedRow.getAttribute('data-reg-type');
                        if (regType === 'auto') {{
                            showAutoBtn = true;
                        }} else {{
                            showManualBtn = true;
                        }}
                    }}
                }}
            }}
            
            if (btnAutoAddVm) {{
                btnAutoAddVm.style.display = showAutoBtn ? 'inline-block' : 'none';
            }}
            if (btnManualAddVm) {{
                btnManualAddVm.style.display = showManualBtn ? 'inline-block' : 'none';
            }}
            
            // Update row highlighting
            document.querySelectorAll('tbody tr').forEach(row => {{
                const cb = row.querySelector('input[name="server_select"]');
                if (cb && cb.checked) row.classList.add('selected');
                else row.classList.remove('selected');
            }});
        }}
        
        function deleteSelected() {{
            if (selectedIds.length === 0) return;
            if (!confirm('确定要删除选中的 ' + selectedIds.length + ' 台服务器吗？')) return;
            
            const form = document.createElement('form');
            form.method = 'POST';
            form.action = '/batch_delete';
            const input = document.createElement('input');
            input.type = 'hidden';
            input.name = 'ids';
            input.value = selectedIds.join(',');
            form.appendChild(input);
            document.body.appendChild(form);
            form.submit();
        }}
        
        function editSelected() {{
            if (selectedIds.length === 0) return;
            if (selectedIds.length > 1) {{
                alert('请只选择一台服务器进行修改');
                return;
            }}
            window.location.href = '/edit?id=' + selectedIds[0];
        }}
        
        // 折叠/展开虚拟机列表
        function toggleVmList(event, physicalId) {{
            event.stopPropagation();
            const btn = document.querySelector('.expand-btn[data-pid="' + physicalId + '"]');
            const vmRows = document.querySelectorAll('.child-of-' + physicalId);
            
            if (btn.classList.contains('expanded')) {{
                // 折叠
                btn.classList.remove('expanded');
                vmRows.forEach(row => row.classList.remove('visible'));
            }} else {{
                // 展开
                btn.classList.add('expanded');
                vmRows.forEach(row => row.classList.add('visible'));
            }}
        }}
        
        // 显示用途详情小窗
        let detailPopup = null;
        let hideDetailTimeout = null;
        
        function showDetail(event, detail) {{
            event.stopPropagation();
            
            // 关闭已有小窗
            if (detailPopup) {{
                detailPopup.remove();
                detailPopup = null;
            }}
            
            // 创建小窗元素
            detailPopup = document.createElement('div');
            detailPopup.className = 'detail-popup visible';
            detailPopup.textContent = detail;
            document.body.appendChild(detailPopup);
            
            // 定位小窗
            const rect = event.target.getBoundingClientRect();
            
            let top = rect.bottom + 8;
            let left = rect.left - 20;
            
            // 确保不超出视口
            if (left + 300 > window.innerWidth) {{
                left = window.innerWidth - 320;
            }}
            
            detailPopup.style.top = top + 'px';
            detailPopup.style.left = left + 'px';
            
            // 鼠标移出小窗后消失
            detailPopup.addEventListener('mouseleave', function() {{
                hideDetailTimeout = setTimeout(() => {{
                    if (detailPopup) {{
                        detailPopup.remove();
                        detailPopup = null;
                    }}
                }}, 200);
            }});
            
            detailPopup.addEventListener('mouseenter', function() {{
                if (hideDetailTimeout) {{
                    clearTimeout(hideDetailTimeout);
                    hideDetailTimeout = null;
                }}
            }});
            
            // 点击其他地方关闭
            setTimeout(() => {{
                document.addEventListener('click', hideDetailOnClickOutside);
            }}, 0);
        }}
        
        function hideDetailOnClickOutside(event) {{
            if (detailPopup && !detailPopup.contains(event.target)) {{
                detailPopup.remove();
                detailPopup = null;
                document.removeEventListener('click', hideDetailOnClickOutside);
            }}
        }}
        
        // 添加虚拟机相关变量和函数
        let currentVmList = [];
        let selectedVm = null;
        let currentNodeName = '';
        
        // 添加虚拟机按钮点击事件
        async function addVmToHost() {{
            if (selectedIds.length === 0) return;
            if (selectedIds.length > 1) {{
                alert('请只选择一台物理机来添加虚拟机');
                return;
            }}
            
            // 获取选中的物理机信息
            const selectedRow = document.querySelector('tr[data-id="' + selectedIds[0] + '"');
            if (!selectedRow) {{
                alert('无法获取物理机信息');
                return;
            }}
            
            // 检查是否是物理机
            const typeBadge = selectedRow.querySelector('.type-badge');
            if (typeBadge && typeBadge.textContent !== '物理机') {{
                alert('只能选择物理机来添加虚拟机');
                return;
            }}
            
            // 获取物理机的主机名作为节点名
            const hostname = selectedRow.querySelector('.hostname')?.textContent;
            if (!hostname) {{
                alert('无法获取物理机主机名');
                return;
            }}
            
            currentNodeName = hostname;
            
            // 打开虚拟机列表弹窗
            openVmListModal();
            
            // 获取虚拟机列表
            await fetchVmList(hostname);
        }}
        
        // 打开虚拟机列表弹窗
        function openVmListModal() {{
            document.getElementById('vmListModal').classList.add('active');
            document.getElementById('vmList').innerHTML = '<p style="text-align:center;color:#888;">加载中...</p>';
            document.getElementById('vmListError').style.display = 'none';
            selectedVm = null;
            document.getElementById('btnConfirmVm').disabled = true;
        }}
        
        // 关闭虚拟机列表弹窗
        function closeVmListModal() {{
            document.getElementById('vmListModal').classList.remove('active');
            currentVmList = [];
            selectedVm = null;
        }}
        
        // 获取虚拟机列表
        async function fetchVmList(nodeName) {{
            try {{
                const response = await fetch('/api/vm_list?node=' + encodeURIComponent(nodeName));
                const data = await response.json();
                
                if (data.success) {{
                    currentVmList = data.vms || [];
                    renderVmList(currentVmList, data.added_vmids || []);
                }} else {{
                    document.getElementById('vmList').innerHTML = '<p style="text-align:center;color:#c62828;">加载失败: ' + (data.error || '未知错误') + '</p>';
                }}
            }} catch (err) {{
                document.getElementById('vmList').innerHTML = '<p style="text-align:center;color:#c62828;">请求失败: ' + err.message + '</p>';
            }}
        }}
        
        // 渲染虚拟机列表
        function renderVmList(vms, addedVmids) {{
            const container = document.getElementById('vmList');
            if (vms.length === 0) {{
                container.innerHTML = '<p style="text-align:center;color:#888;">该节点下没有虚拟机</p>';
                return;
            }}
            
            let html = '';
            vms.forEach(vm => {{
                const isAdded = addedVmids.includes(parseInt(vm.vmid));
                const statusClass = vm.status === 'running' ? 'vm-status-running' : 'vm-status-stopped';
                const statusText = vm.status === 'running' ? '运行中' : '已停止';
                const typeText = vm.type === 'lxc' ? '容器' : '虚拟机';
                
                html += `
                    <div class="vm-item ${{isAdded ? 'disabled' : ''}}" data-vmid="${{vm.vmid}}" data-type="${{vm.type}}" ${{isAdded ? '' : 'onclick="selectVm(this)"'}}>
                        <div class="vm-name">${{vm.name}} <span class="vm-status ${{statusClass}}">${{statusText}}</span> ${{isAdded ? '<span class="vm-added-badge">已添加</span>' : ''}}</div>
                        <div class="vm-id">ID: ${{vm.vmid}} | 类型: ${{typeText}}</div>
                    </div>
                `;
            }});
            
            container.innerHTML = html;
        }}
        
        // 选择虚拟机
        function selectVm(element) {{
            // 移除其他选中状态
            document.querySelectorAll('.vm-item').forEach(item => item.classList.remove('selected'));
            // 添加选中状态
            element.classList.add('selected');
            // 保存选中的虚拟机
            selectedVm = {{
                vmid: element.dataset.vmid,
                type: element.dataset.type
            }};
            // 启用确认按钮
            document.getElementById('btnConfirmVm').disabled = false;
        }}
        
        // 确认选择虚拟机
        async function confirmVmSelection() {{
            if (!selectedVm || !currentNodeName) return;
            
            // 保存选中的虚拟机信息（关闭弹窗前，因为closeVmListModal会重置selectedVm）
            const vmInfo = {{ ...selectedVm }};
            
            // 关闭虚拟机列表弹窗
            closeVmListModal();
            
            // 获取虚拟机详细信息
            try {{
                const response = await fetch('/api/vm_info?node=' + encodeURIComponent(currentNodeName) + 
                    '&vmid=' + encodeURIComponent(vmInfo.vmid) + 
                    '&type=' + encodeURIComponent(vmInfo.type));
                const data = await response.json();
                
                if (data.success) {{
                    // 填充虚拟机表单
                    fillVmForm(data.info, currentNodeName, vmInfo.vmid);
                    // 打开添加虚拟机弹窗
                    openAddVmModal();
                }} else {{
                    alert('获取虚拟机信息失败: ' + (data.error || '未知错误'));
                }}
            }} catch (err) {{
                alert('请求失败: ' + err.message);
            }}
        }}
        
        // 生成GPU选择框HTML（从GPU1开始）
        function generateGpuSelection(gpuCount, usedGpus, checkboxNamePrefix, onchangeCallback) {{
            if (gpuCount <= 0) {{
                return '<p style="color:#888;">该物理机没有可用的GPU</p>';
            }}
            let html = '<div class="gpu-selection">';
            for (let i = 1; i <= gpuCount; i++) {{
                const isUsed = usedGpus.includes(i);
                const disabled = isUsed ? 'disabled' : '';
                const opacity = isUsed ? 'style="opacity:0.5;"' : '';
                html += `<div class="gpu-checkbox" ${{opacity}}>`;
                html += `<input type="checkbox" id="${{checkboxNamePrefix}}_${{i}}" name="${{checkboxNamePrefix}}_${{i}}" value="${{i}}" ${{disabled}} onchange="${{onchangeCallback}}()">`;
                html += `<label for="${{checkboxNamePrefix}}_${{i}}" class="gpu-label">GPU${{i}}</label>`;
                html += '</div>';
            }}
            html += '</div>';
            return html;
        }}
        
        // 更新虚拟机GPU数量显示
        function updateVmGpuCount() {{
            const checkedGpus = document.querySelectorAll('#addVmModal .gpu-selection input[type="checkbox"]:checked');
            const vmGpuCount = document.getElementById('vmGpuCount');
            if (vmGpuCount) vmGpuCount.value = checkedGpus.length;
        }}
        
        // 更新手动添加虚拟机GPU数量显示
        function updateManualVmGpuCount() {{
            const checkedGpus = document.querySelectorAll('#manualAddVmModal .gpu-selection input[type="checkbox"]:checked');
            const manualVmGpuCount = document.getElementById('manualVmGpuCount');
            if (manualVmGpuCount) manualVmGpuCount.value = checkedGpus.length;
        }}
        
        // 填充虚拟机表单
        async function fillVmForm(info, parentHost, vmid) {{
            const vmParentHost = document.getElementById('vmParentHost');
            const vmProxmoxVmid = document.getElementById('vmProxmoxVmid');
            const vmHostname = document.getElementById('vmHostname');
            const vmIp = document.getElementById('vmIp');
            const vmCpu = document.getElementById('vmCpu');
            const vmMemValue = document.getElementById('vmMemValue');
            const vmMemUnit = document.getElementById('vmMemUnit');
            const vmDiskValue = document.getElementById('vmDiskValue');
            const vmDiskUnit = document.getElementById('vmDiskUnit');
            
            if (vmParentHost) vmParentHost.value = parentHost;
            if (vmProxmoxVmid) vmProxmoxVmid.value = vmid;
            if (vmHostname) vmHostname.value = info.hostname || '';
            if (vmIp) vmIp.value = info.ip || '';
            if (vmCpu) vmCpu.value = info.cpu || '';
            if (vmMemValue) vmMemValue.value = info.mem_value || '';
            if (vmMemUnit) vmMemUnit.value = info.mem_unit || 'GB';
            if (vmDiskValue) vmDiskValue.value = info.disk_value || '';
            if (vmDiskUnit) vmDiskUnit.value = info.disk_unit || 'GB';
            
            // 获取物理机GPU信息并生成选择框
            try {{
                const response = await fetch('/api/host_gpu_info?hostname=' + encodeURIComponent(parentHost));
                const data = await response.json();
                const vmGpuSelection = document.getElementById('vmGpuSelection');
                const vmGpuCount = document.getElementById('vmGpuCount');
                if (data.success) {{
                    const gpuSelectionHtml = generateGpuSelection(data.gpu_count, data.used_gpus, 'gpu', 'updateVmGpuCount');
                    if (vmGpuSelection) vmGpuSelection.innerHTML = gpuSelectionHtml;
                    if (vmGpuCount) vmGpuCount.value = 0;
                }} else {{
                    if (vmGpuSelection) vmGpuSelection.innerHTML = '<p style="color:#888;">无法获取GPU信息</p>';
                }}
            }} catch (err) {{
                const vmGpuSelection = document.getElementById('vmGpuSelection');
                if (vmGpuSelection) vmGpuSelection.innerHTML = '<p style="color:#888;">获取GPU信息失败</p>';
            }}
        }}
        
        // 打开添加虚拟机弹窗
        function openAddVmModal() {{
            const addVmModal = document.getElementById('addVmModal');
            if (addVmModal) addVmModal.classList.add('active');
        }}
        
        // 关闭添加虚拟机弹窗
        function closeAddVmModal() {{
            const addVmModal = document.getElementById('addVmModal');
            const addVmForm = document.getElementById('addVmForm');
            if (addVmModal) addVmModal.classList.remove('active');
            if (addVmForm) addVmForm.reset();
        }}
        

        
        // 手动添加虚拟机弹窗函数
        async function openManualAddVmModal() {{
            if (selectedIds.length === 0) return;
            if (selectedIds.length > 1) {{
                alert('请只选择一台物理机来添加虚拟机');
                return;
            }}
            
            // 获取选中的物理机主机名作为父主机
            const selectedRow = document.querySelector('tr[data-id="' + selectedIds[0] + '"]');
            if (!selectedRow) {{
                alert('无法获取物理机信息');
                return;
            }}
            
            const hostname = selectedRow.querySelector('.hostname')?.textContent;
            if (!hostname) {{
                alert('无法获取物理机主机名');
                return;
            }}
            
            // 设置父主机名
            const manualVmParentHost = document.getElementById('manualVmParentHost');
            if (manualVmParentHost) manualVmParentHost.value = hostname;
            
            // 获取物理机GPU信息并生成选择框
            try {{
                const response = await fetch('/api/host_gpu_info?hostname=' + encodeURIComponent(hostname));
                const data = await response.json();
                const manualVmGpuSelection = document.getElementById('manualVmGpuSelection');
                const manualVmGpuCount = document.getElementById('manualVmGpuCount');
                if (data.success) {{
                    const gpuSelectionHtml = generateGpuSelection(data.gpu_count, data.used_gpus, 'gpu', 'updateManualVmGpuCount');
                    if (manualVmGpuSelection) manualVmGpuSelection.innerHTML = gpuSelectionHtml;
                    if (manualVmGpuCount) manualVmGpuCount.value = 0;
                }} else {{
                    if (manualVmGpuSelection) manualVmGpuSelection.innerHTML = '<p style="color:#888;">无法获取GPU信息</p>';
                }}
            }} catch (err) {{
                const manualVmGpuSelection = document.getElementById('manualVmGpuSelection');
                if (manualVmGpuSelection) manualVmGpuSelection.innerHTML = '<p style="color:#888;">获取GPU信息失败</p>';
            }}
            
            // 打开弹窗
            const manualAddVmModal = document.getElementById('manualAddVmModal');
            const manualVmHostname = document.getElementById('manualVmHostname');
            if (manualAddVmModal) manualAddVmModal.classList.add('active');
            if (manualVmHostname) manualVmHostname.focus();
        }}
        
        function closeManualAddVmModal() {{
            const manualAddVmModal = document.getElementById('manualAddVmModal');
            const manualAddVmForm = document.getElementById('manualAddVmForm');
            const manualVmVerifyStatusArea = document.getElementById('manualVmVerifyStatusArea');
            const manualVmSaveBtn = document.getElementById('manualVmSaveBtn');
            
            if (manualAddVmModal) manualAddVmModal.classList.remove('active');
            if (manualAddVmForm) manualAddVmForm.reset();
            // 清除验证状态和按钮文字
            manualVmVerified = false;
            if (manualVmVerifyStatusArea) manualVmVerifyStatusArea.innerHTML = '';
            if (manualVmSaveBtn) manualVmSaveBtn.textContent = '保存';
        }}
        
        // SSH验证状态
        let manualVerified = false;
        let manualVmVerified = false;
        
        // 更新验证状态显示
        function updateVerifyStatus(elementId, message, isSuccess) {{
            let statusEl = document.getElementById(elementId);
            if (!statusEl) {{
                statusEl = document.createElement('span');
                statusEl.id = elementId;
                statusEl.className = 'verify-status';
            }}
            statusEl.textContent = message;
            statusEl.className = 'verify-status' + (isSuccess ? ' success' : ' error');
            return statusEl;
        }}
        
        // 验证手动注册物理机的SSH连接
        async function verifyManualHost() {{
            const ipEl = document.getElementById('manualIp');
            const usernameEl = document.getElementById('manualSshUser');
            const passwordEl = document.getElementById('manualSshPassword');
            const verifyBtn = document.getElementById('manualVerifyBtn');
            const saveBtn = document.getElementById('manualSaveBtn');
            const statusArea = document.getElementById('manualVerifyStatusArea');
            
            if (!ipEl || !usernameEl || !passwordEl || !verifyBtn || !saveBtn || !statusArea) return;
            
            const ip = ipEl.value.trim();
            const username = usernameEl.value.trim();
            const password = passwordEl.value;
            
            if (!ip || !username || !password) {{
                alert('请先填写IP地址、SSH用户名和密码');
                return;
            }}
            
            verifyBtn.disabled = true;
            verifyBtn.textContent = '验证中...';
            
            try {{
                const response = await fetch('/api/verify_ssh?ip=' + encodeURIComponent(ip) + 
                    '&username=' + encodeURIComponent(username) + 
                    '&password=' + encodeURIComponent(password));
                const data = await response.json();
                
                // 在按钮上方的区域显示状态
                statusArea.innerHTML = data.success 
                    ? '<span class="verify-status success">✓ 验证通过</span>'
                    : '<span class="verify-status error">✗ ' + data.error + '</span>';
                
                manualVerified = data.success;
                
                // 更新隐藏字段记录验证状态
                const manualSshVerified = document.getElementById('manualSshVerified');
                if (manualSshVerified) manualSshVerified.value = data.success ? 'true' : 'false';
                
                // 根据验证结果修改保存按钮文字
                if (data.success) {{
                    saveBtn.textContent = '保存';
                }} else {{
                    saveBtn.textContent = '仍然保存';
                }}
            }} catch (err) {{
                statusArea.innerHTML = '<span class="verify-status error">✗ 验证请求失败: ' + err.message + '</span>';
                saveBtn.textContent = '仍然保存';
                const manualSshVerified = document.getElementById('manualSshVerified');
                if (manualSshVerified) manualSshVerified.value = 'false';
            }} finally {{
                verifyBtn.disabled = false;
                verifyBtn.textContent = '验证连接';
            }}
        }}
        
        // 检查手动注册物理机是否已验证（允许未验证时保存）
        function checkManualVerified() {{
            // 始终允许保存，不再弹窗提示
            return true;
        }}
        
        // 验证手动添加虚拟机的SSH连接
        async function verifyManualVmHost() {{
            const ipEl = document.getElementById('manualVmIp');
            const usernameEl = document.getElementById('manualVmSshUser');
            const passwordEl = document.getElementById('manualVmSshPassword');
            const verifyBtn = document.getElementById('manualVmVerifyBtn');
            const saveBtn = document.getElementById('manualVmSaveBtn');
            const statusArea = document.getElementById('manualVmVerifyStatusArea');
            
            if (!ipEl || !usernameEl || !passwordEl || !verifyBtn || !saveBtn || !statusArea) return;
            
            const ip = ipEl.value.trim();
            const username = usernameEl.value.trim();
            const password = passwordEl.value;
            
            if (!ip || !username || !password) {{
                alert('请先填写IP地址、SSH用户名和密码');
                return;
            }}
            
            verifyBtn.disabled = true;
            verifyBtn.textContent = '验证中...';
            
            try {{
                const response = await fetch('/api/verify_ssh?ip=' + encodeURIComponent(ip) + 
                    '&username=' + encodeURIComponent(username) + 
                    '&password=' + encodeURIComponent(password));
                const data = await response.json();
                
                // 在按钮上方的区域显示状态
                statusArea.innerHTML = data.success 
                    ? '<span class="verify-status success">✓ 验证通过</span>'
                    : '<span class="verify-status error">✗ ' + data.error + '</span>';
                
                manualVmVerified = data.success;
                
                // 更新隐藏字段记录验证状态
                const manualVmSshVerified = document.getElementById('manualVmSshVerified');
                if (manualVmSshVerified) manualVmSshVerified.value = data.success ? 'true' : 'false';
                
                // 根据验证结果修改保存按钮文字
                if (data.success) {{
                    saveBtn.textContent = '保存';
                }} else {{
                    saveBtn.textContent = '仍然保存';
                }}
            }} catch (err) {{
                statusArea.innerHTML = '<span class="verify-status error">✗ 验证请求失败: ' + err.message + '</span>';
                saveBtn.textContent = '仍然保存';
                const manualVmSshVerified = document.getElementById('manualVmSshVerified');
                if (manualVmSshVerified) manualVmSshVerified.value = 'false';
            }} finally {{
                verifyBtn.disabled = false;
                verifyBtn.textContent = '验证连接';
            }}
        }}
        
        // 检查手动添加虚拟机是否已验证（允许未验证时保存）
        function checkManualVmVerified() {{
            // 始终允许保存，不再弹窗提示
            return true;
        }}
        
        // 关闭弹窗时重置验证状态
        function closeManualAddModal() {{
            const manualAddModal = document.getElementById('manualAddModal');
            const manualAddForm = document.getElementById('manualAddForm');
            const manualVerifyStatusArea = document.getElementById('manualVerifyStatusArea');
            const manualSaveBtn = document.getElementById('manualSaveBtn');
            
            if (manualAddModal) manualAddModal.classList.remove('active');
            if (manualAddForm) manualAddForm.reset();
            manualVerified = false;
            if (manualVerifyStatusArea) manualVerifyStatusArea.innerHTML = '';
            if (manualSaveBtn) manualSaveBtn.textContent = '保存';
        }}
        
        function updateGpuCount() {{
            const checkedGpus = document.querySelectorAll('.gpu-selection input[type="checkbox"]:checked');
            const gpuCount = document.getElementById('gpuCount');
            if (gpuCount) gpuCount.value = checkedGpus.length;
        }}
        
        // 显示性能监控面板
        // 当前显示性能的服务器ID
        let currentPerfServerId = null;
        // 日志缓存：{{serverId: {{logs: string, timestamp: number}}}}
        let logsCache = {{}};
        
        // 用户菜单切换
        function toggleUserMenu() {{
            const dropdown = document.getElementById('userMenuDropdown');
            dropdown.classList.toggle('active');
        }}
        
        // 点击外部关闭用户菜单
        document.addEventListener('click', function(e) {{
            const menu = document.querySelector('.user-menu');
            if (menu && !menu.contains(e.target)) {{
                const dropdown = document.getElementById('userMenuDropdown');
                if (dropdown) dropdown.classList.remove('active');
            }}
        }});
        
        // Markdown 渲染函数（简化版）
        function renderMarkdown(text) {{
            // 先处理代码块，避免代码块内的内容被其他规则处理
            let codeBlocks = [];
            text = text.replace(/```([\\s\\S]*?)```/g, function(match, code) {{
                codeBlocks.push('<pre style="background:#f5f5f5;padding:6px 8px;border-radius:4px;overflow-x:auto;margin:4px 0;font-size:13px;"><code>' + 
                    code.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;') + 
                    '</code></pre>');
                return '\\x00CODEBLOCK' + (codeBlocks.length - 1) + '\\x00';
            }});
            
            // 处理换行：将多个连续换行合并为1个，然后删除标题/列表前后的换行
            text = text.replace(/\\n\\n+/g, '\\n');
            
            return text
                // 转义 HTML 特殊字符（代码块已处理）
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                // 行内代码 `...`
                .replace(/`([^`]+)`/g, '<code style="background:#f5f5f5;padding:1px 4px;border-radius:3px;font-family:monospace;font-size:13px;">$1</code>')
                // 标题 ## ...（紧凑样式，下边距很小）
                .replace(/^## (.*$)/gim, '<h2 style="font-size:16px;font-weight:700;margin:8px 0 2px;color:#333;border-bottom:1px solid #eee;padding-bottom:2px;">$1</h2>')
                .replace(/^### (.*$)/gim, '<h3 style="font-size:14px;font-weight:600;margin:6px 0 2px;color:#444;">$1</h3>')
                // 粗体 **...**
                .replace(/\\*\\*(.*?)\\*\\*/g, '<strong style="font-weight:600;">$1</strong>')
                // 斜体 *...*
                .replace(/\\*([^*]+)\\*/g, '<em>$1</em>')
                // 列表项 - ...（更紧凑的边距）
                .replace(/^- (.*$)/gim, '<li style="margin:1px 0;margin-left:20px;line-height:1.5;">$1</li>')
                // 删除标题和列表标记后面的换行（避免空行）
                .replace(/(<h[23][^>]*>)<br>/gi, '$1')
                .replace(/(<li[^>]*>)<br>/gi, '$1')
                // 单个换行转为 <br>（但为了紧凑，相邻的br合并）
                .replace(/\\n/g, '<br>')
                .replace(/(<br>)+/g, '<br>')
                // 恢复代码块
                .replace(/\\x00CODEBLOCK(\\d+)\\x00/g, function(match, index) {{
                    return codeBlocks[parseInt(index)];
                }});
        }}
        
        // 预加载日志（后台获取）
        async function preloadLogs(serverId) {{
            try {{
                const response = await fetch('/api/server_logs?id=' + serverId);
                const data = await response.json();
                if (data.success) {{
                    logsCache[serverId] = {{
                        logs: data.logs,
                        timestamp: Date.now()
                    }};
                    console.log('日志预加载完成:', serverId);
                }} else {{
                    console.log('日志预加载失败:', data.error);
                }}
            }} catch (err) {{
                console.log('日志预加载错误:', err);
            }}
        }}
        
        async function showPerformance(serverId, event) {{
            if (event) event.stopPropagation();
            
            // 如果切换到不同的服务器，隐藏AI诊断对话框
            if (currentPerfServerId && currentPerfServerId !== serverId) {{
                const aiDialog = document.getElementById('aiDiagnosisDialog');
                aiDialog.classList.remove('active');
                // 清空AI诊断内容
                document.getElementById('aiDiagnosisContent').innerHTML = '';
            }}
            
            currentPerfServerId = serverId;
            const panel = document.getElementById('performancePanel');
            const tabLabel = document.getElementById('perfTabLabel');
            const hostnameEl = document.getElementById('perfHostname');
            const statusEl = document.getElementById('perfStatus');
            
            // 显示页签和面板
            tabLabel.classList.add('active');
            panel.classList.add('active');
            hostnameEl.textContent = '加载中...';
            statusEl.textContent = '获取数据';
            statusEl.className = 'performance-status';
            
            // 重置饼图
            updatePieChart('cpu', 0);
            updatePieChart('mem', 0);
            updatePieChart('disk', 0);
            document.getElementById('cpuValue').textContent = '--%';
            document.getElementById('memValue').textContent = '--%';
            document.getElementById('diskValue').textContent = '-- MB/s';
            
            try {{
                const response = await fetch('/api/performance?id=' + serverId);
                const data = await response.json();
                
                if (data.success) {{
                    hostnameEl.textContent = data.hostname;
                    statusEl.textContent = data.status === 'running' ? '运行中' : '已停止';
                    statusEl.className = 'performance-status ' + (data.status === 'running' ? 'running' : 'stopped');
                    
                    const perf = data.performance;
                    
                    updatePieChart('cpu', perf.cpu_usage);
                    updatePieChart('mem', perf.mem_usage);
                    
                    // 使用原始字节值动态显示单位
                    const diskIoBytes = perf.disk_io_bytes || 0;
                    const diskIoMb = diskIoBytes / (1024 * 1024);
                    let diskIoDisplay;
                    if (diskIoMb >= 1) {{
                        diskIoDisplay = diskIoMb.toFixed(1) + ' MB/s';
                    }} else if (diskIoBytes >= 1024) {{
                        const diskIoKb = diskIoBytes / 1024;
                        diskIoDisplay = diskIoKb.toFixed(1) + ' KB/s';
                    }} else if (diskIoBytes > 0) {{
                        diskIoDisplay = diskIoBytes.toFixed(0) + ' B/s';
                    }} else {{
                        diskIoDisplay = '0 KB/s';
                    }}
                    // 饼图百分比：基于 MB/s 值，假设 100MB/s 为100%
                    const diskIoPercent = Math.min(diskIoMb / 100 * 100, 100);
                    updatePieChart('disk', diskIoPercent);
                    
                    document.getElementById('cpuValue').textContent = perf.cpu_usage.toFixed(1) + '%';
                    document.getElementById('memValue').textContent = perf.mem_usage.toFixed(1) + '% (' + perf.mem_used.toFixed(1) + '/' + perf.mem_total.toFixed(1) + ' GB)';
                    document.getElementById('diskValue').textContent = diskIoDisplay;
                    
                    // 后台预加载日志（如果缓存中没有），为AI诊断做准备
                    if (!logsCache[serverId]) {{
                        preloadLogs(serverId);
                    }}
                }} else {{
                    hostnameEl.textContent = '获取失败';
                    statusEl.textContent = data.error || '未知错误';
                    statusEl.className = 'performance-status stopped';
                }}
            }} catch (err) {{
                hostnameEl.textContent = '请求失败';
                statusEl.textContent = err.message;
                statusEl.className = 'performance-status stopped';
            }}
        }}
        
        // 刷新按钮事件
        document.getElementById('perfRefreshBtn').addEventListener('click', function() {{
            if (currentPerfServerId) {{
                showPerformance(currentPerfServerId, null);
            }}
        }});
        
        // 关闭按钮事件
        document.getElementById('perfClearBtn').addEventListener('click', function() {{
            const panel = document.getElementById('performancePanel');
            const tabLabel = document.getElementById('perfTabLabel');
            panel.classList.remove('active');
            tabLabel.classList.remove('active');
            currentPerfServerId = null;
            // 关闭 AI 诊断对话框
            document.getElementById('aiDiagnosisDialog').classList.remove('active');
        }});
        
        // AI 诊断按钮事件
        document.getElementById('perfAiDiagnosisBtn').addEventListener('click', async function() {{
            if (!currentPerfServerId) return;
            
            const dialog = document.getElementById('aiDiagnosisDialog');
            const content = document.getElementById('aiDiagnosisContent');
            const btn = document.getElementById('perfAiDiagnosisBtn');
            
            // 显示对话框
            dialog.classList.add('active');
            btn.disabled = true;
            
            // 显示统一的分析提示
            content.innerHTML = '<span style="color:#666;">正在调用大模型分析日志...</span><span class="typing-cursor"></span>';
            
            try {{
                // 调用 AI 诊断 API（流式响应）
                const response = await fetch('/api/ai_diagnosis?id=' + currentPerfServerId);
                
                if (!response.ok) {{
                    content.innerHTML = '<span style="color:#f44336;">获取诊断失败：' + response.statusText + '</span>';
                    btn.disabled = false;
                    return;
                }}
                
                content.innerHTML = '<span class="typing-cursor"></span>';
                let currentText = '';
                
                // 获取 reader 进行流式读取
                const reader = response.body.getReader();
                const decoder = new TextDecoder('utf-8');
                
                while (true) {{
                    const {{ done, value }} = await reader.read();
                    if (done) break;
                    
                    const chunk = decoder.decode(value, {{ stream: true }});
                    currentText += chunk;
                    
                    // 使用 Markdown 渲染
                    const displayText = renderMarkdown(currentText);
                    content.innerHTML = displayText + '<span class="typing-cursor"></span>';
                    
                    // 自动滚动到底部
                    content.scrollTop = content.scrollHeight;
                }}
                
                // 移除光标，最终渲染
                content.innerHTML = renderMarkdown(currentText);
                
            }} catch (err) {{
                content.innerHTML = '<span style="color:#f44336;">诊断请求失败：' + err.message + '</span>';
            }} finally {{
                btn.disabled = false;
            }}
        }});
        
        // 收起AI诊断对话框
        document.getElementById('aiDiagnosisCollapse').addEventListener('click', function() {{
            document.getElementById('aiDiagnosisDialog').classList.remove('active');
        }});
        
        // 更新饼图
        function updatePieChart(type, percentage) {{
            const chart = document.querySelector('.pie-chart.' + type + ' .pie-fill');
            if (!chart) return;
            
            // 圆的周长 = 2 * PI * r = 2 * 3.14159 * 40 ≈ 251.2
            const circumference = 251.2;
            const filled = (percentage / 100) * circumference;
            
            chart.style.strokeDasharray = filled + ' ' + circumference;
        }}
    </script>
</body>
</html>"""
        self.send_html(html)

    def render_models_page(self, session):
        """渲染模型管理页面"""
        config = load_config()
        models = config.get('models', [])
        current_model = config.get('current_model', '')
        username = session.get('username', 'Unknown')
        
        # 预定义公共模型选项
        public_model_options = '''
            <option value="">请选择模型</option>
            <option value="deepseek-chat">DeepSeek Chat</option>
            <option value="kimi-latest">Kimi Latest</option>
            <option value="minimax-text-01">MiniMax</option>
        '''
        
        # 生成模型列表HTML
        models_html = ''
        for model in models:
            model_id = model.get('id', '')
            model_name = model.get('name', '')
            model_type = model.get('type', 'public')
            model_type_label = '公共' if model_type == 'public' else '本地'
            model_type_class = 'model-type-public' if model_type == 'public' else 'model-type-local'
            is_selected = 'selected' if model_id == current_model else ''
            is_checked = 'checked' if model_id == current_model else ''
            
            models_html += f'''
            <div class="model-card {is_selected}" data-model-id="{model_id}">
                <input type="radio" name="selected_model" value="{model_id}" class="model-radio" {is_checked} onchange="selectModel('{model_id}')">
                <div class="model-info">
                    <div class="model-name">{model_name}<span class="model-type {model_type_class}">{model_type_label}</span></div>
                    <div class="model-details">Model: {model.get('model', '')} | URL: {model.get('base_url', '')}</div>
                </div>
                <div class="model-actions">
                    <button class="btn-model-test" onclick="testModel(event, '{model_id}')">验证</button>
                    <button class="btn-model-delete" onclick="deleteModel(event, '{model_id}')">删除</button>
                </div>
            </div>
            '''
        
        if not models_html:
            models_html = '<div style="text-align:center;color:#888;padding:40px;">暂无模型，请添加</div>'
        
        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>模型管理 - 服务器智能管理系统</title>
    <style>{CSS}</style>
</head>
<body>
    <div class="header">
        <h1>服务器智能管理系统 <span class="role-badge admin">管理员</span></h1>
        <div class="user-menu">
            <div class="user-menu-trigger" onclick="toggleUserMenu()">
                <span>{username}</span>
                <span>▲</span>
            </div>
            <div class="user-menu-dropdown" id="userMenuDropdown">
                <a href="/models" class="user-menu-item admin-only">模型管理</a>
                <a href="/logout" class="user-menu-item">退出登录</a>
            </div>
        </div>
    </div>
    <div class="models-container">
        <a href="/dashboard" class="back-link">← 返回控制台</a>
        <div class="models-header">
            <h2>模型管理</h2>
        </div>
        
        <h3 style="margin-bottom:15px;color:#555;">已注册模型（点击单选使用）</h3>
        <div id="modelsList">
            {models_html}
        </div>
        
        <div class="add-model-section">
            <h3>添加新模型</h3>
            <div class="model-tabs">
                <button type="button" class="model-tab active" onclick="switchTab('public')" id="tabPublic">公共模型</button>
                <button type="button" class="model-tab" onclick="switchTab('local')" id="tabLocal">本地模型</button>
            </div>
            
            <form id="addModelForm">
                <div class="model-form-row">
                    <div class="model-form-group">
                        <label>模型显示名称 *</label>
                        <input type="text" id="modelName" placeholder="例如：我的DeepSeek" required>
                    </div>
                    <div class="model-form-group" id="publicModelGroup">
                        <label>模型 *</label>
                        <select id="publicModel">
                            {public_model_options}
                        </select>
                    </div>
                    <div class="model-form-group" id="localModelGroup" style="display:none;">
                        <label>Model *</label>
                        <input type="text" id="localModel" placeholder="例如：llama2-7b">
                    </div>
                </div>
                
                <div class="model-form-row" id="localUrlRow" style="display:none;">
                    <div class="model-form-group" style="flex:2;">
                        <label>Base URL *</label>
                        <input type="text" id="baseUrl" placeholder="例如：http://localhost:11434/v1">
                    </div>
                </div>
                
                <div class="model-form-row">
                    <div class="model-form-group">
                        <label>API Key {f'<span id="apiKeyRequired">*</span>' if True else ''}</label>
                        <input type="password" id="apiKey" placeholder="输入API Key">
                    </div>
                </div>
                
                <button type="submit" class="btn-add-model">添加模型</button>
                <span id="addModelMsg" style="margin-left:15px;"></span>
            </form>
        </div>
    </div>
    
    <script>
        // 用户菜单切换
        function toggleUserMenu() {{
            const dropdown = document.getElementById('userMenuDropdown');
            dropdown.classList.toggle('active');
        }}
        
        document.addEventListener('click', function(e) {{
            const menu = document.querySelector('.user-menu');
            if (menu && !menu.contains(e.target)) {{
                const dropdown = document.getElementById('userMenuDropdown');
                if (dropdown) dropdown.classList.remove('active');
            }}
        }});
        
        // 切换标签页
        let currentTab = 'public';
        function switchTab(tab) {{
            currentTab = tab;
            document.getElementById('tabPublic').classList.toggle('active', tab === 'public');
            document.getElementById('tabLocal').classList.toggle('active', tab === 'local');
            document.getElementById('publicModelGroup').style.display = tab === 'public' ? 'block' : 'none';
            document.getElementById('localModelGroup').style.display = tab === 'local' ? 'block' : 'none';
            document.getElementById('localUrlRow').style.display = tab === 'local' ? 'flex' : 'none';
            document.getElementById('apiKeyRequired').style.display = tab === 'public' ? 'inline' : 'none';
        }}
        
        // 选择模型
        async function selectModel(modelId) {{
            try {{
                const response = await fetch('/api/models/select', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{model_id: modelId}})
                }});
                const data = await response.json();
                if (data.success) {{
                    document.querySelectorAll('.model-card').forEach(card => card.classList.remove('selected'));
                    document.querySelector(`[data-model-id="${{modelId}}"]`).classList.add('selected');
                }} else {{
                    alert('选择模型失败：' + (data.error || '未知错误'));
                }}
            }} catch (e) {{
                alert('请求失败：' + e.message);
            }}
        }}
        
        // 测试模型
        async function testModel(event, modelId) {{
            const btn = event.target;
            const originalText = btn.textContent;
            btn.textContent = '验证中...';
            btn.disabled = true;
            
            // 创建超时控制
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), 15000); // 15秒超时
            
            try {{
                const response = await fetch('/api/models/test', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{model_id: modelId}}),
                    signal: controller.signal
                }});
                clearTimeout(timeoutId);
                
                if (!response.ok) {{
                    alert('验证失败：服务器返回错误 ' + response.status);
                    return;
                }}
                
                const data = await response.json();
                if (data.success) {{
                    alert('验证成功！模型可正常访问。');
                }} else {{
                    alert('验证失败：' + (data.error || '未知错误'));
                }}
            }} catch (e) {{
                if (e.name === 'AbortError') {{
                    alert('验证超时：请求超过15秒未响应，请检查网络或模型配置');
                }} else {{
                    alert('请求失败：' + e.message);
                }}
            }} finally {{
                clearTimeout(timeoutId);
                btn.textContent = originalText;
                btn.disabled = false;
            }}
        }}
        
        // 删除模型
        async function deleteModel(event, modelId) {{
            if (!confirm('确定要删除此模型吗？')) return;
            
            try {{
                const response = await fetch('/api/models/delete', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{model_id: modelId}})
                }});
                const data = await response.json();
                if (data.success) {{
                    location.reload();
                }} else {{
                    alert('删除失败：' + (data.error || '未知错误'));
                }}
            }} catch (e) {{
                alert('请求失败：' + e.message);
            }}
        }}
        
        // 添加模型表单提交
        document.getElementById('addModelForm').addEventListener('submit', async function(e) {{
            e.preventDefault();
            const msgEl = document.getElementById('addModelMsg');
            msgEl.textContent = '';
            
            const name = document.getElementById('modelName').value.trim();
            const apiKey = document.getElementById('apiKey').value.trim();
            
            let model, baseUrl;
            if (currentTab === 'public') {{
                model = document.getElementById('publicModel').value;
                // 根据选择的模型设置URL
                const urlMap = {{
                    'deepseek-chat': 'https://api.deepseek.com',
                    'kimi-latest': 'https://api.moonshot.cn',
                    'minimax-text-01': 'https://api.minimax.chat'
                }};
                baseUrl = urlMap[model];
                if (!model) {{
                    msgEl.textContent = '请选择一个公共模型';
                    msgEl.style.color = '#f44336';
                    return;
                }}
            }} else {{
                model = document.getElementById('localModel').value.trim();
                baseUrl = document.getElementById('baseUrl').value.trim();
            }}
            
            if (!name || !model || (currentTab === 'local' && !baseUrl)) {{
                msgEl.textContent = '请填写所有必填项';
                msgEl.style.color = '#f44336';
                return;
            }}
            
            if (currentTab === 'public' && !apiKey) {{
                msgEl.textContent = '公共模型需要API Key';
                msgEl.style.color = '#f44336';
                return;
            }}
            
            try {{
                const response = await fetch('/api/models/add', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{
                        name: name,
                        model: model,
                        api_key: apiKey,
                        base_url: baseUrl,
                        type: currentTab
                    }})
                }});
                const data = await response.json();
                if (data.success) {{
                    msgEl.textContent = '添加成功！';
                    msgEl.style.color = '#4caf50';
                    setTimeout(() => location.reload(), 1000);
                }} else {{
                    msgEl.textContent = '添加失败：' + (data.error || '未知错误');
                    msgEl.style.color = '#f44336';
                }}
            }} catch (e) {{
                msgEl.textContent = '请求失败：' + e.message;
                msgEl.style.color = '#f44336';
            }}
        }});
    </script>
</body>
</html>"""
        self.send_html(html)

    def render_edit_form(self, server_id):
        data = load_data()
        server = None
        for s in data['servers']:
            if s['id'] == server_id:
                server = s
                break
        
        if not server:
            self.send_response(302)
            self.send_header('Location', '/dashboard')
            self.end_headers()
            return
        
        # 只有虚拟机才需要生成GPU选择框
        gpu_selection_html = ''
        if server['type'] == 'virtual':
            # 获取父物理机的GPU信息
            parent_host = server.get('parent_host', '')
            if parent_host:
                physical_server = get_physical_server_by_hostname(parent_host)
                if physical_server:
                    host_gpu_count = physical_server.get('gpu_count', 0)
                    used_gpus_on_host = get_used_gpus_by_host(parent_host)
                    # 移除当前虚拟机使用的GPU，因为当前虚拟机可以保留自己的GPU
                    for g in server.get('assigned_gpus', []):
                        used_gpus_on_host.discard(g)
                    
                    gpu_sel = []
                    for i in range(1, host_gpu_count + 1):
                        dis = 'disabled' if i in used_gpus_on_host else ''
                        checked = 'checked' if i in server.get('assigned_gpus', []) else ''
                        gpu_sel.append(f'<div class="gpu-checkbox"><input type="checkbox" id="g{i}" name="gpu_{i}" value="{i}" {dis} {checked} onchange="updateGpuCount()"><label for="g{i}" class="gpu-label">GPU{i}</label></div>')
                    gpu_selection_html = ''.join(gpu_sel)
                else:
                    gpu_selection_html = '<p style="color:#888;">无法获取物理机GPU信息</p>'
            else:
                gpu_selection_html = '<p style="color:#888;">未关联物理机</p>'
        else:
            gpu_selection_html = '<p style="color:#888;">物理机不需要分配GPU</p>'
        
        type_physical_selected = 'selected' if server['type'] == 'physical' else ''
        type_virtual_selected = 'selected' if server['type'] == 'virtual' else ''
        
        # 解析内存和磁盘的值和单位
        import re
        mem_match = re.match(r'(\d+)(\w+)', server.get('mem', ''))
        if mem_match:
            mem_value = mem_match.group(1)
            mem_unit = mem_match.group(2)
        else:
            mem_value = server.get('mem', '').replace('GB', '').replace('TB', '')
            mem_unit = 'GB'
        mem_gb_selected = 'selected' if mem_unit == 'GB' else ''
        mem_tb_selected = 'selected' if mem_unit == 'TB' else ''
        
        disk_match = re.match(r'(\d+)(\w+)', server.get('disk', ''))
        if disk_match:
            disk_value = disk_match.group(1)
            disk_unit = disk_match.group(2)
        else:
            disk_value = server.get('disk', '').replace('GB', '').replace('TB', '').replace('PB', '')
            disk_unit = 'TB'
        disk_gb_selected = 'selected' if disk_unit == 'GB' else ''
        disk_tb_selected = 'selected' if disk_unit == 'TB' else ''
        disk_pb_selected = 'selected' if disk_unit == 'PB' else ''
        
        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>修改服务器 - 服务器智能管理系统</title>
    <style>{CSS}</style>
</head>
<body>
    <div class="header">
        <h1>修改服务器信息</h1>
        <a href="/dashboard" style="color:white;text-decoration:none;background:rgba(255,255,255,0.2);padding:10px 20px;border-radius:8px;">返回列表</a>
    </div>
    <div class="container">
        <div class="table-container" style="padding: 30px; max-width: 800px; margin: 0 auto;">
            <form method="POST" action="/update">
                <input type="hidden" name="id" value="{server['id']}">
                <div class="form-row">
                    <div class="form-group">
                        <label>主机名 *</label>
                        <input type="text" name="hostname" value="{server['hostname']}" required>
                    </div>
                    <div class="form-group">
                        <label>类型 *</label>
                        <select name="type" required>
                            <option value="">请选择</option>
                            <option value="physical" {type_physical_selected}>物理机</option>
                            <option value="virtual" {type_virtual_selected}>虚拟机</option>
                        </select>
                    </div>
                </div>
                <div class="form-row">
                    <div class="form-group">
                        <label>用途 *</label>
                        <input type="text" name="purpose" value="{server['purpose']}" required>
                    </div>
                    <div class="form-group">
                        <label>IP地址 *</label>
                        <input type="text" name="ip" value="{server['ip']}" required>
                    </div>
                </div>
                <div class="form-row">
                    <div class="form-group">
                        <label>CPU核心数 *</label>
                        <input type="text" name="cpu" value="{server['cpu']}" required>
                    </div>
                    <div class="form-group">
                        <label>内存 *</label>
                        <div style="display:flex;gap:10px;">
                            <input type="number" name="mem_value" min="1" style="flex:1;" value="{mem_value}" required>
                            <select name="mem_unit" style="width:100px;">
                                <option value="GB" {mem_gb_selected}>GB</option>
                                <option value="TB" {mem_tb_selected}>TB</option>
                            </select>
                        </div>
                    </div>
                </div>
                <div class="form-row">
                    <div class="form-group">
                        <label>磁盘 *</label>
                        <div style="display:flex;gap:10px;">
                            <input type="number" name="disk_value" min="1" style="flex:1;" value="{disk_value}" required>
                            <select name="disk_unit" style="width:100px;">
                                <option value="GB" {disk_gb_selected}>GB</option>
                                <option value="TB" {disk_tb_selected}>TB</option>
                                <option value="PB" {disk_pb_selected}>PB</option>
                            </select>
                        </div>
                    </div>
                    <div class="form-group">
                        <label>GPU数量 *</label>
                        <input type="number" name="gpu_count" id="gpuCount" min="0" max="8" value="{server['gpu_count']}" required {'readonly' if server["type"] == "virtual" else ''} style="{'background:#f0f0f0;cursor:not-allowed;' if server['type'] == 'virtual' else ''}">
                        {'<small style="color:#666;">由分配的GPU自动计算</small>' if server["type"] == "virtual" else ''}
                    </div>
                </div>
                <div class="form-group">
                    <label>用途详情</label>
                    <textarea name="purpose_detail">{server.get('purpose_detail', '')}</textarea>
                </div>
                {'<div class="form-group"><label>分配GPU (自动计算数量)</label><div class="gpu-selection">' + gpu_selection_html + '</div></div>' if server["type"] == "virtual" else ''}
                <div class="form-group">
                    <label>使用人 *</label>
                    <input type="text" name="user" value="{server['user']}" required>
                </div>
                <div style="display:flex;justify-content:flex-end;gap:15px;margin-top:30px;">
                    <a href="/dashboard" class="btn-cancel" style="text-decoration:none;display:inline-block;text-align:center;">取消</a>
                    <button type="submit" class="btn-save">保存修改</button>
                </div>
            </form>
        </div>
    </div>
    <script>
        function updateGpuCount() {{
            const checkedGpus = document.querySelectorAll('.gpu-selection input[type="checkbox"]:checked');
            document.getElementById('gpuCount').value = checkedGpus.length;
        }}
    </script>
</body>
</html>"""
        self.send_html(html)

if __name__ == '__main__':
    if not os.path.exists(DATA_FILE):
        save_data({'servers': []})
    if not os.path.exists(USERS_FILE):
        save_users({'admin': {'password': '123456', 'role': 'admin'}})
    
    # 从环境变量读取配置，支持内网部署自定义
    port = int(os.environ.get('PORT', '8080'))
    host = os.environ.get('HOST', '0.0.0.0')  # 默认绑定所有接口，支持内网访问
    
    print('=' * 50)
    print('服务器智能管理系统 v2.0')
    print('=' * 50)
    print(f'访问地址: http://{host}:{port}')
    if host == '0.0.0.0':
        print('         http://127.0.0.1:' + str(port))
    print('=' * 50)
    print('按 Ctrl+C 停止服务器')
    print('=' * 50)
    
    try:
        server = HTTPServer((host, port), Handler)
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n服务器已停止')
    except Exception as e:
        print(f'\n启动失败: {e}')
        print('请检查端口是否被占用，或尝试设置其他端口:')
        print(f'  set PORT=8081')
        print(f'  python server_v2.py')
