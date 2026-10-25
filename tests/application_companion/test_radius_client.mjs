import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {syncRadius,radiusJSON,cancelRadiusRequests} from '../../extensions/application-companion/radius-client.js';

test('sync uses the small companion catalog and terminates nonadvancing cursors',async()=>{
 const requests=[];
 const server=createServer((req,res)=>{
  requests.push(req.url);res.setHeader('Content-Type','application/json');
  if(req.url==='/api/v1/profiles')res.end(JSON.stringify({items:[{id:1,name:'Alex',cv:{updated_at:'v1'}}]}));
  else if(req.url==='/api/v1/profiles/1/cv/text')res.end(JSON.stringify({text:'Saved CV'}));
  else if(req.url==='/api/v1/companion/jobs?limit=100&after_id=0')res.end(JSON.stringify({items:[{id:10,title:'Local role',url:'https://employer.example/jobs/10'}],next_after_id:10}));
  else if(req.url==='/api/v1/companion/jobs?limit=100&after_id=10')res.end(JSON.stringify({items:[],next_after_id:10}));
  else {res.statusCode=404;res.end('{}');}
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));const origin=`http://127.0.0.1:${server.address().port}`;
 try{await assert.rejects(syncRadius({origin,profile_id:1}),/cursor/i);assert.deepEqual(requests,['/api/v1/profiles','/api/v1/profiles/1/cv/text','/api/v1/companion/jobs?limit=100&after_id=0','/api/v1/companion/jobs?limit=100&after_id=10']);}
 finally{server.closeAllConnections();await new Promise(r=>server.close(r));}
});

test('API reader rejects oversized streamed data and supports cancellation',async()=>{
 const server=createServer((req,res)=>{res.setHeader('Content-Type','application/json');if(req.url==='/large')res.end(' '.repeat(600000));else res.write('{');});
 await new Promise(r=>server.listen(0,'127.0.0.1',r));const origin=`http://127.0.0.1:${server.address().port}`;
 try{
  await assert.rejects(radiusJSON({origin},'/large'),/too much data/i);
  const controller=new AbortController();const work=radiusJSON({origin},'/slow',controller.signal);controller.abort();await assert.rejects(work,/abort/i);
 }finally{server.closeAllConnections();await new Promise(r=>server.close(r));}
});

test('disconnect cancellation aborts metadata requests as well as catalog sync',async()=>{
 let started;const seen=new Promise(resolve=>{started=resolve});
 const server=createServer((req,res)=>{res.setHeader('Content-Type','application/json');res.write('{');started();});
 await new Promise(r=>server.listen(0,'127.0.0.1',r));const origin=`http://127.0.0.1:${server.address().port}`;
 try {const pending=radiusJSON({origin},'/api/v1/profiles');await seen;cancelRadiusRequests();await assert.rejects(pending,/abort/i);}
 finally{server.closeAllConnections();await new Promise(r=>server.close(r));}
});

test('document reader returns bounded binary files and preserves API errors',async()=>{
 const server=createServer((req,res)=>{if(req.url==='/error'){res.statusCode=409;res.setHeader('Content-Type','application/json');res.end(JSON.stringify({detail:'Original CV changed'}));return}res.setHeader('Content-Type','application/pdf');res.end(req.url==='/large'?'x'.repeat(5242881):'%PDF-1.7\nfixture');});
 await new Promise(r=>server.listen(0,'127.0.0.1',r));const origin=`http://127.0.0.1:${server.address().port}`;
 try {const {radiusRequest}=await import('../../extensions/application-companion/radius-client.js');const result=await radiusRequest({origin},'/pdf',{responseType:'bytes'});assert.equal(new TextDecoder().decode(result.bytes),'%PDF-1.7\nfixture');assert.equal(result.type,'application/pdf');await assert.rejects(radiusRequest({origin},'/large',{responseType:'bytes'}),/too much data/i);await assert.rejects(radiusRequest({origin},'/error',{responseType:'bytes'}),/Original CV changed/);}
 finally{server.closeAllConnections();await new Promise(r=>server.close(r));}
});

test('sync imports the selected application facts and CV text, and clears facts for another profile', async () => {
 const server=createServer((req,res)=>{
  res.setHeader('Content-Type','application/json');
  if(req.url==='/api/v1/profiles') res.end(JSON.stringify({items:[
   {id:1,name:'Developer',summary:'English profile',localized_content:{de:{summary:'Deutsches Profil',application_personalization:{career_goals:'Forschung'}}},cv:{updated_at:'v1'},application_details:{first_name:'Alex',birth_date:'1992-02-29',nationality:'Deutschland',driving_licence:'Klasse B',preferences:{salary_amount:'75000'}}},
   {id:2,name:'Other',cv:null,application_details:{}}
  ]}));
  else if(req.url==='/api/v1/profiles/1/cv/text') res.end(JSON.stringify({text:'Confirmed CV text'}));
  else if(req.url.startsWith('/api/v1/companion/jobs?')) res.end(JSON.stringify({items:[],next_after_id:null}));
  else {res.statusCode=404;res.end('{}');}
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r)); const origin=`http://127.0.0.1:${server.address().port}`;
 globalThis.chrome={tabs:{query:async()=>[]}};
 try {
  const first=await syncRadius({origin,profile_id:1});
  assert.equal(first.applicant?.first_name,'Alex');
  assert.equal(first.applicant.birth_date,'1992-02-29');
  assert.equal(first.applicant.nationality,'Deutschland');
  assert.equal(first.applicant.driving_licence,'Klasse B');
  assert.equal(first.applicant.cv_text,'Confirmed CV text');
  assert.equal(first.applicant.preferences.salary_amount,'75000');
  assert.equal(first.applicant.localized_content.en.summary,'English profile');
  assert.equal(first.applicant.localized_content.de.summary,'Deutsches Profil');
  assert.equal(first.applicant.localized_content.de.application_personalization.career_goals,'Forschung');
  const second=await syncRadius({origin,profile_id:2});
  assert.equal(second.applicant.first_name,'');
  assert.equal(second.applicant.birth_date,'');
  assert.equal(second.applicant.nationality,'');
  assert.equal(second.applicant.driving_licence,'');
  assert.equal(second.applicant.cv_text,'');
 } finally {delete globalThis.chrome;server.closeAllConnections();await new Promise(r=>server.close(r));}
});

test('browser-local application CV text is imported from the connected Radius origin', async () => {
 const server=createServer((req,res)=>{res.setHeader('Content-Type','application/json');res.end(JSON.stringify(req.url==='/api/v1/profiles'?{items:[{id:1,name:'Developer',cv:null,application_details:{first_name:'Alex'}}]}:{items:[],next_after_id:null}));});
 await new Promise(r=>server.listen(0,'127.0.0.1',r));const origin=`http://127.0.0.1:${server.address().port}`;
 globalThis.chrome={tabs:{query:async()=>[{id:10}]},scripting:{executeScript:async ({func,args})=>{
  const previous=globalThis.localStorage;
  globalThis.localStorage={getItem:key=>key==='radius:application-cv:v1:1'?JSON.stringify({text:'Saved browser CV',updated_at:'local-v1'}):null};
  try{return [{result:func(...args)}];}finally{globalThis.localStorage=previous;}
 }}};
 try{const synced=await syncRadius({origin,profile_id:1});assert.equal(synced.applicant.cv_text,'Saved browser CV');assert.equal(synced.settings.cv_updated_at,'local-v1');}
 finally{delete globalThis.chrome;server.closeAllConnections();await new Promise(r=>server.close(r));}
});

test('LaTeX uploads send raw UTF-8 source rather than JSON-quoted text', async () => {
 const source='\\documentclass{article}\nJosé & research'; let received=''; let type='';
 const server=createServer(async (req,res)=>{for await(const part of req)received+=part;type=req.headers['content-type'];res.setHeader('Content-Type','application/json');res.end('{"saved":true}');});
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 try{const {radiusRequest}=await import('../../extensions/application-companion/radius-client.js');await radiusRequest({origin:`http://127.0.0.1:${server.address().port}`},'/cv',{method:'PUT',body:source,contentType:'application/x-tex'});assert.equal(received,source);assert.equal(type,'application/x-tex');}
 finally{server.closeAllConnections();await new Promise(r=>server.close(r));}
});

test('document validation can read bounded TeX and handle empty successful deletion responses', async () => {
 const server=createServer((req,res)=>{if(req.method==='DELETE'){res.writeHead(204);res.end();}else{res.setHeader('Content-Type','text/plain');res.end('\\begin{document}José\\end{document}');}});
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 try{const {radiusRequest}=await import('../../extensions/application-companion/radius-client.js');const settings={origin:`http://127.0.0.1:${server.address().port}`};assert.equal(await radiusRequest(settings,'/tex',{responseType:'text'}),'\\begin{document}José\\end{document}');assert.equal(await radiusRequest(settings,'/cv',{method:'DELETE'}),null);}
 finally{server.closeAllConnections();await new Promise(r=>server.close(r));}
});
