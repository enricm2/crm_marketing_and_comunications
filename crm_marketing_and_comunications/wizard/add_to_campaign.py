"""Asistente para añadir leads a una campaña desde la lista del CRM.

Se invoca desde Acciones → «Añadir a campaña» con los leads seleccionados.
Permite elegir una campaña existente o crear una nueva sobre la marcha, y
avisa por adelantado de cuántos leads NO se van a añadir y por qué, en vez de
añadirlos y que fallen luego en el envío.
"""

import logging

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class MarketingAddToCampaign(models.TransientModel):
    _name = 'marketing.add.to.campaign'
    _description = 'Añadir leads a una campaña'

    modo = fields.Selection([
        ('existente', 'Añadir a una campaña existente'),
        ('nueva', 'Crear una campaña nueva'),
    ], string='Qué hacer', default='existente', required=True)

    campaign_id = fields.Many2one(
        'marketing.campaign', string='Campaña',
        domain="[('state', 'not in', ('closed',))]",
        help='Solo se listan campañas no cerradas.',
    )
    nombre_nueva = fields.Char(string='Nombre de la campaña nueva')
    purpose_nueva = fields.Text(
        string='Propósito',
        help='Qué se busca con la campaña. La IA lo usa para redactar.',
    )
    target_nueva = fields.Text(string='Público objetivo')
    service_nueva = fields.Many2one('product.template', string='Servicio a vender')

    lead_ids = fields.Many2many('crm.lead', string='Leads seleccionados')

    # ── Recuento y avisos ─────────────────────────────────────────────────────
    total_seleccionados = fields.Integer(compute='_compute_resumen')
    n_validos = fields.Integer(string='Se añadirán', compute='_compute_resumen')
    n_sin_email = fields.Integer(string='Sin email', compute='_compute_resumen')
    n_excluidos = fields.Integer(string='Excluidos por categoría', compute='_compute_resumen')
    n_ya_estan = fields.Integer(string='Ya en la campaña', compute='_compute_resumen')
    resumen = fields.Html(string='Resumen', compute='_compute_resumen')

    @api.depends('lead_ids', 'campaign_id', 'modo')
    def _compute_resumen(self):
        for w in self:
            leads = w.lead_ids
            w.total_seleccionados = len(leads)

            ya = self.env['crm.lead']
            if w.modo == 'existente' and w.campaign_id:
                ya = self.env['marketing.campaign.lead'].search([
                    ('campaign_id', '=', w.campaign_id.id),
                    ('lead_id', 'in', leads.ids),
                ]).mapped('lead_id')

            pendientes = leads - ya
            # Leads con baja de marketing / RGPD
            bajas = pendientes.filtered(lambda l: l.marketing_opt_out)
            # Un lead excluido por categoría no debe entrar en una campaña:
            # es justo lo que se quiere evitar con "Clientes a excluir".
            excluidos = (pendientes - bajas).filtered(
                lambda l: getattr(l, 'x_linkedin_is_excluded', False))
            sin_email = (pendientes - excluidos - bajas).filtered(
                lambda l: not (l.email_from or '').strip())
            validos = pendientes - excluidos - sin_email - bajas

            w.n_ya_estan = len(ya)
            w.n_excluidos = len(excluidos)
            w.n_sin_email = len(sin_email)
            w.n_validos = len(validos)

            partes = [f'<b>{len(validos)}</b> lead(s) se añadirán a la campaña.']
            if ya:
                partes.append(f'{len(ya)} ya estaban y se omiten.')
            if bajas:
                partes.append(
                    f'<span style="color:#c0392b"><b>{len(bajas)}</b> han solicitado baja de marketing (baja/RGPD) y NO se añaden</span>.'
                )
            if excluidos:
                nombres = ', '.join(
                    (l.partner_name or l.name or '?') for l in excluidos[:4])
                if len(excluidos) > 4:
                    nombres += f' y {len(excluidos) - 4} más'
                partes.append(
                    f'<span style="color:#c0392b"><b>{len(excluidos)}</b> están en una '
                    f'categoría de «Clientes a excluir» y NO se añaden</span>: {nombres}. '
                    'Si alguno debe entrar, marca «Excepción: no excluir» en su ficha.')
            if sin_email:
                partes.append(
                    f'<span style="color:#d68910"><b>{len(sin_email)}</b> no tienen email '
                    'y no se añaden</span> (la campaña envía por email).')
            w.resumen = '<br/>'.join(partes)

    # ── Acción ────────────────────────────────────────────────────────────────

    def action_anadir(self):
        self.ensure_one()

        if self.modo == 'nueva':
            if not (self.nombre_nueva or '').strip():
                raise UserError('Pon un nombre a la campaña nueva.')
            campana = self.env['marketing.campaign'].create({
                'name': self.nombre_nueva.strip(),
                # purpose y target_audience son obligatorios en el modelo:
                # se rellenan con un texto de partida editable, no vacío.
                'purpose': (self.purpose_nueva or '').strip()
                           or 'Pendiente de definir.',
                'target_audience': (self.target_nueva or '').strip()
                                   or 'Pendiente de definir.',
                'service_id': self.service_nueva.id if self.service_nueva else False,
            })
        else:
            if not self.campaign_id:
                raise UserError('Elige la campaña a la que añadir los leads.')
            campana = self.campaign_id

        if not self.n_validos:
            raise UserError(
                'No hay ningún lead que añadir.\n\n'
                f'De los {self.total_seleccionados} seleccionados: '
                f'{self.n_ya_estan} ya estaban, {self.n_excluidos} están excluidos '
                f'por categoría y {self.n_sin_email} no tienen email.'
            )

        ya = self.env['marketing.campaign.lead'].search([
            ('campaign_id', '=', campana.id),
            ('lead_id', 'in', self.lead_ids.ids),
        ]).mapped('lead_id')
        pendientes = self.lead_ids - ya
        validos = pendientes.filtered(
            lambda l: not getattr(l, 'x_linkedin_is_excluded', False)
            and not l.marketing_opt_out
            and (l.email_from or '').strip()
        )

        # Se añaden al m2m de la campaña y se generan las líneas con la misma
        # lógica de prioridad de canal que usa "Sincronizar líneas".
        campana.lead_ids = [(4, l.id) for l in validos]
        campana.action_generate_campaign_leads()

        campana.message_post(body=(
            f'Añadidos <b>{len(validos)}</b> lead(s) desde el CRM. '
            f'Omitidos: {len(ya)} ya estaban, {self.n_excluidos} excluidos por '
            f'categoría, {self.n_sin_email} sin email.'
        ))

        return {
            'type': 'ir.actions.act_window',
            'name': campana.name,
            'res_model': 'marketing.campaign',
            'res_id': campana.id,
            'view_mode': 'form',
            'target': 'current',
        }
