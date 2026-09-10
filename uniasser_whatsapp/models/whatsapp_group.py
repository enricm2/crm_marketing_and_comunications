"""Grupos de WhatsApp — mapeo persistente group_jid → subject."""
import json
import logging
import urllib.request

from odoo import models, fields, api
from .graph_config import get_graph_base

_logger = logging.getLogger(__name__)


class WhatsAppGroup(models.Model):
    _name = 'whatsapp.group'
    _description = 'Grupo WhatsApp'
    _order = 'name'
    _rec_name = 'name'

    account_id = fields.Many2one(
        'whatsapp.account', string='Cuenta', required=True, ondelete='cascade', index=True,
    )
    wa_group_id = fields.Char(string='Group ID (JID)', required=True, index=True)
    name = fields.Char(string='Nombre del grupo')

    _sql_constraints = [
        (
            'unique_group',
            'UNIQUE(account_id, wa_group_id)',
            'El grupo ya existe para esta cuenta.',
        )
    ]

    # ── API pública ──────────────────────────────────────────────────────────

    @api.model
    def _get_or_fetch(self, account, group_jid: str):
        """Devuelve el whatsapp.group, creándolo si es necesario.

        Si no tiene nombre guardado, llama a la Graph API de Meta bajo demanda.
        """
        group = self.search([
            ('account_id', '=', account.id),
            ('wa_group_id', '=', group_jid),
        ], limit=1)

        if not group:
            name = self._fetch_subject_from_meta(account, group_jid)
            group = self.create({
                'account_id': account.id,
                'wa_group_id': group_jid,
                'name': name,
            })
        elif not group.name:
            name = self._fetch_subject_from_meta(account, group_jid)
            if name:
                group.write({'name': name})

        return group

    @api.model
    def _update_name(self, account, group_jid: str, subject: str = None):
        """Crea o actualiza el nombre del grupo.

        Si *subject* viene vacío (webhook sin subject), consulta Meta API.
        Llamado desde los eventos group_settings_update / group_lifecycle_update.
        """
        group = self.search([
            ('account_id', '=', account.id),
            ('wa_group_id', '=', group_jid),
        ], limit=1)

        name = subject or self._fetch_subject_from_meta(account, group_jid)

        if group:
            if name and name != group.name:
                group.write({'name': name})
        else:
            self.create({
                'account_id': account.id,
                'wa_group_id': group_jid,
                'name': name,
            })

    # ── Helpers privados ─────────────────────────────────────────────────────

    def _fetch_subject_from_meta(self, account, group_jid: str):
        """GET /{group_id}?fields=subject en la Graph API v19.0.

        Devuelve el subject como str, o None si falla.
        """
        try:
            token = account.access_token
            if not token:
                return None
            url = (
                f'{get_graph_base(self.env)}/{group_jid}'
                f'?fields=subject&access_token={token}'
            )
            req = urllib.request.Request(url, headers={'User-Agent': 'OdooWA/1.0'})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
                return data.get('subject') or None
        except Exception as exc:
            _logger.debug('WhatsApp: no se pudo obtener subject de %s: %s', group_jid, exc)
            return None

    @property
    def display_name_safe(self):
        """Nombre legible; fallback si todavía no hay subject."""
        self.ensure_one()
        if self.name:
            return self.name
        short_id = self.wa_group_id[:24] if self.wa_group_id else '?'
        return f'Grupo sin nombre (ID: {short_id})'
