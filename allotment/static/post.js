(() => {
  const root=document.getElementById('sow-panel');if(!root)return;
  const form=root.querySelector('[data-sow-launch]'), launch=root.querySelector('[data-launch]');
  const rows=[...root.querySelectorAll('[data-destination]')], controls=root.querySelector('[data-job-control]');
  const progressPanel=document.getElementById('job-run'),inspectPanel=document.getElementById('inspect-panel');
  let displayedRun='';
  const active=['queued','countdown','paused','running'];
  let run=null,destinations=[],timer,flight=null,revision=0,starting=false,controlPending=false;
  const pending=new Set(),errors=new Map();
  const harvestButtons=[...document.querySelectorAll('.harvest-form button')];
  harvestButtons.forEach(button=>{button.dataset.caption=button.textContent;button.dataset.initialDisabled=String(button.disabled);});
  const field=(node,name)=>node.querySelector(`[data-${name}]`);
  // A running Studio can serve a cached template alongside newer static files.
  // Restore additive controls before rendering an older page.
  let setupLink=field(root,'platform-setup');
  if(!setupLink){
    setupLink=document.createElement('a');setupLink.dataset.platformSetup='';
    setupLink.className='button secondary small';setupLink.hidden=true;form.append(setupLink);
  }
  for(const row of rows){
    if(field(row,'testing-required'))continue;
    const notice=document.createElement('p');notice.dataset.testingRequired='';
    notice.className='action-error';notice.hidden=true;
    notice.append('Platform needs testing. ');
    const link=document.createElement('a');link.textContent='Open test';
    const base=root.dataset.statusUrl.split('/p/')[0];
    link.href=`${base}/destinations/#destination-${encodeURIComponent(row.dataset.destination)}`;
    notice.append(link);row.append(notice);
  }
  const text=(node,value)=>{if(node.textContent!==value)node.textContent=value;};
  const message=(node,value)=>{text(node,value||'');node.hidden=!value;if(node.classList.contains('action-error'))node.title=value||'';};
  const busy=()=>starting||active.includes(run?.status);
  function selection(){
    const count=form.querySelectorAll('[name="actions"]:checked:not(:disabled)').length;
    launch.disabled=busy()||pending.size>0||root.dataset.published!=='true';
    text(launch,starting?'Starting…':'Launch');
    text(field(root,'selection-status'),busy()?'Launch in progress':count?`${count} selected`:'Website and summary only');
  }
  function render(){
    const working=busy();
    harvestButtons.forEach(button=>button.disabled=working||button.dataset.initialDisabled==='true');
    for(const row of rows){
      const data=destinations.find(d=>d.id===row.dataset.destination);if(!data)continue;
      row.dataset.reviewUrl=data.review_url;
      row.dataset.verdict=data.verdict;
      const current=working&&run?.platform===data.id;
      row.classList.toggle('sow-current',!!current);
      const checkbox=row.querySelector('[name="actions"]');
      const needsTest=data.needs_test??row.dataset.configured!=='true';
      checkbox.disabled=needsTest;
      if(needsTest)checkbox.checked=false;
      const testing=field(row,'testing-required');testing.hidden=!needsTest;
      if(data.test_url)testing.querySelector('a').href=data.test_url;
      let label=current?(run.operation||'Working'):data.label;
      if(pending.has(data.id))label='Saving review…';
      text(field(row,'destination-status'),label);
      row.querySelector('.check-label').title=row.dataset.configured==='true'?label:'Test required';
      const error=field(row,'destination-error');text(error,errors.get(data.id)||data.error||'');error.title=error.textContent;
      error.hidden=needsTest;
      const link=field(row,'destination-link');link.hidden=!data.url;if(data.url)link.href=data.url;
      field(row,'review').hidden=!data.reviewable;
      row.querySelectorAll('[data-verdict]').forEach(button=>{
        button.hidden=button.dataset.verdict==='unsuccessful'&&data.verdict==='successful';
        button.disabled=working||pending.has(data.id);
        button.setAttribute('aria-pressed',String(button.dataset.verdict===data.verdict));
        button.title=working?'Review after the launch finishes':button.dataset.verdict==='successful'?(data.verdict==='successful'?'Posted — confirmed by you':'Mark as posted'):'Reset for another attempt';
      });
    }
    const panel=progressPanel;panel.hidden=!run&&!starting;
    const progressHost=run?.kind==='harvest'?inspectPanel?.querySelector('[data-inspect-progress]'):field(root,'sow-progress');
    if(progressHost&&panel.parentElement!==progressHost)progressHost.append(panel);
    if(run&&run.id!==displayedRun){
      displayedRun=run.id;
      if(run.kind==='harvest'&&inspectPanel)inspectPanel.open=true;
      else if(working&&root.matches('details'))root.open=true;
    }
    let label=starting?'Starting launch…':({queued:'Starting',countdown:'Countdown',paused:'Paused',running:run?.operation||'Working',complete:'Complete',failed:'Finished with errors',stopped:'Stopped'}[run?.status]||'');
    if(controlPending)label='Updating controls…';
    text(document.getElementById('job-run-state'),label);
    const selected=destinations.filter(d=>run?.destinations?.includes(d.id));
    const done=selected.filter(d=>!['pending','running','submitting',''].includes(d.status)).length;
    const name=rows.find(r=>r.dataset.destination===run?.platform)?.dataset.name;
    const step=run?.total_steps&&working?`Step ${run.step} of ${run.total_steps}`:'';
    message(field(panel,'run-detail'),run?.kind==='harvest'?`Bluesky · ${run.mode==='ids'?'Capture post ID':'Inspect reactions'} · Read-only`:run?[`${done} / ${selected.length} destinations finished`,working?name:'',step].filter(Boolean).join(' · '):'');
    const bar=field(panel,'run-progress');bar.hidden=!selected.length||run?.kind==='harvest';bar.max=selected.length||1;bar.value=done;
    message(document.getElementById('job-run-error'),run?.error);
    field(panel,'handoff').hidden=!working;
    text(field(panel,'handoff'),run?.phase==='handoff'?'Click the Chrome window you want to use. Automation starts when this countdown ends.':'Keep Chrome visible. Release mouse and keyboard after the countdown. F8 stops.');
    if(run?.control_url)controls.action=run.control_url;
    controls.querySelectorAll('button').forEach(button=>{
      button.hidden=button.value==='pause'?run?.status!=='countdown':button.value==='resume'?run?.status!=='paused':!working;
      button.disabled=controlPending||starting;
    });
    selection();countdown();
  }
  function countdown(){
    const node=document.getElementById('job-run-countdown');
    const seconds=run?.countdown_until?Math.max(0,Math.ceil((Date.parse(run.countdown_until)-Date.now())/1000)):run?.remaining;
    text(node,run?.phase==='handoff'||run?.status==='countdown'?` ${seconds||0}s`:run?.status==='paused'?` ${run.remaining||0}s remaining`:'');
    if(run?.phase==='handoff')text(launch,`Choose Chrome · ${seconds||0}s`);
    harvestButtons.forEach(button=>text(button,run?.kind==='harvest'&&run?.phase==='handoff'&&button.form.elements.mode.value===run.mode?`Choose Chrome · ${seconds||0}s`:button.dataset.caption));
  }
  async function request(url,options={}){
    const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),15000);
    try{
      const response=await fetch(url,{cache:'no-store',...options,headers:{Accept:'application/json'},signal:controller.signal});
      const data=await response.json();if(!response.ok)throw Object.assign(new Error(data.error||'Request failed.'),{platform:data.platform,setupUrl:data.setup_url});return data;
    }catch(error){if(error.name==='AbortError')throw new Error('Response timed out. Check status before retrying.');throw error;}
    finally{clearTimeout(timeout);}
  }
  async function refreshHistory(id){
    try{
      const response=await fetch(location.href,{cache:'no-store'});if(!response.ok)return;
      const html=new DOMParser().parseFromString(await response.text(),'text/html');
      const old=document.querySelector('[data-launch-history]'),fresh=html.querySelector('[data-launch-history]');
      if(old&&fresh&&run?.id===id){fresh.open=old.open;old.replaceWith(fresh);}
    }catch(_){} // The durable history is also rendered on the next page load.
  }
  function poll(){
    clearTimeout(timer);if(flight)return flight;
    const version=revision;
    flight=(async()=>{
      try{
        const data=await request(root.dataset.statusUrl);if(version!==revision)return;
        const finished=active.includes(run?.status)&&data.run&&!active.includes(data.run.status);
        run=data.run;destinations=data.destinations||[];render();
        if(finished){if(run.kind==='harvest'){location.reload();return;}void refreshHistory(run.id);}
      }
      catch(error){message(field(root,'sow-error'),error.message);}
      finally{flight=null;timer=setTimeout(poll,busy()?800:5000);}
    })();return flight;
  }
  form.addEventListener('change',selection);
  form.addEventListener('submit',async event=>{
    event.preventDefault();if(launch.disabled)return;
    const payload=new FormData(form);starting=true;revision++;message(field(root,'sow-error'),'');setupLink.hidden=true;render();
    try{
      const data=await request(form.action,{method:'POST',body:payload});revision++;
      run={id:data.id,status:'queued',kind:'launch',destinations:[...new Set([...payload.getAll('actions'),'static','summary'])]};
      for(const d of destinations)if(run.destinations.includes(d.id))Object.assign(d,{status:'pending',label:'Waiting',verdict:'',reviewable:false,blocked:true,error:''});
    }
    catch(error){
      message(field(root,'sow-error'),error.message);
      if(error.platform&&error.setupUrl){
        errors.set(error.platform,error.message);
        const link=setupLink;link.href=error.setupUrl;link.hidden=false;
        const name=rows.find(row=>row.dataset.destination===error.platform)?.dataset.name||error.platform;
        text(link,`Open ${name} test`);
      }
    }
    finally{starting=false;await poll();render();}
  });
  for(const row of rows)for(const button of row.querySelectorAll('[data-verdict]'))button.addEventListener('click',async()=>{
    const id=row.dataset.destination;if(busy()||pending.has(id))return;
    const url=row.dataset.reviewUrl, verdict=button.dataset.verdict;if(!url)return;
    pending.add(id);errors.delete(id);revision++;render();
    try{
      const data=await request(url,{method:'POST',body:new URLSearchParams({csrf:root.dataset.csrf,verdict})});
      if(data.review?.verdict!==verdict)throw new Error('Review was not confirmed.');
      revision++;
      const destination=destinations.find(d=>d.id===id);if(destination){
        destination.verdict=verdict;destination.label=verdict==='successful'?'Posted — confirmed by you':'Ready to select again';destination.blocked=false;
        destination.error='';
        if(verdict==='unsuccessful'){destination.reviewable=false;row.querySelector('[name="actions"]').checked=false;}
      }
      void refreshHistory(run?.id);
    }catch(error){revision++;errors.set(id,error.message);}
    finally{pending.delete(id);render();void poll();}
  });
  controls.addEventListener('submit',async event=>{
    event.preventDefault();if(controlPending)return;
    const data=new FormData(controls);data.set('action',event.submitter.value);controlPending=true;revision++;render();
    try{await request(controls.action,{method:'POST',body:data});revision++;}
    catch(error){message(field(root,'sow-error'),error.message);}
    finally{controlPending=false;render();void poll();}
  });
  selection();void poll();setInterval(countdown,250);
})();
