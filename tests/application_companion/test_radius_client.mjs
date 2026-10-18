import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {syncRadius,radiusJSON,cancelRadiusRequests} from '../../extensions/application-companion/radius-client.js';

test('sync uses the small companion catalog and terminates nonadvancing cursors',async()=>{
 const requests=[];
 const server=createServer((req,res)=>{
  requests.push(req.url);res.setHeader('Content-Type','application/json');
  if(req.url==='/api/v1/profiles')res.end(JSON.stringify({items:[{id:1,name:'Alex',cv:{updated_at:'v1'}}]}));
  else if(req.url==='/api/v1/companion/jobs?limit=100&after_id=0')res.end(JSON.stringify({items:[{id:10,title:'Local role',url:'https://employer.example/jobs/10'}],next_after_id:10}));
  else if(req.url==='/api/v1/companion/jobs?limit=100&after_id=10')res.end(JSON.stringify({items:[],next_after_id:10}));
  else {res.statusCode=404;res.end('{}');}
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));const origin=`http://127.0.0.1:${server.address().port}`;
 try{await assert.rejects(syncRadius({origin,profile_id:1}),/cursor/i);assert.deepEqual(requests,['/api/v1/profiles','/api/v1/companion/jobs?limit=100&after_id=0','/api/v1/companion/jobs?limit=100&after_id=10']);}
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
