const $ = (id) => document.getElementById(id);
const aviso = (t, clase) => { $('msg').textContent = t; $('msg').className = clase; };

chrome.storage.local.get(['odooUrl', 'token']).then((s) => {
  $('odooUrl').value = s.odooUrl || 'https://odoo.uniasser.net';
  $('token').value = s.token || '';
});

/* El permiso para hablar con el Odoo del usuario se pide al guardar, no en el
 * manifiesto: así la extensión no nace pidiendo acceso a dominios que quizá
 * no use, y funciona igual si algún día cambias de dirección. */
async function pedirPermiso(url) {
  try {
    const origen = new URL(url).origin + '/*';
    const yaEsta = await chrome.permissions.contains({ origins: [origen] });
    if (yaEsta) return true;
    return await chrome.permissions.request({ origins: [origen] });
  } catch (e) {
    return false;
  }
}

$('save').addEventListener('click', async () => {
  const odooUrl = $('odooUrl').value.trim().replace(/\/+$/, '');
  const token = $('token').value.trim();
  if (!odooUrl || !token) return aviso('Rellena la URL y el token.', 'err');
  if (!await pedirPermiso(odooUrl)) {
    return aviso('Sin permiso para conectar con esa dirección, la extensión no puede enviar nada.', 'err');
  }
  await chrome.storage.local.set({ odooUrl, token });
  aviso('Guardado.', 'ok');
});

$('test').addEventListener('click', async () => {
  const odooUrl = $('odooUrl').value.trim().replace(/\/+$/, '');
  const token = $('token').value.trim();
  if (!odooUrl || !token) return aviso('Rellena la URL y el token antes de probar.', 'err');
  if (!await pedirPermiso(odooUrl)) return aviso('Falta conceder el permiso de conexión.', 'err');
  await chrome.storage.local.set({ odooUrl, token });
  aviso('Probando…', 'ok');
  const r = await chrome.runtime.sendMessage({ action: 'ping' });
  if (r?.ok) aviso(`Conectado con el perfil «${r.result.profile}» (umbral ${r.result.hot_threshold}).`, 'ok');
  else aviso(r?.error || 'Sin respuesta de Odoo.', 'err');
});
