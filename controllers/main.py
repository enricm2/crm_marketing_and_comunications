"""Controladores HTTP:
- /marketing/unsubscribe/<token>  → baja RGPD desde enlace en email
- /marketing/webhook/<esp>        → webhooks de eventos de ESP
"""
import json
import logging
import urllib.request
import urllib.parse
import re

from odoo import http, SUPERUSER_ID
from odoo.http import request

_logger = logging.getLogger(__name__)


class MarketingCampaignController(http.Controller):

    # ── Enlace de baja RGPD ───────────────────────────────────────────────────

    @http.route('/marketing/unsubscribe/<string:token>', type='http', auth='public', methods=['GET'])
    def unsubscribe(self, token, **kwargs):
        """Procesa la baja RGPD desde el enlace en el email."""
        if token == 'prueba_test_token':
            return request.render('crm_marketing_and_comunications.unsubscribe_success')

        env = request.env(user=SUPERUSER_ID)
        campaign_lead = env['marketing.campaign.lead'].sudo().search([
            ('unsubscribe_token', '=', token)
        ], limit=1)

        if not campaign_lead:
            return request.render('crm_marketing_and_comunications.unsubscribe_invalid')

        lead = campaign_lead.lead_id

        # Registrar exclusión RGPD
        env['marketing.rgpd.exclusion'].sudo().add_exclusion(
            email=lead.email_from,
            phone=lead.phone or '',  # crm.lead en Odoo 19 no tiene 'mobile'
            lead_id=lead.id,
            campaign_id=campaign_lead.campaign_id.id,
            reason='unsubscribe_email',
            excluded_by='unsubscribe_link',
        )

        # Actualizar línea de campaña
        campaign_lead.sudo().write({
            'exclusion_reason': 'rgpd_internal',
            'ai_classification': 'unsubscribe_request',
        })

        # Mover a etapa perdidos si configurado
        campaign = campaign_lead.campaign_id
        if campaign.stage_lost_id:
            lead.sudo().write({'stage_id': campaign.stage_lost_id.id})

        # Log en chatter del lead
        lead.sudo().message_post(
            body='El contacto ha solicitado la baja mediante el enlace del email de campaña. '
                 'Registrado en exclusión RGPD.',
        )

        _logger.info(
            'Unsubscribe processed: token=%s lead=%d email=%s',
            token[:8] + '...', lead.id, lead.email_from,
        )

        return request.render('crm_marketing_and_comunications.unsubscribe_success')

    @http.route('/marketing/baja/<string:token>', type='http', auth='public',
                methods=['GET'])
    def baja_lead(self, token, **kwargs):
        """Baja RGPD desde un email suelto (no de campaña).

        Mismo destino que el enlace de las campañas: la lista `marketing.rgpd
        .exclusion`. Tener dos listas separadas sería la forma más rápida de
        volver a escribir a alguien que ya pidió no recibir nada.
        """
        if token == 'prueba_test_token':
            return request.render('crm_marketing_and_comunications.unsubscribe_success')

        env = request.env(user=SUPERUSER_ID)
        lead = env['crm.lead'].sudo().search([
            ('unsubscribe_token', '=', token),
        ], limit=1)
        if not lead:
            return request.render('crm_marketing_and_comunications.unsubscribe_invalid')

        env['marketing.rgpd.exclusion'].sudo().add_exclusion(
            email=lead.email_from,
            phone=lead.phone or '',
            lead_id=lead.id,
            campaign_id=False,
            reason='unsubscribe_email',
            excluded_by='unsubscribe_link',
        )

        # Si además estaba en campañas activas, se corta ahí también: la baja es
        # de la persona, no del envío concreto por el que llegó.
        lineas = env['marketing.campaign.lead'].sudo().search([
            ('lead_id', '=', lead.id),
            ('exclusion_reason', '=', 'none'),
        ])
        if lineas:
            lineas.write({'exclusion_reason': 'rgpd_internal',
                          'ai_classification': 'unsubscribe_request'})

        lead.message_post(
            body='El contacto ha solicitado la baja mediante el enlace del email. '
                 'Registrado en exclusión RGPD'
                 + (f' y retirado de {len(lineas)} campaña(s) activa(s).' if lineas else '.'),
        )
        _logger.info('Baja procesada: token=%s… lead=%s email=%s',
                     token[:8], lead.id, lead.email_from)
        return request.render('crm_marketing_and_comunications.unsubscribe_success')

    # ── Webhooks ESP ─────────────────────────────────────────────────────────

    @http.route('/marketing/webhook/acumbamail', type='jsonrpc', auth='public', methods=['POST'], csrf=False)
    def webhook_acumbamail(self, **kwargs):
        """Webhook de eventos de Acumbamail."""
        return self._process_esp_webhook('acumbamail')

    @http.route('/marketing/webhook/sendgrid', type='jsonrpc', auth='public', methods=['POST'], csrf=False)
    def webhook_sendgrid(self, **kwargs):
        return self._process_esp_webhook('sendgrid')

    @http.route('/marketing/webhook/brevo', type='jsonrpc', auth='public', methods=['POST'], csrf=False)
    def webhook_brevo(self, **kwargs):
        return self._process_esp_webhook('brevo')

    @http.route('/marketing/webhook/mailgun', type='jsonrpc', auth='public', methods=['POST'], csrf=False)
    def webhook_mailgun(self, **kwargs):
        return self._process_esp_webhook('mailgun')

    # Amazon SES notifica a través de SNS, que envía el cuerpo con
    # Content-Type: text/plain. Por eso esta ruta es `type='http'` y parsea el
    # JSON a mano: con el dispatcher JSON de las demás, SNS recibiría un 400 y
    # AWS acabaría dando de baja la suscripción por fallos repetidos.
    @http.route('/marketing/webhook/aws_ses', type='http', auth='public',
                methods=['POST'], csrf=False)
    def webhook_aws_ses(self, **kwargs):
        try:
            payload = json.loads(request.httprequest.get_data(as_text=True) or '{}')
        except ValueError:
            _logger.warning('Webhook SES: cuerpo no es JSON válido')
            return request.make_response('ok', [('Content-Type', 'text/plain')])

        # SNS no entrega nada hasta que se confirma la suscripción visitando la
        # URL que manda. Se comprueba el host antes de llamarla: sin eso,
        # cualquiera podría usar este endpoint público para que Odoo hiciera
        # peticiones a donde quisiera.
        if payload.get('Type') == 'SubscriptionConfirmation':
            url = payload.get('SubscribeURL') or ''
            host = urllib.parse.urlparse(url).hostname or ''
            if url.startswith('https://') and re.fullmatch(
                    r'sns\.[a-z0-9-]+\.amazonaws\.com', host):
                try:
                    urllib.request.urlopen(url, timeout=15).close()
                    _logger.info('Webhook SES: suscripción SNS confirmada')
                except Exception as exc:  # noqa: BLE001
                    _logger.warning('Webhook SES: no se pudo confirmar: %s', exc)
            else:
                _logger.warning('Webhook SES: SubscribeURL con host no fiable (%s)',
                                host)
            return request.make_response('ok', [('Content-Type', 'text/plain')])

        self._apply_esp_events('aws_ses', payload)
        return request.make_response('ok', [('Content-Type', 'text/plain')])

    def _process_esp_webhook(self, esp_name: str):
        """Procesa un payload de webhook de cualquier ESP."""
        try:
            payload = request.get_json_data() or {}
        except Exception:
            payload = {}
        self._apply_esp_events(esp_name, payload)
        return {'status': 'ok'}

    def _apply_esp_events(self, esp_name: str, payload: dict):
        """Traduce el payload del ESP a eventos y los aplica a los leads."""
        env = request.env(user=SUPERUSER_ID)
        provider = env['marketing.esp.provider'].sudo().search([
            ('name', '=', esp_name),
            ('active', '=', True),
        ], limit=1)

        if not provider:
            _logger.warning('Webhook %s: no provider found', esp_name)
            return

        try:
            events = provider.parse_webhook(payload)
        except Exception as exc:
            _logger.exception('Webhook %s parse error: %s', esp_name, exc)
            return

        for event in events:
            email = (event.get('email') or '').strip().lower()
            event_type = event.get('type', '')
            if not email:
                continue
            self._apply_esp_event(env, email, event_type, event)

    def _apply_esp_event(self, env, email: str, event_type: str, event: dict):
        """Actualiza el marketing.campaign.lead correspondiente con el evento."""
        campaign_leads = env['marketing.campaign.lead'].sudo().search([
            ('lead_email', '=ilike', email),
            ('sent_at', '!=', False),
            ('exclusion_reason', '=', 'none'),
        ])

        if not campaign_leads:
            return

        from odoo import fields as F
        now = F.Datetime.now()

        field_map = {
            'delivered': 'delivered_at',
            'opened': 'opened_at',
            'clicked': 'clicked_at',
            'replied': 'replied_at',
        }

        for cl in campaign_leads:
            vals = {}
            if event_type in field_map and not getattr(cl, field_map[event_type]):
                vals[field_map[event_type]] = now
            elif event_type == 'bounced' and not cl.bounced:
                vals['bounced'] = True
                vals['bounced_at'] = now
            elif event_type == 'unsubscribed':
                # Baja desde el ESP → registrar en RGPD
                env['marketing.rgpd.exclusion'].add_exclusion(
                    email=email,
                    lead_id=cl.lead_id.id,
                    campaign_id=cl.campaign_id.id,
                    reason='unsubscribe_email',
                    excluded_by='unsubscribe_link',
                )
                vals['exclusion_reason'] = 'rgpd_internal'
                vals['ai_classification'] = 'unsubscribe_request'

            if vals:
                cl.write(vals)

        _logger.debug('ESP webhook %s applied to %d leads for email %s',
                      event_type, len(campaign_leads), email)
