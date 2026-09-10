from odoo import models, fields, api


class ResPartner(models.Model):
    _inherit = 'res.partner'

    whatsapp_message_ids = fields.One2many(
        'whatsapp.message', 'partner_id', string='Mensajes WhatsApp',
    )
    whatsapp_message_count = fields.Integer(
        compute='_compute_whatsapp_count',
    )

    @api.depends('whatsapp_message_ids')
    def _compute_whatsapp_count(self):
        for partner in self:
            partner.whatsapp_message_count = len(partner.whatsapp_message_ids)

    def action_open_whatsapp_send_wizard(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Enviar WhatsApp',
            'res_model': 'whatsapp.send.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_partner_id': self.id,
            },
        }

    def action_view_whatsapp_messages(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'WhatsApp — {self.name}',
            'res_model': 'whatsapp.message',
            'view_mode': 'list,form',
            'domain': [('partner_id', '=', self.id)],
            'context': {'default_partner_id': self.id},
        }
