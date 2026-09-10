import json
import logging
import urllib.error
import urllib.request

from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class WhatsAppAccount(models.Model):
    _name = 'whatsapp.account'
    _description = 'Cuenta WhatsApp Business'
    _order = 'company_id, name'

    name = fields.Char(string='Nombre', required=True)
    company_id = fields.Many2one(
        'res.company', string='Empresa',
        default=lambda self: self.env.company,
        required=True,
    )

    # ── Modo de conexión ──────────────────────────────────────────────────────
    connection_type = fields.Selection(
        selection=[
            ('meta_business', 'Meta Business API'),
            ('qr_code', 'QR Code (WhatsApp Web)'),
        ],
        string='Modo de conexión',
        required=True,
        default='meta_business',
        help='Meta Business: requiere cuenta de desarrollador Meta.\n'
             'QR Code: escanea el QR con el móvil del cliente, sin cuenta de desarrollador.',
    )

    # ── Meta Business API ─────────────────────────────────────────────────────
    phone_number_id = fields.Char(
        string='Phone Number ID',
        help='ID del número en Meta (no es el número de teléfono visible).',
    )
    waba_id = fields.Char(
        string='WhatsApp Business Account ID',
        help='WABA ID de Meta Business Manager.',
    )
    access_token = fields.Char(
        string='Access Token',
        groups='uniasser_whatsapp.group_whatsapp_admin',
        help='Token permanente del System User de Meta. Nunca lo compartas.',
    )
    app_secret = fields.Char(
        string='App Secret',
        groups='uniasser_whatsapp.group_whatsapp_admin',
        help='App Secret de la App de Meta. Usado para validar la firma del webhook.',
    )
    webhook_verify_token = fields.Char(
        string='Webhook Verify Token',
        help='Token que Meta envía al verificar el webhook. Elige uno aleatorio.',
    )

    # ── QR Code / Evolution API ───────────────────────────────────────────────
    evolution_url = fields.Char(
        string='URL del Bridge',
        default='http://localhost:8080',
        help='URL base del servicio Evolution API.',
    )
    evolution_api_key = fields.Char(
        string='API Key del Bridge',
        groups='uniasser_whatsapp.group_whatsapp_admin',
    )
    evolution_instance = fields.Char(
        string='Nombre de instancia',
        help='Identificador único para esta conexión en el bridge (ej: empresa_wa).',
    )
    qr_code_image = fields.Binary(
        string='Código QR',
        readonly=True,
        attachment=False,
    )
    qr_state = fields.Selection(
        selection=[
            ('draft', 'Sin conectar'),
            ('connecting', 'Esperando escaneo QR'),
            ('connected', 'Conectado'),
            ('disconnected', 'Desconectado'),
        ],
        string='Estado QR',
        default='draft',
        readonly=True,
    )
    connection_info = fields.Char(string='Dispositivo conectado', readonly=True)
    last_qr_check = fields.Datetime(string='Última comprobación', readonly=True)

    # ── Común ─────────────────────────────────────────────────────────────────
    active = fields.Boolean(default=True)
    last_error = fields.Char(
        string='Último error',
        readonly=True,
        groups='uniasser_whatsapp.group_whatsapp_admin',
    )

    def _get_api(self):
        """Devuelve una instancia de WhatsAppAPI para esta cuenta."""
        self.ensure_one()
        from .whatsapp_api import WhatsAppAPI
        return WhatsAppAPI(
            phone_number_id=self.phone_number_id,
            access_token=self.access_token,
            app_secret=self.app_secret or '',
        )

    def action_test_connection(self):
        """Prueba las credenciales llamando a la Graph API."""
        self.ensure_one()
        try:
            api = self._get_api()
            info = api.get_phone_info()
            display_phone = info.get('display_phone_number', self.phone_number_id)
            verified = info.get('verified_name', '')
            self.last_error = False
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Conexión correcta',
                    'message': f'Número: {display_phone} — Nombre verificado: {verified}',
                    'type': 'success',
                    'sticky': False,
                },
            }
        except Exception as exc:
            msg = str(exc)[:250]
            self.last_error = msg
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Error de conexión',
                    'message': msg,
                    'type': 'danger',
                    'sticky': True,
                },
            }

    def action_sync_templates(self):
        """Sincroniza las plantillas aprobadas desde Meta."""
        self.ensure_one()
        try:
            api = self._get_api()
            templates = api.get_templates(self.waba_id)
            Template = self.env['whatsapp.template']
            synced = 0
            for t in templates:
                wa_id = t.get('id')
                existing = Template.search([
                    ('wa_template_id', '=', wa_id),
                    ('account_id', '=', self.id),
                ], limit=1)
                vals = {
                    'name': t.get('name', ''),
                    'language_code': t.get('language', ''),
                    'category': t.get('category', '').lower() or 'utility',
                    'status': t.get('status', '').lower() or 'pending',
                    'components': str(t.get('components', [])),
                    'wa_template_id': wa_id,
                    'account_id': self.id,
                }
                if existing:
                    existing.write(vals)
                else:
                    Template.create(vals)
                synced += 1
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Plantillas sincronizadas',
                    'message': f'Se sincronizaron {synced} plantilla(s).',
                    'type': 'success',
                    'sticky': False,
                },
            }
        except Exception as exc:
            raise UserError(f'Error al sincronizar plantillas: {exc}') from exc

    # =========================================================================
    # Acciones — QR Code (Evolution API)
    # =========================================================================

    def action_connect_qr(self):
        """Crea la instancia en Evolution API y genera el primer QR."""
        self.ensure_one()
        self._qr_check_fields()
        self._evo_create_instance()
        self._evo_refresh_qr()
        self.qr_state = 'connecting'
        return {
            'type': 'ir.actions.act_window',
            'name': 'Escanea el QR con WhatsApp',
            'res_model': 'whatsapp.account',
            'res_id': self.id,
            'view_mode': 'form',
            'view_id': self.env.ref('uniasser_whatsapp.view_whatsapp_account_qr_popup').id,
            'target': 'new',
        }

    def action_check_qr_status(self):
        """Comprueba si el QR fue escaneado y actualiza el estado."""
        self.ensure_one()
        self._qr_check_fields()
        state = self._evo_get_state()
        self.last_qr_check = fields.Datetime.now()
        if state == 'open':
            self.qr_state = 'connected'
            self.qr_code_image = False
            self.connection_info = self._evo_get_profile_info()
            self.last_error = False
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': '¡Conectado!',
                    'message': f'WhatsApp vinculado. {self.connection_info or ""}',
                    'type': 'success',
                    'sticky': False,
                },
            }
        elif state in ('close', 'refused'):
            self.qr_state = 'disconnected'
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Sin conexión',
                    'message': 'El dispositivo no está conectado. Genera un nuevo QR.',
                    'type': 'warning',
                    'sticky': False,
                },
            }
        else:
            self._evo_refresh_qr()
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Pendiente',
                    'message': 'QR actualizado. Escanéalo con WhatsApp.',
                    'type': 'info',
                    'sticky': False,
                },
            }

    def action_refresh_qr(self):
        """Regenera el QR."""
        self.ensure_one()
        self._qr_check_fields()
        self._evo_refresh_qr()
        return True

    def action_disconnect_qr(self):
        """Cierra sesión en el bridge."""
        self.ensure_one()
        if self.evolution_instance:
            try:
                self._evo_request('DELETE', f'/instance/logout/{self.evolution_instance}')
            except UserError:
                pass
        self.qr_state = 'disconnected'
        self.qr_code_image = False
        self.connection_info = False

    # ── Evolution API helpers ─────────────────────────────────────────────────

    def _qr_check_fields(self):
        if not self.evolution_url or not self.evolution_api_key or not self.evolution_instance:
            raise UserError(
                'Configura la URL del Bridge, la API Key y el Nombre de instancia antes de continuar.'
            )

    def _evo_request(self, method, path, payload=None):
        url = self.evolution_url.rstrip('/') + path
        data = json.dumps(payload).encode() if payload else None
        req = urllib.request.Request(
            url, data=data,
            headers={'apikey': self.evolution_api_key, 'Content-Type': 'application/json'},
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read().decode('utf-8', errors='replace')
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode('utf-8', errors='replace')
            if exc.code == 409:
                return {}   # instancia ya existe — OK
            raise UserError(f'Bridge error {exc.code}: {body[:200]}')
        except urllib.error.URLError as exc:
            raise UserError(
                f'No se puede conectar con Evolution API en {self.evolution_url}.\n'
                f'Verifica que el servicio está activo. Detalle: {exc.reason}'
            )

    def _evo_create_instance(self):
        try:
            self._evo_request('POST', '/instance/create', {
                'instanceName': self.evolution_instance,
                'qrcode': True,
                'integration': 'WHATSAPP-BAILEYS',
            })
        except UserError:
            pass  # 409 ya gestionado en _evo_request

    def _evo_refresh_qr(self):
        try:
            data = self._evo_request('GET', f'/instance/connect/{self.evolution_instance}')
        except UserError as exc:
            _logger.warning('No se pudo obtener QR: %s', exc)
            return
        qr_b64 = (
            data.get('base64')
            or (data.get('qrcode') or {}).get('base64')
            or data.get('code', '')
        )
        if not qr_b64:
            return
        if ',' in qr_b64:
            qr_b64 = qr_b64.split(',', 1)[1]
        self.qr_code_image = qr_b64.encode()

    def _evo_get_state(self):
        try:
            data = self._evo_request('GET', f'/instance/connectionState/{self.evolution_instance}')
            return (data.get('instance') or {}).get('state') or data.get('state', 'close')
        except UserError:
            return 'close'

    def _evo_get_profile_info(self):
        try:
            data = self._evo_request('GET', '/instance/fetchInstances')
            for inst in (data if isinstance(data, list) else []):
                if inst.get('instance', {}).get('instanceName') == self.evolution_instance:
                    owner = inst.get('instance', {}).get('owner', '')
                    name = inst.get('instance', {}).get('profileName', '')
                    return f'{name} ({owner})' if name else owner
        except UserError:
            pass
        return ''

    def _evo_send_text(self, phone: str, body: str) -> dict:
        """Envía mensaje de texto vía Evolution API (modo QR)."""
        normalized = phone.lstrip('+')
        return self._evo_request('POST', f'/message/sendText/{self.evolution_instance}', {
            'number': normalized,
            'text': body,
        })

    def _evo_fetch_profile_picture_url(self, phone_or_jid: str):
        """Obtiene la URL de foto de perfil desde Evolution API.

        Retorna la URL como str, o None si no está disponible.
        """
        try:
            number = phone_or_jid.lstrip('+')
            data = self._evo_request(
                'GET',
                f'/chat/fetchProfilePicture/{self.evolution_instance}?number={number}',
            )
            return data.get('profilePictureUrl') or data.get('url') or None
        except Exception:
            return None

    def _evo_configure_webhook(self, base_url: str):
        """Configura el webhook de Evolution API para recibir mensajes entrantes."""
        webhook_url = base_url.rstrip('/') + '/whatsapp/evolution_webhook'
        try:
            self._evo_request('POST', f'/webhook/set/{self.evolution_instance}', {
                'url': webhook_url,
                'byEvents': True,
                'base64': False,
                'events': [
                    'MESSAGES_UPSERT',
                    'MESSAGES_UPDATE',
                    'CONNECTION_UPDATE',
                ],
            })
            _logger.info(
                'Evolution API webhook configurado: %s para instancia %s',
                webhook_url, self.evolution_instance,
            )
        except UserError as exc:
            _logger.warning('No se pudo configurar webhook Evolution: %s', exc)

    def action_configure_evolution_webhook(self):
        """Botón para configurar el webhook en Evolution API."""
        self.ensure_one()
        self._qr_check_fields()
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        if not base_url:
            raise UserError('Configura la URL base de Odoo en Ajustes → Parámetros técnicos → web.base.url')
        self._evo_configure_webhook(base_url)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Webhook configurado',
                'message': f'Evolution API enviará mensajes a {base_url}/whatsapp/evolution_webhook',
                'type': 'success',
                'sticky': False,
            },
        }
