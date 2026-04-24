import sys
print("Python路径:", sys.executable)
print("Python版本:", sys.version)
print("启动测试服务器...")

import json
import os
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler

class TestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(b'<h1>Server is running!</h1>')
    
    def log_message(self, format, *args):
        pass

server = HTTPServer(('127.0.0.1', 5000), TestHandler)
print('服务器已启动: http://127.0.0.1:5000')
server.serve_forever()
