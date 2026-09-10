"""Wizard de creación de campaña — Fase A del flujo funcional."""
import logging

from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


class CampaignCreateWizard(models.TransientModel):
    """Wizard para crear o ampliar una campaña de marketing.

    Pasos:
      1. Seleccionar etapa CRM → listar leads disponibles
      2. Marcar los leads a incluir
      3. Rellenar brief: propósito, servicio, público objetivo
      4. Elegir canales, ESP y mapeo de etapas de destino
      5. Crear la campaña (o añadir leads a una existente)
    """

    _name = 'campaign.create.wizard'
    _description = 'Wizard: Crear campaña de marketing'

    # ── Paso 1: etapa fuente ──────────────────────────────────────────────────
    stage_source_id = fields.Many2one(
        'crm.stage',
        string='Etapa CRM de origen',
        required=True,
        help='Los leads en esta etapa se listarán para selección.',
    )
    available_lead_ids = fields.Many2many(
        'crm.lead',
        'campaign_wizard_available_lead_rel',
        'wizard_id', 'lead_id',
        string='Leads disponibles en la etapa',
        compute='_compute_available_leads',
    )
    selected_lead_ids = fields.Many2many(
        'crm.lead',
        'campaign_wizard_selected_lead_rel',
        'wizard_id', 'lead_id',
        string='Leads a incluir en la campaña',
        help='Marca los leads que entrarán en esta campaña.',
    )
    selected_lead_count = fields.Integer(
        string='Leads seleccionados',
        compute='_compute_selected_lead_count',
    )

    # ── Paso 2: brief ─────────────────────────────────────────────────────────
    campaign_name = fields.Char(
        string='Nombre de la campaña',
        required=True,
    )
    purpose = fields.Text(
        string='Propósito de la campaña',
        required=True,
        help='¿Qué quieres conseguir? Ej: "5 demos de wa-manager con agencias de BCN".',
    )
    service_id = fields.Many2one(
        'product.template',
        string='Producto / Servicio a vender',
    )
    target_audience = fields.Text(
        string='Público objetivo',
        required=True,
        help='Describe el segmento: sector, tamaño, rol del decisor, dolor típico.',
    )

    # ── Paso 3: canales ───────────────────────────────────────────────────────
    channel_ids = fields.Many2many(
        'marketing.channel',
        'campaign_wizard_channel_rel',
        'wizard_id', 'channel_id',
        string='Canales',
        domain="[('available','=',True)]",
    )
    esp_provider_id = fields.Many2one(
        'marketing.esp.provider',
        string='Proveedor ESP (email)',
        help='Requerido si se usa el canal email.',
    )
    use_email = fields.Boolean(compute='_compute_channels')
    use_whatsapp = fields.Boolean(compute='_compute_channels')

    # ── Paso 4: mapeo de etapas de destino ────────────────────────────────────
    stage_interested_id = fields.Many2one(
        'crm.stage',
        string='Si muestra interés → mover a',
    )
    stage_demo_id = fields.Many2one(
        'crm.stage',
        string='Si solicita demo → mover a',
    )
    stage_lost_id = fields.Many2one(
        'crm.stage',
        string='Si rechaza / pide baja → mover a',
    )

    # ── De dónde sale la plantilla ────────────────────────────────────────────
    #
    # Antes no se preguntaba: al crear la campaña se pasaba directo a generar con
    # IA, y quien ya tenía una plantilla afinada no tenía forma de reutilizarla.

    template_mode = fields.Selection([
        ('ai', 'Generar una nueva con IA'),
        ('copy', 'Copiar la de otra campaña'),
        ('crm', 'Importar una plantilla de email del CRM'),
        ('later', 'Decidirlo después'),
    ], string='Plantilla', default='ai', required=True)
    source_template_id = fields.Many2one(
        'marketing.ai.template', string='Plantilla a copiar',
        domain="[('channel','=','email')]",
        help='Se copia a esta campaña. El original no se toca.',
    )
    source_template_wa_id = fields.Many2one(
        'marketing.ai.template', string='Plantilla WhatsApp a copiar',
        domain="[('channel','=','whatsapp')]",
    )
    source_crm_template_id = fields.Many2one(
        'crm.email.template', string='Plantilla del CRM',
        help='Las de CRM → Configuración → Plantillas de email. Se convierte a '
             'plantilla de campaña conservando marcadores y bloques [PROMPT].',
    )

    # ── Modo: nueva campaña o añadir a existente ──────────────────────────────
    mode = fields.Selection([
        ('new', 'Nueva campaña'),
        ('add_to_existing', 'Añadir leads a campaña existente'),
    ], string='Modo', default='new', required=True)
    existing_campaign_id = fields.Many2one(
        'marketing.campaign',
        string='Campaña existente',
        domain="[('state','in',('draft','approved'))]",
    )

    @api.model
    def default_get(self, fields_list):
        """Recoge los leads marcados en la lista de la que se abre el asistente.

        Sin esto, seleccionar leads y lanzar «crear campaña» los perdía por el
        camino: el asistente solo sabía mirar una etapa, así que el usuario veía
        su selección desaparecer y el botón fallaba con «Debes seleccionar al
        menos un lead».
        """
        vals = super().default_get(fields_list)
        contexto = self.env.context
        if contexto.get('active_model') == 'crm.lead' and contexto.get('active_ids'):
            leads = self.env['crm.lead'].browse(contexto['active_ids']).exists()
            if leads:
                vals.setdefault('selected_lead_ids', [(6, 0, leads.ids)])
                # Si todos vienen de la misma etapa, se propone esa; si vienen de
                # varias no se elige ninguna, porque cualquiera sería mentira.
                etapas = leads.mapped('stage_id')
                if len(etapas) == 1 and not vals.get('stage_source_id'):
                    vals['stage_source_id'] = etapas.id
        return vals

    # ── Computed ──────────────────────────────────────────────────────────────

    @api.depends('stage_source_id', 'selected_lead_ids')
    def _compute_available_leads(self):
        """Leads que se pueden marcar: los de la etapa MÁS los ya seleccionados.

        La unión importa: si el usuario llega con leads marcados desde la lista y
        esos leads están en otra etapa, sin sumarlos aquí desaparecerían de la
        tabla — el `domain` de la vista solo deja marcar lo que esté en esta
        lista — y parecería que el asistente ha perdido su selección.
        """
        for rec in self:
            leads = self.env['crm.lead']
            if rec.stage_source_id:
                leads = self.env['crm.lead'].search([
                    ('stage_id', '=', rec.stage_source_id.id),
                    ('active', '=', True),
                    ('probability', '!=', 0),  # excluir perdidos
                ])
            rec.available_lead_ids = leads | rec.selected_lead_ids

    @api.depends('selected_lead_ids')
    def _compute_selected_lead_count(self):
        for rec in self:
            rec.selected_lead_count = len(rec.selected_lead_ids)

    @api.depends('channel_ids', 'channel_ids.code')
    def _compute_channels(self):
        for rec in self:
            codes = rec.channel_ids.mapped('code')
            rec.use_email = 'email' in codes
            rec.use_whatsapp = 'whatsapp' in codes

    # ── Onchange ──────────────────────────────────────────────────────────────

    @api.onchange('stage_source_id')
    def _onchange_stage_source(self):
        """Al cambiar de etapa se conservan los leads traídos desde la lista.

        Antes esto vaciaba la selección entera. Quien venía con leads marcados y
        luego elegía la etapa origen los perdía sin aviso, y «Crear campaña»
        fallaba sin explicar por qué.
        """
        traidos = set(self.env.context.get('active_ids') or [])
        if traidos:
            # Asignación por recordset, no por comandos (6,0,…): en un registro
            # virtual de onchange los comandos no se aplican igual y la selección
            # acababa vacía de todos modos.
            def _id_real(reg):
                # En un onchange los ids pueden ser NewId; el id de verdad está
                # en `.origin`.
                return getattr(reg.id, 'origin', None) or reg.id

            self.selected_lead_ids = self.selected_lead_ids.filtered(
                lambda l: _id_real(l) in traidos)
        else:
            self.selected_lead_ids = False

    @api.onchange('mode')
    def _onchange_mode(self):
        if self.mode == 'add_to_existing':
            self.campaign_name = False

    @api.onchange('existing_campaign_id')
    def _onchange_existing_campaign(self):
        """Precarga la etapa origen de la campaña existente."""
        if self.existing_campaign_id:
            campaign = self.existing_campaign_id
            self.stage_source_id = campaign.stage_source_id
            self.purpose = campaign.purpose
            self.service_id = campaign.service_id
            self.target_audience = campaign.target_audience

    # ── Acciones ──────────────────────────────────────────────────────────────

    def action_select_all_leads(self):
        """Selecciona todos los leads disponibles en la etapa."""
        self.ensure_one()
        self.selected_lead_ids = self.available_lead_ids

    def action_clear_selection(self):
        self.ensure_one()
        self.selected_lead_ids = False

    def _validate(self):
        if not self.selected_lead_ids:
            raise ValidationError(
                'No hay ningún lead marcado para la campaña.\n\n'
                'Marca las casillas de la tabla «Leads», o pulsa «Seleccionar '
                'todos» para incluir los de la etapa elegida.'
                + ('\n\nNo hay leads disponibles en esa etapa: prueba con otra.'
                   if not self.available_lead_ids else '')
            )
        if not self.channel_ids:
            raise ValidationError('Debes seleccionar al menos un canal (Email o WhatsApp).')
        if self.use_email and not self.esp_provider_id:
            raise ValidationError('Si usas el canal Email, debes seleccionar un proveedor ESP.')
        if self.mode == 'new' and not self.campaign_name:
            raise ValidationError('Introduce un nombre para la campaña.')
        if self.mode == 'add_to_existing' and not self.existing_campaign_id:
            raise ValidationError('Selecciona la campaña a la que añadir los leads.')
        if self.mode == 'new':
            if self.template_mode == 'copy' and not (
                    self.source_template_id or self.source_template_wa_id):
                raise ValidationError(
                    'Has elegido copiar una plantilla existente, pero no has '
                    'indicado cuál.')
            if self.template_mode == 'crm' and not self.source_crm_template_id:
                raise ValidationError(
                    'Has elegido importar una plantilla del CRM, pero no has '
                    'indicado cuál.')

    def action_create_campaign(self):
        """Crea la campaña (o amplía la existente) y abre la vista."""
        self.ensure_one()
        self._validate()

        if self.mode == 'new':
            campaign = self.env['marketing.campaign'].create({
                'name': self.campaign_name,
                'stage_source_id': self.stage_source_id.id if self.stage_source_id else False,
                'lead_ids': [(6, 0, self.selected_lead_ids.ids)],
                'purpose': self.purpose,
                'service_id': self.service_id.id if self.service_id else False,
                'target_audience': self.target_audience,
                'channel_ids': [(6, 0, self.channel_ids.ids)],
                'esp_provider_id': self.esp_provider_id.id if self.esp_provider_id else False,
                'stage_interested_id': self.stage_interested_id.id if self.stage_interested_id else False,
                'stage_demo_id': self.stage_demo_id.id if self.stage_demo_id else False,
                'stage_lost_id': self.stage_lost_id.id if self.stage_lost_id else False,
            })
        else:
            campaign = self.existing_campaign_id
            new_lead_ids = self.selected_lead_ids.ids
            current_ids = campaign.lead_ids.ids
            combined = list(set(current_ids + new_lead_ids))
            campaign.write({'lead_ids': [(6, 0, combined)]})

        # Generar líneas de campaña automáticamente
        campaign.action_generate_campaign_leads()

        # Plantilla: copiar la elegida antes de que nadie pulse «generar con IA»
        if self.mode == 'new' and self.template_mode in ('copy', 'crm'):
            if self.template_mode == 'copy':
                if self.source_template_id and self.use_email:
                    campaign.importar_plantilla('email',
                                                plantilla_ia=self.source_template_id)
                if self.source_template_wa_id and self.use_whatsapp:
                    campaign.importar_plantilla('whatsapp',
                                                plantilla_ia=self.source_template_wa_id)
            else:
                if self.use_email:
                    campaign.importar_plantilla(
                        'email', plantilla_crm=self.source_crm_template_id)
                if self.use_whatsapp:
                    campaign.importar_plantilla(
                        'whatsapp', plantilla_crm=self.source_crm_template_id)

        return {
            'type': 'ir.actions.act_window',
            'name': campaign.name,
            'res_model': 'marketing.campaign',
            'res_id': campaign.id,
            'view_mode': 'form',
            'target': 'current',
        }
