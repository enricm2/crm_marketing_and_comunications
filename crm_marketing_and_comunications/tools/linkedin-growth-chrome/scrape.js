/* Lectura del DOM de LinkedIn.
 *
 * LinkedIn ofusca los nombres de clase y los cambia cada pocos meses, así que
 * aquí no se usa ni uno. Todo se ancla en dos cosas que no pueden cambiar sin
 * romper la propia página:
 *
 *   - los enlaces a perfiles, que siempre son /in/<slug>
 *   - los identificadores urn:li:activity: de la publicación
 *
 * A partir de un enlace de perfil se sube al contenedor de la fila y se lee lo
 * que hay dentro. Es más feo que un selector directo, pero sobrevive a los
 * rediseños.
 */
const LG = {};
/* Se va rellenando durante el proceso: cuando algo falla, el mensaje de error
 * dice exactamente en qué paso y con qué números, en vez de un genérico. */
LG.diag = {};

/* console.warn y no console.log a propósito: la consola de LinkedIn va tan
 * cargada de su propia telemetría que la gente acaba filtrando por nivel, y
 * con el filtro en «Errores y avisos» los console.log desaparecen. Un aviso
 * sobrevive a ese filtro. */
LG.log = (...a) => console.warn('[LinkedIn Growth]', ...a);

/* Traduce el diagnóstico a una frase que se pueda leer en el aviso rojo, para
 * no obligar a abrir la consola solo para saber por dónde se atascó. */
LG.resumenDiag = () => {
  const d = LG.diag || {};
  const p = [];
  if (d.urn === null) p.push('no identifiqué la publicación');
  if (d.botónReacciones === false) p.push('no encontré el botón de reacciones');
  else if (d.ventanaAbierta === false) p.push('el botón no abrió la lista');
  if (typeof d.enlacesEnVentana === 'number') p.push(`${d.enlacesEnVentana} perfiles en la lista`);
  if (typeof d.enlacesPerfilEnPagina === 'number') p.push(`${d.enlacesPerfilEnPagina} enlaces de perfil en la página`);
  if (typeof d.comentarios === 'number') p.push(`${d.comentarios} comentarios`);
  return p.length ? `Detalle: ${p.join('; ')}.` : '';
};
LG.sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* Pausa variable: al desplegar una lista larga hacemos las mismas peticiones
 * que haría una persona leyéndola, no una ráfaga. */
LG.humanPause = () => LG.sleep(500 + Math.random() * 700);

LG.normalizeProfileUrl = (href) => {
  if (!href) return '';
  try {
    const u = new URL(href, 'https://www.linkedin.com');
    const m = u.pathname.match(/\/in\/([^/]+)/);
    return m ? `https://www.linkedin.com/in/${decodeURIComponent(m[1])}` : '';
  } catch (e) {
    return '';
  }
};

/* URN de la publicación: primero la URL, luego cualquier atributo de la página
 * que lo lleve. La URL es lo más fiable cuando estamos en /feed/update/. */
LG.getPostUrn = () => {
  const fromUrl = location.href.match(/urn:li:(?:activity|share|ugcPost):\d+/);
  if (fromUrl) return fromUrl[0];
  // Las URL públicas usan otra forma: /posts/<slug>-activity-7266001-XyZ
  const slug = location.pathname.match(/activity-(\d{10,})/);
  if (slug) return `urn:li:activity:${slug[1]}`;
  const el = document.querySelector('[data-urn*="urn:li:activity"], [data-id*="urn:li:activity"]');
  if (el) {
    const v = el.getAttribute('data-urn') || el.getAttribute('data-id') || '';
    const m = v.match(/urn:li:(?:activity|share|ugcPost):\d+/);
    if (m) return m[0];
  }
  const html = document.documentElement.innerHTML.match(/urn:li:activity:\d+/);
  return html ? html[0] : '';
};

/* El grado de contacto («· 2º», «• 3rd») y los botones se cuelan en el texto
 * de la fila. Se quitan aquí para que el nombre llegue limpio. */
LG.cleanName = (text) => {
  let t = (text || '').replace(/\s+/g, ' ').trim();

  // LinkedIn pega al nombre, en el mismo nodo de texto, dos cosas que no lo
  // son: la insignia de perfil verificado y el grado de contacto. Y el grado
  // no siempre viene con su separador «·», así que hay que quitarlo también
  // cuando aparece suelto al final: «Carlos García Blanco perfil Verificado 1er».
  const COLAS = [
    /\s*[·•∙‧]?\s*(?:perfil\s+)?verificad[oa]s?\b\.?$/i,
    /\s*[·•∙‧]?\s*verified(\s+profile)?\b\.?$/i,
    /\s*[·•∙‧]?\s*(?:1|2|3)\s*(?:º|°|er|ro|nd|rd|st|th|do)\s*\+?$/i,
    /\s*[·•∙‧]\s*(?:1|2|3)\s*\+?$/i,
    /\s*[·•∙‧]\s*$/,
  ];
  // En bucle: pueden venir varias encadenadas y en cualquier orden.
  for (let i = 0; i < 6; i++) {
    const antes = t;
    COLAS.forEach((re) => { t = t.replace(re, '').trim(); });
    if (t === antes) break;
  }
  return t;
};

const NOISE = new Set([
  'seguir', 'follow', 'siguiendo', 'following', 'conectar', 'connect',
  'mensaje', 'message', 'ver perfil', 'view profile', 'me gusta', 'like',
  'responder', 'reply', 'ver más', 'see more', '· 1er', '· 2º', '· 3er',
]);

/* Dada la fila de una persona, saca nombre y titular.
 * El nombre suele venir duplicado (uno para lectores de pantalla, otro
 * visible); se toma el primero no vacío y se descarta el repetido. */
LG.readPerson = (row, anchor) => {
  const url = LG.normalizeProfileUrl(anchor.getAttribute('href'));
  if (!url) return null;

  const textos = [];
  row.querySelectorAll('span, p, div').forEach((el) => {
    if (el.querySelector('span, p, div')) return;      // solo hojas
    const t = (el.textContent || '').replace(/\s+/g, ' ').trim();
    if (!t || t.length > 300) return;
    if (NOISE.has(t.toLowerCase())) return;
    if (/^[·•∙]?\s*(1|2|3)\s*(º|°|er|ro|nd|rd|st|th)?\s*\+?$/i.test(t)) return;
    if (!textos.includes(t)) textos.push(t);
  });

  const nombre = LG.cleanName(textos[0] || anchor.textContent);
  if (!nombre) return null;
  const titular = textos.find((t) => t !== textos[0] && t.length > nombre.length) || '';

  return { nombre, url, titular };
};

/* Recorre un contenedor y devuelve una persona por cada enlace de perfil,
 * sin repetir. */
LG.collectPeople = (root) => {
  const vistos = new Set();
  const gente = [];
  root.querySelectorAll('a[href*="/in/"]').forEach((a) => {
    const url = LG.normalizeProfileUrl(a.getAttribute('href'));
    if (!url || vistos.has(url)) return;
    // Subir hasta el contenedor de la fila: <li> si lo hay, si no un par de
    // niveles, que es donde LinkedIn agrupa nombre y titular.
    const row = a.closest('li') || a.parentElement?.parentElement?.parentElement || a.parentElement;
    if (!row) return;
    const p = LG.readPerson(row, a);
    if (!p) return;
    vistos.add(url);
    gente.push(p);
  });
  return gente;
};

/* Abre la ventana de reacciones y la despliega hasta el final. */
LG.profileHrefs = (root) => {
  const set = new Set();
  (root || document).querySelectorAll('a[href*="/in/"]').forEach((a) => {
    const u = LG.normalizeProfileUrl(a.getAttribute('href'));
    if (u) set.add(u);
  });
  return set;
};

/* La extensión NO pulsa nada de LinkedIn.
 *
 * La versión anterior buscaba el contador de reacciones y lo pulsaba. En
 * español el botón de reaccionar lleva aria-label «Reaccionar Recomendar»,
 * que se le parece lo suficiente: acabó dando "me gusta" a la publicación del
 * propio usuario. Y aun acertando, el clic dispara la navegación interna de
 * LinkedIn y deja la lista a medio montar.
 *
 * Así que ahora la lista la abre la persona —un clic, el mismo que daría para
 * copiar los nombres a mano— y la extensión solo LEE lo que ya está en
 * pantalla. Es más robusto, y elimina de raíz que pueda tocar algo que no
 * debe. */
LG.findOpenReactorList = () => {
  const candidatos = [];

  // Preferencia: un diálogo abierto con perfiles dentro.
  document.querySelectorAll('[role="dialog"], .artdeco-modal').forEach((d) => {
    const n = LG.profileHrefs(d).size;
    if (n > 0) candidatos.push({ el: d, n, tipo: 'diálogo' });
  });

  // Si no hay diálogo reconocible, el bloque más concentrado de perfiles que
  // no sea la página entera.
  if (!candidatos.length) {
    document.querySelectorAll('ul, section, div').forEach((el) => {
      if (el === document.body) return;
      const n = LG.profileHrefs(el).size;
      // Al menos 3 perfiles y que no contenga otro bloque igual de denso:
      // así se coge la lista y no su contenedor.
      if (n < 3) return;
      const hijoDenso = Array.from(el.children).some((c) => LG.profileHrefs(c).size >= n);
      if (hijoDenso) return;
      candidatos.push({ el, n, tipo: el.tagName.toLowerCase() });
    });
  }

  candidatos.sort((a, b) => b.n - a.n);
  LG.diag.listasEncontradas = candidatos.slice(0, 3).map((c) => `${c.tipo}:${c.n}`);
  return candidatos[0] || null;
};

/* ¿Hay un diálogo abierto, aunque todavía esté vacío? Sirve para distinguir
 * «no lo has abierto» de «lo has abierto pero aún no ha cargado». */
LG.hayDialogoAbierto = () =>
  Boolean(document.querySelector('[role="dialog"], .artdeco-modal'));

LG.expandList = async (ctx, onProgress) => {
  const dlg = ctx.root;
  let previos = -1;
  for (let vuelta = 0; vuelta < 40; vuelta++) {
    const scroller = Array.from(dlg.querySelectorAll('*')).find(
      (el) => el.scrollHeight > el.clientHeight + 40 && el.clientHeight > 120
    ) || dlg;
    scroller.scrollTop = scroller.scrollHeight;

    // Único clic que da la extensión, y solo dentro de la lista ya abierta:
    // «Cargar más» no cambia ningún estado, solo pagina lo que se está leyendo.
    const cargarMás = Array.from(dlg.querySelectorAll('button')).find((b) => {
      const t = (b.textContent || '').replace(/\s+/g, ' ').trim();
      return /^(cargar más|mostrar más|ver más resultados|load more|show more( results)?)$/i.test(t);
    });
    if (cargarMás) cargarMás.click();

    await LG.humanPause();
    const n = LG.profileHrefs(dlg).size;
    if (onProgress) onProgress(n);
    if (n === previos) break;          // ya no crece: lista completa
    previos = n;
  }
};

/* Comentarios: están en la propia página, no en una ventana. Además del
 * autor interesa el texto, que es el mejor contexto para la primera
 * conversación. */
LG.collectComments = () => {
  const salida = [];
  const vistos = new Set();
  // Un comentario es un bloque que contiene un enlace de perfil Y bastante
  // texto propio. No se usa ningún nombre de clase: LinkedIn los ofusca.
  document.querySelectorAll('article, li, div[data-id]').forEach((art) => {
    const a = art.querySelector('a[href*="/in/"]');
    if (!a) return;
    if (art.querySelectorAll('a[href*="/in/"]').length > 3) return;   // es un contenedor, no una fila
    const url = LG.normalizeProfileUrl(a.getAttribute('href'));
    if (!url || vistos.has(url)) return;
    const p = LG.readPerson(art, a);
    if (!p) return;
    // El texto del comentario: la línea más larga que no sea el nombre ni el titular.
    const trozos = Array.from(art.querySelectorAll('span, p'))
      .filter((el) => !el.querySelector('span, p'))
      .map((el) => (el.textContent || '').replace(/\s+/g, ' ').trim())
      .filter((t) => t.length > 15 && t !== p.nombre && t !== p.titular);
    const texto = trozos.sort((x, y) => y.length - x.length)[0] || '';
    if (!texto) return;
    vistos.add(url);
    salida.push({ ...p, comentario: texto.slice(0, 2000) });
  });
  return salida;
};

/* Métricas visibles de la publicación: impresiones solo aparecen en tus
 * propios posts, y no siempre. */
LG.readMetrics = () => {
  const num = (s) => {
    const m = (s || '').replace(/\./g, '').match(/(\d[\d.,]*)\s*(mil|k|m)?/i);
    if (!m) return 0;
    let n = parseFloat(m[1].replace(',', '.'));
    if (/mil|k/i.test(m[2] || '')) n *= 1000;
    if (/^m$/i.test(m[2] || '')) n *= 1000000;
    return Math.round(n);
  };
  const texto = document.body.innerText || '';
  const imp = texto.match(/([\d.,]+)\s*(impresiones|impressions)/i);
  return { impresiones: imp ? num(imp[1]) : 0 };
};

/* --------------------------------------------------------------------------
 * Volcado de diagnóstico. Temporal: sirve para ver el HTML real de LinkedIn
 * cuando los selectores dejan de encontrar la lista. No toca nada de la
 * página; serializa el DOM que ya está en pantalla y lo descarga como .txt.
 * -------------------------------------------------------------------------- */
LG.dumpDiagnostico = () => {
  const uniqIn = (el) => new Set(
    Array.from(el.querySelectorAll('a[href*="/in/"]'))
      .map((a) => LG.normalizeProfileUrl(a.getAttribute('href')))
      .filter(Boolean)
  ).size;

  const CAP = 60000;
  const partes = [];
  const push = (titulo, texto) => partes.push(`\n\n===== ${titulo} =====\n${texto}`);

  push('CONTEXTO', JSON.stringify({
    url: location.href,
    fecha: new Date().toISOString(),
    version: chrome.runtime.getManifest ? chrome.runtime.getManifest().version : '?',
    totalEnlacesIn: document.querySelectorAll('a[href*="/in/"]').length,
    roleDialog: document.querySelectorAll('[role="dialog"]').length,
    ariaModal: document.querySelectorAll('[aria-modal="true"]').length,
    artdecoModal: document.querySelectorAll('.artdeco-modal').length,
    urn: LG.getPostUrn() || null,
  }, null, 2));

  // Diálogos / modales completos, cualquiera que sea su marcado.
  document.querySelectorAll('[role="dialog"], [aria-modal="true"], .artdeco-modal')
    .forEach((d, i) => push(`DIALOGO #${i} (${uniqIn(d)} perfiles)`, d.outerHTML.slice(0, CAP)));

  // Bloques más densos en enlaces de perfil (misma idea que findOpenReactorList),
  // con las primeras filas sueltas para ver la estructura de cada persona.
  const cands = [];
  document.querySelectorAll('ul, section, div, aside').forEach((el) => {
    if (el === document.body) return;
    const n = uniqIn(el);
    if (n < 3 || n > 800) return;
    if (Array.from(el.children).some((c) => uniqIn(c) >= n)) return;
    cands.push({ el, n });
  });
  cands.sort((a, b) => b.n - a.n);
  cands.slice(0, 3).forEach((c, i) => {
    push(`CANDIDATO #${i} <${c.el.tagName.toLowerCase()}> (${c.n} perfiles)`, c.el.outerHTML.slice(0, CAP));
    Array.from(c.el.querySelectorAll('a[href*="/in/"]')).slice(0, 5).forEach((a, j) => {
      const fila = a.closest('li') || a.parentElement?.parentElement?.parentElement || a.parentElement;
      push(`  CANDIDATO #${i} · FILA #${j}`, (fila || a).outerHTML.slice(0, 15000));
    });
  });

  // Primeros bloques que parecen comentarios.
  let nc = 0;
  document.querySelectorAll('article, li, div[data-id]').forEach((art) => {
    if (nc >= 4) return;
    const a = art.querySelector('a[href*="/in/"]');
    if (!a || art.querySelectorAll('a[href*="/in/"]').length > 3) return;
    push(`COMENTARIO? #${nc}`, art.outerHTML.slice(0, 15000));
    nc++;
  });

  const blob = new Blob([partes.join('')], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `lg-diagnostico-${Date.now()}.txt`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
  LG.log('diagnóstico volcado:', a.download);
};

/* Titular de la publicación: la primera línea de su texto. */
LG.readPostTitle = () => {
  // Solo se devuelve algo si de verdad es el texto de la publicación. Antes
  // caía al document.title y mandaba «Publicación | LinkedIn», que además
  // pisaba el titular que el usuario hubiera escrito en Odoo.
  const el = document.querySelector('[class*="update-components-text"], [class*="feed-shared-update-v2__description"]');
  if (!el) return '';
  const t = (el.textContent || '').replace(/\s+/g, ' ').trim();
  return t.length > 10 ? t.slice(0, 200) : '';
};
