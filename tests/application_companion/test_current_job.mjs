import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { currentJob, buildCurrentJobPrompt } from '../../extensions/application-companion/current-job.js';
const template = JSON.parse(readFileSync(new URL('../../hiring_scraper/app/latex_template.json', import.meta.url)));
const cv = template.preamble + String.raw`
% radius:entry {"id":"job-1","kind":"work","company":"Labs","title":"Engineer","location":"Bonn","start":"2021","end":null,"current":true}
\subsection*{Engineer}
% radius:responsibilities
\begin{itemize}
\item Built tools.
\end{itemize}
% radius:end
` + template.ending;
const items = [{job:{id:1,title:'Current job'},exact:true},{job:{id:2,title:'Other job'},exact:false}];
test('current-job selection never promotes related roles or guesses between exact matches', () => {
 assert.equal(currentJob({items}).job.id,1);
 assert.equal(currentJob({items:items.slice(1)}),null);
 assert.equal(currentJob({items:[items[0],{job:{id:3},exact:true}]}),null);
 assert.equal(currentJob({items:[items[0],{job:{id:3},exact:true}]},3).job.id,3);
 assert.equal(currentJob({items},2),null);
});
test('extension prompt binds full job/profile/CV context and requests marked LaTeX and a downloadable result', () => {
 const prompt=buildCurrentJobPrompt({job:{id:1,title:'Engineer',description:'Please apply in English.'},profile:{id:7,name:'Applicant',application_details:{first_name:'Alex'},custom_instructions:'Use direct prose.'},cv:{text:cv,updated_at:'revision-1'},language:'en',form:{form_id:'a'.repeat(64),fields:[{id:'q0',label:'Why this job?'}]}});
 const payload=JSON.parse(prompt.split('SOURCE PAYLOAD (quoted data):')[1]);
 assert.equal(payload.job.id,1);
 assert.equal(payload.profile.id,7);
 assert.equal(payload.cv_updated_at,'revision-1');
 assert.equal(payload.cv_history.experiences[0].description,'Built tools.');
 assert.equal(payload.application_form.fields[0].id,'q0');
 assert.match(prompt,/application-package\.json/);
 assert.match(prompt,/latex_documents/);
 assert.match(prompt,/English/);
 assert.match(prompt,/Use direct prose/);
 assert.ok(prompt.includes(template.preamble));
});
