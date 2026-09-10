"""Proveedores de envío de email (ESP) con patrón adaptador."""
import datetime
import hashlib
import hmac
import json
import logging
import urllib.request
import urllib.error
import urllib.parse

from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class MarketingEspProvider(models.Model):
    """Registra y gestiona los ESP (Email Service Providers) disponibles.
    Cada ESP tiene su propio adaptador Python que implementa la interfaz abstracta."""

    _name = 'marketing.esp.provider'
    _description = 'Proveedor de envío de email (ESP)'
    _order = 'name'

    name = fields.Selection([
        ('acumbamail', 'Acumbamail'),
        ('sendgrid', 'SendGrid'),
        ('mailchimp', 'Mailchimp'),
        ('brevo', 'Brevo (Sendinblue)'),
        ('mailgun', 'Mailgun'),
        ('aws_ses', 'Amazon SES'),
        ('odoo_smtp', 'Servidor de Odoo (SMTP propio)'),
    ], string='Proveedor', required=True)
    mail_server_id = fields.Many2one(
        'ir.mail_server',
        string='Servidor de correo saliente',
        help='Solo aplica a "Servidor de Odoo". Si lo dejas vacío, Odoo elegirá '
             'el servidor de mayor prioridad capaz de enviar desde la dirección '
             'remitente indicada abajo.',
    )
    display_name_custom = fields.Char(string='Nombre mostrado')
    api_token = fields.Char(
        string='API Token / Key',
        groups='base.group_system',
        help='Token principal de la API del proveedor.',
    )
    api_secondary_key = fields.Char(
        string='Clave secundaria',
        groups='base.group_system',
        help='Para proveedores que requieren dos claves (ej. Mailchimp: key + server prefix).',
    )
    api_endpoint = fields.Char(
        string='Endpoint base (opcional)',
        help='Dejar vacío para usar el endpoint oficial del proveedor.',
    )
    from_name = fields.Char(string='Nombre del remitente')
    from_email = fields.Char(string='Email del remitente')
    active = fields.Boolean(default=True)
    webhook_secret = fields.Char(
        string='Secreto webhook',
        groups='base.group_system',
        help='Para validar la autenticidad de webhooks entrantes del ESP.',
    )
    notes = fields.Text(string='Notas de configuración')
    campaign_count = fields.Integer(
        string='Campañas',
        compute='_compute_campaign_count',
    )

    def _compute_campaign_count(self):
        for rec in self:
            rec.campaign_count = self.env['marketing.campaign'].search_count([
                ('esp_provider_id', '=', rec.id)
            ])

    def action_test_connection(self):
        """Prueba la conexión con el ESP."""
        self.ensure_one()
        adapter = self._get_adapter()
        try:
            result = adapter.test_connection()
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Conexión OK',
                    'message': result or f'Conectado a {self.name} correctamente.',
                    'type': 'success',
                },
            }
        except Exception as exc:
            raise UserError(f'Error de conexión con {self.name}:\n{exc}') from exc

    def _get_adapter(self):
        """Devuelve el adaptador correspondiente al proveedor."""
        self.ensure_one()
        adapters = {
            'acumbamail': AccumbamailAdapter,
            'sendgrid': SendgridAdapter,
            'mailchimp': MailchimpAdapter,
            'brevo': BrevoAdapter,
            'mailgun': MailgunAdapter,
            'aws_ses': AwsSesAdapter,
            'odoo_smtp': OdooSmtpAdapter,
        }
        cls = adapters.get(self.name)
        if not cls:
            raise UserError(f'Adaptador no implementado para: {self.name}')
        return cls(self)

    def send_email(self, to_email, to_name, subject, html_body, text_body=None,
                   unsubscribe_url=None, campaign_ref=None):
        """Envía un email a través de este ESP."""
        self.ensure_one()
        return self._get_adapter().send_email(
            to_email=to_email,
            to_name=to_name,
            subject=subject,
            html_body=html_body,
            text_body=text_body,
            unsubscribe_url=unsubscribe_url,
            campaign_ref=campaign_ref,
        )

    def parse_webhook(self, payload: dict) -> list[dict]:
        """Procesa el payload de un webhook del ESP.
        Devuelve lista de eventos: [{'type': ..., 'email': ..., 'timestamp': ...}]"""
        self.ensure_one()
        return self._get_adapter().parse_webhook(payload)


# ── Adaptadores ──────────────────────────────────────────────────────────────

class EspAdapterBase:
    """Interfaz abstracta que deben implementar todos los adaptadores."""

    def __init__(self, provider: MarketingEspProvider):
        self.provider = provider
        self.token = provider.api_token or ''
        self.secondary_key = provider.api_secondary_key or ''
        self.from_name = provider.from_name or ''
        self.from_email = provider.from_email or ''

    def _post(self, url, payload, headers=None):
        data = json.dumps(payload).encode('utf-8')
        req_headers = {'Content-Type': 'application/json'}
        if headers:
            req_headers.update(headers)
        req = urllib.request.Request(url, data=data, headers=req_headers, method='POST')
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode('utf-8', errors='replace')
            raise UserError(f'ESP HTTP {exc.code}: {body[:400]}') from exc

    def test_connection(self) -> str:
        raise NotImplementedError

    def send_email(self, to_email, to_name, subject, html_body, text_body=None,
                   unsubscribe_url=None, campaign_ref=None) -> dict:
        raise NotImplementedError

    def parse_webhook(self, payload: dict) -> list[dict]:
        return []


class AccumbamailAdapter(EspAdapterBase):
    """Adaptador para Acumbamail (acumbamail.com)."""

    BASE_URL = 'https://acumbamail.com/api/1'

    def _headers(self):
        return {'auth_token': self.token}

    def test_connection(self) -> str:
        url = f'{self.BASE_URL}/getLists/'
        req = urllib.request.Request(
            url,
            headers={**self._headers(), 'Content-Type': 'application/json'},
            method='GET',
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
            return f'Acumbamail OK — {len(data)} listas disponibles.'
        except Exception as exc:
            raise UserError(f'Error Acumbamail: {exc}') from exc

    def send_email(self, to_email, to_name, subject, html_body, text_body=None,
                   unsubscribe_url=None, campaign_ref=None) -> dict:
        """Envío transaccional individual vía Acumbamail."""
        payload = {
            'auth_token': self.token,
            'to_email': to_email,
            'to_name': to_name or '',
            'from_email': self.from_email,
            'from_name': self.from_name,
            'subject': subject,
            'html': html_body,
        }
        if text_body:
            payload['text'] = text_body
        if unsubscribe_url:
            payload['list_unsubscribe'] = f'<{unsubscribe_url}>'
        url = f'{self.BASE_URL}/sendOne/'
        return self._post(url, payload)

    def parse_webhook(self, payload: dict) -> list[dict]:
        events = []
        for event in payload if isinstance(payload, list) else [payload]:
            etype = event.get('event', '')
            mapping = {
                'open': 'opened',
                'click': 'clicked',
                'bounce': 'bounced',
                'unsubscribe': 'unsubscribed',
                'deliver': 'delivered',
            }
            mapped = mapping.get(etype)
            if mapped:
                events.append({
                    'type': mapped,
                    'email': event.get('email', ''),
                    'timestamp': event.get('timestamp'),
                    'url': event.get('url'),
                })
        return events

    def capacidades(self):
        return {'entregado': True, 'abierto': True, 'clic': True,
                'rebote': True, 'baja': True}


class SendgridAdapter(EspAdapterBase):
    """Adaptador para SendGrid."""

    BASE_URL = 'https://api.sendgrid.com/v3'

    def test_connection(self) -> str:
        req = urllib.request.Request(
            f'{self.BASE_URL}/user/profile',
            headers={'Authorization': f'Bearer {self.token}'},
            method='GET',
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        return f'SendGrid OK — cuenta: {data.get("email", "?")}.'

    def send_email(self, to_email, to_name, subject, html_body, text_body=None,
                   unsubscribe_url=None, campaign_ref=None) -> dict:
        payload = {
            'personalizations': [{'to': [{'email': to_email, 'name': to_name or ''}]}],
            'from': {'email': self.from_email, 'name': self.from_name},
            'subject': subject,
            'content': [{'type': 'text/html', 'value': html_body}],
        }
        if text_body:
            payload['content'].insert(0, {'type': 'text/plain', 'value': text_body})
        headers = {
            'Authorization': f'Bearer {self.token}',
            'Content-Type': 'application/json',
        }
        return self._post(f'{self.BASE_URL}/mail/send', payload, headers=headers)

    def parse_webhook(self, payload: dict) -> list[dict]:
        events = []
        for event in payload if isinstance(payload, list) else [payload]:
            etype = event.get('event', '')
            if etype in ('open', 'click', 'bounce', 'unsubscribe', 'delivered'):
                events.append({
                    'type': etype.replace('open', 'opened').replace('click', 'clicked'),
                    'email': event.get('email', ''),
                    'timestamp': event.get('timestamp'),
                    'url': event.get('url'),
                })
        return events

    def capacidades(self):
        return {'entregado': True, 'abierto': True, 'clic': True,
                'rebote': True, 'baja': True}


class MailchimpAdapter(EspAdapterBase):
    """Adaptador para Mailchimp (Transactional / Mandrill)."""

    def _base_url(self):
        server = self.secondary_key or 'us1'
        return f'https://{server}.api.mailchimp.com/3.0'

    def test_connection(self) -> str:
        import base64
        credentials = base64.b64encode(f'user:{self.token}'.encode()).decode()
        req = urllib.request.Request(
            f'{self._base_url()}/',
            headers={'Authorization': f'Basic {credentials}'},
            method='GET',
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        return f'Mailchimp OK — cuenta: {data.get("account_name", "?")}.'

    def send_email(self, to_email, to_name, subject, html_body, text_body=None,
                   unsubscribe_url=None, campaign_ref=None) -> dict:
        # Mailchimp transaccional requiere Mandrill — simplificado
        raise UserError('Envío transaccional de Mailchimp requiere configurar Mandrill. Contacta con soporte.')

    def parse_webhook(self, payload: dict) -> list[dict]:
        return []

    def capacidades(self):
        return {'entregado': True, 'abierto': True, 'clic': True,
                'rebote': True, 'baja': True}


class BrevoAdapter(EspAdapterBase):
    """Adaptador para Brevo (Sendinblue)."""

    BASE_URL = 'https://api.brevo.com/v3'

    def test_connection(self) -> str:
        req = urllib.request.Request(
            f'{self.BASE_URL}/account',
            headers={'api-key': self.token},
            method='GET',
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        return f'Brevo OK — plan: {data.get("plan", [{}])[0].get("type", "?")}.'

    def send_email(self, to_email, to_name, subject, html_body, text_body=None,
                   unsubscribe_url=None, campaign_ref=None) -> dict:
        payload = {
            'sender': {'email': self.from_email, 'name': self.from_name},
            'to': [{'email': to_email, 'name': to_name or ''}],
            'subject': subject,
            'htmlContent': html_body,
        }
        if text_body:
            payload['textContent'] = text_body
        headers = {'api-key': self.token, 'Content-Type': 'application/json'}
        return self._post(f'{self.BASE_URL}/smtp/email', payload, headers=headers)

    def parse_webhook(self, payload: dict) -> list[dict]:
        events = []
        for event in payload if isinstance(payload, list) else [payload]:
            etype = event.get('event', '')
            mapping = {'opened': 'opened', 'click': 'clicked', 'hardBounce': 'bounced',
                       'softBounce': 'bounced', 'unsubscribed': 'unsubscribed',
                       'delivered': 'delivered'}
            mapped = mapping.get(etype)
            if mapped:
                events.append({
                    'type': mapped,
                    'email': event.get('email', ''),
                    'timestamp': event.get('ts'),
                    'url': event.get('link'),
                })
        return events

    def capacidades(self):
        return {'entregado': True, 'abierto': True, 'clic': True,
                'rebote': True, 'baja': True}


class MailgunAdapter(EspAdapterBase):
    """Adaptador para Mailgun."""

    def _base_url(self):
        domain = self.secondary_key or 'mg.example.com'
        return f'https://api.mailgun.net/v3/{domain}'

    def test_connection(self) -> str:
        import base64
        credentials = base64.b64encode(f'api:{self.token}'.encode()).decode()
        req = urllib.request.Request(
            f'https://api.mailgun.net/v3/domains',
            headers={'Authorization': f'Basic {credentials}'},
            method='GET',
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        return f'Mailgun OK — {data.get("total_count", 0)} dominio(s).'

    def send_email(self, to_email, to_name, subject, html_body, text_body=None,
                   unsubscribe_url=None, campaign_ref=None) -> dict:
        import base64
        recipient = f'{to_name} <{to_email}>' if to_name else to_email
        data = urllib.parse.urlencode({
            'from': f'{self.from_name} <{self.from_email}>',
            'to': recipient,
            'subject': subject,
            'html': html_body,
        }).encode()
        credentials = base64.b64encode(f'api:{self.token}'.encode()).decode()
        req = urllib.request.Request(
            f'{self._base_url()}/messages',
            data=data,
            headers={
                'Authorization': f'Basic {credentials}',
                'Content-Type': 'application/x-www-form-urlencoded',
            },
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())

    def parse_webhook(self, payload: dict) -> list[dict]:
        events = []
        event_data = payload.get('event-data', payload)
        etype = event_data.get('event', '')
        mapping = {'opened': 'opened', 'clicked': 'clicked', 'failed': 'bounced',
                   'unsubscribed': 'unsubscribed', 'delivered': 'delivered'}
        mapped = mapping.get(etype)
        if mapped:
            events.append({
                'type': mapped,
                'email': (event_data.get('recipient') or
                          event_data.get('message', {}).get('headers', {}).get('to', '')),
                'timestamp': event_data.get('timestamp'),
                'url': event_data.get('url'),
            })
        return events

    def capacidades(self):
        return {'entregado': True, 'abierto': True, 'clic': True,
                'rebote': True, 'baja': True}


class AwsSesAdapter(EspAdapterBase):
    """Amazon SES vía SMTP.

    Configuración esperada:
      · Servidor SMTP (api_endpoint)     → El servidor SMTP de AWS (ej. `email-smtp.eu-west-1.amazonaws.com`).
                                           O solo la región (ej. `eu-west-1`), o con puerto (ej. `host:587`).
      · Usuario SMTP (api_token)         → El usuario SMTP de AWS (Access Key ID).
      · Contraseña SMTP (api_secondary_key) → La contraseña SMTP generada por AWS.
      · Configuration Set (webhook_secret)  → Nombre opcional del Configuration Set de AWS (necesario para métricas).
    """

    REGION_POR_DEFECTO = 'eu-west-1'

    def _get_smtp_connection(self):
        import smtplib
        server_part = (self.provider.api_endpoint or '').strip()
        if not server_part:
            # Si está vacío, por defecto usamos la región por defecto
            host = f'email-smtp.{self.REGION_POR_DEFECTO}.amazonaws.com'
            port = 587
        elif '.' in server_part and not '/' in server_part and not ':' in server_part:
            # Si es un dominio (ej. email-smtp.eu-west-1.amazonaws.com)
            host = server_part
            port = 587
        elif ':' in server_part:
            host, port_str = server_part.split(':', 1)
            try:
                port = int(port_str)
            except ValueError:
                port = 587
        else:
            # Si es solo una región (ej. "eu-west-1" o "us-east-1")
            host = f'email-smtp.{server_part}.amazonaws.com'
            port = 587

        smtp_user = self.token
        smtp_password = self.secondary_key

        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=30)
        else:
            server = smtplib.SMTP(host, port, timeout=30)
            server.ehlo()
            if server.has_extn('STARTTLS'):
                server.starttls()
                server.ehlo()

        if smtp_user and smtp_password:
            try:
                server.login(smtp_user, smtp_password)
            except smtplib.SMTPAuthenticationError as exc:
                raise UserError(
                    f'Error de autenticación SMTP en Amazon SES: verifica el usuario y la contraseña.\n{exc}'
                ) from exc
            except Exception as exc:
                raise UserError(
                    f'Error al conectar o autenticar con Amazon SES SMTP: {exc}'
                ) from exc
        return server

    def _config_set(self):
        return (self.provider.webhook_secret or '').strip()

    def test_connection(self) -> str:
        if not self.token or not self.secondary_key:
            raise UserError(
                'Faltan credenciales de AWS: pon el Usuario SMTP en "Usuario SMTP" '
                'y la Contraseña SMTP en "Contraseña SMTP".'
            )
        if not self.from_email:
            raise UserError('Falta el email del remitente, que en SES debe estar '
                            'verificado (dominio o dirección).')
        
        # Probamos conectividad SMTP
        try:
            conn = self._get_smtp_connection()
            conn.quit()
        except Exception as exc:
            raise UserError(f'Fallo al conectar al servidor SMTP de Amazon SES: {exc}')

        # Intentamos enviar un email de prueba para garantizar que todo está validado
        self.send_email(
            'success@simulator.amazonses.com', 'Prueba',
            'Prueba de conexión', '<p>Prueba de conexión desde Odoo.</p>',
        )
        
        aviso = ''
        if not self._config_set():
            aviso = ('\n\nAVISO: no hay Configuration Set configurado. El envío funcionará, '
                     'pero no llegará ninguna estadística de entregas, de aperturas ni rebotes.')
        
        # Extraemos la región para mostrarla
        server_part = (self.provider.api_endpoint or '').strip()
        region = self.REGION_POR_DEFECTO
        if server_part and '.' in server_part:
            parts = server_part.split('.')
            if len(parts) > 1 and 'smtp' in parts[0]:
                region = parts[1]
        elif server_part and '.' not in server_part:
            region = server_part

        return f'Conexión SMTP correcta con Amazon SES ({region}).{aviso}'

    def send_email(self, to_email, to_name, subject, html_body, text_body=None,
                   unsubscribe_url=None, campaign_ref=None) -> dict:
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        from email.header import Header
        
        msg = MIMEMultipart('alternative')
        msg['Subject'] = Header(subject, 'utf-8')

        if self.from_name:
            encoded_from = Header(self.from_name, 'utf-8').encode()
            msg['From'] = f'"{encoded_from}" <{self.from_email}>'
        else:
            msg['From'] = self.from_email

        if to_name:
            encoded_to = Header(to_name, 'utf-8').encode()
            msg['To'] = f'"{encoded_to}" <{to_email}>'
        else:
            msg['To'] = to_email

        if unsubscribe_url:
            msg['List-Unsubscribe'] = f'<{unsubscribe_url}>'
            msg['List-Unsubscribe-Post'] = 'List-Unsubscribe=One-Click'

        config_set = self._config_set()
        if config_set:
            msg['X-SES-CONFIGURATION-SET'] = config_set
        if campaign_ref:
            msg['X-SES-MESSAGE-TAGS'] = f'campana={campaign_ref}'

        if text_body:
            msg.attach(MIMEText(text_body, 'plain', 'utf-8'))
        msg.attach(MIMEText(html_body, 'html', 'utf-8'))

        try:
            conn = self._get_smtp_connection()
            conn.sendmail(self.from_email, [to_email], msg.as_string())
            conn.quit()
        except Exception as exc:
            raise UserError(f'Error al enviar correo vía Amazon SES SMTP: {exc}')

        return {'success': True, 'message_id': msg.get('Message-ID', 'smtp-sent')}

    def parse_webhook(self, payload: dict) -> list[dict]:
        """Interpreta una notificación SNS de SES.

        SNS envuelve el evento: el JSON real viene como texto dentro de
        `Message`. Además, antes de entregar nada, SNS manda una petición de
        confirmación de suscripción, que aquí se ignora (hay que confirmarla
        desde la consola de AWS o visitando la URL que trae).
        """
        if payload.get('Type') == 'SubscriptionConfirmation':
            _logger.info('SES/SNS: confirmación de suscripción pendiente en %s',
                         payload.get('SubscribeURL', '')[:120])
            return []

        datos = payload
        if 'Message' in payload and isinstance(payload['Message'], str):
            try:
                datos = json.loads(payload['Message'])
            except ValueError:
                return []

        mapping = {
            'Delivery': 'delivered',
            'Open': 'opened',
            'Click': 'clicked',
            'Bounce': 'bounced',
            'Complaint': 'unsubscribed',   # marcar como spam equivale a baja
            'Reject': 'bounced',
            'DeliveryDelay': None,
            'Send': None,
        }
        mapped = mapping.get(datos.get('eventType') or datos.get('notificationType'))
        if not mapped:
            return []

        destinatarios = (datos.get('mail') or {}).get('destination') or []
        marca = (datos.get('mail') or {}).get('timestamp')
        url = (datos.get('click') or {}).get('link')
        return [{'type': mapped, 'email': email, 'timestamp': marca, 'url': url}
                for email in destinatarios]

    def capacidades(self):
        # Sin Configuration Set, SES acepta el correo y no informa de nada más.
        # Decirlo aquí evita que la campaña presuma de métricas que no existen.
        if not self._config_set():
            return {'entregado': False, 'abierto': False, 'clic': False,
                    'rebote': False, 'baja': True}
        return {'entregado': True, 'abierto': True, 'clic': True,
                'rebote': True, 'baja': True}


class OdooSmtpAdapter(EspAdapterBase):
    """Envía por el servidor de correo saliente del propio Odoo (ir.mail_server).

    Pensado para cuando el ESP no sirve: por precio, o porque su política de uso
    no cubre el tipo de envío. No habla con ninguna API externa; usa `mail.mail`,
    que a su vez usa el SMTP configurado en Ajustes → Técnico.

    QUÉ SE PIERDE respecto a un ESP de verdad: no hay webhooks, así que **no hay
    seguimiento de aperturas, clics ni rebotes**. Los campos `delivered_at`,
    `opened_at` y `clicked_at` de las líneas de campaña se quedarán vacíos.
    """

    def _mail_server(self):
        """Servidor a usar: el fijado en el proveedor, o el de mayor prioridad."""
        server = self.provider.mail_server_id
        if server:
            return server
        server = self.provider.env['ir.mail_server'].sudo().search(
            [], order='sequence', limit=1)
        if not server:
            raise UserError(
                'No hay ningún servidor de correo saliente configurado en Odoo.\n'
                'Ve a Ajustes → Técnico → Correo electrónico → Servidores de '
                'correo saliente y crea uno.'
            )
        return server

    def test_connection(self) -> str:
        server = self._mail_server()
        try:
            server.test_smtp_connection()
        except Exception as exc:
            raise UserError(
                f'No se pudo conectar con "{server.name}":\n\n{exc}'
            ) from exc
        return (f'Conexión correcta con "{server.name}" '
                f'({server.smtp_host}:{server.smtp_port}).')

    def send_email(self, to_email, to_name, subject, html_body, text_body=None,
                   unsubscribe_url=None, campaign_ref=None) -> dict:
        env = self.provider.env
        server = self._mail_server()

        # Remitente: el del proveedor si está puesto; si no, el usuario SMTP.
        # Con Gmail da igual lo que pongamos: reescribe el From con la cuenta
        # autenticada, así que conviene que coincidan o el email saldrá "en
        # nombre de" y eso perjudica la entregabilidad.
        remitente = self.from_email or server.smtp_user
        if not remitente:
            raise UserError(
                f'No hay dirección de remitente: rellena "Email del remitente" '
                f'en el proveedor o el usuario SMTP en "{server.name}".'
            )
        if self.from_name:
            remitente = f'{self.from_name} <{remitente}>'

        cabeceras = {}
        if unsubscribe_url:
            # Hace que Gmail muestre su enlace de baja nativo. Pesa en la
            # reputación del remitente, así que en envíos en frío importa.
            cabeceras['List-Unsubscribe'] = f'<{unsubscribe_url}>'
            cabeceras['List-Unsubscribe-Post'] = 'List-Unsubscribe=One-Click'
        if campaign_ref:
            cabeceras['X-Campaign-Ref'] = campaign_ref

        mail = env['mail.mail'].sudo().create({
            'subject': subject,
            'body_html': html_body or '',
            'email_to': to_email,
            'email_from': remitente,
            'mail_server_id': server.id,
            'auto_delete': False,
            'headers': repr(cabeceras) if cabeceras else False,
        })
        mail.send(raise_exception=True)

        if mail.state == 'exception':
            raise UserError(
                f'El servidor SMTP rechazó el envío a {to_email}:\n\n'
                f'{mail.failure_reason or "sin detalle"}'
            )
        return {'message_id': mail.message_id or str(mail.id), 'state': mail.state}

    def parse_webhook(self, payload: dict) -> list[dict]:
        # Sin ESP no hay webhooks: el seguimiento de aperturas/clics no existe.
        return []

    def capacidades(self):
        # El SMTP solo confirma que el servidor ACEPTÓ el mensaje, no que
        # llegara al buzón. Sin webhooks no hay aperturas, clics ni rebotes.
        return {'entregado': False, 'abierto': False, 'clic': False,
                'rebote': False, 'baja': True}
