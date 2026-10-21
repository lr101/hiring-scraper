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
