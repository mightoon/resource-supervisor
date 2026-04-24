import json
import os
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler

# 数据文件路径
DATA_FILE = 'servers.json'

# 管理员账号
ADMIN_USER = 'admin'
ADMIN_PASS = '123456'

# GPU总数
TOTAL_GPUS = 8

# 会话存储
sessions = {}


def load_data():
    """加载服务器数据"""
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {'servers': []}


def save_data(data):
    """保存服务器数据"""
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_used_gpus():
    """获取已被使用的GPU列表"""
    data = load_data()
    used_gpus = set()
    for server in data['servers']:
        for gpu in server.get('assigned_gpus', []):
            used_gpus.add(gpu)
    return used_gpus


# HTML模板
LOGIN_HTML = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>服务器资源管理系统 - 登录</title>
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
        .logo {
            width: 100px;
            height: 100px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            border-radius: 50%;
            margin: 0 auto 30px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 50px;
            color: white;
        }
        h1 { color: #333; margin-bottom: 10px; font-size: 28px; }
        .subtitle { color: #666; margin-bottom: 35px; font-size: 14px; }
        .form-group { margin-bottom: 25px; text-align: left; }
        label { display: block; margin-bottom: 8px; color: #555; font-weight: 500; font-size: 14px; }
        input[type="text"], input[type="password"] {
            width: 100%;
            padding: 15px;
            border: 2px solid #e0e0e0;
            border-radius: 10px;
            font-size: 15px;
            transition: all 0.3s ease;
        }
        input[type="text"]:focus, input[type="password"]:focus {
            outline: none;
            border-color: #667eea;
            box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
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
            transition: all 0.3s ease;
            margin-top: 10px;
        }
        .btn-login:hover {
            transform: translateY(-2px);
            box-shadow: 0 10px 30px rgba(102, 126, 234, 0.4);
        }
        .error-message {
            background: #ffebee;
            color: #c62828;
            padding: 12px;
            border-radius: 8px;
            margin-bottom: 20px;
            font-size: 14px;
        }
        .footer { margin-top: 30px; color: #999; font-size: 12px; }
    </style>
</head>
<body>
    <div class="login-container">
        <div class="logo">&#128187;</div>
        <h1>服务器资源管理系统</h1>
        <p class="subtitle">Server Resource Management System</p>
        {error}
        <form method="POST" action="/login">
            <div class="form-group">
                <label for="username">用户名</label>
                <input type="text" id="username" name="username" placeholder="请输入用户名" required autofocus>
            </div>
            <div class="form-group">
                <label for="password">密码</label>
                <input type="password" id="password" name="password" placeholder="请输入密码" required>
            </div>
            <button type="submit" class="btn-login">登 录</button>
        </form>
        <div class="footer">
            <p>默认账号: admin | 密码: 123456</p>
        </div>
    </div>
</body>
</html>'''


DASHBOARD_HTML = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>服务器资源管理系统 - 控制台</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: #f5f7fa;
            min-height: 100vh;
        }}
        .header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 20px 40px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.1);
        }}
        .header h1 {{ font-size: 24px; display: flex; align-items: center; gap: 12px; }}
        .header-actions {{ display: flex; align-items: center; gap: 20px; }}
        .user-info {{ display: flex; align-items: center; gap: 8px; font-size: 14px; }}
        .btn-logout {{
            background: rgba(255, 255, 255, 0.2);
            color: white;
            border: none;
            padding: 10px 20px;
            border-radius: 8px;
            cursor: pointer;
            font-size: 14px;
            transition: all 0.3s ease;
            text-decoration: none;
        }}
        .btn-logout:hover {{ background: rgba(255, 255, 255, 0.3); }}
        .container {{ max-width: 1600px; margin: 0 auto; padding: 30px 40px; }}
        .toolbar {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 25px;
        }}
        .btn-add {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            padding: 14px 28px;
            border-radius: 10px;
            cursor: pointer;
            font-size: 15px;
            font-weight: 600;
            display: flex;
            align-items: center;
            gap: 10px;
            transition: all 0.3s ease;
            box-shadow: 0 4px 15px rgba(102, 126, 234, 0.3);
        }}
        .btn-add:hover {{ transform: translateY(-2px); box-shadow: 0 8px 25px rgba(102, 126, 234, 0.4); }}
        .stats {{ display: flex; gap: 20px; }}
        .stat-card {{
            background: white;
            padding: 15px 25px;
            border-radius: 10px;
            box-shadow: 0 2px 10px rgba(0, 0, 0, 0.05);
        }}
        .stat-label {{ font-size: 12px; color: #888; margin-bottom: 5px; }}
        .stat-value {{ font-size: 24px; font-weight: 700; color: #333; }}
        .table-container {{
            background: white;
            border-radius: 15px;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.08);
            overflow: hidden;
        }}
        table {{ width: 100%; border-collapse: collapse; }}
        thead {{ background: #f8f9fa; }}
        th {{
            padding: 18px 20px;
            text-align: left;
            font-weight: 600;
            color: #555;
            font-size: 14px;
            border-bottom: 2px solid #e9ecef;
        }}
        td {{
            padding: 18px 20px;
            border-bottom: 1px solid #e9ecef;
            font-size: 14px;
            color: #444;
        }}
        tr:hover {{ background: #f8f9fa; }}
        .hostname {{ font-weight: 600; color: #667eea; }}
        .type-badge {{
            display: inline-block;
            padding: 6px 14px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: 600;
        }}
        .type-physical {{ background: #e3f2fd; color: #1976d2; }}
        .type-virtual {{ background: #f3e5f5; color: #7b1fa2; }}
        .purpose-cell {{
            position: relative;
            cursor: help;
            max-width: 150px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }}
        .purpose-cell:hover::after {{
            content: attr(data-detail);
            position: absolute;
            left: 0;
            top: 100%;
            background: #333;
            color: white;
            padding: 10px 15px;
            border-radius: 8px;
            font-size: 13px;
            white-space: normal;
            max-width: 300px;
            z-index: 100;
            margin-top: 5px;
        }}
        .gpu-list {{ display: flex; gap: 5px; flex-wrap: wrap; }}
        .gpu-tag {{
            background: #e8f5e9;
            color: #2e7d32;
            padding: 4px 10px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: 600;
        }}
        .user-cell {{ display: flex; align-items: center; gap: 8px; }}
        .user-avatar {{
            width: 32px;
            height: 32px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
            font-size: 14px;
            font-weight: 600;
        }}
        .btn-delete {{
            background: #ffebee;
            color: #c62828;
            border: none;
            padding: 8px 14px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 12px;
            transition: all 0.3s ease;
        }}
        .btn-delete:hover {{ background: #ef9a9a; color: white; }}
        .empty-state {{
            text-align: center;
            padding: 80px 40px;
            color: #888;
        }}
        .empty-state-icon {{ font-size: 80px; margin-bottom: 20px; opacity: 0.5; }}
        .empty-state h3 {{ font-size: 20px; margin-bottom: 10px; color: #555; }}
        .modal-overlay {{
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(0, 0, 0, 0.5);
            display: none;
            justify-content: center;
            align-items: center;
            z-index: 1000;
            backdrop-filter: blur(5px);
        }}
        .modal-overlay.active {{ display: flex; }}
        .modal {{
            background: white;
            border-radius: 20px;
            width: 90%;
            max-width: 700px;
            max-height: 90vh;
            overflow-y: auto;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
        }}
        .modal-header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 25px 30px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .modal-header h2 {{ font-size: 20px; display: flex; align-items: center; gap: 10px; }}
        .btn-close {{
            background: rgba(255, 255, 255, 0.2);
            border: none;
            color: white;
            width: 36px;
            height: 36px;
            border-radius: 50%;
            cursor: pointer;
            font-size: 20px;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: all 0.3s ease;
        }}
        .btn-close:hover {{ background: rgba(255, 255, 255, 0.3); }}
        .modal-body {{ padding: 30px; }}
        .form-row {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
            margin-bottom: 20px;
        }}
        .form-group {{ margin-bottom: 20px; }}
        label {{ display: block; margin-bottom: 8px; color: #555; font-weight: 500; font-size: 14px; }}
        label .required {{ color: #e74c3c; margin-left: 4px; }}
        input[type="text"], input[type="number"], select, textarea {{
            width: 100%;
            padding: 14px;
            border: 2px solid #e0e0e0;
            border-radius: 10px;
            font-size: 14px;
            transition: all 0.3s ease;
            font-family: inherit;
        }}
        textarea {{ resize: vertical; min-height: 80px; }}
        input:focus, select:focus, textarea:focus {{
            outline: none;
            border-color: #667eea;
            box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
        }}
        .gpu-selection {{
            display: grid;
            grid-template-columns: repeat(8, 1fr);
            gap: 10px;
            margin-top: 10px;
        }}
        .gpu-checkbox {{ position: relative; }}
        .gpu-checkbox input {{
            position: absolute;
            opacity: 0;
            cursor: pointer;
        }}
        .gpu-label {{
            display: block;
            padding: 15px 10px;
            background: #f5f5f5;
            border: 2px solid #e0e0e0;
            border-radius: 10px;
            text-align: center;
            cursor: pointer;
            transition: all 0.3s ease;
            font-weight: 600;
            color: #555;
            font-size: 12px;
        }}
        .gpu-checkbox input:checked + .gpu-label {{
            background: #e8f5e9;
            border-color: #4caf50;
            color: #2e7d32;
        }}
        .gpu-checkbox input:disabled + .gpu-label {{
            background: #eeeeee;
            border-color: #bdbdbd;
            color: #9e9e9e;
            cursor: not-allowed;
            opacity: 0.6;
        }}
        .gpu-legend {{
            display: flex;
            gap: 20px;
            margin-top: 15px;
            font-size: 12px;
            color: #666;
        }}
        .gpu-legend-item {{ display: flex; align-items: center; gap: 8px; }}
        .gpu-legend-box {{
            width: 20px;
            height: 20px;
            border-radius: 4px;
            border: 2px solid;
        }}
        .gpu-legend-available {{ background: #f5f5f5; border-color: #e0e0e0; }}
        .gpu-legend-selected {{ background: #e8f5e9; border-color: #4caf50; }}
        .gpu-legend-used {{ background: #eeeeee; border-color: #bdbdbd; }}
        .modal-footer {{
            padding: 20px 30px;
            border-top: 1px solid #e9ecef;
            display: flex;
            justify-content: flex-end;
            gap: 15px;
        }}
        .btn-cancel {{
            padding: 12px 24px;
            background: #f5f5f5;
            color: #555;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-size: 14px;
            font-weight: 500;
            transition: all 0.3s ease;
        }}
        .btn-cancel:hover {{ background: #e0e0e0; }}
        .btn-save {{
            padding: 12px 30px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-size: 14px;
            font-weight: 600;
            transition: all 0.3s ease;
        }}
        .btn-save:hover {{
            transform: translateY(-2px);
            box-shadow: 0 8px 20px rgba(102, 126, 234, 0.3);
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>&#128187; 服务器资源管理系统</h1>
        <div class="header-actions">
            <div class="user-info">&#128100; 管理员</div>
            <a href="/logout" class="btn-logout">退出登录</a>
        </div>
    </div>
    <div class="container">
        <div class="toolbar">
            <button class="btn-add" onclick="openModal()">&#10133; 添加服务器</button>
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
        </div>
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
                        <th>GPU数量</th>
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
    <div class="modal-overlay" id="addModal">
        <div class="modal">
            <div class="modal-header">
                <h2>&#10133; 添加服务器</h2>
                <button class="btn-close" onclick="closeModal()">&#10005;</button>
            </div>
            <form method="POST" action="/add_server">
                <div class="modal-body">
                    <div class="form-row">
                        <div class="form-group">
                            <label>主机名 <span class="required">*</span></label>
                            <input type="text" name="hostname" placeholder="例如: server-01" required>
                        </div>
                        <div class="form-group">
                            <label>类型 <span class="required">*</span></label>
                            <select name="type" required>
                                <option value="">请选择</option>
                                <option value="physical">物理机</option>
                                <option value="virtual">虚拟机</option>
                            </select>
                        </div>
                    </div>
                    <div class="form-row">
                        <div class="form-group">
                            <label>用途 <span class="required">*</span></label>
                            <input type="text" name="purpose" placeholder="简短描述用途" required>
                        </div>
                        <div class="form-group">
                            <label>IP地址 <span class="required">*</span></label>
                            <input type="text" name="ip" placeholder="例如: 192.168.1.100" required>
                        </div>
                    </div>
                    <div class="form-row">
                        <div class="form-group">
                            <label>CPU (核心数) <span class="required">*</span></label>
                            <input type="text" name="cpu" placeholder="例如: 32核" required>
                        </div>
                        <div class="form-group">
                            <label>内存 <span class="required">*</span></label>
                            <input type="text" name="mem" placeholder="例如: 128GB" required>
                        </div>
                    </div>
                    <div class="form-row">
                        <div class="form-group">
                            <label>磁盘大小 <span class="required">*</span></label>
                            <input type="text" name="disk" placeholder="例如: 2TB" required>
                        </div>
                        <div class="form-group">
                            <label>GPU数量 <span class="required">*</span></label>
                            <input type="number" name="gpu_count" min="0" max="8" value="0" required>
                        </div>
                    </div>
                    <div class="form-group">
                        <label>用途详情</label>
                        <textarea name="purpose_detail" placeholder="详细描述该服务器的用途、运行的服务等..."></textarea>
                    </div>
                    <div class="form-group">
                        <label>分配GPU</label>
                        <div class="gpu-selection">
                            {gpu_selection}
                        </div>
                        <div class="gpu-legend">
                            <div class="gpu-legend-item">
                                <div class="gpu-legend-box gpu-legend-available"></div>
                                <span>可用</span>
                            </div>
                            <div class="gpu-legend-item">
                                <div class="gpu-legend-box gpu-legend-selected"></div>
                                <span>已选</span>
                            </div>
                            <div class="gpu-legend-item">
                                <div class="gpu-legend-box gpu-legend-used"></div>
                                <span>已被使用</span>
                            </div>
                        </div>
                    </div>
                    <div class="form-group">
                        <label>使用人 <span class="required">*</span></label>
                        <input type="text" name="user" placeholder="例如: 张三" required>
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
        function openModal() {{
            document.getElementById('addModal').classList.add('active');
            document.body.style.overflow = 'hidden';
        }}
        function closeModal() {{
            document.getElementById('addModal').classList.remove('active');
            document.body.style.overflow = '';
        }}
        document.getElementById('addModal').addEventListener('click', (e) => {{
            if (e.target === e.currentTarget) closeModal();
        }});
    </script>
</body>
</html>'''


class RequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def get_session(self):
        """获取会话"""
        cookie = self.headers.get('Cookie', '')
        for part in cookie.split(';'):
            if 'session=' in part:
                session_id = part.split('=')[1].strip()
                if session_id in sessions:
                    return sessions[session_id]
        return None

    def set_session(self, session_id, data):
        """设置会话"""
        sessions[session_id] = data

    def send_redirect(self, location):
        """发送重定向"""
        self.send_response(302)
        self.send_header('Location', location)
        self.end_headers()

    def send_html(self, html, status=200):
        """发送HTML响应"""
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(html.encode('utf-8'))

    def do_GET(self):
        """处理GET请求"""
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        session = self.get_session()

        if path == '/' or path == '/login':
            if session:
                self.send_redirect('/dashboard')
            else:
                self.send_html(LOGIN_HTML.format(error=''))

        elif path == '/dashboard':
            if not session:
                self.send_redirect('/login')
            else:
                self.render_dashboard()

        elif path == '/logout':
            cookie = self.headers.get('Cookie', '')
            for part in cookie.split(';'):
                if 'session=' in part:
                    session_id = part.split('=')[1].strip()
                    if session_id in sessions:
                        del sessions[session_id]
            self.send_redirect('/login')

        elif path == '/delete':
            if not session:
                self.send_redirect('/login')
            else:
                query = urllib.parse.parse_qs(parsed.query)
                if 'id' in query:
                    server_id = int(query['id'][0])
                    data = load_data()
                    data['servers'] = [s for s in data['servers'] if s['id'] != server_id]
                    save_data(data)
                self.send_redirect('/dashboard')

        else:
            self.send_html('<h1>404 Not Found</h1>', 404)

    def do_POST(self):
        """处理POST请求"""
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        session = self.get_session()

        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode('utf-8')
        form_data = urllib.parse.parse_qs(body)

        if path == '/login':
            username = form_data.get('username', [''])[0].strip()
            password = form_data.get('password', [''])[0].strip()

            if username == ADMIN_USER and password == ADMIN_PASS:
                import uuid
                session_id = str(uuid.uuid4())
                self.set_session(session_id, {'username': username})
                self.send_response(302)
                self.send_header('Location', '/dashboard')
                self.send_header('Set-Cookie', f'session={session_id}; Path=/')
                self.end_headers()
            else:
                error_html = '<div class="error-message">&#9888; 用户名或密码错误</div>'
                self.send_html(LOGIN_HTML.format(error=error_html))

        elif path == '/add_server':
            if not session:
                self.send_redirect('/login')
            else:
                # 获取选中的GPU
                assigned_gpus = []
                for key in form_data:
                    if key.startswith('gpu_'):
                        gpu_id = int(key.replace('gpu_', ''))
                        assigned_gpus.append(gpu_id)

                new_server = {
                    'id': len(load_data()['servers']) + 1,
                    'hostname': form_data.get('hostname', [''])[0],
                    'type': form_data.get('type', [''])[0],
                    'purpose': form_data.get('purpose', [''])[0],
                    'purpose_detail': form_data.get('purpose_detail', [''])[0],
                    'ip': form_data.get('ip', [''])[0],
                    'cpu': form_data.get('cpu', [''])[0],
                    'mem': form_data.get('mem', [''])[0],
                    'disk': form_data.get('disk', [''])[0],
                    'gpu_count': int(form_data.get('gpu_count', ['0'])[0]),
                    'assigned_gpus': assigned_gpus,
                    'user': form_data.get('user', [''])[0]
                }

                data = load_data()
                data['servers'].append(new_server)
                save_data(data)
                self.send_redirect('/dashboard')

    def render_dashboard(self):
        """渲染控制台页面"""
        data = load_data()
        servers = data['servers']
        used_gpus = get_used_gpus()

        # 生成表格行
        if servers:
            rows = []
            for server in servers:
                type_class = 'type-physical' if server['type'] == 'physical' else 'type-virtual'
                type_text = '物理机' if server['type'] == 'physical' else '虚拟机'
                gpu_tags = ''.join([f'<span class="gpu-tag">GPU {g}</span>' for g in server.get('assigned_gpus', [])])
                detail = server.get('purpose_detail', '') or '暂无详情'
                
                row = f'''<tr>
                    <td class="hostname">{server['hostname']}</td>
                    <td><span class="type-badge {type_class}">{type_text}</span></td>
                    <td class="purpose-cell" data-detail="{detail}">{server['purpose']}</td>
                    <td>{server['ip']}</td>
                    <td>{server['cpu']}</td>
                    <td>{server['mem']}</td>
                    <td>{server['disk']}</td>
                    <td>{server['gpu_count']}</td>
                    <td><div class="gpu-list">{gpu_tags}</div></td>
                    <td><div class="user-cell"><div class="user-avatar">{server['user'][0]}</div><span>{server['user']}</span></div></td>
                    <td><a href="/delete?id={server['id']}" class="btn-delete" onclick="return confirm('确定删除?')">删除</a></td>
                </tr>'''
                rows.append(row)
            table_rows = ''.join(rows)
        else:
            table_rows = '''<tr><td colspan="11">
                <div class="empty-state">
                    <div class="empty-state-icon">&#128187;</div>
                    <h3>暂无服务器记录</h3>
                    <p>点击上方"添加服务器"按钮开始记录</p>
                </div>
            </td></tr>'''

        # 生成GPU选择
        gpu_selection = []
        for i in range(8):
            disabled = 'disabled' if i in used_gpus else ''
            gpu_selection.append(f'''<div class="gpu-checkbox">
                <input type="checkbox" id="gpu{i}" name="gpu_{i}" value="{i}" {disabled}>
                <label for="gpu{i}" class="gpu-label">GPU {i}</label>
            </div>''')

        html = DASHBOARD_HTML.format(
            server_count=len(servers),
            gpu_used=len(used_gpus),
            table_rows=table_rows,
            gpu_selection=''.join(gpu_selection)
        )
        self.send_html(html)


def run_server():
    """启动服务器"""
    # 初始化数据文件
    if not os.path.exists(DATA_FILE):
        save_data({'servers': []})

    server = HTTPServer(('127.0.0.1', 5000), RequestHandler)
    print('服务器已启动: http://127.0.0.1:5000')
    print('按 Ctrl+C 停止服务器')
    server.serve_forever()


if __name__ == '__main__':
    run_server()
