"""Local fictional Radius handoff simulator, for extension integration tests and manual demos."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlsplit,parse_qs
import argparse,json
from pathlib import Path
from html import escape

STYLE='''<style>:root{font:15px/1.6 'Avenir Next','Segoe UI',sans-serif;background:#f6f3ef;color:#29343c}body{margin:0}header{background:#fcfcfb;border-bottom:1px solid #dce3d8;padding:18px max(24px,7vw);display:flex;align-items:center;justify-content:space-between}.brand{font-size:27px;font-weight:800;letter-spacing:-1px;text-decoration:none}main{max-width:900px;margin:42px auto;padding:0 24px}h1{font-size:32px;line-height:1.25;letter-spacing:-.8px}h2{font-size:21px}p{max-width:70ch}.paper{background:#fcfcfb;border:1px solid #dce3d8;border-radius:12px;padding:28px;margin:24px 0}.application-cv-ready{background:#edf3e9;border:1px solid #dce3d8;border-radius:10px;padding:18px}.primary-button,button{display:inline-block;border:1px solid #354e41;background:#354e41;color:white;border-radius:7px;padding:12px 17px;font:600 14px inherit;text-decoration:none;cursor:pointer}a{color:inherit}small{color:#6d7b73}label{display:block;margin:16px 0}input,textarea{display:block;width:95%;font:inherit;padding:10px;border:1px solid #dce3d8;border-radius:6px}textarea{min-height:110px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}@media(max-width:600px){.grid{grid-template-columns:1fr}}[data-theme=dark]{background:#171c20;color:#e2e7e9}</style>'''
FORM='''<h1>Research project manager</h1><p>Research Labs · Employer application</p><form class=paper><div class=grid><label>First name<input id=first></label><label>Last name<input id=last></label></div><label>Email<input id=email type=email></label><label>Cover letter<textarea id=letter></textarea></label><label>Why this role?<textarea id=why></textarea></label><label>CV<input id=cv type=file required></label><label><input id=consent type=checkbox style="width:auto;display:inline"> I agree to the privacy policy</label><button type=submit>Submit application</button></form><script>window.submits=0;document.querySelector('form').onsubmit=e=>{e.preventDefault();submits++;alert('Local demo only; nothing was sent.');}</script>'''

def package(job_id,cv_version):
 return {'format':'radius-application-v1','job_id':job_id,'profile_id':1,'cv_updated_at':cv_version,'cv':[{'id':12,'text':'Coordinated research milestones and partner workshops.'}],'cover_letter':f'Alex Example\n\nApplication for Radius job {job_id}.\n\nI bring experience coordinating research projects, teams and milestones.','application_answers':'Q: Why this role?\nA: I coordinate research projects and connect technical teams with their partners.','review_notes':'Fictional local simulator; no actual ChatGPT request.','experience_bullets':[{'experience_id':'job-1','bullets':['Coordinated research milestones and partner workshops.']}]}

class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_PUT(self):
  # Connected settings now persist reusable facts to Radius. Keep this legacy
  # handoff simulator's stored DOCX revision while saving those profile details.
  if self.path!='/api/v1/profiles/1':return self.respond({'detail':'Not found'},404)
  payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
  self.server.state['application_details']=payload['application_details']
  return self.respond({'id':1,**payload})
 def do_GET(self):
  state=self.server.state;port=self.server.server_port;origin=f'http://127.0.0.1:{port}'
  source=state.get('employer_origin',f'http://localhost:{port}')
  jobs=[{'id':1574,'title':'Research project manager','company_name':'Research Labs','url':source+'/jobs/1574','is_active':True},{'id':2000,'title':'Engineering coordinator','company_name':'Research Labs','url':source+'/jobs/2000','is_active':True}]
  parsed=urlsplit(self.path);path=parsed.path
  if path=='/api/v1/profiles':
   result={'items':state.get('profiles',[{'id':1,'name':'Research profile','application_details':state.get('application_details',json.loads((Path(__file__).parent / 'profile.example.json').read_text())),'cv':{'filename':'example-cv.docx','updated_at':state.get('cv_version','cv1')}}])}
   hold=state.pop('hold_profiles',None)
   if hold:hold[0].set();hold[1].wait(5)
   return self.respond(result)
  if path.startswith('/api/v1/profiles/') and path.endswith('/cv/text'):
   return self.respond({'text':state.get('cv_text','Alex Example. Contact alex@example.invalid. Coordinated research teams.')})
  if path=='/api/v1/companion/jobs':
   hold=state.pop('hold_jobs',None)
   if hold:hold[0].set();hold[1].wait(5)
   return self.respond({'items':state.get('catalog_jobs',jobs),'next_after_id':None})
  if path.startswith('/api/v1/jobs/'):
   job=next((j for j in jobs if str(j['id'])==path.rsplit('/',1)[-1]),None);return self.respond(job,200 if job else 404)
  body=''
  if path=='/':
   body=f'<h1>Your next application, connected</h1><p>This is a fictional local simulator. It sends no application and makes no ChatGPT request. Connect the extension to this Radius address, using the structured fictional profile.</p><section class=paper><h2>Discovered jobs</h2>'+''.join(f'<p><a href="{j["url"]}">{escape(j["title"])}</a> · {escape(j["company_name"])}</p>' for j in jobs)+'</section>'
  elif path.endswith('/apply') and path.startswith('/jobs/'):
   job_id=int(path.split('/')[2]);pack=package(job_id,state.get('cv_version','cv1'))
   body='<h1>Prepare your application</h1><p>Research Labs · Research profile</p><div class=application-cv-ready><strong>Original CV attached</strong><br><small>example-cv.docx · Reused for this application</small></div><section class=paper><h2>Tailor with ChatGPT</h2><p>In Radius this opens the existing ChatGPT CV flow. This simulator supplies a complete fictional return so you can test the extension without a login.</p><button id=simulate type=button>Use simulated ChatGPT return</button><p id=draft-status role=status></p></section>'
   body+='<script>const pack='+json.dumps(pack).replace('<','\\u003c')+''';document.querySelector('#simulate').onclick=()=>{localStorage.setItem('radius:application-draft:v1:'+pack.job_id+':1',JSON.stringify({package:pack,context:{job_id:pack.job_id,profile_id:1,cv_updated_at:pack.cv_updated_at},saved_at:Date.now()}));document.querySelector('#draft-status').textContent='Personalized CV returned. Return to the employer tab; the extension will connect it automatically.';};
if(location.hash.startsWith('#application=')){
 const returned=JSON.parse(decodeURIComponent(location.hash.slice(13)));
 const standard={format:returned.format,job_id:returned.job_id,profile_id:returned.profile_id,cv_updated_at:returned.cv_updated_at,cv:returned.cv,cover_letter:returned.cover_letter,application_answers:returned.application_answers,review_notes:returned.review_notes};
 localStorage.setItem('radius:application-draft:v1:'+returned.job_id+':'+returned.profile_id,JSON.stringify({package:standard,context:{job_id:returned.job_id,profile_id:returned.profile_id,cv_updated_at:returned.cv_updated_at},saved_at:Date.now()}));
 history.replaceState(null,'',location.pathname+location.search);document.querySelector('#draft-status').textContent='Return imported; standard draft saved.';
}</script>'''
  elif path in ['/jobs/1574','/jobs/2000']:
   job_id=int(path.rsplit('/',1)[-1]);title=next(j['title'] for j in jobs if j['id']==job_id)
   body=f'<h1>{escape(title)}</h1><p>Research Labs · Karlsruhe</p><section class=paper><h2>Make research projects happen</h2><p>Coordinate technical teams, university partners, milestones and grant reporting.</p><a class=primary-button href="{source}/ats/apply?jobId={job_id}" target=_blank rel=noopener>Apply now</a></section>'
  elif path=='/ats/apply':body=FORM
  else:return self.respond({'detail':'Not found'},404)
  self.respond('<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Radius application demo</title>'+STYLE+f'<header><a class=brand href="{origin}">radius.</a><small>Fictional local demo</small></header><main>'+body+'</main></html>',html=True)
 def respond(self,value,status=200,html=False):
  body=value.encode() if html else json.dumps(value).encode();self.send_response(status);self.send_header('Content-Type','text/html; charset=utf-8' if html else 'application/json');self.send_header('Content-Length',str(len(body)));self.end_headers()
  try:self.wfile.write(body)
  except (BrokenPipeError,ConnectionResetError):pass # Expected when a sync is cancelled.

def server(port=0,**state):
 app=ThreadingHTTPServer(('127.0.0.1',port),Handler);app.state=state;return app

def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--port',type=int,default=8765);args=parser.parse_args();app=server(args.port)
 print(f'Radius simulator: http://127.0.0.1:{app.server_port}',flush=True)
 try:app.serve_forever()
 except KeyboardInterrupt:pass
 finally:app.server_close()
if __name__=='__main__':main()
