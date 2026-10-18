import assert from 'node:assert/strict'
import { buildApplicationPrompt, parseApplicationPackage } from '../frontend/src/applicationHandoff.ts'
const form={format:'radius-form-v1',job_id:42,profile_id:9,form_id:'a'.repeat(64),url:'https://employer.example/apply?jobId=42',captured_at:Date.now(),fields:[{id:'q0',label:'Why this role?',type:'textarea',required:true,max_length:180,options:[],accept:'',manual:false}]}
const context={job_id:42,profile_id:9,cv_updated_at:'v1'}
const prompt=buildApplicationPrompt({job:{id:42,title:'Coordinator',company_name:'Labs',url:'https://employer.example/jobs/42',description:'Coordinate projects.'},cvText:'Original CV',language:'English',form})
assert.ok(prompt.includes('Why this role?')&&prompt.includes('form_answers')&&prompt.includes('180'),'Preparation must include the actual questions and answer limits')
const pack={format:'radius-application-v1',...context,cv:'CV',cover_letter:'Letter',application_answers:'Known answers',review_notes:'Tailored.',form_answers:{form_id:form.form_id,answers:[{id:'q0',value:'I coordinate research projects.'}]}}
const parsed=parseApplicationPackage(JSON.stringify(pack),context,undefined,form)
assert.deepEqual(parsed.formAnswers,pack.form_answers)
for(const answers of [{form_id:'b'.repeat(64),answers:pack.form_answers.answers},{form_id:form.form_id,answers:[{id:'q0',value:'x'.repeat(181)}]},{form_id:form.form_id,answers:[{id:'q9',value:'Unknown'}]}]) assert.throws(()=>parseApplicationPackage(JSON.stringify({...pack,form_answers:answers}),context,undefined,form))
console.log('Form-aware prompt and bound answer validation passed')
const { readApplicationForm, formStorageKey }=await import('../frontend/src/applicationForm.ts')
globalThis.localStorage={getItem:()=>JSON.stringify({...form,fields:[null]})}
assert.equal(readApplicationForm(42,9),undefined,'Corrupted captured fields must be ignored')
const local=await import('../frontend/src/applicationPreparation.ts')
const localPrompt=local.buildApplicationPrompt({text:'Original CV',updated_at:'v1'},{job:{id:42,title:'Coordinator',company_name:'Labs',url:form.url,description:'Work',location_text:null,employment_type:null,work_arrangement:null},profile:{id:9,name:'Alex'},returnUrl:'https://radius.example/jobs/42/apply',form})
assert.ok(localPrompt.includes('Why this role?')&&localPrompt.includes('form_answers'))
await assert.rejects(local.parseReturnedPackage(JSON.stringify({...pack,form_answers:{form_id:'invalid',answers:[]}}),{jobId:42,profileId:9,cvUpdatedAt:'v1'}))
