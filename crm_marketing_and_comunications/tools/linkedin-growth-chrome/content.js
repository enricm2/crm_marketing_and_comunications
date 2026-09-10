/* Botón e hilo del proceso. La lectura del DOM está en scrape.js; la llamada
 * a Odoo, en el service worker (background.js), que es quien puede hacer
 * peticiones a otro dominio sin toparse con CORS. */

const BTN_ID = 'lg-import-btn';
const TOAST_ID = 'lg-toast-host';

/* La interfaz de la extensión vive dentro de un Shadow DOM.
 *
 * Inyectar elementos sueltos en LinkedIn no funciona: sus hojas de estilo y
 * sus ancestros con `transform` desplazaban el aviso hasta dejarlo medio
 * fuera de pantalla, y colgarlo de <html> —fuera de <body>— hacía que ni
 * siquiera se le aplicara el CSS de la extensión, con lo que aparecía como un
 * bloque suelto al final de la página.
 *
 * Con un shadow root, los estilos de LinkedIn no entran y los míos no salen.
 * El anclaje va en el estilo en línea del anfitrión, que es lo único que la
 * página no puede tocar. */
const ESTILOS = `
  :host { display: block; }
  * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, "Segoe UI", system-ui, Arial, sans-serif; }
  .caja {
    position: relative; max-width: 380px; max-height: 70vh; overflow-y: auto;
    padding: 14px 38px 14px 17px; border-radius: 10px;
    font-size: 14px; line-height: 1.45; color: #FFFFFF; background: #1A3A5C;
    box-shadow: 0 6px 22px rgba(0,0,0,.30); white-space: pre-line;
  }
  .caja.ok    { background: #1E7F4E; }
  .caja.error { background: #A32820; }
  .copiar {
    display: block; margin-top: 11px; font-size: 13px; font-weight: 600;
    color: #1A3A5C; background: #FFFFFF; border: 0; border-radius: 6px;
    padding: 7px 12px; cursor: pointer;
  }
  .copiar:hover { background: #EEF2F6; }
  .cerrar {
    position: absolute; top: 5px; right: 9px; font-size: 21px; line-height: 1;
    color: #FFFFFF; background: none; border: 0; cursor: pointer; opacity: .75;
  }
  .cerrar:hover { opacity: 1; }
  .boton {
    font-size: 14px; font-weight: 600; line-height: 1; color: #FFFFFF;
    background: #1A3A5C; border: 0; border-radius: 999px;
    padding: 10px 17px; cursor: pointer; box-shadow: 0 4px 14px rgba(0,0,0,.22);
  }
  .boton:hover:not(:disabled) { background: #14304C; }
  .boton:disabled { opacity: .55; cursor: progress; }
`;

function crearAnfitrion(id, anclaje) {
  document.getElementById(id)?.remove();
  const host = document.createElement('div');
  host.id = id;
  // Sin `all: initial`: reinicia también `display` y el anfitrión puede
  // quedarse sin renderizar. Se declaran solo las propiedades necesarias,
  // todas con !important para que ninguna hoja de LinkedIn las mueva.
  host.setAttribute('style',
    'display: block !important; position: fixed !important; ' +
    'width: auto !important; height: auto !important; ' +
    'margin: 0 !important; padding: 0 !important; border: 0 !important; ' +
    'visibility: visible !important; opacity: 1 !important; ' +
    'pointer-events: auto !important; transform: none !important; ' +
    'z-index: 2147483647 !important; ' + anclaje);
  document.body.appendChild(host);
  const raiz = host.attachShadow({ mode: 'open' });
  const est = document.createElement('style');
  est.textContent = ESTILOS;
  raiz.appendChild(est);
  return { host, raiz };
}

function toast(texto, tipo = 'info', persistente = false, diagnostico = null) {
  const { host, raiz } = crearAnfitrion(TOAST_ID, 'right: 20px !important; bottom: 20px !important;');

  const caja = document.createElement('div');
  caja.className = `caja ${tipo}`;

  const p = document.createElement('div');
  p.textContent = texto;
  caja.appendChild(p);

  if (diagnostico) {
    const b = document.createElement('button');
    b.className = 'copiar';
    b.type = 'button';
    b.textContent = 'Copiar diagnóstico';
    b.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(JSON.stringify(diagnostico, null, 2));
        b.textContent = 'Copiado ✓';
      } catch (e) {
        b.textContent = 'No se pudo copiar';
      }
    });
    caja.appendChild(b);
  }

  const cerrar = document.createElement('button');
  cerrar.className = 'cerrar';
  cerrar.type = 'button';
  cerrar.setAttribute('aria-label', 'Cerrar');
  cerrar.textContent = '×';
  cerrar.addEventListener('click', () => host.remove());
  caja.appendChild(cerrar);

  raiz.appendChild(caja);
  if (!persistente) setTimeout(() => host.remove(), 7000);
  return { host, texto: p };
}

function estadoTexto(t, texto) { t.texto.textContent = texto; }

async function importar(btn) {
  btn.disabled = true;
  const original = btn.textContent;
  const estado = toast('Leyendo la publicación…', 'info', true);

  try {
    LG.diag = { url: location.href, pasos: [] };
    const paso = (t) => { LG.diag.pasos.push(t); LG.log(t); };
    paso('inicio');
    const urn = LG.getPostUrn();
    LG.diag.urn = urn || null;
    if (!urn) throw new Error('No he encontrado el identificador de la publicación. ' + LG.resumenDiag());
    paso(`urn=${urn}`);

    // 1. Comentarios: están en la página, no hay que abrir nada.
    const comentarios = LG.collectComments();
    LG.diag.comentarios = comentarios.length;
    LG.diag.enlacesPerfilEnPagina = document.querySelectorAll('a[href*="/in/"]').length;
    paso(`comentarios=${comentarios.length} enlacesEnPagina=${LG.diag.enlacesPerfilEnPagina}`);

    // 2. Reacciones: la lista la abre la persona, la extensión solo lee.
    let reacciones = [];
    const lista = LG.findOpenReactorList();
    if (lista) {
      estadoTexto(estado, `Leyendo la lista… ${lista.n}`);
      await LG.expandList({ root: lista.el }, (n) => {
        estadoTexto(estado, `Leyendo la lista… ${n}`);
      });
      reacciones = LG.collectPeople(lista.el);
      LG.diag.enlacesEnLista = LG.profileHrefs(lista.el).size;
      paso(`listaEncontrada=${lista.n} reaccionesLeidas=${reacciones.length}`);
    } else if (!comentarios.length) {
      // No es un error: falta un paso que solo puede dar la persona.
      estado.host.remove();
      toast(
        LG.hayDialogoAbierto()
          ? 'La lista está abierta pero aún no ha cargado a nadie. Espera un par de segundos, desplázala un poco y vuelve a pulsar.'
          : 'Abre tú la lista: pulsa donde pone «N reacciones» bajo la publicación, espera a que salgan los nombres y vuelve a pulsar este botón.\n\nLa extensión no pulsa nada de LinkedIn a propósito: solo lee lo que ya está en pantalla.',
        'info', true, LG.diag
      );
      return;
    }
    LG.diag.reacciones = reacciones.length;

    if (!reacciones.length && !comentarios.length) {
      throw new Error('No he leído a nadie. ' + LG.resumenDiag());
    }

    // Quien comenta manda sobre quien solo reacciona: el comentario vale más
    // puntos y trae texto. Si alguien hizo las dos cosas, se envía la señal
    // de comentario y no se duplica como reacción.
    const conComentario = new Set(comentarios.map((c) => c.url));
    const filas = [
      ...comentarios.map((c) => ({
        nombre: c.nombre, linkedin_url: c.url, headline: c.titular,
        tipo_senal: 'comentario', comentario: c.comentario,
        fecha_senal: new Date().toISOString(), post_id: urn,
      })),
      ...reacciones
        .filter((r) => !conComentario.has(r.url))
        .map((r) => ({
          nombre: r.nombre, linkedin_url: r.url, headline: r.titular,
          tipo_senal: 'reaccion',
          fecha_senal: new Date().toISOString(), post_id: urn,
        })),
    ];

    estadoTexto(estado, `Enviando ${filas.length} señales a Odoo…`);
    paso(`enviando=${filas.length}`);
    const resp = await chrome.runtime.sendMessage({
      action: 'import',
      post: {
        external_id: urn,
        titulo: LG.readPostTitle() || '',
        url: location.href.split('?')[0],
        fecha_publicacion: new Date().toISOString(),
        reacciones: reacciones.length,
        comentarios: comentarios.length,
        impresiones: LG.readMetrics().impresiones,
      },
      rows: filas,
    });

    if (!resp || !resp.ok) throw new Error(resp?.error || 'Odoo no ha respondido.');
    const r = resp.result;
    estado.host.remove();
    LG.diag.resultado = r;
    toast(
      `${r.created} señales nuevas · ${r.duplicated} ya estaban · ` +
      `${r.leads_created} leads nuevos · ${r.hot_signals} calientes`,
      'ok', true, LG.diag
    );
  } catch (e) {
    LG.log('error', e);
    console.warn('[LinkedIn Growth] DIAGNÓSTICO', JSON.stringify(LG.diag, null, 2));
    window.__lgDiag = LG.diag;
    estado.host.remove();
    toast(e.message || String(e), 'error', true, LG.diag);
  } finally {
    btn.disabled = false;
    btn.textContent = original;
  }
}

function pintarBoton() {
  if (document.getElementById(BTN_ID)) return;
  const { host, raiz } = crearAnfitrion(BTN_ID, 'right: 20px !important; bottom: 86px !important;');
  const cont = document.createElement('div');
  cont.style.cssText = 'display:flex; flex-direction:column; gap:8px; align-items:flex-end;';

  const btn = document.createElement('button');
  btn.className = 'boton';
  btn.type = 'button';
  btn.textContent = 'Importar a LinkedIn Growth';
  btn.addEventListener('click', () => importar(btn));
  cont.appendChild(btn);

  // Botón temporal: descarga el HTML de la lista para depurar cuando LinkedIn
  // cambia su marcado y los selectores dejan de encontrarla.
  const dbg = document.createElement('button');
  dbg.className = 'boton';
  dbg.type = 'button';
  dbg.style.cssText = 'background:#5A3A1A; font-size:12px; padding:7px 13px;';
  dbg.textContent = 'Volcar diagnóstico';
  dbg.addEventListener('click', () => {
    try { LG.dumpDiagnostico(); toast('Diagnóstico descargado en tu carpeta de Descargas.', 'ok'); }
    catch (e) { toast('No se pudo volcar: ' + (e.message || e), 'error', true); }
  });
  cont.appendChild(dbg);

  raiz.appendChild(cont);
  requestAnimationFrame(() => {
    const r = host.getBoundingClientRect();
    LG.log(`botón añadido · ${Math.round(r.width)}x${Math.round(r.height)} en (${Math.round(r.left)},${Math.round(r.top)})`);
    if (!r.width || !r.height) LG.log('AVISO: el botón se creó pero mide 0. Algo lo está ocultando.');
  });
}

/* LinkedIn navega sin recargar, así que hay que volver a pintar el botón
 * cuando cambia el contenido. */
console.warn('[LinkedIn Growth] content script activo · v' +
  (chrome.runtime.getManifest ? chrome.runtime.getManifest().version : '?') +
  ' · ' + location.pathname);

const obs = new MutationObserver(() => {
  if (/\/feed\/update\/|\/posts\//.test(location.pathname + location.href)) pintarBoton();
});
obs.observe(document.body, { childList: true, subtree: true });
pintarBoton();
