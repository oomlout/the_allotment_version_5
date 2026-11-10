document.querySelectorAll('[data-counter]').forEach(field => {
  const counter = document.getElementById(field.dataset.counter);
  const update = () => { counter.textContent = `${Array.from(field.value).length} characters`; };
  field.addEventListener('input', update);
  update();
});
document.querySelectorAll('[data-files]').forEach(field => {
  const summary = document.getElementById(field.dataset.files);
  field.addEventListener('change', () => {
    summary.textContent = Array.from(field.files).map(file => `${file.name} (${(file.size / 1024 / 1024).toFixed(1)} MB)`).join(' · ');
  });
});
document.querySelectorAll('form').forEach(form => {
  if (form.hasAttribute('data-stop-test') || form.classList.contains('test-start')) return;
  form.addEventListener('submit', event => {
    // Preserve the clicked button's name/value while preventing accidental double submits.
    window.setTimeout(() => { if(!event.defaultPrevented)form.querySelectorAll('button').forEach(button => { button.disabled = true; }); }, 0);
  });
});

