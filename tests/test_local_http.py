import threading,json,unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import core
class LocalHttpTests(unittest.TestCase):
 def test_real_urllib_stream_and_model_list(self):
  requests=[]
  class Handler(BaseHTTPRequestHandler):
   def log_message(self,*args):pass
   def do_GET(self):
    self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(b'{"data":[{"id":"local-model"}]}')
   def do_POST(self):
    payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])));requests.append((self.path,payload,self.headers.get('Authorization')))
    self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
    for part in [{'choices':[{'index':0,'delta':{'content':'真实流式测试'},'finish_reason':None}]},{'choices':[{'index':0,'delta':{},'finish_reason':'stop'}]}]:
     self.wfile.write(('data: '+json.dumps(part)+'\n\n').encode());self.wfile.flush()
    self.wfile.write(b'data: [DONE]\n\n');self.wfile.flush()
  server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  try:
   url=f'http://127.0.0.1:{server.server_port}/v1';self.assertEqual(core.list_models(url),['local-model'])
   cfg=dict(core.DEFAULTS,base_url=url,model='local-model',stream_enabled=True);parts=[]
   self.assertEqual(core.transform(cfg,'','润色','测试',on_delta=parts.append),'真实流式测试')
   self.assertEqual(parts,['真实流式测试']);self.assertEqual(requests[0][0],'/v1/chat/completions');self.assertTrue(requests[0][1]['stream']);self.assertIsNone(requests[0][2])
  finally:server.shutdown();server.server_close()
