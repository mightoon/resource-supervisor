import json
import os
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
import uuid
import re

# 加载配置文件
def load_config():
    config_file = 'config.json'
    default_config = {
        "proxmox": {
            "host": "192.168.100.160",
            "user": "root@pam",
            "password": "xxxx",
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
PROXMOX_PASSWORD = PROXMOX_CONFIG.get('password', 'xxxx')
PROXMOX_VERIFY_SSL = PROXMOX_CONFIG.get('verify_ssl', False)

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
.header { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 20px 40px; display: flex; justify-content: space-between; align-items: center; }
.header h1 { font-size: 24px; }
.container { max-width: 1400px; margin: 0 auto; padding: 30px 40px; }
.btn-add { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; border: none; padding: 14px 28px; border-radius: 10px; cursor: pointer; font-size: 15px; font-weight: 600; margin-right: 10px; }
.btn-add:hover { transform: translateY(-2px); box-shadow: 0 8px 25px rgba(102, 126, 234, 0.4); }
.btn-batch { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; border: none; padding: 14px 28px; border-radius: 10px; cursor: pointer; font-size: 15px; font-weight: 600; margin-right: 10px; }
.btn-batch:hover { transform: translateY(-2px); box-shadow: 0 8px 25px rgba(102, 126, 234, 0.4); }
.btn-batch-delete { background: #f44336; color: white; border: none; padding: 14px 28px; border-radius: 10px; cursor: pointer; font-size: 15px; font-weight: 600; margin-right: 10px; }
.btn-batch-delete:hover { background: #d32f2f; }
.btn-add-vm { background: #4caf50; color: white; border: none; padding: 14px 28px; border-radius: 10px; cursor: pointer; font-size: 15px; font-weight: 600; margin-right: 10px; }
.btn-add-vm:hover { background: #388e3c; }
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
.empty-state { text-align: center; padding: 80px 40px; color: #888; }
.stats { display: flex; gap: 20px; margin-bottom: 25px; }
.stat-card { background: white; padding: 15px 25px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0, 0, 0, 0.05); }
.stat-label { font-size: 12px; color: #888; margin-bottom: 5px; }
.stat-value { font-size: 24px; font-weight: 700; color: #333; }
.toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 25px; flex-wrap: wrap; gap: 10px; }
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
                        'reg_type': form.get('reg_type', ['manual'])[0]  # 注册方式: auto/manual
                    }
                    
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

    def render_login(self, error_msg, success_msg):
        msg = error_msg or success_msg or ''
        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>登录 - 服务器资源管理系统</title>
    <style>{CSS}</style>
</head>
<body class="login-body">
    <div class="login-container">
        <h1>服务器资源管理系统</h1>
        <p class="login-subtitle">Server Resource Management System</p>
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
        <div class="hint">默认账号: admin | 密码: 123456</div>
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
    <title>注册 - 服务器资源管理系统</title>
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

        if servers:
            rows = []
            # 构建物理机到虚拟机的映射
            physical_servers = [s for s in servers if s['type'] == 'physical']
            virtual_servers = [s for s in servers if s['type'] == 'virtual']
            
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
                        gtags = '<span style="color:#888;">无GPU</span>'
                    elif has_children and physical_children:
                        # 有GPU且有虚拟机，计算空闲GPU数
                        children = physical_children.get(s['id'], [])
                        assigned_gpu_count = 0
                        for vm in children:
                            assigned_gpu_count += len(vm.get('assigned_gpus', []))
                        free_gpu = gpu_count - assigned_gpu_count
                        gtags = f'<span style="color:#2e7d32;font-weight:600;">尚有{free_gpu}卡空闲</span>'
                    else:
                        # 有GPU但没有虚拟机
                        gtags = '<span style="color:#1976d2;font-weight:600;">物理机使用</span>'
                else:
                    # 虚拟机：获取父物理机名称
                    parent = parent_hostname or s.get('parent_host', '未知')
                    gtags = format_gpu_tags(s.get('assigned_gpus', []), lambda g: f'{parent}_GPU{g}')
                
                detail = s.get('purpose_detail', '').rstrip()  # 只去除末尾空白，保留开头的缩进
                
                # 获取注册方式（auto/manual），默认为manual
                reg_type = s.get('reg_type', 'manual')
                
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
                    <td class="hostname">{s['hostname']}</td>
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
        
        add_button = '<button class="btn-add" id="btnRegisterPhysical" onclick="openNodeInputModal()" title="通过proxmox api自动添加物理机">+ 自动注册物理机</button>' if is_admin else ''
        manual_add_button = '<button class="btn-add" id="btnManualAddPhysical" onclick="openManualAddModal()" style="background: linear-gradient(135deg, #42a5f5 0%, #1976d2 100%);">+ 手动注册物理机</button>' if is_admin else ''
        batch_buttons = '''
            <div class="batch-actions" id="batchActions">
                <button type="button" class="btn-batch" onclick="editSelected()">修改选中</button>
                <button type="button" class="btn-batch-delete" onclick="deleteSelected()">删除选中</button>
            </div>
        ''' if is_admin else ''
        auto_add_vm_button = '<button type="button" class="btn-add-vm" id="btnAutoAddVm" onclick="addVmToHost()" style="display:none; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);">自动添加虚拟机</button>' if is_admin else ''
        manual_add_vm_button = '<button type="button" class="btn-add-vm" id="btnManualAddVm" onclick="openManualAddVmModal()" style="display:none; background: #42a5f5;">手动添加虚拟机</button>' if is_admin else ''

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
                    <h2>手动添加物理机</h2>
                    <button class="btn-close" onclick="closeManualAddModal()">&times;</button>
                </div>
                <form method="POST" action="/add" id="manualAddForm">
                    <input type="hidden" name="type" value="physical">
                    <input type="hidden" name="reg_type" value="manual">
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
                    <div class="modal-footer">
                        <button type="button" class="btn-cancel" onclick="closeManualAddModal()">取消</button>
                        <button type="submit" class="btn-save">保存</button>
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
                    <div class="modal-footer">
                        <button type="button" class="btn-cancel" onclick="closeManualAddVmModal()">取消</button>
                        <button type="submit" class="btn-save">保存</button>
                    </div>
                </form>
            </div>
        </div>'''

        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>服务器资源管理系统 - 控制台</title>
    <style>{CSS}</style>
</head>
<body>
    <div class="header">
        <h1>服务器资源管理系统 <span class="role-badge {role_class}">{role_display}</span></h1>
        <div style="display:flex;align-items:center;gap:20px;">
            <span>{username}</span>
            <a href="/logout" style="color:white;text-decoration:none;background:rgba(255,255,255,0.2);padding:10px 20px;border-radius:8px;">退出登录</a>
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
        <div class="toolbar">
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
        
        function openModal() {{ document.getElementById('addModal').classList.add('active'); }}
        function closeModal() {{ 
            document.getElementById('addModal').classList.remove('active');
            // 重置表单
            document.getElementById('addForm').reset();
        }}
        document.getElementById('addModal').addEventListener('click', (e) => {{ if (e.target === e.currentTarget) closeModal(); }});
        
        // 节点名输入弹窗
        function openNodeInputModal() {{ 
            document.getElementById('nodeInputModal').classList.add('active'); 
            document.getElementById('nodeNameInput').focus();
        }}
        function closeNodeInputModal() {{ 
            document.getElementById('nodeInputModal').classList.remove('active'); 
            document.getElementById('nodeError').style.display = 'none';
            document.getElementById('nodeNameInput').value = '';
        }}
        document.getElementById('nodeInputModal').addEventListener('click', (e) => {{ 
            if (e.target === e.currentTarget) closeNodeInputModal(); 
        }});
        
        // 手动添加物理机弹窗
        function openManualAddModal() {{ 
            document.getElementById('manualAddModal').classList.add('active'); 
            document.getElementById('manualHostname').focus();
        }}
        function closeManualAddModal() {{ 
            document.getElementById('manualAddModal').classList.remove('active'); 
            document.getElementById('manualAddForm').reset();
        }}
        document.getElementById('manualAddModal').addEventListener('click', (e) => {{ 
            if (e.target === e.currentTarget) closeManualAddModal(); 
        }});
        
        // 获取节点信息
        async function fetchNodeInfo() {{
            const nodeName = document.getElementById('nodeNameInput').value.trim();
            const errorDiv = document.getElementById('nodeError');
            
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
                    document.getElementById('physHostname').value = data.info.hostname || nodeName;
                    document.getElementById('physCpu').value = data.info.cpu || '';
                    document.getElementById('physMemValue').value = data.info.mem_value || '';
                    document.getElementById('physMemUnit').value = data.info.mem_unit || 'GB';
                    
                    // 填充磁盘信息并初始化原始GB值
                    const diskValue = data.info.disk_value || '';
                    document.getElementById('physDiskValue').value = diskValue;
                    document.getElementById('physDiskUnit').value = data.info.disk_unit || 'GB';
                    currentDiskValueGB = parseInt(diskValue) || 0;  // 保存原始GB值
                    
                    // 填充IP地址（如果API返回了）
                    if (data.info.ip) {{
                        document.getElementById('physIp').value = data.info.ip;
                    }}
                    
                    // 填充GPU数量（如果API返回了）
                    if (data.info.gpu_count !== undefined) {{
                        document.getElementById('physGpuCount').value = data.info.gpu_count;
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
        
        // 回车键提交
        document.getElementById('nodeNameInput').addEventListener('keypress', function(e) {{
            if (e.key === 'Enter') {{
                e.preventDefault();
                fetchNodeInfo();
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
        
        // 监听数值变化，更新原始GB值
        document.getElementById('physDiskValue').addEventListener('change', function() {{
            const unitSelect = document.getElementById('physDiskUnit');
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
        
        function toggleSelectAll() {{
            const selectAll = document.getElementById('selectAll');
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
                batchActions.classList.add('active');
            }} else {{
                batchActions.classList.remove('active');
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
            document.getElementById('vmGpuCount').value = checkedGpus.length;
        }}
        
        // 更新手动添加虚拟机GPU数量显示
        function updateManualVmGpuCount() {{
            const checkedGpus = document.querySelectorAll('#manualAddVmModal .gpu-selection input[type="checkbox"]:checked');
            document.getElementById('manualVmGpuCount').value = checkedGpus.length;
        }}
        
        // 填充虚拟机表单
        async function fillVmForm(info, parentHost, vmid) {{
            document.getElementById('vmParentHost').value = parentHost;
            document.getElementById('vmProxmoxVmid').value = vmid;
            document.getElementById('vmHostname').value = info.hostname || '';
            document.getElementById('vmIp').value = info.ip || '';
            document.getElementById('vmCpu').value = info.cpu || '';
            document.getElementById('vmMemValue').value = info.mem_value || '';
            document.getElementById('vmMemUnit').value = info.mem_unit || 'GB';
            document.getElementById('vmDiskValue').value = info.disk_value || '';
            document.getElementById('vmDiskUnit').value = info.disk_unit || 'GB';
            
            // 获取物理机GPU信息并生成选择框
            try {{
                const response = await fetch('/api/host_gpu_info?hostname=' + encodeURIComponent(parentHost));
                const data = await response.json();
                if (data.success) {{
                    const gpuSelectionHtml = generateGpuSelection(data.gpu_count, data.used_gpus, 'gpu', 'updateVmGpuCount');
                    document.getElementById('vmGpuSelection').innerHTML = gpuSelectionHtml;
                    document.getElementById('vmGpuCount').value = 0;
                }} else {{
                    document.getElementById('vmGpuSelection').innerHTML = '<p style="color:#888;">无法获取GPU信息</p>';
                }}
            }} catch (err) {{
                document.getElementById('vmGpuSelection').innerHTML = '<p style="color:#888;">获取GPU信息失败</p>';
            }}
        }}
        
        // 打开添加虚拟机弹窗
        function openAddVmModal() {{
            document.getElementById('addVmModal').classList.add('active');
        }}
        
        // 关闭添加虚拟机弹窗
        function closeAddVmModal() {{
            document.getElementById('addVmModal').classList.remove('active');
            document.getElementById('addVmForm').reset();
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
            document.getElementById('manualVmParentHost').value = hostname;
            
            // 获取物理机GPU信息并生成选择框
            try {{
                const response = await fetch('/api/host_gpu_info?hostname=' + encodeURIComponent(hostname));
                const data = await response.json();
                if (data.success) {{
                    const gpuSelectionHtml = generateGpuSelection(data.gpu_count, data.used_gpus, 'gpu', 'updateManualVmGpuCount');
                    document.getElementById('manualVmGpuSelection').innerHTML = gpuSelectionHtml;
                    document.getElementById('manualVmGpuCount').value = 0;
                }} else {{
                    document.getElementById('manualVmGpuSelection').innerHTML = '<p style="color:#888;">无法获取GPU信息</p>';
                }}
            }} catch (err) {{
                document.getElementById('manualVmGpuSelection').innerHTML = '<p style="color:#888;">获取GPU信息失败</p>';
            }}
            
            // 打开弹窗
            document.getElementById('manualAddVmModal').classList.add('active');
            document.getElementById('manualVmHostname').focus();
        }}
        
        function closeManualAddVmModal() {{
            document.getElementById('manualAddVmModal').classList.remove('active');
            document.getElementById('manualAddVmForm').reset();
        }}
        
        function updateGpuCount() {{
            const checkedGpus = document.querySelectorAll('.gpu-selection input[type="checkbox"]:checked');
            document.getElementById('gpuCount').value = checkedGpus.length;
        }}
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
    <title>修改服务器 - 服务器资源管理系统</title>
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
                        <input type="number" name="gpu_count" id="gpuCount" min="0" max="8" value="{server['gpu_count']}" required>
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
</body>
</html>"""
        self.send_html(html)

if __name__ == '__main__':
    if not os.path.exists(DATA_FILE):
        save_data({'servers': []})
    if not os.path.exists(USERS_FILE):
        save_users({'admin': {'password': '123456', 'role': 'admin'}})
    
    print('=' * 50)
    print('服务器资源管理系统 v2.0')
    print('=' * 50)
    print('访问地址: http://127.0.0.1:5000')
    print('默认账号: admin / 123456')
    print('=' * 50)
    print('按 Ctrl+C 停止服务器')
    print('=' * 50)
    
    server = HTTPServer(('127.0.0.1', 5000), Handler)
    server.serve_forever()
