// login.html: page script (a file, so the security policy can forbid inline scripts).
// On success the two-logo overlay plays (the same transition as every other page) and the dashboard opens.
const form = document.getElementById('loginForm');
const errorMsg = document.getElementById('errorMsg');
const submitBtn = document.getElementById('submitBtn');
const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const overlay = document.getElementById('goOverlay');
const go = (href, label) => {
  document.getElementById('goLabel').textContent = label;
  overlay.classList.remove('off');
  try { sessionStorage.setItem('cesc-splash', '1'); } catch (_e) { /* storage blocked: fine */ }
  setTimeout(() => { location.href = href; }, reduced ? 80 : 750);
};

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  errorMsg.hidden = true;
  submitBtn.disabled = true;
  submitBtn.querySelector('span').textContent = 'Signing in…';
  try {
    const resp = await fetch('/api/login', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: document.getElementById('username').value, password: document.getElementById('password').value }),
    });
    const data = await resp.json();
    if (resp.ok && data.ok){ go('/dashboard', 'Opening the dashboard'); return; }
    errorMsg.textContent = data.error || 'Invalid username or password.';
    errorMsg.hidden = false;
  } catch (err) {
    errorMsg.textContent = 'Could not reach the server: ' + err.message;
    errorMsg.hidden = false;
  }
  submitBtn.disabled = false;
  submitBtn.querySelector('span').textContent = 'Sign in';
});

document.querySelectorAll('.ai-link').forEach(a => a.addEventListener('click', e => {
  e.preventDefault(); go(a.getAttribute('href'), 'Entering AI mode');
}));
