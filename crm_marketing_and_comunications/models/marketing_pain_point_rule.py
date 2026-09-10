from odoo import models, fields


class MarketingPainPointRule(models.Model):
    _name = 'marketing.pain_point.rule'
    _description = 'Regla de punto de dolor por sector'
    _order = 'service_id, sector_keyword'

    name = fields.Char(
        string='Nombre', required=True,
        help='Nombre descriptivo de la regla (p. ej. "Agencias marketing – WA Manager").',
    )
    sector_keyword = fields.Char(
        string='Keyword de sector',
        required=True,
        help='Palabra clave que se buscará en el sector o resumen del lead '
             '(case-insensitive). Ej: "agencia", "inmobiliaria", "asistencia técnica".',
    )
    service_id = fields.Many2one(
        'product.template',
        string='Producto / Servicio',
        help='Aplicar solo cuando la campaña promueve este producto. '
             'Dejar vacío para aplicar a cualquier campaña.',
        ondelete='set null',
    )
    pain_point_description = fields.Text(
        string='Descripción del punto de dolor',
        required=True,
        help='Texto que describe el punto de dolor de este sector en relación '
             'al servicio. Se inyecta en el prompt de Claude para personalizar '
             'el párrafo de apertura del email/WhatsApp.',
    )
    active = fields.Boolean(default=True)
    notes = fields.Text(string='Notas internas')
