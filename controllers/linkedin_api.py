"""API HTTP del flujo LinkedIn Growth — la superficie que consume n8n.

Todo lo que n8n necesita escribir o leer en Odoo pasa por aquí. Es deliberado
que no reutilice el endpoint JSON-2 genérico de Odoo: ese obliga a n8n a
conocer nombres de modelos y campos, y cualquier renombrado interno rompería
el flujo en silencio. Estos endpoints son un contrato estable — reciben lo que
LinkedIn exporta y devuelven un resumen de lo que ha pasado.

Autenticación: cabecera `X-Vantis-Token` con el token del perfil. El token
identifica además de autenticar, así que n8n no manda ningún identificador de
usuario y no puede escribir en el perfil de otro por error.
"""
import json
import logging

from odoo import http
from odoo.http import request, Response

_logger = logging.getLogger(__name__)

_P = 'crm_marketing_and_comunications.'

TOKEN_HEADER = 'X-Vantis-Token'


class LinkedinApiController(http.Controller):

    # ── Utilidades ────────────────────────────────────────────────────────────

    @staticmethod
    def _json_response(payload, status=200):
        return Response(
            json.dumps(payload, ensure_ascii=False, default=str),
            status=status,
            content_type='application/json; charset=utf-8',
        )

    @staticmethod
    def _get_token():
        headers = request.httprequest.headers
        token = headers.get(TOKEN_HEADER) or headers.get(TOKEN_HEADER.lower())
        if token:
            return token.strip()
        # Fallback a Authorization: Bearer, que es lo que algunos nodos de n8n
        # ponen por defecto al elegir autenticación genérica.
        authorization = headers.get('Authorization') or ''
        if authorization.lower().startswith('bearer '):
            return authorization[7:].strip()
        return ''

    @staticmethod
    def _get_body():
        """Lee el cuerpo de la petición como dict, venga como venga.

        n8n manda JSON con `Content-Type: application/json` en el caso normal,
        pero también form-urlencoded si el nodo está mal configurado. Aceptar
        los dos ahorra una hora de depuración a quien monte el flujo.
        """
        raw = request.httprequest.get_data(as_text=True) or ''
        if raw.strip():
            try:
                data = json.loads(raw)
            except ValueError:
                data = None
            if isinstance(data, dict):
                return data
            if isinstance(data, list):
                return {'rows': data}
        if request.httprequest.form:
            return dict(request.httprequest.form)
        return {}

    def _authenticate(self):
        """Devuelve (perfil, error). El perfil viene ya en sudo."""
        token = self._get_token()
        if not token:
            return None, self._json_response({
                'ok': False,
                'error': 'missing_token',
                'message': f'Falta la cabecera {TOKEN_HEADER}.',
            }, status=401)

        profile = request.env['marketing.linkedin.profile'].sudo()._get_profile_for_token(token)
        if not profile:
            _logger.warning('LinkedIn API: token no reconocido desde %s',
                            request.httprequest.remote_addr)
            return None, self._json_response({
                'ok': False,
                'error': 'invalid_token',
                'message': 'Token no reconocido. Cópialo de la ficha del perfil '
                           'de LinkedIn en Odoo.',
            }, status=403)

        if not profile.active:
            return None, self._json_response({
                'ok': False,
                'error': 'inactive_profile',
                'message': f'El perfil «{profile.name}» está archivado en Odoo.',
            }, status=403)
        return profile, None

    # ── Comprobación de conexión ──────────────────────────────────────────────

    @http.route('/vantis/linkedin/ping', type='http', auth='public',
                methods=['GET', 'POST'], csrf=False, save_session=False)
    def ping(self, **kwargs):
        """Comprueba token y conectividad. Es lo primero que debe probar n8n."""
        profile, error = self._authenticate()
        if error:
            return error
        return self._json_response({
            'ok': True,
            'profile': profile.name,
            'profile_id': profile.id,
            'user': profile.user_id.name,
            'ai_mode': profile.ai_mode,
            'hot_threshold': profile.hot_threshold,
            'odoo_version': '19.0',
        })

    # ── Captura de webhooks para descubrir formatos ───────────────────────────

    @http.route([
        '/vantis/linkedin/capture/<string:token>',
        '/vantis/linkedin/capture/<string:token>/<string:source>',
    ], type='http', auth='public',
        methods=['POST', 'GET', 'PUT'], csrf=False, save_session=False)
    def capture(self, token, source='', **kwargs):
        """Guarda una llamada entrante tal cual, sin interpretarla.

        El token va **en la URL** y no en una cabecera a propósito: servicios
        como Prosp solo dejan configurar la URL de callback, sin cabeceras
        propias. Exigir `X-Vantis-Token` aquí haría el endpoint inservible
        para justo el caso que tiene que resolver.

        Responde 200 siempre que el token sea válido, incluso si el cuerpo es
        ilegible: muchos servicios desactivan un webhook solo cuando acumula
        respuestas de error, y aquí la petición rara es precisamente lo que
        interesa conservar.
        """
        profile = request.env['marketing.linkedin.profile'].sudo()._get_profile_for_token(token)
        if not profile:
            _logger.warning('LinkedIn captura: token no reconocido desde %s',
                            request.httprequest.remote_addr)
            return self._json_response({
                'ok': False, 'error': 'invalid_token',
            }, status=403)

        httprequest = request.httprequest
        capture = request.env['marketing.linkedin.capture'].sudo().record_request(
            profile,
            source=source or kwargs.get('source') or '',
            raw_body=httprequest.get_data(as_text=True) or '',
            headers=dict(httprequest.headers),
            method=httprequest.method,
            query_string=httprequest.query_string.decode(errors='replace'),
            remote_addr=httprequest.remote_addr or '',
        )
        _logger.info(
            'LinkedIn captura: perfil %s, origen "%s", evento "%s" (captura %s).',
            profile.name, source or '—',
            capture.event_name or '(sin identificar)', capture.id or '—',
        )
        return self._json_response({'ok': True, 'captured': capture.id or False})

    # ── Prosp ─────────────────────────────────────────────────────────────────

    @http.route('/vantis/linkedin/prosp/<string:token>', type='http', auth='public',
                methods=['POST', 'GET'], csrf=False, save_session=False)
    @http.route('/uniasser/linkedin/prosp/<string:token>', type='http', auth='public',
                methods=['POST', 'GET'], csrf=False, save_session=False)
    def prosp_webhook(self, token, **kwargs):
        """Recibe un webhook de Prosp y lo trata según la dirección del evento.

        El token va en la URL porque el formulario de Prosp solo permite
        configurar la dirección de callback, sin cabeceras propias.

        Devuelve 200 salvo con token inválido: Prosp desactiva los webhooks que
        acumulan errores, y un evento que todavía no sepamos mapear no es un
        fallo — se guarda para mapearlo desde Odoo y reprocesarlo.

        La lógica vive en `marketing.linkedin.prosp.payload.process` para que
        el botón "Reprocesar" de una captura siga exactamente el mismo camino.
        """
        profile = request.env['marketing.linkedin.profile'].sudo()._get_profile_for_token(token)
        if not profile:
            _logger.warning('Prosp: token no reconocido desde %s',
                            request.httprequest.remote_addr)
            return self._json_response({'ok': False, 'error': 'invalid_token'}, status=403)

        body = self._get_body()
        result = request.env['marketing.linkedin.prosp.payload'].sudo().process(profile, body)

        # Se guarda copia solo de lo que no se ha podido procesar: es lo que hay
        # que mirar después. Guardar también lo procesado duplicaría el payload,
        # que ya queda en `raw_payload` de la propia señal.
        if result['handled'] in ('unmapped', 'no_event_type', 'test_ping'):
            capture = self._capture_raw(
                profile, 'prosp-webhook',
                is_test=result['handled'] == 'test_ping',
            )
            result['captured'] = capture.id if capture else False
            _logger.info('Prosp: %s ("%s") — captura %s.', result['handled'],
                         result.get('event') or '—', result['captured'] or '—')
        else:
            _logger.info('Prosp: evento "%s" tratado como %s.',
                         result.get('event'), result['handled'])

        result['ok'] = not result.get('errors')
        return self._json_response(result)

    def _capture_raw(self, profile, source, is_test=False):
        """Guarda la petición en Webhooks capturados para poder mapearla luego."""
        httprequest = request.httprequest
        return request.env['marketing.linkedin.capture'].sudo().record_request(
            profile,
            source=source,
            raw_body=httprequest.get_data(as_text=True) or '',
            headers=dict(httprequest.headers),
            method=httprequest.method,
            query_string=httprequest.query_string.decode(errors='replace'),
            remote_addr=httprequest.remote_addr or '',
            is_test=is_test,
        )

    # ── Minero de red: ingesta de señales ─────────────────────────────────────

    @http.route('/vantis/linkedin/signals', type='http', auth='public',
                methods=['POST'], csrf=False, save_session=False)
    def ingest_signals(self, **kwargs):
        """Recibe las señales de LinkedIn de una exportación o de la API.

        Cuerpo esperado::

            {
              "source": "n8n_csv",
              "source_file": "comentarios-2026-08.csv",
              "rows": [
                {"nombre": "...", "linkedin_url": "...", "empresa": "...",
                 "cargo": "...", "tipo_senal": "comentario",
                 "fecha_senal": "2026-08-20T10:15:00Z", "post_id": "urn:li:activity:123",
                 "comentario": "..."}
              ]
            }

        Devuelve el recuento de lo procesado y el detalle de las filas que
        fallaron, para que n8n pueda avisar por email sin adivinar nada.
        """
        profile, error = self._authenticate()
        if error:
            return error

        body = self._get_body()
        rows = body.get('rows') or body.get('signals') or body.get('data') or []
        if isinstance(rows, dict):
            rows = [rows]
        if not isinstance(rows, list):
            return self._json_response({
                'ok': False,
                'error': 'bad_payload',
                'message': 'El campo "rows" debe ser una lista de señales.',
            }, status=400)
        if not rows:
            return self._json_response({
                'ok': True, 'message': 'Sin filas que procesar.',
                'received': 0, 'created': 0, 'duplicated': 0, 'ignored': 0,
                'errors': 0,
            })

        source = body.get('source') or 'n8n_csv'
        valid_sources = {'n8n_csv', 'n8n_api', 'manual', 'other'}
        batch = request.env['marketing.linkedin.batch'].sudo().create({
            'profile_id': profile.id,
            'source': source if source in valid_sources else 'other',
            'source_file': body.get('source_file') or body.get('file_name') or False,
        })

        summary = request.env['marketing.linkedin.signal'].sudo().ingest(
            profile, rows, batch=batch,
        )
        batch.apply_summary(summary)

        _logger.info(
            'LinkedIn API: perfil %s — %s filas, %s nuevas, %s duplicadas, '
            '%s ignoradas, %s errores.',
            profile.name, summary['received'], summary['created'],
            summary['duplicated'], summary['ignored'], summary['errors'],
        )
        return self._json_response({
            'ok': True,
            'batch_id': batch.id,
            'batch_reference': batch.reference,
            'received': summary['received'],
            'created': summary['created'],
            'duplicated': summary['duplicated'],
            'ignored': summary['ignored'],
            'errors': summary['errors'],
            'hot_signals': summary['hot'],
            'leads_created': summary['leads_created'],
            'leads_updated': summary['leads_updated'],
            'error_details': summary['error_details'],
        })

    # ── Publicaciones y métricas ──────────────────────────────────────────────

    @http.route('/vantis/linkedin/posts', type='http', auth='public',
                methods=['POST'], csrf=False, save_session=False)
    def upsert_posts(self, **kwargs):
        """Crea o actualiza publicaciones con sus métricas.

        Cuerpo esperado: ``{"posts": [{...}]}``. La clave de identidad es
        ``external_id`` (el URN de LinkedIn): mandar el mismo dos veces
        actualiza las métricas en vez de duplicar la publicación.
        """
        profile, error = self._authenticate()
        if error:
            return error

        body = self._get_body()
        posts = body.get('posts') or body.get('rows') or body.get('data') or []
        if isinstance(posts, dict):
            posts = [posts]
        if not isinstance(posts, list):
            return self._json_response({
                'ok': False, 'error': 'bad_payload',
                'message': 'El campo "posts" debe ser una lista.',
            }, status=400)

        Post = request.env['marketing.linkedin.post'].sudo()
        results, errors = [], []
        for index, payload in enumerate(posts):
            try:
                with request.env.cr.savepoint():
                    post = Post.upsert_from_payload(profile, payload)
                results.append({'index': index, 'post_id': post.id, 'name': post.name})
            except Exception as exc:
                _logger.exception('LinkedIn API: publicación %s no procesada', index)
                errors.append({'index': index, 'error': str(exc)})

        return self._json_response({
            'ok': not errors,
            'processed': len(results),
            'errors': len(errors),
            'posts': results,
            'error_details': errors,
        })

    # ── Métricas para el router ───────────────────────────────────────────────

    @http.route('/vantis/linkedin/metrics', type='http', auth='public',
                methods=['GET', 'POST'], csrf=False, save_session=False)
    def get_metrics(self, days=None, **kwargs):
        """Devuelve las métricas del embudo sin crear ningún diagnóstico.

        Pensado para que el flujo de n8n (o una skill externa) pueda razonar
        con los mismos datos que usa el router interno, sin ensuciar el
        histórico de diagnósticos con ejecuciones de prueba.
        """
        profile, error = self._authenticate()
        if error:
            return error

        body = self._get_body()
        try:
            period_days = int(days or body.get('days') or profile.router_period_days or 30)
        except (TypeError, ValueError):
            period_days = profile.router_period_days or 30

        Diagnostic = request.env['marketing.linkedin.diagnostic'].sudo()
        # Un registro en memoria: sirve para reutilizar la recogida de métricas
        # sin dejar rastro en la tabla.
        probe = Diagnostic.new({'profile_id': profile.id, 'period_days': period_days})
        metrics = probe._collect_metrics()
        diagnosis, stage, reasoning = probe._diagnose(metrics)
        from ..models.linkedin_common import NEXT_ACTION_BY_DIAGNOSIS
        return self._json_response({
            'ok': True,
            'profile': profile.name,
            'period_days': period_days,
            'diagnosis': diagnosis,
            'diagnosis_reasoning': reasoning,
            'bottleneck_stage': stage.name if stage else '',
            'next_action': NEXT_ACTION_BY_DIAGNOSIS.get(diagnosis, ''),
            'ai_context': profile.get_ai_context(),
            'metrics': metrics,
        })

    @http.route('/vantis/linkedin/router/run', type='http', auth='public',
                methods=['POST'], csrf=False, save_session=False)
    def run_router(self, **kwargs):
        """Lanza el router desde el cron de n8n y devuelve el diagnóstico.

        Útil cuando se prefiere que la periodicidad la mande n8n en vez del
        cron de Odoo: el cálculo sigue siendo el mismo.
        """
        profile, error = self._authenticate()
        if error:
            return error

        body = self._get_body()
        try:
            period_days = int(body.get('days') or profile.router_period_days or 30)
        except (TypeError, ValueError):
            period_days = profile.router_period_days or 30
        notify = body.get('notify', True) not in (False, 'false', 'False', 0, '0')

        diagnostic = request.env['marketing.linkedin.diagnostic'].sudo().run_for_profile(
            profile, period_days=period_days, trigger='n8n', notify=notify,
        )
        return self._json_response({
            'ok': diagnostic.state != 'failed',
            'diagnostic_id': diagnostic.id,
            'diagnosis': diagnostic.diagnosis,
            'next_action': diagnostic.next_action,
            'bottleneck_stage': diagnostic.bottleneck_stage_id.name or '',
            'report': diagnostic.report or '',
            'error': diagnostic.error_message or '',
            'metrics': json.loads(diagnostic.metrics_json) if diagnostic.metrics_json else {},
        })

    @http.route('/vantis/linkedin/diagnostic', type='http', auth='public',
                methods=['POST'], csrf=False, save_session=False)
    def receive_diagnostic(self, **kwargs):
        """Recibe el informe redactado por n8n para un diagnóstico ya calculado.

        Cuerpo: ``{"diagnostic_id": 12, "report": "<p>…</p>", "notify": true}``.
        El diagnóstico (la fase señalada) no se toca: n8n redacta, no decide.
        """
        profile, error = self._authenticate()
        if error:
            return error

        body = self._get_body()
        diagnostic_id = body.get('diagnostic_id')
        report = body.get('report') or body.get('informe') or ''
        if not diagnostic_id or not report:
            return self._json_response({
                'ok': False, 'error': 'bad_payload',
                'message': 'Hacen falta "diagnostic_id" y "report".',
            }, status=400)

        diagnostic = request.env['marketing.linkedin.diagnostic'].sudo().browse(
            int(diagnostic_id)
        ).exists()
        if not diagnostic:
            return self._json_response({
                'ok': False, 'error': 'not_found',
                'message': f'No existe el diagnóstico {diagnostic_id}.',
            }, status=404)
        if diagnostic.profile_id != profile:
            return self._json_response({
                'ok': False, 'error': 'forbidden',
                'message': 'Ese diagnóstico pertenece a otro perfil.',
            }, status=403)

        if '<' not in report:
            report = '<p>' + report.replace('\n\n', '</p><p>').replace('\n', '<br/>') + '</p>'
        diagnostic.write({
            'report': report,
            'generated_by': 'n8n',
            'state': 'done' if diagnostic.state != 'failed' else diagnostic.state,
        })
        if body.get('notify', True) not in (False, 'false', 'False', 0, '0'):
            diagnostic.action_notify()

        return self._json_response({'ok': True, 'diagnostic_id': diagnostic.id})

    # ── Ventas ligero: retorno de la preparación de llamada ───────────────────

    @http.route('/vantis/linkedin/call-prep', type='http', auth='public',
                methods=['POST'], csrf=False, save_session=False)
    def receive_call_prep(self, **kwargs):
        """Recibe de n8n la preparación de llamada y la escribe en el lead.

        Cuerpo: ``{"lead_id": 42, "prep": "<h4>…"}``. El resultado se guarda en
        el campo del lead y se publica en el chatter, que es donde el comercial
        lo va a mirar sin salir de Odoo.
        """
        profile, error = self._authenticate()
        if error:
            return error

        body = self._get_body()
        lead_id = body.get('lead_id')
        prep = body.get('prep') or body.get('report') or body.get('preparacion') or ''
        if not lead_id or not prep:
            return self._json_response({
                'ok': False, 'error': 'bad_payload',
                'message': 'Hacen falta "lead_id" y "prep".',
            }, status=400)

        lead = request.env['crm.lead'].sudo().browse(int(lead_id)).exists()
        if not lead:
            return self._json_response({
                'ok': False, 'error': 'not_found',
                'message': f'No existe el lead {lead_id}.',
            }, status=404)

        if '<' not in prep:
            prep = '<p>' + prep.replace('\n\n', '</p><p>').replace('\n', '<br/>') + '</p>'
        lead.apply_call_prep(prep, generated_by='n8n')
        _logger.info('LinkedIn API: preparación de llamada aplicada al lead %s '
                     'desde el perfil %s', lead.id, profile.name)
        return self._json_response({'ok': True, 'lead_id': lead.id})

    @http.route('/vantis/linkedin/lead-context', type='http', auth='public',
                methods=['GET', 'POST'], csrf=False, save_session=False)
    def get_lead_context(self, lead_id=None, **kwargs):
        """Devuelve todo el contexto de un lead para preparar la llamada.

        Es el equivalente a encadenar detalle del lead, comunicaciones y
        actividades: un solo GET en vez de tres nodos HTTP Request en n8n.
        """
        profile, error = self._authenticate()
        if error:
            return error

        body = self._get_body()
        lead_id = lead_id or body.get('lead_id')
        if not lead_id:
            return self._json_response({
                'ok': False, 'error': 'bad_payload',
                'message': 'Falta "lead_id".',
            }, status=400)

        lead = request.env['crm.lead'].sudo().browse(int(lead_id)).exists()
        if not lead:
            return self._json_response({
                'ok': False, 'error': 'not_found',
                'message': f'No existe el lead {lead_id}.',
            }, status=404)

        return self._json_response({
            'ok': True,
            'ai_context': profile.get_ai_context(),
            'context': lead.get_call_prep_context(),
        })
