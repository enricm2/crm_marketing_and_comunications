/* Service worker: el único sitio de la extensión que habla con Odoo.
 * Se hace aquí y no desde el content script porque una petición a otro
 * dominio desde la página de LinkedIn chocaría con CORS; desde el worker,
 * con el permiso de host concedido, no. */

async function ajustes() {
  const s = await chrome.storage.local.get(['odooUrl', 'token']);
  const url = (s.odooUrl || '').replace(/\/+$/, '');
  if (!url || !s.token) {
    throw new Error('Falta configurar la URL de Odoo y el token. Abre las opciones de la extensión.');
  }
  return { url, token: s.token };
}

async function postJson(base, token, ruta, cuerpo) {
  const r = await fetch(base + ruta, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Vantis-Token': token },
    body: JSON.stringify(cuerpo),
  });
  const texto = await r.text();
  let data;
  try { data = JSON.parse(texto); } catch (e) {
    throw new Error(`Odoo respondió algo que no es JSON (HTTP ${r.status}). ¿Es correcta la URL?`);
  }
  if (r.status === 401 || r.status === 403) throw new Error('Token no reconocido por Odoo.');
  if (!r.ok) throw new Error(data.message || `Odoo devolvió HTTP ${r.status}.`);
  return data;
}

chrome.runtime.onMessage.addListener((msg, sender, responder) => {
  (async () => {
    try {
      const { url, token } = await ajustes();

      if (msg.action === 'ping') {
        const r = await fetch(`${url}/vantis/linkedin/ping`, { headers: { 'X-Vantis-Token': token } });
        const d = await r.json();
        if (!d.ok) throw new Error(d.message || 'Token no reconocido.');
        responder({ ok: true, result: d });
        return;
      }

      if (msg.action === 'import') {
        // La publicación primero: así las señales encuentran su post por URN
        // y quedan vinculadas sin pasos manuales.
        await postJson(url, token, '/vantis/linkedin/posts', { posts: [msg.post] });
        const r = await postJson(url, token, '/vantis/linkedin/signals', {
          source: 'n8n_api',
          source_file: `extensión Chrome · ${msg.post.external_id}`,
          rows: msg.rows,
        });
        responder({ ok: true, result: r });
        return;
      }

      responder({ ok: false, error: 'Acción desconocida.' });
    } catch (e) {
      responder({ ok: false, error: e.message || String(e) });
    }
  })();
  return true;                      // respuesta asíncrona
});
