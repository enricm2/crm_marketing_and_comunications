import base64
import json
import re
from odoo import models, fields, api
from odoo.exceptions import UserError

# Colores por defecto de marca
_DEFAULT_HEADER_BG = '#1A3A5C'
_DEFAULT_HEADER_TEXT = '#FFFFFF'
_DEFAULT_BODY_BG = '#FFFFFF'
_DEFAULT_BODY_TEXT = '#555555'
_DEFAULT_CTA_BG = '#1A3A5C'
_DEFAULT_CTA_TEXT = '#FFFFFF'
_DEFAULT_FOOTER_BG = '#EEEEEE'
_DEFAULT_FOOTER_TEXT = '#888888'


class MarketingAiTemplate(models.Model):
    """Plantilla generada por IA (Claude) para email o WhatsApp.
    Requiere revisión y aprobación humana antes de poder usarse en envíos."""

    _name = 'marketing.ai.template'
    _description = 'Plantilla de campaña generada por IA'
    _order = 'campaign_id, channel'
    _inherit = ['mail.thread']

    name = fields.Char(
        string='Nombre', compute='_compute_name', store=True, readonly=False,
    )
    campaign_id = fields.Many2one(
        'marketing.campaign',
        string='Campaña',
        required=True,
        ondelete='cascade',
        index=True,
    )
    channel = fields.Selection([
        ('email', 'Email'),
        ('whatsapp', 'WhatsApp Business'),
        ('linkedin', 'LinkedIn (Prosp)'),
    ], string='Canal', required=True)

    # ── Contenido final ───────────────────────────────────────────────────────
    base_html = fields.Html(
        string='HTML del email',
        sanitize=False,
        help='HTML final del email. Edítalo directamente o usa los campos de diseño y pulsa "Reconstruir HTML".',
    )
    base_text = fields.Text(
        string='Contenido texto plano',
        help='Versión texto plano (para WhatsApp o fallback de email).',
    )
    applied_colors = fields.Text(
        string='Colores aplicados (interno)',
        copy=False,
        help='JSON con los colores que están puestos AHORA MISMO en el HTML. '
             'Sin esto, "Aplicar colores y logo" solo podía funcionar una vez: '
             'buscaba siempre el color POR DEFECTO, que tras el primer cambio '
             'ya no estaba en el HTML.',
    )
    subject = fields.Char(
        string='Asunto (email)',
        help='Asunto del email. Solo aplica al canal email.',
    )

    # ── Diseño visual (email) ─────────────────────────────────────────────────
    # fields.Image (no Binary) para que Odoo redimensione al subir. Un logo de
    # 462 KB se convertía en 617 KB de base64 dentro del HTML y dejaba el email
    # en 629 KB: Gmail recorta el mensaje a partir de ~102 KB, y lo primero que
    # se pierde es el pie... donde está el enlace de baja obligatorio.
    logo_image = fields.Image(
        string='Logo',
        max_width=400,
        max_height=120,
        attachment=True,
        help='Se redimensiona automáticamente a 400×120 px como máximo. '
             'Formatos: PNG, JPG, GIF, WEBP (SVG no se puede redimensionar).',
    )
    logo_image_filename = fields.Char(string='Nombre del archivo del logo')
    logo_alt = fields.Char(string='Texto alternativo del logo', default='Vantis CRM')

    color_header_bg = fields.Char(
        string='Fondo cabecera', default=_DEFAULT_HEADER_BG,
        help='Color hexadecimal, ej: #1A3A5C',
    )
    color_header_text = fields.Char(
        string='Texto cabecera', default=_DEFAULT_HEADER_TEXT,
    )
    color_body_bg = fields.Char(
        string='Fondo cuerpo', default=_DEFAULT_BODY_BG,
    )
    color_body_text = fields.Char(
        string='Texto cuerpo', default=_DEFAULT_BODY_TEXT,
    )
    color_cta_bg = fields.Char(
        string='Fondo botón CTA', default=_DEFAULT_CTA_BG,
    )
    color_cta_text = fields.Char(
        string='Texto botón CTA', default=_DEFAULT_CTA_TEXT,
    )
    color_footer_bg = fields.Char(
        string='Fondo pie', default=_DEFAULT_FOOTER_BG,
    )
    color_footer_text = fields.Char(
        string='Texto pie', default=_DEFAULT_FOOTER_TEXT,
    )

    cta_button_text = fields.Char(
        string='Texto del botón CTA',
        help='Texto del botón de llamada a la acción. Ej: "Solicitar demo"',
    )
    cta_button_url = fields.Char(
        string='URL del botón CTA',
        help='URL a la que lleva el botón CTA. Ej: https://uniasser.com/demo',
    )

    # ── Metadatos IA ──────────────────────────────────────────────────────────
    ai_prompt_used = fields.Text(
        string='Prompt enviado a la IA',
        readonly=True,
    )
    ai_model_used = fields.Char(
        string='Modelo IA usado',
        readonly=True,
        default='claude-sonnet-4-6',
    )

    # ── Estado ────────────────────────────────────────────────────────────────
    state = fields.Selection([
        ('draft', 'Borrador'),
        ('pending_review', 'Pendiente de revisión'),
        ('approved', 'Aprobada'),
        ('rejected', 'Rechazada'),
    ], string='Estado', default='draft', tracking=True)
    reviewed_by = fields.Many2one('res.users', string='Revisada por', readonly=True)
    reviewed_at = fields.Datetime(string='Fecha de revisión', readonly=True)
    rejection_reason = fields.Text(string='Motivo de rechazo')
    version = fields.Integer(string='Versión', default=1, readonly=True)

    @api.depends('campaign_id', 'channel')
    def _compute_name(self):
        for rec in self:
            campaign = rec.campaign_id.name if rec.campaign_id else 'Nueva campaña'
            channel = dict(rec._fields['channel'].selection).get(rec.channel, rec.channel)
            rec.name = f'{campaign} — {channel}'

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._link_to_campaign_slot()
        return records

    def _link_to_campaign_slot(self):
        """Engancha la plantilla al campo de la campaña de su canal si está vacío.

        Sin esto una plantilla creada desde la lista «Plantillas IA» queda
        huérfana: existe y hasta se aprueba, pero la campaña sigue con
        «Plantilla email (IA)» vacía y el envío falla con «la campaña no tiene
        plantilla». Nunca pisa una plantilla ya elegida por el usuario.
        """
        for rec in self:
            campaign = rec.campaign_id
            if not campaign:
                continue
            if rec.channel == 'email' and not campaign.email_template_id:
                campaign.email_template_id = rec.id
            elif rec.channel == 'whatsapp' and not campaign.whatsapp_template_id:
                campaign.whatsapp_template_id = rec.id
            elif rec.channel == 'linkedin' and not campaign.linkedin_template_id:
                campaign.linkedin_template_id = rec.id

    # ── Acciones de estado ────────────────────────────────────────────────────

    def action_submit_review(self):
        self.write({'state': 'pending_review'})

    def action_approve(self):
        self.write({
            'state': 'approved',
            'reviewed_by': self.env.uid,
            'reviewed_at': fields.Datetime.now(),
        })
        # Aprobar es el momento en que el usuario "da por buena" la plantilla:
        # si la campaña aún no tiene ninguna de este canal, esta pasa a serlo.
        self._link_to_campaign_slot()

    def action_reject(self):
        self.write({
            'state': 'rejected',
            'reviewed_by': self.env.uid,
            'reviewed_at': fields.Datetime.now(),
        })

    def action_reset_draft(self):
        self.write({'state': 'draft', 'version': self.version + 1})

    def action_regenerate_with_ai(self):
        """Regenera el contenido llamando a Claude con los datos de la campaña."""
        self.ensure_one()
        campaign = self.campaign_id
        icp = self.env['ir.config_parameter'].sudo()
        _P = 'crm_marketing_and_comunications.'
        anthropic_key = icp.get_param(f'{_P}ai_anthropic_key', '') or \
                        icp.get_param('crm_marketing_and_comunications.ai_anthropic_key', '')
        if not self.env['marketing.ai.service'].proveedores_disponibles():
            from odoo.exceptions import UserError
            raise UserError(
                'No hay clave API de Anthropic configurada.\n'
                'Ve a Ajustes → Marketing Campaigns → Clave API de Anthropic.'
            )

        pain_rules = self.env['marketing.pain_point.rule'].search([
            '|',
            ('service_id', '=', campaign.service_id.id if campaign.service_id else False),
            ('service_id', '=', False),
        ])
        pain_context = '\n'.join([
            f'- Sector "{r.sector_keyword}": {r.pain_point_description}'
            for r in pain_rules
        ]) if pain_rules else ''

        service_name = campaign.service_id.name if campaign.service_id else 'el servicio/producto'

        from odoo.addons.crm_marketing_and_comunications.models.marketing_campaign import MarketingCampaign
        if self.channel == 'email':
            prompt = campaign._build_email_template_prompt(service_name, pain_context)
            content = self.env['marketing.campaign']._call_claude(prompt=prompt, contexto='plantilla email')
            self.write({
                # La IA suele devolver un documento completo (<!DOCTYPE>/<html>).
                # Se guarda como fragmento o el editor visual queda bloqueado.
                'base_html': self._strip_email_document(content),
                'ai_prompt_used': prompt,
                'ai_model_used': 'claude-sonnet-4-6',
                'state': 'pending_review',
                'version': self.version + 1,
                # El HTML recién generado trae los colores por defecto
                'applied_colors': None,
            })
            # Aplicar siempre el diseño configurado: la condición anterior
            # comparaba solo con el default de la cabecera, así que si habías
            # cambiado cualquier otro color (o el CTA) no se aplicaba nada.
            self.action_rebuild_html()
        elif self.channel == 'whatsapp':
            prompt = campaign._build_whatsapp_template_prompt(service_name, pain_context)
            content = self.env['marketing.campaign']._call_claude(prompt=prompt, contexto='plantilla whatsapp')
            self.write({
                'base_text': content,
                'ai_prompt_used': prompt,
                'ai_model_used': 'claude-sonnet-4-6',
                'state': 'pending_review',
                'version': self.version + 1,
            })
        elif self.channel == 'linkedin':
            prompt = campaign._build_linkedin_template_prompt(service_name, pain_context)
            content = self.env['marketing.campaign']._call_claude(prompt=prompt, contexto='plantilla linkedin')
            self.write({
                'base_text': content,
                'ai_prompt_used': prompt,
                'ai_model_used': 'claude-sonnet-4-6',
                'state': 'pending_review',
                'version': self.version + 1,
            })

        campaign.message_post(
            body=f'Plantilla {self.channel} regenerada con IA (versión {self.version}).'
        )
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Contenido regenerado',
                'message': f'La IA ha generado nuevo contenido para la plantilla {self.channel}.',
                'type': 'success',
            },
        }

    # ── Reconstrucción HTML desde campos de diseño ───────────────────────────

    def _get_logo_url(self):
        """URL pública y absoluta del logo.

        NO se usa data URI a propósito: **Gmail elimina las imágenes
        `src="data:image/...;base64,..."`**, así que el logo saldría roto para
        todos los destinatarios de Gmail (Outlook sí las muestra). La forma que
        funciona en todos los clientes es una URL http(s) alcanzable.

        Requiere que el adjunto sea público, porque quien abre el email no
        tiene sesión en Odoo.
        """
        self.ensure_one()
        att = self.env['ir.attachment'].sudo().search([
            ('res_model', '=', self._name),
            ('res_field', '=', 'logo_image'),
            ('res_id', '=', self.id),
        ], limit=1)
        if not att:
            return ''
        if not att.public:
            att.public = True
        base = self.env['ir.config_parameter'].sudo().get_param('web.base.url') or ''
        return f'{base.rstrip("/")}/web/image/{att.id}'

    def _get_logo_html(self):
        """Tag <img> del logo, o un logo de texto si no hay imagen."""
        icp = self.env['ir.config_parameter'].sudo()
        p = 'crm_marketing_and_comunications.'
        comp_name = icp.get_param(p + 'company_name', 'Vantis CRM')
        alt = self.logo_alt or comp_name
        if self.logo_image:
            src = self._get_logo_url()
            if src:
                # data-mkt-logo marca la imagen para poder reemplazarla en
                # sucesivas pulsaciones de "Aplicar colores y logo"
                return (
                    f'<img src="{src}" alt="{alt}" data-mkt-logo="1" '
                    f'style="max-height:60px; max-width:200px; display:block; margin:0 auto;" />'
                )
        # Logo de texto como fallback
        header_text = self.color_header_text or _DEFAULT_HEADER_TEXT
        logo_txt = self.env['ir.config_parameter'].sudo().get_param('crm_marketing_and_comunications.logo_text', 'VANTIS')
        return (
            f'<span style="font-family:Arial,sans-serif; font-size:26px; font-weight:bold; '
            f'color:{header_text}; letter-spacing:3px;">{logo_txt}</span>'
            f'<span style="display:block; font-size:11px; color:{header_text}; '
            f'opacity:0.8; margin-top:4px;">Consulting</span>'
        )

    # ── Bloques [PROMPT] … [/PROMPT] ──────────────────────────────────────────

    # Todo lo que quede FUERA de estas etiquetas es texto estático y se envía
    # tal cual. Lo de DENTRO es una instrucción para la IA, que se resuelve por
    # cada lead y se sustituye por el texto generado.
    #
    # Ejemplo dentro de la plantilla:
    #   Hola,
    #   [PROMPT]Busca en el enriquecimiento del lead sus puntos de dolor y
    #   adapta el texto ofreciéndole nuestros servicios. Máximo 3 frases.[/PROMPT]
    #   Un saludo.
    #
    # Se admiten varios bloques en la misma plantilla; cada uno se resuelve por
    # separado, y el prompt puede incluir la extensión máxima o cualquier otra
    # instrucción de estilo.
    _RE_PROMPT = re.compile(r'\[PROMPT\](.*?)\[/PROMPT\]', re.DOTALL | re.IGNORECASE)

    def tiene_prompts(self, texto=None):
        """¿La plantilla lleva bloques de IA?"""
        self.ensure_one()
        return bool(self._RE_PROMPT.search(texto if texto is not None else (self.base_html or '')))

    def contar_prompts(self):
        self.ensure_one()
        return len(self._RE_PROMPT.findall(self.base_html or ''))

    def resolver_prompts(self, texto, lead=None, contexto_extra=''):
        """Sustituye cada bloque [PROMPT]…[/PROMPT] por lo que genere la IA.

        Si la IA falla, NO devuelve texto a medias: propaga la excepción para
        que quien llama pueda abortar el envío de ese lead. Un email con un
        hueco donde debía ir la personalización hace más daño que no enviarlo.

        :param texto: HTML o texto con las etiquetas
        :param lead: crm.lead del destinatario, para dar contexto a la IA
        :returns: el texto con los bloques resueltos
        :raises UserError: si algún bloque no se pudo generar
        """
        self.ensure_one()
        if not texto or not self._RE_PROMPT.search(texto):
            return texto

        contexto_lead = self._contexto_del_lead(lead) if lead else ''
        servicio = (self.campaign_id.service_id.name
                    if self.campaign_id and self.campaign_id.service_id else '')

        def _resolver(match):
            instruccion = (match.group(1) or '').strip()
            if not instruccion:
                return ''
            prompt = f"""Eres quien redacta un email comercial B2B en español.

=== DATOS DEL DESTINATARIO ===
{contexto_lead or '(sin datos de enriquecimiento)'}

=== SERVICIO QUE OFRECEMOS ===
{servicio or '(no especificado)'}
{contexto_extra}

=== LO QUE TIENES QUE ESCRIBIR ===
{instruccion}

=== REGLAS ===
- Devuelve SOLO el texto pedido. Sin preámbulos, sin comillas, sin explicaciones.
- Va incrustado dentro de un email ya redactado: no pongas saludo ni despedida
  salvo que la instrucción lo pida expresamente.
- No inventes datos del destinatario. Si no hay información suficiente para
  algo, escribe de forma más general en vez de suponer.
- Respeta la extensión que indique la instrucción."""
            generado = self.env['marketing.campaign']._call_claude(
                prompt=prompt, contexto=f'bloque PROMPT (plantilla {self.name})',
            )
            return (generado or '').strip()

        return self._RE_PROMPT.sub(_resolver, texto)

    def _contexto_del_lead(self, lead):
        """Datos del lead que la IA puede usar. Solo lo que existe de verdad."""
        if not lead:
            return ''
        campos = [
            ('Empresa', lead.partner_name or lead.name),
            ('Cargo del contacto', getattr(lead, 'function', '')),
            ('Web', getattr(lead, 'lead_url', '')),
            ('Sector / resumen', getattr(lead, 'enrichment_summary', '')),
            ('Puntos de dolor detectados', getattr(lead, 'enrichment_pain_points', '')),
            ('Servicios que ofrece', getattr(lead, 'enrichment_services', '')),
            ('Tamaño', getattr(lead, 'enrichment_size', '')),
        ]
        return '\n'.join(f'{k}: {v}' for k, v in campos if v)

    def action_previsualizar_prompts(self):
        """Resuelve los bloques con un lead de muestra, para revisar antes de lanzar."""
        self.ensure_one()
        if not self.tiene_prompts():
            raise UserError(
                'Esta plantilla no tiene bloques [PROMPT]…[/PROMPT].\n\n'
                'Escribe la instrucción para la IA entre esas etiquetas dentro '
                'del HTML. Todo lo que quede fuera se envía tal cual.'
            )
        muestra = self.campaign_id.campaign_lead_ids[:1].lead_id
        resuelto = self.resolver_prompts(self.base_html or '', muestra)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': f'{self.contar_prompts()} bloque(s) resueltos',
                'message': ('Vista previa generada con '
                            + (muestra.partner_name or muestra.name or 'un lead de muestra')
                            + '. Revisa el resultado en el campo de vista previa.'
                            if muestra else
                            'No hay leads en la campaña: se ha generado sin datos concretos.'),
                'type': 'success',
                'sticky': True,
            },
        }

    # ── Placeholders {campo} ──────────────────────────────────────────────────
    #
    # Se sustituyen tanto en el envío real como en la vista previa. Cada uno
    # lleva un texto de reserva porque un hueco vacío deja frases rotas
    # («trabajamos con empresas de  en ») y eso se nota más que una
    # generalización.

    PLACEHOLDERS = {
        'nombre':     ('Nombre de pila (sin apellidos)', '_nombre_pila'),
        'nombre_completo': ('Nombre y apellidos',    'contact_name'),
        'empresa':    ('Nombre de la empresa',       'partner_name'),
        'sector':     ('Sector (del enriquecimiento)', 'enrichment_sector'),
        'pais':       ('País',                       'country_id.name'),
        'ciudad':     ('Ciudad',                     'city'),
        'cargo':      ('Cargo del contacto',         'function'),
        'web':        ('Web de la empresa',          'lead_url'),
        'email':      ('Email del contacto',         'email_from'),
        'telefono':   ('Teléfono',                   'phone'),
        'tamano':     ('Tamaño de la empresa',       'enrichment_size'),
    }

    # Qué poner cuando el dato no existe. Vacío donde no hay reserva razonable.
    RESERVAS = {
        'nombre': 'hola',
        'nombre_completo': '',
        'empresa': 'vuestra empresa',
        'sector': 'vuestro sector',
        'pais': '',
        'ciudad': '',
        'cargo': '',
        'web': '',
        'email': '',
        'telefono': '',
        'tamano': '',
    }

    _RE_PLACEHOLDER = re.compile(r'\{([a-z_]+)\}')

    def _valor_placeholder(self, lead, clave):
        """Lee el campo del lead recorriendo la ruta (admite 'country_id.name')."""
        _, ruta = self.PLACEHOLDERS[clave]
        if ruta == '_nombre_pila':
            # Misma lógica que las plantillas de email del CRM, para que
            # {nombre} salude igual en los dos sitios.
            from odoo.addons.crm_marketing_and_comunications.models.crm_email_template import (
                _nombre_pila,
            )
            return _nombre_pila(lead) or self.RESERVAS.get(clave, '')
        valor = lead
        for tramo in ruta.split('.'):
            valor = getattr(valor, tramo, None) if valor else None
            if valor is None:
                break
        texto = '' if valor in (None, False) else str(valor).strip()
        return texto or self.RESERVAS.get(clave, '')

    def valores_placeholders(self, lead=None):
        """Diccionario {clave: valor} para un lead, o los ejemplos si no hay."""
        self.ensure_one()
        if not lead:
            return {
                'nombre': 'Ana', 'nombre_completo': 'Ana Ruiz Bellido',
                'empresa': 'Cerámicas Nules SL',
                'sector': 'fabricación de pavimento cerámico', 'pais': 'España',
                'ciudad': 'Castellón', 'cargo': 'Directora de Operaciones',
                'web': 'https://ejemplo.com', 'email': 'ana@ejemplo.com',
                'telefono': '+34 964 000 000', 'tamano': '25 empleados',
            }
        return {c: self._valor_placeholder(lead, c) for c in self.PLACEHOLDERS}

    def sustituir_placeholders(self, texto, lead=None):
        """Cambia {nombre}, {empresa}, {sector}… por sus valores.

        Un `{algo}` que no sea un placeholder conocido se deja intacto: en el
        HTML hay llaves legítimas (CSS, código) y borrarlas rompería la plantilla.
        """
        self.ensure_one()
        valores = self.valores_placeholders(lead)
        return self._RE_PLACEHOLDER.sub(
            lambda m: valores.get(m.group(1), m.group(0)), texto or '')

    def render_completo(self, texto, lead=None, contexto_extra=''):
        """Deja el texto como se va a enviar: placeholders + bloques de IA.

        El orden importa: primero los placeholders, para que una instrucción
        pueda decir «menciona un reto típico de {sector}» y la IA reciba el
        sector de verdad en vez del literal.
        """
        self.ensure_one()
        texto = self.sustituir_placeholders(texto, lead)
        if self.tiene_prompts(texto):
            texto = self.resolver_prompts(texto, lead, contexto_extra)
        return texto

    placeholders_ayuda = fields.Html(
        string='Placeholders disponibles', compute='_compute_placeholders_ayuda',
    )

    @api.depends('preview_lead_id')
    def _compute_placeholders_ayuda(self):
        for tpl in self:
            lead = tpl.preview_lead_id
            valores = tpl.valores_placeholders(lead)
            titulo = (f'Valores de <b>{lead.partner_name or lead.name}</b>:'
                      if lead else 'Sin lead elegido — se muestran valores de ejemplo:')
            filas = ''.join(
                f'<tr><td style="padding:2px 12px 2px 0"><code>{{{c}}}</code></td>'
                f'<td style="padding:2px 0">{etiqueta}</td>'
                f'<td style="padding:2px 0 2px 12px"><i>{valores.get(c) or "—"}</i></td></tr>'
                for c, (etiqueta, _) in tpl.PLACEHOLDERS.items()
            )
            tpl.placeholders_ayuda = (
                f'<p>{titulo}</p><table>{filas}</table>'
                '<p class="text-muted mb-0">Y dos especiales que rellena el envío: '
                '<code>{{OPENING_PARAGRAPH}}</code> (párrafo de apertura que escribe '
                'la IA para cada lead) y <code>{{UNSUBSCRIBE_URL}}</code> (enlace de '
                'baja, obligatorio).</p>'
            )

    # ── Vista previa con la IA ya ejecutada ───────────────────────────────────

    preview_lead_id = fields.Many2one(
        'crm.lead', string='Lead de muestra',
        help='Con qué lead se genera el ejemplo. Si lo dejas vacío se usa el '
             'primero de la campaña, y si tampoco hay, valores inventados.',
    )
    preview_html = fields.Html(
        string='Vista previa', readonly=True, sanitize=False,
        help='Cómo queda el mensaje: placeholders sustituidos y bloques '
             '[PROMPT] ya redactados por la IA.',
    )
    preview_info = fields.Char(string='Generada con', readonly=True)

    def action_previsualizar(self):
        """Genera el mensaje final de ejemplo, llamando de verdad a la IA.

        Cuesta una llamada por bloque [PROMPT], por eso es un botón y no un
        campo calculado: previsualizar cada vez que se teclea una letra saldría
        caro y lento.
        """
        self.ensure_one()
        lead = self.preview_lead_id or self.campaign_id.campaign_lead_ids[:1].lead_id

        base = self.base_html if self.channel == 'email' else self.base_text
        if not (base or '').strip():
            raise UserError('La plantilla está vacía: no hay nada que previsualizar.')

        resuelto = self.render_completo(base, lead)
        if self.channel == 'email':
            resuelto = resuelto.replace(
                '{{OPENING_PARAGRAPH}}',
                '<p style="background:#fffbe6;border-left:3px solid #f0c000;'
                'padding:6px 10px"><i>[Aquí irá el párrafo de apertura que la IA '
                'escribe para cada lead concreto en el envío]</i></p>')
            resuelto = resuelto.replace('{{UNSUBSCRIBE_URL}}', '#')
        else:
            resuelto = resuelto.replace(
                '{{OPENING_PARAGRAPH}}', '[párrafo de apertura personalizado]')
            resuelto = f'<pre style="white-space:pre-wrap">{resuelto}</pre>'

        n = self.contar_prompts()
        origen = (lead.partner_name or lead.name) if lead else 'valores de ejemplo'
        self.write({
            'preview_html': resuelto,
            'preview_info': (f'{origen} · {n} bloque(s) [PROMPT] resueltos por IA'
                             if n else f'{origen} · sin bloques [PROMPT]'),
        })
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
        }

    # ── Ver / copiar el código HTML ───────────────────────────────────────────

    html_source = fields.Text(
        string='Código HTML',
        compute='_compute_html_source',
        readonly=True,
        store=False,
    )

    @api.depends('base_html')
    def _compute_html_source(self):
        """Documento HTML completo, listo para pegar en otra herramienta.

        `base_html` se guarda como FRAGMENTO para que el editor visual de
        Odoo 19 no se bloquee (ver _wrap_email_document). Para pegarlo en otra
        plataforma hace falta el documento entero con <!DOCTYPE>, <html> y
        <head> — que es exactamente lo que se manda en el envío real.
        """
        for tpl in self:
            try:
                tpl.html_source = tpl._wrap_email_document(tpl.base_html or '')
            except Exception:  # noqa: BLE001 — nunca romper el formulario
                tpl.html_source = tpl.base_html or ''

    def action_view_html_source(self):
        """Abre el HTML completo en una ventana aparte para copiarlo."""
        self.ensure_one()
        if not (self.base_html or '').strip():
            raise UserError(
                'Esta plantilla no tiene HTML todavía. Genérala primero con '
                '"📄 Plantilla en blanco" o "✨ Regenerar contenido con IA".'
            )
        return {
            'type': 'ir.actions.act_window',
            'name': f'Código HTML — {self.name}',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'views': [(
                self.env.ref('crm_marketing_and_comunications.view_marketing_ai_template_html').id,
                'form',
            )],
            'target': 'new',
        }

    def action_rebuild_html(self):
        """Aplica al HTML los colores, logo y CTA configurados arriba."""
        self.ensure_one()
        html = self.base_html or ''
        if not html.strip():
            raise UserError(
                'Esta plantilla no tiene HTML todavía. Pulsa primero '
                '"📄 Plantilla en blanco (sin IA)" o "✨ Regenerar contenido con IA".'
            )

        # Normalizar a fragmento: desbloquea el editor visual si la plantilla
        # venía guardada como documento completo (ver _strip_email_document)
        unwrapped = self._strip_email_document(html)
        was_wrapped = unwrapped != html
        if was_wrapped and not self.subject:
            # Rescatar el <title> como asunto antes de perder el <head>
            title = self._extract_doc_title(html)
            if title:
                self.subject = title
        html = unwrapped

        # 1. Colores — se sustituye lo que HAY en el HTML, no el valor por
        #    defecto, y acotando por propiedad CSS para no confundir un color
        #    de fondo con uno de texto que valgan lo mismo.
        applied = self._get_applied_colors()
        total_subs = 0
        sin_efecto = []
        for field, (prop, default) in self._COLOR_MAP.items():
            new_color = self[field] or default
            old_color = applied.get(field) or default
            html, n = self._sub_css_color(html, prop, old_color, new_color)
            total_subs += n
            if n == 0 and old_color.lower() != new_color.lower():
                sin_efecto.append(self._fields[field].string)

        # 2. Logo
        logo_subs = 0
        if self.logo_image:
            logo_img = self._get_logo_html()
            # a) Logo de texto generado por la IA (span con letter-spacing).
            #    El patrón original exigía literalmente "UNIASSER", así que no
            #    hacía nada si la IA escribía otra cosa.
            html, n1 = re.subn(
                r'<span[^>]*letter-spacing[^>]*>[^<]{1,40}</span>'
                r'(?:\s*<span[^>]*>[^<]{1,40}</span>)?',
                logo_img, html, count=1, flags=re.IGNORECASE,
            )
            # b) Si ya había una imagen de logo puesta, se reemplaza
            if not n1:
                html, n1 = re.subn(
                    r'<img[^>]*data-mkt-logo="1"[^>]*/?>', logo_img, html,
                    count=1, flags=re.IGNORECASE,
                )
            logo_subs = n1

        # 3. Botón CTA
        if self.cta_button_text or self.cta_button_url:
            cta_bg = self.color_cta_bg or _DEFAULT_CTA_BG

            def replace_cta(m):
                tag_open = m.group(1)
                content = m.group(2)
                tag_close = m.group(3)
                new_text = self.cta_button_text or content.strip()
                if self.cta_button_url:
                    tag_open = re.sub(r'href="[^"]*"', f'href="{self.cta_button_url}"', tag_open)
                return f'{tag_open}{new_text}{tag_close}'

            html = re.sub(
                rf'(<a[^>]*{re.escape(cta_bg)}[^>]*>)(.*?)(</a>)',
                replace_cta, html, flags=re.IGNORECASE | re.DOTALL,
            )

        # 3b. Quitar botones/enlaces de reunión repetidos: la IA (y las
        #     importaciones) a veces meten el mismo CTA 2-3 veces y editarlo a
        #     mano no cuela porque al regenerar vuelve. Se deja solo el primero.
        cta_subs = 0
        if self.cta_button_url:
            html, cta_subs = self._dedupe_links(html, self.cta_button_url)

        self.base_html = html
        self._store_applied_colors()

        # Informe honesto: antes decía siempre "aplicado" aunque no hubiera
        # cambiado ni un byte, que es justo lo que hacía parecer que el botón
        # "no hace nada".
        partes = []
        if total_subs:
            partes.append(f'{total_subs} color(es) actualizados')
        if logo_subs:
            partes.append('logo insertado')
        if cta_subs:
            partes.append(f'{cta_subs} enlace(s) de reunión duplicados eliminados')
        if was_wrapped:
            partes.append('HTML desbloqueado para edición visual')

        if not partes:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'No había nada que cambiar',
                    'message': 'Los colores del formulario ya son los que tiene el '
                               'HTML. Cambia algún color arriba y vuelve a pulsar.',
                    'type': 'warning',
                },
            }

        mensaje = ', '.join(partes).capitalize() + '.'
        if sin_efecto:
            mensaje += (' No se encontró dónde aplicar: ' + ', '.join(sin_efecto) +
                        '. Puede que ese elemento no exista en esta plantilla.')

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'HTML actualizado',
                'message': mensaje,
                'type': 'success' if not sin_efecto else 'warning',
            },
        }

    def action_generate_blank_html(self):
        """Genera un HTML limpio desde cero con los campos de diseño configurados."""
        self.ensure_one()
        header_bg = self.color_header_bg or _DEFAULT_HEADER_BG
        header_text = self.color_header_text or _DEFAULT_HEADER_TEXT
        body_bg = self.color_body_bg or _DEFAULT_BODY_BG
        body_text = self.color_body_text or _DEFAULT_BODY_TEXT
        cta_bg = self.color_cta_bg or _DEFAULT_CTA_BG
        cta_text_color = self.color_cta_text or _DEFAULT_CTA_TEXT
        footer_bg = self.color_footer_bg or _DEFAULT_FOOTER_BG
        footer_text = self.color_footer_text or _DEFAULT_FOOTER_TEXT
        cta_label = self.cta_button_text or 'Solicitar información'
        cta_href = self.cta_button_url or '#'
        unsubscribe = '{{UNSUBSCRIBE_URL}}'
        campaign_name = self.campaign_id.name if self.campaign_id else 'Campaña'

        logo_html = self._get_logo_html()

        # OJO: se genera como FRAGMENTO, sin <!DOCTYPE>/<html>/<head>.
        # Con documento completo, el widget html de Odoo 19 deja el campo en
        # solo lectura y la edición visual es imposible (ver
        # _wrap_email_document). El envoltorio se añade al enviar.
        html = f"""<!-- ASUNTO: {campaign_name} — [edita este asunto] -->
<div style="margin:0; padding:0; background-color:#F5F5F5; font-family:Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#F5F5F5;">
<tr><td align="center" style="padding:20px 0;">
<table width="600" cellpadding="0" cellspacing="0" border="0" style="max-width:600px; width:100%;">

  <!-- CABECERA -->
  <tr>
    <td style="background-color:{header_bg}; padding:30px 20px; text-align:center;">
      {logo_html}
    </td>
  </tr>

  <!-- CUERPO -->
  <tr>
    <td style="background-color:{body_bg}; padding:40px 30px;">

      <!-- Párrafo de apertura personalizado (NO borrar este placeholder) -->
      <p style="color:{body_text}; font-size:16px; line-height:1.6; margin:0 0 16px 0;">
        {{{{OPENING_PARAGRAPH}}}}
      </p>

      <!-- Título principal -->
      <h2 style="color:{header_bg}; font-size:22px; margin:24px 0 12px 0;">
        Título principal del email
      </h2>

      <!-- Párrafo de contenido -->
      <p style="color:{body_text}; font-size:16px; line-height:1.6; margin:0 0 16px 0;">
        Describe aquí el servicio o propuesta de valor. Edita este texto directamente.
      </p>

      <!-- Punto destacado 1 -->
      <p style="color:{body_text}; font-size:16px; line-height:1.6; margin:0 0 8px 0;">
        ✅ Beneficio o punto clave 1
      </p>
      <!-- Punto destacado 2 -->
      <p style="color:{body_text}; font-size:16px; line-height:1.6; margin:0 0 8px 0;">
        ✅ Beneficio o punto clave 2
      </p>
      <!-- Punto destacado 3 -->
      <p style="color:{body_text}; font-size:16px; line-height:1.6; margin:0 0 24px 0;">
        ✅ Beneficio o punto clave 3
      </p>

      <!-- Botón CTA -->
      <table cellpadding="0" cellspacing="0" border="0" style="margin:24px 0;">
        <tr>
          <td style="background-color:{cta_bg}; border-radius:4px; text-align:center;">
            <a href="{cta_href}"
               style="display:inline-block; padding:14px 28px; color:{cta_text_color};
                      font-size:16px; font-weight:bold; text-decoration:none;
                      font-family:Arial,sans-serif;">
              {cta_label}
            </a>
          </td>
        </tr>
      </table>

      <!-- Firma -->
      <p style="color:{body_text}; font-size:14px; line-height:1.6; margin:24px 0 0 0;">
        Un saludo,<br/>
        <strong>Enric</strong><br/>
        {comp_name}<br/>
        <a href="mailto:{comp_email}" style="color:{header_bg};">{comp_email}</a>
      </p>

    </td>
  </tr>

  <!-- PIE -->
  <tr>
    <td style="background-color:{footer_bg}; padding:20px 30px; text-align:center;">
      <p style="color:{footer_text}; font-size:12px; margin:0 0 8px 0;">
        {comp_name} | <a href="mailto:{comp_email}" style="color:{footer_text};">{comp_email}</a>
      </p>
      <p style="color:{footer_text}; font-size:12px; margin:0 0 8px 0;">
        <a href="{privacy_url}" style="color:{footer_text};">Política de privacidad</a>
      </p>
      <p style="color:{footer_text}; font-size:12px; margin:0;">
        Para darse de baja de estas comunicaciones:
        <a href="{unsubscribe}" style="color:{footer_text}; text-decoration:underline;">Darse de baja</a>
      </p>
    </td>
  </tr>

</table>
</td></tr>
</table>
</div>"""
        self.base_html = html
        self._store_applied_colors()
        self.state = 'draft'
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Plantilla base creada',
                'message': 'Ahora edita el contenido directamente en el editor HTML.',
                'type': 'success',
            },
        }

    # ── Sustitución de colores ────────────────────────────────────────────────
    #
    # Se acota por PROPIEDAD CSS, no por cadena suelta. Es imprescindible:
    # _DEFAULT_BODY_BG y _DEFAULT_HEADER_TEXT son los dos '#FFFFFF', así que un
    # replace() global del blanco pisaría el otro y rompería la plantilla.

    @staticmethod
    def _sub_css_color(html, prop, old_color, new_color):
        """Reemplaza `prop: old_color` por `prop: new_color`.

        `prop` es 'background-color' o 'color'. Para 'color' hace falta el
        lookbehind, o también casaría dentro de 'background-color'.
        Devuelve (html_nuevo, nº_de_sustituciones).
        """
        if not html or not old_color or not new_color:
            return html, 0
        if old_color.lower() == new_color.lower():
            return html, 0
        guard = r'(?<![-\w])' if prop == 'color' else ''
        pattern = rf'{guard}{prop}\s*:\s*{re.escape(old_color)}\b'
        new_html, n = re.subn(pattern, f'{prop}:{new_color}', html, flags=re.IGNORECASE)
        return new_html, n

    # Mapa: campo del formulario → (propiedad CSS, color por defecto)
    _COLOR_MAP = {
        'color_header_bg':   ('background-color', _DEFAULT_HEADER_BG),
        'color_header_text': ('color',            _DEFAULT_HEADER_TEXT),
        'color_body_bg':     ('background-color', _DEFAULT_BODY_BG),
        'color_body_text':   ('color',            _DEFAULT_BODY_TEXT),
        'color_cta_bg':      ('background-color', _DEFAULT_CTA_BG),
        'color_cta_text':    ('color',            _DEFAULT_CTA_TEXT),
        'color_footer_bg':   ('background-color', _DEFAULT_FOOTER_BG),
        'color_footer_text': ('color',            _DEFAULT_FOOTER_TEXT),
    }

    def _get_applied_colors(self):
        """Colores que hay ahora en el HTML. La primera vez no hay registro,
        así que se asume que están los valores por defecto."""
        self.ensure_one()
        stored = {}
        if self.applied_colors:
            try:
                stored = json.loads(self.applied_colors) or {}
            except (ValueError, TypeError):
                stored = {}
        return {
            field: stored.get(field) or default
            for field, (_prop, default) in self._COLOR_MAP.items()
        }

    def _store_applied_colors(self):
        self.ensure_one()
        self.applied_colors = json.dumps({
            field: (self[field] or default)
            for field, (_prop, default) in self._COLOR_MAP.items()
        })

    # ── Envoltorio del documento ──────────────────────────────────────────────
    #
    # `base_html` se guarda como FRAGMENTO, sin <!DOCTYPE>/<html>/<head>.
    # Motivo: en Odoo 19 el widget html pasa a SOLO LECTURA en cuanto el valor
    # produce un <head> no vacío (computeContainsComplexHTML en
    # html_editor/static/src/fields/html_field.js). Guardando un documento
    # completo, el editor visual quedaba inutilizable y solo se podía tocar por
    # vista de código. El envoltorio se añade al enviar, con _wrap_email_document.

    _DOC_OPEN = ('<!DOCTYPE html>\n<html>\n<head><meta charset="UTF-8">'
                 '<meta name="viewport" content="width=device-width,initial-scale=1"></head>\n')

    @staticmethod
    def _extract_doc_title(html):
        """Texto del <title>. Se rescata antes de descartar el <head>: la IA
        suele poner ahí el asunto pensado para el email, y si no se recupera
        se perdería al convertir a fragmento."""
        if not html:
            return ''
        m = re.search(r'<title[^>]*>(.*?)</title>', html, re.IGNORECASE | re.DOTALL)
        return re.sub(r'\s+', ' ', m.group(1)).strip() if m else ''

    @staticmethod
    def _dedupe_links(html, url):
        """Deja solo el PRIMER <a href="url">…</a>; borra los repetidos.

        Devuelve (html, nº eliminados). Solo quita la etiqueta <a>, no su
        contenedor: puede quedar un <td>/<p> vacío, cosmético, nunca roto.
        """
        if not html or not url:
            return html or '', 0
        patron = re.compile(
            rf'<a\b[^>]*href="{re.escape(url)}"[^>]*>.*?</a>',
            re.IGNORECASE | re.DOTALL)
        n = [0]

        def _sub(m):
            n[0] += 1
            return m.group(0) if n[0] == 1 else ''

        nuevo = patron.sub(_sub, html)
        return nuevo, max(0, n[0] - 1)

    @staticmethod
    def _strip_email_document(html):
        """Documento completo → fragmento editable. Idempotente."""
        if not html:
            return html or ''
        if not re.search(r'<html[\s>]', html, re.IGNORECASE):
            return html
        # Conservar los comentarios de cabecera (p. ej. <!-- ASUNTO: ... -->)
        prefix = ''
        m_pre = re.match(r'\s*((?:<!--.*?-->\s*)+)', html, re.DOTALL)
        if m_pre:
            prefix = m_pre.group(1)
        m = re.search(r'<body[^>]*>(.*)</body>', html, re.IGNORECASE | re.DOTALL)
        inner = m.group(1) if m else re.sub(
            r'^.*?<html[^>]*>|</html>\s*$', '', html, flags=re.IGNORECASE | re.DOTALL)
        # El estilo del <body> se traslada a un div para no perder el fondo
        body_style = ''
        m_style = re.search(r'<body[^>]*style="([^"]*)"', html, re.IGNORECASE)
        if m_style:
            body_style = m_style.group(1)
        inner = inner.strip()
        if body_style:
            inner = f'<div style="{body_style}">{inner}</div>'
        return f'{prefix}{inner}'

    @api.model
    def _wrap_email_document(self, html):
        """Fragmento → documento completo, para el envío real."""
        if not html:
            return html or ''
        if re.search(r'<html[\s>]', html, re.IGNORECASE):
            return html          # ya venía envuelto
        return f'{self._DOC_OPEN}<body style="margin:0;padding:0;">{html}</body>\n</html>'
