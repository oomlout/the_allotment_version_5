/* Post creation and tag controls. No publishing or browser-worker actions. */
(() => {
  const search=document.getElementById('tag-search');
  const filter=()=>document.querySelectorAll('.tag-choice').forEach(label=>{
    label.hidden=!`${label.dataset.tagName} ${label.dataset.tagGroup}`.toLocaleLowerCase().includes(search.value.toLocaleLowerCase());
  });
  search?.addEventListener('input',filter);
  document.getElementById('create-tag')?.addEventListener('click',async event=>{
    const button=event.currentTarget,message=document.getElementById('tag-message');
    const data=new FormData();data.set('csrf',document.querySelector('[name="csrf"]').value);
    data.set('name',document.getElementById('new-tag-name').value);data.set('group',document.getElementById('new-tag-group').value);
    button.disabled=true;
    try {
      const response=await fetch(document.querySelector('.tag-picker').dataset.tagSave,{method:'POST',body:data,headers:{Accept:'application/json'}});
      if(!response.ok)throw new Error('Could not add tag. Check the name and try again.');
      const tag=await response.json();
      let checkbox=Array.from(document.querySelectorAll('[name="tags"]')).find(c=>c.value===tag.id);
      if(!checkbox){
        const label=document.createElement('label');label.className='tag-choice';label.dataset.tagName=tag.name;label.dataset.tagGroup=tag.group;
        checkbox=document.createElement('input');checkbox.type='checkbox';checkbox.name='tags';checkbox.value=tag.id;
        const name=document.createElement('span');name.textContent=tag.name;const group=document.createElement('small');group.textContent=tag.group;
        label.append(checkbox,name,group);document.querySelector('.tag-options').append(label);
      }
      checkbox.checked=true;search.value='';filter();document.getElementById('new-tag-name').value='';message.textContent='Tag added.';
    }catch(error){message.textContent=error.message;}finally{button.disabled=false;}
  });
  document.getElementById('catalogue-search')?.addEventListener('input',event=>{
    document.querySelectorAll('.tag-row').forEach(row=>row.hidden=!row.dataset.search.toLocaleLowerCase().includes(event.target.value.toLocaleLowerCase()));
    document.querySelectorAll('.tag-group').forEach(group=>group.hidden=!Array.from(group.querySelectorAll('.tag-row')).some(row=>!row.hidden));
  });
  document.querySelectorAll('[data-variant-select]').forEach(select=>{
    const update=()=>{const image=document.querySelector(`[data-variant-preview="${select.dataset.variantSelect}"]`);if(image)image.src=select.selectedOptions[0].dataset.src;};
    select.addEventListener('change',update);update();
  });
  let urls=[];
  document.querySelector('input[name="media"]')?.addEventListener('change',event=>{
    const holder=document.getElementById('upload-previews');if(!holder)return;
    urls.forEach(url=>URL.revokeObjectURL(url));urls=[];holder.replaceChildren();
    for(const file of event.target.files){
      if(!file.type.startsWith('image/'))continue;
      const image=document.createElement('img');image.alt=file.name;image.src=URL.createObjectURL(file);urls.push(image.src);holder.append(image);
    }
  });

  const form=document.getElementById('post-form');if(!form)return;
  const frame=document.getElementById('image-editor-frame'),editor=document.getElementById('inline-image-editor'),message=document.getElementById('creation-status');
  let busy=false,submitting=false;
  frame.addEventListener('load',()=>{
    if(frame.getAttribute('src')==='about:blank')return;
    if(!frame.contentDocument?.getElementById('image-editor')){
      message.textContent=frame.contentDocument?.querySelector('.panel p')?.textContent||'Image editor unavailable. The original attachment is kept.';
      editor.hidden=true;
    }
  });
  const saveCrops=async()=>{
    if(editor.hidden)return true;
    let api;
    const deadline=Date.now()+10000;
    do {
      if(editor.hidden)return true;
      api=frame.contentWindow?.allotmentImageEditor;
      if(api?.isReady())break;
      await new Promise(resolve=>setTimeout(resolve,50));
    }while(Date.now()<deadline);
    if(!api?.isReady()){message.textContent='Image editor is still loading. Try saving again.';return false;}
    const ok=await api.savePending();if(!ok)message.textContent='Save the image crops before continuing.';return ok;
  };
  const saveInline=async intent=>{
    if(busy)return;busy=true;message.textContent='Saving draft…';
    try{
      if(!await saveCrops())return;
      const data=new FormData(form);data.set('intent',intent);
      form.querySelectorAll('button,input[type=file]').forEach(el=>el.disabled=true);
      const response=await fetch(form.action,{method:'POST',body:data,headers:{Accept:'application/json'}});
      if(!response.ok){const doc=new DOMParser().parseFromString(await response.text(),'text/html');throw new Error(doc.querySelector('.panel p')?.textContent||'Could not save this draft.');}
      const result=await response.json();form.querySelector('[name=post_id]').value=result.post_id;
      document.getElementById('post-media').innerHTML=result.media_html;
      form.querySelector('[name=media]').value='';document.getElementById('upload-previews').replaceChildren();document.getElementById('file-summary').textContent='';
      history.replaceState(null,'',result.compose_url);
      if(result.editor_url){editor.hidden=false;frame.src=result.editor_url;}
      message.textContent='Draft saved.';
    }catch(error){message.textContent=error.message;}finally{busy=false;form.querySelectorAll('button,input[type=file]').forEach(el=>el.disabled=false);}
  };
  form.querySelector('[name=media]').addEventListener('change',()=>saveInline('images'));
  form.addEventListener('submit',async event=>{
    if(submitting)return;
    event.preventDefault();if(busy)return;
    const intent=event.submitter?.value||'save';
    if(intent==='images'||intent.startsWith('image:')){await saveInline(intent);return;}
    busy=true;
    try{if(await saveCrops()){submitting=true;const button=event.submitter;setTimeout(()=>form.requestSubmit(button||undefined),0);}}
    finally{busy=false;}
  });
  document.getElementById('close-image-editor').addEventListener('click',async()=>{
    if(busy)return;busy=true;
    try{if(await saveCrops()){editor.hidden=true;frame.src='about:blank';}}finally{busy=false;}
  });
  window.addEventListener('message',async event=>{
    if(event.origin!==location.origin||event.source!==frame.contentWindow)return;
    if(event.data?.type==='allotment-editor-height')frame.style.height=Math.min(5000,Math.max(500,Number(event.data.height)||500))+'px';
    if(event.data?.type==='allotment-crops-saved'&&!busy){
      const removed=new Set(Array.from(form.querySelectorAll('[name=keep_media]:not(:checked)')).map(el=>el.value));
      const id=form.querySelector('[name=post_id]').value,url=new URL(location.href);url.pathname=url.pathname.replace(/\/compose\/.*$/,'/compose/'+encodeURIComponent(id)+'/media');url.search='';
      const response=await fetch(url);if(!response.ok)return;
      document.getElementById('post-media').innerHTML=await response.text();
      form.querySelectorAll('[name=keep_media]').forEach(el=>{if(removed.has(el.value))el.checked=false;});
    }
  });
  const links=document.getElementById('post-links');
  const relabel=()=>links.querySelectorAll('.link-input-row label').forEach((label,index)=>label.firstChild.textContent=`Link ${index+1}`);
  document.getElementById('add-link').addEventListener('click',()=>{
    if(links.children.length>=12){message.textContent='Use up to 12 links including the repository.';return;}
    const row=document.createElement('div');row.className='link-input-row';
    const label=document.createElement('label');label.append(document.createTextNode('Link'));
    const input=document.createElement('input');input.type='url';input.name='links';input.placeholder='https://';label.append(input);
    const remove=document.createElement('button');remove.type='button';remove.className='button secondary';remove.dataset.removeLink='';remove.textContent='Remove';row.append(label,remove);links.append(row);relabel();input.focus();
  });
  links.addEventListener('click',event=>{if(event.target.closest('[data-remove-link]')){event.target.closest('.link-input-row').remove();relabel();}});
})();
