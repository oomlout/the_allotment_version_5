/* Cropper.js 1.x adapter. The rest of Studio uses only saved variant manifests.
 * Upgrade instructions: guides/IMAGE_EDITOR_GUIDE.md. Never edit vendor files.
 */
(() => {
  const config=JSON.parse(document.getElementById('image-config').textContent);
  const source=document.getElementById('crop-source'),status=document.getElementById('image-status');
  const preview=document.getElementById('edited-preview'),keys=Object.keys(config.ratios);
  const state={},neutral=()=>({brightness:1,contrast:1,saturation:1});
  let current='square',ready=false,busy=false,restoring=false,frame,resizeTimer,resizeCrop;
  const dirty=new Set(),markDirty=()=>{if(ready&&!busy&&!restoring)dirty.add(current);};
  const centred=key=>{
    const [a,b]=config.ratios[key],width=Math.min(config.width,config.height*a/b),height=width*b/a;
    return {x:(config.width-width)/2,y:(config.height-height)/2,width,height,rotate:0,scaleX:1,scaleY:1};
  };
  for(const key of keys){
    const saved=config.variants[key];
    state[key]=saved?.source_sha256===config.sourceHash && saved.recipe?.engine==='cropperjs-1'
      ? structuredClone(saved.recipe) : {engine:'cropperjs-1',crop:centred(key),adjustments:neutral()};
  }
  const adjustments=()=>Object.fromEntries(['brightness','contrast','saturation'].map(key=>[key,Number(document.getElementById(`adjust-${key}`).value)/100]));
  const changedLook=()=>{
    if(document.getElementById('all-adjustments').checked){for(const key of keys){state[key].adjustments=adjustments();dirty.add(key);}}
    else markDirty();
    render();
  };
  const colour=values=>`brightness(${values.brightness}) contrast(${values.contrast}) saturate(${values.saturation})`;
  const restoreCrop=data=>{
    // Cropper clamps dimensions; set position separately so floating-point
    // rounding at the maximum size cannot reset a dragged crop to its old top.
    const {x,y,...shape}=data;cropper.setData(shape);cropper.setData({x,y});
  };
  const finishResize=()=>{
    clearTimeout(resizeTimer);
    if(!resizeCrop)return;
    restoring=true;restoreCrop(resizeCrop);resizeCrop=null;restoring=false;
  };
  const capture=()=>{finishResize();if(ready)state[current]={engine:'cropperjs-1',crop:cropper.getData(),adjustments:adjustments()};};
  const output=(limit=config.maxExport)=>{
    const data=cropper.getData(),scale=Math.min(1,limit/Math.max(data.width,data.height));
    const raw=cropper.getCroppedCanvas({width:Math.max(1,Math.round(data.width*scale)),height:Math.max(1,Math.round(data.height*scale)),maxWidth:limit,maxHeight:limit,fillColor:'#fff',imageSmoothingQuality:'high'});
    if(!raw || !raw.width || !raw.height)throw new Error('Choose a non-empty crop.');
    const canvas=document.createElement('canvas');canvas.width=raw.width;canvas.height=raw.height;
    const context=canvas.getContext('2d');context.filter=colour(adjustments());context.drawImage(raw,0,0);return canvas;
  };
  const render=()=>{
    if(!ready || busy)return;
    cancelAnimationFrame(frame);frame=requestAnimationFrame(()=>{
      try{
        const canvas=output(360);preview.width=canvas.width;preview.height=canvas.height;preview.getContext('2d').drawImage(canvas,0,0);
        const data=cropper.getData();for(const key of ['x','y','width','height'])if(document.activeElement!==document.getElementById(`crop-${key}`))document.getElementById(`crop-${key}`).value=Math.round(data[key]);
        const scale=Math.min(1,config.maxExport/Math.max(data.width,data.height));document.getElementById('crop-size').textContent=`${Math.round(data.width*scale)} × ${Math.round(data.height*scale)} px`;
        for(const [key,value] of Object.entries(adjustments()))document.getElementById(`value-${key}`).textContent=`${Math.round(value*100)}%`;
      }catch(error){status.textContent=error.message;}
    });
  };
  const activate=key=>{
    restoring=true;
    current=key;const [a,b]=config.ratios[key];cropper.reset();cropper.setAspectRatio(a/b);
    restoreCrop(state[key].crop);
    for(const [name,value] of Object.entries(state[key].adjustments))document.getElementById(`adjust-${name}`).value=Math.round(value*100);
    document.querySelectorAll('[data-ratio]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.ratio===key)));restoring=false;render();
  };
  if(typeof Cropper==='undefined'){status.textContent='Image editor could not load. Reload the page.';return;}
  const cropper=new Cropper(source,{viewMode:1,aspectRatio:1,autoCropArea:1,dragMode:'move',cropBoxMovable:true,cropBoxResizable:true,toggleDragModeOnDblclick:false,checkOrientation:false,
    ready(){ready=true;activate(current);status.textContent='Original kept. Choose a crop to edit.';document.getElementById('image-editor').dataset.ready='true';},
    crop(){render();},cropstart(){finishResize();},cropend(){markDirty();},zoom(){markDirty();}});
  window.addEventListener('resize',()=>{
    if(!ready||busy)return;
    resizeCrop ||= cropper.getData();restoring=true;clearTimeout(resizeTimer);
    resizeTimer=setTimeout(()=>{finishResize();render();},300);
  },{capture:true});
  document.querySelectorAll('[data-ratio]').forEach(button=>button.addEventListener('click',()=>{if(!ready||busy)return;capture();activate(button.dataset.ratio);}));
  document.querySelectorAll('[data-tool]').forEach(button=>button.addEventListener('click',()=>{
    if(!ready||busy)return;finishResize();markDirty();
    switch(button.dataset.tool){case 'rotate-left':cropper.rotate(-90);break;case 'rotate-right':cropper.rotate(90);break;case 'flip':cropper.scaleX(-cropper.getData().scaleX);break;case 'zoom-in':cropper.zoom(.1);break;case 'zoom-out':cropper.zoom(-.1);break;case 'reset':state[current].crop=centred(current);activate(current);break;}
    render();
  }));
  document.getElementById('apply-crop').addEventListener('click',()=>{
    if(!ready||busy)return;
    finishResize();
    const values=Object.fromEntries(['x','y','width','height'].map(key=>[key,Number(document.getElementById(`crop-${key}`).value)]));
    if(Object.values(values).some(value=>!Number.isFinite(value))||values.width<=0||values.height<=0){status.textContent='Enter valid crop dimensions.';return;}
    cropper.setData(values);markDirty();render();
  });
  for(const key of ['brightness','contrast','saturation'])document.getElementById(`adjust-${key}`).addEventListener('input',changedLook);
  document.querySelectorAll('[data-look]').forEach(button=>button.addEventListener('click',()=>{
    if(!ready||busy)return;
    const look={original:neutral(),pop:{brightness:1.05,contrast:1.12,saturation:1.15},soft:{brightness:1.08,contrast:.92,saturation:.9},mono:{brightness:1,contrast:1.1,saturation:0}}[button.dataset.look];
    for(const [key,value] of Object.entries(look))document.getElementById(`adjust-${key}`).value=Math.round(value*100);changedLook();
  }));
  const save=async all=>{
    if(!ready||busy)return false;capture();const active=current;busy=true;status.textContent='Saving crops…';
    document.querySelectorAll('.image-editor button,.image-editor input').forEach(el=>el.disabled=true);
    try{
      const data=new FormData(),recipes={};data.set('csrf',document.getElementById('image-csrf').value);data.set('source_hash',config.sourceHash);data.set('revision',config.revision);
      for(const key of (all?keys:[active])){
        activate(key);const canvas=output();capture();recipes[key]=state[key];
        const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/jpeg',.94));if(!blob)throw new Error('Could not export this crop.');
        data.set(key,blob,`${key}.jpg`);
      }
      data.set('recipes',JSON.stringify(recipes));
      const response=await fetch(config.saveUrl,{method:'POST',body:data,headers:{Accept:'application/json'}});
      if(!response.ok){const text=await response.text();const doc=new DOMParser().parseFromString(text,'text/html');throw new Error(doc.querySelector('.error-message')?.textContent||doc.querySelector('.panel p')?.textContent||'Save failed. Reload if another editor changed this image.');}
      const result=await response.json();config.revision=result.revision;config.variants=result.variants;
      const holder=document.getElementById('saved-variants');holder.replaceChildren();
      for(const [key,v] of Object.entries(result.variants)){
        const link=document.createElement('a');link.dataset.saved=key;link.target='_blank';link.rel='noopener';
        link.href=config.mediaBase+v.file.split('/').map(encodeURIComponent).join('/');
        const image=document.createElement('img');image.src=link.href;image.alt=`${config.labels[key]} crop`;
        const label=document.createElement('strong');label.textContent=config.labels[key];const size=document.createElement('span');size.textContent=`${v.width} × ${v.height}${v.needs_review?' · review crop':''}`;
        link.append(image,label,size);holder.append(link);
      }
      (all?keys:[active]).forEach(key=>dirty.delete(key));
      status.textContent=all?'Four crop versions saved.':'Crop saved.';
      if(window.parent!==window)window.parent.postMessage({type:'allotment-crops-saved'},location.origin);
      return true;
    }catch(error){status.textContent=error.message;return false;}finally{activate(active);busy=false;document.querySelectorAll('.image-editor button,.image-editor input').forEach(el=>el.disabled=false);render();}
  };
  window.allotmentImageEditor={isReady:()=>ready&&!busy,savePending:()=>dirty.size||Object.keys(config.variants).length<keys.length?save(true):Promise.resolve(true)};
  document.getElementById('saved-variants').addEventListener('click',event=>{
    const link=event.target.closest('[data-saved]');if(!link||!ready||busy)return;
    event.preventDefault();capture();activate(link.dataset.saved);document.getElementById('image-editor').scrollIntoView({block:'start'});
  });
  if(window.parent!==window)new ResizeObserver(()=>window.parent.postMessage({type:'allotment-editor-height',height:document.getElementById('embedded-content').getBoundingClientRect().height+10},location.origin)).observe(document.getElementById('embedded-content'));
  document.getElementById('save-crop').addEventListener('click',()=>save(false));document.getElementById('save-all-crops').addEventListener('click',()=>save(true));
  window.addEventListener('beforeunload',event=>{if(dirty.size){event.preventDefault();event.returnValue='';}});
})();
