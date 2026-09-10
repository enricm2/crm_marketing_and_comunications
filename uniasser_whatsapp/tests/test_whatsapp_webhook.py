import hashlib
import hmac
import json
import time

from odoo.tests import HttpCase, tagged


@tagged('post_install', '-at_install', 'whatsapp')
class TestWhatsAppWebhook(HttpCase):
    """Tests de integración para el webhook de WhatsApp y el procesamiento de mensajes."""

    def setUp(self):
        super().setUp()
        self.account = self.env['whatsapp.account'].create({
            'name': 'Test Account',
            'phone_number_id': 'TEST_PHONE_ID_123',
            'waba_id': 'TEST_WABA_ID_456',
            'access_token': 'test_access_token',
            'app_secret': 'test_app_secret',
            'webhook_verify_token': 'test_verify_token_abc',
        })

    # ── Utilidades ────────────────────────────────────────────────────────────

    def _build_meta_payload(self, phone='34612345678', msg_type='text',
                            body='Hola test', wa_id=None):
        """Construye un payload de webhook de Meta con un mensaje entrante."""
        wa_id = wa_id or f'wamid.test_{int(time.time() * 1000)}'
        return {
            'object': 'whatsapp_business_account',
            'entry': [{
                'id': 'ENTRY_ID',
                'changes': [{
                    'value': {
                        'messaging_product': 'whatsapp',
                        'metadata': {
                            'display_phone_number': '34900000000',
                            'phone_number_id': 'TEST_PHONE_ID_123',
                        },
                        'contacts': [{
                            'profile': {'name': 'Test User'},
                            'wa_id': phone,
                        }],
                        'messages': [{
                            'from': phone,
                            'id': wa_id,
                            'timestamp': str(int(time.time())),
                            'type': msg_type,
                            'text': {'body': body} if msg_type == 'text' else {},
                        }],
                    },
                    'field': 'messages',
                }],
            }],
        }

    def _build_status_payload(self, wa_id, status='delivered'):
        """Construye un payload de actualización de estado."""
        return {
            'object': 'whatsapp_business_account',
            'entry': [{
                'id': 'ENTRY_ID',
                'changes': [{
                    'value': {
                        'messaging_product': 'whatsapp',
                        'metadata': {
                            'display_phone_number': '34900000000',
                            'phone_number_id': 'TEST_PHONE_ID_123',
                        },
                        'statuses': [{
                            'id': wa_id,
                            'status': status,
                            'timestamp': str(int(time.time())),
                            'recipient_id': '34612345678',
                        }],
                    },
                    'field': 'messages',
                }],
            }],
        }

    def _sign_payload(self, payload_bytes: bytes) -> str:
        """Genera la firma HMAC-SHA256 correcta para el payload."""
        return 'sha256=' + hmac.new(
            b'test_app_secret',
            payload_bytes,
            hashlib.sha256,
        ).hexdigest()

    # ── Test 1: Verificación del webhook (GET) ────────────────────────────────

    def test_webhook_verify_get(self):
        """GET /whatsapp/webhook con token válido devuelve el challenge."""
        challenge = 'my_challenge_xyz_12345'
        url = (
            f'/whatsapp/webhook'
            f'?hub.mode=subscribe'
            f'&hub.verify_token=test_verify_token_abc'
            f'&hub.challenge={challenge}'
        )
        response = self.url_open(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, challenge)

    def test_webhook_verify_get_wrong_token(self):
        """GET /whatsapp/webhook con token incorrecto devuelve 403."""
        url = (
            '/whatsapp/webhook'
            '?hub.mode=subscribe'
            '&hub.verify_token=WRONG_TOKEN'
            '&hub.challenge=should_not_appear'
        )
        response = self.url_open(url)
        self.assertEqual(response.status_code, 403)

    # ── Test 2: Mensaje entrante ──────────────────────────────────────────────

    def test_inbound_message_creates_record(self):
        """POST con payload válido de mensaje entrante crea un whatsapp.message."""
        wa_id = f'wamid.test_inbound_{int(time.time() * 1000)}'
        payload = self._build_meta_payload(
            phone='34611111111',
            body='Mensaje de prueba entrante',
            wa_id=wa_id,
        )
        payload_bytes = json.dumps(payload).encode()
        signature = self._sign_payload(payload_bytes)

        response = self.url_open(
            '/whatsapp/webhook',
            data=payload_bytes,
            headers={
                'Content-Type': 'application/json',
                'X-Hub-Signature-256': signature,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, 'OK')

        # Verificar que se creó el mensaje
        msg = self.env['whatsapp.message'].search([('wa_message_id', '=', wa_id)], limit=1)
        self.assertTrue(msg, 'Debe crearse un whatsapp.message con el wa_message_id del payload')
        self.assertEqual(msg.direction, 'inbound')
        self.assertEqual(msg.body, 'Mensaje de prueba entrante')
        self.assertEqual(msg.message_type, 'text')
        self.assertEqual(msg.account_id, self.account)
        self.assertTrue(msg.partner_id, 'Debe crearse o encontrarse un partner')

    # ── Test 3: Deduplicación ─────────────────────────────────────────────────

    def test_deduplication_same_wa_message_id(self):
        """Enviar el mismo wa_message_id dos veces no duplica el registro."""
        wa_id = f'wamid.test_dedup_{int(time.time() * 1000)}'
        payload = self._build_meta_payload(wa_id=wa_id, body='Primero')
        payload_bytes = json.dumps(payload).encode()
        signature = self._sign_payload(payload_bytes)
        headers = {
            'Content-Type': 'application/json',
            'X-Hub-Signature-256': signature,
        }

        # Primera llamada
        r1 = self.url_open('/whatsapp/webhook', data=payload_bytes, headers=headers)
        self.assertEqual(r1.status_code, 200)

        # Segunda llamada con mismo wa_id (simulando reintento de Meta)
        r2 = self.url_open('/whatsapp/webhook', data=payload_bytes, headers=headers)
        self.assertEqual(r2.status_code, 200)

        count = self.env['whatsapp.message'].search_count([('wa_message_id', '=', wa_id)])
        self.assertEqual(count, 1, 'Solo debe existir UN mensaje a pesar del envío duplicado')

    # ── Test 4: Actualización de estado ──────────────────────────────────────

    def test_status_update_delivered(self):
        """POST de status update cambia el estado del mensaje saliente a 'delivered'."""
        wa_id = f'wamid.test_status_{int(time.time() * 1000)}'
        # Crear mensaje saliente previo
        msg = self.env['whatsapp.message'].create({
            'account_id': self.account.id,
            'wa_message_id': wa_id,
            'direction': 'outbound',
            'body': 'Mensaje saliente de prueba',
            'message_type': 'text',
            'status': 'sent',
            'phone': '34699999999',
        })

        # Enviar status update
        payload = self._build_status_payload(wa_id=wa_id, status='delivered')
        payload_bytes = json.dumps(payload).encode()
        signature = self._sign_payload(payload_bytes)

        response = self.url_open(
            '/whatsapp/webhook',
            data=payload_bytes,
            headers={
                'Content-Type': 'application/json',
                'X-Hub-Signature-256': signature,
            },
        )
        self.assertEqual(response.status_code, 200)

        msg.invalidate_recordset()
        self.assertEqual(msg.status, 'delivered',
                         'El estado del mensaje debe haberse actualizado a "delivered"')

    def test_status_update_read(self):
        """Status update 'read' avanza correctamente desde 'delivered'."""
        wa_id = f'wamid.test_read_{int(time.time() * 1000)}'
        msg = self.env['whatsapp.message'].create({
            'account_id': self.account.id,
            'wa_message_id': wa_id,
            'direction': 'outbound',
            'body': 'Test leído',
            'message_type': 'text',
            'status': 'delivered',
            'phone': '34688888888',
        })

        payload = self._build_status_payload(wa_id=wa_id, status='read')
        payload_bytes = json.dumps(payload).encode()
        signature = self._sign_payload(payload_bytes)

        self.url_open(
            '/whatsapp/webhook',
            data=payload_bytes,
            headers={
                'Content-Type': 'application/json',
                'X-Hub-Signature-256': signature,
            },
        )
        msg.invalidate_recordset()
        self.assertEqual(msg.status, 'read')

    def test_status_does_not_go_backwards(self):
        """Un estado 'read' no debe retroceder a 'sent'."""
        wa_id = f'wamid.test_noback_{int(time.time() * 1000)}'
        msg = self.env['whatsapp.message'].create({
            'account_id': self.account.id,
            'wa_message_id': wa_id,
            'direction': 'outbound',
            'body': 'No retroceders',
            'message_type': 'text',
            'status': 'read',
            'phone': '34677777777',
        })

        # Intentar "retroceder" a sent
        self.env['whatsapp.message']._process_status_update({'id': wa_id, 'status': 'sent'})
        msg.invalidate_recordset()
        self.assertEqual(msg.status, 'read', 'El estado no debe retroceder de read a sent')

    # ── Test 5: Procesamiento directo ─────────────────────────────────────────

    def test_process_inbound_creates_partner(self):
        """_process_inbound crea un partner automático si no existe."""
        phone = '34666000111'
        # Asegurar que no existe el partner
        existing = self.env['res.partner'].search([('mobile', 'like', phone[-9:])])
        existing.unlink()

        wa_id = f'wamid.test_newpartner_{int(time.time() * 1000)}'
        msg_data = {
            'id': wa_id,
            'from': phone,
            'timestamp': str(int(time.time())),
            'type': 'text',
            'text': {'body': 'Soy un contacto nuevo'},
        }
        record = self.env['whatsapp.message']._process_inbound(self.account, msg_data)
        self.assertTrue(record, 'Debe devolver el mensaje creado')
        self.assertTrue(record.partner_id, 'Debe crearse un partner automático')
        self.assertIn('WhatsApp', record.partner_id.name)

    def test_process_inbound_finds_existing_partner(self):
        """_process_inbound encuentra un partner existente por su móvil."""
        phone_suffix = '611222333'
        partner = self.env['res.partner'].create({
            'name': 'Partner Existente Test',
            'mobile': f'+34{phone_suffix}',
        })
        wa_id = f'wamid.test_existing_{int(time.time() * 1000)}'
        msg_data = {
            'id': wa_id,
            'from': f'34{phone_suffix}',
            'timestamp': str(int(time.time())),
            'type': 'text',
            'text': {'body': 'Hola desde número existente'},
        }
        record = self.env['whatsapp.message']._process_inbound(self.account, msg_data)
        self.assertEqual(record.partner_id, partner,
                         'Debe asociarse al partner existente por número de móvil')
