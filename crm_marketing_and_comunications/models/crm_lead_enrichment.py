"""Enriquecimiento de leads con IA (Anthropic / Gemini / OpenAI)."""
import json
import logging
import re
import threading
import urllib.request
import urllib.error
import urllib.parse
from html.parser import HTMLParser

import odoo
import odoo.api
from odoo import models, fields, api
from odoo.modules.registry import Registry
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)
_P = 'crm_marketing_and_comunications.'

# Patrones de redes sociales
_SOCIAL_PATTERNS = {
    'linkedin': re.compile(r'https?://(?:www\.)?linkedin\.com/(?:in|company|pub)/[^\s"\'<>]+', re.I),
    'twitter':  re.compile(r'https?://(?:www\.)?(?:twitter\.com|x\.com)/[^\s"\'<>?/]+', re.I),
    'meta':     re.compile(r'https?://(?:www\.)?(?:facebook\.com|fb\.com|instagram\.com)/[^\s"\'<>?]+', re.I),
}

# Palabras clave que identifican páginas con datos de contacto y legales
_KEY_PAGE_PATTERNS = re.compile(
    r'contact|contacto|contacta|contact-us|about|sobre|acerca|quienes|'
    r'who-we-are|nosotros|empresa|equipo|team|'
    r'aviso.?legal|legal|privacidad|privacy|rgpd|gdpr|lopd|'
    r'proteccion.?datos|politica|terms|condiciones|cookies',
    re.I,
)

_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'es-ES,es;q=0.9,en;q=0.8',
    'Accept-Encoding': 'identity',
}


# ── Scraper web ────────────────────────────────────────────────────────────────

class _TextExtractor(HTMLParser):
    """Extrae texto plano, title, meta description, links internos y RRSS."""
    SKIP = {'script', 'style', 'noscript'}

    def __init__(self):
        super().__init__()
        self.title = ''
        self.description = ''
        self._chunks = []
        self._skip = False
        self._in_title = False
        self._in_a = False
        self._current_href = ''
        self._current_a_text = []
        self._depth = 0
        # Lista de (href, anchor_text) para poder filtrar por texto del enlace
        self.raw_links = []        # compatibilidad: lista de hrefs
        self.links_with_text = []  # lista de (href, anchor_text)

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip = True
            self._depth += 1
        if tag == 'title':
            self._in_title = True
        if tag == 'meta':
            d = dict(attrs)
            if d.get('name', '').lower() == 'description':
                self.description = d.get('content', '')
        if tag == 'a':
            href = dict(attrs).get('href', '').strip()
            self._current_href = href
            self._current_a_text = []
            self._in_a = bool(href)
            if href:
                self.raw_links.append(href)

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self._depth -= 1
            if self._depth <= 0:
                self._skip = False
                self._depth = 0
        if tag == 'title':
            self._in_title = False
        if tag == 'a' and self._in_a:
            anchor_text = ' '.join(self._current_a_text).strip()
            self.links_with_text.append((self._current_href, anchor_text))
            self._in_a = False
            self._current_href = ''
            self._current_a_text = []

    def handle_data(self, data):
        text = data.strip()
        if not text:
            return
        if self._in_title:
            self.title = text
        if self._in_a:
            self._current_a_text.append(text)
        if not self._skip:
            self._chunks.append(text)

    def get_text_head_tail(self, head=3000, tail=2000):
        full = ' '.join(self._chunks)
        if len(full) <= head + tail:
            return full
        return full[:head] + '\n...\n' + full[-tail:]

    def get_social_links(self):
        result = {'linkedin': '', 'twitter': '', 'meta': ''}
        for href in self.raw_links:
            for net, pat in _SOCIAL_PATTERNS.items():
                if not result[net] and pat.match(href):
                    result[net] = href.split('?')[0].rstrip('/')
        return result


def _fetch_html(url: str, timeout: int = 12) -> tuple[str, str]:
    """Descarga una URL. Devuelve (html, content_type) o ('', '')."""
    try:
        req = urllib.request.Request(url, headers=dict(_HEADERS))
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ct = resp.headers.get('Content-Type', '')
            if 'html' not in ct.lower():
                return '', ''
            raw = resp.read(400_000)
            charset = 'utf-8'
            m = re.search(r'charset=([^\s;]+)', ct)
            if m:
                charset = m.group(1)
            return raw.decode(charset, errors='replace'), ct
    except Exception as exc:
        _logger.debug('Scrape fetch error %s: %s', url, exc)
        return '', ''


def _scrape_url(url: str, head: int = 3000, tail: int = 2000) -> dict:
    """Descarga y parsea una URL. Devuelve dict con title, description, text, social, links, links_with_text, tels, emails."""
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url

    html, _ = _fetch_html(url)
    if not html:
        return {'title': '', 'description': '', 'text': '', 'social': {}, 'links': [], 'links_with_text': [], 'tels': [], 'emails': []}

    # Extraer teléfonos de tel: links (los más fiables — son clicables en móvil)
    tels = list(dict.fromkeys(
        t.strip() for t in re.findall(r'tel:([+\d\s\-\(\)\.]+)', html, re.I)
        if len(re.sub(r'\D', '', t)) >= 7
    ))

    # Extraer emails de mailto: links
    emails = list(dict.fromkeys(
        e.strip().lower() for e in re.findall(r'mailto:([^\s"\'<>?&,;]+)', html, re.I)
        if '@' in e
    ))

    # RRSS en HTML crudo (más fiable para contenido dinámico)
    extra_social = {'linkedin': '', 'twitter': '', 'meta': ''}
    for net, pat in _SOCIAL_PATTERNS.items():
        m = pat.search(html)
        if m:
            extra_social[net] = m.group(0).split('?')[0].rstrip('/')

    parser = _TextExtractor()
    try:
        parser.feed(html)
    except Exception:
        pass

    social = parser.get_social_links()
    for net in ('linkedin', 'twitter', 'meta'):
        if not social[net] and extra_social[net]:
            social[net] = extra_social[net]

    return {
        'title': parser.title,
        'description': parser.description,
        'text': parser.get_text_head_tail(head, tail),
        'social': social,
        'links': parser.raw_links,
        'links_with_text': parser.links_with_text,
        'tels': tels,
        'emails': emails,
    }


# Palabras clave en el TEXTO del enlace que indican páginas legales/contacto
_ANCHOR_TEXT_PATTERNS = re.compile(
    r'pol[ií]tica\s+de\s+privacidad|privacidad|aviso\s+legal|legal|'
    r'protecci[oó]n\s+de\s+datos|datos\s+personales|rgpd|gdpr|lopd|'
    r'contacto|contact[ao]|sobre\s+nosotros|qui[eé]nes\s+somos|about|'
    r'equipo|team|empresa',
    re.I,
)

# Rutas comunes de páginas legales para usar como fallback
_PRIVACY_FALLBACK_PATHS = [
    '/politica-de-privacidad', '/politica-privacidad', '/privacidad',
    '/aviso-legal', '/aviso_legal', '/legal',
    '/politica-cookies', '/cookies',
    '/proteccion-de-datos', '/proteccion_datos',
    '/rgpd', '/gdpr', '/lopd',
    '/privacy-policy', '/privacy',
    '/contacto', '/contact', '/contacta',
]


def _find_key_subpages(base_url: str, links_with_text: list[tuple], max_pages: int = 5) -> list[str]:
    """
    Selecciona subpáginas del mismo dominio relevantes para datos de contacto y legales.
    Busca tanto en la URL/path como en el texto del enlace (anchor text).
    links_with_text: lista de (href, anchor_text) tal como devuelve _TextExtractor.
    """
    parsed = urllib.parse.urlparse(base_url)
    base_domain = parsed.netloc.lower()
    base_root = f"{parsed.scheme}://{parsed.netloc}"

    seen = set()
    selected = []

    for href, anchor_text in links_with_text:
        if len(selected) >= max_pages:
            break

        # Construir URL absoluta
        if href.startswith('http'):
            abs_url = href
        elif href.startswith('/'):
            abs_url = base_root + href
        else:
            continue  # ignorar anchors, mailto, javascript, etc.

        # Solo mismo dominio
        parsed_href = urllib.parse.urlparse(abs_url)
        if parsed_href.netloc.lower() != base_domain:
            continue

        path = parsed_href.path.lower()

        # Sin extensiones de archivo que no sean HTML
        if re.search(r'\.(pdf|jpg|png|gif|svg|css|js|xml|zip)$', path, re.I):
            continue

        # Coincidencia por URL/path O por texto del enlace
        path_match = _KEY_PAGE_PATTERNS.search(path)
        text_match = _ANCHOR_TEXT_PATTERNS.search(anchor_text) if anchor_text else None

        if not path_match and not text_match:
            continue

        clean = abs_url.split('#')[0].split('?')[0].rstrip('/')
        if clean and clean not in seen and clean != base_root:
            seen.add(clean)
            selected.append(clean)

    return selected


def _find_privacy_pages_fallback(base_url: str, existing: set) -> list[str]:
    """
    Prueba rutas comunes de política de privacidad / aviso legal.
    Solo incluye las que responden con HTML (HEAD request).
    Se usa cuando el scraping de la portada no encontró páginas legales.
    """
    parsed = urllib.parse.urlparse(base_url)
    base_root = f"{parsed.scheme}://{parsed.netloc}"
    found = []

    for path in _PRIVACY_FALLBACK_PATHS:
        url = base_root + path
        if url in existing:
            continue
        try:
            req = urllib.request.Request(url, method='HEAD', headers=dict(_HEADERS))
            with urllib.request.urlopen(req, timeout=6) as resp:
                ct = resp.headers.get('Content-Type', '')
                if resp.status < 400 and 'html' in ct.lower():
                    found.append(url)
                    _logger.info('Privacy fallback found: %s', url)
                    # Con una página de privacidad ya es suficiente para el fallback
                    if re.search(r'privacidad|aviso|legal|rgpd|gdpr|lopd|privacy', path, re.I):
                        break
        except Exception:
            pass

    return found


def _scrape_site(lead_url: str) -> dict:
    """
    Raspa la portada + subpáginas clave (contacto, sobre nosotros, aviso legal,
    política de privacidad). La política de privacidad tiene prioridad máxima
    porque por RGPD debe contener nombre legal, NIF/CIF y domicilio social.

    Si la portada no enlaza a páginas legales (URL ni anchor text), prueba
    rutas comunes como /politica-de-privacidad, /aviso-legal, /legal, etc.
    """
    if not lead_url.startswith(('http://', 'https://')):
        lead_url = 'https://' + lead_url

    # 1. Portada
    main = _scrape_url(lead_url, head=2000, tail=1500)
    social = dict(main['social'])
    all_tels = list(main['tels'])
    all_emails = list(main['emails'])

    sections = []
    if main['text']:
        sections.append(f"[PORTADA — {main['title']}]\n{main['text']}")

    # 2. Identificar subpáginas clave buscando en URL y en texto del enlace
    subpages = _find_key_subpages(lead_url, main['links_with_text'], max_pages=5)
    _logger.info('Enrichment: subpages found via links for %s: %s', lead_url, subpages)

    # 3. Fallback: si no encontramos ninguna página legal/contacto,
    #    probar rutas comunes de política de privacidad y aviso legal
    has_legal = any(
        re.search(r'aviso|legal|privacidad|rgpd|gdpr|lopd|privacy|datos|protec', u, re.I)
        for u in subpages
    )
    if not has_legal:
        fallback_pages = _find_privacy_pages_fallback(lead_url, set(subpages))
        if fallback_pages:
            _logger.info('Enrichment: privacy fallback pages for %s: %s', lead_url, fallback_pages)
            subpages = fallback_pages + subpages  # las páginas legales van primero

    # 4. Ordenar: política de privacidad/aviso legal siempre primero
    #    (contienen los datos RGPD obligatorios: nombre, CIF, domicilio)
    def _priority(url):
        if re.search(r'privacidad|privacy|rgpd|gdpr|lopd|protec', url, re.I):
            return 0
        if re.search(r'aviso|legal', url, re.I):
            return 1
        if re.search(r'contact', url, re.I):
            return 2
        if re.search(r'sobre|nosotros|about|quienes|equipo', url, re.I):
            return 3
        return 4

    subpages_sorted = sorted(subpages, key=_priority)

    # 5. Raspar subpáginas
    for sub_url in subpages_sorted:
        sub = _scrape_url(sub_url, head=3000, tail=2000)
        if sub['text']:
            label = sub['title'] or sub_url.split('/')[-1] or sub_url
            sections.append(f"[{label.upper()} — {sub_url}]\n{sub['text']}")
        for t in sub['tels']:
            if t not in all_tels:
                all_tels.append(t)
        for e in sub['emails']:
            if e not in all_emails:
                all_emails.append(e)
        for net in ('linkedin', 'twitter', 'meta'):
            if not social.get(net) and sub['social'].get(net):
                social[net] = sub['social'][net]

    return {
        'title': main['title'],
        'description': main['description'],
        'text': '\n\n'.join(sections),
        'social': social,
        'tels': all_tels,
        'emails': all_emails,
    }


# ── Llamadas a APIs de IA ──────────────────────────────────────────────────────

def _call_anthropic(api_key: str, prompt: str) -> str:
    import urllib.request, json
    body = json.dumps({
        'model': 'claude-sonnet-4-6',
        'max_tokens': 4096,
        'messages': [{'role': 'user', 'content': prompt}],
    }).encode()
    req = urllib.request.Request(
        'https://api.anthropic.com/v1/messages',
        data=body,
        headers={
            'x-api-key': api_key,
            'anthropic-version': '2023-06-01',
            'content-type': 'application/json',
        },
        method='POST',
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
    return data['content'][0]['text']


def _call_gemini(api_key: str, prompt: str) -> str:
    import urllib.request, json
    body = json.dumps({
        'contents': [{'parts': [{'text': prompt}]}],
        'generationConfig': {'maxOutputTokens': 4096},
    }).encode()
    url = (
        f'https://generativelanguage.googleapis.com/v1beta/models/'
        f'gemini-2.5-flash:generateContent?key={api_key}'
    )
    req = urllib.request.Request(
        url, data=body,
        headers={'content-type': 'application/json'},
        method='POST',
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
    return data['candidates'][0]['content']['parts'][0]['text']


def _call_openai(api_key: str, prompt: str) -> str:
    import urllib.request, json
    body = json.dumps({
        'model': 'gpt-4o',
        'messages': [{'role': 'user', 'content': prompt}],
        'max_tokens': 4096,
    }).encode()
    req = urllib.request.Request(
        'https://api.openai.com/v1/chat/completions',
        data=body,
        headers={
            'Authorization': f'Bearer {api_key}',
            'content-type': 'application/json',
        },
        method='POST',
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
    return data['choices'][0]['message']['content']


def _call_ai(anthropic_key, gemini_key, openai_key, preferred, prompt):
    """Llama al proveedor preferido, con fallback a los demás."""
    providers = {
        'anthropic': (anthropic_key, _call_anthropic),
        'gemini': (gemini_key, _call_gemini),
        'openai': (openai_key, _call_openai),
    }
    order = [preferred] + [k for k in providers if k != preferred]
    last_exc = None
    for name in order:
        key, fn = providers[name]
        if not key:
            continue
        try:
            return fn(key, prompt)
        except Exception as exc:
            _logger.warning('Enrichment AI %s error: %s', name, exc)
            last_exc = exc
    raise UserError(
        f'No se pudo obtener respuesta de ningún proveedor de IA.\n'
        f'Verifica las claves API en Ajustes → CRM - IA.\n'
        f'Último error: {last_exc}'
    )


def _extract_json(text: str) -> dict:
    """Extrae el primer bloque JSON del texto de la IA."""
    m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if m:
        text = m.group(1)
    else:
        m = re.search(r'\{.*\}', text, re.DOTALL)
        if m:
            text = m.group(0)
    try:
        return json.loads(text)
    except Exception:
        return {}


# ── Mixin con los campos y la acción de enriquecimiento ───────────────────────

class CrmLeadEnrichment(models.Model):
    _inherit = 'crm.lead'

    # ── Campo URL del lead ────────────────────────────────────────────────────
    lead_url = fields.Char(
        string='URL empresa lead',
        help='Dominio/URL de la empresa del lead. Se rellena automáticamente '
             'del email pero puedes editarlo.',
    )

    def init(self):
        """Backfill lead_url para leads existentes."""
        generic = (
            'gmail.com', 'hotmail.com', 'yahoo.com', 'outlook.com',
            'icloud.com', 'live.com', 'me.com', 'aol.com',
        )
        placeholders = ','.join(['%s'] * len(generic))

        # 1. Añadir https:// a leads que ya tienen dominio pero sin protocolo
        self.env.cr.execute("""
            UPDATE crm_lead
               SET lead_url = 'https://' || lead_url
             WHERE lead_url IS NOT NULL
               AND lead_url != ''
               AND lead_url NOT LIKE 'http%%'
        """)

        # 2. Rellenar lead_url vacío desde el email
        self.env.cr.execute(f"""
            UPDATE crm_lead
               SET lead_url = 'https://' || lower(
                       split_part(
                           regexp_replace(email_from, '.*@', ''),
                           '>', 1
                       )
                   )
             WHERE (lead_url IS NULL OR lead_url = '')
               AND email_from IS NOT NULL
               AND email_from LIKE '%%@%%'
               AND lower(
                       split_part(
                           regexp_replace(email_from, '.*@', ''),
                           '>', 1
                       )
                   ) NOT IN ({placeholders})
        """, generic)

    @api.onchange('email_from')
    def _onchange_email_from_url(self):
        """Rellena lead_url al cambiar el email en el formulario."""
        if self.email_from and not self.lead_url:
            domain = self._domain_from_email(self.email_from)
            if domain:
                self.lead_url = domain

    def _search_company_url(self, company_name):
        """Buscador integrado de sitios web usando DuckDuckGo HTML.
        
        Retorna la URL raíz o None si falla. Totalmente seguro contra fallos/bloqueos.
        """
        import urllib.parse
        import requests
        from bs4 import BeautifulSoup
        import random
        
        user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0'
        ]
        
        query = f"{company_name} website"
        headers = {
            'User-Agent': random.choice(user_agents),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'es-ES,es;q=0.9',
            'Referer': 'https://duckduckgo.com/',
        }
        url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote_plus(query)}"
        
        exclude_domains = [
            'linkedin.com', 'facebook.com', 'instagram.com', 'twitter.com', 'x.com',
            'youtube.com', 'crunchbase.com', 'wikipedia.org', 'mapspub', 'paginasamarillas',
            'axesor.es', 'einforma.com', 'empresia.es', 'infocif.es', 'github.com',
            'directorio', 'pymes', 'guiaenred', 'cylex', 'vulka', 'expansion.com',
            'pinterest.com', 'yahoo.com', 'google.com'
        ]
        
        try:
            response = requests.get(url, headers=headers, timeout=5)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                links = []
                for a in soup.find_all('a', href=True):
                    href = a['href']
                    if 'uddg=' in href:
                        parsed = urllib.parse.urlparse(href)
                        query_params = urllib.parse.parse_qs(parsed.query)
                        real_url = query_params.get('uddg', [None])[0]
                        if real_url:
                            links.append(real_url)
                    elif href.startswith('http') and not any(d in href for d in ['duckduckgo.com', 'google.com']):
                        links.append(href)
                        
                for link in links:
                    parsed_link = urllib.parse.urlparse(link)
                    domain = parsed_link.netloc.lower()
                    if domain and not any(ex in domain for ex in exclude_domains):
                        return f"{parsed_link.scheme}://{parsed_link.netloc}"
        except Exception:
            pass
        return None

    @api.model_create_multi
    def create(self, vals_list):
        """Auto-rellena lead_url al crear leads por código, excel, o API."""
        import re
        linkedin_pattern = re.compile(r'^LinkedIn\s*[\u2014\u2013-]\s*(.+)$', re.IGNORECASE)
        
        for vals in vals_list:
            # 1. Rellenar desde email si está disponible
            if not vals.get('lead_url') and vals.get('email_from'):
                domain = self._domain_from_email(vals['email_from'])
                if domain:
                    vals['lead_url'] = domain
                    
            # 2. Rellenar mediante búsqueda si sigue vacío y tiene nombre (bypasseado en importaciones)
            if not vals.get('lead_url') and vals.get('name') and not self.env.context.get('sin_enriquecer_al_crear') and not self.env.context.get('import_file'):
                name_str = vals['name'].strip()
                match = linkedin_pattern.match(name_str)
                if match:
                    company_name = match.group(1).strip()
                    found_url = self._search_company_url(company_name)
                    if found_url:
                        vals['lead_url'] = found_url

            # Todo lead nuevo nace pendiente de enriquecer, salvo que ya venga
            # con un estado puesto (p. ej. una copia de otro lead).
            vals.setdefault('enrichment_state', 'pending')

        leads = super().create(vals_list)
        leads._lanzar_enriquecimiento_al_crear()

        # Copiar automáticamente de enriquecimiento a los campos principales de contacto si están vacíos al crear
        for lead in leads:
            vals_to_write = {}
            if not lead.email_from and lead.enrichment_email_found:
                vals_to_write['email_from'] = lead.enrichment_email_found
            if not lead.phone:
                if lead.enrichment_phone_found:
                    vals_to_write['phone'] = lead.enrichment_phone_found
                elif lead.enrichment_mobile_found:
                    vals_to_write['phone'] = lead.enrichment_mobile_found
            if vals_to_write:
                super(CrmLeadEnrichment, lead).write(vals_to_write)

        return leads

    def write(self, vals):
        res = super().write(vals)
        
        # Copiar automáticamente de la pestaña de enriquecimiento a los campos principales
        # de contacto si están vacíos. Se procesa después del write original para atrapar
        # cualquier cambio en los datos de enriquecimiento o vaciado de campos principales.
        for lead in self:
            vals_to_write = {}
            if not lead.email_from and lead.enrichment_email_found:
                vals_to_write['email_from'] = lead.enrichment_email_found
            if not lead.phone:
                if lead.enrichment_phone_found:
                    vals_to_write['phone'] = lead.enrichment_phone_found
                elif lead.enrichment_mobile_found:
                    vals_to_write['phone'] = lead.enrichment_mobile_found
            if vals_to_write:
                super(CrmLeadEnrichment, lead).write(vals_to_write)
                
        return res

    def action_copy_enrichment_data(self):
        """Copia de forma masiva los datos de enriquecimiento a los campos principales de contacto si están vacíos."""
        modificados = 0
        for lead in self:
            vals = {}
            if not lead.email_from and lead.enrichment_email_found:
                vals['email_from'] = lead.enrichment_email_found
            if not lead.phone:
                if lead.enrichment_phone_found:
                    vals['phone'] = lead.enrichment_phone_found
                elif lead.enrichment_mobile_found:
                    vals['phone'] = lead.enrichment_mobile_found
            if vals:
                super(CrmLeadEnrichment, lead).write(vals)
                modificados += 1
                
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Datos copiados',
                'message': f'Se han actualizado {modificados} leads con sus datos de enriquecimiento.',
                'type': 'success',
                'next': {'type': 'ir.actions.client', 'tag': 'reload'},
            },
        }

    def _lanzar_enriquecimiento_al_crear(self):
        """Enriquece al crear, pero solo cuando es UN lead suelto.

        Si se están dando de alta varios a la vez (importación de CSV o pegado
        en la lista), no se lanza nada: se dejan pendientes y se avisa. Son
        procesos de 15-40 segundos por lead entre scraping y llamada a la IA;
        arrancarlos en cadena durante una importación de cien filas bloquearía
        la importación y saturaría las APIs. Además el usuario quiere revisar
        lo importado antes de gastar llamadas: enriquecer y cualificar sobre
        datos que aún no ha mirado no le sirve de nada.
        """
        if self.env.context.get('sin_enriquecer_al_crear'):
            return

        candidatos = self.filtered(lambda l: l._puede_enriquecerse())

        # Los que no tienen ni URL ni dominio de email no van a poder
        # enriquecerse nunca tal como están: marcarlos «pendientes» los dejaría
        # atascados para siempre en un filtro que el usuario no puede vaciar.
        sin_datos = self - candidatos
        if sin_datos:
            sin_datos.write({
                'enrichment_state': 'skipped',
                'enrichment_error': 'Sin URL de empresa ni email con dominio propio.',
            })
        if not candidatos:
            return

        # Importación de CSV: Odoo marca el contexto. Y más de uno a la vez es,
        # por definición, un lote.
        es_lote = len(self) > 1 or bool(self.env.context.get('import_file'))
        if es_lote:
            candidatos._avisar_lote_pendiente()
            return

        candidatos._enriquecer_en_segundo_plano()

    def _puede_enriquecerse(self):
        """¿Hay de dónde sacar información? Sin URL ni email no hay nada que raspar."""
        self.ensure_one()
        if (self.lead_url or '').strip():
            return True
        if bool(self._domain_from_email(self.email_from or '')):
            return True
        if (self.partner_name or '').strip():
            return True
        if self.name:
            linkedin_pattern = re.compile(r'^LinkedIn\s*[\u2014\u2013-]\s*(.+)$', re.IGNORECASE)
            if linkedin_pattern.match(self.name.strip()):
                return True
        return False

    def _avisar_lote_pendiente(self):
        """Avisa de que hay leads importados sin enriquecer.

        Odoo no puede abrir un diálogo por su cuenta al terminar una importación,
        así que el aviso va por notificación persistente y los leads quedan
        localizables con el filtro «Pendientes de enriquecer».
        """
        self._notify_bus(
            self.env.user.partner_id.id,
            f'{len(self)} leads importados sin enriquecer',
            'Antes de cualificarlos, filtra por «Pendientes de enriquecer» en la '
            'lista de leads, selecciónalos y usa Acciones → «Enriquecer leads». '
            'Te preguntará antes de empezar.',
            notif_type='warning', sticky=True,
        )

    def _enriquecer_en_segundo_plano(self):
        """Lanza el enriquecimiento sin bloquear al usuario.

        Se reutiliza la acción masiva, que ya monta su propio cursor e informa
        por el bus. Sin hilo aparte, guardar un lead tardaría medio minuto.
        """
        try:
            self.action_enrich_leads_bulk()
        except Exception as exc:  # noqa: BLE001
            _logger.warning('No se pudo lanzar el enriquecimiento automático '
                            'de %s: %s', self.ids, exc)

    # ── Estado del enriquecimiento ────────────────────────────────────────────
    # Sin esto no había forma de saber qué leads se habían quedado sin enriquecer:
    # los fallos se perdían en el log y el lead parecía normal, solo que vacío.
    enrichment_state = fields.Selection([
        ('pending', 'Pendiente'),
        ('done', 'Enriquecido'),
        ('error', 'Error'),
        ('skipped', 'Sin datos suficientes'),
    ], string='Estado del enriquecimiento', readonly=True, index=True, copy=False)
    # Casilla para ver de un vistazo si el lead está enriquecido, sin tener que
    # abrirlo. `enrichment_state` tiene cuatro valores y no cabe como columna
    # legible en una lista; esto es el sí/no que se mira de reojo.
    enrichment_ok = fields.Boolean(
        string='Enriquecido', compute='_compute_enrichment_ok', store=True,
        index=True, copy=False,
        help='Marcado cuando la IA ya ha analizado la web del lead. '
             'Si está sin marcar, mira el estado para saber si está pendiente, '
             'falló, o no hay datos con los que trabajar.',
    )

    @api.depends('enrichment_state')
    def _compute_enrichment_ok(self):
        for lead in self:
            lead.enrichment_ok = lead.enrichment_state == 'done'

    enrichment_error = fields.Char(
        string='Motivo del fallo', readonly=True, copy=False,
        help='Por qué no se pudo enriquecer. Se limpia al conseguirlo.',
    )

    # ── Resultados del enriquecimiento ────────────────────────────────────────
    enrichment_date = fields.Datetime(
        string='Última actualización IA', readonly=True,
    )
    enrichment_company_name = fields.Char(
        string='Nombre empresa', readonly=True,
    )
    enrichment_sector = fields.Char(
        string='Sector', readonly=True,
    )
    enrichment_vat = fields.Char(
        string='NIF / CIF', readonly=True,
    )
    enrichment_phone_found = fields.Char(
        string='Teléfono (web)', readonly=True,
    )
    enrichment_mobile_found = fields.Char(
        string='Móvil (web)', readonly=True,
    )
    enrichment_size = fields.Char(
        string='Tamaño empresa', readonly=True,
    )
    enrichment_address = fields.Char(
        string='Dirección', readonly=True,
    )
    enrichment_country = fields.Char(
        string='País', readonly=True,
    )
    enrichment_probability_ai = fields.Integer(
        string='% Probabilidad (IA)', readonly=True,
    )
    # ── Contacto encontrado ───────────────────────────────────────────────────
    enrichment_email_found = fields.Char(
        string='Email (web)', readonly=True,
    )
    # ── Redes sociales ────────────────────────────────────────────────────────
    enrichment_linkedin = fields.Char(
        string='LinkedIn', readonly=True,
    )
    enrichment_meta = fields.Char(
        string='Facebook / Instagram', readonly=True,
    )
    enrichment_twitter = fields.Char(
        string='Twitter / X', readonly=True,
    )
    # ── Análisis IA ───────────────────────────────────────────────────────────
    enrichment_summary = fields.Html(
        string='Resumen empresa', readonly=True, sanitize=True,
    )
    enrichment_pain_points = fields.Html(
        string='Puntos de dolor que puedo resolver', readonly=True, sanitize=True,
    )
    enrichment_email_advice = fields.Html(
        string='Consejo para el email', readonly=True, sanitize=True,
    )
    enrichment_extra = fields.Html(
        string='Información adicional', readonly=True, sanitize=True,
    )

    # ── Extracción del dominio ────────────────────────────────────────────────

    @staticmethod
    def _domain_from_email(email_str):
        m = re.search(r'@([\w\-\.]+)', email_str or '')
        if not m:
            return ''
        domain = m.group(1).lower()
        generic = {'gmail.com', 'hotmail.com', 'yahoo.com', 'outlook.com',
                   'icloud.com', 'live.com', 'me.com', 'aol.com'}
        return '' if domain in generic else f'https://{domain}'

    # ── Acción principal: enriquecer ──────────────────────────────────────────

    def _notify_bus(self, partner_id, title, message, notif_type='info', sticky=False):
        """Envía una notificación en tiempo real al usuario vía bus."""
        try:
            self.env['bus.bus']._sendone(
                self.env['res.partner'].browse(partner_id),
                'simple_notification',
                {'title': title, 'message': message, 'type': notif_type, 'sticky': sticky},
            )
        except Exception:
            pass

    def action_enrich_leads_bulk(self):
        """Acción masiva: lanza el enriquecimiento en un hilo de fondo para
        que el navegador quede libre y reciba las notificaciones en tiempo real."""
        if not self:
            raise UserError('No hay leads seleccionados.')

        lead_ids   = self.ids
        total      = len(lead_ids)
        dbname     = self.env.cr.dbname
        uid        = self.env.uid
        partner_id = self.env.user.partner_id.id

        def _background():
            """Hilo que procesa los leads uno a uno con su propio cursor."""
            _logger.info('Bulk enrichment thread started: %d leads, uid=%s, partner=%s', total, uid, partner_id)
            ok = 0
            errors = []

            def _send_notify(cr_inner, env_inner, title, message, notif_type='info', sticky=False):
                """Envía notificación bus y hace commit; loguea cualquier error."""
                try:
                    env_inner['bus.bus']._sendone(
                        env_inner['res.partner'].browse(partner_id),
                        'simple_notification',
                        {'title': title, 'message': message,
                         'type': notif_type, 'sticky': sticky},
                    )
                    _logger.info('Bus notify sent: [%s] %s', title, message)
                except Exception as exc:
                    _logger.warning('Bus _sendone error: %s', exc)
                try:
                    cr_inner.commit()
                    _logger.info('Bus notify committed')
                except Exception as exc:
                    _logger.warning('Bus notify commit error: %s', exc)

            try:
                with Registry(dbname).cursor() as cr:
                    env = api.Environment(cr, uid, {})
                    _logger.info('Bulk enrichment: cursor and env created OK')

                    _send_notify(cr, env,
                        '🔍 Enriquecimiento iniciado',
                        f'Procesando {total} lead(s) en segundo plano…',
                    )

                    for i, lead_id in enumerate(lead_ids, 1):
                        lead = env['crm.lead'].browse(lead_id)
                        label = lead.partner_name or lead.name or f'Lead #{lead_id}'
                        _logger.info('Bulk enrichment: processing lead %d/%d id=%s label=%s', i, total, lead_id, label)

                        _send_notify(cr, env, f'⏳ {i}/{total} Analizando…', label)

                        try:
                            lead.action_enrich_lead()
                            ok += 1
                            _logger.info('Bulk enrichment: lead %s OK', lead_id)
                            _send_notify(cr, env, f'✅ {i}/{total} Enriquecido', label, notif_type='success')
                            cr.commit()
                        except Exception as exc:
                            err_msg = str(exc)[:150]
                            errors.append(f'{label}: {err_msg}')
                            _logger.warning('Bulk enrichment error lead %s: %s', lead_id, exc)
                            try:
                                cr.rollback()
                            except Exception:
                                pass
                            _send_notify(cr, env,
                                f'⚠️ {i}/{total} Error',
                                f'{label}: {err_msg}',
                                notif_type='warning', sticky=True,
                            )

                    # Resumen final
                    if errors:
                        resumen = f'{ok} OK · {len(errors)} con errores:\n' + '\n'.join(errors[:5])
                        _send_notify(cr, env, '🏁 Finalizado', resumen, notif_type='warning', sticky=True)
                    else:
                        _send_notify(cr, env,
                            '🏁 Finalizado',
                            f'{ok} de {total} lead(s) enriquecidos correctamente.',
                            notif_type='success', sticky=True,
                        )
                    _logger.info('Bulk enrichment thread finished: ok=%d errors=%d', ok, len(errors))

            except Exception as exc:
                _logger.exception('Bulk enrichment thread crashed: %s', exc)

        t = threading.Thread(target=_background, daemon=True)
        t.start()

        # Responder al navegador de inmediato para que quede libre
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': '🔍 Enriquecimiento iniciado',
                'message': f'Procesando {total} lead(s) en segundo plano. Recibirás una notificación por cada uno.',
                'type': 'info',
                'sticky': False,
            },
        }

    def action_enrich_lead(self):
        """Enriquece el lead dejando constancia de cómo acabó.

        Antes, si fallaba, la excepción subía y el lead se quedaba exactamente
        igual que antes: sin datos y sin ninguna señal de que se hubiera
        intentado. Quien lo mirara después no podía distinguir «nunca se
        intentó» de «se intentó y falló», y el motivo solo estaba en el log.
        """
        self.ensure_one()
        try:
            resultado = self._enrich_lead_inner()
        except Exception as exc:
            self.sudo().write({
                'enrichment_state': 'error',
                'enrichment_error': str(exc)[:250],
            })
            raise
        self.sudo().write({'enrichment_state': 'done', 'enrichment_error': False})
        return resultado

    def _enrich_lead_inner(self):
        self.ensure_one()

        icp = self.env['ir.config_parameter'].sudo()

        def cfg(key, default=''):
            return icp.get_param(f'{_P}{key}', default)

        anthropic_key = cfg('ai_anthropic_key')
        gemini_key = cfg('ai_gemini_key')
        openai_key = cfg('ai_openai_key')

        if not gemini_key:
            gemini_key = icp.get_param('ia_agents_treasury_control.gemini_api_key', '') or icp.get_param('mcp_agents_financial_and_treasury_control.gemini_api_key', '')

        preferred = cfg('ai_preferred', 'anthropic')

        if not any([anthropic_key, gemini_key, openai_key]):
            raise UserError(
                'No hay ninguna clave API de IA configurada.\n'
                'Ve a Ajustes → CRM - IA y añade al menos una clave.'
            )

        my_desc = cfg('my_company_description')
        my_audience = cfg('my_company_target_audience')
        my_pain = cfg('my_company_pain_points')
        my_url1 = cfg('my_company_url_1')
        my_url2 = cfg('my_company_url_2')
        my_url3 = cfg('my_company_url_3')

        # ── URL del lead ──────────────────────────────────────────────────────
        lead_url = (self.lead_url or '').strip()
        if not lead_url:
            domain = self._domain_from_email(self.email_from or '')
            if domain:
                lead_url = domain
                self.lead_url = domain

        # Si sigue vacío, buscamos el nombre de la empresa (priorizando el campo Company/partner_name o el nombre del lead)
        if not lead_url:
            company_name = False
            if self.partner_name:
                company_name = self.partner_name.strip()
            
            if not company_name and self.name:
                linkedin_pattern = re.compile(r'^LinkedIn\s*[\u2014\u2013-]\s*(.+)$', re.IGNORECASE)
                match = linkedin_pattern.match(self.name.strip())
                if match:
                    company_name = match.group(1).strip()
                    
            if company_name:
                # 1. Intentamos buscarla primero por DuckDuckGo
                try:
                    found_url = self._search_company_url(company_name)
                    if found_url:
                        lead_url = found_url
                        self.lead_url = found_url
                except Exception:
                    pass
                
                # 2. Si DDG falla/timeoutea o es bloqueado, usamos la IA como un fallback de altísima fiabilidad!
                if not lead_url:
                    ai_prompt = f"""
                    A continuación se presenta el nombre de una empresa española o internacional. Suponiendo que es una empresa real, ¿cuál es con alta probabilidad el dominio oficial de su sitio web? (por ejemplo, para 'Mercadona' es 'mercadona.es', para 'Telefónica' es 'telefonica.com', para 'Cibertelia' es 'cibertelia.com').
                    
                    Nombre de la empresa: {company_name}
                    
                    Responde ÚNICAMENTE con la URL raíz oficial (ej: 'https://cibertelia.com') o 'not_found' si no puedes determinarla con un nivel alto de confianza. No agregues explicaciones ni comentarios.
                    """
                    try:
                        # Llama a la IA para adivinar/predecir la URL oficial
                        ai_url = _call_ai(anthropic_key, gemini_key, openai_key, preferred, ai_prompt).strip()
                        # Extraer URL si la IA devolvió algo válido
                        if 'http' in ai_url or '.' in ai_url:
                            # Limpiar posibles comillas o espacios de la IA
                            url_match = re.search(r'(https?://[^\s\'"]+|[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})', ai_url)
                            if url_match:
                                cleaned_url = url_match.group(1)
                                if not cleaned_url.startswith('http'):
                                    cleaned_url = f"https://{cleaned_url}"
                                lead_url = cleaned_url
                                self.lead_url = cleaned_url
                                _logger.info("Company URL for %s predicted by AI: %s", company_name, lead_url)
                    except Exception as ai_exc:
                        _logger.warning("Failed to predict company URL using AI: %s", ai_exc)

        if not lead_url:
            raise UserError(
                'El lead no tiene URL ni email con dominio empresarial.\n'
                'Rellena el campo "URL empresa lead" antes de enriquecer.'
            )

        # ── Scraping del lead (portada + contacto + sobre nosotros + aviso legal)
        lead_data = _scrape_site(lead_url)
        social = lead_data.get('social', {})
        tels = lead_data.get('tels', [])
        emails = lead_data.get('emails', [])

        # Bloque de datos pre-extraídos (fiables — vienen directamente del HTML)
        preextracted_lines = []
        if tels:
            preextracted_lines.append(f"Teléfonos (tel: links): {' / '.join(tels)}")
        if emails:
            preextracted_lines.append(f"Emails (mailto: links): {' / '.join(emails)}")
        if social.get('linkedin'):
            preextracted_lines.append(f"LinkedIn: {social['linkedin']}")
        if social.get('twitter'):
            preextracted_lines.append(f"Twitter/X: {social['twitter']}")
        if social.get('meta'):
            preextracted_lines.append(f"Facebook/Instagram: {social['meta']}")
        preextracted_block = '\n'.join(preextracted_lines) or 'Ninguno detectado automáticamente.'

        lead_text = (
            f"Título: {lead_data['title']}\n"
            f"Descripción meta: {lead_data['description']}\n\n"
            f"{lead_data['text']}"
        ).strip()

        # ── Scraping de mi empresa ────────────────────────────────────────────
        my_web_context = ''
        for url in filter(None, [my_url1, my_url2, my_url3]):
            d = _scrape_url(url, head=1000, tail=500)
            if d['text']:
                my_web_context += f"\n[{url}] {d['title']} — {d['text'][:1500]}"

        my_context_block = '\n'.join(filter(None, [
            f"Descripción: {my_desc}" if my_desc else '',
            f"Público objetivo: {my_audience}" if my_audience else '',
            f"Problemas que resuelvo: {my_pain}" if my_pain else '',
            f"Web de mi empresa: {my_web_context}" if my_web_context else '',
        ]))

        prompt = f"""Eres un experto en inteligencia comercial B2B. Tu tarea es extraer datos de contacto y generar análisis comercial a partir del contenido de una web.

=== DATOS PRE-EXTRAÍDOS DIRECTAMENTE DEL HTML (úsalos como fuente de verdad) ===
{preextracted_block}

=== MI EMPRESA ===
{my_context_block or 'No especificada.'}

=== CONTENIDO DE LA WEB DEL LEAD (URL: {lead_url}) ===
El contenido incluye la portada, página de contacto, "sobre nosotros" y aviso legal (si existen).
El aviso legal español contiene por ley el nombre completo, CIF/NIF y domicilio social.

{lead_text or 'No se pudo obtener contenido de la web.'}

=== INSTRUCCIONES DE EXTRACCIÓN ===

DATOS DE CONTACTO — extrae con máxima precisión:
1. Teléfonos: están en los "Datos pre-extraídos" como tel: links. También pueden aparecer en el pie de página, sección de contacto o aviso legal.
2. Dirección y NIF/CIF: busca en el aviso legal (sección "DATOS DE LA ENTIDAD" o similar) y en la página de contacto.
3. Redes sociales: usa los valores pre-extraídos. Son URLs exactas del HTML.
4. Email: puede aparecer en mailto: links o en texto de la página de contacto/aviso legal.

Genera este JSON EXACTO sin texto adicional fuera de él:

{{
  "company_name": "denominación social completa de la empresa",
  "sector": "business sector/industry in English (e.g. Fintech, Consulting, Agency, Energy, Renewables, Software, AI Software, Marketing Agency, Retail, Proptech, Healthtech, Edtech, etc. — must be in English representing most startups and SMEs)",
  "vat": "NIF o CIF (formato: B12345678 o similar) — busca en aviso legal",
  "phone": "teléfono(s) fijo(s) — usa los pre-extraídos, separa varios con ' / '",
  "mobile": "móvil(es) — usa los pre-extraídos, separa varios con ' / '",
  "email": "email de contacto principal si aparece",
  "address": "domicilio social completo — busca en aviso legal o página de contacto",
  "country": "país principal",
  "size": "micro / pequeña / mediana / grande",
  "linkedin": "URL LinkedIn pre-extraída o encontrada en el texto, si no cadena vacía",
  "twitter": "URL Twitter/X pre-extraída o encontrada en el texto, si no cadena vacía",
  "meta": "URL Facebook o Instagram pre-extraída o encontrada en el texto, si no cadena vacía",
  "success_probability": 75,
  "summary": "<p>3-5 frases sobre a qué se dedica, modelo de negocio y contexto.</p>",
  "pain_points": "<ul><li><strong>Pain 1</strong>: dolor que YO puedo resolver</li></ul>",
  "email_advice": "<p>Consejo para el primer email: tono, problema a atacar, gancho.</p>",
  "extra": "<p>Info adicional: tecnologías, sectores, noticias, reconocimientos, etc.</p>"
}}

REGLAS:
- Los datos pre-extraídos (tel:, mailto:, RRSS) son fiables — úsalos directamente sin modificar.
- Para phone vs mobile: números con 6xx/7xx son móviles (España), 9xx son fijos. Adapta según el país.
- Si hay varios teléfonos del mismo tipo, inclúyelos todos separados por ' / '.
- success_probability: 0-100 según encaje entre mi empresa y el lead.
- pain_points: solo los que YO puedo resolver. Mínimo 2, máximo 6.
- Si no encuentras un dato, deja el campo con cadena vacía "".
- Responde SOLO el JSON. Sin texto antes ni después. Sin bloques de código markdown.
"""

        raw = _call_ai(anthropic_key, gemini_key, openai_key, preferred, prompt)
        data = _extract_json(raw)

        if not data:
            raise UserError(
                'La IA no devolvió un JSON válido. Respuesta recibida:\n\n'
                + raw[:500]
            )

        # ── Escribir campos ───────────────────────────────────────────────────
        def _get_val(field_name, ai_value):
            current_val = getattr(self, field_name, False)
            if current_val:
                return current_val
            return ai_value

        vals = {
            'enrichment_date': fields.Datetime.now(),
            'enrichment_company_name': _get_val('enrichment_company_name', data.get('company_name', '')),
            'enrichment_sector': _get_val('enrichment_sector', data.get('sector', '')),
            'enrichment_vat': _get_val('enrichment_vat', data.get('vat', '')),
            'enrichment_phone_found': _get_val('enrichment_phone_found', data.get('phone', '')),
            'enrichment_mobile_found': _get_val('enrichment_mobile_found', data.get('mobile', '')),
            'enrichment_email_found': _get_val('enrichment_email_found', data.get('email', '')),
            'enrichment_address': _get_val('enrichment_address', data.get('address', '')),
            'enrichment_country': _get_val('enrichment_country', data.get('country', '')),
            'enrichment_size': _get_val('enrichment_size', data.get('size', '')),
            'enrichment_linkedin': _get_val('enrichment_linkedin', data.get('linkedin', '')),
            'enrichment_twitter': _get_val('enrichment_twitter', data.get('twitter', '')),
            'enrichment_meta': _get_val('enrichment_meta', data.get('meta', '')),
            'enrichment_summary': _get_val('enrichment_summary', data.get('summary', '')),
            'enrichment_pain_points': _get_val('enrichment_pain_points', data.get('pain_points', '')),
            'enrichment_email_advice': _get_val('enrichment_email_advice', data.get('email_advice', '')),
            'enrichment_extra': _get_val('enrichment_extra', data.get('extra', '')),
        }
        # Conversión robusta: la IA puede devolver int, float o string
        raw_prob = data.get('success_probability')
        _logger.info('Lead %s enrichment: success_probability raw=%r type=%s', self.id, raw_prob, type(raw_prob).__name__)
        p = None
        try:
            p = int(float(str(raw_prob)))
        except (TypeError, ValueError):
            _logger.warning('Lead %s: no se pudo convertir success_probability=%r', self.id, raw_prob)

        if p is not None:
            vals['enrichment_probability_ai'] = p
            vals['probability'] = float(p)

        # Copiar a campos estándar del lead si están vacíos
        if data.get('company_name') and not self.partner_name:
            vals['partner_name'] = data['company_name']
        if data.get('phone') and not self.phone:
            vals['phone'] = data['phone']
        # OJO: `crm.lead` NO tiene campo `mobile` en Odoo 19 (sí lo tiene
        # res.partner). Escribirlo reventaba el enriquecimiento entero DESPUÉS
        # de haber scrapeado la web y llamado a la IA, así que se perdía todo el
        # trabajo y el lead se quedaba sin enriquecer. El móvil encontrado se
        # guarda en `enrichment_mobile_found`, y solo se promociona a `phone`
        # si el lead no tenía ningún teléfono.
        if data.get('mobile') and not self.phone and not vals.get('phone'):
            vals['phone'] = data['mobile']
        if data.get('email') and not self.email_from:
            vals['email_from'] = data['email']
        if data.get('address') and not self.street:
            vals['street'] = data['address']
        if data.get('country'):
            country = self.env['res.country'].search(
                [('name', 'ilike', data['country'])], limit=1
            )
            if country and not self.country_id:
                vals['country_id'] = country.id

        self.write(vals)

        # Estrellas: write separado para evitar que campos computados lo sobreescriban
        if p is not None:
            if p >= 80:
                stars = '3'
            elif p >= 68:
                stars = '2'
            elif p >= 60:
                stars = '1'
            else:
                stars = '0'
            self.write({'priority': stars})
            _logger.info('Lead %s → probabilidad %s%% → priority %s estrellas', self.id, p, stars)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Lead enriquecido',
                'message': (
                    f"Análisis completado para {data.get('company_name') or lead_url}. "
                    f"Probabilidad: {data.get('success_probability', '?')}%"
                ),
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.client', 'tag': 'reload'},
            },
        }
