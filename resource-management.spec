# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['server_v2.py'],
    pathex=[],
    binaries=[],
    datas=[('templates', 'templates'), ('config.json', '.'), ('servers.json', '.'), ('users.json', '.')],
    hiddenimports=['paramiko', 'paramiko.transport', 'paramiko.ssh_exception', 'paramiko.rsakey', 'paramiko.ed25519key', 'proxmoxer', 'proxmoxer.backends', 'proxmoxer.backends.https', 'proxmoxer.backends.openssh', 'proxmoxer.core', 'requests', 'urllib3', 'cryptography', 'cryptography.hazmat.backends', 'bcrypt', 'pynacl', 'idna', 'charset_normalizer'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='服务器智能管理系统',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
