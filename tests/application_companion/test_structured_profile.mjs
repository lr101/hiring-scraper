import test from 'node:test';
import assert from 'node:assert/strict';
import {validateProfile} from '../../extensions/application-companion/profile.js';
import {readPackage,connectSources} from '../../extensions/application-companion/sources.js';
const job={id:'research',company:'Example Labs',title:'Manager',location:'Karlsruhe, Germany',start:'2021',current:true,cv_bullet_ids:[12,13]};
const pack={format:'radius-application-v1',job_id:1574,profile_id:1,cv_updated_at:'v1',cv:[{id:12,text:'Managed research grants.'},{id:13,text:'Coordinated three partners.'}],cover_letter:'Letter',application_answers:''};
test('keeps structured facts, explicit common answers and stable job identity across saves',()=>{
 const profile=validateProfile({website:'https://example.invalid',preferences:{available_from:'2026-12-01',salary_amount:'65000',salary_currency:'EUR',salary_period:'year',authorization_country:'Germany',authorized_to_work:true,sponsorship_required:false,willing_to_relocate:false,notice_period:'One month',weekly_hours:'20',employment_type:'Part-time'},experiences:[job]});
 assert.equal(profile.experiences[0].location,'Karlsruhe, Germany');assert.equal(profile.preferences.sponsorship_required,false);
 assert.deepEqual(validateProfile(profile),profile);assert.equal(profile.experiences[0].id,'research');
 assert.equal(validateProfile({}).preferences.authorized_to_work,null);
});
test('rejects impossible availability, duplicate job IDs, overlapping CV bullet bindings and non-explicit booleans',()=>{
 for(const preferences of [{available_from:'2026-02-30'},{authorized_to_work:'yes'},{salary_amount:'65k'},{salary_currency:'euros'},{weekly_hours:'200'}])assert.throws(()=>validateProfile({preferences}));
 assert.throws(()=>validateProfile({experiences:[job,{...job,company:'Other'}]}));
 assert.throws(()=>validateProfile({experiences:[job,{...job,id:'other',company:'Other'}]}));
});
test('connects existing CV paragraph IDs as tailored bullets without replacing reusable facts',()=>{
 const base=validateProfile({experiences:[job]});const connected=connectSources(base,'',pack,'https://forms.example/structured');
 assert.deepEqual(connected.application.experience_bullets,[{experience_id:'research',bullets:['Managed research grants.','Coordinated three partners.']}]);
 assert.equal(connected.experiences[0].start,'2021');assert.equal(connected.experiences[0].location,job.location);
 assert.equal(connected.experiences[0].description,'');
 assert.throws(()=>connectSources(base,'',{...pack,cv:[pack.cv[0]]},'https://forms.example/structured'),/13/);
});
test('accepts explicit ChatGPT job IDs and rejects unknown or duplicate job bindings',async()=>{
 const explicit={...pack,cv:[],experience_bullets:[{experience_id:'research',bullets:['Tailored bullet.']}]};
 const base={experiences:[{...job,cv_bullet_ids:[]}]};
 const parsed=await readPackage(JSON.stringify(explicit));assert.equal(connectSources(base,'',parsed,'https://forms.example/structured').application.experience_bullets[0].bullets[0],'Tailored bullet.');
 assert.throws(()=>connectSources(base,'',{...explicit,experience_bullets:[{experience_id:'unknown',bullets:['Wrong job.']}]},''),/unknown/i);
 await assert.rejects(()=>readPackage(JSON.stringify({...explicit,experience_bullets:[explicit.experience_bullets[0],explicit.experience_bullets[0]]})),/duplicate/i);
});
