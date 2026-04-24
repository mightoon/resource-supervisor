import json
import os
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler

DATA_FILE = 'servers.json'

# HTML模板
HTML = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>服务器资源管理系统</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: #f5f7fa;
            min-height: 100vh;
        }
        .header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 20px 40px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .header h1 { font-size: 24px; }
        .container { max-width: 1400px; margin: 0 auto; padding: 30px 40px; }
        .btn-add {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            padding: 14px 28px;
            border-radius: 10px;
            cursor: pointer;
            font-size: 15px;
            font-weight: 600;
            margin-bottom: 25px;
        }}
        .table-container {{
            background: white;
            border-radius: 15px;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.08);
            overflow: hidden;
        }}
        table { width: 100%; border-collapse: collapse; }
        th {
            padding: 18px 20px;
            text-align: left;
            font-weight: 600;
            color: #555;
            font-size: 14px;
            background: #f8f9fa;
            border-bottom: 2px solid #e9ecef;
        }
        td {
            padding: 18px 20px;
            border-bottom: 1px solid #e9ecef;
            font-size: 14px;
            color: #444;
        }
        tr:hover { background: #f8f9fa; }
        .hostname { font-weight: 600; color: #667eea; }
        .type-badge {
            display: inline-block;
            padding: 6px 14px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: 600;
        }
        .type-physical { background: #e3f2fd; color: #1976d2; }
        .type-virtual { background: #f3e5f5; color: #7b1fa2; }
        .gpu-tag {
            background: #e8f5e9;
            color: #2e7d32;
            padding: 4px 10px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: 600;
            margin-right: 5px;
        }
        .modal {
            display: none;
            position: fixed;
            top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(0,0,0,0.5);
            justify-content: center;
            align-items: center;
            z-index: 1000;
        }
        .modal.active { display: flex; }
        .modal-content {
            background: white;
            border-radius: 20px;
            width: 90%;
            max-width: 600px;
            max-height: 90vh;
            overflow-y: auto;
        }
        .modal-header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 25px 30px;
        }
        .modal-body { padding: 30px; }
        .form-row {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
            margin-bottom: 20px;
        }}
        .form-group {{ margin-bottom: 20px; }}
        label {{ display: block; margin-bottom: 8px; color: #555; font-weight: 500; font-size: 14px; }}
        input, select, textarea {
            width: 100%;
            padding: 14px;
            border: 2px solid #e0e0e0;
            border-radius: 10px;
            font-size: 14px;
        }
        textarea { resize: vertical; min-height: 80px; }
        .gpu-selection {
            display: grid;
            grid-template-columns: repeat(8, 1fr);
            gap: 10px;
            margin-top: 10px;
        }
        .gpu-checkbox { position: relative; }
        .gpu-checkbox input { position: absolute; opacity: 0; }
        .gpu-label {
            display: block;
            padding: 12px 5px;
            background: #f5f5f5;
            border: 2px solid #e0e0e0;
            border-radius: 8px;
            text-align: center;
            cursor: pointer;
            font-size: 11px;
        }
        .gpu-checkbox input:checked + .gpu-label {
            background: #e8f5e9;
            border-color: #4caf50;
            color: #2e7d32;
        }
        .gpu-checkbox input:disabled + .gpu-label {
            background: #eeeeee;
            border-color: #bdbdbd;
            color: #9e9e9e;
            cursor: not-allowed;
            opacity: 0.6;
        }
        .modal-footer {
            padding: 20px 30px;
            border-top: 1px solid #e9ecef;
            display: flex;
            justify-content: flex-end;
            gap: 15px;
        }
        .btn-cancel, .btn-save {
            padding: 12px 24px;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-size: 14px;
            font-weight: 600;
        }
        .btn-cancel { background: #f5f5f5; color: #555; }
        .btn-save {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }
        .btn-delete {
            background: #ffebee;
            color: #c62828;
            border: none;
            padding: 8px 14px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 12px;
        }
        .empty-state {
            text-align: center;
            padding: 80px 40px;
            color: #888;
        }
        .stats {
            display: flex;
            gap: 20px;
            margin-bottom: 25px;
        }
        .stat-card {
            background: white;
            padding: 15px 25px;
            border-radius: 10px;
            box-shadow: 0 2px 10px rgba(0, 0, 0, 0.05);
        }
        .stat-label {{ font-size: 12px; color: #888; margin-bottom: 5px; }}
        .stat-value { font-size: 24px; font-weight: 700; color: #333; }
    </style>
</head>
<body>
    <div class="header">
        <h1>服务器资源管理系统</h1>
        <a href="/logout" style="color:white;text-decoration:none;">退出登录</a>
    </div>
    <div class="container">
        <div class="stats">
            <div class="stat-card">
                <div class="stat-label">服务器总数</div>
                <div class="stat-value">{server_count}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">GPU已用/总数</div>
                <div class="stat-value">{gpu_used}/8</div>
            </div>
        </div>
        <button class="btn-add" onclick="openModal()">+ 添加服务器</button>
        <div class="table-container">
            <table>
                <thead>
                    <tr>
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
                        <th>操作</th>
                    </tr>
                </thead>
                <tbody>
                    {table_rows}
                </tbody>
            </table>
        </div>
    </div>
    <div class="modal" id="addModal">
        <div class="modal-content">
            <div class="modal-header">
                <h2>添加服务器</h2>
            </div>
            <form method="POST" action="/add">
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
                            <input type="text" name="mem" placeholder="例如: 128GB" required>
                        </div>
                    </div>
                    <div class="form-row">
                        <div class="form-group">
                            <label>磁盘 *</label>
                            <input type="text" name="disk" placeholder="例如: 2TB" required>
                        </div>
                        <div class="form-group">
                            <label>GPU数量 *</label>
                            <input type="number" name="gpu_count" min="0" max="8" value="0" required>
                        </div>
                    </div>
                    <div class="form-group">
                        <label>用途详情</label>
                        <textarea name="purpose_detail" placeholder="详细描述..."></textarea>
                    </div>
                    <div class="form-group">
                        <label>分配GPU</label>
                        <div class="gpu-selection">
                            {gpu_selection}
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
    </div>
    <script>
        function openModal() {{ document.getElementById('addModal').classList.add('active'); }}
        function closeModal() {{ document.getElementById('addModal').classList.remove('active'); }}
        document.getElementById('addModal').addEventListener('click', (e) => {{
            if (e.target === e.currentTarget) closeModal();
        }});
    </script>
</body>
</html>'''

LOGIN_HTML = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>登录 - 服务器资源管理系统</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
        }
        .login-container {
            background: white;
            padding: 50px;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
            width: 100%;
            max-width: 420px;
            text-align: center;
        }
        h1 {{ color: #333; margin-bottom: 30px; font-size: 24px; }}
        .form-group {{ margin-bottom: 25px; text-align: left; }}
        label {{ display: block; margin-bottom: 8px; color: #555; font-weight: 500; }}
        input {
            width: 100%;
            padding: 15px;
            border: 2px solid #e0e0e0;
            border-radius: 10px;
            font-size: 15px;
        }
        input:focus {
            outline: none;
            border-color: #667eea;
        }
        .btn-login {
            width: 100%;
            padding: 16px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            border-radius: 10px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            margin-top: 10px;
        }
        .error {
            background: #ffebee;
            color: #c62828;
            padding: 12px;
            border-radius: 8px;
            margin-bottom: 20px;
            font-size: 14px;
        }
        .hint { margin-top: 20px; color: #999; font-size: 12px; }
    </style>
</head>
<body>
    <div class="login-container">
        <h1>服务器资源管理系统</h1>
        {error}
        <form method="POST" action="/login">
            <div class="form-group">
                <label>用户名</label>
                <input type="text" name="username" placeholder="admin" required>
            </div>
            <div class="form-group">
                <label>密码</label>
                <input type="password" name="password" placeholder="123456" required>
            </div>
            <button type="submit" class="btn-login">登 录</button>
        </form>
        <div class="hint">默认账号: admin | 密码: 123456</div>
    </div>
</body>
</html>'''

sessions = {}

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {'servers': []}

def save_data(data):
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_used_gpus():
    data = load_data()
    used = set()
    for s in data['servers']:
        for g in s.get('assigned_gpus', []):
            used.add(g)
    return used

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

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
                self.send_html(LOGIN_HTML.format(error=''))

        elif path == '/dashboard':
            if not session:
                self.send_response(302)
                self.send_header('Location', '/login')
                self.end_headers()
            else:
                self.render_dashboard()

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
            if user == 'admin' and pwd == '123456':
                import uuid
                sid = str(uuid.uuid4())
                sessions[sid] = {'user': user}
                self.send_response(302)
                self.send_header('Location', '/dashboard')
                self.send_header('Set-Cookie', f'session={sid}; Path=/')
                self.end_headers()
            else:
                self.send_html(LOGIN_HTML.format(error='<div class="error">用户名或密码错误</div>'))

        elif path == '/add':
            if not session:
                self.send_response(302)
                self.send_header('Location', '/login')
                self.end_headers()
            else:
                gpus = []
                for key in form:
                    if key.startswith('gpu_'):
                        gpus.append(int(key.replace('gpu_', '')))
                
                data = load_data()
                new_server = {
                    'id': len(data['servers']) + 1,
                    'hostname': form.get('hostname', [''])[0],
                    'type': form.get('type', [''])[0],
                    'purpose': form.get('purpose', [''])[0],
                    'purpose_detail': form.get('purpose_detail', [''])[0],
                    'ip': form.get('ip', [''])[0],
                    'cpu': form.get('cpu', [''])[0],
                    'mem': form.get('mem', [''])[0],
                    'disk': form.get('disk', [''])[0],
                    'gpu_count': int(form.get('gpu_count', ['0'])[0]),
                    'assigned_gpus': gpus,
                    'user': form.get('user', [''])[0]
                }
                data['servers'].append(new_server)
                save_data(data)
                self.send_response(302)
                self.send_header('Location', '/dashboard')
                self.end_headers()

    def render_dashboard(self):
        data = load_data()
        servers = data['servers']
        used_gpus = get_used_gpus()

        if servers:
            rows = []
            for s in servers:
                tcls = 'type-physical' if s['type'] == 'physical' else 'type-virtual'
                ttxt = '物理机' if s['type'] == 'physical' else '虚拟机'
                gtags = ''.join([f'<span class="gpu-tag">GPU {g}</span>' for g in s.get('assigned_gpus', [])])
                row = f'''<tr>
                    <td class="hostname">{s['hostname']}</td>
                    <td><span class="type-badge {tcls}">{ttxt}</span></td>
                    <td title="{s.get('purpose_detail', '')}">{s['purpose']}</td>
                    <td>{s['ip']}</td>
                    <td>{s['cpu']}</td>
                    <td>{s['mem']}</td>
                    <td>{s['disk']}</td>
                    <td>{s['gpu_count']}</td>
                    <td>{gtags}</td>
                    <td>{s['user']}</td>
                    <td><a href="/delete?id={s['id']}" class="btn-delete" onclick="return confirm('删除?')">删除</a></td>
                </tr>'''
                rows.append(row)
            table_rows = ''.join(rows)
        else:
            table_rows = '<tr><td colspan="11"><div class="empty-state">暂无服务器记录</div></td></tr>'

        gpu_sel = []
        for i in range(8):
            dis = 'disabled' if i in used_gpus else ''
            gpu_sel.append(f'<div class="gpu-checkbox"><input type="checkbox" id="g{i}" name="gpu_{i}" value="{i}" {dis}><label for="g{i}" class="gpu-label">GPU{i}</label></div>')

        html = HTML.format(
            server_count=len(servers),
            gpu_used=len(used_gpus),
            table_rows=table_rows,
            gpu_selection=''.join(gpu_sel)
        )
        self.send_html(html)

if __name__ == '__main__':
    if not os.path.exists(DATA_FILE):
        save_data({'servers': []})
    
    print('=' * 50)
    print('服务器资源管理系统')
    print('=' * 50)
    print('访问地址: http://127.0.0.1:5000')
    print('登录账号: admin')
    print('登录密码: 123456')
    print('=' * 50)
    print('按 Ctrl+C 停止服务器')
    print('=' * 50)
    
    server = HTTPServer(('127.0.0.1', 5000), Handler)
    server.serve_forever()
