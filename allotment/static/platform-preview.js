(() => {
  const panel=document.getElementById('platform-preview-panel');
  if(!panel)return;
  const form=document.querySelector('[data-platform-text-form]');
  const images=panel.querySelector('[data-preview-images]');
  const original=panel.querySelector('[data-preview-original]').content.textContent;
  function render(){
    for(const card of panel.querySelectorAll('[data-platform-preview]')){
      const id=card.dataset.platformPreview;
      const editor=form?.querySelector(`[data-platform-copy="${id}"]`);
      if(!editor)continue;
      const custom=editor.querySelector('[name="platforms"]').checked;
      card.querySelector('[data-preview-text]').textContent=custom?editor.querySelector('textarea').value:original;
      for(const field of ['title','tags']){
        const target=card.querySelector(`[data-preview-${field}]`);
        target.textContent=custom?(editor.querySelector(`[name="${field}_${id}"]`)?.value||''):'';
        target.hidden=!target.textContent;
      }
    }
  }
  function showImages(){
    panel.querySelectorAll('.platform-preview-images').forEach(group=>group.hidden=!images.checked);
  }
  // A browser history restore can restore <details open>; opening is manual only.
  function resetPreview(){
    panel.open=false;
    const textPanel=document.getElementById('platform-text-panel');if(textPanel)textPanel.open=false;
    images.checked=false;showImages();
  }
  images.addEventListener('change',showImages);
  form?.addEventListener('input',render);
  form?.addEventListener('change',render);
  window.addEventListener('pageshow',resetPreview);
  resetPreview();render();
})();
