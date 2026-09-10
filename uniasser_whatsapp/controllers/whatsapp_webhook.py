import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class WhatsAppWebhookController(http.Controller):

    @http.route(
        '/whatsapp/webhook',
        type='http', auth='public', methods=['GET'], csrf=False,
    )
    def verify_webhook(self, **kwargs):
        """Meta llama a este GET para verificar el webhook (hub.challenge handshake)."""
        mode = kwargs.get('hub.mode')
        token = kwargs.get('hub.verify_token')
        challenge = kwargs.get('hub.challenge', '')

        account = request.env['whatsapp.account'].sudo().search([
            ('webhook_verify_token', '=', token),
            ('active', '=', True),
        ], limit=1)

        if mode == 'subscribe' and account:
            _logger.info('WhatsApp webhook verificado para cuenta "%s"', account.name)
            return request.make_response(
                challenge,
                headers=[('Content-Type', 'text/plain')],
            )

        _logger.warning(
            'WhatsApp webhook verification fallida: mode=%s token=%s',
            mode, (token or '')[:10],
        )
        return request.make_response('Forbidden', status=403)

    @http.route(
        '/whatsapp/webhook',
        type='http', auth='public', methods=['POST'], csrf=False,
    )
    def receive_webhook(self, **kwargs):
        """Recibe notificaciones de Meta: mensajes entrantes y actualizaciones de estado."""
        payload_bytes = request.httprequest.data
        signature = request.httprequest.headers.get('X-Hub-Signature-256', '')

        try:
            data = json.loads(payload_bytes)
        except Exception:
            _logger.error('WhatsApp webhook: payload inválido (no JSON)')
            return request.make_response('Bad Request', status=400)

        for entry in data.get('entry', []):
            for change in entry.get('changes', []):
                value = change.get('value', {})
                phone_number_id = value.get('metadata', {}).get('phone_number_id', '')

                account = request.env['whatsapp.account'].sudo().search([
                    ('phone_number_id', '=', phone_number_id),
                    ('active', '=', True),
                ], limit=1)

                if not account:
                    _logger.warning(
                        'WhatsApp webhook: sin cuenta para phone_number_id=%s',
                        phone_number_id,
                    )
                    continue

                # Validar firma HMAC
                if account.app_secret:
                    from ..models.whatsapp_api import WhatsAppAPI
                    if not WhatsAppAPI.verify_signature(
                        payload_bytes, signature, account.app_secret
                    ):
                        _logger.warning(
                            'WhatsApp webhook: firma HMAC inválida para cuenta "%s"',
                            account.name,
                        )
                        # En producción: descomenta la siguiente línea para rechazar:
                        # return request.make_response('Forbidden', status=403)

                field = change.get('field', 'messages')

                # ── Eventos de grupo: actualizar nombre ───────────────────────
                if field in (
                    'group_settings_update',
                    'group_lifecycle_update',
                    'group_participants_update',
                    'group_status_update',
                ):
                    group_jid = value.get('group_id') or value.get('id') or ''
                    subject = value.get('subject') or value.get('name') or ''
                    if group_jid:
                        try:
                            request.env['whatsapp.group'].sudo()._update_name(
                                account, group_jid, subject or None
                            )
                        except Exception:
                            _logger.exception(
                                'WhatsApp webhook: error actualizando grupo %s', group_jid
                            )
                    continue

                if field != 'messages':
                    # Cualquier otro campo (calls, account_update, quality_update,
                    # phone_number_name_update…) se descartaba en silencio: no
                    # quedaba ni rastro de que Meta lo hubiera enviado, así que
                    # cuando algo fallaba no había forma de saber que el evento
                    # había llegado y se había tirado.
                    #
                    # OJO: al implantar las llamadas de voz hay que añadir una
                    # rama `field == 'calls'` ANTES de este descarte, leyendo el
                    # array value['calls']. Es exactamente el mismo agujero que
                    # había en wa-manager (backend/src/routes/webhooks.js).
                    _logger.info(
                        'WhatsApp webhook: campo no gestionado "%s" (cuenta %s) — descartado',
                        field, account.id,
                    )
                    continue

                # ── Mensajes entrantes ────────────────────────────────────────
                contacts = value.get('contacts', [])
                for msg in value.get('messages', []):
                    try:
                        request.env['whatsapp.message'].sudo()._process_inbound(
                            account, msg, contacts
                        )
                    except Exception:
                        _logger.exception(
                            'WhatsApp webhook: error procesando mensaje %s', msg.get('id')
                        )

                # Actualizaciones de estado (sent/delivered/read/failed)
                for status_upd in value.get('statuses', []):
                    try:
                        request.env['whatsapp.message'].sudo()._process_status_update(
                            status_upd
                        )
                    except Exception:
                        _logger.exception('WhatsApp webhook: error en status update')

        # Meta exige siempre 200 OK y respuesta rápida
        return request.make_response('OK', headers=[('Content-Type', 'text/plain')])
