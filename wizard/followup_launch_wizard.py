"""Wizard: lanzar un seguimiento (re-enganche) sobre los leads de una etapa.

Crea una `marketing.campaign` de tipo `followup`, que reutiliza tal cual el
sistema de plantillas ([PROMPT]…[/PROMPT], placeholders) y la lógica de envío
(goteo / Amazon SES) de las campañas normales. Lo único propio del seguimiento
es el enfoque del mensaje: recordar un contacto anterior sin respuesta y
proponer una reunión.
"""
import logging

from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class MarketingFollowupLaunch(models.TransientModel):
    _name = 'marketing.followup.launch'
    _description = 'Wizard: lanzar seguimiento por etapa'

    name = fields.Char(
        string='Nombre del seguimiento', required=True,
        default=lambda self: self._default_name(),
    )
    stage_id = fields.Many2one(
        'crm.stage', string='Etapa CRM a seguir', required=True,
        help='Se seguirá a los leads activos de esta etapa que cumplan los '
             'filtros de abajo.',
    )
    service_id = fields.Many2one('product.template', string='Producto / Servicio')
    purpose = fields.Text(
        string='Objetivo del seguimiento', required=True,
        default='Reactivar a los leads que no respondieron a nuestro contacto '
                'anterior y cerrar una reunión breve.',
    )
    target_audience = fields.Text(
        string='Público objetivo', required=True,
        default='Leads que ya recibieron un mensaje nuestro y no han respondido.',
    )
    esp_provider_id = fields.Many2one(
        'marketing.esp.provider', string='Proveedor ESP (email)', required=True,
    )
    source_campaign_id = fields.Many2one(
        'marketing.campaign', string='Campaña de origen (opcional)',
        domain="[('campaign_type','=','outreach')]",
        help='La campaña previa cuyo mensaje no obtuvo respuesta. Solo se usa '
             'para dar contexto a la IA (fecha y propósito del contacto '
             'anterior).',
    )

    # ── De dónde sale la plantilla ───────────────────────────────────────────
    template_mode = fields.Selection([
        ('ai', 'Generarla después con IA (plantilla de seguimiento)'),
        ('copy', 'Copiar una plantilla de email existente'),
    ], string='Plantilla', default='ai', required=True)
    source_template_id = fields.Many2one(
        'marketing.ai.template', string='Plantilla a copiar',
        domain="[('channel','=','email')]",
        help='Se copia al seguimiento: editarla aquí no toca el original.',
    )

    # ── Filtros de a quién seguir ────────────────────────────────────────────
    only_no_reply = fields.Boolean(
        string='Solo leads sin respuesta previa', default=True,
        help='Excluye los leads que ya han respondido a cualquier campaña o '
             'seguimiento anterior.',
    )
    skip_recent_days = fields.Integer(
        string='No seguir si se contactó en los últimos (días)', default=30,
        help='Evita reescribir a quien recibió un email nuestro hace poco. '
             '0 = sin este filtro.',
    )

    # ── Mapeo de etapas de respuesta ─────────────────────────────────────────
    stage_interested_id = fields.Many2one('crm.stage', string='Si muestra interés → mover a')
    stage_demo_id = fields.Many2one('crm.stage', string='Si pide reunión → mover a')
    stage_lost_id = fields.Many2one('crm.stage', string='Si rechaza o pide baja → mover a')

    candidate_count = fields.Integer(
        string='Leads candidatos', compute='_compute_candidate_count',
    )

    @api.model
    def _default_name(self):
        return 'Seguimiento %s' % fields.Date.to_string(fields.Date.context_today(self))

    @api.depends('stage_id', 'only_no_reply', 'skip_recent_days')
    def _compute_candidate_count(self):
        for rec in self:
            rec.candidate_count = len(rec._candidate_leads())

    def _candidate_leads(self):
        """Leads de la etapa que se van a seguir, tras aplicar los filtros."""
        self.ensure_one()
        if not self.stage_id:
            return self.env['crm.lead']

        leads = self.env['crm.lead'].search([
            ('stage_id', '=', self.stage_id.id),
            ('active', '=', True),
            ('probability', '!=', 0),  # excluir perdidos
        ])
        # El seguimiento va solo por email.
        leads = leads.filtered(lambda l: (l.email_from or '').strip())
        if not leads:
            return leads

        Line = self.env['marketing.campaign.lead']
        if self.only_no_reply:
            ya_respondieron = Line.search([
                ('lead_id', 'in', leads.ids),
                ('replied_at', '!=', False),
            ]).mapped('lead_id')
            leads -= ya_respondieron

        if self.skip_recent_days and self.skip_recent_days > 0 and leads:
            corte = fields.Datetime.subtract(
                fields.Datetime.now(), days=self.skip_recent_days)
            recientes = Line.search([
                ('lead_id', 'in', leads.ids),
                ('sent_at', '>=', corte),
            ]).mapped('lead_id')
            leads -= recientes

        return leads

    def action_launch(self):
        """Crea el seguimiento como campaña de tipo `followup` y abre su ficha."""
        self.ensure_one()
        if self.template_mode == 'copy' and not self.source_template_id:
            raise UserError('Elige la plantilla a copiar o cambia a '
                            '«Generarla después con IA».')

        leads = self._candidate_leads()
        if not leads:
            raise UserError(
                'No hay ningún lead que seguir en la etapa «%s» con los filtros '
                'actuales. Prueba a desmarcar «Solo leads sin respuesta previa» '
                'o a bajar los días del filtro de contacto reciente.'
                % self.stage_id.name
            )

        channel_email = self.env.ref(
            'crm_marketing_and_comunications.channel_email', raise_if_not_found=False)
        if not channel_email:
            raise UserError('No se encuentra el canal «Email Marketing». '
                            'Revisa la configuración de canales de marketing.')

        campaign = self.env['marketing.campaign'].create({
            'name': self.name,
            'campaign_type': 'followup',
            'stage_source_id': self.stage_id.id,
            'followup_source_campaign_id': self.source_campaign_id.id or False,
            'lead_ids': [(6, 0, leads.ids)],
            'purpose': self.purpose,
            'service_id': self.service_id.id or False,
            'target_audience': self.target_audience,
            'channel_ids': [(6, 0, channel_email.ids)],
            'esp_provider_id': self.esp_provider_id.id,
            'stage_interested_id': self.stage_interested_id.id or False,
            'stage_demo_id': self.stage_demo_id.id or False,
            'stage_lost_id': self.stage_lost_id.id or False,
        })

        campaign.action_generate_campaign_leads()

        plantilla_nota = ''
        if self.template_mode == 'copy':
            campaign.importar_plantilla('email', plantilla_ia=self.source_template_id)
            plantilla_nota = 'Plantilla copiada y enlazada: revísala y apruébala.'
        else:
            # Generar aquí mismo: así el seguimiento queda con plantilla enlazada
            # sin que el usuario tenga que acordarse de pulsar otro botón. Si la
            # IA falla, el seguimiento se crea igual y se avisa de cómo seguir.
            try:
                campaign.action_generate_ai_template()
                plantilla_nota = ('Plantilla de seguimiento generada con IA y '
                                  'enlazada: revísala y apruébala.')
            except Exception as e:  # noqa: BLE001
                _logger.warning('Seguimiento %s: no se pudo generar la plantilla: %s',
                                campaign.id, e)
                plantilla_nota = ('No se pudo generar la plantilla automáticamente '
                                  '(%s). Ábrela y pulsa «Generar plantilla con IA».'
                                  % e)

        campaign.message_post(body=(
            'Seguimiento creado desde la etapa «%s»: %d lead(s) seleccionados. '
            '%s Después: «✨ Generar contenido IA (todos)», «✅ Aprobar todo el '
            'contenido» y lanzar el envío.'
            % (self.stage_id.name, len(leads), plantilla_nota)
        ))

        return {
            'type': 'ir.actions.act_window',
            'name': campaign.name,
            'res_model': 'marketing.campaign',
            'res_id': campaign.id,
            'view_mode': 'form',
            'target': 'current',
        }
