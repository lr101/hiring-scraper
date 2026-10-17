import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {urlKey,matchJobs,addBinding,profileStamp,readyPackage,validateSettings} from '../../extensions/application-companion/vault.js';
const settings={origin:'https://radius.example',api_origin:'https://radius.example',profile_id:1,cv_updated_at:'cv1'};
const jobs=[{id:10,title:'Project manager',company_name:'Labs',url:'https://careers.example/jobs/10'},{id:11,title:'Other role',company_name:'Labs',url:'https://careers.example/jobs/11'}];
test('matches exact job identities while ignoring marketing parameters, never entire hosts',()=>{
 assert.equal(urlKey('https://careers.example/jobs/10?utm_source=feed#apply'),'https://careers.example/jobs/10');
 assert.equal(matchJobs({jobs,bindings:[]},'https://careers.example/jobs/10?utm_source=mail')[0].id,10);
 assert.equal(matchJobs({jobs,bindings:[]},'https://careers.example/jobs/99').length,0);
 assert.notEqual(urlKey('https://ats.example/apply?jobId=10'),urlKey('https://ats.example/apply?jobId=11'));
 assert.notEqual(urlKey('https://ats.example/#/job/10'),urlKey('https://ats.example/#/job/11'));
});
test('persists a distinct Apply endpoint against its Radius job without mutating source URLs',()=>{
 const state=addBinding({jobs,bindings:[]},10,'https://ats.example/apply?jobId=10','apply');
 assert.equal(matchJobs(state,'https://ats.example/apply?jobId=10')[0].id,10);
 assert.equal(state.jobs[0].url,jobs[0].url);assert.equal(state.bindings[0].job_id,10);
 const shared=addBinding(state,11,'https://ats.example/apply?jobId=10','apply');
 assert.equal(matchJobs(shared,'https://ats.example/apply?jobId=10').length,2);
 assert.throws(()=>addBinding(state,10,'https://ats.example/login?session=secret','apply'),/session|login|transient/i);
 assert.throws(()=>addBinding(state,99,'https://ats.example/apply','apply'),/unknown/i);
});
test('a prepared entry becomes stale after profile facts, selected profile, CV version or expiry change',async()=>{
 globalThis.crypto ??= webcrypto;
 const applicant={first_name:'Alex',experiences:[]};const stamp=await profileStamp(applicant);
 const record={job_id:10,profile_id:1,cv_updated_at:'cv1',profile_stamp:stamp,saved_at:Date.now(),package:{format:'radius-application-v1',job_id:10,profile_id:1,cv_updated_at:'cv1',cv:[{id:12,text:'Tailored CV bullet'}],cover_letter:'Letter',application_answers:''}};
 const state={settings,jobs,bindings:[],packages:{'10:1':record}};
 assert.equal(readyPackage(state,10,stamp).job_id,10);
 assert.equal(readyPackage(state,10,await profileStamp({...applicant,first_name:'Other'})),null);
 assert.equal(readyPackage({...state,settings:{...settings,cv_updated_at:'cv2'}},10,stamp),null);
 assert.equal(readyPackage({...state,settings:{...settings,profile_id:2}},10,stamp),null);
 assert.equal(readyPackage({...state,packages:{'10:1':{...record,saved_at:1}}},10,stamp),null);
});
test('connection settings require explicit safe origins, and transient URLs cannot become permanent aliases',()=>{
 assert.equal(validateSettings({...settings,origin:'https://radius.example/jobs/10'}).origin,'https://radius.example');
 assert.throws(()=>validateSettings({...settings,origin:'http://public.example'}),/HTTPS|loopback/);
 assert.throws(()=>validateSettings({...settings,origin:'https://name:password@radius.example'}));
 assert.equal(urlKey('javascript:alert(1)'),null);
});
test('only a same-origin, explicit unchanged job identifier links a later wizard step',async()=>{
 const {sameJobReference}=await import('../../extensions/application-companion/vault.js');
 assert.equal(sameJobReference('https://ats.example/apply?jobId=10&step=1','https://ats.example/apply?jobId=10&step=2'),true);
 assert.equal(sameJobReference('https://ats.example/apply?jobId=10','https://ats.example/apply?jobId=11'),false);
 assert.equal(sameJobReference('https://ats.example/apply?jobId=10','https://other.example/apply?jobId=10'),false);
 assert.equal(sameJobReference('https://ats.example/apply','https://ats.example/profile'),false);
});
test('equivalent stored packages compare identically after Chrome storage reorders object keys',async()=>{
 const {stableJSON}=await import('../../extensions/application-companion/vault.js');
 assert.equal(stableJSON({cv:[{id:12,text:'Bullet'}],job_id:10}),stableJSON({job_id:10,cv:[{text:'Bullet',id:12}]}));
});
test('the canonical Radius detail/preparation page shares the same job identity as its external listing',()=>{
 const state={settings,jobs,bindings:[]};
 assert.equal(matchJobs(state,'https://radius.example/jobs/10?profile_id=1')[0].id,10);
 assert.equal(matchJobs(state,'https://radius.example/jobs/10/apply?profile_id=1')[0].id,10);
 assert.equal(matchJobs(state,'https://unrelated.example/jobs/10').length,0);
});
