"""Perfil LinkedIn: la unidad multiusuario del flujo LinkedIn Growth.

Todo lo que en el planteamiento original estaba escrito "a medida de Enric"
(rol, contexto de empresa, ICP, tono, umbrales, teléfono de aviso) vive aquí
como datos del perfil. Cada usuario —o cada cliente, si el módulo se despliega
en su base— crea el suyo, y las señales, publicaciones y diagnósticos cuelgan
de un perfil concreto. Ningún texto de negocio está incrustado en el código.
"""
import json
import logging
import re
import secrets

from markupsafe import Markup

from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError

from .linkedin_common import SCORE_FIELD_BY_TYPE, TRACK_FIELD_BY_TYPE

_logger = logging.getLogger(__name__)

_P = 'crm_marketing_and_comunications.'


def _sin_acentos(texto):
    """Quita tildes para que "Fundación" case con la keyword "fundacion"."""
    import unicodedata
    return ''.join(
        c for c in unicodedata.normalize('NFD', texto or '')
        if unicodedata.category(c) != 'Mn'
    )


def _casa_palabra(kw, texto):
    """Coincidencia por PALABRA COMPLETA, no por subcadena.

    Imprescindible para siglas cortas: buscar "ia" como subcadena casaría con
    "Cerámicas", "Valencia" o "Logística" y excluiría media base de datos. Con
    límites de palabra, "ia" solo casa con "IA Labs" o "consultora IA".

    Las claves de varias palabras ("inteligencia artificial") se tratan igual:
    la secuencia completa debe aparecer delimitada.
    """
    import re
    return re.search(r'(?<![0-9a-z])' + re.escape(kw) + r'(?![0-9a-z])', texto) is not None


class MarketingLinkedinProfile(models.Model):
    _name = 'marketing.linkedin.profile'
    _description = 'Perfil de LinkedIn (contexto y reglas por usuario)'
    _order = 'name'
    _inherit = ['mail.thread']

    # ── Identificación ────────────────────────────────────────────────────────
    name = fields.Char(
        string='Nombre del perfil', required=True, tracking=True,
        help='Cómo se llama este perfil dentro de Odoo. Ej: "Enric — Uniasser".',
    )
    user_id = fields.Many2one(
        'res.users',
        string='Usuario propietario',
        required=True,
        default=lambda self: self.env.user,
        tracking=True,
        help='Usuario dueño del perfil de LinkedIn. Las señales y publicaciones '
             'de este perfil se le atribuyen a él, y solo él (o un responsable) '
             'las ve si están activas las reglas de registro.',
    )
    company_id = fields.Many2one(
        'res.company',
        string='Compañía',
        required=True,
        default=lambda self: self.env.company,
    )
    active = fields.Boolean(default=True)
    linkedin_url = fields.Char(
        string='URL del perfil de LinkedIn',
        help='Ej: https://www.linkedin.com/in/tu-perfil/',
    )
    prosp_api_key = fields.Char(
        string='Prosp API Key',
        password=True,
        help='API Key personal obtenida en los Ajustes de tu cuenta de Prosp.'
    )

    # ── Rol y contexto (lo que hace el sistema multiusuario) ──────────────────
    role_description = fields.Text(
        string='Rol',
        required=True,
        help='Quién eres y desde dónde hablas. Se inyecta literalmente en el '
             'prompt de la IA. Ej: "Consultor B2B para founders/CEOs de PYMEs '
             'innovadoras".',
    )
    business_context = fields.Text(
        string='Contexto de negocio',
        required=True,
        help='Empresa, mercado, servicios y propuesta de valor. Se inyecta en '
             'todos los prompts (router de embudo y preparación de llamada).',
    )
    icp_description = fields.Text(
        string='Descripción del cliente ideal (ICP)',
        help='Descripción en texto libre del cliente ideal. La usa la IA para '
             'valorar el encaje; el filtro automático usa las keywords de abajo.',
    )
    icp_sector_keywords = fields.Char(
        string='Keywords de sector (ICP)',
        help='Separadas por comas. Si la empresa del contacto contiene alguna, '
             'suma el bonus de encaje ICP. Ej: "industrial, cerámica, logística".',
    )
    icp_role_keywords = fields.Char(
        string='Keywords de cargo (ICP)',
        help='Separadas por comas. Ej: "ceo, founder, director general, gerente".',
    )
    icp_exclude_keywords = fields.Char(
        string='Keywords de exclusión',
        help='Separadas por comas. Si aparecen en cargo o empresa, la señal '
             'nunca cuenta como ICP. Ej: "estudiante, becario, recruiter".',
    )
    exclusion_ids = fields.One2many(
        'marketing.linkedin.exclusion', 'profile_id',
        string='Clientes a excluir',
        # One2many es copy=False por defecto: sin esto, duplicar un perfil para
        # un segundo comercial se llevaría los criterios de ICP pero perdería
        # las exclusiones en silencio, y volvería a prospectar lo descartado.
        copy=True,
        help='Categorías de empresas que NO interesan. Un lead que caiga en '
             'una categoría activa recibe puntuación 0, salvo excepción manual '
             'marcada en su ficha.',
    )
    exclusion_count = fields.Integer(compute='_compute_exclusion_count')

    tone_guidelines = fields.Text(
        string='Tono y estilo',
        help='Cómo debe sonar lo que genere la IA para este perfil.',
    )

    # ── Qué señales seguir ────────────────────────────────────────────────────
    # Un interruptor por tipo. Los tipos desmarcados se descartan al entrar:
    # no crean señal, ni lead, ni puntúan. Se cuentan aparte en el lote, para
    # que quede claro que la fila llegó y se ignoró a propósito y no que se
    # perdió por el camino.
    track_comment = fields.Boolean(string='Seguir comentarios', default=True)
    track_reaction = fields.Boolean(string='Seguir reacciones / likes', default=True)
    track_share = fields.Boolean(string='Seguir compartidos', default=True)
    track_mention = fields.Boolean(string='Seguir menciones', default=True)
    track_profile_view = fields.Boolean(string='Seguir vistas de perfil', default=True)
    track_connection = fields.Boolean(string='Seguir nuevas conexiones', default=True)
    track_follow = fields.Boolean(string='Seguir nuevos seguidores', default=True)
    track_message = fields.Boolean(string='Seguir mensajes directos', default=True)

    # ── Puntuación de señales ─────────────────────────────────────────────────
    score_comment = fields.Integer(string='Comentario', default=2)
    score_reaction = fields.Integer(string='Reacción / Like', default=1)
    score_share = fields.Integer(string='Compartido', default=2)
    score_mention = fields.Integer(string='Mención', default=3)
    score_profile_view = fields.Integer(string='Vista de perfil', default=1)
    score_connection = fields.Integer(string='Nueva conexión', default=2)
    score_follow = fields.Integer(string='Nuevo seguidor', default=1)
    score_message = fields.Integer(string='Mensaje directo', default=3)
    score_icp_bonus = fields.Integer(
        string='Bonus por encaje ICP', default=2,
        help='Puntos extra que suma una señal cuando el contacto encaja con el ICP.',
    )
    hot_threshold = fields.Integer(
        string='Umbral de señal caliente', default=4,
        help='Puntuación acumulada del lead a partir de la cual se considera '
             'una señal caliente: dispara el aviso y, si está activo, la '
             'cualificación automática.',
    )

    # ── Umbrales del router de embudo ─────────────────────────────────────────
    router_period_days = fields.Integer(
        string='Periodo de análisis (días)', default=30,
        help='Ventana que analiza el router de embudo. 30 días para el ritmo '
             'semanal; 90 para revisar tendencia.',
    )
    router_min_engagement_rate = fields.Float(
        string='Engagement mínimo (%)', default=3.0, digits=(16, 2),
        help='Por debajo de esta tasa media se considera que el contenido no '
             'está generando atención suficiente.',
    )
    router_min_leads = fields.Integer(
        string='Leads mínimos en el periodo', default=8,
        help='Volumen de leads nuevos que se espera en el periodo. Por debajo, '
             'el router entiende que no está entrando gente al pipeline.',
    )
    router_min_signals = fields.Integer(
        string='Señales mínimas en el periodo', default=20,
        help='Interacciones de LinkedIn que se esperan en el periodo.',
    )
    router_min_signal_to_lead = fields.Float(
        string='Conversión mínima señal → lead (%)', default=25.0, digits=(16, 2),
        help='Porcentaje de señales que deberían acabar en lead. Por debajo, '
             'el cuello de botella es de captación, no de posicionamiento.',
    )
    router_stage_stuck_days = fields.Integer(
        string='Días para considerar un lead parado', default=21,
        help='Un lead sin movimiento durante más de estos días cuenta como '
             'atascado en su etapa.',
    )
    router_cron_enabled = fields.Boolean(
        string='Ejecutar el router automáticamente', default=True,
        help='Incluye este perfil en el cron semanal del router de embudo.',
    )

    # ── Automatización sobre el CRM ───────────────────────────────────────────
    auto_create_lead = fields.Boolean(
        string='Crear lead automáticamente', default=True,
        help='Si no existe lead ni contacto para la señal, crea un crm.lead '
             'nuevo. Si se desactiva, la señal queda registrada sin lead y '
             'se puede convertir a mano.',
    )
    team_id = fields.Many2one(
        'crm.team', string='Equipo de ventas',
        help='Equipo al que se asignan los leads creados desde señales.',
    )
    salesperson_id = fields.Many2one(
        'res.users', string='Comercial asignado',
        help='Comercial de los leads creados desde señales. Si se deja vacío, '
             'se asigna al usuario propietario del perfil.',
    )
    stage_new_id = fields.Many2one(
        'crm.stage', string='Etapa inicial',
        help='Etapa en la que entran los leads creados desde una señal. '
             'Vacío = la etapa por defecto del pipeline.',
    )
    stage_qualified_id = fields.Many2one(
        'crm.stage', string='Etapa de cualificado',
        help='Etapa a la que se mueve el lead cuando supera el umbral de '
             'señal caliente y la cualificación automática está activa.',
    )
    auto_qualify = fields.Boolean(
        string='Cualificar automáticamente', default=True,
        help='Mueve el lead a la etapa de cualificado al superar el umbral.',
    )
    auto_schedule_call = fields.Boolean(
        string='Planificar llamada automáticamente', default=True,
        help='Crea una actividad de tipo llamada cuando el lead supera el umbral.',
    )
    notify_whatsapp = fields.Boolean(
        string='Avisar por WhatsApp', default=False,
        help='Envía un WhatsApp al número de aviso cuando entra una señal caliente. '
             'Requiere una cuenta activa del módulo uniasser_whatsapp.',
    )
    notify_phone = fields.Char(
        string='Teléfono de aviso',
        help='Número con prefijo internacional al que se mandan los avisos de '
             'señal caliente y los informes del router. Ej: +34600000000.',
    )

    # ── Integración con n8n ───────────────────────────────────────────────────
    webhook_token = fields.Char(
        string='Token de este perfil',
        readonly=True, copy=False, index=True,
        default=lambda self: secrets.token_urlsafe(32),
        help='Token que identifica a este perfil en TODAS las llamadas entre '
             'Odoo y n8n, en los dos sentidos, con la cabecera '
             'X-Vantis-Token. Es uno por perfil, no uno por webhook.\n\n'
             'No lleva restricción de administrador a propósito: las reglas de '
             'registro ya hacen que cada usuario vea solo sus propios perfiles, '
             'y quien configura su flujo de n8n necesita poder copiar su token '
             'sin depender de que un administrador se lo pase.',
    )
    n8n_base_url = fields.Char(
        string='URL de n8n (específica)',
        help='Solo si este perfil usa una instancia de n8n distinta de la '
             'configurada en Ajustes. Vacío = usar la global.',
    )
    drive_folder = fields.Char(
        string='Carpeta de Drive',
        default='/Minero-LinkedIn/inbox/',
        help='Carpeta que vigila el trigger de Google Drive en n8n para este '
             'perfil. Informativo desde Odoo: quien la vigila es n8n.',
    )
    ai_mode = fields.Selection([
        ('odoo', 'Odoo llama a Claude directamente'),
        ('n8n', 'Delegar en el flujo de n8n'),
    ], string='Motor de IA', default='odoo', required=True,
        help='"Odoo" genera el informe del router y la preparación de llamada '
             'con la clave de Anthropic de Ajustes, sin salir de Odoo. "n8n" '
             'dispara el webhook y espera a que el flujo devuelva el resultado '
             'por el endpoint de retorno.',
    )

    # ── Contadores ────────────────────────────────────────────────────────────
    signal_ids = fields.One2many(
        'marketing.linkedin.signal', 'profile_id', string='Señales del perfil',
    )
    post_ids = fields.One2many(
        'marketing.linkedin.post', 'profile_id', string='Publicaciones del perfil',
    )
    diagnostic_ids = fields.One2many(
        'marketing.linkedin.diagnostic', 'profile_id', string='Diagnósticos del perfil',
    )
    signal_count = fields.Integer(string='Señales', compute='_compute_counts')
    post_count = fields.Integer(string='Publicaciones', compute='_compute_counts')
    lead_count = fields.Integer(string='Leads generados', compute='_compute_counts')
    diagnostic_count = fields.Integer(string='Diagnósticos', compute='_compute_counts')

    # ── Guía de integración ───────────────────────────────────────────────────
    integration_guide = fields.Html(
        string='Cómo conectar n8n con este perfil',
        compute='_compute_integration_guide',
        sanitize=False,
    )

    _unique_user_name = models.Constraint(
        'UNIQUE(user_id, name)',
        'Ya existe un perfil de LinkedIn con ese nombre para este usuario.',
    )

    # ── Cómputos ──────────────────────────────────────────────────────────────

    @api.depends('signal_ids', 'post_ids', 'diagnostic_ids', 'signal_ids.lead_id')
    def _compute_counts(self):
        for profile in self:
            profile.signal_count = len(profile.signal_ids)
            profile.post_count = len(profile.post_ids)
            profile.diagnostic_count = len(profile.diagnostic_ids)
            profile.lead_count = len(set(profile.signal_ids.mapped('lead_id').ids))

    def _compute_integration_guide(self):
        """Genera la chuleta de integración con las URLs y el token reales.

        Es lo primero que hay que copiar al montar el flujo en n8n, así que se
        muestra ya resuelta en la ficha del perfil en vez de en un README que
        nadie abre.
        """
        base_url = self._get_odoo_base_url()
        for profile in self:
            token = profile.sudo().webhook_token or '(sin token: guarda el perfil)'
            n8n = profile._get_n8n_base_url() or '(sin configurar)'
            profile.integration_guide = f"""
<div class="o_linkedin_guide">
  <p class="text-muted">
    Copia estos datos en los nodos HTTP Request de n8n. Todas las llamadas
    entrantes se autentican con la cabecera
    <code>X-Vantis-Token</code>, que además identifica a este perfil:
    n8n no tiene que mandar ningún <code>profile_id</code>.
  </p>
  <table class="table table-sm">
    <tr><th style="width:32%">URL base de Odoo</th><td><code>{base_url}</code></td></tr>
    <tr><th>Token de este perfil</th><td><code>{token}</code></td></tr>
    <tr><th>URL base de n8n</th><td><code>{n8n}</code></td></tr>
  </table>
  <h5>n8n → Odoo (entrantes)</h5>
  <table class="table table-sm">
    <tr><th style="width:32%">Señales del minero de red</th>
        <td><code>POST {base_url}/vantis/linkedin/signals</code></td></tr>
    <tr><th>Métricas de publicaciones</th>
        <td><code>POST {base_url}/vantis/linkedin/posts</code></td></tr>
    <tr><th>Resultado de preparación de llamada</th>
        <td><code>POST {base_url}/vantis/linkedin/call-prep</code></td></tr>
    <tr><th>Resultado del router de embudo</th>
        <td><code>POST {base_url}/vantis/linkedin/diagnostic</code></td></tr>
    <tr><th>Métricas para el router (lectura)</th>
        <td><code>GET {base_url}/vantis/linkedin/metrics?days=30</code></td></tr>
    <tr><th>Comprobación de conexión</th>
        <td><code>GET {base_url}/vantis/linkedin/ping</code></td></tr>
  </table>
  <h5>Prosp</h5>
  <table class="table table-sm">
    <tr><th style="width:32%">Callback URL para Prosp</th>
        <td><code>POST {profile.get_prosp_url()}</code></td></tr>
    <tr><th>Captura genérica (diagnóstico)</th>
        <td><code>POST {profile.get_capture_url('prosp')}</code></td></tr>
  </table>
  <p class="text-muted">
    Pega la primera en <b>Prosp → Create a webhook → Callback URL</b>. Los
    eventos entrantes (respuestas, conexiones aceptadas) se convierten en
    señales y puntúan; los salientes se anotan en el lead sin puntuar. Los
    eventos que no reconozca se guardan en <b>Webhooks capturados</b> para
    mapearlos desde <b>Eventos de Prosp</b>, sin tocar el módulo.
  </p>
  <p class="text-muted">
    Apunta aquí un webhook de un servicio externo cuyo formato no conozcas.
    Guarda el cuerpo y las cabeceras tal cual, <b>sin tocar el CRM</b>, para
    poder escribir el mapeo con un evento real delante. El token va en la URL
    porque servicios como Prosp solo permiten configurar la dirección, sin
    cabeceras propias. Cambia el último tramo (<code>/prosp</code>) para
    distinguir varios orígenes.
  </p>

  <h5>Odoo → n8n (salientes)</h5>
  <table class="table table-sm">
    <tr><th style="width:32%">Preparar llamada (botón del lead)</th>
        <td>{profile._describe_webhook('call_prep',
            'solo si el motor de IA de este perfil es «n8n»')}</td></tr>
    <tr><th>Router de embudo</th>
        <td>{profile._describe_webhook('router',
            'solo si el motor de IA de este perfil es «n8n»')}</td></tr>
    <tr><th>Aviso de señal caliente</th>
        <td>{profile._describe_webhook('hot_signal',
            'opcional: Odoo ya avisa por chatter y WhatsApp')}</td></tr>
    <tr><th>Probar conexión</th>
        <td>{profile._describe_webhook('ping', '')}</td></tr>
  </table>
  <p class="text-muted">
    Una ruta vacía significa que Odoo <b>no</b> llama a n8n para esa acción.
    Se cambian en <b>Ajustes → Marketing Campaigns → LinkedIn Growth</b>.
  </p>
</div>
"""

    def get_prosp_url(self):
        """Callback URL que hay que pegar en Prosp."""
        self.ensure_one()
        base = self._get_odoo_base_url()
        token = self.sudo().webhook_token or ''
        if not base or not token:
            return ''
        return f'{base}/vantis/linkedin/prosp/{token}'

    def get_capture_url(self, source='prosp'):
        """URL a la que apuntar un webhook externo para capturar su formato.

        El token va dentro de la ruta porque los servicios que hay que
        diagnosticar suelen dejar configurar solo la URL, sin cabeceras.
        """
        self.ensure_one()
        base = self._get_odoo_base_url()
        token = self.sudo().webhook_token or ''
        if not base or not token:
            return ''
        return f'{base}/vantis/linkedin/capture/{token}/{source}'

    def _describe_webhook(self, param_suffix, note):
        """Texto de una fila de la guía: la URL real o el motivo de que no haya."""
        self.ensure_one()
        url = self._get_webhook_url(param_suffix)
        if url:
            suffix = f' <span class="text-muted">({note})</span>' if note else ''
            return f'<code>POST {url}</code>{suffix}'
        if not self._get_n8n_base_url():
            return '<span class="text-muted">sin URL de n8n configurada</span>'
        detail = f' — {note}' if note else ''
        return f'<span class="text-muted">desactivado (ruta vacía){detail}</span>'

    # ── Utilidades de configuración ───────────────────────────────────────────

    @api.model
    def _get_odoo_base_url(self):
        """URL pública de este Odoo tal y como debe verla n8n.

        No se usa `web.base.url` directamente porque ese parámetro es de toda
        la instancia —lo consumen los correos, el portal y los enlaces de
        cualquier módulo—, suele estar congelado (`web.base.url.freeze`) y no
        tiene por qué coincidir con el nombre por el que se quiere exponer la
        API. Aquí se puede fijar uno propio en Ajustes; si se deja vacío, se
        cae al general, que es lo correcto en una instalación sencilla.
        """
        icp = self.env['ir.config_parameter'].sudo()
        return (
            icp.get_param(f'{_P}linkedin_odoo_base_url', '')
            or icp.get_param('web.base.url', '')
            or ''
        ).rstrip('/')

    def _get_n8n_base_url(self):
        """URL de n8n de este perfil, con caída a la global de Ajustes."""
        self.ensure_one()
        if self.n8n_base_url:
            return self.n8n_base_url.rstrip('/')
        icp = self.env['ir.config_parameter'].sudo()
        return (icp.get_param(f'{_P}linkedin_n8n_base_url', '') or '').rstrip('/')

    def _get_webhook_url(self, param_suffix):
        """URL del webhook saliente, o '' si no hay ninguna ruta configurada.

        Regla única: **sin ruta configurada, Odoo no llama a n8n.** Vaciar el
        campo en Ajustes es la forma de apagar un aviso.

        No hay ruta por defecto en el código a propósito. Las rutas de fábrica
        se siembran una sola vez en `data/linkedin_data.xml`; si el código
        además cayera en un valor por defecto cuando el parámetro falta,
        vaciarlo no serviría de nada — y Odoo borra el parámetro (no lo deja
        vacío) cuando se guarda un campo `char` en blanco, así que "falta" y
        "lo he apagado" son el mismo estado desde la interfaz.
        """
        self.ensure_one()
        base = self._get_n8n_base_url()
        if not base:
            return ''
        path = (self.env['ir.config_parameter'].sudo().get_param(
            f'{_P}linkedin_webhook_{param_suffix}', '',
        ) or '').strip()
        if not path:
            return ''
        if not path.startswith('/'):
            path = '/' + path
        return base + path

    @api.model
    def _get_profile_for_token(self, token):
        """Devuelve el perfil dueño de un token entrante, o un recordset vacío.

        Se usa desde el controlador HTTP, sin usuario autenticado, así que va
        en sudo. La búsqueda es por token exacto: no hay fallback a "el primer
        perfil activo", porque eso mezclaría datos entre usuarios.
        """
        if not token:
            return self.browse()
        return self.sudo().with_context(active_test=False).search(
            [('webhook_token', '=', token)], limit=1,
        )

    @api.model
    def _get_default_profile(self, user=None):
        """Perfil por defecto de un usuario (el primero activo que posea)."""
        user = user or self.env.user
        return self.search([('user_id', '=', user.id)], limit=1)

    @api.model
    def _create_seed_profile(self, vals):
        """Crea un perfil de ejemplo a partir de datos XML, si procede.

        Se llama desde `data/linkedin_seed_data.xml` con noupdate. Resuelve el
        usuario por login en lugar de por XML-ID porque el mismo módulo se
        instala en bases de clientes donde ese login no existe: allí la función
        no hace nada, en vez de reventar la instalación o crear el perfil de
        otro. Tampoco pisa nada: si el usuario ya tiene un perfil con ese
        nombre, se respeta lo que haya escrito.
        """
        login = (vals or {}).pop('login', '')
        if not login:
            return False
        user = self.env['res.users'].sudo().search([('login', '=', login)], limit=1)
        if not user:
            _logger.info(
                'LinkedIn: no se crea el perfil de ejemplo, no existe el usuario %s.',
                login,
            )
            return False
        existing = self.sudo().with_context(active_test=False).search([
            ('user_id', '=', user.id), ('name', '=', vals.get('name')),
        ], limit=1)
        if existing:
            return existing.id
        vals['user_id'] = user.id
        vals.setdefault('company_id', user.company_id.id)
        profile = self.sudo().create(vals)
        _logger.info('LinkedIn: perfil de ejemplo «%s» creado para %s.',
                     profile.name, login)
        return profile.id

    def action_regenerate_token(self):
        """Genera un token nuevo. Invalida el que esté puesto en n8n."""
        for profile in self:
            profile.sudo().webhook_token = secrets.token_urlsafe(32)
            profile.message_post(
                body='Token de integración regenerado. Actualiza la cabecera '
                     'X-Vantis-Token en los nodos HTTP Request de n8n.',
            )
        return True

    # ── Puntuación y encaje ICP ───────────────────────────────────────────────

    def is_tracked_signal_type(self, signal_type):
        """¿Este perfil quiere registrar señales de este tipo?

        Un tipo desconocido devuelve False: si LinkedIn empieza a exportar una
        interacción nueva, es mejor ignorarla y que se vea en el recuento del
        lote que colarla sin criterio de puntuación.
        """
        self.ensure_one()
        field_name = TRACK_FIELD_BY_TYPE.get(signal_type)
        if not field_name:
            return False
        return bool(self[field_name])

    def _compute_exclusion_count(self):
        for rec in self:
            rec.exclusion_count = len(rec.exclusion_ids.filtered('active'))

    def action_crear_exclusiones_por_defecto(self):
        """Siembra las categorías habituales para no empezar de cero."""
        self.ensure_one()
        creadas = self.env['marketing.linkedin.exclusion'].crear_categorias_por_defecto(self)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Categorías creadas' if creadas else 'Ya había categorías',
                'message': (f'{len(creadas)} categorías de exclusión añadidas. '
                            'Revísalas y ajusta las palabras clave.') if creadas
                           else 'Este perfil ya tiene categorías de exclusión definidas.',
                'type': 'success' if creadas else 'info',
            },
        }

    def matching_exclusion(self, company_name='', job_title=''):
        """Devuelve la categoría de exclusión que casa, o un recordset vacío.

        Busca en empresa Y cargo, sin distinguir mayúsculas ni acentos: un lead
        escrito como "Fundación" tiene que casar con la keyword "fundacion".
        """
        self.ensure_one()
        Excl = self.env['marketing.linkedin.exclusion']
        haystack = _sin_acentos(f'{company_name or ""} {job_title or ""}'.lower())
        if not haystack.strip():
            return Excl.browse()

        for cat in self.exclusion_ids.filtered('active'):
            for kw in self._split_keywords(cat.keywords):
                if kw and _casa_palabra(_sin_acentos(kw), haystack):
                    return cat
        return Excl.browse()

    def _score_for_signal_type(self, signal_type):
        """Puntos que vale un tipo de señal según la configuración del perfil."""
        self.ensure_one()
        field_name = SCORE_FIELD_BY_TYPE.get(signal_type)
        if not field_name:
            return 0
        return self[field_name] or 0

    @staticmethod
    def _split_keywords(raw):
        if not raw:
            return []
        return [kw.strip().lower() for kw in raw.split(',') if kw.strip()]

    def is_icp_fit(self, company_name='', job_title=''):
        """Evalúa el encaje ICP por keywords de sector, cargo y exclusión.

        La exclusión manda: un "recruiter" de una empresa del sector objetivo
        sigue sin ser ICP. Si el perfil no define ninguna keyword positiva,
        devuelve False en vez de True — sin criterio configurado no se puede
        afirmar que encaja, y marcar todo como ICP inflaría el score de todos
        los leads a la vez.
        """
        self.ensure_one()
        haystack = f'{company_name or ""} {job_title or ""}'.lower()
        if not haystack.strip():
            return False

        for kw in self._split_keywords(self.icp_exclude_keywords):
            if kw in haystack:
                return False

        # Categorías estructuradas de "Clientes a excluir": mandan igual que
        # las keywords sueltas de arriba.
        if self.matching_exclusion(company_name, job_title):
            return False

        sector_kws = self._split_keywords(self.icp_sector_keywords)
        role_kws = self._split_keywords(self.icp_role_keywords)
        if not sector_kws and not role_kws:
            return False

        sector_hit = any(kw in (company_name or '').lower() for kw in sector_kws)
        role_hit = any(kw in (job_title or '').lower() for kw in role_kws)
        # Si el perfil solo ha definido uno de los dos criterios, basta con ese.
        if sector_kws and role_kws:
            return sector_hit or role_hit
        return sector_hit or role_hit

    # ── Contexto para los prompts de IA ───────────────────────────────────────

    def get_ai_context(self):
        """Bloque de contexto que se antepone a cualquier prompt de este perfil.

        Sustituye al contexto fijo de marca que usa el flujo de campañas: aquí
        el texto sale del perfil, no de una constante del módulo, porque cada
        usuario del sistema tiene el suyo.
        """
        self.ensure_one()
        parts = [
            '=== ROL ===',
            self.role_description or '(sin definir)',
            '',
            '=== CONTEXTO DE NEGOCIO ===',
            self.business_context or '(sin definir)',
        ]
        if self.icp_description:
            parts += ['', '=== CLIENTE IDEAL (ICP) ===', self.icp_description]
        icp_bits = []
        if self.icp_sector_keywords:
            icp_bits.append(f'Sectores objetivo: {self.icp_sector_keywords}')
        if self.icp_role_keywords:
            icp_bits.append(f'Cargos objetivo: {self.icp_role_keywords}')
        if self.icp_exclude_keywords:
            icp_bits.append(f'Excluir: {self.icp_exclude_keywords}')
        if icp_bits:
            parts += ['', *icp_bits]
        if self.tone_guidelines:
            parts += ['', '=== TONO Y ESTILO ===', self.tone_guidelines]
        return '\n'.join(parts)

    # ── Avisos ────────────────────────────────────────────────────────────────

    def notify(self, body, force_whatsapp=False):
        """Manda un aviso al dueño del perfil.

        Siempre deja el aviso en el chatter del perfil (queda rastro aunque no
        haya WhatsApp configurado) y, si procede, lo manda también por WhatsApp.
        Nunca levanta excepción: un fallo de aviso no puede tumbar la ingesta
        de señales ni un cron.
        """
        self.ensure_one()
        try:
            # Markup: desde Odoo 17 `message_post` escapa el HTML de un `str`
            # plano, y la nota se vería con las etiquetas a la vista.
            self.message_post(body=Markup(body), partner_ids=self.user_id.partner_id.ids)
        except Exception:
            _logger.exception('LinkedIn: no se pudo publicar el aviso en el chatter')

        if not (force_whatsapp or self.notify_whatsapp):
            return False
        phone = (self.notify_phone or '').strip()
        if not phone:
            _logger.warning(
                'LinkedIn: perfil %s tiene avisos por WhatsApp activos pero no '
                'hay teléfono de aviso.', self.name,
            )
            return False
        return self._send_whatsapp(phone, body)

    def _send_whatsapp(self, phone, body):
        """Envía el aviso por la cuenta activa de uniasser_whatsapp.

        Replica el reparto por tipo de conexión del wizard de ese módulo
        (QR → Evolution API, resto → Graph API) para no duplicar credenciales.
        """
        self.ensure_one()
        account = self.env['whatsapp.account'].sudo().search(
            [('active', '=', True)], limit=1,
        )
        if not account:
            _logger.warning('LinkedIn: no hay cuenta de WhatsApp activa para el aviso.')
            return False

        from odoo.addons.uniasser_whatsapp.models.whatsapp_api import WhatsAppAPI

        # El cuerpo llega como HTML porque también va al chatter.
        plain = self._html_to_text(body)
        normalized = WhatsAppAPI.normalize_phone(phone)
        if not normalized:
            _logger.warning('LinkedIn: teléfono de aviso no normalizable: %s', phone)
            return False
        try:
            if account.connection_type == 'qr_code':
                account._evo_send_text(normalized, plain)
            else:
                account._get_api().send_text(to=normalized, body=plain)
        except Exception:
            _logger.exception('LinkedIn: fallo enviando el aviso por WhatsApp')
            return False
        return True

    @staticmethod
    def _html_to_text(value):
        """Convierte el HTML del aviso en texto plano para WhatsApp."""
        text = re.sub(r'<br\s*/?>', '\n', value or '')
        text = re.sub(r'</p>', '\n', text)
        text = re.sub(r'<[^>]+>', '', text)
        text = text.replace('&nbsp;', ' ').replace('&amp;', '&')
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    # ── Llamadas salientes a n8n ──────────────────────────────────────────────

    def call_n8n(self, param_suffix, payload, timeout=15):
        """POST a un webhook de n8n con el token de este perfil.

        Devuelve (ok, mensaje). No levanta excepción para que los crons y los
        botones puedan decidir qué hacer con el fallo.
        """
        self.ensure_one()
        url = self._get_webhook_url(param_suffix)
        if not url:
            return False, (
                'No hay ruta de webhook configurada para esta acción. '
                'Revísala en Ajustes → Marketing Campaigns → LinkedIn Growth '
                '(una ruta vacía significa que el aviso está desactivado).'
            )

        import urllib.error
        import urllib.request

        body = dict(payload or {})
        body.setdefault('profile_id', self.id)
        body.setdefault('profile_name', self.name)
        body.setdefault('odoo_base_url', self._get_odoo_base_url())

        request = urllib.request.Request(
            url,
            data=json.dumps(body, default=str).encode(),
            headers={
                'Content-Type': 'application/json',
                'X-Vantis-Token': self.sudo().webhook_token or '',
            },
            method='POST',
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode(errors='replace')
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors='replace')[:300] if exc.fp else ''
            _logger.warning('LinkedIn: n8n devolvió %s en %s — %s', exc.code, url, detail)
            return False, f'n8n devolvió HTTP {exc.code}: {detail or exc.reason}'
        except Exception as exc:
            _logger.warning('LinkedIn: no se pudo llamar a n8n en %s — %s', url, exc)
            return False, f'No se pudo contactar con n8n: {exc}'
        return True, raw[:2000]

    # ── Acciones de la ficha ──────────────────────────────────────────────────

    # ── Regeneración de leads tras cambiar la configuración ───────────────────

    def action_regenerar_leads_por_rol(self):
        """Recalcula la puntuación de los leads con la configuración actual.

        Las señales se puntúan UNA vez, cuando entran: tipo de señal + bonus si
        encaja con el ICP. Y la puntuación del lead es la suma acumulada de sus
        señales. Eso significa que cambiar los cargos objetivo, los sectores o
        las categorías de exclusión NO afecta a nada de lo ya capturado: los
        leads de ayer conservan la puntuación que les dio la configuración de
        ayer, y conviven con los de hoy como si fueran comparables. No lo son.

        Este botón rehace el cálculo desde el origen: repuntúa cada señal con
        los criterios de ahora, vuelve a sumar, y reevalúa las exclusiones.

        Lo que NO hace: inventar leads ni volver a llamar a LinkedIn. Solo
        recalcula lo que ya está capturado.
        """
        self.ensure_one()

        señales = self.env['marketing.linkedin.signal'].search([
            ('profile_id', '=', self.id),
            ('state', '!=', 'discarded'),
        ])
        if not señales:
            return self._notificacion(
                'Nada que regenerar',
                'Este perfil todavía no tiene señales capturadas.',
                'warning',
            )

        # 1) Repuntuar cada señal con los criterios actuales
        for señal in señales:
            encaja = self.is_icp_fit(señal.company_name, señal.job_title)
            puntos = self._score_for_signal_type(señal.signal_type)
            if encaja:
                puntos += self.score_icp_bonus
            if señal.icp_fit != encaja or señal.score != puntos:
                señal.write({'icp_fit': encaja, 'score': puntos})

        # 2) Rehacer la suma de cada lead afectado.
        #    Se suman TODAS las señales del lead, no solo las de este perfil: la
        #    puntuación es del lead, y un mismo contacto puede haber reaccionado
        #    a los perfiles de dos comerciales distintos. Quedarse solo con las
        #    de este perfil le borraría los puntos del otro.
        leads = señales.mapped('lead_id')
        antes = {l.id: (l.x_linkedin_score or 0, l.x_linkedin_is_hot) for l in leads}

        todas = self.env['marketing.linkedin.signal'].search([
            ('lead_id', 'in', leads.ids),
            ('state', '!=', 'discarded'),
        ])
        suma = {}
        for señal in todas:
            suma[señal.lead_id.id] = suma.get(señal.lead_id.id, 0) + (señal.score or 0)

        for lead in leads:
            nuevo = suma.get(lead.id, 0)
            if (lead.x_linkedin_score or 0) != nuevo:
                lead.x_linkedin_score = nuevo

        # 3) Reevaluar exclusiones: pone a 0 los que caigan en una categoría
        #    activa, salvo los que tengan la excepción manual marcada.
        leads._reevaluar_exclusion()
        leads.invalidate_recordset()

        # 4) Contar qué ha cambiado, para que el usuario sepa si el ajuste de
        #    criterios ha servido de algo o no ha movido nada.
        subieron = bajaron = nuevos_hot = perdieron_hot = 0
        for lead in leads:
            viejo_score, era_hot = antes[lead.id]
            if (lead.x_linkedin_score or 0) > viejo_score:
                subieron += 1
            elif (lead.x_linkedin_score or 0) < viejo_score:
                bajaron += 1
            if lead.x_linkedin_is_hot and not era_hot:
                nuevos_hot += 1
            elif era_hot and not lead.x_linkedin_is_hot:
                perdieron_hot += 1
        excluidos = len(leads.filtered('x_linkedin_is_excluded'))

        resumen = (
            f'{len(señales)} señales repuntuadas · {len(leads)} leads revisados.\n'
            f'Suben: {subieron} · Bajan: {bajaron} · Excluidos ahora: {excluidos}\n'
            f'Pasan a prioritarios: {nuevos_hot} · Dejan de serlo: {perdieron_hot}'
        )
        self.message_post(body=Markup(
            '<p><b>Leads regenerados según la configuración actual</b></p>'
            f'<p>{resumen}</p>'.replace('\n', '<br/>')
        ))
        if not (subieron or bajaron or nuevos_hot or perdieron_hot):
            return self._notificacion(
                'Sin cambios',
                'La configuración actual da exactamente el mismo resultado que '
                'la anterior. No se ha movido ninguna puntuación.\n\n' + resumen,
                'warning',
            )
        return self._notificacion('Leads regenerados', resumen, 'success')

    @staticmethod
    def _notificacion(titulo, mensaje, tipo='success'):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {'title': titulo, 'message': mensaje,
                       'type': tipo, 'sticky': True},
        }

    def action_view_signals(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Señales — {self.name}',
            'res_model': 'marketing.linkedin.signal',
            'view_mode': 'list,form',
            'domain': [('profile_id', '=', self.id)],
            'context': {'default_profile_id': self.id},
        }

    def action_view_posts(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Publicaciones — {self.name}',
            'res_model': 'marketing.linkedin.post',
            'view_mode': 'list,form',
            'domain': [('profile_id', '=', self.id)],
            'context': {'default_profile_id': self.id},
        }

    def action_view_diagnostics(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Diagnósticos — {self.name}',
            'res_model': 'marketing.linkedin.diagnostic',
            'view_mode': 'list,form',
            'domain': [('profile_id', '=', self.id)],
            'context': {'default_profile_id': self.id},
        }

    def action_view_leads(self):
        self.ensure_one()
        lead_ids = self.signal_ids.mapped('lead_id').ids
        return {
            'type': 'ir.actions.act_window',
            'name': f'Leads de LinkedIn — {self.name}',
            'res_model': 'crm.lead',
            'view_mode': 'list,kanban,form',
            'domain': [('id', 'in', lead_ids)],
        }

    def action_run_router(self):
        """Lanza el router de embudo para este perfil desde la ficha."""
        self.ensure_one()
        diagnostic = self.env['marketing.linkedin.diagnostic'].run_for_profile(self)
        return {
            'type': 'ir.actions.act_window',
            'name': 'Diagnóstico del embudo',
            'res_model': 'marketing.linkedin.diagnostic',
            'res_id': diagnostic.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_test_n8n(self):
        """Comprueba que Odoo alcanza el n8n configurado."""
        self.ensure_one()
        ok, message = self.call_n8n('ping', {'action': 'ping', 'source': 'odoo'})
        if not ok:
            raise UserError(
                'No se pudo contactar con n8n.\n\n'
                f'{message}\n\n'
                'Comprueba la URL en Ajustes y que el workflow con la ruta '
                '/webhook/vantis-ping esté activo en n8n.'
            )
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Conexión con n8n correcta',
                'message': f'Respuesta: {message[:200] or "(vacía)"}',
                'type': 'success',
                'sticky': False,
            },
        }

    @api.constrains('hot_threshold')
    def _check_hot_threshold(self):
        for profile in self:
            if profile.hot_threshold < 1:
                raise ValidationError(
                    'El umbral de señal caliente debe ser al menos 1: con 0 '
                    'toda señal sería caliente y el aviso perdería sentido.'
                )
