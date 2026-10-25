import test from 'node:test';
import assert from 'node:assert/strict';
import { validateProfile } from '../../extensions/application-companion/profile.js';
import { profileForLanguage } from '../../extensions/application-companion/profile-language.js';
test('companion import and export retain independent language content and reject shared facts inside translations', () => {
 const result=validateProfile({email:'alex@example.org',localized_content:{en:{summary:'Research',application_personalization:{writing_samples:'English voice'}},de:{summary:'Forschung',application_preferences:{notice_period:'Drei Monate'}}}});
 assert.equal(result.localized_content.de.summary,'Forschung');
 assert.equal(result.localized_content.en.application_personalization.writing_samples,'English voice');
 assert.throws(()=>validateProfile({localized_content:{fr:{summary:'Texte'}}}));
 assert.throws(()=>validateProfile({localized_content:{de:{email:'other@example.org'}}}));
 assert.throws(()=>validateProfile({localized_content:{de:{application_preferences:{salary_amount:'1'}}}}));
 assert.throws(()=>validateProfile({localized_content:{de:{application_preferences:{authorization_country:'Austria'}}}}));
 assert.throws(()=>validateProfile({localized_content:{de:{summary:'x'.repeat(5001)}}}));
 assert.equal(profileForLanguage({summary:'Legacy English'},'en').summary,'Legacy English');
 assert.equal(profileForLanguage({summary:'Legacy English'},'de').summary,undefined);
});

test('German form filling selects translated common answers without changing shared salary and contact facts', async () => {
 const { withCVLanguage } = await import('../../extensions/application-companion/tex-cv.js');
 const result = withCVLanguage({email:'alex@example.org',preferences:{salary_amount:'65000',notice_period:'Three months',authorization_country:'Germany',authorized_to_work:true},localized_content:{de:{application_preferences:{notice_period:'Drei Monate'}}}}, 'de-DE');
 assert.equal(result.preferences.notice_period,'Drei Monate');
 assert.equal(result.preferences.salary_amount,'65000');
 assert.equal(result.preferences.authorization_country,'Germany');
 assert.equal(result.preferences.authorized_to_work,true);
 assert.equal(result.email,'alex@example.org');
});


test('cover-letter examples survive companion validation in their own language and stay out of other-language briefs', async () => {
 const { buildCurrentJobPrompt } = await import('../../extensions/application-companion/current-job.js');
 const { readFileSync } = await import('node:fs');
 const template = JSON.parse(readFileSync(new URL('../../hiring_scraper/app/latex_template.json', import.meta.url)));
 const validated = validateProfile({localized_content:{en:{application_personalization:{cover_letter_example:'English letter reference'}},de:{application_personalization:{cover_letter_example:'Deutsches Anschreiben als Vorbild'}}}});
 const profile = {id:7,name:'Alex',...validated};
 const cv = {updated_at:'v1',text:template.preamble+String.raw`
% radius:entry {"id":"work-1","kind":"work","company":"Labs","title":"Engineer","location":"Bonn","start":"2021","end":null,"current":true}
% radius:responsibilities
Built tools.
% radius:end
`+template.ending};
 for (const [language,want,excluded] of [['en','English letter reference','Deutsches Anschreiben als Vorbild'],['de','Deutsches Anschreiben als Vorbild','English letter reference']]) {
  const prompt = buildCurrentJobPrompt({job:{id:42,title:'Engineer'},profile,cv,language});
  const payload = JSON.parse(prompt.split('SOURCE PAYLOAD (quoted data):\n').at(-1));
  assert.equal(payload.supporting_profile.application_personalization.cover_letter_example,want);
  assert.ok(!prompt.includes(excluded));
 }
 assert.throws(()=>validateProfile({localized_content:{de:{application_personalization:{cover_letter_example:'x'.repeat(16001)}}}}));
});
