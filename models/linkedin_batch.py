"""Lotes de ingesta de señales.

Cada llamada de n8n al endpoint de señales deja un lote. Sin esto, cuando una
exportación entra a medias no hay forma de saber qué fila falló ni de repetir
solo esa importación: es la caja negra del minero de red.
"""
import json

from odoo import models, fields, api


class MarketingLinkedinBatch(models.Model):
    _name = 'marketing.linkedin.batch'
    _description = 'Lote de ingesta de señales de LinkedIn'
    _order = 'create_date desc'
    _rec_name = 'reference'

    reference = fields.Char(
        string='Referencia', readonly=True, copy=False, default='Lote nuevo',
    )
    profile_id = fields.Many2one(
        'marketing.linkedin.profile', string='Perfil',
        required=True, index=True, ondelete='cascade',
    )
    company_id = fields.Many2one(
        related='profile_id.company_id', store=True, readonly=True,
    )
    source = fields.Selection([
        ('n8n_csv', 'n8n — exportación CSV de LinkedIn'),
        ('n8n_api', 'n8n — API de LinkedIn'),
        ('manual', 'Carga manual desde Odoo'),
        ('other', 'Otro'),
    ], string='Origen', default='n8n_csv', required=True)
    source_file = fields.Char(
        string='Fichero de origen',
        help='Nombre del CSV que procesó n8n. Facilita rastrear qué exportación '
             'trajo cada señal.',
    )
    executed_at = fields.Datetime(
        string='Procesado', default=fields.Datetime.now, readonly=True,
    )
    state = fields.Selection([
        ('done', 'Procesado'),
        ('partial', 'Procesado con errores'),
        ('failed', 'Fallido'),
    ], string='Estado', default='done', required=True)

    received_count = fields.Integer(string='Filas recibidas', readonly=True)
    created_count = fields.Integer(string='Señales nuevas', readonly=True)
    duplicated_count = fields.Integer(string='Duplicadas', readonly=True)
    ignored_count = fields.Integer(
        string='Ignoradas por tipo', readonly=True,
        help='Filas cuyo tipo de señal no está marcado como "seguir" en el '
             'perfil. Llegaron y se descartaron a propósito.',
    )
    error_count = fields.Integer(string='Errores', readonly=True)
    hot_count = fields.Integer(string='Señales calientes', readonly=True)
    leads_created_count = fields.Integer(string='Leads creados', readonly=True)
    leads_updated_count = fields.Integer(string='Leads actualizados', readonly=True)

    signal_ids = fields.One2many(
        'marketing.linkedin.signal', 'batch_id', string='Señales',
    )
    error_details = fields.Text(
        string='Detalle de errores', readonly=True,
        help='Filas que no se pudieron procesar, con el motivo. n8n recibe lo '
             'mismo en la respuesta para poder avisar por email.',
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('reference', 'Lote nuevo') == 'Lote nuevo':
                vals['reference'] = self.env['ir.sequence'].next_by_code(
                    'marketing.linkedin.batch'
                ) or f'LOTE-{fields.Datetime.now():%Y%m%d%H%M%S}'
        return super().create(vals_list)

    def apply_summary(self, summary):
        """Vuelca el resumen devuelto por `marketing.linkedin.signal.ingest`."""
        self.ensure_one()
        errors = summary.get('error_details') or []
        self.write({
            'received_count': summary.get('received', 0),
            'created_count': summary.get('created', 0),
            'duplicated_count': summary.get('duplicated', 0),
            'ignored_count': summary.get('ignored', 0),
            'error_count': summary.get('errors', 0),
            'hot_count': summary.get('hot', 0),
            'leads_created_count': summary.get('leads_created', 0),
            'leads_updated_count': summary.get('leads_updated', 0),
            'error_details': json.dumps(errors, ensure_ascii=False, indent=2) if errors else False,
            'state': self._state_from_summary(summary),
        })
        return self

    @staticmethod
    def _state_from_summary(summary):
        received = summary.get('received', 0)
        errors = summary.get('errors', 0)
        if errors and errors >= received:
            return 'failed'
        if errors:
            return 'partial'
        return 'done'

    def action_view_signals(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Señales — {self.reference}',
            'res_model': 'marketing.linkedin.signal',
            'view_mode': 'list,form',
            'domain': [('batch_id', '=', self.id)],
        }
