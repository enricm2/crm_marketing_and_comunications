from odoo import models, fields


class MarketingChannel(models.Model):
    _name = 'marketing.channel'
    _description = 'Canal de marketing'
    _order = 'sequence, name'

    name = fields.Char(string='Canal', required=True, translate=True)
    code = fields.Selection([
        ('email', 'Email'),
        ('whatsapp', 'WhatsApp Business'),
        ('linkedin', 'LinkedIn (Prosp)'),
        ('meta', 'Meta Ads'),
    ], string='Código', required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    available = fields.Boolean(
        string='Disponible',
        default=True,
        help='Desactivar para canales "próximamente" (ej. Meta Ads).',
    )
    coming_soon_note = fields.Char(
        string='Nota "Próximamente"',
        help='Texto que se muestra cuando el canal no está disponible.',
    )
    color = fields.Integer(string='Color', default=0)
