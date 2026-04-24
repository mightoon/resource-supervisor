#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
测试GPU检测逻辑 - 查看cdta节点的PCI设备
"""

from proxmoxer import ProxmoxAPI

# Proxmox API 配置
PROXMOX_HOST = "192.168.100.160"
PROXMOX_USER = "root@pam"
PROXMOX_PASSWORD = "xxx"

def test_gpu_detection():
    """测试GPU检测"""
    try:
        proxmox = ProxmoxAPI(
            PROXMOX_HOST,
            user=PROXMOX_USER,
            password=PROXMOX_PASSWORD,
            verify_ssl=False
        )
        
        node_name = "cdta"
        
        print("=" * 60)
        print(f"测试节点: {node_name}")
        print("=" * 60)
        
        # 获取PCI设备列表
        print("\n获取PCI设备列表...")
        pci_devices = proxmox.nodes(node_name).hardware.pci.get()
        
        print(f"\n总共发现 {len(pci_devices)} 个PCI设备\n")
        print("-" * 60)
        
        # 打印所有设备信息
        for i, device in enumerate(pci_devices, 1):
            print(f"\n设备 {i}:")
            print(f"  设备名: {device.get('device_name', 'N/A')}")
            print(f"  厂商名: {device.get('vendor_name', 'N/A')}")
            print(f"  设备类: {device.get('class_name', 'N/A')}")
            print(f"  厂商ID: {device.get('vendor_id', 'N/A')}")
            print(f"  设备ID: {device.get('device_id', 'N/A')}")
            print(f"  类代码: {device.get('class', 'N/A')}")
            print(f"  ID: {device.get('id', 'N/A')}")
        
        print("\n" + "=" * 60)
        print("GPU检测结果:")
        print("=" * 60)
        
        # GPU 厂商ID映射
        gpu_vendors = {
            '10de': 'NVIDIA',
            '1022': 'AMD',
            '1002': 'AMD',
        }
        
        # GPU 设备类代码
        gpu_class_codes = ['0300', '0302', '0380']
        
        gpu_count = 0
        detected_gpus = []
        
        for device in pci_devices:
            device_name = device.get('device_name', '').lower()
            vendor_name = device.get('vendor_name', '').lower()
            class_name = device.get('class_name', '').lower()
            vendor_id = device.get('vendor_id', '').lower()
            class_code = device.get('class', '').lower()
            
            is_gpu = False
            reason = ""
            
            # 方法1: 通过设备名称精确匹配
            if 'nvidia' in device_name or 'geforce' in device_name or 'tesla' in device_name or 'quadro' in device_name:
                is_gpu = True
                reason = "NVIDIA设备名匹配"
            elif 'amd' in device_name and ('radeon' in device_name or 'mi' in device_name or 'instinct' in device_name):
                is_gpu = True
                reason = "AMD设备名匹配"
            elif 'ati' in device_name and 'radeon' in device_name:
                is_gpu = True
                reason = "ATI设备名匹配"
            # 方法2: 通过厂商ID + 设备类代码匹配
            elif vendor_id in gpu_vendors:
                if any(code in class_code for code in gpu_class_codes):
                    is_gpu = True
                    reason = f"厂商ID匹配({gpu_vendors.get(vendor_id, vendor_id)}) + 类代码匹配"
            # 方法3: 3D控制器
            elif '3d controller' in class_name:
                if any(keyword in device_name for keyword in ['nvidia', 'amd', 'radeon', 'ati']):
                    is_gpu = True
                    reason = "3D控制器 + 关键词匹配"
            
            if is_gpu:
                gpu_count += 1
                detected_gpus.append({
                    'name': device.get('device_name', 'Unknown'),
                    'vendor': device.get('vendor_name', 'Unknown'),
                    'class': device.get('class_name', 'Unknown'),
                    'reason': reason
                })
                print(f"\n✓ 发现GPU #{gpu_count}:")
                print(f"  名称: {device.get('device_name', 'Unknown')}")
                print(f"  厂商: {device.get('vendor_name', 'Unknown')}")
                print(f"  类型: {device.get('class_name', 'Unknown')}")
                print(f"  原因: {reason}")
        
        print(f"\n{'=' * 60}")
        print(f"总计检测到 GPU 数量: {gpu_count}")
        print(f"{'=' * 60}")
        
        if gpu_count == 0:
            print("\n未检测到GPU，可能的设备名关键词:")
            keywords_found = set()
            for device in pci_devices:
                name = device.get('device_name', '').lower()
                if 'vga' in name or '3d' in name or 'display' in name or 'graphic' in name:
                    keywords_found.add(device.get('device_name'))
            if keywords_found:
                for kw in keywords_found:
                    print(f"  - {kw}")
        
    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    test_gpu_detection()
