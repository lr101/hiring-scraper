import test from 'node:test';
import assert from 'node:assert/strict';
import {deflateSync} from 'node:zlib';
import {readFile} from 'node:fs/promises';
import {readPackage,connectSources} from '../../extensions/application-companion/sources.js';

const pack={format:'radius-application-v1',job_id:42,profile_id:3,cv_updated_at:'2026-10-16T12:00:00Z',cv:[{id:1,text:'Tailored paragraph'}],cover_letter:'Known cover letter.',application_answers:'Q: Availability?\nA: Two weeks',review_notes:'Personalized for this role.'};
test('connects the actual Radius package schema to original CV facts without replacing identity',async()=>{
  const parsed=await readPackage('```json\n'+JSON.stringify(pack)+'\n```');
  const merged=connectSources({first_name:'Known',email:'original@example.invalid'},'CV email original@example.invalid',parsed,'https://jobs.example/apply');
  assert.equal(merged.first_name,'Known');assert.equal(merged.application.job_id,42);
  assert.equal(merged.application.application_answers,pack.application_answers);
  assert.match(merged.source_notes,/paragraph edits/);
});
test('decodes both existing Radius return formats locally',async()=>{
  const plain='https://radius.example/jobs/42/apply#application='+encodeURIComponent(JSON.stringify(pack));
  const compressed='https://radius.example/jobs/42/apply#applicationz='+deflateSync(JSON.stringify(pack)).toString('base64url');
  assert.deepEqual(await readPackage(plain),pack);assert.deepEqual(await readPackage(compressed),pack);
});
test('the bundled dummy return link decodes to the complete fictional package',async()=>{
  const link=await readFile(new URL('../../extensions/application-companion/dummy-return-link.txt',import.meta.url),'utf8');
  const data=JSON.parse(await readFile(new URL('../../extensions/application-companion/dummy-package.json',import.meta.url),'utf8'));
  assert.deepEqual(await readPackage(link),data);assert.equal(data.job_id,1574);
});
test('does not combine an explicitly different returned applicant with the original CV',()=>{
  assert.throws(()=>connectSources({email:'actual@example.invalid'},'Original CV',{
    ...pack,cover_letter:'Mara Neumann\nmara.neumann@example.com\n\nDear team, ...'
  },'https://forms.example/apply'),/different applicant/);
});
test('detects differing contact emails throughout returned candidate documents',()=>{
  for(const update of [
    {cover_letter:'Intro '.repeat(100)+'other@example.invalid'},
    {cv:'Candidate contact: other@example.invalid'},
    {application_answers:'Q: Email\nA: other@example.invalid'},
  ])assert.throws(()=>connectSources({email:'actual@example.invalid'},'Original CV',{...pack,...update},'https://forms.example/apply'),/different/);
});
test('rejects malformed and oversized packages and preserves differing known CV contact',async()=>{
  await assert.rejects(()=>readPackage('{"format":"wrong"}'));
  const large={...pack,application_answers:'x'.repeat(220000)};
  await assert.rejects(()=>readPackage('#applicationz='+deflateSync(JSON.stringify(large)).toString('base64url')));
  const merged=connectSources({email:'existing@example.invalid'},'other@example.invalid',null,'');
  assert.equal(merged.email,'existing@example.invalid');assert.match(merged.source_notes,/preserved/);
});

test('structured question answers reject duplicate, unfinished and control text', async () => {
 const form_id='a'.repeat(64);
 const original=JSON.parse(JSON.stringify(pack));
 for(const answers of [[{id:'q0',value:'One'},{id:'q0',value:'Two'}],[{id:'q0',value:'[TODO: answer]'}],[{id:'q0',value:'Unsafe\u0001text'}]])
  await assert.rejects(readPackage(JSON.stringify({...original,form_answers:{form_id,answers}})));
});

test('a source-only revised CV does not treat template metadata as applicant contacts',()=>{
 const source=String.raw`\documentclass{article}
% Template maintainer: other@example.invalid
\begin{document}
% radius:entry {"id":"work-1","kind":"work","company":"Labs","title":"Engineer","location":"Bonn","start":"2021","end":null,"current":true}
% radius:responsibilities
\begin{itemize}
\item Built research tools.
\end{itemize}
% radius:end
\end{document}`;
 const merged=connectSources({email:'actual@example.invalid'},source,{...pack,cv:'The current CV is available in its LaTeX source and PDF.',latex_documents:{cv:source}},'https://forms.example/apply');
 assert.equal(merged.email,'actual@example.invalid');
 assert.equal(merged.experiences[0].description,'Built research tools.');
});
