import base64
import hashlib
import logging
import re
from html import escape

from odoo import api, fields, models
from odoo.exceptions import UserError

# Se reutiliza el conmutador de proveedores del enriquecimiento (Anthropic →
# Gemini → OpenAI): si uno falla, prueba el siguiente. No hay motivo para
# tener dos implementaciones de lo mismo en el mismo módulo.
from .crm_lead_enrichment import _call_ai

_logger = logging.getLogger(__name__)

# Marcadores que se sustituyen al aplicar la plantilla a un lead concreto.
# Se documentan en la propia vista para que el usuario los tenga a mano.
def _nombre_pila(lead):
    """Solo el nombre de pila, para saludar.

    Tres cuidados:
    · Si el contacto está enlazado a un partner que es EMPRESA, su `name` es el
      nombre de la empresa — saludar con él daría «Hola, Booster MindTech».
      Por eso manda `contact_name` salvo que el partner sea una persona.
    · Hay contactos guardados como «Apellidos, Nombre»: ahí el nombre va detrás
      de la coma.
    · Los nombres importados en MAYÚSCULAS se pasan a capitalizado; gritar el
      nombre en el saludo es lo primero que delata un envío automatizado.
    """
    bruto = ''
    partner = lead.partner_id if lead else None
    if partner and not partner.is_company:
        bruto = partner.name or ''
    bruto = (bruto or (lead.contact_name if lead else '') or '').strip()
    if not bruto:
        return ''

    if ',' in bruto:
        bruto = bruto.split(',', 1)[1].strip() or bruto.split(',', 1)[0].strip()

    # Tratamientos y titulaciones que no son el nombre
    descartar = {'sr', 'sra', 'sr.', 'sra.', 'don', 'doña', 'dr', 'dr.', 'dra',
                 'dra.', 'd.', 'dª', 'mr', 'mrs', 'ms', 'ing', 'lic'}
    partes = [t for t in bruto.split() if t.lower().strip('.') not in descartar]
    if not partes:
        return ''

    nombre = partes[0]
    # «Mª», «Mᵃ», «M.» sueltos no son un nombre: se coge también el siguiente.
    if len(partes) > 1 and (len(nombre.rstrip('.')) <= 2 or nombre.lower().rstrip('.') in
                            ('ma', 'mª', 'm', 'jm', 'jc')):
        nombre = f'{nombre} {partes[1]}'

    if nombre.isupper():
        nombre = nombre.title()
    return nombre


def _val(valor, reserva=''):
    """Texto limpio de un campo, o su reserva si viene vacío."""
    if valor in (None, False):
        return reserva
    texto = str(valor).strip()
    return texto or reserva


# Marcadores disponibles en el asunto, el cuerpo y el recuadro.
# La reserva importa: un hueco vacío deja frases rotas («empresas del sector  en
# ») y eso canta más que generalizar.
PLACEHOLDERS = {
    '{nombre}':      (lambda lead: _val(_nombre_pila(lead), 'hola'),
                      'Nombre de pila (solo el nombre, sin apellidos)'),
    '{nombre_completo}': (lambda lead: _val(
                          (lead.partner_id.name if lead.partner_id and not lead.partner_id.is_company else '')
                          or lead.contact_name),
                      'Nombre y apellidos'),
    '{empresa}':     (lambda lead: _val(lead.partner_name or (lead.partner_id.parent_id.name if lead.partner_id else ''), 'vuestra empresa'),
                      'Nombre de la empresa'),
    '{comercial}':   (lambda lead: _val(lead.user_id.name), 'Comercial asignado'),
    '{oportunidad}': (lambda lead: _val(lead.name), 'Nombre del lead/oportunidad'),
    '{sector}':      (lambda lead: _val(lead.enrichment_sector, 'vuestro sector'),
                      'Sector (del enriquecimiento)'),
    '{pais}':        (lambda lead: _val(lead.country_id.name or lead.enrichment_country),
                      'País'),
    '{ciudad}':      (lambda lead: _val(lead.city), 'Ciudad'),
    '{cargo}':       (lambda lead: _val(lead.function), 'Cargo del contacto'),
    '{web}':         (lambda lead: _val(lead.lead_url), 'Web de la empresa'),
    '{email}':       (lambda lead: _val(lead.email_from), 'Email del contacto'),
    '{telefono}':    (lambda lead: _val(lead.phone), 'Teléfono'),
    '{tamano}':      (lambda lead: _val(lead.enrichment_size), 'Tamaño de la empresa'),
    # La URL real la aporta `_url_baja`, que este módulo deja vacía: la lista de
    # bajas RGPD vive en el módulo de campañas y es él quien la rellena.
    '{baja}':        (lambda lead: lead._url_baja() if hasattr(lead, '_url_baja') else '',
                      'Enlace para darse de baja'),
}

# Valores de ejemplo para la vista previa sin lead.
DEMO = {
    '{nombre}': 'Jose', '{nombre_completo}': 'Jose Martínez Ruiz',
    '{empresa}': 'Empresa Ejemplo',
    '{oportunidad}': 'Oportunidad de ejemplo',
    '{sector}': 'fabricación cerámica', '{pais}': 'España',
    '{ciudad}': 'Castellón', '{cargo}': 'Director General',
    '{web}': 'https://ejemplo.com', '{email}': 'jose@ejemplo.com',
    '{telefono}': '+34 964 000 000', '{tamano}': '25 empleados',
    '{baja}': 'https://ejemplo.odoo.com/marketing/baja/EJEMPLO',
}

# Bloques de instrucción para la IA dentro del cuerpo.
RE_PROMPT = re.compile(r'\[PROMPT\](.*?)\[/PROMPT\]', re.DOTALL | re.IGNORECASE)


class CrmEmailTemplate(models.Model):
    """Plantilla visual de email para el historial de comunicaciones.

    El objetivo es no tener que tocar HTML: el usuario rellena campos sueltos
    (logo, colores, título, texto, recuadro) y el modelo genera un HTML de
    email limpio y compatible con clientes de correo.

    Por qué el HTML se genera aquí y no se edita a mano:
    el widget `html` de Odoo 19 se vuelve de SOLO LECTURA en cuanto el valor
    mete algo en <head> (ver computeContainsComplexHTML en
    addons/html_editor/static/src/fields/html_field.js). Pegar un email de
    Gmail arrastra <meta>/<style> y bloquea el editor, que es justo lo que
    obligaba a editar en código.
    """

    _name = 'crm.email.template'
    _description = 'Plantilla de email (CRM Comunicaciones)'
    _order = 'sequence, name'

    name = fields.Char('Nombre de la plantilla', required=True)
    sequence = fields.Integer('Orden', default=10)
    active = fields.Boolean('Activa', default=True)

    # ── Cabecera ─────────────────────────────────────────────────────────
    logo = fields.Image('Logo', max_width=600, max_height=200,
                        help='Se muestra centrado sobre el banner. Formato recomendado: PNG con fondo transparente.')
    banner_color = fields.Char('Color del banner', default='#075E54',
                               help='Color de fondo de la franja superior.')
    show_banner = fields.Boolean('Mostrar banner', default=True)

    # ── Cuerpo ───────────────────────────────────────────────────────────
    heading = fields.Char('Título', default='Hola, {nombre}')
    body_text = fields.Text(
        'Texto del mensaje',
        help='Texto plano. Los saltos de línea se respetan; no hace falta escribir HTML.')
    text_color = fields.Char('Color de la fuente', default='#111b21')
    link_color = fields.Char('Color de los enlaces', default='#128C7E')

    # ── Recuadro destacado ───────────────────────────────────────────────
    show_box = fields.Boolean('Añadir recuadro destacado')
    box_text = fields.Text('Texto del recuadro')
    box_color = fields.Char('Color del recuadro', default='#d9fdd3')
    box_border_color = fields.Char('Color del borde', default='#25D366')

    # ── Pie ──────────────────────────────────────────────────────────────
    footer_text = fields.Text('Texto del pie')
    footer_color = fields.Char('Color del pie', default='#667781')

    preview_html = fields.Html(
        'Vista previa', compute='_compute_preview_html',
        sanitize=False, readonly=True, store=False)

    # ── Render ───────────────────────────────────────────────────────────

    def _substitute(self, text, lead=None):
        """Sustituye los marcadores. Sin lead usa valores de ejemplo, para que
        la vista previa no salga con llaves sueltas."""
        if not text:
            return ''
        for key, (getter, _etiqueta) in PLACEHOLDERS.items():
            if lead:
                value = getter(lead)
            elif key == '{comercial}':
                value = self.env.user.name or ''
            else:
                value = DEMO.get(key, '')
            text = text.replace(key, value or '')
        return text

    # ── Bloques [PROMPT] … [/PROMPT] ──────────────────────────────────────

    def _resolver_prompts(self, text, lead=None):
        """Cambia cada bloque por lo que escriba la IA para ESTE lead.

        Se llama a la IA una vez por bloque. Si falla, se propaga: es preferible
        no enviar a enviar un email con la instrucción interna dentro, que es
        exactamente lo que veía el usuario antes de esto.
        """
        self.ensure_one()
        if not text or not RE_PROMPT.search(text):
            return text

        Enrich = self.env['crm.lead']
        icp = self.env['ir.config_parameter'].sudo()

        def cfg(clave, defecto=''):
            return icp.get_param(f'crm_marketing_and_comunications.{clave}', defecto)

        gemini = cfg('ai_gemini_key') or icp.get_param(
            'ia_agents_treasury_control.gemini_api_key', '')
        claves = (cfg('ai_anthropic_key'), gemini, cfg('ai_openai_key'),
                  cfg('ai_preferred', 'anthropic'))
        if not any(claves[:3]):
            raise UserError(
                'Hay bloques [PROMPT] en la plantilla pero no hay ninguna clave '
                'de IA configurada.\nAjustes → CRM - IA.'
            )

        contexto = self._contexto_lead(lead)

        def _uno(match):
            instruccion = (match.group(1) or '').strip()
            if not instruccion:
                return ''
            prompt = (
                'Eres quien redacta un email comercial B2B en español.\n\n'
                f'=== DATOS DEL DESTINATARIO ===\n{contexto}\n\n'
                f'=== LO QUE TIENES QUE ESCRIBIR ===\n{instruccion}\n\n'
                '=== REGLAS ===\n'
                '- Devuelve SOLO el texto pedido: sin preámbulos, sin comillas, '
                'sin explicaciones.\n'
                '- Va incrustado en un email ya redactado: no pongas saludo ni '
                'despedida salvo que la instrucción lo pida.\n'
                '- No inventes datos del destinatario. Si falta información, '
                'escribe de forma más general en vez de suponer.\n'
                '- Para resaltar puedes usar **negrita** y *cursiva* con '
                'asteriscos; se convierten a formato real en el email. Úsalo con '
                'moderación: un email lleno de negritas se lee como publicidad.'
            )
            texto = _call_ai(*claves, prompt)
            return (texto or '').strip()

        return RE_PROMPT.sub(_uno, text)

    def _contexto_lead(self, lead):
        """Lo que la IA sabe del destinatario. Solo datos que existen."""
        if not lead:
            return ('(sin lead concreto: es una vista previa de ejemplo, '
                    'inventa un caso plausible y genérico)')
        campos = [
            ('Empresa', lead.partner_name or lead.name),
            ('Contacto', lead.contact_name),
            ('Cargo', lead.function),
            ('Sector', lead.enrichment_sector),
            ('Tamaño', lead.enrichment_size),
            ('País', lead.country_id.name or lead.enrichment_country),
            ('Web', lead.lead_url),
            ('Resumen de la empresa', lead.enrichment_summary),
            ('Puntos de dolor detectados', lead.enrichment_pain_points),
            ('Consejo de enfoque', lead.enrichment_email_advice),
        ]
        lineas = []
        for etiqueta, valor in campos:
            if not valor:
                continue
            texto = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', str(valor))).strip()
            if texto:
                lineas.append(f'{etiqueta}: {texto[:600]}')
        return '\n'.join(lineas) or '(el lead no tiene datos de enriquecimiento)'

    def _text_to_html(self, text):
        """Texto plano → HTML seguro, respetando párrafos, saltos y marcado.

        El orden es deliberado: **primero se escapa** y solo después se aplica el
        marcado. Al revés, cualquiera que escribiera HTML en el cuerpo lo vería
        ejecutado en el email; así lo único que genera etiquetas es el marcado
        que reconocemos aquí.
        """
        if not text:
            return ''
        blocks = [b for b in re.split(r'\n\s*\n', text.strip()) if b.strip()]
        return ''.join(
            '<p style="margin:0 0 14px 0">%s</p>'
            % self._aplicar_marcado(escape(b.strip())).replace('\n', '<br/>')
            for b in blocks
        )

    @staticmethod
    def _aplicar_marcado(texto):
        """**negrita**, *cursiva*, _cursiva_ → <strong> y <em>.

        Se usan estilos inline y no <b>/<i> a secas porque algunos clientes de
        correo (Outlook con Word como motor) ignoran las etiquetas semánticas si
        heredan un estilo de la tabla contenedora.

        Los delimitadores tienen que pegar al texto: `**así**` sí, `** así **`
        no. Eso evita que una línea de lista («* Primer punto») se convierta en
        cursiva a medias, y que un asterisco suelto se coma medio párrafo.
        """
        if not texto:
            return texto
        # Negrita primero: si no, el ** lo capturaría la regla de cursiva.
        # `(?:[^\n*]|\*(?!\*))+?` admite asteriscos sueltos dentro, para que
        # **negrita con *cursiva* dentro** funcione; lo que no admite es otro
        # `**`, que cerraría la negrita.
        texto = re.sub(r'\*\*(?=\S)((?:[^\n*]|\*(?!\*))+?)(?<=\S)\*\*',
                       r'<strong style="font-weight:600">\1</strong>', texto)
        texto = re.sub(r'(?<![\w*])\*(?=\S)([^\n*]+?)(?<=\S)\*(?![\w*])',
                       r'<em>\1</em>', texto)
        texto = re.sub(r'(?<![\w_])_(?=\S)([^\n_]+?)(?<=\S)_(?![\w_])',
                       r'<em>\1</em>', texto)

        # Enlaces con texto: [pincha aquí](https://…) y también
        # [date de baja]({baja}) o ({{UNSUBSCRIBE_URL}}), porque en modo crudo —
        # al copiar la plantilla a una campaña — la URL todavía es un marcador.
        # Sin aceptarlos, el enlace se copiaba como texto literal y el
        # destinatario recibía «[haz click aquí]({{UNSUBSCRIBE_URL}})» en vez de
        # un enlace de baja: un incumplimiento de RGPD a la vista de todos.
        texto = re.sub(
            r'\[([^\]\n]+)\]\((https?://[^\s)]+|\{{1,2}[^}\s)]+\}{1,2})\)',
            r'<a href="\2" style="color:inherit">\1</a>', texto)

        # URL suelta → enlace. Se excluyen las que ya están dentro de un href,
        # para no anidar etiquetas al pasar dos veces por aquí.
        texto = re.sub(
            r'(?<!href=")(?<!>)(https?://[^\s<]+)(?![^<]*</a>)',
            r'<a href="\1" style="color:inherit">\1</a>', texto)
        return texto

    def _logo_src(self):
        """URL pública y absoluta del logo.

        NO se usa data URI a propósito: **Gmail elimina las imágenes
        `src="data:image/...;base64,..."`** y el logo saldría roto para todos
        sus destinatarios (Outlook sí las muestra). Además, un logo de 46 KB se
        convierte en ~62 KB de base64 dentro del cuerpo, y Gmail recorta los
        mensajes a partir de ~102 KB.

        Requiere marcar el adjunto como público, porque quien abre el email no
        tiene sesión en Odoo.
        """
        self.ensure_one()
        if not self.logo:
            return ''
        att = self.env['ir.attachment'].sudo().search([
            ('res_model', '=', self._name),
            ('res_field', '=', 'logo'),
            ('res_id', '=', self.id),
        ], limit=1)
        if not att:
            return ''
        if not att.public:
            att.public = True
        base = self.env['ir.config_parameter'].sudo().get_param('web.base.url') or ''
        return f'{base.rstrip("/")}/web/image/{att.id}'

    def _preparar(self, texto, lead=None):
        """Placeholders + bloques [PROMPT], en ese orden.

        Primero los marcadores, para que una instrucción pueda decir
        «un reto típico de {sector}» y la IA reciba el sector de verdad.
        """
        self.ensure_one()
        if self.env.context.get('_crudo'):
            return texto or ''
        texto = self._substitute(texto, lead)
        if not RE_PROMPT.search(texto or ''):
            return texto
        if self.env.context.get('_resolver_ia', True):
            return self._resolver_prompts(texto, lead)
        # Vista previa barata: se enseña dónde está el bloque sin gastar IA.
        return RE_PROMPT.sub(
            lambda m: ('［ Aquí escribirá la IA: '
                       + re.sub(r'\s+', ' ', (m.group(1) or '').strip())[:160]
                       + '… ］'),
            texto)

    def render_html(self, lead=None, resolver_ia=True, crudo=False):
        """HTML final del email. Todo en estilos inline y con tablas, que es
        lo único que interpretan de forma fiable Outlook y Gmail.

        `resolver_ia=False` deja los bloques [PROMPT] marcados en vez de
        ejecutarlos: lo usa la vista previa automática del formulario, que se
        recalcula con cada tecla y no puede estar llamando a la IA.

        `crudo=True` NO toca el texto: ni sustituye marcadores ni marca los
        bloques. Es para copiar la plantilla a otro sitio, donde lo que interesa
        es conservar `{nombre}` y `[PROMPT]…[/PROMPT]` tal cual; con el render
        normal se llevaría los valores de ejemplo («Hola, Jose») incrustados y
        habría perdido las instrucciones para la IA.
        """
        self.ensure_one()
        self = self.with_context(_resolver_ia=resolver_ia, _lead_ia=lead,
                                 _crudo=crudo)
        text_color = self.text_color or '#111b21'
        parts = []

        parts.append(
            '<div style="margin:0;padding:0;background:#f0f2f5">'
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
            'style="background:#f0f2f5;padding:24px 12px">'
            '<tr><td align="center">'
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="600" '
            'style="max-width:600px;width:100%;background:#ffffff;border-radius:10px;overflow:hidden;'
            'font-family:Arial,Helvetica,sans-serif">'
        )

        if self.show_banner:
            logo = self._logo_src()
            inner = (f'<img src="{logo}" alt="" style="max-height:56px;max-width:80%;display:block;'
                     'margin:0 auto;border:0" />') if logo else '&nbsp;'
            parts.append(
                f'<tr><td align="center" style="background:{self.banner_color or "#075E54"};'
                f'padding:22px 20px">{inner}</td></tr>'
            )
        elif self._logo_src():
            parts.append(
                f'<tr><td align="center" style="padding:22px 20px">'
                f'<img src="{self._logo_src()}" alt="" style="max-height:56px;max-width:80%;'
                'display:block;margin:0 auto;border:0" /></td></tr>'
            )

        parts.append(f'<tr><td style="padding:28px 32px;color:{text_color};font-size:15px;line-height:1.55">')

        heading = self._preparar(self.heading, lead)
        if heading:
            parts.append(
                f'<h1 style="margin:0 0 18px 0;font-size:20px;line-height:1.3;'
                f'color:{text_color};font-weight:600">'
                f'{self._aplicar_marcado(escape(heading))}</h1>'
            )

        parts.append(self._text_to_html(self._preparar(self.body_text, lead)))

        if self.show_box and self.box_text:
            box = self._text_to_html(self._preparar(self.box_text, lead))
            parts.append(
                f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
                f'style="margin:20px 0"><tr><td style="background:{self.box_color or "#d9fdd3"};'
                f'border-left:4px solid {self.box_border_color or "#25D366"};border-radius:6px;'
                f'padding:14px 18px;color:{text_color};font-size:15px;line-height:1.5">{box}</td></tr></table>'
            )

        parts.append('</td></tr>')

        footer = self._preparar(self.footer_text, lead)
        if footer:
            parts.append(
                f'<tr><td style="padding:18px 32px 26px;border-top:1px solid #e9edef;'
                f'color:{self.footer_color or "#667781"};font-size:12px;line-height:1.5">'
                f'{self._text_to_html(footer)}</td></tr>'
            )

        parts.append('</table></td></tr></table></div>')
        return ''.join(parts)

    @api.depends('logo', 'banner_color', 'show_banner', 'heading', 'body_text',
                 'text_color', 'link_color', 'show_box', 'box_text', 'box_color',
                 'box_border_color', 'footer_text', 'footer_color')
    def _compute_preview_html(self):
        for tpl in self:
            try:
                tpl.preview_html = tpl.render_html(resolver_ia=False)
            except Exception:  # noqa: BLE001 — la vista previa nunca debe romper el formulario
                tpl.preview_html = '<p style="color:#d32f2f">No se pudo generar la vista previa.</p>'

    # ── Vista previa con la IA ya ejecutada ──────────────────────────────

    preview_lead_id = fields.Many2one(
        'crm.lead', string='Lead de muestra',
        help='Con qué lead se genera el ejemplo. Vacío = valores inventados.')
    preview_ia_html = fields.Html(
        'Vista previa con IA', sanitize=False, readonly=True, copy=False)
    preview_ia_info = fields.Char('Generada con', readonly=True, copy=False)
    preview_ia_firma = fields.Char(readonly=True, copy=False)
    placeholders_ayuda = fields.Html(
        'Marcadores disponibles', compute='_compute_placeholders_ayuda')

    @api.depends('preview_lead_id')
    def _compute_placeholders_ayuda(self):
        for tpl in self:
            lead = tpl.preview_lead_id
            titulo = (f'Valores de <b>{escape(lead.partner_name or lead.name or "")}</b>:'
                      if lead else 'Sin lead elegido — valores de ejemplo:')
            filas = ''
            for clave, (getter, etiqueta) in PLACEHOLDERS.items():
                if lead:
                    valor = getter(lead)
                elif clave == '{comercial}':
                    valor = tpl.env.user.name or ''
                else:
                    valor = DEMO.get(clave, '')
                filas += (f'<tr><td style="padding:2px 12px 2px 0"><code>{escape(clave)}</code></td>'
                          f'<td style="padding:2px 0">{escape(etiqueta)}</td>'
                          f'<td style="padding:2px 0 2px 12px"><i>{escape(valor or "—")}</i></td></tr>')
            tpl.placeholders_ayuda = f'<p>{titulo}</p><table>{filas}</table>'

    # ── Vista previa: lead de muestra ────────────────────────────────────

    def _lead_de_muestra(self):
        """Un lead real y bien enriquecido para el ejemplo.

        Se prefiere uno que tenga sector y puntos de dolor: con un lead vacío la
        IA no tiene de dónde tirar y escribe generalidades, que es justo lo que
        no sirve para juzgar si la plantilla funciona.
        """
        self.ensure_one()
        if self.preview_lead_id:
            return self.preview_lead_id
        Lead = self.env['crm.lead']
        for dominio in (
            [('enrichment_sector', '!=', False),
             ('enrichment_pain_points', '!=', False),
             ('partner_name', '!=', False)],
            [('enrichment_sector', '!=', False)],
            [('enrichment_date', '!=', False)],
        ):
            lead = Lead.search(dominio, limit=1, order='enrichment_date desc')
            if lead:
                return lead
        return Lead.browse()

    def _firma_contenido(self, lead):
        """Huella del contenido + lead. Si no cambia, no se vuelve a llamar a la IA."""
        self.ensure_one()
        crudo = f'{self.heading}|{self.body_text}|{self.box_text}|{lead.id if lead else 0}'
        return hashlib.sha256(crudo.encode('utf-8')).hexdigest()

    def _regenerar_preview_ia(self, forzar=False):
        """Rehace la vista previa con la IA, si hace falta.

        No se hace en un campo calculado a propósito: los calculados se recalculan
        muchas veces (al teclear, al abrir, al recargar) y cada pasada costaría una
        llamada por bloque. Aquí solo se llama cuando el texto o el lead han
        cambiado de verdad.
        """
        for tpl in self:
            if not RE_PROMPT.search((tpl.body_text or '') + (tpl.box_text or '')
                                    + (tpl.heading or '')):
                tpl.preview_ia_html = False
                tpl.preview_ia_info = False
                tpl.preview_ia_firma = False
                continue
            lead = tpl._lead_de_muestra()
            firma = tpl._firma_contenido(lead)
            if not forzar and firma == tpl.preview_ia_firma and tpl.preview_ia_html:
                continue
            n = len(RE_PROMPT.findall((tpl.body_text or '') + (tpl.box_text or '')
                                      + (tpl.heading or '')))
            try:
                html = tpl.render_html(lead, resolver_ia=True)
                info = (f'{lead.partner_name or lead.name} · {n} bloque(s) '
                        f'desarrollados por la IA') if lead else \
                       f'valores de ejemplo · {n} bloque(s) desarrollados'
            except Exception as exc:  # noqa: BLE001
                # Que falle la IA no puede impedir guardar la plantilla.
                _logger.warning('Vista previa IA de la plantilla %s: %s', tpl.id, exc)
                html = ('<p style="color:#d32f2f">No se pudo generar la vista previa '
                        f'con IA: {escape(str(exc)[:200])}</p>')
                info = 'Error al llamar a la IA'
            tpl.preview_ia_html = html
            tpl.preview_ia_info = info
            tpl.preview_ia_firma = firma
            if lead and not tpl.preview_lead_id:
                tpl.preview_lead_id = lead.id

    @api.model_create_multi
    def create(self, vals_list):
        plantillas = super().create(vals_list)
        plantillas._regenerar_preview_ia()
        return plantillas

    def write(self, vals):
        res = super().write(vals)
        # Solo si ha cambiado algo que afecte al texto o al lead de muestra.
        # Cambiar un color no debe costar una llamada a la IA.
        if {'heading', 'body_text', 'box_text', 'preview_lead_id'} & set(vals):
            self._regenerar_preview_ia()
        return res

    def action_previsualizar_ia(self):
        """Rehace la vista previa aunque no haya cambiado nada (otro lead, u otra
        redacción de la IA para el mismo texto)."""
        self.ensure_one()
        self._regenerar_preview_ia(forzar=True)
        return {'type': 'ir.actions.act_window', 'res_model': self._name,
                'res_id': self.id, 'view_mode': 'form', 'target': 'current'}
