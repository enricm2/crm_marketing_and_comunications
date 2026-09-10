from datetime import timedelta

from odoo import models, fields, api


class CrmLead(models.Model):
    _inherit = 'crm.lead'

    whatsapp_message_ids = fields.One2many(
        'whatsapp.message', 'lead_id', string='Mensajes WhatsApp',
    )
    whatsapp_message_count = fields.Integer(
        compute='_compute_whatsapp_stats', store=True,
    )
    last_whatsapp_date = fields.Datetime(
        compute='_compute_whatsapp_stats', store=True,
        string='Último WhatsApp recibido',
    )
    last_whatsapp_ago = fields.Char(
        compute='_compute_whatsapp_ago', string='Último WA',
    )

    @api.depends(
        'whatsapp_message_ids',
        'whatsapp_message_ids.timestamp',
        'whatsapp_message_ids.direction',
    )
    def _compute_whatsapp_stats(self):
        for lead in self:
            msgs = lead.whatsapp_message_ids
            lead.whatsapp_message_count = len(msgs)
            inbound = msgs.filtered(lambda m: m.direction == 'inbound')
            lead.last_whatsapp_date = max(inbound.mapped('timestamp'), default=False)

    @api.depends('last_whatsapp_date')
    def _compute_whatsapp_ago(self):
        now = fields.Datetime.now()
        for lead in self:
            if not lead.last_whatsapp_date:
                lead.last_whatsapp_ago = ''
                continue
            delta = now - lead.last_whatsapp_date
            if delta < timedelta(hours=1):
                mins = int(delta.total_seconds() / 60)
                lead.last_whatsapp_ago = f'hace {mins}m'
            elif delta < timedelta(days=1):
                hours = int(delta.total_seconds() / 3600)
                lead.last_whatsapp_ago = f'hace {hours}h'
            else:
                days = delta.days
                lead.last_whatsapp_ago = f'hace {days}d'

    def action_open_whatsapp_send_wizard(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Enviar WhatsApp',
            'res_model': 'whatsapp.send.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_lead_id': self.id,
                'default_partner_id': self.partner_id.id if self.partner_id else False,
            },
        }

    def action_view_whatsapp_messages(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'WhatsApp — {self.name}',
            'res_model': 'whatsapp.message',
            'view_mode': 'list,form',
            'domain': [('lead_id', '=', self.id)],
            'context': {'default_lead_id': self.id},
        }
