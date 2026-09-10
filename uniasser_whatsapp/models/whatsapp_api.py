import hashlib
import hmac
import json
import logging
import re

import requests

_logger = logging.getLogger(__name__)
from .graph_config import GRAPH_BASE_DEFAULT

# Versión única para todo el módulo — ver graph_config.py
BASE_URL = GRAPH_BASE_DEFAULT


class WhatsAppAPI:
    """Pure HTTP layer for WhatsApp Business Cloud API (Meta Graph API).

    La versión se define en graph_config.py, no aquí.

    Not an Odoo model — plain Python class used by whatsapp.account via _get_api().
    """

    def __init__(self, phone_number_id: str, access_token: str, app_secret: str = ''):
        self.phone_number_id = phone_number_id
        self.access_token = access_token
        self.app_secret = app_secret
        self._session = requests.Session()
        self._session.headers.update({
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json',
        })

    # ------------------------------------------------------------------
    # Phone info / connection test
    # ------------------------------------------------------------------

    def get_phone_info(self) -> dict:
        """GET /{phone_number_id} — verifica credenciales y devuelve info del número."""
        url = f'{BASE_URL}/{self.phone_number_id}'
        response = self._session.get(url, timeout=10)
        response.raise_for_status()
        return response.json()

    # ------------------------------------------------------------------
    # Sending messages
    # ------------------------------------------------------------------

    def send_text(self, to: str, body: str) -> dict:
        """POST /{phone_number_id}/messages — tipo text."""
        url = f'{BASE_URL}/{self.phone_number_id}/messages'
        payload = {
            'messaging_product': 'whatsapp',
            'recipient_type': 'individual',
            'to': to,
            'type': 'text',
            'text': {
                'preview_url': False,
                'body': body,
            },
        }
        response = self._session.post(url, json=payload, timeout=15)
        response.raise_for_status()
        return response.json()

    def send_template(self, to: str, template_name: str, language_code: str,
                      components: list = None) -> dict:
        """POST /{phone_number_id}/messages — tipo template."""
        url = f'{BASE_URL}/{self.phone_number_id}/messages'
        template_payload = {
            'name': template_name,
            'language': {'code': language_code},
        }
        if components:
            template_payload['components'] = components

        payload = {
            'messaging_product': 'whatsapp',
            'recipient_type': 'individual',
            'to': to,
            'type': 'template',
            'template': template_payload,
        }
        response = self._session.post(url, json=payload, timeout=15)
        response.raise_for_status()
        return response.json()

    def send_media(self, to: str, media_type: str, media_id: str, caption: str = '') -> dict:
        """POST /{phone_number_id}/messages — tipo image/document/audio/video."""
        url = f'{BASE_URL}/{self.phone_number_id}/messages'
        media_payload = {'id': media_id}
        if caption and media_type in ('image', 'document', 'video'):
            media_payload['caption'] = caption

        payload = {
            'messaging_product': 'whatsapp',
            'recipient_type': 'individual',
            'to': to,
            'type': media_type,
            media_type: media_payload,
        }
        response = self._session.post(url, json=payload, timeout=15)
        response.raise_for_status()
        return response.json()

    # ------------------------------------------------------------------
    # Media upload / download
    # ------------------------------------------------------------------

    def upload_media(self, file_bytes: bytes, mimetype: str, filename: str) -> str:
        """POST /{phone_number_id}/media — devuelve media_id (str)."""
        url = f'{BASE_URL}/{self.phone_number_id}/media'
        # Must send as multipart/form-data — override Content-Type for this request
        headers = {
            'Authorization': f'Bearer {self.access_token}',
        }
        files = {
            'file': (filename, file_bytes, mimetype),
        }
        data = {
            'messaging_product': 'whatsapp',
            'type': mimetype,
        }
        response = requests.post(url, headers=headers, files=files, data=data, timeout=60)
        response.raise_for_status()
        result = response.json()
        media_id = result.get('id', '')
        if not media_id:
            raise ValueError(f'Meta no devolvió media_id: {result}')
        return media_id

    def download_media(self, media_id: str) -> bytes:
        """GET /{media_id} → obtiene URL temporal → descarga bytes del archivo."""
        # Step 1: Get the download URL
        url = f'{BASE_URL}/{media_id}'
        response = self._session.get(url, timeout=10)
        response.raise_for_status()
        info = response.json()
        download_url = info.get('url')
        if not download_url:
            raise ValueError(f'Meta no devolvió URL de descarga para media_id={media_id}')

        # Step 2: Download the file (use same session for auth header)
        dl_response = self._session.get(download_url, timeout=60)
        dl_response.raise_for_status()
        return dl_response.content

    # ------------------------------------------------------------------
    # Templates
    # ------------------------------------------------------------------

    def get_templates(self, waba_id: str) -> list:
        """GET /{waba_id}/message_templates — devuelve lista de templates."""
        url = f'{BASE_URL}/{waba_id}/message_templates'
        params = {
            'limit': 250,
            'fields': 'id,name,language,category,status,components',
        }
        all_templates = []
        while url:
            response = self._session.get(url, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()
            all_templates.extend(data.get('data', []))
            # Handle pagination
            paging = data.get('paging', {})
            next_url = paging.get('next')
            url = next_url if next_url else None
            params = {}  # next URL already has params embedded
        return all_templates

    # ------------------------------------------------------------------
    # Signature verification
    # ------------------------------------------------------------------

    @staticmethod
    def verify_signature(payload_bytes: bytes, signature_header: str, app_secret: str) -> bool:
        """Valida X-Hub-Signature-256 = sha256(app_secret, payload).

        Returns True if the signature is valid, False otherwise.
        """
        if not signature_header or not app_secret:
            return False
        expected = 'sha256=' + hmac.new(
            app_secret.encode('utf-8'),
            payload_bytes,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature_header)

    # ------------------------------------------------------------------
    # Phone normalization
    # ------------------------------------------------------------------

    @staticmethod
    def normalize_phone(phone: str) -> str:
        """Normaliza número a E.164 sin '+'.

        Añade prefijo 34 (España) si empieza por 6, 7 o 9 y tiene 9 dígitos.
        Elimina espacios, guiones, paréntesis, puntos y el signo +.
        """
        clean = re.sub(r'[\s\+\-\(\)\.]', '', phone or '')
        if len(clean) == 9 and clean[0] in '679':
            clean = '34' + clean
        return clean
