"""Caché de fotos de perfil de contactos WhatsApp.

Lógica de negocio:
  - Cuentas QR (Evolution API / Baileys): se puede consultar la foto de cualquier
    contacto con el que la cuenta tenga conversación, dentro del alcance de privacidad
    que ese contacto haya configurado en WhatsApp.
  - Cuentas Meta Cloud API: la API de Meta NO expone fotos de perfil de contactos
    de terceros. Solo se puede obtener el logo/perfil de la propia cuenta de negocio
    (whatsapp_business_profile). Esto es una limitación de los ToS de WhatsApp Business
    Platform, no un bug pendiente de resolver.
"""
import base64
import hashlib
import json
import logging
import urllib.request
from datetime import timedelta

from odoo import models, fields, api
from .graph_config import get_graph_base

_logger = logging.getLogger(__name__)

# TTL de refresco: no volver a llamar al proveedor antes de este intervalo
PHOTO_REFRESH_DAYS = 7
# Limpiar perfiles sin actividad reciente
CLEANUP_INACTIVE_DAYS = 90


class WhatsAppContactProfile(models.Model):
    _name = 'whatsapp.contact.profile'
    _description = 'Perfil de contacto WhatsApp (caché de foto)'
    _order = 'last_checked_at desc'

    account_id = fields.Many2one(
        'whatsapp.account', string='Cuenta', required=True, ondelete='cascade', index=True,
    )
    connection_type = fields.Selection(
        related='account_id.connection_type', store=True, readonly=True,
        string='Tipo de conexión',
    )
    wa_jid = fields.Char(
        string='JID WhatsApp', required=True, index=True,
        help='Identificador de contacto en WhatsApp: {phone}@s.whatsapp.net para contactos, '
             'business:{account_id} para el propio perfil de negocio.',
    )
    partner_id = fields.Many2one(
        'res.partner', string='Contacto Odoo', ondelete='set null', index=True,
    )
    display_name_wa = fields.Char(string='Nombre WhatsApp')
    about = fields.Text(string='Estado/Bio')
    has_photo = fields.Boolean(string='Tiene foto', default=False)
    photo_hash = fields.Char(string='SHA-256 de la foto', size=64)
    photo_attachment_id = fields.Many2one(
        'ir.attachment', string='Foto de perfil', ondelete='set null',
    )
    photo_updated_at = fields.Datetime(string='Foto actualizada el')
    last_checked_at = fields.Datetime(string='Última comprobación')

    _sql_constraints = [
        (
            'unique_account_jid',
            'UNIQUE(account_id, wa_jid)',
            'Ya existe un perfil para esta cuenta y JID.',
        )
    ]

    # ── API pública ──────────────────────────────────────────────────────────

    @api.model
    def get_contact_avatar(self, account, partner):
        """Punto de entrada único desde la UI.

        Devuelve (bytes, mimetype) o (None, None).
          - Cuentas QR: sirve foto cacheada o dispara sync si no hay registro reciente.
          - Cuentas cloud_api: devuelve (None, None) siempre para contactos de terceros.
            (Meta Cloud API no expone fotos de perfil de terceros — limitación de ToS,
            no un bug pendiente.)
        """
        if account.connection_type == 'cloud_api':
            # Meta Cloud API does not expose third-party contact profile pictures (ToS limitation).
            # This is by design — do not add a fallback provider here without legal review.
            return None, None

        # Construir JID desde el teléfono del partner
        phone = (partner.phone or partner.mobile or '').strip().lstrip('+')
        if not phone:
            return None, None
        jid = f'{phone}@s.whatsapp.net'

        profile = self._get_or_sync(account, jid, partner)
        if not profile or not profile.has_photo or not profile.photo_attachment_id:
            return None, None

        try:
            raw = base64.b64decode(profile.photo_attachment_id.datas)
            return raw, profile.photo_attachment_id.mimetype or 'image/jpeg'
        except Exception:
            return None, None

    @api.model
    def get_business_avatar(self, account):
        """Devuelve la foto de perfil de la cuenta de negocio (cloud_api).

        Llama a whatsapp_business_profile de Meta y cachea el resultado.
        Solo aplicable a cuentas cloud_api.
        """
        if account.connection_type != 'cloud_api':
            return None, None

        jid = f'business:{account.id}'
        profile = self.search([
            ('account_id', '=', account.id),
            ('wa_jid', '=', jid),
        ], limit=1)

        needs_sync = (
            not profile
            or not profile.last_checked_at
            or fields.Datetime.now() - profile.last_checked_at > timedelta(days=PHOTO_REFRESH_DAYS)
        )
        if needs_sync:
            profile = self._sync_business_profile(account, existing=profile)

        if not profile or not profile.has_photo or not profile.photo_attachment_id:
            return None, None

        try:
            raw = base64.b64decode(profile.photo_attachment_id.datas)
            return raw, profile.photo_attachment_id.mimetype or 'image/jpeg'
        except Exception:
            return None, None

    # ── Sincronización ───────────────────────────────────────────────────────

    @api.model
    def _get_or_sync(self, account, jid, partner=None):
        """Devuelve el perfil, disparando sync si no hay registro o si el TTL expiró."""
        profile = self.search([
            ('account_id', '=', account.id),
            ('wa_jid', '=', jid),
        ], limit=1)

        needs_sync = (
            not profile
            or not profile.last_checked_at
            or fields.Datetime.now() - profile.last_checked_at > timedelta(days=PHOTO_REFRESH_DAYS)
        )
        if needs_sync:
            profile = self._sync_contact_profile(account, jid, partner=partner, existing=profile)

        return profile

    def _sync_contact_profile(self, account, jid, partner=None, existing=None):
        """Consulta Evolution API para obtener la foto del contacto.

        Solo para cuentas QR. Descarga la imagen, calcula SHA-256, y solo
        sobreescribe el attachment si la foto ha cambiado.

        Retorna el registro whatsapp.contact.profile actualizado.
        """
        now = fields.Datetime.now()
        vals_base = {
            'account_id': account.id,
            'wa_jid': jid,
            'last_checked_at': now,
        }
        if partner:
            vals_base['partner_id'] = partner.id

        # Obtener URL fresca de Evolution API
        try:
            pic_url = account._evo_fetch_profile_picture_url(jid)
        except Exception as exc:
            _logger.debug('WA profile sync: no se pudo obtener URL para %s: %s', jid, exc)
            pic_url = None

        if not pic_url:
            vals_base['has_photo'] = False
            return self._upsert_profile(existing, vals_base)

        # Descargar imagen
        try:
            req = urllib.request.Request(pic_url, headers={'User-Agent': 'WhatsApp/2.24.6.82 A'})
            with urllib.request.urlopen(req, timeout=10) as resp:
                img_bytes = resp.read()
                content_type = resp.headers.get('Content-Type', 'image/jpeg').split(';')[0]
        except Exception as exc:
            _logger.debug('WA profile sync: error descargando foto de %s: %s', jid, exc)
            vals_base['has_photo'] = False
            return self._upsert_profile(existing, vals_base)

        if len(img_bytes) < 100:
            vals_base['has_photo'] = False
            return self._upsert_profile(existing, vals_base)

        new_hash = hashlib.sha256(img_bytes).hexdigest()

        # Si el hash no cambió, solo actualizar last_checked_at
        if existing and existing.photo_hash == new_hash and existing.has_photo:
            existing.write({'last_checked_at': now})
            return existing

        # Hash nuevo o primera vez — guardar attachment
        img_b64 = base64.b64encode(img_bytes).decode()
        partner_id = partner.id if partner else (existing.partner_id.id if existing else False)

        att_vals = {
            'name': f'wa_profile_{jid}',
            'mimetype': content_type,
            'datas': img_b64,
        }
        if partner_id:
            att_vals.update({'res_model': 'res.partner', 'res_id': partner_id})

        # Borrar attachment anterior si existe
        if existing and existing.photo_attachment_id:
            existing.photo_attachment_id.sudo().unlink()

        att = self.env['ir.attachment'].sudo().create(att_vals)

        vals_base.update({
            'has_photo': True,
            'photo_hash': new_hash,
            'photo_attachment_id': att.id,
            'photo_updated_at': now,
        })
        return self._upsert_profile(existing, vals_base)

    def _sync_business_profile(self, account, existing=None):
        """Sincroniza el perfil de negocio propio desde Meta Cloud API.

        GET /v21.0/{phone_number_id}/whatsapp_business_profile
            ?fields=about,address,description,email,profile_picture_url,websites,vertical

        Solo para cuentas cloud_api.
        """
        now = fields.Datetime.now()
        jid = f'business:{account.id}'
        vals_base = {
            'account_id': account.id,
            'wa_jid': jid,
            'last_checked_at': now,
        }

        phone_number_id = account.phone_number_id
        access_token = account.access_token
        if not phone_number_id or not access_token:
            _logger.warning('WA business profile sync: cuenta %s sin phone_number_id o token', account.id)
            return existing or self._upsert_profile(None, vals_base)

        try:
            fields_req = 'about,address,description,email,profile_picture_url,websites,vertical'
            url = (
                f'{get_graph_base(self.env)}/{phone_number_id}/whatsapp_business_profile'
                f'?fields={fields_req}&access_token={access_token}'
            )
            req = urllib.request.Request(url, headers={'User-Agent': 'OdooWA/1.0'})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
        except Exception as exc:
            _logger.warning('WA business profile sync error: %s', exc)
            return existing or self._upsert_profile(None, vals_base)

        bp = (data.get('data') or [{}])[0] if isinstance(data.get('data'), list) else data
        vals_base['about'] = bp.get('about') or bp.get('description') or ''
        pic_url = bp.get('profile_picture_url')

        if not pic_url:
            vals_base['has_photo'] = False
            return self._upsert_profile(existing, vals_base)

        # Descargar logo
        try:
            req = urllib.request.Request(pic_url, headers={'User-Agent': 'OdooWA/1.0'})
            with urllib.request.urlopen(req, timeout=10) as resp:
                img_bytes = resp.read()
                content_type = resp.headers.get('Content-Type', 'image/jpeg').split(';')[0]
        except Exception as exc:
            _logger.warning('WA business logo download error: %s', exc)
            vals_base['has_photo'] = False
            return self._upsert_profile(existing, vals_base)

        if len(img_bytes) < 100:
            vals_base['has_photo'] = False
            return self._upsert_profile(existing, vals_base)

        new_hash = hashlib.sha256(img_bytes).hexdigest()

        if existing and existing.photo_hash == new_hash and existing.has_photo:
            existing.write({'last_checked_at': now, 'about': vals_base.get('about', '')})
            return existing

        img_b64 = base64.b64encode(img_bytes).decode()
        if existing and existing.photo_attachment_id:
            existing.photo_attachment_id.sudo().unlink()

        att = self.env['ir.attachment'].sudo().create({
            'name': f'wa_business_profile_{account.id}',
            'mimetype': content_type,
            'datas': img_b64,
            'res_model': 'whatsapp.account',
            'res_id': account.id,
        })

        vals_base.update({
            'has_photo': True,
            'photo_hash': new_hash,
            'photo_attachment_id': att.id,
            'photo_updated_at': now,
        })
        return self._upsert_profile(existing, vals_base)

    # ── Mantenimiento ────────────────────────────────────────────────────────

    @api.model
    def action_cleanup_inactive(self):
        """Elimina perfiles sin actividad reciente (> CLEANUP_INACTIVE_DAYS días).

        Llamado desde ir.cron diario.
        """
        cutoff = fields.Datetime.now() - timedelta(days=CLEANUP_INACTIVE_DAYS)
        old_profiles = self.search([
            ('last_checked_at', '<', cutoff),
            ('last_checked_at', '!=', False),
        ])
        if old_profiles:
            # Borrar attachments
            old_profiles.mapped('photo_attachment_id').sudo().unlink()
            count = len(old_profiles)
            old_profiles.unlink()
            _logger.info('WA profile cleanup: eliminados %d perfiles inactivos', count)

    @api.model
    def action_sync_active_profiles(self):
        """Refresca fotos de perfiles activos (con actividad en los últimos 30 días).

        Llamado desde ir.cron semanal. Solo procesa cuentas QR.
        """
        cutoff_active = fields.Datetime.now() - timedelta(days=30)
        cutoff_refresh = fields.Datetime.now() - timedelta(days=PHOTO_REFRESH_DAYS)

        profiles_to_refresh = self.search([
            ('connection_type', '=', 'qr_code'),
            ('partner_id.message_ids.date', '>=', cutoff_active),
            '|',
            ('last_checked_at', '<', cutoff_refresh),
            ('last_checked_at', '=', False),
        ], limit=200)

        for profile in profiles_to_refresh:
            try:
                self._sync_contact_profile(
                    profile.account_id, profile.wa_jid,
                    partner=profile.partner_id, existing=profile,
                )
            except Exception as exc:
                _logger.debug('WA profile sync error for %s: %s', profile.wa_jid, exc)

        # Sincronizar perfiles de negocio cloud_api
        cloud_accounts = self.env['whatsapp.account'].search([
            ('connection_type', '=', 'meta_business'),
            ('active', '=', True),
        ])
        for account in cloud_accounts:
            jid = f'business:{account.id}'
            existing = self.search([('account_id', '=', account.id), ('wa_jid', '=', jid)], limit=1)
            try:
                self._sync_business_profile(account, existing=existing or None)
            except Exception as exc:
                _logger.debug('WA business profile sync error for account %s: %s', account.id, exc)

    # ── Helpers privados ─────────────────────────────────────────────────────

    def _upsert_profile(self, existing, vals):
        """Crea o actualiza el registro de perfil."""
        if existing:
            existing.write({k: v for k, v in vals.items() if k not in ('account_id', 'wa_jid')})
            return existing
        return self.create(vals)
