"""Captura de webhooks entrantes sin interpretarlos.

Sirve para descubrir el formato real de un servicio externo cuya documentación
no publica los payloads —el caso de Prosp— antes de escribir el mapeo. Guarda
lo que llegue tal cual: cuerpo, cabeceras y método. Con un evento real delante
se escribe la traducción a `marketing.linkedin.signal` en un rato, en vez de
adivinar nombres de campos y descubrir el error semanas después.

Es una herramienta de diagnóstico, no parte del camino de datos: nada de lo que
entra aquí llega al CRM. Cuando el mapeo esté escrito, este endpoint deja de
usarse (o se queda como red de seguridad para eventos desconocidos).
"""
import json
import logging

from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Claves donde los servicios habituales meten el nombre del evento. Se prueban
# en orden; con Prosp aún no sabemos cuál usa, que es justo lo que queremos ver.
EVENT_NAME_KEYS = (
    'event', 'event_type', 'eventType', 'event_name', 'eventName',
    'type', 'action', 'topic', 'name', 'trigger',
)

# Tope del cuerpo guardado. Un payload legítimo de webhook no llega ni de lejos;
# el tope está para que un envío erróneo no llene la tabla.
MAX_PAYLOAD_CHARS = 100_000

CAPTURE_RETENTION_DAYS = 30


class MarketingLinkedinCapture(models.Model):
    _name = 'marketing.linkedin.capture'
    _description = 'Webhook entrante capturado (diagnóstico)'
    _order = 'received_at desc, id desc'
    _rec_name = 'display_name'

    profile_id = fields.Many2one(
        'marketing.linkedin.profile', string='Perfil',
        required=True, index=True, ondelete='cascade',
    )
    user_id = fields.Many2one(
        related='profile_id.user_id', string='Propietario', store=True, readonly=True,
    )
    company_id = fields.Many2one(
        related='profile_id.company_id', store=True, readonly=True,
    )
    received_at = fields.Datetime(
        string='Recibido', default=fields.Datetime.now, readonly=True, index=True,
    )
    source = fields.Char(
        string='Origen', readonly=True,
        help='De dónde dice venir la llamada. Se toma del último tramo de la '
             'URL cuando se indica, para poder capturar varios servicios a la vez.',
    )
    event_name = fields.Char(
        string='Evento', readonly=True, index=True,
        help='Nombre del evento deducido del payload. Si sale vacío, el '
             'servicio lo manda con una clave que todavía no reconocemos: '
             'se ve en el cuerpo completo.',
    )
    http_method = fields.Char(string='Método', readonly=True)
    query_string = fields.Char(string='Parámetros de URL', readonly=True)
    remote_addr = fields.Char(string='IP de origen', readonly=True)
    content_type = fields.Char(string='Content-Type', readonly=True)
    headers = fields.Text(
        string='Cabeceras', readonly=True,
        help='Todas las cabeceras recibidas. Aquí se ve si el servicio manda '
             'firma de verificación, y con qué nombre.',
    )
    payload = fields.Text(
        string='Cuerpo recibido', readonly=True,
        help='El JSON tal y como llegó, formateado. Es lo que hace falta para '
             'escribir el mapeo.',
    )
    payload_keys = fields.Char(
        string='Claves de primer nivel', readonly=True,
        help='Atajo para ver de un vistazo qué campos trae, sin leer el JSON.',
    )
    is_test = fields.Boolean(
        string='Envío de prueba', readonly=True,
        help='Prosp manda un payload de prueba con valores de relleno al crear '
             'o guardar un webhook. Se marca para no confundirlo con un evento '
             'real que no se ha sabido mapear.',
    )
    prosp_event_id = fields.Many2one(
        'marketing.linkedin.prosp.event', string='Mapeo aplicado',
        ondelete='set null', readonly=True,
        help='Regla con la que se reprocesó esta captura.',
    )
    reprocess_result = fields.Char(string='Resultado del reprocesado', readonly=True)

    state = fields.Selection([
        ('new', 'Sin revisar'),
        ('mapped', 'Mapeada'),
        ('ignored', 'No aplica'),
    ], string='Estado', default='new', required=True, index=True)
    notes = fields.Text(string='Notas')
    display_name = fields.Char(compute='_compute_display_name', store=True)

    @api.depends('event_name', 'source', 'received_at')
    def _compute_display_name(self):
        for record in self:
            label = record.event_name or '(evento sin identificar)'
            source = f'{record.source} · ' if record.source else ''
            stamp = fields.Datetime.to_string(record.received_at)[:16] \
                if record.received_at else ''
            record.display_name = f'{source}{label} · {stamp}'

    # ── Registro ──────────────────────────────────────────────────────────────

    @api.model
    def record_request(self, profile, source, raw_body, headers, method,
                       query_string='', remote_addr='', is_test=False):
        """Guarda una petición entrante sin interpretarla.

        Nunca levanta excepción: si el capturador fallara, el servicio externo
        vería un error y podría desactivar el webhook por su cuenta, que es
        justo lo contrario de lo que se busca aquí.
        """
        try:
            body = (raw_body or '')[:MAX_PAYLOAD_CHARS]
            parsed, pretty, keys = None, body, ''
            try:
                parsed = json.loads(body) if body.strip() else None
            except ValueError:
                parsed = None
            if isinstance(parsed, (dict, list)):
                pretty = json.dumps(parsed, ensure_ascii=False, indent=2)[:MAX_PAYLOAD_CHARS]
            if isinstance(parsed, dict):
                keys = ', '.join(list(parsed.keys())[:40])
            elif isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
                keys = '[lista] ' + ', '.join(list(parsed[0].keys())[:40])

            return self.sudo().create({
                'profile_id': profile.id,
                'source': source or '',
                'event_name': self._guess_event_name(parsed),
                'http_method': method or '',
                'query_string': (query_string or '')[:500],
                'remote_addr': remote_addr or '',
                'content_type': (headers or {}).get('Content-Type', ''),
                'headers': json.dumps(dict(headers or {}), ensure_ascii=False, indent=2)[:20000],
                'payload': pretty,
                'payload_keys': keys[:500],
                'is_test': is_test,
                # Una prueba no está "sin revisar": no hay nada que mapear.
                'state': 'ignored' if is_test else 'new',
            })
        except Exception:
            _logger.exception('LinkedIn: no se pudo guardar el webhook capturado')
            return self.browse()

    @api.model
    def _guess_event_name(self, parsed):
        """Busca el nombre del evento en las claves habituales, sin inventar.

        Mira también un nivel dentro de `data` / `payload`, que es donde
        bastantes servicios anidan el contenido real.
        """
        if not isinstance(parsed, dict):
            return ''
        for key in EVENT_NAME_KEYS:
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:120]
        for wrapper in ('data', 'payload', 'body', 'object'):
            inner = parsed.get(wrapper)
            if isinstance(inner, dict):
                for key in EVENT_NAME_KEYS:
                    value = inner.get(key)
                    if isinstance(value, str) and value.strip():
                        return value.strip()[:120]
        return ''

    # ── Mapear y reprocesar ───────────────────────────────────────────────────

    def action_create_mapping(self):
        """Deja la captura resuelta: mapea si hace falta, y procesa siempre.

        El botón decía «Mapear este evento» y abría un formulario de mapeo aunque
        el mapeo YA existiera. El usuario lo cerraba y la captura seguía en
        «nueva»: pulsar el botón no hacía nada visible. Ahora:

          · Si es el envío de prueba de Prosp → se marca y se dice que no hay
            nada que mapear (esos payloads traen `event_name` sin sustituir).
          · Si el mapeo ya existe → no se pregunta nada: se reprocesa la captura
            y TODAS las demás pendientes del mismo evento, que es lo que el
            usuario quería. Llegaron antes de que existiera la regla.
          · Solo si el evento es realmente desconocido se abre el formulario.
        """
        self.ensure_one()

        # Payload de prueba: Prosp manda la plantilla sin rellenar al guardar el
        # webhook. No es un evento que se haya dejado de mapear, es relleno.
        if self._es_payload_de_prueba():
            self.write({'is_test': True, 'state': 'ignored',
                        'reprocess_result': 'Envío de prueba de Prosp: sin datos reales.'})
            return self._notificacion(
                'No hay nada que mapear',
                'Es el envío de prueba que Prosp manda al guardar un webhook: '
                'trae «event_name» sin sustituir y sin datos. Se ha marcado como '
                'prueba para que deje de aparecer entre los eventos sin mapear.',
                'warning')

        if not self.event_name:
            raise UserError(
                'Esta captura no trae nombre de evento, así que no hay nada que '
                'mapear. Míralo en el cuerpo recibido: puede que el servicio use '
                'una clave que todavía no reconocemos.'
            )

        Event = self.env['marketing.linkedin.prosp.event']
        existing = Event.find_for_event(self.event_name)
        if existing:
            return self._reprocesar_pendientes_del_evento(existing)

        from .linkedin_prosp import normalize_event_code
        return {
            'type': 'ir.actions.act_window',
            'name': f'Mapear «{self.event_name}»',
            'res_model': 'marketing.linkedin.prosp.event',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_name': self.event_name,
                'default_event_code': normalize_event_code(self.event_name),
                'default_direction': 'inbound',
                # Para volver a esta captura y procesarla al guardar el mapeo.
                'capture_origen_id': self.id,
            },
        }

    def _es_payload_de_prueba(self):
        """¿El cuerpo guardado es el ping de prueba de Prosp?

        Se comprueba sobre el payload, no sobre el campo `is_test`: las capturas
        antiguas se guardaron antes de que se supiera reconocer este envío, y
        tienen la marca a False aunque sean prueba.
        """
        self.ensure_one()
        from .linkedin_prosp import is_test_ping
        try:
            return is_test_ping(json.loads(self.payload or '{}'))
        except ValueError:
            return False

    def _reprocesar_pendientes_del_evento(self, mapping):
        """Reprocesa esta captura y las demás pendientes del mismo evento."""
        self.ensure_one()
        hermanas = self.search([
            ('id', '!=', self.id),
            ('state', '=', 'new'),
            ('event_name', '=', self.event_name),
        ])
        resultado = self.action_reprocess()
        detalle = resultado['params']['message']

        procesadas = 0
        for captura in hermanas:
            try:
                captura.action_reprocess()
                procesadas += 1
            except Exception as exc:  # noqa: BLE001
                _logger.warning('Captura %s no se pudo reprocesar: %s', captura.id, exc)

        mensaje = f'«{mapping.name}» ya estaba mapeado, así que se ha procesado.\n{detalle}'
        if hermanas:
            mensaje += (f'\n\nAdemás se han reprocesado {procesadas} de {len(hermanas)} '
                        f'capturas pendientes del mismo evento.')
        return self._notificacion('Captura procesada', mensaje,
                                  resultado['params'].get('type', 'success'))

    @staticmethod
    def _notificacion(titulo, mensaje, tipo='success'):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {'title': titulo, 'message': mensaje,
                       'type': tipo, 'sticky': True},
        }

    def action_reprocess(self):
        """Vuelve a pasar el payload guardado por el mismo camino que la llamada.

        Es lo que permite no perder el primer evento de un tipo nuevo: llegó
        antes de existir su regla, y aquí se recupera.
        """
        self.ensure_one()
        if not self.payload:
            raise UserError('Esta captura no tiene cuerpo que reprocesar.')
        try:
            payload = json.loads(self.payload)
        except ValueError as exc:
            raise UserError(
                f'El cuerpo guardado no es JSON válido, no se puede reprocesar: {exc}'
            ) from exc

        result = self.env['marketing.linkedin.prosp.payload'].process(
            self.profile_id, payload, capture=self,
        )
        handled = result.get('handled')
        summary = {
            'inbound': lambda: (
                f'Señal creada ({result.get("signal_type")}).' if result.get('created')
                else 'Ya estaba registrada: no se ha duplicado.'
            ),
            'outbound': lambda: (
                'Anotada en el lead como acción saliente.' if result.get('lead_id')
                else 'Acción saliente sin lead en el CRM: no se ha creado nada.'
            ),
            'ignored': lambda: 'El mapeo dice que este evento se ignora.',
            'unmapped': lambda: 'Sigue sin mapeo: crea la regla y vuelve a intentarlo.',
            'test_ping': lambda: 'Es el envío de prueba de Prosp: no hay datos reales.',
            'no_event_type': lambda: 'El payload no trae eventType.',
        }.get(handled, lambda: f'Resultado: {handled}')()

        vals = {'reprocess_result': summary[:250]}
        if result.get('mapping_id'):
            vals['prosp_event_id'] = result['mapping_id']
        if handled in ('inbound', 'outbound', 'ignored'):
            vals['state'] = 'mapped'
        self.write(vals)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Captura reprocesada',
                'message': summary,
                'type': 'success' if handled in ('inbound', 'outbound') else 'warning',
                'sticky': False,
            },
        }

    # ── Acciones ──────────────────────────────────────────────────────────────

    def action_mark_mapped(self):
        return self.write({'state': 'mapped'})

    def action_mark_ignored(self):
        return self.write({'state': 'ignored'})

    # ── Limpieza ──────────────────────────────────────────────────────────────

    @api.autovacuum
    def _gc_captures(self):
        """Borra capturas antiguas.

        Es un diagnóstico temporal: sin caducidad, un webhook activo llenaría
        la tabla indefinidamente con payloads que ya nadie va a leer.
        """
        from datetime import timedelta
        limit = fields.Datetime.now() - timedelta(days=CAPTURE_RETENTION_DAYS)
        old = self.search([('received_at', '<', limit), ('state', '!=', 'mapped')])
        count = len(old)
        if count:
            old.unlink()
            _logger.info('LinkedIn: %s capturas de webhook caducadas eliminadas.', count)
        return count
