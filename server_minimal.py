from http.server import HTTPServer, BaseHTTPRequestHandler

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        print(format % args)
    
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(b'<h1>Server is running!</h1><p>Test successful</p>')

print('Starting server on http://127.0.0.1:5000')
server = HTTPServer(('127.0.0.1', 5000), Handler)
print('Server started! Press Ctrl+C to stop.')
server.serve_forever()
