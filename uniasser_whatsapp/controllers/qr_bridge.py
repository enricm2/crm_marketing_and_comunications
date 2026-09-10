"""QR bridge: polling de estado y webhook de Evolution API para mensajes entrantes."""
import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class WhatsAppQrController(http.Controller):

    @http.route(
        '/whatsapp/qr_status/<int:account_id>',
        type='http',
        auth='user',
        methods=['GET'],
        csrf=False,
    )
    def qr_status(self, account_id, **_kw):
        account = request.env['whatsapp.account'].browse(account_id)
        if not account.exists():
            return request.make_response(
                json.dumps({'error': 'not_found'}),
                headers=[('Content-Type', 'application/json')],
                status=404,
            )

        result = {
            'state': account.qr_state,
            'connection_info': account.connection_info or '',
        }

        if account.qr_state == 'connecting':
            bridge_state = account._evo_get_state()
            if bridge_state == 'open':
                account.sudo().action_check_qr_status()
                result['state'] = 'connected'
                result['connection_info'] = account.connection_info or ''
            elif bridge_state == 'connecting':
                account.sudo()._evo_refresh_qr()

        if account.qr_code_image and account.qr_state == 'connecting':
            try:
                b64 = account.qr_code_image
                if isinstance(b64, bytes):
                    b64 = b64.decode('utf-8', errors='replace')
                result['qr_data_uri'] = f'data:image/png;base64,{b64}'
            except Exception:
                pass

        return request.make_response(
            json.dumps(result),
            headers=[('Content-Type', 'application/json')],
        )

    @http.route(
        '/whatsapp/evolution_webhook',
        type='http',
        auth='public',
        methods=['POST'],
        csrf=False,
    )
    def evolution_webhook(self, **_kw):
        """Recibe eventos de Evolution API: mensajes entrantes y actualizaciones de conexión."""
        try:
            data = json.loads(request.httprequest.data or b'{}')
        except Exception:
            return request.make_response('Bad Request', status=400)

        event = data.get('event', '')
        instance_name = data.get('instance', '')

        if not instance_name:
            return request.make_response('OK')

        account = request.env['whatsapp.account'].sudo().search([
            ('evolution_instance', '=', instance_name),
            ('active', '=', True),
        ], limit=1)

        if not account:
            _logger.warning('Evolution webhook: sin cuenta para instancia "%s"', instance_name)
            return request.make_response('OK')

        # ── Mensaje entrante ──────────────────────────────────────────────────
        if event in ('messages.upsert', 'MESSAGES_UPSERT'):
            raw = data.get('data', [])
            # Evolution API v2 envía data como lista; v1 como objeto único
            msg_list = raw if isinstance(raw, list) else [raw]

            from datetime import datetime, timezone
            WhatsAppMessage = request.env['whatsapp.message'].sudo()

            for msg_data in msg_list:
                if not isinstance(msg_data, dict):
                    continue
                key = msg_data.get('key', {})

                # Ignorar mensajes propios (fromMe)
                if key.get('fromMe'):
                    continue

                remote_jid = key.get('remoteJid', '')
                wa_id = key.get('id', '')
                # Ignorar mensajes de grupos
                if '@g.us' in remote_jid:
                    continue
                phone = remote_jid.split('@')[0] if '@' in remote_jid else remote_jid

                if not wa_id or not phone:
                    continue

                # Deduplicar
                if WhatsAppMessage.search_count([('wa_message_id', '=', wa_id)]):
                    continue

                # Extraer cuerpo del mensaje
                message = msg_data.get('message', {})
                body = (
                    message.get('conversation')
                    or message.get('extendedTextMessage', {}).get('text')
                    or message.get('imageMessage', {}).get('caption')
                    or message.get('documentMessage', {}).get('caption')
                    or ''
                )

                # Determinar tipo
                if 'imageMessage' in message:
                    msg_type = 'image'
                    body = body or '[imagen]'
                elif 'documentMessage' in message:
                    msg_type = 'document'
                    body = body or '[documento]'
                elif 'audioMessage' in message:
                    msg_type = 'audio'
                    body = '[audio]'
                elif 'videoMessage' in message:
                    msg_type = 'video'
                    body = body or '[vídeo]'
                elif 'stickerMessage' in message:
                    msg_type = 'sticker'
                    body = '[sticker]'
                else:
                    msg_type = 'text'

                timestamp_unix = int(msg_data.get('messageTimestamp', 0) or 0)
                timestamp = (
                    datetime.fromtimestamp(timestamp_unix, tz=timezone.utc).replace(tzinfo=None)
                    if timestamp_unix else None
                )

                partner = WhatsAppMessage._find_or_create_partner(phone)
                lead = WhatsAppMessage._find_lead(partner)

                record = WhatsAppMessage.create({
                    'account_id': account.id,
                    'partner_id': partner.id,
                    'lead_id': lead.id if lead else False,
                    'wa_message_id': wa_id,
                    'direction': 'inbound',
                    'body': body,
                    'message_type': msg_type,
                    'status': 'pending',
                    'timestamp': timestamp,
                    'phone': phone,
                })
                record._post_to_chatter()
                _logger.info(
                    'Evolution webhook: mensaje entrante de %s para cuenta "%s"',
                    phone, account.name,
                )

        # ── Actualización de conexión ─────────────────────────────────────────
        elif event in ('connection.update', 'CONNECTION_UPDATE'):
            conn_data = data.get('data', {})
            state = conn_data.get('state', '')
            if state == 'open' and account.qr_state != 'connected':
                account.write({
                    'qr_state': 'connected',
                    'qr_code_image': False,
                    'connection_info': account._evo_get_profile_info(),
                    'last_error': False,
                })
            elif state in ('close', 'refused') and account.qr_state == 'connected':
                account.write({'qr_state': 'disconnected'})

        return request.make_response('OK')
