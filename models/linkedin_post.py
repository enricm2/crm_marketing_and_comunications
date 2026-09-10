"""Publicaciones de LinkedIn y sus métricas.

El seguimiento de contenido vive en Odoo, no en la base de datos de contenido
externa, porque es aquí donde se cruza con el CRM: una publicación sabe cuántas
señales generó, cuántos leads salieron de ellas y cuánto pipeline hay detrás.
Ese cruce es lo que alimenta al router de embudo.
"""
import logging

from odoo import models, fields, api
from odoo.exceptions import UserError

from .linkedin_common import POST_FORMATS, normalize_linkedin_url

_logger = logging.getLogger(__name__)


class MarketingLinkedinPost(models.Model):
    _name = 'marketing.linkedin.post'
    _description = 'Publicación de LinkedIn'
    _order = 'published_date desc, id desc'
    _inherit = ['mail.thread']

    name = fields.Char(
        string='Titular / gancho', required=True, tracking=True,
        help='Cómo identificas la publicación en Odoo. Normalmente la primera '
             'línea del post.',
    )
    profile_id = fields.Many2one(
        'marketing.linkedin.profile',
        string='Perfil',
        required=True,
        index=True,
        ondelete='cascade',
        default=lambda self: self.env['marketing.linkedin.profile']._get_default_profile(),
    )
    user_id = fields.Many2one(
        related='profile_id.user_id', string='Autor', store=True, readonly=True,
    )
    company_id = fields.Many2one(
        related='profile_id.company_id', string='Compañía', store=True, readonly=True,
    )
    active = fields.Boolean(default=True)

    # ── Identificación en LinkedIn ────────────────────────────────────────────
    external_id = fields.Char(
        string='ID / URN en LinkedIn',
        index=True, copy=False,
        help='Identificador del post en LinkedIn (urn:li:activity:...). Es la '
             'clave con la que n8n actualiza las métricas sin duplicar.',
    )
    url = fields.Char(string='URL de la publicación')

    # ── Contenido ─────────────────────────────────────────────────────────────
    state = fields.Selection([
        ('draft', 'Borrador'),
        ('scheduled', 'Programada'),
        ('published', 'Publicada'),
        ('archived', 'Archivada'),
    ], string='Estado', default='draft', required=True, tracking=True)
    published_date = fields.Datetime(string='Fecha de publicación', tracking=True)
    scheduled_date = fields.Datetime(string='Fecha programada')
    content = fields.Text(string='Contenido')
    post_format = fields.Selection(
        POST_FORMATS, string='Formato', default='text',
    )
    content_pillar = fields.Char(
        string='Pilar de contenido',
        help='Tema o eje editorial al que pertenece. Sirve para comparar qué '
             'líneas de contenido generan conversación y cuáles no.',
    )
    cta = fields.Char(
        string='Llamada a la acción',
        help='Qué se pedía en el post: comentar una palabra, agendar, descargar…',
    )

    # ── Métricas ──────────────────────────────────────────────────────────────
    impressions = fields.Integer(string='Impresiones', tracking=True)
    reactions = fields.Integer(string='Reacciones')
    comments = fields.Integer(string='Comentarios')
    shares = fields.Integer(string='Compartidos')
    clicks = fields.Integer(string='Clics')
    followers_gained = fields.Integer(string='Seguidores ganados')
    metrics_updated_at = fields.Datetime(string='Métricas actualizadas', readonly=True)

    engagement_rate = fields.Float(
        string='Tasa de engagement (%)',
        compute='_compute_engagement_rate', store=True, digits=(16, 2),
        help='(Reacciones + comentarios + compartidos) / impresiones × 100.',
    )

    # ── Cruce con el CRM ──────────────────────────────────────────────────────
    signal_ids = fields.One2many(
        'marketing.linkedin.signal', 'post_id', string='Señales generadas',
    )
    signal_count = fields.Integer(
        string='Señales', compute='_compute_crm_stats', store=True,
    )
    lead_count = fields.Integer(
        string='Leads', compute='_compute_crm_stats', store=True,
    )
    conversation_rate = fields.Float(
        string='Conversión post → conversación (%)',
        compute='_compute_crm_stats', store=True, digits=(16, 2),
        help='Señales que acabaron en lead sobre impresiones, ×100. Es la '
             'métrica que el router usa para separar un problema de '
             'posicionamiento de uno de captación.',
    )
    notes = fields.Text(string='Notas')

    _unique_external_id = models.Constraint(
        'UNIQUE(profile_id, external_id)',
        'Ya existe una publicación con ese ID de LinkedIn para este perfil.',
    )

    # ── Cómputos ──────────────────────────────────────────────────────────────

    @api.depends('impressions', 'reactions', 'comments', 'shares')
    def _compute_engagement_rate(self):
        for post in self:
            if post.impressions:
                interactions = post.reactions + post.comments + post.shares
                post.engagement_rate = round(interactions * 100.0 / post.impressions, 2)
            else:
                post.engagement_rate = 0.0

    @api.depends('signal_ids', 'signal_ids.lead_id', 'signal_ids.state', 'impressions')
    def _compute_crm_stats(self):
        for post in self:
            valid = post.signal_ids.filtered(lambda s: s.state != 'discarded')
            post.signal_count = len(valid)
            leads = valid.mapped('lead_id')
            post.lead_count = len(leads)
            post.conversation_rate = (
                round(len(leads) * 100.0 / post.impressions, 2) if post.impressions else 0.0
            )

    # ── Ingesta desde n8n ─────────────────────────────────────────────────────

    @api.model
    def upsert_from_payload(self, profile, payload):
        """Crea o actualiza una publicación a partir del payload de n8n.

        La clave de identidad es `external_id` dentro del perfil. Si no viene,
        se intenta por URL normalizada; si tampoco, se crea una publicación
        nueva (n8n mandará el external_id la próxima vez y ya no duplicará).
        """
        vals = self._payload_to_vals(payload)
        post = self.browse()

        external_id = vals.get('external_id')
        if external_id:
            post = self.search([
                ('profile_id', '=', profile.id),
                ('external_id', '=', external_id),
            ], limit=1)
        if not post and vals.get('url'):
            normalized = normalize_linkedin_url(vals['url'])
            candidates = self.search([('profile_id', '=', profile.id), ('url', '!=', False)])
            post = candidates.filtered(
                lambda p: normalize_linkedin_url(p.url) == normalized
            )[:1]

        vals['metrics_updated_at'] = fields.Datetime.now()
        if post:
            # No pisamos el contenido editorial escrito a mano en Odoo con un
            # payload que solo trae métricas.
            update_vals = {k: v for k, v in vals.items() if v not in (False, None, '')}
            update_vals.pop('profile_id', None)
            post.write(update_vals)
            post._link_pending_signals()
            return post

        vals['profile_id'] = profile.id
        vals.setdefault('name', 'Publicación de LinkedIn')
        if vals.get('published_date') and not vals.get('state'):
            vals['state'] = 'published'
        post = self.create(vals)
        post._link_pending_signals()
        return post

    @api.model
    def _payload_to_vals(self, payload):
        """Traduce las claves del payload de n8n a campos del modelo."""
        def pick(*keys):
            for key in keys:
                if payload.get(key) not in (None, ''):
                    return payload[key]
            return None

        def as_int(value):
            try:
                return int(float(value))
            except (TypeError, ValueError):
                return 0

        vals = {}
        headline = pick('name', 'titulo', 'hook', 'gancho', 'headline')
        if headline:
            vals['name'] = str(headline)[:250]
        for field_name, keys in (
            ('external_id', ('external_id', 'post_id', 'urn', 'activity_urn')),
            ('url', ('url', 'link', 'post_url')),
            ('content', ('content', 'texto', 'text', 'body')),
            ('content_pillar', ('content_pillar', 'pilar', 'topic', 'tema')),
            ('cta', ('cta', 'llamada_accion')),
        ):
            value = pick(*keys)
            if value is not None:
                vals[field_name] = str(value)

        post_format = pick('post_format', 'formato', 'format', 'type')
        if post_format:
            code = str(post_format).strip().lower()
            valid = {c for c, _l in POST_FORMATS}
            if code in valid:
                vals['post_format'] = code

        published = pick('published_date', 'fecha_publicacion', 'published_at', 'date')
        if published:
            parsed = self.env['marketing.linkedin.signal']._parse_datetime(published)
            if parsed:
                vals['published_date'] = parsed
                vals['state'] = 'published'

        for field_name, keys in (
            ('impressions', ('impressions', 'impresiones', 'views')),
            ('reactions', ('reactions', 'reacciones', 'likes')),
            ('comments', ('comments', 'comentarios')),
            ('shares', ('shares', 'compartidos', 'reposts')),
            ('clicks', ('clicks', 'clics')),
            ('followers_gained', ('followers_gained', 'seguidores', 'new_followers')),
        ):
            value = pick(*keys)
            if value is not None:
                vals[field_name] = as_int(value)
        return vals

    def _link_pending_signals(self):
        """Engancha señales que llegaron antes que la publicación.

        El CSV de comentarios puede procesarse antes de que se registren las
        métricas del post. Esas señales guardan la referencia en
        `external_post_ref`; aquí se resuelven.
        """
        for post in self:
            if not post.external_id:
                continue
            orphans = self.env['marketing.linkedin.signal'].search([
                ('profile_id', '=', post.profile_id.id),
                ('post_id', '=', False),
                ('external_post_ref', '=', post.external_id),
            ])
            if orphans:
                orphans.write({'post_id': post.id})

    # ── Acciones ──────────────────────────────────────────────────────────────

    def action_view_signals(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Señales — {self.name}',
            'res_model': 'marketing.linkedin.signal',
            'view_mode': 'list,form',
            'domain': [('post_id', '=', self.id)],
            'context': {
                'default_post_id': self.id,
                'default_profile_id': self.profile_id.id,
            },
        }

    def action_view_leads(self):
        self.ensure_one()
        lead_ids = self.signal_ids.mapped('lead_id').ids
        if not lead_ids:
            raise UserError('Esta publicación todavía no ha generado ningún lead.')
        return {
            'type': 'ir.actions.act_window',
            'name': f'Leads de "{self.name}"',
            'res_model': 'crm.lead',
            'view_mode': 'list,kanban,form',
            'domain': [('id', 'in', lead_ids)],
        }

    def action_import_signals(self):
        """Abre el asistente de importación apuntando a esta publicación.

        LinkedIn no exporta quién reacciona o comenta un post, así que esas
        señales entran pegando la lista a mano. Sin este botón hay que ir al
        menú de importación y acordarse de elegir la publicación: olvidarlo
        deja las señales sueltas y la publicación sin atribución.
        """
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Importar señales — {self.name}',
            'res_model': 'linkedin.import.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_profile_id': self.profile_id.id,
                'default_post_id': self.id,
                'default_input_mode': 'text',
            },
        }

    def action_mark_published(self):
        for post in self:
            post.write({
                'state': 'published',
                'published_date': post.published_date or fields.Datetime.now(),
            })
