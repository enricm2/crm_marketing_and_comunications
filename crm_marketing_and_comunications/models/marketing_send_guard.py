"""Capa común de verificación antes de cualquier envío.
Este modelo centraliza los dos filtros de cumplimiento en cascada:
  a) Lista Robinson (opcional, solo España)
  b) Exclusión RGPD interna (siempre activa, cualquier país)
"""
import logging
import re

from odoo import models, api

_logger = logging.getLogger(__name__)

_P = 'crm_marketing_and_comunications.'

_ES_COUNTRY_CODES = {'es', 'spain', 'españa', 'espana'}


class MarketingSendGuard(models.AbstractModel):
    """Interfaz de verificación de envíos. Nunca instanciado como tabla."""

    _name = 'marketing.send.guard'
    _description = 'Guardia de envío (RGPD + Robinson)'

    @api.model
    def check(self, campaign_lead) -> tuple[bool, str]:
        """Comprueba si se puede enviar a este lead de campaña.

        Returns:
            (True, '') si se puede enviar.
            (False, motivo) si está bloqueado.
        """
        lead = campaign_lead.lead_id
        email = (lead.email_from or '').strip().lower()
        phone = (lead.phone or '')  # crm.lead en Odoo 19 no tiene 'mobile'

        # ── a) Lista Robinson (solo leads en España, si está activada) ─────
        is_spain = self._is_spain(lead)
        if is_spain:
            icp = self.env['ir.config_parameter'].sudo()
            robinson_enabled = icp.get_param(f'{_P}robinson_check_enabled', 'True') == 'True'
            robinson_key = icp.get_param(f'{_P}robinson_api_key', '')

            if robinson_enabled and robinson_key:
                robinson_cache = self.env['marketing.robinson.cache']
                if robinson_cache.check_robinson(email=email, phone=phone):
                    campaign_lead.write({
                        'exclusion_reason': 'robinson',
                        'excluded_at': fields.Datetime.now() if hasattr(campaign_lead, 'excluded_at') else None,
                    })
                    _logger.info(
                        'Robinson block: campaign_lead=%d lead=%s email=%s',
                        campaign_lead.id, lead.id, email,
                    )
                    return False, 'Contacto inscrito en Lista Robinson. Envío bloqueado.'
            elif robinson_enabled and not robinson_key:
                # Configurada como activa pero sin credenciales → aviso en log
                _logger.warning(
                    'Robinson check enabled but no API credentials — skipping check for lead %d',
                    lead.id,
                )

        # ── b) Exclusión RGPD interna (siempre, cualquier país) ───────────
        exclusion_model = self.env['marketing.rgpd.exclusion']
        if exclusion_model.is_excluded(email=email, phone=phone):
            campaign_lead.write({'exclusion_reason': 'rgpd_internal'})
            _logger.info(
                'RGPD internal block: campaign_lead=%d email=%s',
                campaign_lead.id, email,
            )
            return False, 'Contacto en lista de exclusión RGPD interna.'

        # ── c) Duplicados dentro de la misma campaña ──────────────────────
        campaign = campaign_lead.campaign_id
        if campaign_lead.channel_used == 'email' and email:
            # Buscar si ya se ha enviado un correo a esta misma dirección en esta campaña
            sent_count = self.env['marketing.campaign.lead'].search_count([
                ('campaign_id', '=', campaign.id),
                ('id', '!=', campaign_lead.id),
                ('lead_email', '=ilike', email),
                ('sent_at', '!=', False),
            ])
            if sent_count > 0:
                campaign_lead.write({
                    'exclusion_reason': 'duplicate',
                    'excluded_at': fields.Datetime.now() if hasattr(campaign_lead, 'excluded_at') else None,
                })
                _logger.info(
                    'Duplicate email block: campaign_lead=%d campaign=%d email=%s',
                    campaign_lead.id, campaign.id, email,
                )
                return False, 'Este correo ya ha sido enviado a este destinatario en esta campaña.'

        elif campaign_lead.channel_used == 'whatsapp' and phone:
            phone_clean = re.sub(r'\D', '', phone)
            if phone_clean:
                # Buscar otras líneas de la misma campaña enviadas por WhatsApp
                sent_leads = self.env['marketing.campaign.lead'].search([
                    ('campaign_id', '=', campaign.id),
                    ('id', '!=', campaign_lead.id),
                    ('channel_used', '=', 'whatsapp'),
                    ('sent_at', '!=', False),
                ])
                for sl in sent_leads:
                    sl_phone = sl.lead_phone or (sl.lead_id and sl.lead_id.phone) or ''
                    sl_phone_clean = re.sub(r'\D', '', sl_phone)
                    if sl_phone_clean == phone_clean:
                        campaign_lead.write({
                            'exclusion_reason': 'duplicate',
                            'excluded_at': fields.Datetime.now() if hasattr(campaign_lead, 'excluded_at') else None,
                        })
                        _logger.info(
                            'Duplicate phone block: campaign_lead=%d campaign=%d phone=%s',
                            campaign_lead.id, campaign.id, phone,
                        )
                        return False, 'Este número de WhatsApp ya ha sido enviado a este destinatario en esta campaña.'

        return True, ''

    @staticmethod
    def _is_spain(lead) -> bool:
        """Determina si el lead es de España por país o prefijo telefónico."""
        if lead.country_id:
            code = (lead.country_id.code or '').lower()
            name = (lead.country_id.name or '').lower()
            if code == 'es' or 'spain' in name or 'españa' in name or 'espana' in name:
                return True
        # Inferir por prefijo telefónico
        phone = (lead.phone or '')  # crm.lead en Odoo 19 no tiene 'mobile'
        digits = re.sub(r'\D', '', phone)
        if digits.startswith('34') and len(digits) >= 11:
            return True
        return False


# Importación diferida para evitar circularidades
from odoo import fields  # noqa: E402
