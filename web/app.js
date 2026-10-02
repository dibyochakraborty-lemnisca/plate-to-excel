const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const state = {experiments:[], experiment:null, view:'capture',  editingExperiment:false, uploading:false, busy:false, urls:[], reviewing:null, reviewDirty:false};
let db;
const openDB = new Promise((resolve,reject) => {
  const request = indexedDB.open('plate-lab',1);
  request.onupgradeneeded = () => {request.result.createObjectStore('photos',{keyPath:'id'}); request.result.createObjectStore('settings');};
  request.onsuccess = () => {db=request.result;resolve(db);}; request.onerror=()=>reject(request.error);
});
async function storage(store,method,value,key) {
  await openDB;
  return new Promise((resolve,reject) => {
    const transaction=db.transaction(store,method==='get'||method==='getAll'?'readonly':'readwrite');
    const request=key===undefined?transaction.objectStore(store)[method](value):transaction.objectStore(store)[method](value,key);
    transaction.oncomplete=()=>resolve(request.result); transaction.onerror=()=>reject(transaction.error); transaction.onabort=()=>reject(transaction.error);
  });
}
function notice(message,error=false){$('notice').textContent=message;$('notice').className=error?'error':'';$('notice').hidden=false;}
function fail(error){notice(error.message||String(error),true);}
async function api(path,options={}) {
  const headers={'X-Plate-Request':'1',...options.headers};
  if(options.body && !(options.body instanceof FormData)){headers['Content-Type']='application/json';options.body=JSON.stringify(options.body);}
  let response;
  try{response=await fetch('/api'+path,{...options,headers,signal:AbortSignal.timeout(45000)});}catch{throw new Error('Connection interrupted. Your saved photos are safe on this device. Retry when connected.');}
  if(!response.ok){let data;try{data=await response.json();}catch{data={detail:'Request failed. Please retry.'};}const error=new Error(typeof data.detail==='string'?data.detail:'Check the form values and try again.');error.status=response.status;throw error;}
  return response.status===204?null:response.json();
}
function localTime(iso=new Date().toISOString()){const date=new Date(iso);return new Date(date.getTime()-date.getTimezoneOffset()*60000).toISOString().slice(0,16);}
function formatTime(iso){return new Date(iso).toLocaleString([], {dateStyle:'medium',timeStyle:'short'});}
function connection(){ $('connection').textContent=navigator.onLine?'Online':'Offline · saving locally'; }
async function cacheExperiments(){await storage('settings','put',{experiments:state.experiments,selected:state.experiment?.id},'experiments');}
function renderExperimentPicker(){ $('experiment-select').innerHTML='<option value="">Choose an experiment</option>'+state.experiments.map(exp=>`<option value="${esc(exp.id)}">${esc(exp.name)}</option>`).join('');$('experiment-select').value=state.experiment?.id||''; }
async function loadExperiments(){state.experiments=await api('/experiments');renderExperimentPicker();await cacheExperiments();}
async function selectExperiment(id){
  if(state.reviewDirty&&!confirm('Discard the unsaved reading edits before changing experiments?')){$('experiment-select').value=state.experiment.id;return;}
  state.reviewDirty=false;state.reviewing=null;
  if(!id){state.experiment=null;renderExperiment();return;}
  try{if(!navigator.onLine)throw new Error('offline');state.experiment=await api('/experiments/'+id);await storage('settings','put',state.experiment,'detail-'+id);}
  catch(error){if(error.status)throw error;state.experiment=await storage('settings','get','detail-'+id);if(!state.experiment)throw new Error('Open this experiment online once before capturing offline.');$('connection').textContent='Connection unavailable · saving locally';}
  await cacheExperiments();renderExperimentPicker();renderExperiment();
}
function templateSummary(template){return template?`FL-${String(template.flask_start).padStart(2,'0')} to FL-${String(template.flask_start+template.flask_count-1).padStart(2,'0')} · ${template.replicates} replicates · ${template.row_direction==='bottom'?'bottom to top':'top to bottom'}, ${template.column_direction==='left'?'left to right':'right to left'}`:'Add a reading template in Setup before capturing.';}
function templateOptions(selected){return state.experiment.templates.map(template=>`<option value="${esc(template.id)}" ${template.id===selected?'selected':''}>${esc(template.name)}</option>`).join('');}
function renderExperiment(){
  const exp=state.experiment;$('experiment-body').hidden=!exp;$('no-experiment').hidden=!!exp;if(!exp)return;
  const current=localStorage.getItem('plate-template-'+exp.id)||$('capture-template').value;$('capture-template').innerHTML=templateOptions(current);
  $('template-summary').textContent=templateSummary(exp.templates.find(t=>t.id===$('capture-template').value));
  for(const id of ['camera','upload'])$(id).disabled=!exp.templates.length;
  $('camera-label').classList.toggle('disabled',!exp.templates.length);$('upload-label').classList.toggle('disabled',!exp.templates.length);
  $('review-count').textContent=exp.captures.filter(c=>c.status==='ready').length||'';
  renderServerPhotos();renderQueue().catch(fail);renderSetup();renderReviewPicker();renderPlots();
}
function switchView(view){
  if(state.reviewDirty&&view!=='review'&&!confirm('Discard the unsaved reading edits?'))return;
  if(view!=='review')state.reviewDirty=false;
  state.view=view;
  for(const button of document.querySelectorAll('[data-view]')){button.setAttribute('aria-current',button.dataset.view===view?'page':'false');$('view-'+button.dataset.view).hidden=button.dataset.view!==view;}
  if(view==='review')renderReview();
}
async function refresh(){
  if(!state.experiment||!navigator.onLine)return;
  const id=state.experiment.id;const exp=await api('/experiments/'+id);
  if(state.experiment?.id!==id)return;
  state.experiment=exp;await storage('settings','put',exp,'detail-'+id);
  renderServerPhotos();renderSetup();renderReviewPicker();renderPlots();
  $('review-count').textContent=exp.captures.filter(c=>c.status==='ready').length||'';
  if(state.view==='review'&&!state.reviewDirty&&state.reviewing?.revision!==exp.captures.find(c=>c.id===$('review-photo').value)?.revision)renderReview();
}
async function saveFiles(files){
  if(!files.length)return;
  const experiment=state.experiment;const templateId=$('capture-template').value;
  if(!templateId)throw new Error('Add or select a reading template first.');
  let saved=0;
  for(const file of files){
    if(file.size>20*1024*1024){notice(`${file.name} exceeds 20 MB. Other photos can still be added.`,true);continue;}
    if(!['image/jpeg','image/png','image/webp'].includes(file.type)){notice(`${file.name}: use JPEG, PNG or WebP. Choose “Most Compatible” in iPhone camera settings if it creates HEIC photos.`,true);continue;}
    const bitmap=await createImageBitmap(file);const pixels=bitmap.width*bitmap.height;bitmap.close();
    if(pixels>25000000){notice(`${file.name} exceeds 25 megapixels. Use a smaller image.`,true);continue;}
    const photo={id:crypto.randomUUID(),experimentId:experiment.id,templateId,filename:file.name||'Capture.jpg',capturedAt:new Date().toISOString(),blob:file,status:'local'};
    try{await storage('photos','put',photo);}catch{throw new Error('This device could not save the photo. Free storage and retry; keep your original photo.');}
    saved++;
  }
  if(saved){notice(`${saved} photo${saved===1?'':'s'} saved on this device.`);await navigator.storage?.persist?.();}
  await renderQueue();
  if(navigator.onLine)uploadQueue().catch(fail);
}
async function renderQueue(){
  if(!state.experiment)return;
  const photos=(await storage('photos','getAll')).filter(p=>p.experimentId===state.experiment.id);
  for(const url of state.urls)URL.revokeObjectURL(url);state.urls=[];
  $('queue-count').textContent=photos.length?`(${photos.length})`:'';
  $('upload-queue').disabled=!photos.length||state.uploading||!navigator.onLine;
  $('local-queue').innerHTML=photos.length?photos.map(photo=>{
    const url=URL.createObjectURL(photo.blob);state.urls.push(url);
    return `<article class="photo-row"><img src="${url}" alt="Saved photo preview"><div class="photo-info"><div class="photo-name">${esc(photo.filename)}</div><p>${esc(formatTime(photo.capturedAt))}</p><span class="badge">${state.uploading?'Waiting for upload confirmation':'Saved on this device'}</span>${photo.error?`<p class="error-text">${esc(photo.error)}</p>`:''}</div><button class="quiet" data-remove-local="${photo.id}" ${state.uploading?'disabled':''}>Remove</button></article>`;
  }).join(''):'<p class="empty">No photos waiting to upload. Take a photo to start.</p>';
}
async function uploadQueue(){
  if(state.uploading||!navigator.onLine)return;
  state.uploading=true;await renderQueue();
  try{
    const photos=(await storage('photos','getAll')).sort((a,b)=>a.capturedAt.localeCompare(b.capturedAt));
    for(const photo of photos){
      const data=new FormData();data.append('id',photo.id);data.append('template_id',photo.templateId);data.append('captured_at',photo.capturedAt);data.append('photo',photo.blob,photo.filename);
      try{await api(`/experiments/${photo.experimentId}/photos`,{method:'POST',body:data});await storage('photos','delete',photo.id);}
      catch(error){photo.error=error.message;await storage('photos','put',photo);notice(error.message,true);break;}
    }
    await refresh();
  }finally{state.uploading=false;await renderQueue();}
}
const statusLabels={uploaded:'Uploaded · ready to extract',queued:'Waiting for extraction',processing:'Reading photo…',ready:'Needs review',reviewed:'Reviewed',failed:'Extraction failed'};
function renderServerPhotos(){
  const captures=state.experiment.captures;
  $('capture-list').innerHTML=captures.length?captures.map(c=>`<article class="photo-row"><img src="/api/photos/${c.id}/image" alt="${esc(c.filename)}" loading="lazy"><div class="photo-info"><div class="photo-name">${esc(c.filename)}</div><p>${esc(formatTime(c.captured_at))} · ${esc(c.template.name)} · sample ${c.sample_index+1}</p><span class="badge ${c.status==='failed'?'failed':''}">${statusLabels[c.status]||esc(c.status)}</span>${c.error?`<p class="error-text">${esc(c.error)}</p>`:''}</div>${['ready','reviewed'].includes(c.status)?`<button class="secondary" data-review="${c.id}">Review</button>`:['uploaded','failed'].includes(c.status)?`<button class="secondary" data-process="${c.id}">${c.status==='failed'?'Retry':'Extract'}</button>`:''}</article>`).join(''):'<p class="empty">Uploaded photos will appear here and stay in this workspace.</p>';
  $('process-all').disabled=!navigator.onLine||!captures.some(c=>['uploaded','failed'].includes(c.status));
}
function renderSetup(){
  const exp=state.experiment;$('sheet-status').textContent=exp.sheet_id?`Sync: ${exp.sync_status}`:'No Google Sheet linked yet. Add its URL in Edit experiment.';
  $('sheet-error').textContent=exp.sync_error;$('sheet-link').hidden=!exp.sheet_id;$('sheet-link').href=exp.sheet_id?`https://docs.google.com/spreadsheets/d/${encodeURIComponent(exp.sheet_id)}`:'#';
  $('sync-sheet').disabled=!exp.sheet_id||!navigator.onLine||['syncing','pending'].includes(exp.sync_status);
  $('templates').innerHTML=exp.templates.length?exp.templates.map(t=>`<div class="template-row"><div><strong>${esc(t.name)}</strong><p class="muted">${esc(templateSummary(t))}</p></div><button class="quiet" data-copy-template="${t.id}">Duplicate</button></div>`).join(''):'<p class="empty">Add your first template to start capturing.</p>';
}
function editExperiment(existing){
  state.editingExperiment=existing;$('experiment-editor').hidden=false;$('experiment-form-title').textContent=existing?'Edit experiment':'New experiment';
  const form=$('experiment-form');form.elements.namedItem('name').value=existing?state.experiment.name:'';form.started_at.value=localTime(existing?state.experiment.started_at:undefined);form.sheet_id.value=existing&&state.experiment.sheet_id?`https://docs.google.com/spreadsheets/d/${state.experiment.sheet_id}`:'';
  form.elements.namedItem('name').focus();$('experiment-editor').scrollIntoView({block:'start',behavior:'smooth'});
}
function editTemplate(template){
  $('template-editor').hidden=false;const form=$('template-form');form.reset();$('template-preview').innerHTML='';
  if(template){for(const [key,value] of Object.entries(template))if(form.elements.namedItem(key))form.elements.namedItem(key).value=value;form.elements.namedItem('name').value=template.name+' copy';}
  form.elements.namedItem('name').focus();
}
function templateData(){const data=Object.fromEntries(new FormData($('template-form')));for(const key of ['flask_start','flask_count','replicates','start_slot'])data[key]=Number(data[key]);return data;}
function renderReviewPicker(){
  const previous=$('review-photo').value;const photos=state.experiment.captures.filter(c=>['ready','reviewed'].includes(c.status));
  $('review-photo').innerHTML=photos.map(c=>`<option value="${c.id}">${esc(c.filename)} · ${esc(formatTime(c.captured_at))}</option>`).join('');
  if(photos.some(c=>c.id===previous))$('review-photo').value=previous;
}
function renderReview(){
  const capture=state.experiment.captures.find(c=>c.id===$('review-photo').value);state.reviewing=capture;
  if(!capture){$('review-content').innerHTML='<p class="empty">Extract a saved photo to review its values and mapping.</p>';return;}
  const options=(items,value)=>items.map(x=>`<option ${x===value?'selected':''}>${esc(x)}</option>`).join('');
  $('review-content').innerHTML=`<div class="review-layout"><div><a href="/api/photos/${capture.id}/image" target="_blank" rel="noopener"><img class="source-photo" src="/api/photos/${capture.id}/image" alt="${esc(capture.filename)} — open full-size"></a><p class="muted">Original photo · ${esc(formatTime(capture.original_captured_at))}</p><details><summary>Change sample or template</summary><p class="muted">Remapping restores values from the original extraction. Your earlier saved review stays in the audit history.</p><label>Template<select id="remap-template">${templateOptions(capture.template_id)}</select></label><label>Sample number within this template<input type="number" id="remap-sample" min="1" value="${capture.sample_index+1}"></label><button id="remap" class="secondary">Preview new mapping</button></details></div><div><h3>${esc(capture.template.name)}</h3>${capture.error?`<p class="error-text">${esc(capture.error)} Use Change sample or template to remap.</p>`:''}<p class="muted">Sample ${capture.sample_index+1} · plate ${capture.readings[0]?.plate_number||'—'}. Each well must be assigned once.</p><label>Sample capture time (local)<input id="review-time" type="datetime-local" value="${localTime(capture.captured_at)}" required></label><div class="table-scroll"><table><thead><tr><th>Flask</th><th>Rep.</th><th>Row</th><th>Column</th><th>OD</th></tr></thead><tbody>${capture.readings.map((r,index)=>`<tr data-reading="${index}" class="${r.value===null?'missing':''}"><td>${esc(r.flask)}</td><td>${r.replicate}</td><td><select aria-label="${esc(r.flask)} replicate ${r.replicate} row" data-field="row">${options(capture.plate.rows,r.row)}</select></td><td><select aria-label="${esc(r.flask)} replicate ${r.replicate} column" data-field="column">${options(capture.plate.columns,r.column)}</select></td><td><input class="od" data-field="value" aria-label="${esc(r.flask)} replicate ${r.replicate} OD" type="number" step="any" value="${r.value??''}" placeholder="Unreadable"></td></tr>`).join('')}</tbody></table></div><p class="muted">Yellow rows need attention. Blank replicates stay missing; they are never treated as zero.</p><div class="save-bar"><button id="save-review" ${!capture.readings.length?'disabled':''}>Save reviewed readings</button><span id="review-save-state" class="muted">${capture.status==='reviewed'?'Saved review':'Not reviewed yet'}</span></div></div></div>`;
}
function renderPlots(){
  const points=state.experiment.trajectories;const flasks=[...new Set(points.map(p=>p.flask))];const previous=$('plot-flask').value;
  $('plot-flask').innerHTML=flasks.map(f=>`<option>${esc(f)}</option>`).join('');if(flasks.includes(previous))$('plot-flask').value=previous;drawPlot();
}
function drawPlot(){
  const points=state.experiment.trajectories.filter(p=>p.flask===$('plot-flask').value);const valid=points.filter(p=>p.mean!==null);
  $('plot-table').innerHTML=points.length?`<table><thead><tr><th>Capture time</th><th>Hours</th><th>Mean OD</th><th>SD</th><th>Replicates</th></tr></thead><tbody>${points.map(p=>`<tr><td>${esc(formatTime(p.captured_at))}</td><td>${p.hours.toFixed(3)}</td><td>${p.mean===null?'Incomplete':p.mean.toFixed(4)}</td><td>${p.sd===null?'—':p.sd.toFixed(4)}</td><td>${p.count}/${p.expected}</td></tr>`).join('')}</tbody></table>`:'';
  if(!valid.length){$('plot').innerHTML='<p class="empty">Review a complete set of replicates to see a trajectory here.</p>';return;}
  const xmin=Math.min(0,...points.map(p=>p.hours)),xmax=Math.max(xmin+1,...points.map(p=>p.hours));const ymin=Math.min(0,...valid.map(p=>p.mean)),ymax=Math.max(ymin+.01,...valid.map(p=>p.mean))*1.05;
  const x=v=>64+(v-xmin)/(xmax-xmin)*680,y=v=>320-(v-ymin)/(ymax-ymin)*270;
  let drawing='';for(let i=0;i<=4;i++){let xv=xmin+(xmax-xmin)*i/4,yv=ymin+(ymax-ymin)*i/4;drawing+=`<line class="grid" x1="64" y1="${y(yv)}" x2="744" y2="${y(yv)}"/><text x="54" y="${y(yv)+4}" text-anchor="end">${yv.toFixed(3)}</text><text x="${x(xv)}" y="345" text-anchor="middle">${xv.toFixed(1)}</text>`;}
  let segment=[];for(const point of [...points,{mean:null}]){if(point.mean===null){if(segment.length)drawing+=`<polyline class="line" points="${segment.join(' ')}"/>`;segment=[];}else segment.push(`${x(point.hours)},${y(point.mean)}`);}
  drawing+=valid.map(p=>`<circle cx="${x(p.hours)}" cy="${y(p.mean)}" r="4"><title>${esc(formatTime(p.captured_at))}: ${p.mean.toFixed(4)} OD</title></circle>`).join('');
  $('plot').innerHTML=`<svg viewBox="0 0 800 390" role="img" aria-label="${esc($('plot-flask').value)} mean OD versus elapsed hours"><text x="64" y="26">Mean OD</text>${drawing}<text x="400" y="380" text-anchor="middle">Hours from experiment start</text></svg>`;
}
async function busy(button,callback){if(button.disabled)return;button.disabled=true;try{await callback();}catch(error){fail(error);}finally{if(button.isConnected)button.disabled=false;}}
$('experiment-select').onchange=()=>selectExperiment($('experiment-select').value).catch(fail);
$('new-experiment').onclick=()=>editExperiment(false);$('edit-experiment').onclick=()=>editExperiment(true);$('cancel-experiment').onclick=()=>{$('experiment-editor').hidden=true;};
$('experiment-form').onsubmit=event=>{event.preventDefault();busy(event.submitter,async()=>{const data=Object.fromEntries(new FormData(event.target));data.started_at=new Date(data.started_at).toISOString();const exp=await api('/experiments'+(state.editingExperiment?'/'+state.experiment.id:''),{method:state.editingExperiment?'PUT':'POST',body:data});$('experiment-editor').hidden=true;await loadExperiments();await selectExperiment(exp.id);if(!exp.templates?.length){switchView('setup');if(!state.experiment.templates.length)editTemplate();}notice('Experiment saved.');});};
for(const button of document.querySelectorAll('[data-view]'))button.onclick=()=>switchView(button.dataset.view);
$('capture-template').onchange=()=>{localStorage.setItem('plate-template-'+state.experiment.id,$('capture-template').value);$('template-summary').textContent=templateSummary(state.experiment.templates.find(t=>t.id===$('capture-template').value));};
for(const id of ['camera','upload'])$(id).onchange=event=>{saveFiles([...event.target.files]).catch(fail);event.target.value='';};
$('upload-queue').onclick=()=>uploadQueue().catch(fail);
$('local-queue').onclick=event=>{const button=event.target.closest('[data-remove-local]');if(button&&!state.uploading&&confirm('Remove this photo from the device queue? It has not been uploaded.'))storage('photos','delete',button.dataset.removeLocal).then(renderQueue).catch(fail);};
$('process-all').onclick=()=>busy($('process-all'),async()=>{for(const c of state.experiment.captures.filter(c=>['uploaded','failed'].includes(c.status)))await api(`/photos/${c.id}/process`,{method:'POST'});await refresh();notice('Photos queued for extraction. You can keep capturing.');});
$('capture-list').onclick=event=>{const button=event.target.closest('button');if(button?.dataset.process)busy(button,async()=>{await api(`/photos/${button.dataset.process}/process`,{method:'POST'});await refresh();});if(button?.dataset.review){switchView('review');$('review-photo').value=button.dataset.review;renderReview();}};
$('new-template').onclick=()=>editTemplate();$('cancel-template').onclick=()=>{$('template-editor').hidden=true;};
$('templates').onclick=event=>{const button=event.target.closest('[data-copy-template]');if(button)editTemplate(state.experiment.templates.find(t=>t.id===button.dataset.copyTemplate));};
$('template-form').onsubmit=event=>{event.preventDefault();busy(event.submitter,async()=>{await api(`/experiments/${state.experiment.id}/templates`,{method:'POST',body:templateData()});await selectExperiment(state.experiment.id);$('template-editor').hidden=true;switchView('capture');notice('Template saved. Ready to capture.');});};
$('preview-template').onclick=()=>busy($('preview-template'),async()=>{
  const t=templateData();const rows=$('preview-rows').value.split(',').map(v=>v.trim()).filter(Boolean),cols=$('preview-cols').value.split(',').map(v=>v.trim()).filter(Boolean);
  if(!rows.length||!cols.length||!Number.isInteger(t.replicates)||t.replicates<1||!Number.isInteger(t.flask_count)||t.flask_count<1)throw new Error('Enter grid labels and positive whole-number counts.');
  if(t.row_direction==='bottom')rows.reverse();if(t.column_direction==='right')cols.reverse();const slots=[];
  for(let c=0;c+t.replicates<=cols.length;c+=t.replicates)for(const row of rows)slots.push(cols.slice(c,c+t.replicates).map(col=>`${row}/${col}`).join(', '));
  const usable=slots.slice(t.start_slot);if(usable.length<t.flask_count)throw new Error('This example grid cannot fit the selected flasks and replicates.');
  $('template-preview').innerHTML=`<p>${Math.floor(usable.length/t.flask_count)} full samples fit on this plate.</p><table><thead><tr><th>Sample</th><th>Flask</th><th>Wells (row/column)</th></tr></thead><tbody>${usable.slice(0,Math.min(usable.length,2*t.flask_count)).map((s,i)=>`<tr><td>${Math.floor(i/t.flask_count)+1}</td><td>FL-${String(t.flask_start+i%t.flask_count).padStart(2,'0')}</td><td>${esc(s)}</td></tr>`).join('')}</tbody></table>`;
});
$('review-photo').onchange=()=>{if(state.reviewDirty&&!confirm('Discard unsaved edits and open another photo?')){$('review-photo').value=state.reviewing.id;return;}state.reviewDirty=false;renderReview();};
$('review-content').oninput=event=>{if(event.target.closest('[data-reading]')||['review-time','remap-sample','remap-template'].includes(event.target.id)){state.reviewDirty=true;$('review-save-state').textContent='Unsaved changes';}};
$('review-content').onchange=event=>{if(['row','column'].includes(event.target.dataset.field)){const row=event.target.closest('[data-reading]');const original=state.reviewing.plate.wells.find(w=>w.row===row.querySelector('[data-field=row]').value&&w.column===row.querySelector('[data-field=column]').value);row.querySelector('[data-field=value]').value=original?.value??'';row.classList.toggle('missing',original?.value==null);}};
$('review-content').onclick=event=>{
  const button=event.target.closest('button');if(!button)return;
  if(button.id==='save-review')busy(button,async()=>{const capture=state.reviewing;const readings=[...document.querySelectorAll('[data-reading]')].map(row=>({...capture.readings[Number(row.dataset.reading)],row:row.querySelector('[data-field=row]').value,column:row.querySelector('[data-field=column]').value,value:row.querySelector('[data-field=value]').value===''?null:Number(row.querySelector('[data-field=value]').value)}));await api(`/photos/${capture.id}/review`,{method:'PUT',body:{revision:capture.revision,captured_at:$('review-time').value===localTime(capture.captured_at)?capture.captured_at:new Date($('review-time').value).toISOString(),sample_index:capture.sample_index,readings}});state.reviewDirty=false;await refresh();notice(state.experiment.sheet_id?'Review saved. Trajectories updated and Google Sheet queued for sync.':'Review saved. Trajectories updated. Link a Google Sheet in Setup to sync.');});
  if(button.id==='remap')busy(button,async()=>{if(!confirm('Replace the current mapping with values from the original extraction?'))return;const c=state.reviewing;await api(`/photos/${c.id}/remap`,{method:'POST',body:{revision:c.revision,template_id:$('remap-template').value,sample_index:Number($('remap-sample').value)-1}});state.reviewDirty=false;await refresh();notice('Mapping updated. Review it before saving.');});
};
$('plot-flask').onchange=drawPlot;$('download').onclick=()=>{window.location.href=`/api/experiments/${state.experiment.id}/export`;};
$('sync-sheet').onclick=()=>busy($('sync-sheet'),async()=>{await api(`/experiments/${state.experiment.id}/sync`,{method:'POST'});await refresh();});
window.addEventListener('online',()=>{connection();uploadQueue().catch(fail);});window.addEventListener('offline',()=>{connection();renderQueue().catch(fail);});
window.addEventListener('beforeunload',event=>{if(state.reviewDirty){event.preventDefault();event.returnValue='';}});
async function start(){
  connection();if('serviceWorker'in navigator)navigator.serviceWorker.register('/sw.js').catch(()=>notice('Offline page loading is unavailable in this browser. Keep the app open while capturing.',true));
  await openDB;
  try{const cached=await storage('settings','get','experiments');await loadExperiments();const id=cached?.selected||state.experiments[0]?.id;if(id)await selectExperiment(id);else renderExperiment();await uploadQueue();}
  catch(error){const saved=await storage('settings','get','experiments');if(!error.status&&saved){state.experiments=saved.experiments||[];renderExperimentPicker();if(saved.selected)await selectExperiment(saved.selected);notice('Offline. Photos save on this device and upload when this app reconnects.');}else{renderExperiment();fail(error);}}
}
start().catch(fail);
setInterval(()=>{if(state.experiment&&navigator.onLine&&document.visibilityState==='visible')refresh().catch(()=>{});},5000);
