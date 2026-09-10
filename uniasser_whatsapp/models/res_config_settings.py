from odoo import models, fields


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    whatsapp_account_count = fields.Integer(
        string='Cuentas configuradas',
        compute='_compute_whatsapp_account_count',
    )

    def _compute_whatsapp_account_count(self):
        count = self.env['whatsapp.account'].search_count([])
        for rec in self:
            rec.whatsapp_account_count = count

    def action_open_whatsapp_accounts(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Cuentas WhatsApp',
            'res_model': 'whatsapp.account',
            'view_mode': 'list,form',
        }

    def action_open_whatsapp_templates(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Plantillas WhatsApp',
            'res_model': 'whatsapp.template',
            'view_mode': 'list,form',
        }
