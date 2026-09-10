"""Línea de campaña: un lead dentro de una campaña, con su estado individual."""
import json
import logging
import re
import secrets
import urllib.request

from odoo import models, fields, api
from odoo.exceptions import UserError
from odoo.tools import html2plaintext

_logger = logging.getLogger(__name__)

_P = 'crm_marketing_and_comunications.'

AI_CLASSIFICATION_SELECTION = [
    ('no_response', 'Sin respuesta'),
    ('interested', 'Interesado'),
    ('highly_interested', 'Mucho interés'),
    ('wants_demo', 'Quiere demo/reunión'),
    ('objection', 'Objeción'),
    ('question', 'Pregunta / pide más info'),
    ('unsubscribe_request', 'Petición de baja'),
    ('not_interested', 'No interesado'),
    ('do_not_disturb', 'No molestar'),
    ('courtesy', 'Solo cordialidad'),
    ('needs_human', 'Requiere revisión humana'),
]


class MarketingCampaignLead(models.Model):
    """Una entrada por cada lead dentro de una campaña.
    Permite trackear múltiples campañas sobre el mismo lead sin conflictos."""

    _name = 'marketing.campaign.lead'
    _description = 'Lead en campaña de marketing'
    _order = 'campaign_id, id'
    _inherit = ['mail.thread']

    campaign_id = fields.Many2one(
        'marketing.campaign',
        string='Campaña',
        required=True,
        ondelete='cascade',
        index=True,
    )
    lead_id = fields.Many2one(
        'crm.lead',
        string='Lead / Oportunidad',
        required=True,
        ondelete='cascade',
        index=True,
    )
    lead_name = fields.Char(
        string='Empresa / Lead',
        related='lead_id.partner_name',
        store=True,
    )
    lead_email = fields.Char(
        string='Email',
        related='lead_id.email_from',
        store=True,
    )
    lead_phone = fields.Char(
        string='Teléfono',
        related='lead_id.phone',
        store=True,
    )
    lead_country_id = fields.Many2one(
        'res.country',
        string='País del lead',
        related='lead_id.country_id',
        store=True,
    )

    channel_used = fields.Selection([
        ('email', 'Email'),
        ('whatsapp', 'WhatsApp Business'),
        ('linkedin', 'LinkedIn (Prosp)'),
    ], string='Canal', required=True, default='email')

    # ── Contenido personalizado ───────────────────────────────────────────────
    personalized_opening = fields.Text(
        string='Párrafo de apertura personalizado (IA)',
        help='Párrafo de apertura generado por Claude para ESTE lead concreto, '
             'basado en su enriquecimiento y puntos de dolor.',
    )
    personalized_content = fields.Html(
        string='Contenido final personalizado',
        sanitize=False,
        help='Contenido completo listo para enviar: plantilla base + apertura personalizada.',
    )
    content_approved = fields.Boolean(
        string='Contenido aprobado',
        tracking=True,
        help='Debe estar activado para que el envío sea posible.',
    )
    content_approved_by = fields.Many2one('res.users', string='Aprobado por', readonly=True)
    content_approved_at = fields.Datetime(string='Fecha de aprobación', readonly=True)

    # ── Token de baja RGPD ────────────────────────────────────────────────────
    unsubscribe_token = fields.Char(
        string='Token de baja',
        readonly=True,
        copy=False,
        help='Token único para el enlace de baja RGPD. Se genera al crear la línea.',
    )

    # ── Tracking de envío ─────────────────────────────────────────────────────
    sent_at = fields.Datetime(string='Enviado el', readonly=True)
    delivered_at = fields.Datetime(string='Entregado el', readonly=True)
    opened_at = fields.Datetime(string='Abierto el', readonly=True)
    clicked_at = fields.Datetime(string='Clic el', readonly=True)
    replied_at = fields.Datetime(string='Respondido el', readonly=True)
    bounced = fields.Boolean(string='Rebotado', default=False)
    bounced_at = fields.Datetime(string='Rebotado el', readonly=True)

    # ── Respuesta entrante ────────────────────────────────────────────────────
    last_inbound_message = fields.Text(
        string='Último mensaje recibido',
        help='Texto del último mensaje recibido del lead (email o WhatsApp).',
    )
    last_inbound_at = fields.Datetime(string='Recibido el')

    # ── Clasificación IA ──────────────────────────────────────────────────────
    ai_classification = fields.Selection(
        AI_CLASSIFICATION_SELECTION,
        string='Clasificación IA',
        default='no_response',
        tracking=True,
    )
    ai_close_probability = fields.Float(
        string='Probabilidad de cierre (IA)',
        default=0.0,
        digits=(5, 2),
    )
    ai_confidence = fields.Float(
        string='Confianza de clasificación',
        default=0.0,
        digits=(5, 2),
        help='0.0 – 1.0. Por debajo del umbral configurado → revisión humana.',
    )
    ai_classification_reason = fields.Text(
        string='Justificación IA',
        help='Razonamiento breve de Claude para la clasificación.',
    )
    ai_classified_at = fields.Datetime(string='Clasificado el', readonly=True)

    # ── Seguimiento ───────────────────────────────────────────────────────────
    follow_up_state = fields.Selection([
        ('pending', 'Pendiente de aprobación'),
        ('sent', 'Enviado'),
        ('not_needed', 'No necesario'),
    ], string='Estado seguimiento', default='not_needed')
    follow_up_content = fields.Html(
        string='Contenido del seguimiento',
        sanitize=False,
    )
    follow_up_approved_by = fields.Many2one('res.users', string='Seguimiento aprobado por', readonly=True)

    # ── Exclusión RGPD ────────────────────────────────────────────────────────
    exclusion_reason = fields.Selection([
        ('none', 'Sin exclusión'),
        ('robinson', 'Lista Robinson'),
        ('rgpd_internal', 'Exclusión RGPD interna'),
        ('sin_canal', 'Sin canal disponible'),
        ('error_ia', 'Error de IA — pendiente de subsanar'),
        ('duplicate', 'Contacto duplicado en campaña'),
    ], string='Motivo de exclusión', default='none', tracking=True)
    ai_error = fields.Text(
        string='Último error de IA', readonly=True, copy=False,
        help='Causa por la que no se pudo generar el contenido. El lead queda '
             'excluido hasta que se subsane y se regenere correctamente.',
    )
    ai_error_at = fields.Datetime(string='Fecha del error de IA', readonly=True, copy=False)
    excluded_at = fields.Datetime(string='Excluido el', readonly=True)

    _sql_constraints = [
        (
            'unique_campaign_lead',
            'UNIQUE(campaign_id, lead_id)',
            'Este lead ya está incluido en la campaña.',
        ),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('unsubscribe_token'):
                vals['unsubscribe_token'] = secrets.token_urlsafe(32)
        return super().create(vals_list)

    def _marcar_error_ia(self, mensaje):
        """Registra el fallo y excluye el lead hasta que se subsane."""
        self.ensure_one()
        texto = (mensaje or 'Error desconocido')[:2000]
        self.write({
            'exclusion_reason': 'error_ia',
            'excluded_at': fields.Datetime.now(),
            'ai_error': texto,
            'ai_error_at': fields.Datetime.now(),
        })
        self.message_post(body=f'<b>Excluido por error de IA:</b><br/>{texto}')
        _logger.warning('[campaña %s] lead %s excluido por error de IA: %s',
                        self.campaign_id.reference, self.lead_id.display_name, texto)

    def _limpiar_error_ia(self):
        """Devuelve a la cola un lead cuyo error ya se ha subsanado."""
        for rec in self:
            if rec.exclusion_reason == 'error_ia':
                rec.write({
                    'exclusion_reason': 'none',
                    'excluded_at': False,
                    'ai_error': False,
                    'ai_error_at': False,
                })
                rec.message_post(body='Error de IA subsanado: vuelve a la cola de envío.')

    def action_reintentar_ia(self):
        """Reintenta la generación. Si va bien, sale de la exclusión."""
        for rec in self:
            rec._limpiar_error_ia()
            try:
                rec.action_generate_personalized_content()
            except Exception:  # noqa: BLE001
                # _marcar_error_ia ya lo ha vuelto a excluir con la causa nueva
                continue
        recuperados = len(self.filtered(lambda r: r.exclusion_reason != 'error_ia'))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Reintento terminado',
                'message': f'{recuperados} de {len(self)} lead(s) recuperados.',
                'type': 'success' if recuperados == len(self) else 'warning',
            },
        }

    def get_unsubscribe_url(self):
        """Devuelve la URL de baja para este lead."""
        self.ensure_one()
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        return f'{base_url}/marketing/unsubscribe/{self.unsubscribe_token}'

    # ── Generación de contenido personalizado ─────────────────────────────────

    def _build_followup_opening_prompt(self, campaign, lead, enrichment_block):
        """Prompt del párrafo de apertura cuando la campaña es de tipo seguimiento.

        A diferencia del primer contacto, aquí se parte de que el lead YA
        recibió un mensaje y no respondió: el párrafo recuerda ese contacto sin
        reproche y reengancha hacia una reunión.
        """
        prior = campaign._followup_prior_contact_context(lead)
        return f"""Eres quien redacta el PRIMER PÁRRAFO de un email de SEGUIMIENTO comercial B2B en español. El destinatario YA recibió un mensaje nuestro y no respondió: NO es un primer contacto.

=== CAMPAÑA / OBJETIVO ===
Propósito: {campaign.purpose}
Servicio: {campaign.service_id.name if campaign.service_id else 'el servicio'}
Público objetivo: {campaign.target_audience}

=== CONTACTO ANTERIOR ===
{prior}

=== EMPRESA DESTINATARIA ===
Nombre: {lead.partner_name or lead.name}
Email: {lead.email_from or '—'}
Información de enriquecimiento:
{enrichment_block}

=== INSTRUCCIONES ===
1. Escribe UN párrafo de 2-4 frases.
2. Recuerda de forma cordial que ya le escribimos, SIN reproche y sin frases de queja tipo "no he obtenido respuesta".
3. Da una razón concreta y NUEVA para retomar la conversación ahora (aporta valor, no insistas).
4. Encamina de forma natural hacia una reunión breve; el CTA explícito ya está en el cuerpo de la plantilla.
5. Usa algún detalle del enriquecimiento si lo hay, pero no inventes datos.
6. NO empieces con "Estimado/a" ni con el nombre — el saludo ya está en la plantilla.
7. Devuelve SOLO el párrafo, sin comillas, sin explicaciones.
"""

    def action_generate_personalized_content(self):
        """Genera el párrafo de apertura personalizado para ESTE lead."""
        self.ensure_one()
        if self.exclusion_reason != 'none':
            raise UserError('Este lead está excluido y no puede recibir comunicaciones.')

        campaign = self.campaign_id
        lead = self.lead_id
        icp = self.env['ir.config_parameter'].sudo()
        api_key = icp.get_param(f'{_P}ai_anthropic_key', '') or \
                  icp.get_param('crm_marketing_and_comunications.ai_anthropic_key', '')
        # Basta con que haya CUALQUIER proveedor: el servicio conmuta solo
        if not self.env['marketing.ai.service'].proveedores_disponibles():
            raise UserError(
                'No hay ninguna clave de IA configurada.\n'
                'Define al menos una (Anthropic, Gemini u OpenAI) en Ajustes. '
                'Si hay varias, se usa la preferida y las demás quedan de respaldo.'
            )

        # Datos de enriquecimiento del lead
        enrichment_fields = {
            'Sector / Resumen': getattr(lead, 'enrichment_summary', ''),
            'Puntos de dolor detectados': getattr(lead, 'enrichment_pain_points', ''),
            'Consejo para el email': getattr(lead, 'enrichment_email_advice', ''),
            'Tamaño empresa': getattr(lead, 'enrichment_size', ''),
        }
        enrichment_block = '\n'.join([
            f'- {k}: {v}' for k, v in enrichment_fields.items() if v
        ]) or 'Sin enriquecimiento disponible.'

        # Puntos de dolor aplicables
        summary_text = re.sub(r'<[^>]+>', ' ', str(getattr(lead, 'enrichment_summary', '') or ''))
        pain_points_text = re.sub(r'<[^>]+>', ' ', str(getattr(lead, 'enrichment_pain_points', '') or ''))
        combined_text = f'{summary_text} {pain_points_text}'.lower()

        applicable_rules = []
        for rule in self.env['marketing.pain_point.rule'].search([
            '|',
            ('service_id', '=', campaign.service_id.id if campaign.service_id else False),
            ('service_id', '=', False),
        ]):
            if rule.sector_keyword.lower() in combined_text:
                applicable_rules.append(rule.pain_point_description)

        pain_context = '\n'.join(f'- {r}' for r in applicable_rules) or \
                       'No se detectó sector específico — usa el propósito de la campaña.'

        if campaign.campaign_type == 'followup':
            prompt = self._build_followup_opening_prompt(campaign, lead, enrichment_block)
        else:
            prompt = f"""Eres un experto en copywriting B2B personalizado. Tu tarea es escribir el PRIMER PÁRRAFO de apertura de un email comercial, personalizado para UNA empresa concreta.

=== CAMPAÑA ===
Propósito: {campaign.purpose}
Servicio: {campaign.service_id.name if campaign.service_id else 'el servicio'}
Público objetivo general: {campaign.target_audience}

=== EMPRESA DESTINATARIA ===
Nombre: {lead.partner_name or lead.name}
Email: {lead.email_from or '—'}
Información de enriquecimiento:
{enrichment_block}

=== PUNTOS DE DOLOR APLICABLES A ESTE SECTOR ===
{pain_context}

=== INSTRUCCIONES ===
1. Escribe UN párrafo de 2-4 frases que ATAQUE el punto de dolor más relevante para ESTA empresa concreta.
2. Menciona detalles específicos del enriquecimiento si los hay (sector, tamaño, actividad).
3. Conecta el dolor con el servicio que ofrecemos de forma natural, sin ser brusco.
4. Tono: profesional, directo, empático. No agresivo ni servil.
5. NO empieces con "Estimado/a" ni con el nombre — el saludo ya está en la plantilla.
6. NO incluyas más texto que el párrafo. Solo el párrafo de apertura.

Devuelve SOLO el párrafo, sin comillas, sin explicaciones.
"""
        try:
            opening = campaign._call_claude(prompt=prompt, contexto='apertura personalizada')
        except Exception as e:  # noqa: BLE001
            # No se envía nada a medias: se registra, se excluye y se informa.
            self._marcar_error_ia(f'Apertura personalizada: {e}')
            raise
        self.personalized_opening = opening.strip()

        # Insertar en la plantilla base
        template = None
        if self.channel_used == 'email' and campaign.email_template_id:
            template = campaign.email_template_id
            if template.base_html:
                # Placeholders ({nombre}, {empresa}, {sector}, {pais}…) y bloques
                # [PROMPT] con los datos de ESTE lead. Se hace antes de meter el
                # párrafo de apertura para no reprocesar lo que la IA acaba de
                # escribir.
                base = template.render_completo(template.base_html, lead)
                content = base.replace(
                    '{{OPENING_PARAGRAPH}}',
                    f'<p>{opening.strip()}</p>',
                )
                # Red de seguridad: aunque la plantilla lleve el botón de
                # reunión repetido, el email de cada lead sale con uno solo.
                if template.cta_button_url:
                    content, _ = template._dedupe_links(content, template.cta_button_url)
                # Añadir URL de baja real
                content = content.replace('{{UNSUBSCRIBE_URL}}', self.get_unsubscribe_url())
                # base_html se guarda como FRAGMENTO para que el editor visual
                # de Odoo 19 no quede bloqueado en solo lectura. El documento
                # completo (<!DOCTYPE>, <html>, <head>) se añade aquí, que es
                # donde de verdad hace falta: en el email que sale.
                content = template._wrap_email_document(content)
                self.personalized_content = content
        elif self.channel_used == 'whatsapp' and campaign.whatsapp_template_id:
            template = campaign.whatsapp_template_id
            if template.base_text:
                base = template.render_completo(template.base_text, lead)
                content = base.replace('{{OPENING_PARAGRAPH}}', opening.strip())
                self.personalized_content = content
        elif self.channel_used == 'linkedin' and campaign.linkedin_template_id:
            template = campaign.linkedin_template_id
            if template.base_text:
                base = template.render_completo(template.base_text, lead)
                content = base.replace('{{OPENING_PARAGRAPH}}', opening.strip())
                self.personalized_content = content

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Contenido personalizado generado',
                'message': f'Párrafo de apertura generado para {lead.partner_name or lead.name}.',
                'type': 'success',
            },
        }

    def action_approve_content(self):
        """Aprueba el contenido personalizado para envío."""
        self.write({
            'content_approved': True,
            'content_approved_by': self.env.uid,
            'content_approved_at': fields.Datetime.now(),
        })

    def action_send(self):
        """Envía el mensaje a través del canal correspondiente."""
        self.ensure_one()
        if not self.content_approved:
            raise UserError('El contenido debe estar aprobado antes de enviar.')
        if self.exclusion_reason != 'none':
            motivo = dict(self._fields['exclusion_reason'].selection).get(
                self.exclusion_reason, self.exclusion_reason)
            if self.exclusion_reason == 'error_ia':
                raise UserError(
                    'Este lead está excluido porque falló la generación con IA:\n\n'
                    f'{self.ai_error or "(sin detalle)"}\n\n'
                    'Subsana la causa y pulsa "Reintentar IA" para devolverlo a la cola.'
                )
            if self.exclusion_reason == 'sin_canal':
                raise UserError(
                    'Este lead no tiene email, y WhatsApp no está autorizado en '
                    'esta campaña (o no tiene móvil). Añádele un email, o marca '
                    '"Autorizo el uso de WhatsApp" en la campaña y vuelve a '
                    'generar las líneas.'
                )
            raise UserError(f'Este lead está excluido: {motivo}.')

        guard = self.env['marketing.send.guard']
        allowed, reason = guard.check(self)
        if not allowed:
            raise UserError(f'Envío bloqueado: {reason}')

        if self.channel_used == 'email':
            self._send_email()
        elif self.channel_used == 'whatsapp':
            self._send_whatsapp()

    def _send_email(self):
        campaign = self.campaign_id
        esp = campaign.esp_provider_id
        if not esp:
            raise UserError('La campaña no tiene un proveedor ESP configurado.')

        lead = self.lead_id
        to_email = lead.email_from
        if not to_email:
            raise UserError(f'El lead {lead.name} no tiene email.')

        template = campaign.email_template_id
        # Orden de preferencia del asunto. Antes solo se miraba el comentario
        # <!-- ASUNTO: --> del HTML, así que el campo "Asunto (email)" del
        # formulario se ignoraba por completo y las plantillas sin ese
        # comentario salían siempre con el texto genérico.
        subject = ''
        if template:
            if template.subject:
                subject = template.subject.strip()
            elif template.base_html:
                m = re.search(r'<!--\s*ASUNTO:\s*(.+?)\s*-->', template.base_html)
                if m:
                    subject = m.group(1).strip()
        if not subject:
            subject = f'Información sobre {campaign.service_id.name or "nuestros servicios"}'

        unsub = self.get_unsubscribe_url()

        # El proveedor decide el transporte: si es "Servidor de Odoo", su
        # adaptador envía por ir.mail_server en vez de por una API externa.
        esp.send_email(
            to_email=to_email,
            to_name=lead.partner_name or '',
            subject=subject,
            html_body=self.personalized_content or '',
            unsubscribe_url=unsub,
            campaign_ref=campaign.reference,
        )

        self.write({'sent_at': fields.Datetime.now()})
        self.message_post(body=f'Email enviado a {to_email}.')

    def _send_whatsapp(self):
        campaign = self.campaign_id
        lead = self.lead_id

        # Red de seguridad: aunque la línea quedara marcada como 'whatsapp' por
        # cualquier vía (importación, cambio manual, datos antiguos), sin la
        # autorización explícita de la campaña no sale ningún WhatsApp.
        if not campaign.whatsapp_authorized:
            raise UserError(
                'WhatsApp no está autorizado en esta campaña. Marca "Autorizo el '
                'uso de WhatsApp" en la ficha de la campaña si de verdad quieres '
                'usar ese canal.'
            )

        # El email manda: si el lead tiene dirección de correo, no se le
        # escribe por WhatsApp aunque la línea diga lo contrario.
        if (lead.email_from or '').strip():
            raise UserError(
                f'{lead.partner_name or lead.name} tiene email '
                f'({lead.email_from}). El email tiene prioridad: cambia el canal '
                'de esta línea a "email" en vez de enviar por WhatsApp.'
            )

        # crm.lead en Odoo 19 no tiene `mobile`, solo `phone`. Antes ponía
        # `lead.phone or lead.mobile`: no reventaba de milagro, porque el `or`
        # corta antes cuando hay teléfono, pero con `phone` vacío habría dado
        # AttributeError en vez del mensaje de abajo.
        phone = (lead.phone or '').strip()
        if not phone:
            raise UserError(f'El lead {lead.name} no tiene teléfono para WhatsApp.')

        wa_account = self.env['whatsapp.account'].search([('active', '=', True)], limit=1)
        if not wa_account:
            raise UserError('No hay cuenta de WhatsApp Business activa configurada.')

        wa_api = wa_account._get_api()
        wa_api.send_text(phone=phone, body=self.personalized_content or '')
        self.write({'sent_at': fields.Datetime.now()})
        self.message_post(body=f'WhatsApp enviado a {phone}.')

    # ── Clasificación IA de respuesta entrante ────────────────────────────────

    def action_classify_response(self):
        """Clasifica la última respuesta recibida con Claude."""
        self.ensure_one()
        if not self.last_inbound_message:
            raise UserError('No hay respuesta entrante que clasificar.')

        icp = self.env['ir.config_parameter'].sudo()
        api_key = icp.get_param(f'{_P}ai_anthropic_key', '') or \
                  icp.get_param('crm_marketing_and_comunications.ai_anthropic_key', '')
        # Basta con que haya CUALQUIER proveedor: el servicio conmuta solo
        if not self.env['marketing.ai.service'].proveedores_disponibles():
            raise UserError(
                'No hay ninguna clave de IA configurada.\n'
                'Define al menos una (Anthropic, Gemini u OpenAI) en Ajustes. '
                'Si hay varias, se usa la preferida y las demás quedan de respaldo.'
            )

        campaign = self.campaign_id
        prompt = f"""Clasifica el siguiente mensaje de respuesta a una campaña de marketing B2B.

=== CONTEXTO DE LA CAMPAÑA ===
Propósito: {campaign.purpose}
Servicio: {campaign.service_id.name if campaign.service_id else '—'}

=== MENSAJE RECIBIDO ===
{self.last_inbound_message}

=== INSTRUCCIONES ===
Clasifica en UNA de estas categorías:
- interested: muestra interés en el servicio sin pedir demo aún
- highly_interested: muestra mucho interés, pide llamada urgente o expresa un interés muy alto
- wants_demo: pide reunión, demo, llamada, más información concreta
- objection: tiene dudas o pone objeciones, pero no es un rechazo
- question: pregunta algo concreto (precio, características, ejemplo)
- unsubscribe_request: pide explícitamente darse de baja o no recibir más comunicaciones de manera formal (RGPD)
- not_interested: rechaza sin pedir baja explícita ni que no le molestemos
- do_not_disturb: pide que no le molestemos aunque no pida darse de baja explícitamente
- courtesy: contesta sólo por cordialidad/cortesía sin interés real (por ejemplo, dando las gracias cordialmente o diciendo 'ya lo tenemos solucionado, gracias')
- needs_human: ambiguo, no encaja claramente en ninguna categoría

Devuelve EXACTAMENTE este JSON sin texto adicional:
{{
  "classification": "<una de las categorías anteriores>",
  "confidence": <número entre 0.0 y 1.0>,
  "close_probability": <número entre 0 y 100>,
  "explicit_unsubscribe": <true o false — solo true si la petición de baja es explícita y clara>,
  "reason": "<justificación breve en español, máximo 100 palabras>"
}}
"""
        try:
            raw = campaign._call_claude(prompt=prompt, contexto='clasificar respuesta')
            m = re.search(r'\{.*\}', raw, re.DOTALL)
            data = json.loads(m.group(0)) if m else {}
        except Exception as exc:
            _logger.warning('AI classification error: %s', exc)
            data = {}

        classification = data.get('classification', 'needs_human')
        confidence = float(data.get('confidence', 0.0))
        close_prob = float(data.get('close_probability', 0.0))
        reason = data.get('reason', '')
        explicit_unsub = bool(data.get('explicit_unsubscribe', False))

        threshold = campaign.ai_confidence_threshold or 0.85

        vals = {
            'ai_classification': classification,
            'ai_confidence': confidence,
            'ai_close_probability': close_prob,
            'ai_classification_reason': reason,
            'ai_classified_at': fields.Datetime.now(),
        }
        self.write(vals)

        if confidence >= threshold:
            self._apply_classification(classification, explicit_unsub)
        else:
            self.write({'ai_classification': 'needs_human'})
            self.message_post(
                body=f'Clasificación IA: {classification} (confianza {confidence:.0%} — por debajo del umbral {threshold:.0%}). '
                     f'REQUIERE REVISIÓN MANUAL.',
            )
            # Notificación al responsable
            self._notify_needs_human(classification, reason)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Respuesta clasificada',
                'message': f'Clasificación: {classification} (confianza: {confidence:.0%}).',
                'type': 'success' if confidence >= threshold else 'warning',
            },
        }

    def _apply_classification(self, classification, explicit_unsub):
        """Aplica acciones automáticas según la clasificación con confianza suficiente."""
        campaign = self.campaign_id
        lead = self.lead_id

        if classification in ('interested', 'highly_interested') and campaign.stage_interested_id:
            lead.write({'stage_id': campaign.stage_interested_id.id})
            self.message_post(body=f'Lead movido a etapa "{campaign.stage_interested_id.name}" (interesado/mucho interés).')
            if classification == 'highly_interested':
                lead.activity_schedule(
                    'mail.mail_activity_data_call',
                    summary='Llamar urgente — lead con mucho interés desde campaña',
                    user_id=lead.user_id.id or self.env.uid,
                )

        elif classification == 'wants_demo' and campaign.stage_demo_id:
            lead.write({'stage_id': campaign.stage_demo_id.id})
            self.message_post(body=f'Lead movido a etapa "{campaign.stage_demo_id.name}" (quiere demo).')
            lead.activity_schedule(
                'mail.mail_activity_data_call',
                summary='Agendar demo/reunión — lead interesado desde campaña',
                user_id=lead.user_id.id or self.env.uid,
            )

        elif classification in ('unsubscribe_request',) or (
            classification == 'not_interested' and explicit_unsub
        ):
            # Baja RGPD — solo si confianza ya validada arriba
            self.env['marketing.rgpd.exclusion'].add_exclusion(
                email=lead.email_from,
                phone=lead.phone or '',  # crm.lead en Odoo 19 no tiene 'mobile'
                lead_id=lead.id,
                campaign_id=campaign.id,
                reason='explicit_rejection',
                excluded_by='ai_auto',
            )
            self.write({
                'exclusion_reason': 'rgpd_internal',
                'excluded_at': fields.Datetime.now(),
            })
            if campaign.stage_lost_id:
                lead.write({'stage_id': campaign.stage_lost_id.id})
            self.message_post(body='Lead excluido de comunicaciones RGPD (petición de baja). Movido a etapa de perdidos.')

        elif classification == 'not_interested' and campaign.stage_lost_id:
            lead.write({'stage_id': campaign.stage_lost_id.id})
            self.message_post(body=f'Lead movido a etapa "{campaign.stage_lost_id.name}" (no interesado).')

        elif classification == 'do_not_disturb':
            if campaign.stage_lost_id:
                lead.write({'stage_id': campaign.stage_lost_id.id})
            self.message_post(body='Lead pide no ser molestado de nuevo (sin baja formal). Movido a etapa de perdidos.')

        elif classification == 'courtesy':
            self.message_post(body='El lead ha respondido por cortesía/cordialidad. No requiere acción comercial directa.')

        elif classification == 'question':
            self.write({'follow_up_state': 'pending'})
            self.message_post(body='Lead pide más información. Pendiente de generar seguimiento personalizado.')

    def _notify_needs_human(self, classification, reason):
        """Envía actividad de revisión al responsable del lead."""
        try:
            self.lead_id.activity_schedule(
                'mail.mail_activity_data_todo',
                summary=f'Revisar respuesta de campaña "{self.campaign_id.name}"',
                note=f'Clasificación IA (baja confianza): {classification}. {reason}',
                user_id=self.lead_id.user_id.id or self.env.uid,
            )
        except Exception as exc:
            _logger.warning('Could not schedule activity: %s', exc)

    def message_post(self, **kwargs):
        res = super(MarketingCampaignLead, self).message_post(**kwargs)

        # Solo se auto-clasifican CORREOS entrantes de verdad. Las notas del
        # propio sistema ("Email enviado a…", "Clasificación IA…") son
        # message_type 'notification' y las postea OdooBot; sin este filtro se
        # clasificaban a sí mismas —OdooBot está inactivo, así que no contaba
        # como usuario interno— y cada lead enviado acababa en bucle en
        # "requiere revisión humana".
        mtype = kwargs.get('message_type') or res.message_type
        if mtype not in ('email', 'comment'):
            return res

        body_html = kwargs.get('body') or res.body
        if not body_html:
            return res

        author_id = kwargs.get('author_id') or res.author_id.id
        internos = self.env['res.users'].sudo().with_context(
            active_test=False).search([]).partner_id.ids
        odoobot = self.env.ref('base.partner_root', raise_if_not_found=False)
        if odoobot:
            internos = internos + odoobot.ids

        is_reply = False
        if author_id and author_id not in internos:
            is_reply = True
        elif kwargs.get('email_from') and kwargs.get('email_from') == self.lead_email:
            is_reply = True

        if is_reply:
            plaintext = html2plaintext(body_html).strip()
            if plaintext:
                self.write({
                    'last_inbound_message': plaintext,
                    'last_inbound_at': fields.Datetime.now(),
                    'replied_at': fields.Datetime.now(),
                })
                try:
                    self.action_classify_response()
                except Exception as exc:
                    _logger.warning("Error auto-classifying campaign lead %s response: %s", self.id, exc)
        return res
