import json
import os
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
import uuid

DATA_FILE = 'servers.json'
USERS_FILE = 'users.json'
sessions = {}

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {'servers': []}

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
    data = load_data()
    used = set()
    for s in data['servers']:
        for g in s.get('assigned_gpus', []):
            used.add(g)
    return used

CSS = """
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #f5f7fa; min-height: 100vh; }
.header { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 20px 40px; display: flex; justify-content: space-between; align-items: center; }
.header h1 { font-size: 24px; }
.container { max-width: 1400px; margin: 0 auto; padding: 30px 40px; }
.btn-add { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; border: none; padding: 14px 28px; border-radius: 10px; cursor: pointer; font-size: 15px; font-weight: 600; margin-right: 10px; }
.btn-add:hover { transform: translateY(-2px); box-shadow: 0 8px 25px rgba(102, 126, 234, 0.4); }
.btn-batch { background: #ff9800; color: white; border: none; padding: 14px 28px; border-radius: 10px; cursor: pointer; font-size: 15px; font-weight: 600; margin-right: 10px; }
.btn-batch:hover { background: #f57c00; }
.btn-batch-delete { background: #f44336; color: white; border: none; padding: 14px 28px; border-radius: 10px; cursor: pointer; font-size: 15px; font-weight: 600; }
.btn-batch-delete:hover { background: #d32f2f; }
.btn-batch:disabled, .btn-batch-delete:disabled { background: #ccc; cursor: not-allowed; }
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
.gpu-tag { background: #e8f5e9; color: #2e7d32; padding: 4px 10px; border-radius: 4px; font-size: 12px; font-weight: 600; margin-right: 5px; }
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
                    new_server = {
                        'id': len(data['servers']) + 1,
                        'hostname': form.get('hostname', [''])[0],
                        'type': form.get('type', [''])[0],
                        'purpose': form.get('purpose', [''])[0],
                        'purpose_detail': form.get('purpose_detail', [''])[0],
                        'ip': form.get('ip', [''])[0],
                        'cpu': form.get('cpu', [''])[0],
                        'mem': mem_display,
                        'disk': disk_display,
                        'gpu_count': len(gpus),  # 使用实际选择的GPU数量
                        'assigned_gpus': gpus,
                        'user': form.get('user', [''])[0]
                    }
                    data['servers'].append(new_server)
                    save_data(data)
                    self.send_response(302)
                    self.send_header('Location', '/dashboard')
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
                            s['gpu_count'] = len(gpus)  # 使用实际选择的GPU数量
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
        used_gpus = get_used_gpus()
        is_admin = session.get('role') == 'admin'
        username = session.get('username', '')
        role_display = '管理员' if is_admin else '访客'
        role_class = 'role-admin' if is_admin else 'role-viewer'

        if servers:
            rows = []
            for s in servers:
                tcls = 'type-physical' if s['type'] == 'physical' else 'type-virtual'
                ttxt = '物理机' if s['type'] == 'physical' else '虚拟机'
                gtags = ''.join([f'<span class="gpu-tag">GPU {g}</span>' for g in s.get('assigned_gpus', [])])
                detail = s.get('purpose_detail', '').replace('"', '&quot;')
                checkbox = f'<input type="checkbox" name="server_select" value="{s["id"]}" onchange="updateSelection()">' if is_admin else ''
                row = f'''<tr data-id="{s['id']}">
                    <td class="checkbox-col">{checkbox}</td>
                    <td class="hostname">{s['hostname']}</td>
                    <td><span class="type-badge {tcls}">{ttxt}</span></td>
                    <td title="{detail}">{s['purpose']}</td>
                    <td>{s['ip']}</td>
                    <td>{s['cpu']}</td>
                    <td>{s['mem']}</td>
                    <td>{s['disk']}</td>
                    <td>{s['gpu_count']}</td>
                    <td>{gtags}</td>
                    <td>{s['user']}</td>
                </tr>'''
                rows.append(row)
            table_rows = ''.join(rows)
        else:
            checkbox_header = '<th class="checkbox-col"><input type="checkbox" id="selectAll" onclick="toggleSelectAll()"></th>' if is_admin else ''
            table_rows = f'<tr><td colspan="{12 if is_admin else 11}"><div class="empty-state">暂无服务器记录</div></td></tr>'

        checkbox_header = '<th class="checkbox-col"><input type="checkbox" id="selectAll" onclick="toggleSelectAll()"></th>' if is_admin else ''
        viewer_notice = '<div class="viewer-notice"><p>您当前以访客身份登录，仅可查看服务器信息，无法进行添加、修改或删除操作。</p></div>' if not is_admin else ''
        
        add_button = '<button class="btn-add" onclick="openModal()">+ 添加服务器</button>' if is_admin else ''
        batch_buttons = '''
            <div class="batch-actions" id="batchActions">
                <button type="button" class="btn-batch" onclick="editSelected()">修改选中</button>
                <button type="button" class="btn-batch-delete" onclick="deleteSelected()">删除选中</button>
            </div>
        ''' if is_admin else ''

        gpu_sel = []
        for i in range(8):
            dis = 'disabled' if i in used_gpus else ''
            gpu_sel.append(f'<div class="gpu-checkbox"><input type="checkbox" id="g{i}" name="gpu_{i}" value="{i}" {dis} onchange="updateGpuCount()"><label for="g{i}" class="gpu-label">GPU{i}</label></div>')
        
        gpu_selection_html = ''.join(gpu_sel)
        
        # 构建添加模态框HTML
        add_modal_html = f'''<div class="modal" id="addModal">
            <div class="modal-content">
                <div class="modal-header">
                    <h2>添加服务器</h2>
                    <button class="btn-close" onclick="closeModal()">&times;</button>
                </div>
                <form method="POST" action="/add" id="addForm">
                    <div class="modal-body">
                        <div class="form-row">
                            <div class="form-group">
                                <label>主机名 *</label>
                                <input type="text" name="hostname" required>
                            </div>
                            <div class="form-group">
                                <label>类型 *</label>
                                <select name="type" required>
                                    <option value="">请选择</option>
                                    <option value="physical">物理机</option>
                                    <option value="virtual">虚拟机</option>
                                </select>
                            </div>
                        </div>
                        <div class="form-row">
                            <div class="form-group">
                                <label>用途 *</label>
                                <input type="text" name="purpose" required>
                            </div>
                            <div class="form-group">
                                <label>IP地址 *</label>
                                <input type="text" name="ip" required>
                            </div>
                        </div>
                        <div class="form-row">
                            <div class="form-group">
                                <label>CPU *</label>
                                <input type="text" name="cpu" placeholder="例如: 32核" required>
                            </div>
                            <div class="form-group">
                                <label>内存 *</label>
                                <div style="display:flex;gap:10px;">
                                    <input type="number" name="mem_value" min="1" style="flex:1;" required>
                                    <select name="mem_unit" style="width:100px;">
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
                                    <input type="number" name="disk_value" min="1" style="flex:1;" required>
                                    <select name="disk_unit" style="width:100px;">
                                        <option value="GB">GB</option>
                                        <option value="TB">TB</option>
                                        <option value="PB">PB</option>
                                    </select>
                                </div>
                            </div>
                            <div class="form-group">
                                <label>GPU数量 *</label>
                                <input type="number" name="gpu_count" id="gpuCount" min="0" max="8" value="0" required readonly style="background:#f5f5f5;">
                            </div>
                        </div>
                        <div class="form-group">
                            <label>用途详情</label>
                            <textarea name="purpose_detail" placeholder="详细描述..."></textarea>
                        </div>
                        <div class="form-group">
                            <label>分配GPU (自动计算数量)</label>
                            <div class="gpu-selection">
                                {gpu_selection_html}
                            </div>
                        </div>
                        <div class="form-group">
                            <label>使用人 *</label>
                            <input type="text" name="user" required>
                        </div>
                    </div>
                    <div class="modal-footer">
                        <button type="button" class="btn-cancel" onclick="closeModal()">取消</button>
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
                <div class="stat-label">服务器总数</div>
                <div class="stat-value">{len(servers)}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">GPU已用/总数</div>
                <div class="stat-value">{len(used_gpus)}/8</div>
            </div>
        </div>
        <div class="toolbar">
            <div style="display:flex;align-items:center;">
                {add_button}
                {batch_buttons}
            </div>
        </div>
        <div class="table-container">
            <table>
                <thead>
                    <tr>
                        {checkbox_header}
                        <th>主机名</th>
                        <th>类型</th>
                        <th>用途</th>
                        <th>IP地址</th>
                        <th>CPU</th>
                        <th>内存</th>
                        <th>磁盘</th>
                        <th>GPU</th>
                        <th>分配GPU</th>
                        <th>使用人</th>
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
        function closeModal() {{ document.getElementById('addModal').classList.remove('active'); }}
        document.getElementById('addModal').addEventListener('click', (e) => {{ if (e.target === e.currentTarget) closeModal(); }});
        
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
        
        function updateSelection() {{
            const checkboxes = document.querySelectorAll('input[name="server_select"]:checked');
            selectedIds = Array.from(checkboxes).map(cb => cb.value);
            const batchActions = document.getElementById('batchActions');
            if (selectedIds.length > 0) {{
                batchActions.classList.add('active');
            }} else {{
                batchActions.classList.remove('active');
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
        
        used_gpus = get_used_gpus()
        # 移除当前服务器使用的GPU，因为当前服务器可以保留自己的GPU
        for g in server.get('assigned_gpus', []):
            used_gpus.discard(g)
        
        gpu_sel = []
        for i in range(8):
            dis = 'disabled' if i in used_gpus else ''
            checked = 'checked' if i in server.get('assigned_gpus', []) else ''
            gpu_sel.append(f'<div class="gpu-checkbox"><input type="checkbox" id="g{i}" name="gpu_{i}" value="{i}" {dis} {checked} onchange="updateGpuCount()"><label for="g{i}" class="gpu-label">GPU{i}</label></div>')
        
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
                        <label>CPU *</label>
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
                        <input type="number" name="gpu_count" id="gpuCount" min="0" max="8" value="{server['gpu_count']}" required readonly style="background:#f5f5f5;">
                    </div>
                </div>
                <div class="form-group">
                    <label>用途详情</label>
                    <textarea name="purpose_detail">{server.get('purpose_detail', '')}</textarea>
                </div>
                <div class="form-group">
                    <label>分配GPU (自动计算数量)</label>
                    <div class="gpu-selection">
                        {''.join(gpu_sel)}
                    </div>
                </div>
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
