import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';
import {validateProfile} from '../../extensions/application-companion/profile.js';

const example = JSON.parse(await readFile(new URL('./profile.example.json', import.meta.url)));
test('accepts the existing example profile without inventing optional contacts', () => {
  const profile = validateProfile(example);
  assert.equal(profile.experiences.length, 3);
  assert.equal(profile.phone, '');
  assert.equal(profile.experiences[0].end, null);
});
test('rejects invalid dates, chronology errors and current/end conflicts', () => {
  for (const update of [{start:'2020-13'}, {start:'0000-01'}, {current:false,end:null}, {end:'2025-01'}, {current:false,start:'2026-01',end:'2025-01'}]) {
    const data = structuredClone(example);
    Object.assign(data.experiences[0], update);
    assert.throws(() => validateProfile(data));
  }
});
test('preserves known year precision without inventing months',()=>{
  const data=structuredClone(example);data.experiences[1].start='2022';data.experiences[1].end='2024';
  assert.equal(validateProfile(data).experiences[1].start,'2022');
});
test('rejects unsupported fields and duplicate job identities', () => {
  assert.throws(() => validateProfile({...example, work_authorization:true}));
  assert.throws(() => validateProfile({...example, experiences:[example.experiences[0],example.experiences[0]]}));
  assert.throws(() => validateProfile({...example, experiences:[{...example.experiences[0],current:'false'}]}));
});
test('retains missing contacts as blank and rejects unsafe URL schemes', () => {
  assert.equal(validateProfile({}).first_name, '');
  assert.throws(() => validateProfile({...example,linkedin:'javascript:alert(1)'}));
  assert.throws(() => validateProfile({...example,email:'unknown'}));
});
