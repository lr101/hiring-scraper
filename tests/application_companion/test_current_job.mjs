import test from 'node:test';
import assert from 'node:assert/strict';
import { currentJob } from '../../extensions/application-companion/current-job.js';
const items = [{job:{id:1,title:'Current job'},exact:true},{job:{id:2,title:'Other job'},exact:false}];
test('current-job selection never promotes related roles or guesses between exact matches', () => {
 assert.equal(currentJob({items}).job.id,1);
 assert.equal(currentJob({items:items.slice(1)}),null);
 assert.equal(currentJob({items:[items[0],{job:{id:3},exact:true}]}),null);
 assert.equal(currentJob({items:[items[0],{job:{id:3},exact:true}]},3).job.id,3);
 assert.equal(currentJob({items},2),null);
});
