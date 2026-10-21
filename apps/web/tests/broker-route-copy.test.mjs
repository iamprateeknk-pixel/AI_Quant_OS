import {test} from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";

const source=readFileSync(new URL("../src/app/[section]/page.tsx",import.meta.url),"utf8");

test("configured broker flag is labeled separately from new-order admission",()=>{
 assert.match(source,/<th>Adapter enabled<\/th>/);
 assert.match(source,/<td>\{broker\.enabled \? "Enabled" : "Disabled"\}<\/td>/);
 assert.doesNotMatch(source,/<th>New orders<\/th>/);
});

test("configured routes table scroll region is keyboard accessible and named",()=>{
 assert.match(source,/<TableScrollRegion label="Configured broker routes table"><table>/);
});

test("configured route table wires the shared horizontal keyboard scroll behavior",()=>{
 assert.match(source,/TableScrollRegion label="Configured broker routes table"/);
});
