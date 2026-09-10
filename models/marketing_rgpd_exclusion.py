import logging
import re

from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class MarketingRgpdExclusion(models.Model):
    """Lista de exclusión RGPD propia. Este modelo NUNCA se desactiva.
    Cualquier lead cuyo email o teléfono normalizado aparezca aquí queda
    bloqueado para cualquier envío de marketing, sea cual sea el canal."""

    _name = 'marketing.rgpd.exclusion'
    _description = 'Exclusión RGPD – Lista de bajas y rechazos'
    _order = 'excluded_at desc'

    partner_email = fields.Char(
        string='Email',
        index=True,
        help='Email normalizado (minúsculas, sin espacios).',
    )
    partner_phone = fields.Char(
        string='Teléfono (E.164)',
        index=True,
        help='Teléfono normalizado en formato E.164 (ej. 34612345678).',
    )
    lead_id = fields.Many2one(
        'crm.lead',
        string='Lead de origen',
        ondelete='set null',
    )
    campaign_id = fields.Many2one(
        'marketing.campaign',
        string='Campaña de origen',
        ondelete='set null',
    )
    reason = fields.Selection([
        ('unsubscribe_email', 'Baja email (enlace de baja)'),
        ('unsubscribe_whatsapp', 'Baja WhatsApp'),
        ('explicit_rejection', 'Rechazo explícito'),
        ('manual', 'Alta manual'),
    ], string='Motivo', required=True, default='manual')
    excluded_at = fields.Datetime(
        string='Fecha de exclusión',
        default=fields.Datetime.now,
        required=True,
    )
    excluded_by = fields.Selection([
        ('ai_auto', 'Automático (IA)'),
        ('user_manual', 'Manual (usuario)'),
        ('unsubscribe_link', 'Enlace de baja'),
    ], string='Excluido por', required=True, default='user_manual')
    notes = fields.Text(string='Notas')

    _sql_constraints = [
        (
            'unique_partner_email',
            'UNIQUE(partner_email)',
            'Este email ya está en la lista de exclusión RGPD.',
        ),
    ]

    @api.model
    def is_excluded(self, email=None, phone=None):
        """Comprueba si un email o teléfono normalizado está excluido.
        Devuelve True si hay exclusión."""
        domain = []
        if email:
            norm_email = (email or '').strip().lower()
            if norm_email:
                domain += [('partner_email', '=', norm_email)]
        if phone:
            norm_phone = self._normalize_phone(phone)
            if norm_phone:
                if domain:
                    domain = ['|'] + domain + [('partner_phone', '=', norm_phone)]
                else:
                    domain = [('partner_phone', '=', norm_phone)]
        if not domain:
            return False
        return bool(self.search_count(domain))

    @api.model
    def add_exclusion(self, email=None, phone=None, lead_id=None, campaign_id=None,
                      reason='explicit_rejection', excluded_by='ai_auto'):
        """Añade una exclusión. Si el email ya existe, no crea duplicado."""
        norm_email = (email or '').strip().lower() or False
        norm_phone = self._normalize_phone(phone) if phone else False

        if norm_email and self.search([('partner_email', '=', norm_email)]):
            _logger.info('RGPD exclusion already exists for email: %s', norm_email)
            # Asegurar que los CRM leads estén marcados de todas formas
            self._sync_opt_out_to_crm(norm_email, norm_phone)
            return

        vals = {
            'partner_email': norm_email,
            'partner_phone': norm_phone,
            'lead_id': lead_id,
            'campaign_id': campaign_id,
            'reason': reason,
            'excluded_by': excluded_by,
        }
        self.create(vals)
        _logger.info('RGPD exclusion added: email=%s phone=%s reason=%s', norm_email, norm_phone, reason)
        self._sync_opt_out_to_crm(norm_email, norm_phone)

    def _sync_opt_out_to_crm(self, norm_email, norm_phone):
        """Marca como 'Baja de Marketing' todos los leads que coincidan."""
        lead_domain = []
        if norm_email:
            lead_domain.append(('email_from', '=ilike', norm_email))
        if norm_phone:
            if lead_domain:
                lead_domain = ['|'] + lead_domain + [('phone', 'like', norm_phone)]
            else:
                lead_domain.append(('phone', 'like', norm_phone))

        if lead_domain:
            leads = self.env['crm.lead'].sudo().search(lead_domain).filtered(lambda l: not l.marketing_opt_out)
            if leads:
                leads.write({'marketing_opt_out': True})
                for lead in leads:
                    try:
                        lead.message_post(body="Se ha marcado 'Baja de Marketing / RGPD' en la ficha de este contacto porque solicitó la baja (enlace, manual o IA).")
                    except Exception:
                        pass

    @staticmethod
    def _normalize_phone(phone):
        """Normaliza a formato E.164 (solo dígitos, sin +)."""
        if not phone:
            return ''
        digits = re.sub(r'\D', '', phone)
        return digits
