"""Traer a una campaña una plantilla ya hecha.

El asistente de creación permite elegir el origen, pero solo en el momento de
crear. Quien ya tenía la campaña hecha —o cambia de idea después de generar una
plantilla con IA que no convence— se quedaba sin salida: no había forma de
importar sin borrar la campaña y empezar de nuevo.
"""

from odoo import api, fields, models
from odoo.exceptions import UserError


class MarketingTemplateImport(models.TransientModel):
    _name = 'marketing.template.import'
    _description = 'Importar plantilla a una campaña'

    campaign_id = fields.Many2one('marketing.campaign', required=True, readonly=True)
    canal = fields.Selection([
        ('email', 'Email'),
        ('whatsapp', 'WhatsApp'),
    ], string='Canal', required=True, default='email')

    origen = fields.Selection([
        ('copy', 'Copiar la de otra campaña'),
        ('crm', 'Importar una plantilla de email del CRM'),
    ], string='Origen', required=True, default='crm')

    source_template_id = fields.Many2one(
        'marketing.ai.template', string='Plantilla a copiar',
        help='De cualquier campaña. Se copia: el original no se toca.')
    source_crm_template_id = fields.Many2one(
        'crm.email.template', string='Plantilla del CRM')

    aviso = fields.Html(compute='_compute_aviso')

    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        if self.env.context.get('active_model') == 'marketing.campaign':
            vals['campaign_id'] = self.env.context.get('active_id')
        return vals

    @api.depends('campaign_id', 'canal')
    def _compute_aviso(self):
        for w in self:
            campana = w.campaign_id
            actual = (campana.email_template_id if w.canal == 'email'
                      else campana.whatsapp_template_id)
            if actual:
                # Sustituir una plantilla ya hecha no es inocuo: si estaba
                # aprobada, la campaña vuelve a revisión.
                w.aviso = (
                    f'<b>Esta campaña ya tiene plantilla de {w.canal}</b> '
                    f'(«{actual.display_name}», {dict(actual._fields["state"].selection).get(actual.state)}).<br/>'
                    'Al importar se crea otra y pasa a ser la de la campaña. '
                    'La anterior no se borra: seguirá accesible desde Plantillas IA.'
                )
            else:
                w.aviso = (f'La campaña no tiene plantilla de {w.canal} todavía. '
                           'La importada quedará en <b>borrador</b> para que la revises.')

    def action_importar(self):
        self.ensure_one()
        if self.origen == 'copy' and not self.source_template_id:
            raise UserError('Indica la plantilla que quieres copiar.')
        if self.origen == 'crm' and not self.source_crm_template_id:
            raise UserError('Indica la plantilla del CRM que quieres importar.')
        if self.origen == 'copy' and self.source_template_id.channel != self.canal:
            raise UserError(
                f'La plantilla elegida es de canal «{self.source_template_id.channel}» '
                f'y estás importando a «{self.canal}». Elige una del mismo canal.')

        self.campaign_id.importar_plantilla(
            canal=self.canal,
            plantilla_ia=self.source_template_id if self.origen == 'copy' else None,
            plantilla_crm=self.source_crm_template_id if self.origen == 'crm' else None,
        )
        # Si la campaña estaba aprobada, la plantilla nueva está sin revisar:
        # volver a revisión evita lanzar un envío con algo que nadie ha mirado.
        if self.campaign_id.state == 'approved':
            self.campaign_id.state = 'template_review'
        return {'type': 'ir.actions.act_window',
                'res_model': 'marketing.campaign',
                'res_id': self.campaign_id.id,
                'view_mode': 'form', 'target': 'current'}
