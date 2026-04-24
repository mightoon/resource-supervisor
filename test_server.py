from http.server import HTTPServer, BaseHTTPRequestHandler

class TestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(b'<h1>Server is running!</h1><p>Python http.server works!</p>')
    
    def log_message(self, format, *args):
        print(format % args)

print('Starting test server on http://127.0.0.1:5000')
server = HTTPServer(('127.0.0.1', 5000), TestHandler)
print('Server started! Press Ctrl+C to stop.')
server.serve_forever()
