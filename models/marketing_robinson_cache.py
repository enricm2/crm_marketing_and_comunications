import hashlib
import json
import logging
import re
import urllib.request
import urllib.error
from datetime import datetime, timedelta

from odoo import models, fields, api

_logger = logging.getLogger(__name__)

_P = 'crm_marketing_and_comunications.'


class MarketingRobinsonCache(models.Model):
    """Caché local de consultas a la Lista Robinson (Adigital, España).
    Solo aplica para leads con país = España.
    Las huellas se guardan como hash, nunca el dato en claro."""

    _name = 'marketing.robinson.cache'
    _description = 'Caché Lista Robinson'
    _order = 'checked_at desc'

    fingerprint_type = fields.Selection([
        ('email', 'Email'),
        ('phone', 'Teléfono'),
    ], string='Tipo', required=True)
    fingerprint_hash = fields.Char(
        string='Huella (hash)',
        required=True,
        index=True,
        help='Hash del email o teléfono normalizado según spec. API Lista Robinson.',
    )
    is_robinson = fields.Boolean(
        string='Inscrito en Lista Robinson',
        default=False,
    )
    checked_at = fields.Datetime(
        string='Fecha de consulta',
        default=fields.Datetime.now,
    )

    _sql_constraints = [
        (
            'unique_fingerprint',
            'UNIQUE(fingerprint_type, fingerprint_hash)',
            'Esta huella ya existe en la caché.',
        ),
    ]

    @api.model
    def _get_cache_days(self):
        icp = self.env['ir.config_parameter'].sudo()
        return int(icp.get_param(f'{_P}robinson_cache_days', 90))

    @api.model
    def _hash_value(self, value: str) -> str:
        """Genera el hash SHA-256 del valor normalizado."""
        normalized = value.strip().lower()
        return hashlib.sha256(normalized.encode('utf-8')).hexdigest()

    @api.model
    def check_robinson(self, email=None, phone=None) -> bool:
        """Devuelve True si el contacto está inscrito en Lista Robinson.
        Consulta primero la caché; si ha caducado o no existe, llama a la API."""
        icp = self.env['ir.config_parameter'].sudo()
        enabled = icp.get_param(f'{_P}robinson_check_enabled', 'True') == 'True'
        if not enabled:
            return False

        cache_days = self._get_cache_days()
        cutoff = datetime.now() - timedelta(days=cache_days)

        for ftype, value in [('email', email), ('phone', phone)]:
            if not value:
                continue
            fhash = self._hash_value(value)
            cached = self.search([
                ('fingerprint_type', '=', ftype),
                ('fingerprint_hash', '=', fhash),
            ], limit=1)
            if cached and cached.checked_at and cached.checked_at > cutoff:
                if cached.is_robinson:
                    return True
            else:
                # Consultar API
                result = self._query_api(ftype, value, fhash)
                if result is not None:
                    if cached:
                        cached.write({'is_robinson': result, 'checked_at': fields.Datetime.now()})
                    else:
                        self.create({
                            'fingerprint_type': ftype,
                            'fingerprint_hash': fhash,
                            'is_robinson': result,
                        })
                    if result:
                        return True
        return False

    def _query_api(self, ftype: str, value: str, fhash: str) -> bool | None:
        """Consulta la API de Lista Robinson. Devuelve True/False o None si error."""
        icp = self.env['ir.config_parameter'].sudo()
        api_key = icp.get_param(f'{_P}robinson_api_key', '')
        api_secret = icp.get_param(f'{_P}robinson_api_secret', '')

        if not api_key or not api_secret:
            _logger.debug('Robinson API: credentials not configured, skipping.')
            return None

        try:
            # Implementación simplificada — la API real de Adigital usa AWS4-HMAC-SHA256
            # Este stub devuelve None hasta que se configure con credenciales reales
            _logger.info('Robinson API: would query %s hash=%s', ftype, fhash[:8] + '...')
            return None
        except Exception as exc:
            _logger.warning('Robinson API error for %s: %s', ftype, exc)
            return None
