import logging
from datetime import datetime, timezone

from odoo import models, fields, api

_logger = logging.getLogger(__name__)

MSG_TYPE_SELECTION = [
    ('text', 'Texto'),
    ('template', 'Plantilla'),
    ('image', 'Imagen'),
    ('document', 'Documento'),
    ('audio', 'Audio'),
    ('video', 'Vídeo'),
    ('location', 'Ubicación'),
    ('sticker', 'Sticker'),
    ('reaction', 'Reacción'),
    ('unknown', 'Desconocido'),
]

STATUS_SELECTION = [
    ('pending', 'Pendiente'),
    ('sent', 'Enviado'),
    ('delivered', 'Entregado'),
    ('read', 'Leído'),
    ('failed', 'Fallido'),
]


class WhatsAppMessage(models.Model):
    _name = 'whatsapp.message'
    _description = 'Mensaje WhatsApp'
    _order = 'timestamp asc, id asc'
    _rec_name = 'body'

    account_id = fields.Many2one(
        'whatsapp.account', string='Cuenta', required=True, ondelete='cascade',
        index=True,
    )
    partner_id = fields.Many2one(
        'res.partner', string='Contacto', index=True, ondelete='set null',
    )
    wa_group_id = fields.Many2one(
        'whatsapp.group', string='Grupo WhatsApp', index=True, ondelete='set null',
        help='Rellenado solo para mensajes de grupo.',
    )
    wa_profile_name = fields.Char(
        string='Nombre WhatsApp',
        help='profile.name recibido en el webhook de Meta (puede cambiar con el tiempo).',
    )
    lead_id = fields.Many2one(
        'crm.lead', string='Lead / Oportunidad', index=True, ondelete='set null',
    )
    wa_message_id = fields.Char(
        string='WA Message ID', index=True,
        help='ID único del mensaje en Meta. Usado para deduplicar.',
    )
    direction = fields.Selection(
        [('inbound', 'Recibido'), ('outbound', 'Enviado')],
        string='Dirección', required=True, default='outbound',
    )
    body = fields.Text(string='Mensaje')
    message_type = fields.Selection(MSG_TYPE_SELECTION, default='text', required=True)
    attachment_ids = fields.Many2many(
        'ir.attachment',
        'whatsapp_message_attachment_rel',
        'message_id', 'attachment_id',
        string='Adjuntos',
    )
    status = fields.Selection(STATUS_SELECTION, default='pending')
    template_id = fields.Many2one('whatsapp.template', string='Plantilla')
    timestamp = fields.Datetime(string='Fecha/Hora', default=fields.Datetime.now)
    phone = fields.Char(string='Teléfono (normalizado)')
    error_message = fields.Char(string='Error')
    mail_message_id = fields.Many2one(
        'mail.message', string='Mensaje chatter', ondelete='set null',
    )

    _sql_constraints = [
        (
            'unique_wa_message_id',
            'UNIQUE(wa_message_id)',
            'Ya existe un mensaje con este WA Message ID.',
        ),
    ]

    # ── Procesamiento de mensajes entrantes ───────────────────────────────────

    @api.model
    def _process_inbound(self, account, msg_data: dict, contacts: list = None):
        """Crea whatsapp.message desde el payload de Meta y publica en chatter.

        *contacts* es el array ``value.contacts`` del webhook (puede ser vacío).
        Contiene ``[{"wa_id": "346...", "profile": {"name": "Juan"}}]``.
        """
        wa_id = msg_data.get('id')
        if not wa_id:
            return

        # Deduplicar
        if self.search_count([('wa_message_id', '=', wa_id)]):
            _logger.debug('WhatsApp: mensaje duplicado ignorado: %s', wa_id)
            return

        phone = msg_data.get('from', '')
        msg_type = msg_data.get('type', 'unknown')
        timestamp_unix = int(msg_data.get('timestamp', 0))
        timestamp = (
            datetime.fromtimestamp(timestamp_unix, tz=timezone.utc).replace(tzinfo=None)
            if timestamp_unix else fields.Datetime.now()
        )

        # ── Nombre de perfil de WhatsApp (contacts[].profile.name) ───────────
        contacts = contacts or []
        contact_info = next((c for c in contacts if c.get('wa_id') == phone), None)
        profile_name = (contact_info or {}).get('profile', {}).get('name', '') or ''

        # ── Detectar si es mensaje de grupo ───────────────────────────────────
        # recipient_type == 'group', o el campo context.from contiene @g.us
        recipient_type = msg_data.get('recipient_type', '')
        context_from = (msg_data.get('context') or {}).get('from', '')
        group_jid = None
        if recipient_type == 'group' or '@g.us' in context_from:
            # El JID del grupo viene en context.from para mensajes en grupos
            group_jid = context_from if '@g.us' in context_from else None
            # Fallback: si el propio msg.to es el grupo (no suele darse en entrantes)
            if not group_jid and '@g.us' in msg_data.get('to', ''):
                group_jid = msg_data.get('to')

        wa_group_rec = None
        if group_jid:
            wa_group_rec = self.env['whatsapp.group']._get_or_fetch(account, group_jid)

        # Extraer cuerpo según tipo
        body = ''
        if msg_type == 'text':
            body = msg_data.get('text', {}).get('body', '')
        elif msg_type in ('image', 'document', 'audio', 'video', 'sticker'):
            caption = msg_data.get(msg_type, {}).get('caption', '')
            body = caption or f'[{msg_type}]'
        elif msg_type == 'location':
            loc = msg_data.get('location', {})
            body = (
                f"Ubicación: {loc.get('name', '')} "
                f"({loc.get('latitude')}, {loc.get('longitude')})"
            )
        elif msg_type == 'reaction':
            body = msg_data.get('reaction', {}).get('emoji', '👍')

        partner = self._find_or_create_partner(phone, profile_name)
        lead = self._find_lead(partner)

        record = self.create({
            'account_id': account.id,
            'partner_id': partner.id,
            'wa_group_id': wa_group_rec.id if wa_group_rec else False,
            'wa_profile_name': profile_name or False,
            'lead_id': lead.id if lead else False,
            'wa_message_id': wa_id,
            'direction': 'inbound',
            'body': body,
            'message_type': msg_type if msg_type in dict(MSG_TYPE_SELECTION) else 'unknown',
            'status': 'read',
            'timestamp': timestamp,
            'phone': phone,
        })

        # Descargar adjuntos de medios si los hay
        if msg_type in ('image', 'document', 'audio', 'video', 'sticker'):
            try:
                record._download_inbound_media(account, msg_data)
            except Exception as exc:
                _logger.warning('WhatsApp: no se pudo descargar medio %s: %s', msg_type, exc)

        record._post_to_chatter()
        return record

    @api.model
    def _process_status_update(self, status_data: dict):
        """Actualiza el estado de un mensaje saliente (sent/delivered/read/failed)."""
        wa_id = status_data.get('id')
        new_status = status_data.get('status', '')
        if not wa_id or new_status not in dict(STATUS_SELECTION):
            return
        record = self.search([('wa_message_id', '=', wa_id)], limit=1)
        if record:
            # Solo avanzar estado, nunca retroceder
            order = ['pending', 'sent', 'delivered', 'read', 'failed']
            current_idx = order.index(record.status) if record.status in order else 0
            new_idx = order.index(new_status) if new_status in order else 0
            if new_idx > current_idx or new_status == 'failed':
                vals = {'status': new_status}
                if new_status == 'failed':
                    errors = status_data.get('errors', [{}])
                    vals['error_message'] = (
                        errors[0].get('title', 'Error desconocido') if errors else ''
                    )
                record.write(vals)

    @api.model
    def _find_or_create_partner(self, phone: str, profile_name: str = ''):
        """Busca res.partner por teléfono (últimos 9 dígitos). Crea si no existe.

        Prioridad de nombre:
          1. Nombre existente en Odoo (partner.name) — si no es el auto-generado
          2. profile.name de WhatsApp (*profile_name*)
          3. Número formateado como último recurso
        """
        from .whatsapp_api import WhatsAppAPI
        normalized = WhatsAppAPI.normalize_phone(phone)
        suffix = normalized[-9:] if len(normalized) >= 9 else normalized
        partner = self.env['res.partner'].search([
            '|',
            ('phone', 'like', suffix),
            ('mobile', 'like', suffix),
        ], limit=1)

        formatted_phone = f'+{normalized}' if not normalized.startswith('+') else normalized
        auto_name_prefix = 'WhatsApp '  # prefijo de nombres auto-generados

        if partner:
            # Si el nombre es el auto-generado y ahora tenemos profile.name → actualizarlo
            is_auto_name = partner.name and partner.name.startswith(auto_name_prefix)
            if is_auto_name and profile_name:
                partner.write({'name': profile_name})
                _logger.info(
                    'WhatsApp: nombre del partner %d actualizado: "%s" → "%s"',
                    partner.id, partner.name, profile_name,
                )
        else:
            name = profile_name or f'WhatsApp {phone}'
            partner = self.env['res.partner'].create({
                'name': name,
                'phone': formatted_phone,
            })
            _logger.info(
                'WhatsApp: creado partner automático para %s → "%s" (id=%d)',
                phone, name, partner.id,
            )
        return partner

    @api.model
    def _find_lead(self, partner):
        """Busca el crm.lead abierto más reciente del partner."""
        if not partner:
            return None
        return self.env['crm.lead'].search([
            ('partner_id', '=', partner.id),
            ('active', '=', True),
        ], order='date_deadline desc, id desc', limit=1)

    def _post_to_chatter(self):
        """Publica el mensaje en el chatter del lead (o partner si no hay lead)."""
        self.ensure_one()
        subtype = self.env.ref(
            'uniasser_whatsapp.mt_whatsapp_message',
            raise_if_not_found=False,
        )
        direction_icon = '📥' if self.direction == 'inbound' else '📤'
        body_html = (
            f'<div style="font-family:sans-serif">'
            f'<span style="color:#25D366;font-weight:bold">🟢 WhatsApp {direction_icon}</span><br/>'
            f'{self.body or ""}'
            f'</div>'
        )
        target = self.lead_id or self.partner_id
        if not target:
            return
        try:
            mail_msg = target.message_post(
                body=body_html,
                message_type='comment',
                subtype_id=subtype.id if subtype else False,
                author_id=(
                    self.env.ref('base.public_partner').id
                    if self.direction == 'inbound'
                    else self.env.user.partner_id.id
                ),
            )
            self.mail_message_id = mail_msg.id
        except Exception as exc:
            _logger.warning('WhatsApp: error publicando en chatter: %s', exc)

    def _download_inbound_media(self, account, msg_data: dict):
        """Descarga y guarda el adjunto del mensaje entrante."""
        import base64
        msg_type = msg_data.get('type')
        media_info = msg_data.get(msg_type, {})
        media_id = media_info.get('id')
        if not media_id:
            return
        api = account._get_api()
        content = api.download_media(media_id)
        mimetype = media_info.get('mime_type', 'application/octet-stream')
        filename = media_info.get('filename') or f'{msg_type}_{media_id}'
        att = self.env['ir.attachment'].create({
            'name': filename,
            'datas': base64.b64encode(content).decode('ascii'),
            'mimetype': mimetype,
            'res_model': self._name,
            'res_id': self.id,
        })
        self.attachment_ids = [(4, att.id)]
