import { companionMarkdown } from './companion-markdown.js';
// Versioned, inert view models. Markdown pre-escapes content; templates escape other values.
import { readable, openFoundrySource } from './foundry-context.js';
import { knowledgeReaderUrl } from './knowledge-companion.js';

const types = new Set(['narrative','image','gallery','entity_card','table','book_excerpt']);
export function safeRichAssetPath(service, path) {
  if (typeof path !== 'string') return false;
  if (service === 'shrecknet') return /^\/users\/me\/character-companion\/turns\/[a-f0-9-]{36}\/assets\/a[a-f0-9]{20}$/.test(path);
  return service === 'shreckworld' && /^\/(?:ontologies\/[1-9][0-9]*\/items|shreckworlds\/[A-Za-z0-9_-]+\/library-items)\/[A-Za-z0-9_-]+\/documents\/[A-Za-z0-9_-]+\/media\/p[1-9][0-9]*-(?:thumbnail|image-[0-2])\?revision=[a-f0-9]{64}$/.test(path);
}
export function publicUrl(value) {
  try { const url = new URL(value); return ['http:','https:'].includes(url.protocol) && !url.username && !url.password ? url.href : null; } catch { return null; }
}
export function richMessage(message) {
  const details=message.answer_details, rich=details?.rich_response;
  try {
    if (!rich || rich.version!==1 || !Array.isArray(rich.blocks) || !rich.blocks.length || rich.blocks.length>8 || !Array.isArray(rich.assets) || rich.assets.length>4 || !Array.isArray(rich.targets) || rich.targets.length>20 || JSON.stringify(rich).length>65536) return null;
    const assetIds=new Set(rich.assets.map(a=>a.id)), targetIds=new Set(rich.targets.map(t=>t.id));
    if (assetIds.size!==rich.assets.length || targetIds.size!==rich.targets.length || new Set(rich.blocks.map(b=>b.id)).size!==rich.blocks.length) return null;
    const segmentIds=rich.blocks.flatMap(b=>b.type==='narrative'?b.segment_ids:[]);
    if (segmentIds.length!==details.segments.length || segmentIds.some((id,index)=>id!==details.segments[index].id) || new Set(segmentIds).size!==segmentIds.length) return null;
    const evidenceIds=new Set(details.evidence.map(e=>e.evidence_id));
    const refsValid=(ids)=>Array.isArray(ids)&&ids.every(id=>evidenceIds.has(id));
    if (rich.targets.some(t=>!['atlas','shreckworld','chronicle','foundry','web'].includes(t.kind)||!t.reference))return null;
    if (rich.assets.some(a=>!targetIds.has(a.target_id)||!refsValid(a.evidence_ids)||
      !(a.delivery_ref?.kind==='foundry_local'||(a.delivery_ref?.kind==='external'&&publicUrl(a.delivery_ref.url))||(a.delivery_ref?.kind==='service'&&safeRichAssetPath(a.delivery_ref.service,a.delivery_ref.path)))))return null;
    const blocks=[];
    for (const b of rich.blocks) {
      if (!types.has(b.type)||!refsValid(b.evidence_ids)||(b.target_id&&!targetIds.has(b.target_id))||(b.asset_id&&!assetIds.has(b.asset_id)))return null;
      if (b.type==='table' && (!Array.isArray(b.columns)||!b.columns.length||b.columns.length>8||!Array.isArray(b.rows)||!b.rows.length||b.rows.length>20||new Set(b.rows.map(r=>r.id)).size!==b.rows.length||b.rows.some(r=>r.cells.length!==b.columns.length||!refsValid(r.evidence_ids)||r.cells.some(c=>!refsValid(c.evidence_ids)||!['string','number','boolean'].includes(typeof c.value)&&c.value!==null))))return null;
      if (b.type==='gallery'&&(!Array.isArray(b.items)||!b.items.length||b.items.length>4||b.items.some(i=>!assetIds.has(i.asset_id)||!refsValid(i.evidence_ids)||(i.target_id&&!targetIds.has(i.target_id)))))return null;
      const sources=(ids,target)=>ids.map(eid=>({messageId:message.id,evidenceId:eid,targetId:target,label:details.evidence.find(e=>e.evidence_id===eid)?.title}));
      const visual=(assetId)=>{const asset=rich.assets.find(a=>a.id===assetId);return asset?{messageId:message.id,id:asset.id,attribution:asset.attribution,external:asset.delivery_ref.kind==='external'}:null;};
      blocks.push({...b,messageId:message.id,narrative:b.type==='narrative',entity:b.type==='entity_card',excerpt:b.type==='book_excerpt',table:b.type==='table',image:b.type==='image',gallery:b.type==='gallery',
        passages:b.type==='narrative'?b.segment_ids.map(id=>{const s=details.segments.find(s=>s.id===id);return {text:s.text,markdown:companionMarkdown(s.text),sources:sources(s.evidence_ids,null)};}):[],
        visual:visual(b.asset_id),visuals:b.type==='gallery'?b.items.map(i=>visual(i.asset_id)):[],sources:sources(b.evidence_ids,b.target_id),
        columns:b.type==='table'?b.columns.map((c,index)=>({...c,index})):[],
        rows:b.type==='table'?b.rows.map(r=>({...r,cells:r.cells.map(c=>({text:c.value===null?'—':String(c.value),sources:sources(c.evidence_ids,r.target_id)}))})):[]});
    }
    return {blocks,assets:rich.assets,targets:rich.targets};
  } catch { return null; }
}

export async function foundryImage(reference) {
  if (reference.foundry_world_id!==globalThis.game?.world?.id)throw new Error('Open this image in its original Foundry table.');
  const doc=await foundry.utils.fromUuid(reference.uuid);
  if (!readable(doc))throw new Error('Image unavailable.');
  const revision=doc._stats?.modifiedTime==null?null:String(doc._stats.modifiedTime);
  if (reference.source_revision!==revision)throw new Error('This document image has changed.');
  // Only the current document's own artwork; never follow arbitrary snapshot URLs.
  const url=doc.documentName==='JournalEntryPage'&&doc.type==='image'?doc.src:doc.img;
  if (!url || /^(?:data:|javascript:|file:|blob:|\/\/)/i.test(url))throw new Error('Image unavailable.');
  const resolved=new URL(url,globalThis.location?.href);
  if (resolved.origin!==globalThis.location?.origin)throw new Error('External Foundry images are unavailable here.');
  if (!readable(doc))throw new Error('Image access changed.');
  return resolved.href;
}

export async function openRichSource(workspace, messageId, evidenceId, targetId) {
  const message=workspace.messages.find(m=>m.id===messageId),rich=message&&richMessage(message);
  const evidence=message?.answer_details?.evidence?.find(e=>e.evidence_id===evidenceId);
  if (!evidence || !rich)throw new Error('Source unavailable.');
  const target=rich.targets.find(t=>t.id===targetId);
  if (target && !rich.blocks.some(b=>b.target_id===targetId&&b.evidence_ids.includes(evidenceId)))throw new Error('Source is not attached to this block.');
  const kind=target?.kind||evidence.source_type,ref=target?.reference||evidence.reference;
  if (kind==='foundry')return openFoundrySource(ref);
  if (kind==='atlas') {
    if (!workspace.ontologies.some(o=>o.id===ref.ontology_id))throw new Error('This source belongs to another world.');
    const kind=ref.node_label==='Scene'?'scene':'entity';
    const url=new URL(`/home/worlds/${kind}/${encodeURIComponent(ref.ontology_id+':'+ref.node_id)}`,workspace.app.connection.serverUrl);
    window.open(url.href,'_blank','noopener,noreferrer');return;
  }
  let url;
  if (kind==='web')url=publicUrl(ref.url);
  if (kind==='shreckworld') {
    url=new URL(knowledgeReaderUrl(workspace.app.connection.serverUrl,ref.shreckworld_id||evidence.scope?.shreckworld_id,ref.library_item_id));
    if (ref.ontology_id||evidence.scope?.ontology_id)url.searchParams.set('ontologyId',String(ref.ontology_id||evidence.scope.ontology_id));
    if (ref.page_number)url.searchParams.set('page',String(ref.page_number));
  }
  if (kind==='chronicle') {
    url=new URL('/home/playerdesk/chronicle/'+encodeURIComponent(ref.chronicle_id),workspace.app.connection.serverUrl);
    if (ref.section_id)url.searchParams.set('chronicleSection',ref.section_id);
  }
  if (!url)throw new Error('This source cannot be opened here.');
  window.open(String(url),'_blank','noopener,noreferrer');
}

export function bindRichMedia(workspace, root) {
  root?.querySelectorAll('[data-companion-rich-asset]').forEach(node=>{
    const messageId=node.dataset.messageId,assetId=node.dataset.companionRichAsset;
    const message=workspace.messages.find(m=>m.id===messageId),rich=message&&richMessage(message);
    const asset=rich?.assets.find(a=>a.id===assetId);
    if (!asset)return;
    const load=async()=>{
      const generation=workspace.generation,session=workspace.app.session;
      try {
        let url;
        const delivery=asset.delivery_ref;
        if (delivery.kind==='foundry_local')url=await foundryImage(delivery.reference);
        else if (delivery.kind==='external')url=publicUrl(delivery.url);
        else {
          if (!safeRichAssetPath(delivery.service,delivery.path))throw new Error('Invalid asset path.');
          const base=delivery.service==='shrecknet'?workspace.app.connection.serverUrl:workspace.app._serviceUrl('shreckWorld');
          url=await workspace.app._loadObjectUrl(`companion:${messageId}:${assetId}`,()=>workspace.app.client.requestAsset(base,delivery.path));
        }
        if (generation!==workspace.generation||session!==workspace.app.session||!node.isConnected)return;
        if (!url)throw new Error('Image unavailable.');
        const img=node.querySelector('img');
        img.onerror=()=>{img.hidden=true;node.querySelector('[role="status"]').textContent='Image unavailable';};
        img.referrerPolicy='no-referrer';img.src=url;img.hidden=false;
        node.querySelector('[role="status"]').textContent='';
        const enlarge=node.querySelector('[data-rich-enlarge]');
        enlarge.hidden=false;
        enlarge.onclick=()=>{
          const dialog=document.createElement('dialog'),picture=document.createElement('img'),close=document.createElement('button');
          dialog.className='shrecknet-companion-lightbox';picture.src=url;picture.alt='Source image';picture.referrerPolicy='no-referrer';close.textContent='Close image';
          close.onclick=()=>dialog.close();dialog.addEventListener('close',()=>dialog.remove());dialog.append(picture,close);document.body.append(dialog);dialog.showModal();
        };
      } catch { if (node.isConnected)node.querySelector('[role="status"]').textContent='Image unavailable'; }
    };
    if (asset.delivery_ref.kind==='external')node.querySelector('[data-rich-external]')?.addEventListener('click',()=>void load(),{once:true});
    else void load();
  });
  root?.querySelectorAll('[data-rich-sort]').forEach(button=>button.addEventListener('click',()=>{
    const table=button.closest('table'),body=table.querySelector('tbody'),index=Number(button.dataset.richSort);
    const descending=button.dataset.descending!=='true';button.dataset.descending=String(descending);
    [...body.rows].sort((a,b)=>a.cells[index].textContent.localeCompare(b.cells[index].textContent,undefined,{numeric:true})*(descending?-1:1)).forEach(row=>body.append(row));
    table.querySelectorAll('th').forEach(th=>th.setAttribute('aria-sort','none'));
    button.closest('th').setAttribute('aria-sort',descending?'descending':'ascending');
  }));
}
