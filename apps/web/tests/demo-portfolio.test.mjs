import {test} from 'node:test';
import assert from 'node:assert/strict';
import {existsSync,readFileSync} from 'node:fs';
import ts from 'typescript';
const path=new URL('../src/features/portfolio/demo-portfolio.ts',import.meta.url);
const fixture=JSON.parse(readFileSync(new URL('./fixtures/demo-portfolio.json',import.meta.url),'utf8'));
const now=fixture.observation.publication.record.observed_at;
async function parser(){assert(existsSync(path),'Demo portfolio parser must exist');return (await import(`data:text/javascript;base64,${Buffer.from(ts.transpileModule(readFileSync(path,'utf8'),{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText).toString('base64')}`)).parseDemoPortfolio;}
test('demo account amounts remain exact strings and unwanted private fields are stripped',async()=>{
 const parse=await parser(), value=structuredClone(fixture);value.password='fixture-private';value.observation.publication.record.account.login='fixture-private';
 const view=parse(value,now);assert.equal(view.state,'AVAILABLE');assert.equal(view.record.account.balance,'10000.0');assert.equal(view.record.positions[0].volume_lots,'0.02');assert.equal(view.record.orders[0].remaining_lots,'0.02');assert(!JSON.stringify(view).includes('fixture-private'));
});
test('render time uses original microsecond expiry and never extends a stale record',async()=>{
 const parse=await parser();assert.equal(parse(fixture,fixture.observation.publication.record.valid_until).state,'STALE');assert.equal(parse(fixture,'2026-10-09T11:59:59Z'),null);
 const value=structuredClone(fixture);value.observation.state='STALE';assert.equal(parse(value,now).state,'STALE');
});
test('invalid authority, source interval, money, duplicate identity and unsafe numeric tickets are withheld',async()=>{
 const parse=await parser();for(const change of [v=>v.may_affect_portfolio=true,v=>v.observation.publication.record.native_attestation='VERIFIED',v=>v.observation.publication.record.valid_until='2026-10-09T12:00:30Z',v=>v.observation.publication.record.account.balance='NaN',v=>v.observation.publication.record.positions.push(v.observation.publication.record.positions[0]),v=>v.observation.publication.record.orders[0].ticket=9007199254740992,v=>v.observation.publication.record.positions[0].currency='EUR']){const value=structuredClone(fixture);change(value);assert.equal(parse(value,now),null);}
});
test('unconfigured, empty and withheld frames never invent zero account values',async()=>{
 const parse=await parser();const value=structuredClone(fixture);value.observation.state='UNAVAILABLE';value.observation.publication=null;value.configured=false;assert.equal(parse(value,now).record,null);
 value.configured=true;value.observation.publication={...fixture.observation.publication,state:'EMPTY',record:null};assert.equal(parse(value,now).reason,'EMPTY');
 value.observation.publication.state='WITHHELD';value.observation.publication.code='SOURCE_UNAVAILABLE';assert.equal(parse(value,now).reason,'SOURCE_UNAVAILABLE');
});
test('microsecond lease boundary survives browser millisecond timestamp precision',async()=>{
 const parse=await parser(),value=structuredClone(fixture),p=value.observation.publication;
 p.published_at=p.record.observed_at='2026-10-09T12:00:00.000001Z';p.record.valid_until='2026-10-09T12:00:03.000001Z';
 assert.equal(parse(value,'2026-10-09T12:00:03Z').state,'AVAILABLE');assert.equal(parse(value,p.record.valid_until).state,'STALE');
});
test('negative credit cannot hide behind decimal underflow and counts remain bounded',async()=>{
 const parse=await parser();for(const change of [v=>v.observation.publication.record.account.credit='-1e-999',v=>v.observation.publication.record.orders=Array(9).fill(v.observation.publication.record.orders[0])]){const value=structuredClone(fixture);change(value);assert.equal(parse(value,now),null);}
});
