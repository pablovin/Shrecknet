import assert from 'node:assert/strict';
import test from 'node:test';
import {readFile} from 'node:fs/promises';
import {richMessage,safeRichAssetPath,foundryImage,openRichSource} from '../scripts/apps/companion-rich-response.js';
const fixture=JSON.parse(await readFile(new URL('./fixtures/companion-rich-response.v1.json',import.meta.url),'utf8'));

test('shared v1 fixture renders six types and preserves zero/cell sources',()=>{
 const view=richMessage(fixture);
 assert.deepEqual(view.blocks.map(b=>b.type),['narrative','entity_card','image','gallery','table','book_excerpt']);
 assert.equal(view.blocks[4].rows[0].cells[1].text,'0');
 assert.equal(view.blocks[4].rows[0].cells[1].sources[0].evidenceId,'foundry:e2');
});
test('unknown versions, missing assets and malformed tables retain text fallback',()=>{
 const message=structuredClone(fixture);message.answer_details.rich_response.version=2;assert.equal(richMessage(message),null);
 message.answer_details.rich_response.version=1;message.answer_details.rich_response.assets=[];assert.equal(richMessage(message),null);
 assert.equal(safeRichAssetPath('shrecknet','//evil.test/image'),false);
 assert.equal(safeRichAssetPath('shrecknet','/media/image?token=secret'),false);
});
test('Foundry images require the original table, unchanged revision and current read permission',async()=>{
 globalThis.game={world:{id:'table'},user:{id:'reader'}};
 globalThis.location={href:'https://foundry.test/game',origin:'https://foundry.test'};
 let allowed=true;
 const doc={documentName:'Actor',img:'worlds/table/hero.png',_stats:{modifiedTime:1},testUserPermission:()=>allowed};
 globalThis.foundry={utils:{fromUuid:async()=>doc}};
 const ref={foundry_world_id:'table',uuid:'Actor.hero',source_revision:'1'};
 assert.equal(await foundryImage(ref),'https://foundry.test/worlds/table/hero.png');
 await assert.rejects(foundryImage({...ref,foundry_world_id:'other'}),/original Foundry table/);
 await assert.rejects(foundryImage({...ref,source_revision:'2'}),/changed/);
 allowed=false;await assert.rejects(foundryImage(ref),/unavailable/);
 delete globalThis.game;delete globalThis.foundry;delete globalThis.location;
});
test('source controls cannot borrow a citation from another saved message',async()=>{
 await assert.rejects(openRichSource({messages:[fixture]},'other','atlas:e1','t1'),/unavailable/);
 await assert.rejects(openRichSource({messages:[fixture]},fixture.id,'foundry:e2','t1'),/not attached/);
});

test('book source navigation preserves physical page and ontology, without credentials',async()=>{
 const opened=[];globalThis.window={open:(...args)=>opened.push(args)};
 const workspace={messages:[fixture],ontologies:[{id:7}],app:{connection:{serverUrl:'https://site.test/backend_api'}}};
 await openRichSource(workspace,fixture.id,'shreckworld:e3','t3');
 const url=new URL(opened[0][0]);
 assert.equal(url.pathname,'/home/shreckworld/shreckworld/shreckworld');
 assert.equal(url.searchParams.get('page'),'12');assert.equal(url.searchParams.get('ontologyId'),'7');
 assert.equal(url.searchParams.get('sourceId'),'book');assert.equal(opened[0][2],'noopener,noreferrer');
 delete globalThis.window;
});
