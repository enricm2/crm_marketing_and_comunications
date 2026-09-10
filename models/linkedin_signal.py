"""Señales de LinkedIn: cada interacción real que produce un contacto.

Una señal es un hecho observado (alguien comentó, reaccionó, vio el perfil,
pidió conexión). El módulo no la inventa ni la va a buscar: la recibe de n8n,
que a su vez la saca de las exportaciones nativas de LinkedIn o de una API
oficial. Aquí se deduplica, se puntúa, se cruza con el CRM y, si supera el
umbral del perfil, se convierte en un aviso accionable.
"""
import hashlib
import json
import logging
from datetime import datetime, timezone

from markupsafe import Markup

from odoo import models, fields, api
from odoo.exceptions import UserError

from .linkedin_common import (
    SIGNAL_TYPES,
    SIGNAL_TYPE_LABELS,
    normalize_linkedin_url,
    normalize_signal_type,
    split_headline,
)

_logger = logging.getLogger(__name__)


class MarketingLinkedinSignal(models.Model):
    _name = 'marketing.linkedin.signal'
    _description = 'Señal de LinkedIn'
    _order = 'signal_date desc, id desc'
    _inherit = ['mail.thread']
    _rec_name = 'display_name'

    # ── Origen ────────────────────────────────────────────────────────────────
    profile_id = fields.Many2one(
        'marketing.linkedin.profile',
        string='Perfil',
        required=True,
        index=True,
        ondelete='cascade',
        default=lambda self: self.env['marketing.linkedin.profile']._get_default_profile(),
    )
    user_id = fields.Many2one(
        related='profile_id.user_id', string='Propietario', store=True, readonly=True,
    )
    company_id = fields.Many2one(
        related='profile_id.company_id', string='Compañía', store=True, readonly=True,
    )
    batch_id = fields.Many2one(
        'marketing.linkedin.batch', string='Lote de importación',
        ondelete='set null', index=True,
    )

    # ── Datos del contacto ────────────────────────────────────────────────────
    contact_name = fields.Char(string='Nombre', required=True, index=True)
    linkedin_url = fields.Char(
        string='URL de LinkedIn', index=True,
        help='URL del perfil del contacto. Es la clave principal de '
             'deduplicación: se normaliza antes de comparar.',
    )
    company_name = fields.Char(string='Empresa', index=True)
    job_title = fields.Char(string='Cargo')
    location = fields.Char(string='Ubicación')
    email = fields.Char(string='Email')
    phone = fields.Char(string='Teléfono')

    # ── La señal ──────────────────────────────────────────────────────────────
    signal_type = fields.Selection(
        SIGNAL_TYPES, string='Tipo de señal', required=True, index=True,
    )
    signal_date = fields.Datetime(
        string='Fecha de la señal', required=True, index=True,
        default=fields.Datetime.now,
    )
    post_id = fields.Many2one(
        'marketing.linkedin.post', string='Publicación',
        ondelete='set null', index=True,
    )
    external_post_ref = fields.Char(
        string='Referencia externa del post',
        help='ID del post en LinkedIn cuando la publicación aún no existe en '
             'Odoo. Se resuelve solo cuando llegan sus métricas.',
    )
    comment_text = fields.Text(
        string='Texto del comentario / mensaje',
        help='Lo que escribió el contacto. Es el mejor contexto disponible '
             'para preparar la primera conversación.',
    )

    # ── Valoración ────────────────────────────────────────────────────────────
    score = fields.Integer(
        string='Puntos', readonly=True,
        help='Puntos que aporta esta señal al lead, según la configuración '
             'del perfil (tipo de señal + bonus de encaje ICP).',
    )
    icp_fit = fields.Boolean(
        string='Encaja con el ICP', readonly=True,
        help='Calculado con las keywords de sector, cargo y exclusión del perfil.',
    )
    is_hot = fields.Boolean(
        string='Señal caliente', readonly=True, index=True,
        help='El lead superó el umbral del perfil al procesar esta señal.',
    )

    # ── Estado y enlace con el CRM ────────────────────────────────────────────
    state = fields.Selection([
        ('new', 'Nueva'),
        ('linked', 'Vinculada a lead existente'),
        ('created', 'Lead creado'),
        ('pending', 'Sin vincular'),
        ('discarded', 'Descartada'),
    ], string='Estado', default='new', required=True, index=True, tracking=True)
    lead_id = fields.Many2one(
        'crm.lead', string='Lead / Oportunidad', ondelete='set null', index=True,
    )
    partner_id = fields.Many2one(
        'res.partner', string='Contacto', ondelete='set null',
    )
    # Estado de enriquecimiento del lead al que apunta la señal. Se mira desde
    # aquí para decidir a quién llamar: una señal caliente sobre un lead que
    # nadie ha analizado todavía es media señal.
    lead_enrichment_ok = fields.Boolean(
        related='lead_id.enrichment_ok', string='Lead enriquecido', readonly=True,
    )
    lead_enrichment_state = fields.Selection(
        related='lead_id.enrichment_state', string='Estado enriquecimiento',
        readonly=True,
    )
    processing_error = fields.Char(string='Error de procesado', readonly=True)

    # ── Trazabilidad ──────────────────────────────────────────────────────────
    dedupe_hash = fields.Char(
        string='Huella de deduplicación', index=True, readonly=True, copy=False,
    )
    raw_payload = fields.Text(
        string='Payload original', readonly=True,
        help='Fila tal y como la mandó n8n. Sirve para depurar mapeos de CSV '
             'sin tener que reproducir la exportación.',
    )
    display_name = fields.Char(compute='_compute_display_name', store=True)

    _unique_dedupe = models.Constraint(
        'UNIQUE(profile_id, dedupe_hash)',
        'Esta señal ya estaba registrada para este perfil.',
    )

    # ── Cómputos ──────────────────────────────────────────────────────────────

    @api.depends('contact_name', 'signal_type', 'company_name')
    def _compute_display_name(self):
        for signal in self:
            label = SIGNAL_TYPE_LABELS.get(signal.signal_type, 'Señal')
            company = f' ({signal.company_name})' if signal.company_name else ''
            signal.display_name = f'{label} · {signal.contact_name or "Sin nombre"}{company}'

    # ── Utilidades de normalización ───────────────────────────────────────────

    # Nombres de mes que aparecen en las exportaciones de LinkedIn. Se traducen
    # a número en vez de usar `%b`, porque `strptime` con nombres de mes depende
    # del locale del proceso: en un servidor en inglés, «26 Aug 2026» funciona y
    # «26 ago 2026» no, y al revés. Traducir a «26-08-2026» quita esa dependencia.
    _MONTHS = {
        'jan': '01', 'ene': '01', 'january': '01', 'enero': '01',
        'feb': '02', 'february': '02', 'febrero': '02',
        'mar': '03', 'march': '03', 'marzo': '03',
        'apr': '04', 'abr': '04', 'april': '04', 'abril': '04',
        'may': '05', 'mayo': '05',
        'jun': '06', 'june': '06', 'junio': '06',
        'jul': '07', 'july': '07', 'julio': '07',
        'aug': '08', 'ago': '08', 'august': '08', 'agosto': '08',
        'sep': '09', 'sept': '09', 'september': '09', 'septiembre': '09',
        'oct': '10', 'october': '10', 'octubre': '10',
        'nov': '11', 'november': '11', 'noviembre': '11',
        'dec': '12', 'dic': '12', 'december': '12', 'diciembre': '12',
    }

    @api.model
    def _months_to_numbers(self, text):
        """Convierte «26 Aug 2026» o «26 de agosto de 2026» en «26-08-2026»."""
        import re as _re
        cleaned = _re.sub(r'\bde\b', ' ', text, flags=_re.IGNORECASE)
        cleaned = _re.sub(r'[,]+', ' ', cleaned)
        parts = [part for part in _re.split(r'[\s\-]+', cleaned.strip()) if part]
        if len(parts) != 3:
            return text
        converted = [
            self._MONTHS.get(part.lower().rstrip('.'), part) for part in parts
        ]
        if converted == parts:
            return text
        return '-'.join(converted)

    @api.model
    def _parse_datetime(self, value):
        """Convierte a datetime naive UTC lo que mande el origen.

        LinkedIn y n8n mandan fechas en ISO 8601 con o sin zona, y a veces solo
        la fecha. Se aceptan los tres casos; si no se reconoce, se devuelve
        None y el llamante decide (normalmente: usar la fecha de ingesta).
        """
        if not value:
            return None
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value
        text = str(value).strip()
        if not text:
            return None
        # Epoch en segundos o milisegundos.
        if text.isdigit():
            number = int(text)
            if number > 10_000_000_000:
                number //= 1000
            try:
                return datetime.fromtimestamp(number, tz=timezone.utc).replace(tzinfo=None)
            except (ValueError, OSError, OverflowError):
                return None
        normalized = self._months_to_numbers(
            text.replace('Z', '+00:00').replace('/', '-')
        )
        for parser in (
            lambda v: datetime.fromisoformat(v),
            lambda v: datetime.strptime(v, '%Y-%m-%d %H:%M:%S'),
            lambda v: datetime.strptime(v, '%Y-%m-%d'),
            lambda v: datetime.strptime(v, '%d-%m-%Y %H:%M'),
            lambda v: datetime.strptime(v, '%d-%m-%Y'),
            lambda v: datetime.strptime(v, '%m-%d-%Y'),
            lambda v: datetime.strptime(v, '%d-%m-%Y %H:%M:%S'),
        ):
            try:
                parsed = parser(normalized)
            except ValueError:
                continue
            if parsed.tzinfo:
                parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
            return parsed
        return None

    @api.model
    def _build_dedupe_hash(self, vals):
        """Huella estable de una señal.

        Identidad = perfil + persona + tipo + post + día. El día, y no la hora
        exacta, porque las exportaciones de LinkedIn redondean la marca de
        tiempo de forma distinta según el informe: con la hora dentro, la misma
        interacción entraría dos veces.
        """
        identity = normalize_linkedin_url(vals.get('linkedin_url')) or (
            f"{(vals.get('contact_name') or '').strip().lower()}|"
            f"{(vals.get('company_name') or '').strip().lower()}"
        )
        signal_date = vals.get('signal_date')
        day = ''
        if signal_date:
            day = str(signal_date)[:10]
        post_ref = str(vals.get('external_post_ref') or vals.get('post_id') or '')
        raw = '|'.join([
            str(vals.get('profile_id') or ''),
            identity,
            vals.get('signal_type') or '',
            post_ref,
            day,
        ])
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    # ── Ingesta ───────────────────────────────────────────────────────────────

    @api.model
    def ingest(self, profile, rows, batch=None):
        """Punto de entrada único de las señales. Lo usa el controlador de n8n.

        Devuelve un resumen con lo que ha pasado con cada fila. Nunca levanta
        excepción por una fila mala: se anota el error y se sigue, porque un
        CSV de 300 comentarios no puede perderse entero por una fecha rara.
        """
        summary = {
            'received': len(rows or []),
            'created': 0,
            'duplicated': 0,
            'ignored': 0,
            'errors': 0,
            'hot': 0,
            'leads_created': 0,
            'leads_updated': 0,
            'signal_ids': [],
            'error_details': [],
        }
        for index, row in enumerate(rows or []):
            try:
                signal, status = self._ingest_one(profile, row, batch)
            except Exception as exc:
                summary['errors'] += 1
                summary['error_details'].append({'row': index, 'error': str(exc)})
                # El savepoint de `_ingest_one` ya ha deshecho lo que esta
                # fila hubiera escrito; el resto del lote sigue en pie.
                _logger.exception('LinkedIn: fila %s no procesada — %s', index, exc)
                continue
            if status == 'duplicated':
                summary['duplicated'] += 1
                continue
            if status == 'ignored':
                summary['ignored'] += 1
                continue
            summary['created'] += 1
            summary['signal_ids'].append(signal.id)
            if signal.is_hot:
                summary['hot'] += 1
            if signal.state == 'created':
                summary['leads_created'] += 1
            elif signal.state == 'linked':
                summary['leads_updated'] += 1
        return summary

    @api.model
    def _ingest_one(self, profile, row, batch=None):
        """Procesa una fila.

        Devuelve (señal, estado), con estado en 'created', 'duplicated' o
        'ignored'. Se distingue "ignorada" de "duplicada" porque significan
        cosas distintas para quien mira el lote: una es una decisión de
        configuración y la otra es que ese dato ya estaba.
        """
        vals = self._row_to_vals(profile, row)

        if not profile.is_tracked_signal_type(vals['signal_type']):
            return self.browse(), 'ignored'

        # Nunca convertir al dueño del perfil en señal ni en lead de sí mismo.
        # Pasa con más facilidad de la que parece: al leer la lista de
        # reacciones de una publicación propia aparece uno mismo si le ha dado
        # a "me gusta", y acaba creándose un lead con tu nombre en tu CRM.
        propia = normalize_linkedin_url(profile.linkedin_url)
        if propia and normalize_linkedin_url(vals.get('linkedin_url')) == propia:
            _logger.info('LinkedIn: descartada una señal del propio %s.', profile.name)
            return self.browse(), 'ignored'

        dedupe_hash = self._build_dedupe_hash(vals)
        existing = self.search([
            ('profile_id', '=', profile.id),
            ('dedupe_hash', '=', dedupe_hash),
        ], limit=1)
        if existing:
            return existing, 'duplicated'

        vals['dedupe_hash'] = dedupe_hash
        if batch:
            vals['batch_id'] = batch.id
        vals['raw_payload'] = json.dumps(row, ensure_ascii=False, default=str)[:20000]

        with self.env.cr.savepoint():
            signal = self.create(vals)
            signal._process()
        return signal, 'created'

    @api.model
    def _row_to_vals(self, profile, row):
        """Traduce una fila del origen a valores del modelo.

        Acepta tanto los nombres del prompt original en castellano
        (`nombre`, `tipo_senal`, `fecha_senal`) como los ingleses, para que el
        flujo de n8n pueda mandar lo que le resulte natural.
        """
        def pick(*keys):
            for key in keys:
                value = row.get(key)
                if value not in (None, ''):
                    return value
            return None

        contact_name = pick('contact_name', 'nombre', 'name', 'full_name', 'fullName')
        if not contact_name:
            raise UserError('La fila no trae nombre del contacto.')

        signal_type = normalize_signal_type(
            pick('signal_type', 'tipo_senal', 'tipo_señal', 'type', 'tipo')
        )
        if not signal_type:
            raise UserError(
                'Tipo de señal no reconocido: '
                f'{pick("signal_type", "tipo_senal", "type") or "(vacío)"}'
            )

        signal_date = self._parse_datetime(
            pick('signal_date', 'fecha_senal', 'fecha_señal', 'date', 'fecha', 'timestamp')
        ) or fields.Datetime.now()

        vals = {
            'profile_id': profile.id,
            'contact_name': str(contact_name).strip()[:250],
            'signal_type': signal_type,
            'signal_date': signal_date,
        }
        for field_name, keys in (
            ('linkedin_url', ('linkedin_url', 'linkedinUrl', 'profile_url', 'url', 'perfil')),
            ('company_name', ('company_name', 'empresa', 'company', 'organization')),
            # 'headline' NO va aquí: es el titular entero de LinkedIn y hay
            # que partirlo. Si se aceptara como cargo, se asignaría completo y
            # el partidor de abajo ya no tendría nada que hacer.
            ('job_title', ('job_title', 'cargo', 'title', 'position')),
            ('location', ('location', 'ubicacion', 'ubicación', 'city')),
            ('email', ('email', 'correo', 'mail')),
            ('phone', ('phone', 'telefono', 'teléfono', 'mobile')),
            ('comment_text', ('comment_text', 'comentario', 'comment', 'texto', 'message')),
        ):
            value = pick(*keys)
            if value is not None:
                vals[field_name] = str(value).strip()

        # Titular en crudo: lo mandan la extensión de Chrome y cualquier origen
        # que lea el DOM de LinkedIn, donde cargo y empresa vienen en una sola
        # cadena. Se parte con la MISMA regla que el lector de pegados —una
        # sola implementación— y solo si no vinieron ya separados.
        headline = pick('headline', 'titular')
        if headline and not (vals.get('job_title') and vals.get('company_name')):
            job, company = split_headline(str(headline))
            vals.setdefault('job_title', job)
            if company:
                vals.setdefault('company_name', company)

        post_ref = pick('post_id', 'external_post_ref', 'post_urn', 'urn', 'post')
        if post_ref:
            post = self.env['marketing.linkedin.post'].search([
                ('profile_id', '=', profile.id),
                ('external_id', '=', str(post_ref)),
            ], limit=1)
            if post:
                vals['post_id'] = post.id
            else:
                vals['external_post_ref'] = str(post_ref)
        return vals

    # ── Procesado: puntuación, CRM y avisos ───────────────────────────────────

    def _process(self):
        """Puntúa la señal, la cruza con el CRM y dispara los automatismos."""
        for signal in self:
            profile = signal.profile_id
            icp_fit = profile.is_icp_fit(signal.company_name, signal.job_title)
            score = profile._score_for_signal_type(signal.signal_type)
            if icp_fit:
                score += profile.score_icp_bonus
            signal.write({'icp_fit': icp_fit, 'score': score})

            try:
                signal._link_to_crm()
            except Exception as exc:
                signal.write({
                    'state': 'pending',
                    'processing_error': str(exc)[:250],
                })
                _logger.exception('LinkedIn: señal %s no se pudo cruzar con el CRM', signal.id)
                continue

            signal._apply_automation()

    def _link_to_crm(self):
        """Busca lead o contacto existente; si no hay, crea el lead."""
        self.ensure_one()
        lead = self._find_matching_lead()
        if lead:
            self._register_on_lead(lead, is_new=False)
            self.write({'lead_id': lead.id, 'state': 'linked',
                        'partner_id': lead.partner_id.id or False,
                        'processing_error': False})
            return lead

        partner = self._find_matching_partner()
        if not self.profile_id.auto_create_lead:
            self.write({
                'state': 'pending',
                'partner_id': partner.id if partner else False,
            })
            return self.env['crm.lead']

        lead = self._create_lead(partner)
        self.write({'lead_id': lead.id, 'state': 'created',
                    'partner_id': partner.id if partner else False,
                    'processing_error': False})
        return lead

    def _find_matching_lead(self):
        """Busca el lead de esta persona sin cruzar datos entre perfiles.

        Orden de fiabilidad: URL de LinkedIn > email > nombre+empresa. El
        nombre suelto no basta: hay demasiados homónimos como para fusionar
        señales de dos personas distintas en un mismo lead.
        """
        self.ensure_one()
        Lead = self.env['crm.lead'].with_context(active_test=False)

        normalized_url = normalize_linkedin_url(self.linkedin_url)
        if normalized_url:
            # Se acota primero por el identificador del perfil (`/in/<slug>`)
            # con un ilike, y solo después se compara normalizado. Filtrar en
            # Python sobre todos los leads con URL funcionaba, pero recorría el
            # pipeline entero en cada fila del CSV.
            slug = normalized_url.rstrip('/').rsplit('/', 1)[-1]
            domain = [('x_linkedin_url', 'ilike', slug)] if slug else \
                [('x_linkedin_url', '!=', False)]
            candidates = Lead.search(domain, limit=200)
            match = candidates.filtered(
                lambda l: normalize_linkedin_url(l.x_linkedin_url) == normalized_url
            )[:1]
            if match:
                return match

        if self.email:
            match = Lead.search([('email_from', '=ilike', self.email.strip())], limit=1)
            if match:
                return match

        if self.contact_name and self.company_name:
            match = Lead.search([
                ('contact_name', '=ilike', self.contact_name.strip()),
                ('partner_name', '=ilike', self.company_name.strip()),
            ], limit=1)
            if match:
                return match

        if self.partner_id:
            match = Lead.search([('partner_id', '=', self.partner_id.id)], limit=1)
            if match:
                return match
        return Lead.browse()

    def _find_matching_partner(self):
        """Busca un contacto ya existente en la agenda, por email o nombre+empresa."""
        self.ensure_one()
        Partner = self.env['res.partner']
        if self.email:
            partner = Partner.search([('email', '=ilike', self.email.strip())], limit=1)
            if partner:
                return partner
        if self.contact_name and self.company_name:
            partner = Partner.search([
                ('name', '=ilike', self.contact_name.strip()),
                ('parent_id.name', '=ilike', self.company_name.strip()),
            ], limit=1)
            if partner:
                return partner
        return Partner.browse()

    def _create_lead(self, partner=None):
        """Crea el crm.lead de una señal nueva."""
        self.ensure_one()
        profile = self.profile_id
        source = self.env.ref(
            'crm_marketing_and_comunications.utm_source_linkedin_signal',
            raise_if_not_found=False,
        )
        medium = self.env.ref('utm.utm_medium_linkedin', raise_if_not_found=False)

        title = self.company_name or self.contact_name
        # 'lead' u 'opportunity' según lo que el comercial pueda ver.
        #
        # En Odoo, los registros de tipo `lead` solo existen en la interfaz si
        # está activada la función "Leads" del CRM (grupo crm.group_use_lead).
        # Sin ella no hay menú de leads y el pipeline muestra únicamente
        # oportunidades: crear `lead` deja los registros en la base pero
        # invisibles, que es peor que no crearlos — parece que el módulo no
        # funciona cuando en realidad sí.
        owner = profile.salesperson_id or profile.user_id
        tipo = 'lead' if owner.has_group('crm.group_use_lead') else 'opportunity'
        vals = {
            'name': f'LinkedIn — {title}',
            'type': tipo,
            'contact_name': self.contact_name,
            'partner_name': self.company_name or False,
            'function': self.job_title or False,
            'email_from': self.email or False,
            'phone': self.phone or False,
            'city': self.location or False,
            'x_linkedin_url': self.linkedin_url or False,
            'x_linkedin_profile_id': profile.id,
            'x_linkedin_score': 0,
            'description': self._build_lead_description(),
            'user_id': (profile.salesperson_id or profile.user_id).id,
        }
        if partner:
            vals['partner_id'] = partner.id
        if profile.team_id:
            vals['team_id'] = profile.team_id.id
        if profile.stage_new_id:
            vals['stage_id'] = profile.stage_new_id.id
        if source:
            vals['source_id'] = source.id
        if medium:
            vals['medium_id'] = medium.id

        lead = self.env['crm.lead'].with_context(sin_enriquecer_al_crear=True).create(vals)
        lead.tag_ids = [(4, self._get_signal_tag().id)]
        self._register_on_lead(lead, is_new=True)
        return lead

    def _build_lead_description(self):
        """Contexto de la señal que se guarda en las notas del lead."""
        self.ensure_one()
        lines = [
            f'<p><b>Origen:</b> señal de LinkedIn ({SIGNAL_TYPE_LABELS.get(self.signal_type)})</p>',
            f'<p><b>Fecha:</b> {self.signal_date}</p>',
        ]
        if self.linkedin_url:
            lines.append(f'<p><b>Perfil:</b> <a href="{self.linkedin_url}" '
                         f'target="_blank">{self.linkedin_url}</a></p>')
        if self.job_title:
            lines.append(f'<p><b>Cargo:</b> {self.job_title}</p>')
        if self.post_id:
            lines.append(f'<p><b>Publicación:</b> {self.post_id.name}</p>')
        if self.comment_text:
            lines.append(f'<p><b>Escribió:</b> «{self.comment_text}»</p>')
        return ''.join(lines)

    def _get_signal_tag(self):
        """Etiqueta de CRM por tipo de señal, creada bajo demanda."""
        self.ensure_one()
        label = f'LinkedIn: {SIGNAL_TYPE_LABELS.get(self.signal_type, "señal")}'
        # sudo: crear la etiqueta es un efecto colateral del procesado, no una
        # acción del usuario; un comercial sin permisos sobre crm.tag no debe
        # ver fallar la ingesta de una señal por eso.
        Tag = self.env['crm.tag'].sudo()
        tag = Tag.search([('name', '=', label)], limit=1)
        if not tag:
            tag = Tag.create({'name': label})
        return tag

    def _register_on_lead(self, lead, is_new=False):
        """Suma los puntos de la señal al lead y deja la nota en el chatter."""
        self.ensure_one()
        profile = self.profile_id
        new_score = (lead.x_linkedin_score or 0) + self.score
        write_vals = {
            'x_linkedin_score': new_score,
            'x_linkedin_last_signal_date': self.signal_date,
        }
        if not lead.x_linkedin_url and self.linkedin_url:
            write_vals['x_linkedin_url'] = self.linkedin_url
        if not lead.x_linkedin_profile_id:
            write_vals['x_linkedin_profile_id'] = profile.id
        lead.write(write_vals)

        if not is_new:
            lead.tag_ids = [(4, self._get_signal_tag().id)]

        detail = f'«{self.comment_text}»' if self.comment_text else ''
        post_ref = f' en «{self.post_id.name}»' if self.post_id else ''
        lead.message_post(
            body=Markup(
                f'<p><b>Nueva señal de LinkedIn:</b> '
                 f'{SIGNAL_TYPE_LABELS.get(self.signal_type)}{post_ref} '
                 f'el {self.signal_date}. {detail}</p>'
                 f'<p>Puntuación LinkedIn del lead: <b>{new_score}</b> '
                f'({"+" if self.score >= 0 else ""}{self.score}'
                f'{", encaja con el ICP" if self.icp_fit else ""}).</p>'
            ),
        )
        self.is_hot = new_score >= profile.hot_threshold

    def _apply_automation(self):
        """Cualificación automática, actividad de llamada y aviso.

        Sustituye a la acción automatizada que habría que configurar a mano en
        Odoo: aquí las reglas son campos del perfil, así que cada usuario tiene
        las suyas sin tocar Ajustes → Técnico.
        """
        self.ensure_one()
        if not self.lead_id or not self.is_hot:
            return
        profile = self.profile_id
        lead = self.lead_id

        if profile.auto_qualify and profile.stage_qualified_id and \
                lead.stage_id != profile.stage_qualified_id:
            vals = {'stage_id': profile.stage_qualified_id.id}
            # Cualificar es pasar al pipeline, no solo cambiar de etapa.
            #
            # Con la función "Leads" activa, un registro de tipo `lead` vive en
            # la bandeja de leads y NO aparece en el pipeline por mucho que se
            # le cambie la etapa. Para que el comercial lo vea donde trabaja,
            # hay que convertirlo en oportunidad. Sin esto, la etapa quedaba
            # bien puesta y el lead seguía sin salir por ningún lado.
            if lead.type == 'lead':
                vals['type'] = 'opportunity'
            lead.write(vals)

        if profile.auto_schedule_call:
            has_open_call = self.env['mail.activity'].search_count([
                ('res_model', '=', 'crm.lead'),
                ('res_id', '=', lead.id),
                ('activity_type_id', '=',
                 self.env.ref('mail.mail_activity_data_call').id),
            ])
            if not has_open_call:
                lead.activity_schedule(
                    'mail.mail_activity_data_call',
                    summary='Llamar — señal caliente de LinkedIn',
                    note=f'Puntuación LinkedIn: {lead.x_linkedin_score}. '
                         f'Última señal: {SIGNAL_TYPE_LABELS.get(self.signal_type)}.',
                    user_id=(profile.salesperson_id or profile.user_id).id,
                )

        self._notify_hot()

    def _notify_hot(self):
        """Avisa al dueño del perfil de que hay una señal caliente."""
        self.ensure_one()
        profile = self.profile_id
        lead = self.lead_id
        base_url = self.env['marketing.linkedin.profile']._get_odoo_base_url()
        lead_url = f'{base_url}/odoo/crm/{lead.id}' if lead else ''

        body = (
            f'<p><b>Señal caliente de LinkedIn</b></p>'
            f'<p>{self.contact_name}'
            f'{f" ({self.company_name})" if self.company_name else ""}'
            f'{f" — {self.job_title}" if self.job_title else ""}<br/>'
            f'{SIGNAL_TYPE_LABELS.get(self.signal_type)}'
            f'{f" en «{self.post_id.name}»" if self.post_id else ""}.<br/>'
            f'Puntuación del lead: <b>{lead.x_linkedin_score if lead else self.score}</b>.</p>'
        )
        if self.comment_text:
            body += f'<p>Escribió: «{self.comment_text}»</p>'
        if lead_url:
            body += f'<p>Ficha del lead: {lead_url}</p>'

        profile.notify(body)

        # Aviso adicional vía n8n. Viene DESACTIVADO de fábrica (sin ruta
        # configurada): Odoo ya ha avisado arriba por chatter y WhatsApp, y el
        # propio flujo del minero de red suele tener su nodo de notificación
        # después de mandar las señales. Activar los dos caminos a la vez
        # significa dos avisos por la misma señal. Se activa poniendo la ruta
        # en Ajustes, y entonces conviene quitar el nodo equivalente de n8n.
        if profile._get_webhook_url('hot_signal'):
            profile.call_n8n('hot_signal', {
                'action': 'hot_signal',
                'signal_id': self.id,
                'lead_id': lead.id if lead else False,
                'lead_url': lead_url,
                'contact_name': self.contact_name,
                'company_name': self.company_name,
                'job_title': self.job_title,
                'linkedin_url': self.linkedin_url,
                'signal_type': self.signal_type,
                'comment_text': self.comment_text,
                'score': lead.x_linkedin_score if lead else self.score,
                'icp_fit': self.icp_fit,
            })

    # ── Actividad saliente (Prosp) ────────────────────────────────────────────

    @api.model
    def _log_prosp_outbound(self, profile, extracted, mapping):
        """Anota en el lead una acción que hemos hecho nosotros. No puntúa.

        Deliberadamente NO crea el lead si no existe: los eventos salientes son
        todo lo que la automatización dispara sobre cada contacto de cada
        campaña, y crear un lead por cada «mensaje enviado» llenaría el pipeline
        de gente que no ha mostrado el menor interés. El lead nace cuando el
        prospecto responde o acepta, que es cuando hay algo que trabajar.

        Devuelve el id del lead anotado, o False si no había ninguno.
        """
        lead = self._find_lead_by_linkedin_url(extracted.get('linkedin_url'))
        if not lead:
            _logger.debug(
                'Prosp: acción saliente "%s" sobre %s sin lead en el CRM; '
                'no se crea nada.', mapping.name, extracted.get('linkedin_url'),
            )
            return False

        campaign = extracted.get('campaign_name') or extracted.get('campaign_id') or ''
        detail = f'<br/>{extracted["content"]}' if extracted.get('content') else ''
        lead.message_post(
            body=Markup(
                f'<p><b>Prosp — {mapping.name}</b>'
                f'{f" (campaña: {campaign})" if campaign else ""}.'
                f'{detail}</p>'
                f'<p class="text-muted">Acción saliente: no suma puntuación '
                f'de LinkedIn.</p>'
            ),
        )
        return lead.id

    @api.model
    def _find_lead_by_linkedin_url(self, url):
        """Localiza un lead por su URL de LinkedIn, normalizada."""
        normalized = normalize_linkedin_url(url)
        if not normalized:
            return self.env['crm.lead']
        slug = normalized.rstrip('/').rsplit('/', 1)[-1]
        domain = [('x_linkedin_url', 'ilike', slug)] if slug else \
            [('x_linkedin_url', '!=', False)]
        candidates = self.env['crm.lead'].with_context(active_test=False).search(
            domain, limit=200,
        )
        return candidates.filtered(
            lambda l: normalize_linkedin_url(l.x_linkedin_url) == normalized
        )[:1]

    # ── Acciones manuales ─────────────────────────────────────────────────────

    def action_process(self):
        """Reprocesa señales que quedaron sin vincular o con error."""
        for signal in self:
            if signal.state == 'discarded':
                continue
            signal._process()
        return True

    def action_discard(self):
        """Descarta la señal sin borrarla: deja de contar en las métricas."""
        for signal in self:
            signal.write({'state': 'discarded', 'is_hot': False})
        return True

    def action_open_lead(self):
        self.ensure_one()
        if not self.lead_id:
            raise UserError(
                'Esta señal no tiene lead. Pulsa "Procesar" para intentar '
                'vincularla o créalo a mano desde el CRM.'
            )
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'crm.lead',
            'res_id': self.lead_id.id,
            'view_mode': 'form',
        }

    def action_open_linkedin(self):
        self.ensure_one()
        if not self.linkedin_url:
            raise UserError('Esta señal no trae URL de LinkedIn.')
        return {'type': 'ir.actions.act_url', 'url': self.linkedin_url, 'target': 'new'}
