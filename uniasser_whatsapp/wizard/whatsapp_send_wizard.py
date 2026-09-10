from datetime import timedelta

from odoo import models, fields, api
from odoo.exceptions import UserError


class WhatsAppSendWizard(models.TransientModel):
    _name = 'whatsapp.send.wizard'
    _description = 'Enviar mensaje WhatsApp'

    account_id = fields.Many2one(
        'whatsapp.account', string='Cuenta',
        default=lambda self: self.env['whatsapp.account'].search(
            [('company_id', '=', self.env.company.id), ('active', '=', True)], limit=1
        ),
        required=True,
    )
    partner_id = fields.Many2one('res.partner', string='Contacto')
    lead_id = fields.Many2one('crm.lead', string='Lead')
    phone = fields.Char(compute='_compute_phone', string='Teléfono')
    is_within_window = fields.Boolean(
        compute='_compute_window', string='Dentro de ventana 24h',
    )
    send_mode = fields.Selection(
        [('text', 'Texto libre'), ('template', 'Plantilla')],
        string='Tipo de mensaje', default='text',
    )
    body = fields.Text(string='Mensaje')
    template_id = fields.Many2one(
        'whatsapp.template', string='Plantilla',
        domain="[('account_id', '=', account_id), ('status', '=', 'approved')]",
    )

    @api.depends('partner_id', 'lead_id')
    def _compute_phone(self):
        for w in self:
            partner = w.partner_id or (w.lead_id.partner_id if w.lead_id else False)
            w.phone = (partner.phone or '') if partner else ''

    @api.depends('partner_id', 'lead_id')
    def _compute_window(self):
        cutoff = fields.Datetime.now() - timedelta(hours=24)
        for w in self:
            partner = w.partner_id or (w.lead_id.partner_id if w.lead_id else False)
            if not partner:
                w.is_within_window = False
                continue
            w.is_within_window = bool(
                self.env['whatsapp.message'].search([
                    ('partner_id', '=', partner.id),
                    ('direction', '=', 'inbound'),
                    ('timestamp', '>=', cutoff),
                ], limit=1)
            )

    def action_send(self):
        self.ensure_one()
        if not self.phone:
            raise UserError(
                'El contacto no tiene número de teléfono/móvil registrado.'
            )

        from ..models.whatsapp_api import WhatsAppAPI
        normalized = WhatsAppAPI.normalize_phone(self.phone)
        if not normalized:
            raise UserError(f'No se pudo normalizar el teléfono: {self.phone}')

        account = self.account_id
        is_qr = account.connection_type == 'qr_code'

        if self.send_mode == 'template':
            if not self.template_id:
                raise UserError('Selecciona una plantilla.')
            if is_qr:
                # QR: enviar el cuerpo del template como texto
                result = account._evo_send_text(normalized, self.template_id.body_text or '')
                wa_message_id = (result.get('key') or {}).get('id', '')
            else:
                api = account._get_api()
                result = api.send_template(
                    to=normalized,
                    template_name=self.template_id.name,
                    language_code=self.template_id.language_code or 'es',
                )
                wa_message_id = result.get('messages', [{}])[0].get('id')
            body = f'[Plantilla: {self.template_id.name}]'
        else:
            if not self.body:
                raise UserError('Escribe un mensaje antes de enviar.')
            if is_qr:
                result = account._evo_send_text(normalized, self.body)
                wa_message_id = (result.get('key') or {}).get('id', '')
            else:
                api = account._get_api()
                result = api.send_text(to=normalized, body=self.body)
                wa_message_id = result.get('messages', [{}])[0].get('id')
            body = self.body
        partner = self.partner_id or (self.lead_id.partner_id if self.lead_id else False)

        msg = self.env['whatsapp.message'].create({
            'account_id': self.account_id.id,
            'partner_id': partner.id if partner else False,
            'lead_id': self.lead_id.id if self.lead_id else False,
            'wa_message_id': wa_message_id,
            'direction': 'outbound',
            'body': body,
            'message_type': 'template' if self.send_mode == 'template' else 'text',
            'status': 'sent',
            'phone': normalized,
            'template_id': self.template_id.id if self.template_id else False,
        })
        msg._post_to_chatter()

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'WhatsApp enviado',
                'message': f'Mensaje enviado a {self.phone}',
                'type': 'success',
                'sticky': False,
            },
        }
