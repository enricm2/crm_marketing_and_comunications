"""Integración con Prosp: traducción de sus webhooks a señales del CRM.

Prosp es una herramienta de prospección **saliente**: la mayoría de sus
eventos describen lo que tú haces sobre el prospecto (visitar su perfil, dar
like a su publicación, enviarle un mensaje), no lo que él hace sobre ti. Esa
distinción es la razón de ser de este archivo.

Si «LinkedIn Profile Visited» se tradujera a una señal de tipo `vista_perfil`,
cada lead acumularía puntos por la actividad de tu propia automatización: la
puntuación dejaría de medir interés y pasaría a medir tu volumen de envíos,
todos los leads cruzarían el umbral y los avisos perderían todo valor. Por eso
cada evento lleva una **dirección** explícita:

- `inbound`  → el prospecto ha actuado. Se convierte en señal y puntúa.
- `outbound` → hemos actuado nosotros. Se anota en el historial del lead, sin
               puntos y sin disparar el umbral.
- `ignore`   → ruido administrativo que no aporta nada al CRM.

El mapa es un modelo editable y no una constante del código porque Prosp no
documenta las cadenas exactas de `eventType`: cuando aparezca una desconocida,
se captura y se mapea desde la interfaz, sin tocar el módulo.
"""
import logging
import re

from odoo import models, fields, api

from .linkedin_common import SIGNAL_TYPES

_logger = logging.getLogger(__name__)


# Prosp manda un envío de prueba al crear o guardar un webhook, con TODOS los
# valores de relleno: `eventType` vale literalmente "event_name" y los datos del
# lead son descripciones («first name of the lead»). Reconocerlo importa por dos
# razones: confirma al usuario que la conexión funciona en vez de dejarle un
# "evento desconocido" que parece un fallo, y evita que un contacto llamado
# "first name of the lead" acabe en el CRM.
PROSP_TEST_EVENT_CODES = {'eventname', 'test', 'testevent'}
PROSP_TEST_CONTENT_MARKER = 'this is a test triggered when adding a webhook'


def is_test_ping(payload):
    """¿Es el envío de prueba que Prosp manda al guardar un webhook?"""
    if not isinstance(payload, dict):
        return False
    if normalize_event_code(payload.get('eventType')) in PROSP_TEST_EVENT_CODES:
        return True
    data = payload.get('eventData')
    content = (data or {}).get('content') if isinstance(data, dict) else ''
    return PROSP_TEST_CONTENT_MARKER in str(content or '').lower()


def normalize_event_code(raw):
    """Reduce un `eventType` a su forma comparable.

    Prosp muestra los eventos como «LinkedIn Message Replied» pero no publica
    con qué cadena los manda: podría ser `linkedin_message_replied`,
    `LinkedInMessageReplied` o `linkedin.message.replied`. Quitando todo lo que
    no sea alfanumérico y bajando a minúsculas, las tres formas coinciden y el
    mapa funciona sin saber cuál usa.
    """
    return re.sub(r'[^a-z0-9]', '', (raw or '').lower())


class MarketingLinkedinProspEvent(models.Model):
    _name = 'marketing.linkedin.prosp.event'
    _description = 'Mapeo de eventos de Prosp'
    _order = 'direction, name'

    name = fields.Char(string='Evento', required=True)
    event_code = fields.Char(
        string='Código normalizado', required=True, index=True,
        help='El `eventType` del webhook sin mayúsculas ni separadores. Se '
             'rellena solo a partir del nombre; solo hay que tocarlo si Prosp '
             'manda una cadena que no se parece a la etiqueta de su interfaz.',
    )
    direction = fields.Selection([
        ('inbound', 'Entrante — lo hace el prospecto'),
        ('outbound', 'Saliente — lo hacemos nosotros'),
        ('ignore', 'Ignorar'),
    ], string='Dirección', required=True, default='outbound')
    signal_type = fields.Selection(
        SIGNAL_TYPES, string='Tipo de señal',
        help='Solo para eventos entrantes: en qué señal se convierte. Los '
             'salientes no puntúan nunca, por diseño.',
    )
    active = fields.Boolean(default=True)
    notes = fields.Text(string='Notas')

    _unique_code = models.Constraint(
        'UNIQUE(event_code)',
        'Ya existe un mapeo para ese código de evento.',
    )

    @api.onchange('name')
    def _onchange_name(self):
        for record in self:
            if record.name and not record.event_code:
                record.event_code = normalize_event_code(record.name)

    @api.onchange('direction')
    def _onchange_direction(self):
        for record in self:
            if record.direction != 'inbound':
                record.signal_type = False

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('event_code') and vals.get('name'):
                vals['event_code'] = normalize_event_code(vals['name'])
            else:
                vals['event_code'] = normalize_event_code(vals.get('event_code'))
        mapeos = super().create(vals_list)
        mapeos._reprocesar_capturas_pendientes()
        return mapeos

    def _reprocesar_capturas_pendientes(self):
        """Procesa las capturas que estaban esperando a este mapeo.

        Sin esto, crear la regla no rescataba nada: las capturas que llegaron
        antes seguían en «nueva» hasta que alguien las abriera una a una y
        pulsara «Reprocesar». Que es justo lo que nadie hace.
        """
        Captura = self.env['marketing.linkedin.capture']
        for mapeo in self:
            pendientes = Captura.search([('state', '=', 'new')]).filtered(
                lambda c, m=mapeo: normalize_event_code(c.event_name) == m.event_code
            )
            for captura in pendientes:
                try:
                    captura.action_reprocess()
                except Exception as exc:  # noqa: BLE001
                    _logger.warning('Captura %s no se pudo reprocesar tras crear '
                                    'el mapeo %s: %s', captura.id, mapeo.name, exc)

    def write(self, vals):
        if 'event_code' in vals:
            vals['event_code'] = normalize_event_code(vals['event_code'])
        return super().write(vals)

    @api.model
    def find_for_event(self, event_type):
        """Mapeo de un `eventType` entrante, o recordset vacío si no se conoce."""
        code = normalize_event_code(event_type)
        if not code:
            return self.browse()
        return self.search([('event_code', '=', code)], limit=1)


class MarketingLinkedinProspPayload(models.AbstractModel):
    """Traducción del payload de Prosp a las filas que entiende `ingest()`.

    Va en un modelo aparte y no en el controlador para que la lógica sea
    probable sin levantar un servidor HTTP y para que el día que Prosp ofrezca
    también consulta por API se pueda reutilizar tal cual.
    """
    _name = 'marketing.linkedin.prosp.payload'
    _description = 'Traductor de payloads de Prosp'

    @api.model
    def extract(self, payload):
        """Saca de un payload de Prosp lo que necesitamos, sin interpretarlo.

        Formato real (confirmado con un envío de Prosp)::

            {"eventType": "...",
             "eventData": {"campaignId", "campaignName", "workspaceId",
                           "lead", "sender", "content", "timestamp",
                           "profileInfo": {"firstName", "lastName",
                                           "linkedinUrl", "company",
                                           "companyUrl", "websiteUrl", "bio",
                                           "headline", "jobTitle",
                                           "companyOverview"}}}
        """
        payload = payload if isinstance(payload, dict) else {}
        data = payload.get('eventData') or {}
        if not isinstance(data, dict):
            data = {}
        profile_info = data.get('profileInfo') or {}
        if not isinstance(profile_info, dict):
            profile_info = {}

        name = ' '.join(
            str(profile_info.get(key) or '').strip()
            for key in ('firstName', 'lastName')
        ).strip()

        return {
            'event_type': payload.get('eventType') or '',
            'contact_name': name,
            'linkedin_url': (profile_info.get('linkedinUrl')
                             or data.get('lead') or ''),
            'company_name': profile_info.get('company') or '',
            # `jobTitle` es el cargo limpio; `headline` es el titular libre del
            # perfil. Se prefiere el primero y se cae al segundo, que es lo que
            # LinkedIn muestra cuando el usuario no ha rellenado el cargo.
            'job_title': (profile_info.get('jobTitle')
                          or profile_info.get('headline') or ''),
            'website': profile_info.get('websiteUrl') or '',
            # El esquema que publica Prosp no los menciona, pero los manda: el
            # email es oro para el CRM y mejora el cruce con leads existentes.
            'email': profile_info.get('email') or '',
            'phone': profile_info.get('phoneNumber') or '',
            'linkedin_id': profile_info.get('linkedinId') or '',
            'content': data.get('content') or '',
            'timestamp': data.get('timestamp'),
            'sender': data.get('sender') or '',
            'campaign_id': data.get('campaignId') or '',
            'campaign_name': data.get('campaignName') or '',
            'workspace_id': data.get('workspaceId') or '',
        }

    # Tipos de señal donde `content` es de verdad algo que escribió la persona.
    # En los demás Prosp manda una descripción enlatada del evento («accepted a
    # connection request»), y guardarla como comentario haría creer que el
    # contacto escribió eso.
    CONTENT_AS_COMMENT_TYPES = ('comment', 'message', 'mention')

    @api.model
    def to_signal_row(self, extracted, signal_type):
        """Convierte lo extraído en una fila para `marketing.linkedin.signal.ingest`."""
        row = {
            'contact_name': extracted['contact_name'],
            'linkedin_url': extracted['linkedin_url'],
            'empresa': extracted['company_name'],
            'cargo': extracted['job_title'],
            'tipo_senal': signal_type,
        }
        if extracted.get('email'):
            row['email'] = extracted['email']
        if extracted.get('phone'):
            row['phone'] = extracted['phone']
        if extracted.get('timestamp'):
            # Prosp manda epoch en milisegundos; `_parse_datetime` ya lo detecta.
            row['fecha_senal'] = extracted['timestamp']
        if extracted.get('content') and signal_type in self.CONTENT_AS_COMMENT_TYPES:
            row['comentario'] = extracted['content']
        return row

    # ── Procesado ─────────────────────────────────────────────────────────────

    @api.model
    def process(self, profile, payload, capture=None):
        """Trata un payload de Prosp y devuelve un resumen de lo hecho.

        Vive aquí y no en el controlador para que el botón "Reprocesar" de una
        captura pueda ejecutar exactamente el mismo camino que la llamada HTTP
        original: un evento que llegó antes de estar mapeado se recupera sin
        pedirle a Prosp que lo repita, cosa que Prosp no permite.

        `capture` es la captura de origen cuando se reprocesa; sirve para no
        volver a guardar una copia de algo que ya está guardado.
        """
        extracted = self.extract(payload)
        event_type = extracted['event_type']

        if is_test_ping(payload):
            return {
                'handled': 'test_ping',
                'event': event_type,
                'message': 'Envío de prueba de Prosp recibido correctamente. '
                           'La conexión funciona. No se ha creado nada en el CRM '
                           'porque no trae datos reales.',
            }

        if not event_type:
            return {'handled': 'no_event_type',
                    'message': 'El payload no trae eventType.'}

        profile = self._route_by_sender(profile, extracted['sender'])
        mapping = self.env['marketing.linkedin.prosp.event'].find_for_event(event_type)
        if not mapping:
            return {'handled': 'unmapped', 'event': event_type,
                    'message': 'Evento sin mapear.'}

        if mapping.direction == 'ignore':
            return {'handled': 'ignored', 'event': event_type,
                    'mapping_id': mapping.id}

        if mapping.direction == 'outbound':
            lead_id = self.env['marketing.linkedin.signal']._log_prosp_outbound(
                profile, extracted, mapping,
            )
            # Intentar actualizar estadísticas de la campaña de Odoo
            self._update_campaign_lead_stats(extracted, event_type, lead_id, 'outbound')
            return {
                'handled': 'outbound', 'event': event_type,
                'mapping_id': mapping.id, 'lead_id': lead_id or False,
                'message': 'Acción saliente anotada en el lead. No puntúa.'
            }

        row = self.to_signal_row(extracted, mapping.signal_type)
        summary = self.env['marketing.linkedin.signal'].ingest(profile, [row])
        
        # Intentar actualizar estadísticas de la campaña de Odoo (respuestas)
        lead_id = False
        if summary.get('signal_ids'):
            signal = self.env['marketing.linkedin.signal'].browse(summary['signal_ids'][0])
            lead_id = signal.lead_id.id if signal.lead_id else False
        self._update_campaign_lead_stats(extracted, event_type, lead_id, 'inbound')

        return {
            'handled': 'inbound',
            'event': event_type,
            'mapping_id': mapping.id,
            'signal_type': mapping.signal_type,
            'created': summary['created'],
            'duplicated': summary['duplicated'],
            'ignored': summary['ignored'],
            'errors': summary['errors'],
            'error_details': summary['error_details'],
            'signal_ids': summary['signal_ids'],
        }

    @api.model
    def _update_campaign_lead_stats(self, extracted, event_type, lead_id, direction):
        """Busca si el lead pertenece a una campaña de marketing de Odoo con LinkedIn
        y actualiza las fechas de envío (outbound) o de respuesta (inbound) en tiempo real.
        """
        if not lead_id:
            lead_url = extracted.get('linkedin_url')
            if lead_url:
                lead = self.env['marketing.linkedin.signal']._find_lead_by_linkedin_url(lead_url)
                lead_id = lead.id if lead else False
        
        if not lead_id:
            return False

        # Buscar campaña en Odoo que coincida con el ID o Nombre de la campaña en Prosp
        prosp_campaign_id = extracted.get('campaign_id')
        prosp_campaign_name = extracted.get('campaign_name')
        if not prosp_campaign_id and not prosp_campaign_name:
            return False
        
        domain = [('use_linkedin', '=', True)]
        if prosp_campaign_id and prosp_campaign_name:
            domain += ['|', ('prosp_campaign_id', '=', prosp_campaign_id), ('name', '=', prosp_campaign_name)]
        elif prosp_campaign_id:
            domain += [('prosp_campaign_id', '=', prosp_campaign_id)]
        else:
            domain += [('name', '=', prosp_campaign_name)]
            
        campaign = self.env['marketing.campaign'].search(domain, limit=1)
        if not campaign:
            return False

        # Buscar la línea de la campaña
        campaign_lead = self.env['marketing.campaign.lead'].search([
            ('campaign_id', '=', campaign.id),
            ('lead_id', '=', lead_id)
        ], limit=1)
        
        if not campaign_lead:
            return False

        vals = {}
        # Mapear los tipos de evento de Prosp a las estadísticas de la campaña
        # Outbound: Mensajes enviados
        if direction == 'outbound':
            if 'message' in event_type.lower() or 'inmail' in event_type.lower():
                if not campaign_lead.sent_at:
                    vals['sent_at'] = fields.Datetime.now()
        # Inbound: Respuestas recibidas
        elif direction == 'inbound':
            if 'reply' in event_type.lower() or 'replied' in event_type.lower() or 'message' in event_type.lower():
                if not campaign_lead.replied_at:
                    vals['replied_at'] = fields.Datetime.now()
                    # Si no tenía clasificación, la marcamos temporalmente para revisión
                    if campaign_lead.ai_classification == 'no_response':
                        vals['ai_classification'] = 'no_classification'

        if vals:
            campaign_lead.write(vals)
            # Disparar recómputo de estadísticas de la campaña
            campaign._compute_stats()
            return True
            
        return False

    @api.model
    def _route_by_sender(self, profile, sender_url):
        """Perfil dueño de la cuenta de LinkedIn que ejecutó la acción.

        En un espacio de Prosp con varios comerciales, `sender` dice desde qué
        cuenta salió. Si ese perfil existe en Odoo, la señal se le atribuye a él
        aunque el token de la URL sea compartido.
        """
        if not sender_url:
            return profile
        from .linkedin_common import normalize_linkedin_url
        target = normalize_linkedin_url(sender_url)
        if not target or target == normalize_linkedin_url(profile.linkedin_url):
            return profile
        candidates = self.env['marketing.linkedin.profile'].sudo().search(
            [('linkedin_url', '!=', False)]
        )
        match = candidates.filtered(
            lambda p: normalize_linkedin_url(p.linkedin_url) == target
        )[:1]
        return match or profile


class MarketingProspCampaign(models.Model):
    _name = 'marketing.prosp.campaign'
    _description = 'Campaña de Prosp'
    _order = 'name'

    name = fields.Char(string='Campaña', required=True)
    prosp_id = fields.Char(string='ID de Prosp', required=True)
    profile_id = fields.Many2one(
        'marketing.linkedin.profile',
        string='Perfil de LinkedIn',
        required=True,
        ondelete='cascade',
    )


class MarketingProspList(models.Model):
    _name = 'marketing.prosp.list'
    _description = 'Lista de Prosp'
    _order = 'name'

    name = fields.Char(string='Lista', required=True)
    prosp_id = fields.Char(string='ID de Prosp', required=True)
    profile_id = fields.Many2one(
        'marketing.linkedin.profile',
        string='Perfil de LinkedIn',
        required=True,
        ondelete='cascade',
    )
