"""Extensión de crm.lead para el flujo LinkedIn Growth.

Los campos llevan prefijo `x_` a propósito: son exactamente los nombres que el
planteamiento original y el flujo de n8n esperan escribir por la API JSON-2
(`x_linkedin_score`). Si aquí se llamaran de otra forma, cada nodo HTTP Request
de n8n tendría que traducir nombres, y el primer despiste crearía leads con la
puntuación en un campo que nadie lee.

Este archivo no toca nada del flujo de campañas: los campos y botones de
`crm_lead.py` siguen intactos.
"""
import json
import logging
import re

from markupsafe import Markup

from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

_P = 'crm_marketing_and_comunications.'


class CrmLead(models.Model):
    _inherit = 'crm.lead'

    # ── Señales de LinkedIn ───────────────────────────────────────────────────
    x_linkedin_url = fields.Char(
        string='Perfil de LinkedIn',
        help='URL del perfil del contacto. Es la clave con la que el minero de '
             'red deduplica: dos señales de la misma URL van al mismo lead.',
    )
    x_linkedin_score = fields.Integer(
        string='Puntuación LinkedIn',
        default=0, tracking=True, index=True,
        help='Suma de los puntos de todas las señales de LinkedIn recibidas de '
             'este contacto. Al superar el umbral del perfil se considera un '
             'lead caliente.',
    )
    # ── Exclusión por categoría ───────────────────────────────────────────
    x_linkedin_excluded_by = fields.Many2one(
        'marketing.linkedin.exclusion',
        string='Excluido por',
        ondelete='set null', index=True, readonly=True,
        help='Categoría de "Clientes a excluir" en la que ha caído este lead. '
             'Mientras esté marcado, su puntuación se fuerza a 0.',
    )
    x_linkedin_exclusion_override = fields.Boolean(
        string='Excepción: no excluir',
        default=False, tracking=True,
        help='Márcalo para tratar este lead como válido AUNQUE encaje en una '
             'categoría de exclusión. Es la excepción manual: la puntuación '
             'vuelve a contar con normalidad.',
    )
    x_linkedin_is_excluded = fields.Boolean(
        string='Excluido', compute='_compute_is_excluded', store=True, index=True,
        help='Excluido de verdad = cae en una categoría Y no tiene excepción.',
    )

    x_linkedin_profile_id = fields.Many2one(
        'marketing.linkedin.profile',
        string='Perfil LinkedIn de origen',
        ondelete='set null',
        help='Perfil cuyo contenido generó la señal que trajo este lead.',
    )
    x_linkedin_last_signal_date = fields.Datetime(
        string='Última señal de LinkedIn', readonly=True,
    )
    linkedin_signal_ids = fields.One2many(
        'marketing.linkedin.signal', 'lead_id', string='Señales de LinkedIn',
    )
    linkedin_signal_count = fields.Integer(
        string='Señales', compute='_compute_linkedin_signal_count',
    )
    x_linkedin_is_hot = fields.Boolean(
        string='Lead caliente de LinkedIn',
        compute='_compute_linkedin_is_hot', store=True,
        help='La puntuación supera el umbral del perfil de origen.',
    )

    # ── Preparación de llamada (ventas ligero) ────────────────────────────────
    x_call_prep = fields.Html(
        string='Preparación de llamada', sanitize=False, readonly=True, copy=False,
        help='Contexto, objeciones probables y siguiente paso generados a partir '
             'de lo que ya hay en Odoo sobre este lead.',
    )
    x_call_prep_date = fields.Datetime(
        string='Preparación generada el', readonly=True, copy=False,
    )
    x_call_prep_state = fields.Selection([
        ('none', 'Sin preparar'),
        ('pending', 'Solicitada a n8n'),
        ('ready', 'Lista'),
        ('failed', 'Fallida'),
    ], string='Estado de la preparación', default='none', readonly=True, copy=False)

    @api.depends('linkedin_signal_ids')
    def _compute_linkedin_signal_count(self):
        for lead in self:
            lead.linkedin_signal_count = len(lead.linkedin_signal_ids)

    @api.depends('x_linkedin_excluded_by', 'x_linkedin_exclusion_override')
    def _compute_is_excluded(self):
        for lead in self:
            lead.x_linkedin_is_excluded = bool(
                lead.x_linkedin_excluded_by and not lead.x_linkedin_exclusion_override
            )

    def _reevaluar_exclusion(self):
        """Recalcula la categoría de exclusión y fuerza la puntuación a 0.

        Se llama al crear o actualizar un lead desde LinkedIn. La puntuación se
        pone a 0 (no se resta ni se conserva) porque el criterio es binario: si
        la empresa no interesa, da igual cuántas señales genere.
        """
        for lead in self:
            perfil = lead.x_linkedin_profile_id
            if not perfil:
                continue
            cat = perfil.matching_exclusion(
                lead.partner_name or lead.name or '',
                lead.function or '',
            )
            vals = {'x_linkedin_excluded_by': cat.id if cat else False}
            # La excepción manual manda: si está marcada, no se toca el score
            if cat and not lead.x_linkedin_exclusion_override and lead.x_linkedin_score:
                vals['x_linkedin_score'] = 0
            lead.write(vals)

    # ── Baja RGPD desde cualquier email, no solo de campaña ───────────────────
    #
    # Las campañas ya tenían enlace de baja porque cada lead tiene una línea de
    # campaña con su token. Un email suelto enviado desde la ficha del lead no
    # tiene esa línea, así que el token vive aquí, en el propio lead: así el
    # marcador {baja} funciona en cualquier plantilla y todas las bajas caen en
    # la MISMA lista RGPD.

    unsubscribe_token = fields.Char(
        string='Token de baja', copy=False, index=True, readonly=True,
        help='Identificador del enlace de baja de este contacto. Se genera solo.',
    )

    def _asegurar_token_baja(self):
        """Crea el token si falta. `secrets` y no `uuid`: es un enlace público."""
        import secrets
        for lead in self:
            if not lead.unsubscribe_token:
                lead.sudo().unsubscribe_token = secrets.token_urlsafe(24)
        return self

    def _url_baja(self):
        """URL de baja de este lead. La usa el marcador {baja}."""
        self.ensure_one()
        if not self.id:
            return ''
        self._asegurar_token_baja()
        base = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        return f'{base}/marketing/baja/{self.unsubscribe_token}'

    def action_reevaluar_exclusiones(self):
        """Acción de servidor: reevaluar los leads seleccionados."""
        self._reevaluar_exclusion()
        excluidos = len(self.filtered('x_linkedin_is_excluded'))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Exclusiones reevaluadas',
                'message': f'{len(self)} lead(s) revisados, {excluidos} excluidos.',
                'type': 'success',
            },
        }

    @api.depends('x_linkedin_score', 'x_linkedin_profile_id.hot_threshold',
                 'x_linkedin_is_excluded')
    def _compute_linkedin_is_hot(self):
        for lead in self:
            # Un lead excluido nunca es "caliente", aunque arrastre puntuación
            # de antes de definirse la categoría de exclusión.
            if lead.x_linkedin_is_excluded:
                lead.x_linkedin_is_hot = False
                continue
            threshold = lead.x_linkedin_profile_id.hot_threshold or 0
            lead.x_linkedin_is_hot = bool(threshold) and lead.x_linkedin_score >= threshold

    # ── Acciones ──────────────────────────────────────────────────────────────

    def action_view_linkedin_signals(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Señales de LinkedIn — {self.partner_name or self.name}',
            'res_model': 'marketing.linkedin.signal',
            'view_mode': 'list,form',
            'domain': [('lead_id', '=', self.id)],
        }

    def action_open_linkedin_profile(self):
        self.ensure_one()
        if not self.x_linkedin_url:
            raise UserError('Este lead no tiene URL de LinkedIn registrada.')
        return {'type': 'ir.actions.act_url', 'url': self.x_linkedin_url, 'target': 'new'}

    def action_prepare_call(self):
        """Genera la preparación de llamada de este lead (Patrón A).

        Según el perfil: la escribe Claude desde Odoo y aparece al instante en
        la ficha, o se delega en n8n y el resultado vuelve por el endpoint de
        retorno como nota del chatter. En los dos casos el resultado acaba en
        el mismo sitio, así que el usuario no tiene que saber cuál está activo.
        """
        self.ensure_one()
        profile = self._get_call_prep_profile()

        if profile and profile.ai_mode == 'n8n':
            ok, message = profile.call_n8n(
                'call_prep', self._get_call_prep_payload(profile),
            )
            if not ok:
                self.x_call_prep_state = 'failed'
                raise UserError(
                    'No se pudo lanzar la preparación en n8n.\n\n'
                    f'{message}\n\n'
                    'Revisa la URL del webhook en Ajustes → Marketing Campaigns → '
                    'LinkedIn Growth, o cambia el motor de IA del perfil a '
                    '"Odoo llama a Claude directamente".'
                )
            self.write({'x_call_prep_state': 'pending'})
            self.message_post(
                body='Preparación de llamada solicitada al flujo de n8n. '
                     'El resultado aparecerá aquí en cuanto responda.',
            )
            return self._notification(
                'Preparación solicitada',
                'n8n está preparando la llamada. El resultado llegará al chatter.',
                'info',
            )

        html = self._generate_call_prep_with_claude(profile)
        self.apply_call_prep(html, generated_by='claude')
        return self._notification(
            'Preparación lista',
            'Tienes el contexto, las objeciones y el siguiente paso en la pestaña '
            '"Preparación de llamada".',
            'success',
        )

    def _notification(self, title, message, kind):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {'title': title, 'message': message, 'type': kind, 'sticky': False},
        }

    def _get_call_prep_profile(self):
        """Perfil de LinkedIn que aporta rol y contexto para este lead.

        Prioridad: el perfil que trajo el lead, el del comercial asignado, el
        del usuario que pulsa el botón. Si no hay ninguno, se genera con el
        contexto vacío antes que negarse: la preparación sigue siendo útil,
        solo menos afinada.
        """
        self.ensure_one()
        Profile = self.env['marketing.linkedin.profile']
        if self.x_linkedin_profile_id:
            return self.x_linkedin_profile_id
        if self.user_id:
            profile = Profile.search([('user_id', '=', self.user_id.id)], limit=1)
            if profile:
                return profile
        return Profile._get_default_profile()

    # ── Recogida del contexto del lead ────────────────────────────────────────

    def get_call_prep_context(self):
        """Todo lo que Odoo ya sabe de este lead, en un dict serializable.

        Es lo que consume tanto el prompt de Claude como el payload de n8n:
        una sola fuente, para que las dos rutas preparen la llamada con
        exactamente la misma información.
        """
        self.ensure_one()
        return {
            'lead_id': self.id,
            'name': self.name,
            'type': self.type,
            'contact_name': self.contact_name or '',
            'company_name': self.partner_name or '',
            'job_title': self.function or '',
            'email': self.email_from or '',
            'phone': self.phone or '',
            'website': self.website or '',
            'city': self.city or '',
            'country': self.country_id.name or '',
            'industry': (self.partner_id.industry_id.name or '') if self.partner_id else '',
            'stage': self.stage_id.name or '',
            'expected_revenue': self.expected_revenue,
            'probability': self.probability,
            'priority': self.priority,
            'date_open': fields.Datetime.to_string(self.date_open) if self.date_open else '',
            'date_deadline': fields.Date.to_string(self.date_deadline) if self.date_deadline else '',
            'tags': self.tag_ids.mapped('name'),
            'source': self.source_id.name or '',
            'description': self._html_to_text(self.description or ''),
            'salesperson': self.user_id.name or '',
            'linkedin': {
                'url': self.x_linkedin_url or '',
                'score': self.x_linkedin_score,
                'last_signal': fields.Datetime.to_string(self.x_linkedin_last_signal_date)
                if self.x_linkedin_last_signal_date else '',
                'signals': [{
                    'type': signal.signal_type,
                    'date': fields.Datetime.to_string(signal.signal_date),
                    'post': signal.post_id.name or '',
                    'text': signal.comment_text or '',
                } for signal in self.linkedin_signal_ids.sorted('signal_date', reverse=True)[:10]],
            },
            'communications': self._get_communications_context(),
            'activities': self._get_activities_context(),
        }

    def _get_communications_context(self):
        """Historial de emails y notas registrado en crm.communication."""
        self.ensure_one()
        if 'crm.communication' not in self.env:
            return []
        communications = self.env['crm.communication'].search(
            [('lead_id', '=', self.id)], order='date desc', limit=15,
        )
        return [{
            'date': fields.Datetime.to_string(comm.date),
            'type': dict(comm._fields['action_type'].selection).get(
                comm.action_type, comm.action_type,
            ),
            'subject': comm.subject or '',
            'body': self._html_to_text(comm.description or '')[:1500],
            'user': comm.user_id.name or '',
        } for comm in communications]

    def _get_activities_context(self):
        """Actividades pendientes y las últimas cerradas."""
        self.ensure_one()
        pending = self.env['mail.activity'].search(
            [('res_model', '=', 'crm.lead'), ('res_id', '=', self.id)],
            order='date_deadline',
        )
        done = self.env['mail.message'].search([
            ('model', '=', 'crm.lead'), ('res_id', '=', self.id),
            ('mail_activity_type_id', '!=', False),
        ], order='date desc', limit=10)
        return {
            'pending': [{
                'type': activity.activity_type_id.name or '',
                'summary': activity.summary or '',
                'deadline': fields.Date.to_string(activity.date_deadline)
                if activity.date_deadline else '',
                'user': activity.user_id.name or '',
                'note': self._html_to_text(activity.note or '')[:500],
            } for activity in pending],
            'done': [{
                'type': message.mail_activity_type_id.name or '',
                'date': fields.Datetime.to_string(message.date),
                'body': self._html_to_text(message.body or '')[:500],
            } for message in done],
        }

    @staticmethod
    def _html_to_text(value):
        text = re.sub(r'<br\s*/?>', '\n', value or '')
        text = re.sub(r'</(p|div|li|tr)>', '\n', text)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = text.replace('&nbsp;', ' ').replace('&amp;', '&')
        text = re.sub(r'[ \t]{2,}', ' ', text)
        return re.sub(r'\n{3,}', '\n\n', text).strip()

    # ── Generación de la preparación ──────────────────────────────────────────

    def _get_call_prep_payload(self, profile):
        """Payload del webhook `ventas-ligero` de n8n."""
        self.ensure_one()
        base_url = self.env['marketing.linkedin.profile']._get_odoo_base_url()
        return {
            'action': 'call_prep',
            'lead_id': self.id,
            'partner_name': self.partner_name or self.contact_name or self.name,
            'stage_id': self.stage_id.name or '',
            'requested_by': self.env.user.name,
            'ai_context': profile.get_ai_context() if profile else '',
            'context': self.get_call_prep_context(),
            'callback_url': base_url + '/vantis/linkedin/call-prep',
        }

    def _generate_call_prep_with_claude(self, profile):
        """Pide la preparación a Claude con el rol y contexto del perfil."""
        self.ensure_one()
        icp = self.env['ir.config_parameter'].sudo()
        api_key = icp.get_param(f'{_P}ai_anthropic_key', '') or \
            icp.get_param('crm_marketing_and_comunications.ai_anthropic_key', '')
        if not self.env['marketing.ai.service'].proveedores_disponibles():
            raise UserError(
                'No hay clave API de Anthropic configurada.\n\n'
                'Ponla en Ajustes → Marketing Campaigns, o cambia el motor de '
                'IA del perfil de LinkedIn a "Delegar en el flujo de n8n".'
            )

        prompt = self._build_call_prep_prompt(profile)
        try:
            text = self.env['marketing.campaign']._call_claude(prompt=prompt, contexto='lead linkedin')
        except Exception as exc:
            self.x_call_prep_state = 'failed'
            raise UserError(f'Claude no pudo generar la preparación: {exc}') from exc
        if not text:
            raise UserError('Claude devolvió una respuesta vacía.')
        if '<' not in text:
            text = '<p>' + text.replace('\n\n', '</p><p>').replace('\n', '<br/>') + '</p>'
        return text

    def _build_call_prep_prompt(self, profile):
        """Prompt de preparación de llamada, con el contexto del perfil delante."""
        self.ensure_one()
        context = self.get_call_prep_context()
        ai_context = profile.get_ai_context() if profile else \
            '(sin perfil de LinkedIn configurado: usa un tono consultivo B2B neutro)'
        had_call = bool(context['activities']['done']) or bool(context['communications'])
        return f"""{ai_context}

=== TAREA ===
Prepara una llamada comercial con este lead. No ejecutes nada ni propongas
acciones sobre el CRM: solo redacta la preparación.

=== DATOS DEL LEAD (todo lo que hay en el CRM) ===
{json.dumps(context, ensure_ascii=False, indent=2, default=str)}

=== QUÉ DEBES DEVOLVER ===
1. <b>Dónde está la conversación</b>: exactamente 3 líneas. Qué ha pasado, qué
   se sabe del interlocutor y en qué punto está.
2. <b>Objeciones probables</b>: entre 2 y 3, deducidas del sector, el tamaño de
   la empresa y la etapa del pipeline. Cada una con una respuesta corta.
3. {'<b>Siguiente paso sugerido</b>: uno solo, concreto y con fecha.'
   if had_call else
   '<b>Preguntas de discovery</b>: exactamente 3, abiertas y específicas de este caso.'}

=== FORMATO ===
HTML simple (<h4>, <p>, <ul>, <li>, <b>). Sin <html> ni <body>. Sin preámbulo.
Nada de generalidades: cada frase debe apoyarse en un dato del lead. Si falta
información para algo, dilo en una línea en vez de inventarla.
Devuelve SOLO el HTML."""

    def apply_call_prep(self, html, generated_by='claude'):
        """Guarda la preparación en el lead y la deja también en el chatter.

        Lo usan las dos rutas: la generación local y el retorno de n8n. En el
        chatter queda el histórico (cada preparación, con su fecha); en el
        campo queda siempre la última, que es la que se mira antes de llamar.
        """
        self.ensure_one()
        self.write({
            'x_call_prep': html,
            'x_call_prep_date': fields.Datetime.now(),
            'x_call_prep_state': 'ready',
        })
        origin = {
            'claude': 'Claude (desde Odoo)',
            'n8n': 'el flujo de n8n',
        }.get(generated_by, generated_by)
        self.message_post(
            body=Markup(
                f'<p><b>Preparación de llamada</b> generada por {origin}:</p>{html}'
            ),
        )
        return True
