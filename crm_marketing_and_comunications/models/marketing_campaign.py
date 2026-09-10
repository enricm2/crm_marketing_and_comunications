"""Modelo principal de campañas de marketing."""
import json
import logging
import re
import urllib.request

import pytz

from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

_P = 'crm_marketing_and_comunications.'

# Tope duro de envíos diarios en el goteo. Google Workspace penaliza
# y acaba bloqueando cuentas que mandan correo no solicitado en volumen
# desde un dominio sin historial de envío masivo.
DRIP_HARD_CAP = 50

# Brand guidelines de Uniasser inyectados en cada prompt de generación
UNIASSER_BRAND_CONTEXT = """
=== IDENTIDAD DE MARCA: UNIASSER / VANTIS ===
Empresa: Uniasser Consulting (marca comercial: Vantis para el producto wa-manager)
Tono: profesional, cercano, directo, sin jerga técnica excesiva.
Paleta: azul corporativo (#1A3A5C), blanco (#FFFFFF), gris claro (#F5F5F5), acento verde (#2ECC71).
Tipografía email: Arial/Helvetica, 16px cuerpo, 22px títulos H2, 18px H3.
Logo: texto "Uniasser" en azul con ícono de engranaje. Siempre en la cabecera.
Firma: Enric, Uniasser Consulting | uniasser.com | enric@uniasser.com
RGPD: El footer SIEMPRE incluye enlace de baja visible, política de privacidad y datos de la empresa.
Estilo email: diseño limpio, 600px ancho, sección header (logo + fondo azul), cuerpo blanco, footer gris.
"""


class MarketingCampaign(models.Model):
    _name = 'marketing.campaign'
    _description = 'Campaña de marketing'
    _order = 'create_date desc'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _rec_name = 'name'

    # ── Identificación ────────────────────────────────────────────────────────
    name = fields.Char(string='Nombre de la campaña', required=True, tracking=True)
    reference = fields.Char(
        string='Referencia', readonly=True, copy=False,
        default=lambda self: self.env['ir.sequence'].next_by_code(
            'marketing.campaign'
        ) or 'CAMP-NUEVO',
    )
    state = fields.Selection([
        ('draft', 'Borrador'),
        ('template_review', 'Revisión de plantilla'),
        ('approved', 'Aprobada'),
        ('sending', 'Enviando'),
        ('active', 'Activa (esperando respuestas)'),
        ('closed', 'Cerrada'),
    ], string='Estado', default='draft', tracking=True)
    campaign_type = fields.Selection([
        ('outreach', 'Campaña de captación'),
        ('followup', 'Seguimiento (re-enganche)'),
    ], string='Tipo', default='outreach', required=True, tracking=True,
        help='«Seguimiento» recuerda a los leads que no respondieron a un '
             'contacto anterior y ajusta el mensaje para reengancharlos y '
             'cerrar una reunión. Usa el mismo sistema de plantillas '
             '([PROMPT]…[/PROMPT], placeholders) y la misma lógica de envío '
             '(goteo / Amazon SES) que una campaña normal.')
    followup_source_campaign_id = fields.Many2one(
        'marketing.campaign',
        string='Campaña de origen',
        domain="[('id','!=',id)]",
        help='Opcional. La campaña previa cuyo mensaje no obtuvo respuesta. '
             'Se usa solo para dar contexto a la IA: fecha y propósito del '
             'contacto anterior.',
    )

    # ── Configuración de leads fuente ─────────────────────────────────────────
    stage_source_id = fields.Many2one(
        'crm.stage',
        string='Etapa CRM origen',
        help='Leads en esta etapa se pueden seleccionar para la campaña.',
        tracking=True,
    )
    lead_ids = fields.Many2many(
        'crm.lead',
        'marketing_campaign_crm_lead_rel',
        'campaign_id', 'lead_id',
        string='Leads en la campaña',
        help='Snapshot de leads seleccionados al crear/actualizar la campaña.',
    )
    lead_count = fields.Integer(
        string='Total leads', compute='_compute_lead_count', store=True,
    )
    campaign_lead_ids = fields.One2many(
        'marketing.campaign.lead',
        'campaign_id',
        string='Líneas de campaña',
    )
    campaign_lead_count = fields.Integer(
        string='Total líneas campaña', compute='_compute_campaign_lead_count',
    )

    # ── Brief de campaña ──────────────────────────────────────────────────────
    purpose = fields.Text(
        string='Propósito de la campaña',
        required=True,
        help='¿Qué se busca conseguir con esta campaña? '
             'Ej: "Conseguir 3 demos de wa-manager con agencias de marketing de Barcelona."',
    )
    service_id = fields.Many2one(
        'product.template',
        string='Producto / Servicio a vender',
        help='El producto o servicio que se va a promocionar en esta campaña.',
    )
    target_audience = fields.Text(
        string='Público objetivo',
        required=True,
        help='Descripción del segmento al que va dirigida la campaña. '
             'Ej: "Agencias de marketing digital de 5-20 empleados que gestionan '
             'WhatsApp Business de sus clientes."',
    )

    # ── Canales ───────────────────────────────────────────────────────────────
    channel_ids = fields.Many2many(
        'marketing.channel',
        string='Canales',
        help='Canales a usar en esta campaña.',
    )
    use_email = fields.Boolean(
        string='Email marketing',
        compute='_compute_channels', store=True,
    )
    use_whatsapp = fields.Boolean(
        string='WhatsApp Business',
        compute='_compute_channels', store=True,
    )
    use_linkedin = fields.Boolean(
        string='LinkedIn (Prosp)',
        compute='_compute_channels', store=True,
    )
    prosp_campaign_id = fields.Char(
        string='ID de Campaña Prosp',
        tracking=True,
        help='ID de la campaña en Prosp para vincular envíos y estadísticas.',
    )
    prosp_list_id = fields.Char(
        string='ID de Lista Prosp',
        tracking=True,
        help='ID de la lista de leads en Prosp a la que añadir los contactos.',
    )
    prosp_campaign_helper_id = fields.Many2one(
        'marketing.prosp.campaign',
        string='Elegir Campaña Prosp',
        help='Selecciona cómodamente la campaña de Prosp importada.',
    )
    prosp_list_helper_id = fields.Many2one(
        'marketing.prosp.list',
        string='Elegir Lista Prosp',
        help='Selecciona cómodamente la lista de contactos de Prosp importada.',
    )
    prosp_drip_enabled = fields.Boolean(
        string='Goteo Prosp activo',
        default=False,
        copy=False,
        tracking=True,
    )

    @api.onchange('prosp_campaign_helper_id')
    def _onchange_prosp_campaign_helper(self):
        if self.prosp_campaign_helper_id:
            self.prosp_campaign_id = self.prosp_campaign_helper_id.prosp_id

    @api.onchange('prosp_list_helper_id')
    def _onchange_prosp_list_helper(self):
        if self.prosp_list_helper_id:
            self.prosp_list_id = self.prosp_list_helper_id.prosp_id

    whatsapp_authorized = fields.Boolean(
        string='Autorizo el uso de WhatsApp',
        default=False,
        tracking=True,
        help='Sin marcar, esta campaña NUNCA enviará por WhatsApp: los leads sin '
             'email quedarán excluidos en vez de recibir un mensaje. El email '
             'tiene prioridad siempre; WhatsApp solo se usa como alternativa '
             'para quien no tiene dirección de correo.',
    )

    # ── Envío de prueba ───────────────────────────────────────────────────────
    test_email = fields.Char(
        string='Email de prueba',
        help='Dirección a la que enviar una prueba antes de lanzar la campaña. '
             'Usa la tuya o una de confianza: se envía por el mismo proveedor '
             'ESP y con la misma plantilla que recibirán los leads.',
    )
    test_sent_at = fields.Datetime(string='Última prueba enviada', readonly=True)
    esp_provider_id = fields.Many2one(
        'marketing.esp.provider',
        string='Proveedor ESP (email)',
        help='Proveedor de envío de email a usar (Acumbamail, etc.).',
    )
    esp_provider_name = fields.Selection(
        related='esp_provider_id.name',
        store=True,
    )

    # ── Plantillas IA ─────────────────────────────────────────────────────────
    email_template_id = fields.Many2one(
        'marketing.ai.template',
        string='Plantilla email (IA)',
        domain="[('campaign_id','=',id),('channel','=','email')]",
    )
    email_template_state = fields.Selection(
        related='email_template_id.state', string='Estado plantilla email',
    )
    whatsapp_template_id = fields.Many2one(
        'marketing.ai.template',
        string='Plantilla WhatsApp (IA)',
        domain="[('campaign_id','=',id),('channel','=','whatsapp')]",
    )
    linkedin_template_id = fields.Many2one(
        'marketing.ai.template',
        string='Plantilla LinkedIn (IA)',
        domain="[('campaign_id','=',id),('channel','=','linkedin')]",
    )
    ai_template_ids = fields.One2many(
        'marketing.ai.template',
        'campaign_id',
        string='Plantillas IA',
    )
    ai_template_count = fields.Integer(
        string='Plantillas', compute='_compute_ai_template_count',
    )

    # ── Mapeo de etapas de destino ────────────────────────────────────────────
    stage_interested_id = fields.Many2one(
        'crm.stage',
        string='Etapa si muestra interés',
        help='A qué etapa del CRM mover el lead si responde mostrando interés.',
    )
    stage_demo_id = fields.Many2one(
        'crm.stage',
        string='Etapa si solicita demo/reunión',
        help='A qué etapa mover el lead si solicita una reunión o demo.',
    )
    stage_lost_id = fields.Many2one(
        'crm.stage',
        string='Etapa si rechaza o pide baja',
        help='A qué etapa mover el lead si rechaza o solicita darse de baja.',
    )

    # ── Configuración de IA ───────────────────────────────────────────────────
    ai_confidence_threshold = fields.Float(
        string='Umbral de confianza IA',
        default=0.85,
        help='Clasificaciones por debajo de este umbral no ejecutan acciones '
             'automáticas — se notifica al usuario para revisión manual.',
    )

    # ── Estadísticas agregadas ────────────────────────────────────────────────
    stats_sent = fields.Integer(
        string='Enviados', compute='_compute_stats', store=True,
    )
    stats_delivered = fields.Integer(
        string='Entregados', compute='_compute_stats', store=True,
    )
    stats_opened = fields.Integer(
        string='Abiertos', compute='_compute_stats', store=True,
    )
    stats_clicked = fields.Integer(
        string='Clics', compute='_compute_stats', store=True,
    )
    stats_replied = fields.Integer(
        string='Respondidos', compute='_compute_stats', store=True,
    )
    stats_bounced = fields.Integer(
        string='Rebotados', compute='_compute_stats', store=True,
    )
    stats_unsubscribed = fields.Integer(
        string='Bajas', compute='_compute_stats', store=True,
    )
    stats_interested = fields.Integer(
        string='Interesados', compute='_compute_stats', store=True,
    )
    stats_wants_demo = fields.Integer(
        string='Quieren demo', compute='_compute_stats', store=True,
    )
    stats_excluded_rgpd = fields.Integer(
        string='Excluidos RGPD/Robinson', compute='_compute_stats', store=True,
    )
    stats_excluded_no_channel = fields.Integer(
        string='Sin email/teléfono', compute='_compute_stats', store=True,
    )
    stats_excluded_duplicate = fields.Integer(
        string='Duplicados en campaña', compute='_compute_stats', store=True,
    )
    stats_excluded_ai_error = fields.Integer(
        string='Errores de IA', compute='_compute_stats', store=True,
    )

    # ── Alertas RGPD ─────────────────────────────────────────────────────────
    robinson_disabled_warning = fields.Boolean(
        string='Aviso Robinson activo',
        compute='_compute_robinson_warning',
    )

    # ── Notas ─────────────────────────────────────────────────────────────────
    notes = fields.Html(string='Notas internas')

    # ── Sincronización Gmail ────────────────────────────────────────────────
    gmail_sync_label = fields.Char(
        string='Etiqueta Gmail de esta campaña',
        help='Etiqueta/carpeta de Gmail a la que limitar la sincronización de '
             'correos de los leads de esta campaña. Vacío = se usa la etiqueta '
             'global de Ajustes, y si tampoco hay, todo el buzón.',
    )

    # ── Computed fields ───────────────────────────────────────────────────────

    @api.depends('lead_ids')
    def _compute_lead_count(self):
        for rec in self:
            rec.lead_count = len(rec.lead_ids)

    def _compute_campaign_lead_count(self):
        for rec in self:
            rec.campaign_lead_count = len(rec.campaign_lead_ids)

    def _compute_ai_template_count(self):
        for rec in self:
            rec.ai_template_count = len(rec.ai_template_ids)

    @api.depends('channel_ids', 'channel_ids.code')
    def _compute_channels(self):
        for rec in self:
            codes = rec.channel_ids.mapped('code')
            rec.use_email = 'email' in codes
            rec.use_whatsapp = 'whatsapp' in codes
            rec.use_linkedin = 'linkedin' in codes

    @api.depends('campaign_lead_ids.sent_at', 'campaign_lead_ids.delivered_at',
                 'campaign_lead_ids.opened_at', 'campaign_lead_ids.clicked_at',
                 'campaign_lead_ids.replied_at', 'campaign_lead_ids.ai_classification',
                 'campaign_lead_ids.exclusion_reason')
    def _compute_stats(self):
        for rec in self:
            leads = rec.campaign_lead_ids
            rec.stats_sent = len(leads.filtered('sent_at'))
            rec.stats_delivered = len(leads.filtered('delivered_at'))
            rec.stats_opened = len(leads.filtered('opened_at'))
            rec.stats_clicked = len(leads.filtered('clicked_at'))
            rec.stats_replied = len(leads.filtered('replied_at'))
            rec.stats_bounced = len(leads.filtered(lambda l: l.bounced))
            # La baja se cuenta por el enlace pulsado (que marca la línea como
            # 'rgpd_internal' + clasificación) O por una respuesta clasificada
            # como petición de baja. El enlace es el dato fiable: lo controlamos
            # nosotros y funciona con cualquier proveedor de envío.
            rec.stats_unsubscribed = len(leads.filtered(
                lambda l: l.ai_classification == 'unsubscribe_request'
                or l.exclusion_reason == 'rgpd_internal'
            ))
            rec.stats_interested = len(leads.filtered(
                lambda l: l.ai_classification == 'interested'
            ))
            rec.stats_wants_demo = len(leads.filtered(
                lambda l: l.ai_classification == 'wants_demo'
            ))
            rec.stats_excluded_rgpd = len(leads.filtered(
                lambda l: l.exclusion_reason in ('rgpd_internal', 'robinson')
            ))
            rec.stats_excluded_no_channel = len(leads.filtered(
                lambda l: l.exclusion_reason == 'sin_canal'
            ))
            rec.stats_excluded_duplicate = len(leads.filtered(
                lambda l: l.exclusion_reason == 'duplicate'
            ))
            rec.stats_excluded_ai_error = len(leads.filtered(
                lambda l: l.exclusion_reason == 'error_ia'
            ))

    def _compute_robinson_warning(self):
        icp = self.env['ir.config_parameter'].sudo()
        enabled = icp.get_param(f'{_P}robinson_check_enabled', 'True') == 'True'
        api_key = icp.get_param(f'{_P}robinson_api_key', '')
        for rec in self:
            rec.robinson_disabled_warning = not enabled or not api_key

    # ── Acciones de estado ────────────────────────────────────────────────────

    def action_load_leads_from_stage(self):
        """Busca todos los leads activos en la etapa seleccionada y los asocia a la campaña."""
        self.ensure_one()
        if self.state not in ('draft',):
            raise UserError('Solo se pueden cargar leads en estado borrador.')
        if not self.stage_source_id:
            raise UserError('Selecciona primero una Etapa CRM origen.')

        leads = self.env['crm.lead'].search([
            ('stage_id', '=', self.stage_source_id.id),
            ('active', '=', True),
        ])

        if not leads:
            raise UserError(f'No se encontraron leads activos en la etapa "{self.stage_source_id.name}".')

        # Asociamos los leads nuevos al campo Many2many (4, id) evita duplicados
        self.write({'lead_ids': [(4, lead.id) for lead in leads]})
        self.message_post(body=f'Se han asociado {len(leads)} leads desde la etapa "{self.stage_source_id.name}".')

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'marketing.campaign',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_generate_campaign_leads(self):
        """Crea las líneas marketing.campaign.lead para los leads seleccionados."""
        self.ensure_one()
        if self.state not in ('draft',):
            raise UserError('Solo se pueden generar líneas en estado borrador.')

        existing_lead_ids = self.campaign_lead_ids.mapped('lead_id').ids
        new_leads = self.lead_ids.filtered(lambda l: l.id not in existing_lead_ids)

        existing_emails = set()
        existing_phones = set()
        existing_linkedin_urls = set()
        for cl in self.campaign_lead_ids:
            email_from = cl.lead_email or (cl.lead_id and cl.lead_id.email_from)
            phone = cl.lead_phone or (cl.lead_id and cl.lead_id.phone)
            linkedin_url = cl.lead_id and cl.lead_id.x_linkedin_url
            if email_from:
                existing_emails.add(email_from.strip().lower())
            if phone:
                phone_clean = re.sub(r'\D', '', phone)
                if phone_clean:
                    existing_phones.add(phone_clean)
            if linkedin_url:
                existing_linkedin_urls.add(linkedin_url.strip().lower())

        lines = []
        n_email = n_whatsapp = n_linkedin = n_sin_canal = n_duplicados = n_bajas = 0
        for lead in new_leads:
            tiene_email = bool((lead.email_from or '').strip())
            tiene_movil = bool((lead.phone or '').strip())
            tiene_linkedin = bool((lead.x_linkedin_url or '').strip())

            # ── Control de baja de marketing / RGPD ───────────────────────
            es_baja = lead.marketing_opt_out or self.env['marketing.rgpd.exclusion'].is_excluded(email=lead.email_from, phone=lead.phone)

            if es_baja:
                channel = 'linkedin' if self.use_linkedin else ('email' if tiene_email else 'whatsapp')
                exclusion = 'rgpd_internal'
                n_bajas += 1
            else:
                # ── Prioridad de canal ────────────────────────────────────────
                if self.use_linkedin and tiene_linkedin:
                    channel, exclusion = 'linkedin', 'none'
                elif self.use_email and tiene_email:
                    channel, exclusion = 'email', 'none'
                elif self.whatsapp_authorized and self.use_whatsapp and tiene_movil:
                    channel, exclusion = 'whatsapp', 'none'
                else:
                    channel = 'linkedin' if self.use_linkedin else ('email' if tiene_email else 'whatsapp')
                    exclusion = 'sin_canal'

            # ── Prevención de duplicados en la misma campaña ───────────────
            if exclusion == 'none':
                if channel == 'linkedin':
                    lk_norm = lead.x_linkedin_url.strip().lower()
                    if lk_norm in existing_linkedin_urls:
                        exclusion = 'duplicate'
                        n_duplicados += 1
                    else:
                        existing_linkedin_urls.add(lk_norm)
                        n_linkedin += 1
                elif channel == 'email':
                    email_norm = lead.email_from.strip().lower()
                    if email_norm in existing_emails:
                        exclusion = 'duplicate'
                        n_duplicados += 1
                    else:
                        existing_emails.add(email_norm)
                        n_email += 1
                elif channel == 'whatsapp':
                    phone_clean = re.sub(r'\D', '', lead.phone or '')
                    if phone_clean and phone_clean in existing_phones:
                        exclusion = 'duplicate'
                        n_duplicados += 1
                    elif phone_clean:
                        existing_phones.add(phone_clean)
                        n_whatsapp += 1
                    else:
                        n_whatsapp += 1
            elif exclusion == 'sin_canal':
                n_sin_canal += 1

            lines.append({
                'campaign_id': self.id,
                'lead_id': lead.id,
                'channel_used': channel,
                'exclusion_reason': exclusion,
                'excluded_at': fields.Datetime.now() if exclusion != 'none' else False,
            })
        if lines:
            self.env['marketing.campaign.lead'].create(lines)
            detalle = f'{n_email} por email'
            if n_whatsapp:
                detalle += f', {n_whatsapp} por WhatsApp'
            if n_linkedin:
                detalle += f', {n_linkedin} por LinkedIn'
            if n_sin_canal:
                detalle += f', {n_sin_canal} sin canal disponible (excluidos)'
            if n_duplicados:
                detalle += f', {n_duplicados} duplicados (excluidos)'
            if n_bajas:
                detalle += f', {n_bajas} con baja solicitada (excluidos)'
            self.message_post(body=f'Líneas de campaña sincronizadas: {len(lines)} nuevas líneas creadas ({detalle}).')
        else:
            ya = self.env['marketing.campaign.lead'].search_count([('campaign_id','=',self.id)])
            self.message_post(body=f'Sincronización solicitada: No había nuevos leads que crear. Las {ya} líneas ya existían.')

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'marketing.campaign',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
        }

    # ── Resolución automática de plantillas ──────────────────────────────────

    def _ensure_channel_templates(self):
        """Rellena email_template_id / whatsapp_template_id si están vacíos.

        Red de seguridad para plantillas creadas por vías que no enganchan el
        campo de la campaña (importaciones antiguas, ediciones a mano). Elige la
        plantilla del canal de ESTA campaña: la aprobada si la hay, si no la más
        reciente. Se llama al principio de las acciones de contenido y envío,
        así el flujo se auto-repara sin que el usuario tenga que tocar el
        desplegable.
        """
        for rec in self:
            if rec.use_email and not rec.email_template_id:
                cand = rec.ai_template_ids.filtered(lambda t: t.channel == 'email')
                elegida = cand.filtered(lambda t: t.state == 'approved')[:1] \
                    or cand.sorted('id')[-1:]
                if elegida:
                    rec.email_template_id = elegida.id
            if rec.use_whatsapp and not rec.whatsapp_template_id:
                cand = rec.ai_template_ids.filtered(lambda t: t.channel == 'whatsapp')
                elegida = cand.filtered(lambda t: t.state == 'approved')[:1] \
                    or cand.sorted('id')[-1:]
                if elegida:
                    rec.whatsapp_template_id = elegida.id
            if rec.use_linkedin and not rec.linkedin_template_id:
                cand = rec.ai_template_ids.filtered(lambda t: t.channel == 'linkedin')
                elegida = cand.filtered(lambda t: t.state == 'approved')[:1] \
                    or cand.sorted('id')[-1:]
                if elegida:
                    rec.linkedin_template_id = elegida.id

    # ── Acciones en bloque sobre las líneas ───────────────────────────────────

    def _lines_ready_for_content(self):
        self.ensure_one()
        channels = []
        if self.use_email:
            channels.append('email')
        if self.use_whatsapp:
            channels.append('whatsapp')
        if self.use_linkedin:
            channels.append('linkedin')
        return self.env['marketing.campaign.lead'].search([
            ('campaign_id', '=', self.id),
            ('channel_used', 'in', channels),
            ('exclusion_reason', '=', 'none'),
            ('sent_at', '=', False),
        ])

    def _lines_missing_content(self):
        """Líneas enviables (sin excluir, sin enviar) que aún no tienen
        el contenido personalizado montado."""
        self.ensure_one()
        return self._lines_ready_for_content().filtered(
            lambda l: not l.personalized_content)

    def action_generate_all_content(self):
        """Arranca la generación de contenido con IA en segundo plano.

        Es una llamada a Claude por lead (~10 s). En una lista de 20+ leads eso
        supera de largo el tiempo máximo de una petición web: el navegador se
        cortaba a mitad y dejaba parte de los leads sin contenido, sin avisar.
        Ahora un cron los procesa por lotes (ver `_content_gen_process_batch`),
        igual que el envío a goteo.
        """
        self.ensure_one()
        self._ensure_channel_templates()
        pendientes = self._lines_missing_content()
        if not pendientes:
            tiene_lineas = bool(self.env['marketing.campaign.lead'].search_count(
                [('campaign_id', '=', self.id)]))
            if not tiene_lineas:
                raise UserError(
                    'Esta campaña todavía no tiene líneas de campaña.\n\n'
                    'En la sección «Leads»: carga los leads de la etapa y pulsa '
                    '«Sincronizar líneas de campaña». Después ya podrás generar '
                    'el contenido.'
                )
            raise UserError(
                'Todas las líneas enviables ya tienen contenido generado.\n\n'
                'Si has cambiado la plantilla y quieres rehacerlo, usa el botón '
                '«🔄 Regenerar contenido IA»: descarta lo generado y lo vuelve a '
                'crear desde la plantilla actual. Si no, el siguiente paso es '
                '«✅ Aprobar todo el contenido».'
            )
        if self.use_email and not self.email_template_id:
            raise UserError(
                'Este envío usa email pero no tiene plantilla. Pulsa «Generar '
                'plantilla con IA» o elige una en la pestaña «Plantillas IA».'
            )

        self.content_gen_enabled = True
        cron = self.env.ref(
            'crm_marketing_and_comunications.ir_cron_marketing_content_gen',
            raise_if_not_found=False)
        if cron and not cron.active:
            cron.sudo().active = True

        self.message_post(body=(
            f'Generación de contenido con IA en marcha para {len(pendientes)} '
            f'lead(s). Se procesa por lotes en segundo plano.'
        ))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Generación de contenido en marcha',
                'message': (
                    f'{len(pendientes)} lead(s) en cola. Puedes cerrar esta '
                    'ventana: sigue en segundo plano. Refresca la lista de leads '
                    'para ver el avance; cuando termine, pulsa «✅ Aprobar todo '
                    'el contenido».'
                ),
                'type': 'success',
                'sticky': True,
            },
        }

    def action_regenerate_all_content(self):
        """Descarta el contenido personalizado ya generado y lo vuelve a crear
        desde la plantilla ACTUAL.

        Pensado para cuando se cambia la plantilla después de haber generado:
        sin esto, «Generar contenido IA (todos)» dice que ya está todo hecho y
        no hay forma de rehacerlo. Solo afecta a líneas NO enviadas — lo que ya
        salió no se toca.
        """
        self.ensure_one()
        self._ensure_channel_templates()
        if self.use_email and not self.email_template_id:
            raise UserError(
                'Este envío usa email pero no tiene plantilla. Elige una en la '
                'pestaña «Plantillas IA» antes de regenerar.'
            )
        objetivo = self._lines_ready_for_content()  # email, sin excluir, sin enviar
        if not objetivo:
            raise UserError(
                'No hay líneas enviables sin enviar que regenerar. '
                '(Las ya enviadas no se pueden rehacer.)'
            )
        enviadas = len(self.campaign_lead_ids.filtered('sent_at'))
        objetivo.write({
            'personalized_opening': False,
            'personalized_content': False,
            'content_approved': False,
            'content_approved_by': False,
            'content_approved_at': False,
        })
        aviso = f'Contenido descartado en {len(objetivo)} línea(s) no enviada(s)'
        if enviadas:
            aviso += f' ({enviadas} ya enviada(s) no se tocan)'
        self.message_post(body=aviso + '. Se regenera desde la plantilla actual.')
        return self.action_generate_all_content()

    def action_stop_content_gen(self):
        """Detiene la generación de contenido en segundo plano."""
        self.ensure_one()
        self.content_gen_enabled = False
        self.message_post(body='Generación de contenido detenida manualmente.')

    # Cuántos leads procesa el cron por pasada. Bajo a propósito: cada uno es
    # una llamada a la IA, y conviene que una pasada del cron acabe rápido.
    CONTENT_GEN_BATCH = 5

    def _content_gen_process_batch(self):
        """Genera el contenido de hasta CONTENT_GEN_BATCH leads pendientes.

        Devuelve True si generó alguno; False si ya no quedaba nada (y en ese
        caso apaga el flag y deja un resumen en el chatter).
        """
        self.ensure_one()
        self._ensure_channel_templates()

        pendientes = self._lines_missing_content()[:self.CONTENT_GEN_BATCH]
        if not pendientes:
            self.content_gen_enabled = False
            inf = self.informe_errores_ia()
            resumen = 'Generación de contenido con IA terminada.'
            if inf['total']:
                resumen += (f' {inf["total"]} lead(s) quedaron excluidos por '
                            f'error de IA — usa «⚠ Errores de IA» para verlos.')
            self.message_post(body=resumen)
            return False

        for linea in pendientes:
            try:
                linea.action_generate_personalized_content()
                self.env.cr.commit()
            except Exception as e:  # noqa: BLE001
                self.env.cr.rollback()
                _logger.exception('[gen-contenido] campaña %s: lead %s',
                                  self.reference, linea.id)
                # Excluir el lead SÍ o SÍ: si no, vuelve a entrar en el lote y
                # el cron se queda en bucle atascado en el mismo lead.
                try:
                    linea._marcar_error_ia(f'Generación de contenido: {e}')
                    self.env.cr.commit()
                except Exception:  # noqa: BLE001
                    self.env.cr.rollback()
                    _logger.exception('[gen-contenido] no se pudo excluir lead %s',
                                      linea.id)
        return True

    @api.model
    def _cron_generate_content(self):
        """Punto de entrada del cron: recorre las campañas con generación activa."""
        campanas = self.search([('content_gen_enabled', '=', True)])
        if not campanas:
            cron = self.env.ref(
                'crm_marketing_and_comunications.ir_cron_marketing_content_gen',
                raise_if_not_found=False)
            if cron and cron.active:
                cron.sudo().active = False
            return
        for campaign in campanas:
            try:
                campaign._content_gen_process_batch()
            except Exception:  # noqa: BLE001
                _logger.exception('[gen-contenido] error en la campaña %s',
                                  campaign.reference)

    def action_approve_all_content(self):
        """Aprueba de golpe todo el contenido ya generado y sin aprobar."""
        self.ensure_one()
        pendientes = self._lines_ready_for_content().filtered(
            lambda l: l.personalized_content and not l.content_approved)
        if not pendientes:
            raise UserError(
                'No hay contenido generado pendiente de aprobar. Genera primero '
                'el contenido con IA.'
            )
        pendientes.action_approve_content()
        self.message_post(body=f'Aprobado en bloque el contenido de {len(pendientes)} linea(s).')
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Contenido aprobado',
                'message': f'{len(pendientes)} linea(s) listas para enviar.',
                'type': 'success',
            },
        }

    # ── Qué se puede medir con el proveedor actual ────────────────────────────

    tracking_aviso = fields.Html(
        string='Alcance del seguimiento', compute='_compute_tracking_aviso',
    )

    @api.depends('esp_provider_id', 'esp_provider_id.name')
    def _compute_tracking_aviso(self):
        """Explica qué métricas son fiables y cuáles no con este proveedor.

        Sin esto, un 0 en "abiertos" es ambiguo: puede significar que nadie
        abrió el email o que no hay forma de saberlo. Mejor decirlo.
        """
        ETIQUETAS = {
            'entregado': 'Entregados',
            'abierto': 'Aperturas',
            'clic': 'Clics',
            'rebote': 'Rebotes',
            'baja': 'Bajas',
        }
        for rec in self:
            if not rec.esp_provider_id:
                rec.tracking_aviso = (
                    '<b>Sin proveedor de envío configurado.</b> '
                    'No se puede medir nada hasta que elijas uno.'
                )
                continue
            try:
                caps = rec.esp_provider_id._get_adapter().capacidades()
            except Exception:  # noqa: BLE001
                rec.tracking_aviso = (
                    '<b>No se pudo determinar el alcance del seguimiento</b> '
                    'para este proveedor.'
                )
                continue

            si = [ETIQUETAS[k] for k, v in caps.items() if v]
            no = [ETIQUETAS[k] for k, v in caps.items() if not v]

            partes = []
            if si:
                partes.append('<b>Se puede medir:</b> ' + ', '.join(si) + '.')
            if no:
                partes.append(
                    '<span style="color:#c0392b"><b>NO se puede medir con este '
                    'proveedor:</b> ' + ', '.join(no) + '.</span> '
                    'Esos contadores se quedarán a cero, y ese cero <u>no</u> '
                    'significa que no haya pasado: significa que no tenemos el dato.'
                )
            if rec.esp_provider_id.name == 'aws_ses' and not caps['entregado']:
                partes.append(
                    '<i>Amazon SES puede informar de todo esto, pero hace falta '
                    'crear un <b>Configuration Set</b> en AWS con destino SNS '
                    'apuntando al webhook de Odoo, y escribir su nombre en el '
                    'campo "Secreto webhook" del proveedor.</i>'
                )
            elif rec.esp_provider_id.name == 'odoo_smtp':
                partes.append(
                    '<i>El SMTP solo confirma que el servidor aceptó el mensaje, '
                    'no que llegara al buzón. Para métricas reales hace falta un '
                    'proveedor con webhooks (Acumbamail, Brevo, SendGrid, '
                    'Mailgun o Amazon SES).</i>'
                )
            rec.tracking_aviso = '<br/>'.join(partes)

    # ── Reutilizar una plantilla en vez de generarla ──────────────────────────

    def importar_plantilla(self, canal='email', plantilla_ia=None, plantilla_crm=None):
        """Copia una plantilla ya hecha a esta campaña.

        Generar siempre con IA obliga a rehacer y revisar de cero un trabajo que
        a menudo ya está hecho y afinado en otra campaña o en las plantillas de
        email del CRM. Aquí se copia, y queda como borrador para poder retocarla
        sin tocar el original.

        La copia es deliberada: enlazar la misma plantilla en dos campañas haría
        que editarla en una cambiara la otra sin avisar.
        """
        self.ensure_one()
        Plantilla = self.env['marketing.ai.template']

        if plantilla_ia:
            vals = {
                'campaign_id': self.id,
                'channel': canal,
                'name': f'{self.name} — {canal}',
                'state': 'draft',
                'ai_prompt_used': plantilla_ia.ai_prompt_used,
            }
            if canal == 'email':
                vals['base_html'] = plantilla_ia.base_html
                vals['subject'] = plantilla_ia.subject
                # Los colores y el logo son parte del diseño: sin ellos la copia
                # sale en blanco y negro y no se parece al original.
                for campo in ('logo_image', 'logo_image_filename', 'logo_alt',
                              'cta_button_text', 'cta_button_url',
                              'color_header_bg', 'color_header_text',
                              'color_body_bg', 'color_body_text',
                              'color_cta_bg', 'color_cta_text',
                              'color_footer_bg', 'color_footer_text'):
                    if campo in plantilla_ia._fields:
                        vals[campo] = plantilla_ia[campo]
            else:
                vals['base_text'] = plantilla_ia.base_text
            nueva = Plantilla.create(vals)

        elif plantilla_crm:
            nueva = Plantilla.create(self._vals_desde_plantilla_crm(plantilla_crm, canal))

        else:
            raise UserError('No se ha indicado ninguna plantilla de origen.')

        if canal == 'email':
            self.email_template_id = nueva.id
        else:
            self.whatsapp_template_id = nueva.id
        self.message_post(body=(
            f'Plantilla de {canal} importada de '
            f'«{(plantilla_ia or plantilla_crm).display_name}». '
            'Es una copia: editarla no afecta al original.'))
        return nueva

    def action_importar_plantilla(self):
        """Abre el asistente para traer una plantilla ya hecha."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Usar una plantilla existente',
            'res_model': 'marketing.template.import',
            'view_mode': 'form',
            'target': 'new',
            'context': {'active_model': 'marketing.campaign', 'active_id': self.id},
        }

    def _vals_desde_plantilla_crm(self, plantilla_crm, canal):
        """Traduce una `crm.email.template` a plantilla de campaña.

        Las dos usan ya los mismos marcadores ({nombre}, {sector}…) y los mismos
        bloques [PROMPT], así que el texto viaja tal cual. Lo que sí cambia es el
        enlace de baja: en el CRM es {baja} y aquí {{UNSUBSCRIBE_URL}}, que es lo
        que sustituye el envío de campaña.
        """
        self.ensure_one()
        Plantilla = self.env['marketing.ai.template']

        if canal == 'whatsapp':
            texto = (plantilla_crm.body_text or '').replace('{baja}', '')
            return {
                'campaign_id': self.id, 'channel': 'whatsapp', 'state': 'draft',
                'name': f'{self.name} — whatsapp',
                'base_text': texto.strip(),
            }

        # `crudo=True`: queremos la PLANTILLA, no un email de muestra. Sin esto
        # los marcadores se sustituían por los valores de ejemplo («Hola, Jose»)
        # y los bloques [PROMPT] se perdían, que es justo el trabajo que se
        # quería reutilizar.
        html = plantilla_crm.render_html(lead=None, resolver_ia=False, crudo=True)
        html = Plantilla._strip_email_document(html)
        html = html.replace('{baja}', '{{UNSUBSCRIBE_URL}}')
        if '{{UNSUBSCRIBE_URL}}' not in html:
            # El enlace de baja es obligatorio en un envío masivo; si la
            # plantilla de origen no lo traía, se añade al final.
            html += ('<p style="font-size:11px;color:#888888;text-align:center">'
                     '<a href="{{UNSUBSCRIBE_URL}}" style="color:#888888">'
                     'Darse de baja</a></p>')
        if '{{OPENING_PARAGRAPH}}' not in html:
            # Sin este hueco, la campaña no puede insertar el párrafo que la IA
            # escribe para cada lead: el envío saldría igual para todos.
            html = '<p>{{OPENING_PARAGRAPH}}</p>' + html
        return {
            'campaign_id': self.id, 'channel': 'email', 'state': 'draft',
            'name': f'{self.name} — email',
            'base_html': html,
            'subject': plantilla_crm.heading or '',
        }

    # ── Informe de errores tras el lanzamiento ────────────────────────────────

    error_lead_count = fields.Integer(
        string='Leads con error de IA', compute='_compute_error_lead_count',
    )

    def _compute_error_lead_count(self):
        for rec in self:
            rec.error_lead_count = self.env['marketing.campaign.lead'].search_count([
                ('campaign_id', '=', rec.id),
                ('exclusion_reason', '=', 'error_ia'),
            ]) if rec.id else 0

    def action_ver_errores_ia(self):
        """Abre la lista de leads excluidos por error de IA."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Errores de IA — {self.name}',
            'res_model': 'marketing.campaign.lead',
            'view_mode': 'list,form',
            'domain': [('campaign_id', '=', self.id),
                       ('exclusion_reason', '=', 'error_ia')],
            'context': {'search_default_campaign_id': self.id},
        }

    def informe_errores_ia(self):
        """Resumen de los fallos, agrupados por causa.

        Se llama al terminar el goteo y desde el botón de la campaña. Agrupa
        porque lo habitual es que 30 leads fallen por el MISMO motivo (sin
        saldo, clave caducada) y no interesa leer 30 líneas iguales.
        """
        self.ensure_one()
        fallidos = self.env['marketing.campaign.lead'].search([
            ('campaign_id', '=', self.id),
            ('exclusion_reason', '=', 'error_ia'),
        ])
        if not fallidos:
            return {'total': 0, 'causas': [], 'texto': ''}

        por_causa = {}
        for f in fallidos:
            # Se agrupa por la primera línea: el detalle largo varía por lead
            causa = (f.ai_error or 'Sin detalle').strip().split('\n')[0][:160]
            por_causa.setdefault(causa, []).append(f)

        lineas = []
        for causa, leads in sorted(por_causa.items(), key=lambda x: -len(x[1])):
            nombres = ', '.join(
                (l.lead_id.partner_name or l.lead_id.name or f'#{l.id}') for l in leads[:5]
            )
            if len(leads) > 5:
                nombres += f' y {len(leads) - 5} más'
            lineas.append(f'• {len(leads)} lead(s) — {causa}\n    {nombres}')

        return {
            'total': len(fallidos),
            'causas': [{'causa': c, 'leads': len(l)} for c, l in por_causa.items()],
            'texto': '\n'.join(lineas),
        }

    def action_informe_errores_ia(self):
        """Muestra el informe por pantalla y lo deja en el chatter."""
        self.ensure_one()
        inf = self.informe_errores_ia()
        if not inf['total']:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Sin errores',
                    'message': 'Ningún lead de esta campaña está excluido por error de IA.',
                    'type': 'success',
                },
            }
        self.message_post(body=(
            f'<b>Informe de errores de IA — {inf["total"]} lead(s) afectados</b>'
            f'<pre style="white-space:pre-wrap">{inf["texto"]}</pre>'
            '<i>Están excluidos del envío hasta que se subsane la causa. '
            'Usa «Reintentar IA» sobre ellos cuando esté resuelto.</i>'
        ))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': f'{inf["total"]} lead(s) con error de IA',
                'message': inf['texto'][:400] + '\n\nInforme completo en el chatter.',
                'type': 'warning',
                'sticky': True,
            },
        }

    # ── Generación de contenido en segundo plano ────────────────────────────

    content_gen_enabled = fields.Boolean(
        string='Generando contenido con IA', default=False, copy=False,
        tracking=True,
        help='Mientras esté activo, un cron va generando por lotes el contenido '
             'personalizado de los leads que aún no lo tienen. Se apaga solo al '
             'terminar.')
    content_gen_pending = fields.Integer(
        string='Leads sin contenido', compute='_compute_content_gen_pending')

    def _compute_content_gen_pending(self):
        for rec in self:
            channels = []
            if rec.use_email:
                channels.append('email')
            if rec.use_whatsapp:
                channels.append('whatsapp')
            if rec.use_linkedin:
                channels.append('linkedin')
            rec.content_gen_pending = self.env['marketing.campaign.lead'].search_count([
                ('campaign_id', '=', rec.id),
                ('channel_used', 'in', channels),
                ('exclusion_reason', '=', 'none'),
                ('sent_at', '=', False),
                '|', ('personalized_content', '=', False),
                     ('personalized_content', '=', ''),
            ]) if rec.id else 0

    # ── Envío a goteo ─────────────────────────────────────────────────────────

    drip_enabled = fields.Boolean(
        string='Envío a goteo activo', default=False, tracking=True, copy=False,
        help='Mientras esté activo, el cron envía UN email por ejecución. Se '
             'apaga solo al terminar la lista o al pasar la hora de corte.')
    drip_stop_hour = fields.Float(
        string='Dejar de enviar a las', default=14.0,
        help='Hora local a partir de la cual no se envía nada más hoy. '
             '14.0 = 14:00, 13.5 = 13:30.')
    drip_max_per_day = fields.Integer(
        string='Máximo de envíos al día', default=DRIP_HARD_CAP,
        help=f'Tope duro de seguridad. No se puede subir de {DRIP_HARD_CAP} (u 100 si utilizas Amazon SES): '
             'Google Workspace penaliza y acaba bloqueando las cuentas que '
             'mandan mucho correo no solicitado desde un dominio sin historial '
             'de envío masivo.')

    @api.constrains('drip_max_per_day', 'esp_provider_id')
    def _check_drip_max_per_day(self):
        for rec in self:
            if rec.drip_max_per_day < 1:
                raise ValidationError('El máximo de envíos al día debe ser al menos 1.')
            
            # Si el proveedor es Amazon SES, el tope de seguridad sube a 100
            max_limit = 100 if rec.esp_provider_id.name == 'aws_ses' else DRIP_HARD_CAP
            
            if rec.drip_max_per_day > max_limit:
                if rec.esp_provider_id.name == 'aws_ses':
                    raise ValidationError(
                        f'El máximo de envíos al día no puede superar {max_limit} para Amazon SES.'
                    )
                else:
                    raise ValidationError(
                        f'El máximo de envíos al día no puede superar {max_limit}. '
                        'Es un tope de seguridad para no comprometer la reputación '
                        'de tu dominio en Google.'
                    )

    @api.onchange('esp_provider_id')
    def _onchange_esp_provider_id(self):
        """Establece automáticamente el tope diario recomendado según el proveedor."""
        if self.esp_provider_id.name == 'aws_ses':
            self.drip_max_per_day = 100
        else:
            self.drip_max_per_day = DRIP_HARD_CAP

    def _drip_sent_today(self):
        """Cuántos se han enviado ya hoy en esta campaña (hora local)."""
        self.ensure_one()
        ahora = fields.Datetime.context_timestamp(self, fields.Datetime.now())
        inicio_local = ahora.replace(hour=0, minute=0, second=0, microsecond=0)
        inicio_utc = inicio_local.astimezone(pytz.utc).replace(tzinfo=None)
        return self.env['marketing.campaign.lead'].search_count([
            ('campaign_id', '=', self.id),
            ('sent_at', '>=', fields.Datetime.to_string(inicio_utc)),
        ])

    def _drip_send_one(self):
        """Procesa y envía los correos pendientes.

        Si se utiliza Amazon SES, envía de golpe todos los correos del margen diario
        en una sola ejecución, ya que Amazon ya gestiona la reputación y entrega.
        Para otros proveedores, envía de uno en uno y espaciado según la frecuencia del cron.
        """
        ahora = fields.Datetime.context_timestamp(self, fields.Datetime.now())
        hora_actual = ahora.hour + ahora.minute / 60.0

        if hora_actual >= self.drip_stop_hour:
            self.drip_enabled = False
            self.message_post(body=(
                f'Goteo detenido: son las {ahora.strftime("%H:%M")} y la hora de '
                f'corte es las {int(self.drip_stop_hour):02d}:'
                f'{int((self.drip_stop_hour % 1) * 60):02d}.'
            ))
            return False

        # Tope diario: protege la reputación del dominio
        enviados_hoy = self._drip_sent_today()
        margen = self.drip_max_per_day - enviados_hoy
        if margen <= 0:
            self.drip_enabled = False
            self.message_post(body=(
                f'Goteo detenido: alcanzado el tope de {self.drip_max_per_day} '
                f'envíos hoy. Vuelve a activarlo mañana para continuar.'
            ))
            return False

        # Definimos el límite de envíos para esta ejecución
        es_aws_ses = (self.esp_provider_id.name == 'aws_ses')
        limite = margen if es_aws_ses else 1

        pendientes = self.env['marketing.campaign.lead'].search([
            ('campaign_id', '=', self.id),
            ('channel_used', '=', 'email'),
            ('sent_at', '=', False),
            ('exclusion_reason', '=', 'none'),
            ('content_approved', '=', True),
        ], order='id', limit=limite)

        if not pendientes:
            self.drip_enabled = False
            self.message_post(body='Goteo terminado: no quedan envíos pendientes.')
            # Informe automático al cerrar el envío
            inf = self.informe_errores_ia()
            if inf['total']:
                self.message_post(body=(
                    f'<b>⚠ {inf["total"]} lead(s) NO recibieron el email por errores de IA</b>'
                    f'<pre style="white-space:pre-wrap">{inf["texto"]}</pre>'
                    '<i>Siguen excluidos hasta que se subsane la causa.</i>'
                ))
            return False

        # Envío en bloque o secuencial
        ok = 0
        for pendiente in pendientes:
            try:
                # Utiliza el método action_send original
                pendiente.action_send()
                _logger.info('[goteo] campaña %s: enviado a %s',
                             self.reference, pendiente.lead_id.email_from)
                ok += 1
            except UserError as ue:
                # Bloqueo de validación o guard (por ejemplo, Robinson, duplicados, etc.): se anota y se sigue sin traceback
                _logger.warning('[goteo] campaña %s: lead %s bloqueado/no enviado: %s',
                                self.reference, pendiente.id, str(ue))
                pendiente.message_post(body=f'Envío omitido (guard/validación): {ue}')
            except Exception as e:  # noqa: BLE001
                # Un lead problemático no debe parar la cola: se anota y se sigue.
                _logger.exception('[goteo] campaña %s: fallo con lead %s',
                                  self.reference, pendiente.id)
                pendiente.message_post(body=f'Fallo inesperado en el envío: {e}')

        return ok > 0

    @api.model
    def _cron_drip_send(self):
        """Punto de entrada del cron: recorre las campañas con goteo activo."""
        campanas_activas = self.search([('drip_enabled', '=', True)])
        if not campanas_activas:
            cron = self.env.ref('crm_marketing_and_comunications.ir_cron_marketing_drip_send', raise_if_not_found=False)
            if cron and cron.active:
                cron.sudo().active = False
            return

        for campaign in campanas_activas:
            try:
                campaign._drip_send_one()
            except Exception:  # noqa: BLE001
                _logger.exception('[goteo] error en la campaña %s', campaign.reference)

    def action_start_drip(self):
        """Arranca el goteo tras comprobar que hay algo que enviar."""
        self.ensure_one()
        self._ensure_channel_templates()

        pendientes = self.env['marketing.campaign.lead'].search_count([
            ('campaign_id', '=', self.id),
            ('channel_used', '=', 'email'),
            ('sent_at', '=', False),
            ('exclusion_reason', '=', 'none'),
            ('content_approved', '=', True),
        ])
        if not pendientes:
            raise UserError(
                'No hay ningún envío listo. Para que un lead entre en la cola '
                'necesita: canal email, contenido generado Y APROBADO, sin '
                'exclusión y sin enviar todavía.'
            )

        enviados_hoy = self._drip_sent_today()
        margen = self.drip_max_per_day - enviados_hoy
        if margen <= 0:
            raise UserError(
                f'Hoy ya se han enviado {enviados_hoy} emails en esta campaña y el '
                f'tope diario es {self.drip_max_per_day}. Continúa mañana.'
            )

        hoy = min(pendientes, margen)
        corte = (f'{int(self.drip_stop_hour):02d}:'
                 f'{int((self.drip_stop_hour % 1) * 60):02d}')

        self.drip_enabled = True

        # Activar el cron de goteo en la base de datos si no está activo
        cron = self.env.ref('crm_marketing_and_comunications.ir_cron_marketing_drip_send', raise_if_not_found=False)
        if cron and not cron.active:
            cron.sudo().active = True

        es_aws_ses = (self.esp_provider_id.name == 'aws_ses')
        self.message_post(body=(
            f'Envío activado (Amazon SES en bloque: {es_aws_ses}): {pendientes} en cola, hasta {hoy} hoy '
            f'(tope diario {self.drip_max_per_day}), corte a las {corte}.'
        ))

        if es_aws_ses:
            aviso = (f'{hoy} email(s) se enviarán en bloque (de un solo golpe) en el próximo ciclo de cron (segundos/minutos). '
                     f'Puedes cerrar esta ventana: todo se procesa en segundo plano.')
        else:
            aviso = (f'{hoy} email(s) se enviarán de uno en uno, espaciados, hasta '
                     f'las {corte}. Puedes cerrar esta ventana: sigue en segundo plano.')

        if pendientes > margen:
            aviso += (f' Los {pendientes - margen} restantes quedan para mañana '
                      f'por el tope de {self.drip_max_per_day} al día.')

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Envío en bloque en marcha' if es_aws_ses else 'Envío pausado en marcha',
                'message': aviso,
                'type': 'success',
                'sticky': True,
            },
        }

    def action_stop_drip(self):
        self.ensure_one()
        self.drip_enabled = False
        self.message_post(body='Goteo detenido manualmente.')

        # Desactivar el cron si no quedan campañas con goteo activo
        otras_activas = self.search_count([('drip_enabled', '=', True), ('id', '!=', self.id)])
        if otras_activas == 0:
            cron = self.env.ref('crm_marketing_and_comunications.ir_cron_marketing_drip_send', raise_if_not_found=False)
            if cron and cron.active:
                cron.sudo().active = False

    def action_check_duplicates_and_issues(self):
        """Analiza la cola de envío en busca de destinatarios duplicados, bajas o incidencias."""
        self.ensure_one()
        
        # Buscar todas las líneas pendientes de envío de la campaña
        lines = self.env['marketing.campaign.lead'].search([
            ('campaign_id', '=', self.id),
            ('sent_at', '=', False),
        ])

        emails_seen = {}
        phones_seen = {}
        
        dupes_email = []
        dupes_phone = []
        bajas_encontradas = []
        
        for line in lines:
            email = (line.lead_email or (line.lead_id and line.lead_id.email_from) or '').strip().lower()
            phone = (line.lead_phone or (line.lead_id and line.lead_id.phone) or '')
            phone_clean = re.sub(r'\D', '', phone)
            
            # 1. Comprobar si es una baja/RGPD
            es_baja = line.lead_id.marketing_opt_out or self.env['marketing.rgpd.exclusion'].is_excluded(email=email, phone=phone)
            if es_baja:
                if line.exclusion_reason == 'none':
                    bajas_encontradas.append(f"{line.lead_id.name or line.lead_id.display_name} ({email or phone})")
                    # Marcarlo como excluido proactivamente
                    line.write({
                        'exclusion_reason': 'rgpd_internal',
                        'excluded_at': fields.Datetime.now(),
                    })
                    
            # 2. Comprobar duplicidad si no está excluido
            if line.exclusion_reason == 'none':
                if line.channel_used == 'email' and email:
                    if email in emails_seen:
                        dupes_email.append(f"{line.lead_id.name or line.lead_id.display_name} ({email})")
                        # Marcarlo como excluido proactivamente
                        line.write({
                            'exclusion_reason': 'duplicate',
                            'excluded_at': fields.Datetime.now(),
                        })
                    else:
                        emails_seen[email] = line
                        
                elif line.channel_used == 'whatsapp' and phone_clean:
                    if phone_clean in phones_seen:
                        dupes_phone.append(f"{line.lead_id.name or line.lead_id.display_name} ({phone})")
                        # Marcarlo como excluido proactivamente
                        line.write({
                            'exclusion_reason': 'duplicate',
                            'excluded_at': fields.Datetime.now(),
                        })
                    else:
                        phones_seen[phone_clean] = line

        # Generar informe en chatter y en pantalla
        partes = []
        hubo_cambios = False
        
        if bajas_encontradas:
            hubo_cambios = True
            partes.append(f"<b>⚠️ {len(bajas_encontradas)} contactos con baja solicitada detectados y excluidos:</b><br/>" + "<br/>".join(bajas_encontradas))
            
        if dupes_email:
            hubo_cambios = True
            partes.append(f"<b>👥 {len(dupes_email)} destinatarios de email duplicados detectados y excluidos:</b><br/>" + "<br/>".join(dupes_email))
            
        if dupes_phone:
            hubo_cambios = True
            partes.append(f"<b>👥 {len(dupes_phone)} destinatarios de WhatsApp duplicados detectados y excluidos:</b><br/>" + "<br/>".join(dupes_phone))
            
        if hubo_cambios:
            reporte_html = "<br/><br/>".join(partes)
            self.message_post(body=f"<b>🔍 INFORME DE VERIFICACIÓN DE CAMPAÑA:</b><br/>{reporte_html}")
            title = "Verificación Completada - Incidencias Resueltas"
            message = "Se encontraron incidencias (duplicados/bajas) que han sido auto-excluidas para seguridad. Revisa las notas de la campaña."
            ntype = "warning"
        else:
            self.message_post(body="<b>🔍 INFORME DE VERIFICACIÓN DE CAMPAÑA:</b> No se encontraron destinatarios duplicados, bajas ni incidencias. Todo listo para enviar.")
            title = "Campaña Correcta"
            message = "Análisis completado: no hay duplicados ni bajas en cola. Todo limpio."
            ntype = "success"
            
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': title,
                'message': message,
                'type': ntype,
                'sticky': True if hubo_cambios else False,
            },
        }

    def action_send_test_email(self):
        """Envía una prueba de la campaña a `test_email`.

        Va por el MISMO proveedor ESP y con la MISMA plantilla que recibirán
        los leads, para que la prueba sirva de algo: se ve el asunto real, el
        logo, los colores y cómo lo renderiza tu cliente de correo.

        No toca el estado de la campaña ni marca ningún lead como enviado.
        """
        self.ensure_one()
        self._ensure_channel_templates()

        destino = (self.test_email or '').strip()
        if not destino:
            raise UserError('Escribe una dirección en "Email de prueba" antes de enviar.')
        if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', destino):
            raise UserError(f'"{destino}" no parece una dirección de email válida.')

        template = self.email_template_id
        if not template or not template.base_html:
            raise UserError(
                'Este envío no tiene una plantilla de email con contenido.\n\n'
                'Pulsa «Generar plantilla con IA» en la barra superior, o abre la '
                'pestaña «Plantillas IA», elige una en «Plantilla email (IA)» y '
                'asegúrate de que tiene HTML (botón «Reconstruir HTML» o '
                '«Regenerar con IA» dentro de la plantilla).'
            )
        esp = self.esp_provider_id
        if not esp:
            raise UserError('La campaña no tiene proveedor ESP configurado.')

        # Contenido: se usa un lead real de la campaña como muestra para que la
        # personalización se vea de verdad. Si aún no hay líneas, se rellena
        # con un texto de ejemplo.
        muestra = self.campaign_lead_ids[:1]
        if muestra and muestra.personalized_opening:
            apertura = f'<p>{muestra.personalized_opening.strip()}</p>'
        elif muestra:
            apertura = (f'<p><em>[Párrafo personalizado — se generará con IA para '
                        f'{muestra.lead_id.partner_name or muestra.lead_id.name}]</em></p>')
        else:
            apertura = ('<p><em>[Aquí irá el párrafo personalizado que la IA escribe '
                        'para cada empresa destinataria]</em></p>')

        contenido = template.base_html.replace('{{OPENING_PARAGRAPH}}', apertura)

        # En una prueba no hay token de lead, apuntamos a un token de prueba seguro
        # para mostrar el diseño de la página de baja sin alterar datos.
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        test_url = f"{base_url}/marketing/unsubscribe/prueba_test_token" if base_url else '#'
        contenido = contenido.replace('{{UNSUBSCRIBE_URL}}', test_url)

        # Mismo envoltorio de documento que en el envío real
        contenido = template._wrap_email_document(contenido)

        asunto = (template.subject or '').strip()
        if not asunto:
            m = re.search(r'<!--\s*ASUNTO:\s*(.+?)\s*-->', template.base_html)
            asunto = m.group(1).strip() if m else \
                f'Información sobre {self.service_id.name or "nuestros servicios"}'

        try:
            esp.send_email(
                to_email=destino,
                to_name='Prueba',
                subject=f'[PRUEBA] {asunto}',
                html_body=contenido,
                unsubscribe_url=base_url or None,
                campaign_ref=f'{self.reference or self.id}-TEST',
            )
        except Exception as e:  # noqa: BLE001 — el error del ESP debe verse en pantalla
            detalle = str(e)
            # Errores conocidos con causa concreta: decir QUÉ hay que hacer en
            # vez de mandar a "revisa las credenciales", que despista cuando el
            # token es válido y lo que falta es contratar/activar un producto.
            if 'SMTP is not active' in detalle:
                raise UserError(
                    'Acumbamail rechaza el envío porque en tu cuenta NO está '
                    'activado el envío transaccional (SMTP).\n\n'
                    'El token es correcto: funciona para listas y campañas, pero '
                    'no para `sendOne`, que es el que se usa aquí.\n\n'
                    'Para arreglarlo, en tu panel de Acumbamail activa o contrata '
                    '"Email transaccional / SMTP". Como alternativa, configura un '
                    'servidor de correo saliente en Odoo '
                    '(Ajustes → Técnico → Servidores de correo saliente).\n\n'
                    f'Respuesta literal de Acumbamail: {detalle}'
                ) from e
            if 'HTTP 401' in detalle or 'HTTP 403' in detalle:
                raise UserError(
                    f'El proveedor ESP rechazó la autenticación:\n\n{detalle}\n\n'
                    'Revisa el token de API del proveedor.'
                ) from e
            raise UserError(
                f'El proveedor ESP rechazó el envío de prueba:\n\n{detalle}\n\n'
                'Revisa la configuración del proveedor y el dominio remitente.'
            ) from e

        self.test_sent_at = fields.Datetime.now()
        self.message_post(body=f'Envío de prueba realizado a <b>{destino}</b>.')

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Prueba enviada',
                'message': f'Se ha enviado a {destino}. Revisa también la carpeta '
                           'de spam: es la mejor pista de si el dominio remitente '
                           'está bien configurado (SPF/DKIM).',
                'type': 'success',
                'sticky': True,
            },
        }

    def action_generate_ai_template(self):
        """Genera la plantilla base con Claude API."""
        self.ensure_one()
        if not self.use_email and not self.use_whatsapp:
            raise UserError('Selecciona al menos un canal antes de generar la plantilla.')

        icp = self.env['ir.config_parameter'].sudo()
        anthropic_key = icp.get_param(f'{_P}ai_anthropic_key', '')
        if not anthropic_key:
            # Fallback al key del módulo crm_marketing_and_comunications
            anthropic_key = icp.get_param('crm_marketing_and_comunications.ai_anthropic_key', '')
        if not anthropic_key:
            raise UserError(
                'No hay clave API de Anthropic configurada.\n'
                'Ve a Ajustes → Marketing Campaigns → Clave API de Anthropic.'
            )

        # Puntos de dolor relevantes para el servicio
        pain_rules = self.env['marketing.pain_point.rule'].search([
            '|',
            ('service_id', '=', self.service_id.id if self.service_id else False),
            ('service_id', '=', False),
        ])
        pain_context = ''
        if pain_rules:
            pain_context = '\n'.join([
                f'- Sector "{r.sector_keyword}": {r.pain_point_description}'
                for r in pain_rules
            ])

        service_name = self.service_id.name if self.service_id else 'el servicio/producto'

        created = []

        if self.use_email:
            if self.campaign_type == 'followup':
                prompt = self._build_followup_email_template_prompt(service_name, pain_context)
            else:
                prompt = self._build_email_template_prompt(service_name, pain_context)
            html_content = self._call_claude(prompt=prompt, contexto='plantilla email')
            template = self.env['marketing.ai.template'].create({
                'campaign_id': self.id,
                'channel': 'email',
                'base_html': html_content,
                'ai_prompt_used': prompt,
                'state': 'pending_review',
            })
            self.email_template_id = template.id
            created.append('email')

        if self.use_whatsapp:
            prompt_wa = self._build_whatsapp_template_prompt(service_name, pain_context)
            text_content = self._call_claude(prompt=prompt_wa, contexto='plantilla whatsapp')
            template_wa = self.env['marketing.ai.template'].create({
                'campaign_id': self.id,
                'channel': 'whatsapp',
                'base_text': text_content,
                'ai_prompt_used': prompt_wa,
                'state': 'pending_review',
            })
            self.whatsapp_template_id = template_wa.id
            created.append('whatsapp')

        self.state = 'template_review'
        self.message_post(
            body=f'Plantilla(s) generada(s) por IA: {", ".join(created)}. Pendiente de revisión.',
        )

        # Abrir las plantillas generadas para revisión inmediata
        template_ids = self.ai_template_ids.ids
        if len(template_ids) == 1:
            return {
                'type': 'ir.actions.act_window',
                'name': 'Revisar plantilla IA',
                'res_model': 'marketing.ai.template',
                'res_id': template_ids[0],
                'view_mode': 'form',
                'target': 'current',
            }
        return {
            'type': 'ir.actions.act_window',
            'name': f'Plantillas IA — {self.name}',
            'res_model': 'marketing.ai.template',
            'view_mode': 'list,form',
            'domain': [('campaign_id', '=', self.id)],
            'context': {'default_campaign_id': self.id},
            'target': 'current',
        }

    def _build_email_template_prompt(self, service_name, pain_context):
        unsubscribe_url = '{{UNSUBSCRIBE_URL}}'
        return f"""{UNIASSER_BRAND_CONTEXT}

=== TAREA ===
Genera una plantilla de email en HTML (diseño responsive, 600px ancho, tabla centrada) para una campaña de email marketing B2B.

CAMPAÑA:
- Propósito: {self.purpose}
- Servicio: {service_name}
- Público objetivo: {self.target_audience}

PUNTOS DE DOLOR POR SECTOR (para el contexto; el párrafo de apertura será personalizado por lead):
{pain_context or 'Sin reglas de puntos de dolor configuradas — usa el propósito de la campaña.'}

ESTRUCTURA Y COLORES — OBLIGATORIO EXACTAMENTE ASÍ:

1. WRAPPER exterior: background-color:#F5F5F5; padding:20px 0;

2. HEADER (fondo azul oscuro):
   - background-color:#1A3A5C; padding:30px 20px; text-align:center;
   - Logo: texto "UNIASSER" con style="font-family:Arial,sans-serif; font-size:28px; font-weight:bold; color:#FFFFFF; letter-spacing:3px; text-decoration:none;"
   - Subtítulo logo: "Consulting" con style="font-size:12px; color:#FFFFFF; opacity:0.8; display:block; margin-top:4px;"
   - IMPORTANTE: TODO el texto del header debe ser color:#FFFFFF (blanco) para que se lea sobre el fondo azul.

3. CUERPO (fondo blanco):
   - background-color:#FFFFFF; padding:40px 30px;
   - TODO el texto del cuerpo: color:#333333; (gris oscuro, NUNCA blanco)
   - Títulos H2: color:#1A3A5C; font-size:22px; font-family:Arial,sans-serif;
   - Párrafos: color:#555555; font-size:16px; line-height:1.6; font-family:Arial,sans-serif;
   - El PRIMER PÁRRAFO es placeholder: <p style="color:#555555; font-size:16px; line-height:1.6; font-family:Arial,sans-serif;">{{{{OPENING_PARAGRAPH}}}}</p>
   - Botón CTA: background-color:#1A3A5C; color:#FFFFFF; padding:14px 28px; border-radius:4px; text-decoration:none; font-weight:bold; display:inline-block;

4. SEPARADOR VISUAL entre secciones del cuerpo (si las hay):
   - Usa background-color:#F5F5F5; padding:20px 30px; (fondo gris claro)
   - Texto: color:#333333; NUNCA color blanco en fondo gris.

5. FOOTER (fondo gris):
   - background-color:#EEEEEE; padding:20px 30px; text-align:center;
   - Todo el texto: color:#888888; font-size:12px;
   - OBLIGATORIO: "Para darse de baja: <a href='{unsubscribe_url}' style='color:#1A3A5C;'>Darse de baja</a>"
   - Política de privacidad: <a href='https://uniasser.com/privacidad' style='color:#1A3A5C;'>Política de privacidad</a>
   - Datos empresa: Uniasser Consulting | enric@uniasser.com

REGLAS CRÍTICAS — INCUMPLIRLAS HACE EL EMAIL INUTILIZABLE:
- El cuerpo del email (zona de contenido) SOLO puede tener fondo blanco (#FFFFFF) o gris muy claro (#F5F5F5). PROHIBIDO usar fondo azul oscuro o cualquier color oscuro en el cuerpo.
- El fondo azul oscuro (#1A3A5C) ÚNICAMENTE se usa en la CABECERA y en el botón CTA.
- NUNCA texto blanco sobre fondo blanco o gris claro — es invisible.
- NUNCA texto gris claro sobre fondo blanco — es ilegible.
- Sobre fondo blanco (#FFFFFF) o gris claro (#F5F5F5): texto SIEMPRE #333333 o #555555.
- Sobre fondo azul oscuro (#1A3A5C, solo cabecera y CTA): texto SIEMPRE #FFFFFF.
- Si quieres resaltar una sección, usa borde izquierdo de color o fondo #EEF2F7 (azul muy claro), NUNCA un fondo oscuro.
- NO devuelvas el HTML dentro de bloques de código markdown (sin ``` al inicio ni al final).

COMPATIBILIDAD:
- Usa tablas HTML para la estructura (compatible con Outlook).
- CSS inline en todos los elementos (no hojas de estilo externas ni <style>).
- Ancho máximo: 600px en tabla centrada con margin:0 auto.

Asunto del email sugerido: incluirlo como comentario HTML al inicio <!-- ASUNTO: ... -->

Devuelve SOLO el HTML completo, sin explicaciones adicionales.
"""

    def _build_followup_email_template_prompt(self, service_name, pain_context):
        """Plantilla de email para un SEGUIMIENTO (re-enganche).

        Reutiliza intacta la estructura, los colores y el placeholder
        {{OPENING_PARAGRAPH}} del prompt normal; solo cambia el enfoque del
        cuerpo: es un segundo contacto a quien no respondió, no un primer
        acercamiento.
        """
        base = self._build_email_template_prompt(service_name, pain_context)
        extra = f"""

=== AJUSTE OBLIGATORIO: ESTO ES UN SEGUIMIENTO, NO UN PRIMER CONTACTO ===
El email se envía a personas a las que YA escribimos y NO respondieron.
Mantén EXACTAMENTE la estructura, los colores y el placeholder
{{{{OPENING_PARAGRAPH}}}} indicados arriba, pero reescribe el cuerpo así:
- Tono de recordatorio cordial. Nada de reproche ni de culpabilizar por no haber contestado.
- Una frase que deje claro que es un segundo contacto ("te escribí hace unas semanas sobre…").
- Un motivo NUEVO para retomar la conversación ahora (un dato, un caso, una mejora concreta de {service_name}).
- Cuerpo de 130 palabras como máximo, sin contar cabecera ni pie.

REGLA CRÍTICA SOBRE LA LLAMADA A LA ACCIÓN — INCUMPLIRLA ROMPE EL EMAIL:
- Debe haber EXACTAMENTE UN enlace/botón de "agendar reunión" en todo el email: el botón CTA.
- PROHIBIDO repetir el enlace de la reunión en el texto del cuerpo, en la despedida o en el pie.
- El único otro enlace permitido es el de baja ({{{{UNSUBSCRIBE_URL}}}}) y, si acaso, la política de privacidad.
- NO añadas bloques [PROMPT]…[/PROMPT]: la personalización por lead ya la aporta {{{{OPENING_PARAGRAPH}}}}.
"""
        return base + extra

    def _followup_prior_contact_context(self, lead):
        """Texto sobre el contacto anterior a este lead, para dar contexto a la IA.

        Busca la última línea de campaña ya enviada a este lead (la de
        `followup_source_campaign_id` si se indicó, o cualquiera si no).
        """
        self.ensure_one()
        Line = self.env['marketing.campaign.lead']
        domain = [
            ('lead_id', '=', lead.id),
            ('sent_at', '!=', False),
            ('campaign_id', '!=', self.id),
        ]
        if self.followup_source_campaign_id:
            domain.append(('campaign_id', '=', self.followup_source_campaign_id.id))
        prev = Line.search(domain, order='sent_at desc', limit=1)
        if not prev:
            return ('No hay registro del envío anterior en el sistema. Trátalo '
                    'como un segundo contacto genérico: "te escribí hace unas '
                    'semanas".')
        fecha = fields.Date.to_string(fields.Datetime.context_timestamp(
            self, prev.sent_at).date())
        return (f'Fecha del contacto anterior: {fecha}.\n'
                f'Campaña anterior: {prev.campaign_id.name}.\n'
                f'Propósito de aquel mensaje: {prev.campaign_id.purpose or "—"}')

    def _build_whatsapp_template_prompt(self, service_name, pain_context):
        return f"""{UNIASSER_BRAND_CONTEXT}

=== TAREA ===
Genera el texto de un mensaje WhatsApp Business para una campaña de marketing B2B.

CAMPAÑA:
- Propósito: {self.purpose}
- Servicio: {service_name}
- Público objetivo: {self.target_audience}

PUNTOS DE DOLOR POR SECTOR:
{pain_context or 'Sin reglas configuradas — usa el propósito de la campaña.'}

REQUISITOS:
1. Máximo 1000 caracteres.
2. Tono: profesional pero conversacional, como si fuera de persona a persona.
3. El primer párrafo es el de apertura personalizada — usa el placeholder {{{{OPENING_PARAGRAPH}}}} exactamente así.
4. Incluye una pregunta o CTA claro al final.
5. NO uses markdown excesivo (WhatsApp acepta *negrita* y _cursiva_ básica).
6. Al final incluye la opción de baja: "Si no deseas recibir más mensajes, responde BAJA."
7. Firma: Enric · Uniasser Consulting

Devuelve SOLO el texto del mensaje, sin explicaciones.
"""

    def _build_linkedin_template_prompt(self, service_name, pain_context):
        return f"""{UNIASSER_BRAND_CONTEXT}

=== TAREA ===
Genera una plantilla de mensaje para una campaña de LinkedIn (Prosp) B2B.

CAMPAÑA:
- Propósito: {self.purpose}
- Servicio: {service_name}
- Público objetivo: {self.target_audience}

PUNTOS DE DOLOR POR SECTOR (para el contexto; el párrafo de apertura será personalizado por lead):
{pain_context or 'Sin reglas configuradas — usa el propósito de la campaña.'}

REQUISITOS DEL MENSAJE DE LINKEDIN:
1. Debe ser corto, directo y sumamente profesional (máximo 800 caracteres).
2. Tono: profesional pero cercano, de persona a persona.
3. El primer párrafo es el de apertura personalizada — usa el placeholder {{{{OPENING_PARAGRAPH}}}} exactamente así.
4. Introduce de forma natural tu propuesta de valor ({service_name}).
5. Incluye una llamada a la acción (CTA) corta e informal para agendar una charla corta o preguntar si sufren ese dolor.
6. NO uses markdown excesivo ni firmas de correo largas.

Devuelve SOLO el texto completo del mensaje, sin explicaciones adicionales.
"""

    def _call_claude(self, api_key: str = '', prompt: str = '', contexto: str = '') -> str:
        """Genera texto con IA, conmutando de proveedor si uno falla.

        Mantiene el nombre y la firma antiguos porque hay 9 puntos de llamada
        repartidos por el módulo; así todos heredan el respaldo sin tocarlos.
        El parámetro `api_key` se ignora: las claves las resuelve el servicio,
        que necesita todas para poder conmutar.
        """
        texto = self.env['marketing.ai.service'].generar(prompt, contexto)
        # Quitar los bloques markdown que los modelos añaden a veces (```html …```)
        texto = re.sub(r'^```[a-z]*\s*', '', (texto or '').strip(), flags=re.IGNORECASE)
        texto = re.sub(r'\s*```$', '', texto.strip())
        return texto.strip()

    def _check_license(self):
        """Verifica si la licencia actual de Vantis CRM + Marketing es válida y no ha expirado.
        Si la licencia es inválida o ha expirado, lanza una excepción de Odoo.
        """
        icp = self.env['ir.config_parameter'].sudo()
        license_key = icp.get_param('crm_marketing_and_comunications.license_key', '').strip()

        if not license_key:
            raise UserError(
                'Falta la Licencia de Producto.\n'
                'Por favor, introduce tu clave de licencia en Ajustes ➔ Vantis CRM + Marketing.\n\n'
                '¿No tienes licencia? Puedes adquirir una en apps.odoo.com o solicitar una clave '
                'de prueba gratuita de 15 días enviando un email a info@uniasser.com.'
            )

        db_uuid = icp.get_param('database.uuid', '').strip()

        # Bypass de Propietario: si se usa la clave PRO vinculada al UUID de la base de datos o clave maestra
        if license_key == f"VANTIS-PRO-{db_uuid}" or license_key == "VANTIS-OWNER-MASTER-KEY-2026":
            return

        import urllib.request
        import json

        base_url = "https://vantis.uniasser.net"
        endpoint = f"{base_url}/api/v1/license/check"
        
        payload = {
            "license_key": license_key,
            "database_uuid": db_uuid,
            "module": "crm_marketing_and_comunications"
        }
        
        try:
            req = urllib.request.Request(
                endpoint,
                data=json.dumps(payload).encode(),
                headers={'Content-Type': 'application/json'},
                method='POST'
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                if not data.get('valid'):
                    raise UserError(
                        f'Licencia inválida: {data.get("message", "La clave de licencia no es válida para esta base de datos o ha expirado.")}\n\n'
                        'Por favor, introduce una clave válida o solicita una prueba de 15 días enviando un email a info@uniasser.com.'
                    )
        except UserError:
            raise
        except Exception as e:
            _logger.warning('[licencia] no se pudo conectar al servidor de licencias Vantis: %s. Tolerancia activa.', e)

    def action_approve_campaign(self):
        """Marca la campaña como aprobada y lista para envío."""
        self.ensure_one()
        self._check_license()
        self._ensure_channel_templates()
        if self.use_email:
            if not self.email_template_id:
                raise UserError(
                    'Este envío usa email pero no tiene ninguna plantilla de email.\n'
                    'Pulsa «Generar plantilla con IA», o crea/elige una en la '
                    'pestaña «Plantillas IA».'
                )
            if self.email_template_id.state != 'approved':
                raise UserError(
                    'La plantilla de email debe estar aprobada antes de aprobar la campaña.\n'
                    'Ábrela y pulsa «Aprobar».'
                )
        if self.use_whatsapp and self.whatsapp_template_id:
            if self.whatsapp_template_id.state != 'approved':
                raise UserError('La plantilla de WhatsApp debe estar aprobada antes de aprobar la campaña.')
        self.state = 'approved'
        self.message_post(body='Campaña aprobada. Lista para envío.')

    def action_close(self):
        self.state = 'closed'
        self.message_post(body='Campaña cerrada.')

    def action_reopen(self):
        """Devuelve la campaña a borrador.

        Antes no existía ninguna salida del estado 'closed': una campaña
        cerrada por error quedaba muerta, sin forma de recuperarla desde la
        interfaz. Cerrar debe ser reversible.

        No toca las líneas de campaña ni lo ya enviado: solo reabre la ficha
        para poder seguir trabajando (probar, ajustar plantilla, etc.).
        """
        self.ensure_one()
        anterior = dict(self._fields['state'].selection).get(self.state, self.state)
        self.state = 'draft'
        self.message_post(body=f'Campaña reabierta (estaba en «{anterior}»).')
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'marketing.campaign',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_view_campaign_leads(self):
        return {
            'type': 'ir.actions.act_window',
            'name': f'Leads — {self.name}',
            'res_model': 'marketing.campaign.lead',
            'view_mode': 'list,form',
            'domain': [('campaign_id', '=', self.id)],
            'context': {'default_campaign_id': self.id},
        }

    def action_view_campaign_replies(self):
        self.ensure_one()
        inbox_view_id = self.env.ref('crm_marketing_and_comunications.view_marketing_campaign_lead_inbox_list').id
        form_view_id = self.env.ref('crm_marketing_and_comunications.view_marketing_campaign_lead_form').id
        return {
            'type': 'ir.actions.act_window',
            'name': f'Respuestas — {self.name}',
            'res_model': 'marketing.campaign.lead',
            'view_mode': 'list,form',
            'domain': [('campaign_id', '=', self.id), ('last_inbound_message', '!=', False)],
            'context': {'default_campaign_id': self.id},
            'views': [(inbox_view_id, 'list'), (form_view_id, 'form')],
        }

    def action_view_ai_templates(self):
        return {
            'type': 'ir.actions.act_window',
            'name': f'Plantillas IA — {self.name}',
            'res_model': 'marketing.ai.template',
            'view_mode': 'list,form',
            'domain': [('campaign_id', '=', self.id)],
            'context': {'default_campaign_id': self.id},
        }

    def action_gmail_resync_leads(self):
        """Marca los leads de la campaña para que la sincronización automática
        de Gmail (cron cada 10 min) los procese en la próxima pasada.

        No sincroniza aquí mismo (94 leads × una búsqueda IMAP se comería el
        tiempo de la petición web): solo los pone al principio de la cola del
        cron, que ya usa la etiqueta de la campaña vía `_gmail_sync_folder`.
        """
        self.ensure_one()
        leads = self.campaign_lead_ids.mapped('lead_id').filtered('email_from')
        if not leads:
            raise UserError('Esta campaña no tiene leads con email.')
        leads.write({'gmail_last_sync': False})
        etiqueta = (self.gmail_sync_label or '').strip()
        destino = f'la etiqueta «{etiqueta}»' if etiqueta else 'todo el buzón / la etiqueta global'
        self.message_post(body=(
            f'{len(leads)} lead(s) marcados para re-sincronizar con Gmail ({destino}) '
            f'en la próxima pasada del proceso automático.'))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Marcados para sincronizar',
                'message': (f'{len(leads)} lead(s). El proceso en segundo plano '
                            f'(cada 10 min) los sincronizará usando {destino}.'),
                'type': 'success',
            },
        }

    def action_launch_in_prosp(self):
        """Activa el goteo en segundo plano para exportar leads a Prosp en lotes de 15
        con pausas de seguridad, evitando bloqueos de Cloudflare y rate limits.
        """
        self.ensure_one()
        self._check_license()
        if not self.use_linkedin:
            raise UserError('Esta campaña no tiene el canal LinkedIn (Prosp) seleccionado.')

        # Resguardo de seguridad: si el ID técnico está vacío pero seleccionaron una del desplegable
        if not self.prosp_campaign_id and self.prosp_campaign_helper_id:
            self.prosp_campaign_id = self.prosp_campaign_helper_id.prosp_id

        if not self.prosp_list_id and self.prosp_list_helper_id:
            self.prosp_list_id = self.prosp_list_helper_id.prosp_id

        if not self.prosp_campaign_id:
            raise UserError('Por favor, selecciona una campaña en el desplegable de Prosp (o introduce su ID de forma manual).')
        if not self.prosp_list_id:
            raise UserError('Por favor, selecciona una lista en el desplegable de Prosp (o introduce su ID de forma manual).')

        # Encontrar el perfil de LinkedIn para el usuario comercial actual
        profile = self.env['marketing.linkedin.profile'].search([
            ('user_id', '=', self.env.user.id),
            ('active', '=', True)
        ], limit=1)
        if not profile:
            raise UserError(
                'No tienes un perfil de LinkedIn configurado en Odoo.\n'
                'Por favor, crea tu perfil en Configuración → Perfiles de LinkedIn antes de lanzar.'
            )
        if not profile.prosp_api_key:
            raise UserError(
                f'El perfil de LinkedIn de {profile.name} no tiene configurada la Prosp API Key.\n'
                'Por favor, configúrala en Configuración → Perfiles de LinkedIn.'
            )

        # Buscar líneas con contenido aprobado y sin enviar
        lines = self.campaign_lead_ids.filtered(
            lambda l: l.content_approved and not l.sent_at and l.lead_id.x_linkedin_url
        )
        if not lines:
            raise UserError('No hay contactos aprobados y pendientes de enviar para esta campaña con URL de LinkedIn.')

        self.write({
            'prosp_drip_enabled': True,
            'state': 'sending'
        })

        # Activar el cron de goteo de Prosp de inmediato si está inactivo
        cron = self.env.ref('crm_marketing_and_comunications.ir_cron_prosp_drip', raise_if_not_found=False)
        if cron and not cron.active:
            cron.sudo().active = True

        self.message_post(body=(
            f'Activado goteo de lanzamiento a Prosp para {len(lines)} lead(s) aprobados. '
            f'Se enviarán en lotes automáticos de 15 cada 10 minutos para cumplir con los límites de seguridad.'
        ))

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Goteo Prosp Activado',
                'message': f'La cola se procesará en segundo plano. Lotes automáticos de 15 leads cada 10 min.',
                'type': 'success',
                'sticky': False,
            }
        }

    @api.model
    def _cron_prosp_drip(self):
        """Punto de entrada del cron: recorre las campañas con goteo Prosp activo."""
        campanas = self.search([('prosp_drip_enabled', '=', True), ('state', '=', 'sending')])
        if not campanas:
            # Apagar cron si no hay campañas activas para ahorrar recursos
            cron = self.env.ref('crm_marketing_and_comunications.ir_cron_prosp_drip', raise_if_not_found=False)
            if cron and cron.active:
                cron.sudo().active = False
            return
        for campaign in campanas:
            try:
                campaign._prosp_drip_process_batch()
            except Exception:
                _logger.exception('[goteo-prosp] error en campaña %s', campaign.name)

    def _prosp_drip_process_batch(self):
        """Envía un lote de hasta 15 leads a la API de Prosp."""
        self.ensure_one()
        profile = self.env['marketing.linkedin.profile'].search([
            ('user_id', '=', self.create_uid.id),
            ('active', '=', True)
        ], limit=1)
        if not profile or not profile.prosp_api_key:
            _logger.warning('[goteo-prosp] campaña %s sin perfil o API Key de LinkedIn activa', self.name)
            self.prosp_drip_enabled = False
            return False

        # Lote de 15 leads aprobados y sin enviar
        lines = self.campaign_lead_ids.filtered(
            lambda l: l.content_approved and not l.sent_at and l.lead_id.x_linkedin_url
        )[:15]

        if not lines:
            # Fin del goteo
            self.write({
                'prosp_drip_enabled': False,
                'state': 'active'
            })
            self.message_post(body='Lanzamiento de campaña en Prosp finalizado con éxito (todas las líneas enviadas).')
            return False

        import urllib.request
        import urllib.error
        import json
        import re
        import time
        import random

        base_url = self.env['ir.config_parameter'].sudo().get_param(
            'crm_marketing_and_comunications.prosp_api_base_url',
            'https://prosp.ai/api'
        ).rstrip('/')

        endpoint = f"{base_url}/v1/leads"
        success_count = 0

        for line in lines:
            # Pausa aleatoria corta (0.3 a 0.8 segundos) entre peticiones individuales para emular comportamiento humano
            time.sleep(random.uniform(0.3, 0.8))

            html_content = line.personalized_content or ''
            plain_message = re.sub(r'<[^>]*>', '', html_content).strip()

            payload = {
                "api_key": profile.prosp_api_key,
                "linkedin_url": line.lead_id.x_linkedin_url,
                "list_id": self.prosp_list_id,
                "campaign_id": self.prosp_campaign_id,
                "data": [
                    {"property": "personalized_message", "value": plain_message},
                    {"property": "first_name", "value": line.lead_id.name.split(' ')[0] if line.lead_id.name else ''},
                    {"property": "company_name", "value": line.lead_id.partner_name or ''}
                ]
            }

            request = urllib.request.Request(
                endpoint,
                data=json.dumps(payload, default=str).encode(),
                headers={
                    'Content-Type': 'application/json',
                    'Authorization': f'Bearer {profile.prosp_api_key}',
                    'Accept': 'application/json',
                },
                method='POST',
            )
            try:
                with urllib.request.urlopen(request, timeout=15) as response:
                    line.write({'sent_at': fields.Datetime.now()})
                    success_count += 1
                    self.env.cr.commit()
            except Exception as exc:
                _logger.error('[goteo-prosp] error en linea %s (lead %s): %s', line.id, line.lead_id.name, exc)

        _logger.info('[goteo-prosp] campaña %s: enviados %d leads en este lote', self.name, success_count)
        return True

    def action_sync_prosp_campaigns(self):
        """Conecta a Prosp, obtiene todas las listas y campañas, y las guarda
        localmente en Odoo para poder seleccionarlas cómodamente sin copiar IDs.
        """
        self.ensure_one()
        profile = self.env['marketing.linkedin.profile'].search([
            ('user_id', '=', self.env.user.id),
            ('active', '=', True)
        ], limit=1)
        if not profile or not profile.prosp_api_key:
            raise UserError('Por favor, configura primero tu Prosp API Key en tu perfil de LinkedIn.')

        import urllib.request
        import json

        url = "https://mcp.prosp.ai/mcp"
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json, text/event-stream',
            'Authorization': f'Bearer {profile.prosp_api_key}'
        }

        # 1. Sincronizar Campañas
        payload_campaigns = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "list_campaigns",
                "arguments": {}
            }
        }
        campaigns = []
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload_campaigns).encode(),
                headers=headers,
                method='POST'
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw_body = resp.read().decode()
                for line in raw_body.split('\n'):
                    if line.startswith('data: '):
                        json_data = json.loads(line[6:])
                        text_content = json_data.get('result', {}).get('content', [{}])[0].get('text', '')
                        if text_content:
                            campaign_info = json.loads(text_content)
                            campaigns = campaign_info.get('data', [])
                            break
                
                ProspCampaignObj = self.env['marketing.prosp.campaign']
                ProspCampaignObj.search([('profile_id', '=', profile.id)]).unlink()
                for c in campaigns:
                    ProspCampaignObj.create({
                        'name': c.get('campaign_name') or c.get('name') or 'Sin nombre',
                        'prosp_id': c.get('campaign_id') or c.get('id') or '',
                        'profile_id': profile.id,
                    })
        except Exception as e:
            raise UserError(f'Error al obtener campañas de Prosp:\n{e}')

        # 2. Sincronizar Listas de Contactos
        payload_lists = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "list_contact_lists",
                "arguments": {}
            }
        }
        contact_lists = []
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload_lists).encode(),
                headers=headers,
                method='POST'
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw_body = resp.read().decode()
                for line in raw_body.split('\n'):
                    if line.startswith('data: '):
                        json_data = json.loads(line[6:])
                        text_content = json_data.get('result', {}).get('content', [{}])[0].get('text', '')
                        if text_content:
                            list_info = json.loads(text_content)
                            contact_lists = list_info.get('data', [])
                            break
                
                ProspListObj = self.env['marketing.prosp.list']
                ProspListObj.search([('profile_id', '=', profile.id)]).unlink()
                for l in contact_lists:
                    ProspListObj.create({
                        'name': l.get('name') or l.get('list_name') or l['id'],
                        'prosp_id': l['id'],
                        'profile_id': profile.id,
                    })
        except Exception as e:
            raise UserError(f'Error al obtener listas de Prosp:\n{e}')

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Opciones Sincronizadas',
                'message': f'Se han cargado {len(campaigns)} campañas y {len(contact_lists)} listas desde Prosp con éxito. Ya puedes elegirlas.',
                'type': 'success',
                'sticky': False,
            }
        }
