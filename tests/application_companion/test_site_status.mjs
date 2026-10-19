import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';

test('badge resolves an uncached company on arbitrary subdomains using hostname only', async () => {
 const requests=[];
 const server=createServer((req,res)=>{requests.push(req.url);res.setHeader('Content-Type','application/json');res.end(JSON.stringify({job_count:4321,feeds:[{id:1,url:'https://careers.employer.example/jobs',status:'parsed'}],items:[{id:4321,title:'Engineer',url:'https://careers.employer.example/jobs/4321',company_name:'Employer'}]}));});
 await new Promise(r=>server.listen(0,'127.0.0.1',r));const origin=`http://127.0.0.1:${server.address().port}`;
 const local={radiusVault:{settings:{origin,api_origin:origin,profile_id:1},jobs:[],bindings:[],packages:{}}},session={};let badge,title;
 const storage=data=>({get:async key=>({[key]:data[key]}),set:async values=>Object.assign(data,values)});
 globalThis.chrome={storage:{local:storage(local),session:storage(session)},action:{setBadgeText:async value=>{badge=value.text},setBadgeBackgroundColor:async()=>{},setTitle:async value=>{title=value.title}}};
 try {
  const {updateBadge,pageState}=await import('../../extensions/application-companion/application-state.js');
  await updateBadge(1,'https://news.eu.employer.example/anything?private=secret');
  assert.equal(badge,'•');assert.match(title,/4321/);
  assert.deepEqual(requests,['/api/v1/companion/site?host=news.eu.employer.example']);
  const state=await pageState(1,'https://news.eu.employer.example/anything');
  assert.equal(state.site.job_count,4321);assert.equal(state.items[0].exact,false);
  await updateBadge(1,'https://news.eu.employer.example/other');
  assert.equal(requests.length,1);
 }finally{server.closeAllConnections();await new Promise(r=>server.close(r));delete globalThis.chrome;}
});
