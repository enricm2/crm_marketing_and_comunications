"""Router de embudo: dónde está el cuello de botella.

Cruza dos señales que normalmente se miran por separado —el rendimiento del
contenido en LinkedIn y el estado del pipeline en el CRM— y dice cuál de las
tres fases está frenando: posicionamiento, captación o conversión. El objetivo
es decidir con datos qué se trabaja esta semana en vez de a ojo.

El informe se puede generar de dos formas, según el perfil: llamando a Claude
desde Odoo o delegando en el flujo de n8n, que devuelve el texto por el
endpoint de retorno. El diagnóstico numérico —el que decide la fase— siempre
se calcula aquí: es una regla, no una opinión, y debe ser reproducible.
"""
import json
import logging
from datetime import timedelta

from odoo import models, fields, api
from odoo.exceptions import UserError

from .linkedin_common import FUNNEL_DIAGNOSES, NEXT_ACTION_BY_DIAGNOSIS

_logger = logging.getLogger(__name__)

_P = 'crm_marketing_and_comunications.'


class MarketingLinkedinDiagnostic(models.Model):
    _name = 'marketing.linkedin.diagnostic'
    _description = 'Diagnóstico del embudo (router)'
    _order = 'run_date desc, id desc'
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
    run_date = fields.Datetime(
        string='Fecha del diagnóstico', default=fields.Datetime.now, readonly=True,
    )
    period_days = fields.Integer(string='Periodo analizado (días)', readonly=True)
    trigger = fields.Selection([
        ('manual', 'Manual'),
        ('cron', 'Programado'),
        ('n8n', 'Desde n8n'),
    ], string='Disparado por', default='manual', readonly=True)

    # ── Resultado ─────────────────────────────────────────────────────────────
    diagnosis = fields.Selection(
        FUNNEL_DIAGNOSES, string='Diagnóstico', readonly=True, index=True,
    )
    next_action = fields.Char(
        string='Siguiente skill / agente', readonly=True,
        help='Qué ejecutar a continuación según el diagnóstico.',
    )
    bottleneck_stage_id = fields.Many2one(
        'crm.stage', string='Etapa atascada', readonly=True,
        help='Solo se rellena en diagnósticos de conversión: la etapa concreta '
             'donde se quedan parados los leads.',
    )
    report = fields.Html(
        string='Informe', sanitize=False,
        help='Informe corto (máx. 200 palabras) con el diagnóstico y qué hacer.',
    )
    generated_by = fields.Selection([
        ('rules', 'Reglas (sin IA)'),
        ('claude', 'Claude desde Odoo'),
        ('n8n', 'Flujo de n8n'),
    ], string='Informe generado por', readonly=True, default='rules')
    state = fields.Selection([
        ('draft', 'En curso'),
        ('done', 'Completado'),
        ('failed', 'Fallido'),
    ], string='Estado', default='draft', required=True, readonly=True)
    error_message = fields.Char(string='Error', readonly=True)
    notified = fields.Boolean(string='Avisado', readonly=True)

    # ── Métricas de contenido ─────────────────────────────────────────────────
    posts_count = fields.Integer(string='Publicaciones', readonly=True)
    impressions = fields.Integer(string='Impresiones', readonly=True)
    avg_engagement_rate = fields.Float(
        string='Engagement medio (%)', readonly=True, digits=(16, 2),
    )
    signals_count = fields.Integer(string='Señales', readonly=True)
    hot_signals_count = fields.Integer(string='Señales calientes', readonly=True)
    signal_to_lead_rate = fields.Float(
        string='Señal → lead (%)', readonly=True, digits=(16, 2),
    )

    # ── Métricas de pipeline ──────────────────────────────────────────────────
    leads_created = fields.Integer(string='Leads nuevos', readonly=True)
    leads_from_linkedin = fields.Integer(string='Leads desde LinkedIn', readonly=True)
    open_leads = fields.Integer(string='Leads abiertos', readonly=True)
    won_count = fields.Integer(string='Ganados', readonly=True)
    lost_count = fields.Integer(string='Perdidos', readonly=True)
    win_rate = fields.Float(string='Tasa de cierre (%)', readonly=True, digits=(16, 2))

    metrics_json = fields.Text(
        string='Métricas completas (JSON)', readonly=True,
        help='Todo lo medido, incluida la foto por etapa. Es lo que se manda a '
             'n8n y a la IA para redactar el informe.',
    )
    display_name = fields.Char(compute='_compute_display_name', store=True)

    @api.depends('profile_id', 'run_date', 'diagnosis')
    def _compute_display_name(self):
        labels = dict(FUNNEL_DIAGNOSES)
        for record in self:
            diagnosis = labels.get(record.diagnosis, 'Sin diagnóstico')
            date_txt = fields.Datetime.to_string(record.run_date)[:16] if record.run_date else ''
            record.display_name = f'{diagnosis} · {record.profile_id.name or ""} · {date_txt}'

    # ── Ejecución ─────────────────────────────────────────────────────────────

    @api.model
    def run_for_profile(self, profile, period_days=None, trigger='manual', notify=True):
        """Ejecuta el router para un perfil y devuelve el diagnóstico creado."""
        period_days = period_days or profile.router_period_days or 30
        diagnostic = self.create({
            'profile_id': profile.id,
            'period_days': period_days,
            'trigger': trigger,
        })

        try:
            metrics = diagnostic._collect_metrics()
            diagnosis, stage, reasoning = diagnostic._diagnose(metrics)
            diagnostic.write({
                'diagnosis': diagnosis,
                'next_action': NEXT_ACTION_BY_DIAGNOSIS.get(diagnosis, ''),
                'bottleneck_stage_id': stage.id if stage else False,
                'metrics_json': json.dumps(metrics, ensure_ascii=False, indent=2, default=str),
                'posts_count': metrics['content']['posts'],
                'impressions': metrics['content']['impressions'],
                'avg_engagement_rate': metrics['content']['avg_engagement_rate'],
                'signals_count': metrics['content']['signals'],
                'hot_signals_count': metrics['content']['hot_signals'],
                'signal_to_lead_rate': metrics['content']['signal_to_lead_rate'],
                'leads_created': metrics['pipeline']['leads_created'],
                'leads_from_linkedin': metrics['pipeline']['leads_from_linkedin'],
                'open_leads': metrics['pipeline']['open_leads'],
                'won_count': metrics['pipeline']['won'],
                'lost_count': metrics['pipeline']['lost'],
                'win_rate': metrics['pipeline']['win_rate'],
            })
            diagnostic._build_report(metrics, reasoning)
            diagnostic.state = 'done'
        except Exception as exc:
            _logger.exception('LinkedIn: router de embudo fallido para %s', profile.name)
            diagnostic.write({'state': 'failed', 'error_message': str(exc)[:250]})
            return diagnostic

        if notify and diagnostic.report:
            diagnostic.action_notify()
        return diagnostic

    # ── Recogida de métricas ──────────────────────────────────────────────────

    def _collect_metrics(self):
        """Reúne las dos mitades del diagnóstico: contenido y pipeline."""
        self.ensure_one()
        date_from = fields.Datetime.now() - timedelta(days=self.period_days)
        return {
            'profile': self.profile_id.name,
            'period_days': self.period_days,
            'date_from': fields.Datetime.to_string(date_from),
            'date_to': fields.Datetime.to_string(fields.Datetime.now()),
            'content': self._collect_content_metrics(date_from),
            'pipeline': self._collect_pipeline_metrics(date_from),
            'benchmarks': {
                'min_engagement_rate': self.profile_id.router_min_engagement_rate,
                'min_leads': self.profile_id.router_min_leads,
                'min_signals': self.profile_id.router_min_signals,
                'min_signal_to_lead_rate': self.profile_id.router_min_signal_to_lead,
                'stage_stuck_days': self.profile_id.router_stage_stuck_days,
            },
        }

    def _collect_content_metrics(self, date_from):
        """Rendimiento del contenido publicado en el periodo."""
        self.ensure_one()
        posts = self.env['marketing.linkedin.post'].search([
            ('profile_id', '=', self.profile_id.id),
            ('state', '=', 'published'),
            ('published_date', '>=', date_from),
        ])
        signals = self.env['marketing.linkedin.signal'].search([
            ('profile_id', '=', self.profile_id.id),
            ('signal_date', '>=', date_from),
            ('state', '!=', 'discarded'),
        ])
        signals_with_lead = signals.filtered(lambda s: s.lead_id)

        impressions = sum(posts.mapped('impressions'))
        engagement_rates = [p.engagement_rate for p in posts if p.impressions]
        avg_engagement = round(sum(engagement_rates) / len(engagement_rates), 2) \
            if engagement_rates else 0.0

        top_posts = posts.sorted(lambda p: p.engagement_rate, reverse=True)[:3]
        return {
            'posts': len(posts),
            'impressions': impressions,
            'reactions': sum(posts.mapped('reactions')),
            'comments': sum(posts.mapped('comments')),
            'shares': sum(posts.mapped('shares')),
            'avg_engagement_rate': avg_engagement,
            'signals': len(signals),
            'hot_signals': len(signals.filtered('is_hot')),
            'signals_with_lead': len(signals_with_lead),
            'signal_to_lead_rate': round(
                len(signals_with_lead) * 100.0 / len(signals), 2
            ) if signals else 0.0,
            'signals_by_type': {
                stype: len(signals.filtered(lambda s, t=stype: s.signal_type == t))
                for stype in set(signals.mapped('signal_type'))
            },
            'top_posts': [{
                'name': p.name,
                'impressions': p.impressions,
                'engagement_rate': p.engagement_rate,
                'leads': p.lead_count,
            } for p in top_posts],
        }

    def _collect_pipeline_metrics(self, date_from):
        """Foto del pipeline del perfil en el periodo, etapa a etapa."""
        self.ensure_one()
        Lead = self.env['crm.lead']
        scope_domain = self._get_pipeline_scope_domain()

        created = Lead.search(scope_domain + [('create_date', '>=', date_from)])
        source = self.env.ref(
            'crm_marketing_and_comunications.utm_source_linkedin_signal',
            raise_if_not_found=False,
        )
        from_linkedin = created.filtered(
            lambda l: source and l.source_id == source
        ) if source else Lead.browse()

        won = Lead.search(scope_domain + [
            ('date_closed', '>=', date_from), ('stage_id.is_won', '=', True),
        ])
        lost = Lead.with_context(active_test=False).search(scope_domain + [
            ('date_closed', '>=', date_from), ('active', '=', False),
        ])
        open_leads = Lead.search(scope_domain + [('stage_id.is_won', '=', False)])

        stuck_days = self.profile_id.router_stage_stuck_days or 21
        stuck_before = fields.Datetime.now() - timedelta(days=stuck_days)

        stages = []
        for stage in self.env['crm.stage'].search([], order='sequence'):
            in_stage = open_leads.filtered(lambda l, s=stage: l.stage_id == s)
            if not in_stage and not stage.is_won:
                stages.append({
                    'stage_id': stage.id, 'name': stage.name,
                    'sequence': stage.sequence, 'open': 0, 'stuck': 0,
                    'entered_in_period': 0, 'is_won': stage.is_won,
                })
                continue
            stuck = in_stage.filtered(lambda l: l.write_date and l.write_date < stuck_before)
            stages.append({
                'stage_id': stage.id,
                'name': stage.name,
                'sequence': stage.sequence,
                'open': len(in_stage),
                'stuck': len(stuck),
                'entered_in_period': len(created.filtered(lambda l, s=stage: l.stage_id == s)),
                'is_won': stage.is_won,
            })

        total_closed = len(won) + len(lost)
        return {
            'leads_created': len(created),
            'leads_from_linkedin': len(from_linkedin),
            'open_leads': len(open_leads),
            'won': len(won),
            'lost': len(lost),
            'win_rate': round(len(won) * 100.0 / total_closed, 2) if total_closed else 0.0,
            'stages': stages,
        }

    def _get_pipeline_scope_domain(self):
        """Qué parte del pipeline mira este perfil.

        Si el perfil tiene equipo de ventas, se mira ese equipo; si no, los
        leads del comercial asignado o del propietario. Sin este acotado, en
        una base multiusuario el router de un usuario diagnosticaría sobre el
        pipeline de todos.
        """
        self.ensure_one()
        profile = self.profile_id
        if profile.team_id:
            return [('team_id', '=', profile.team_id.id)]
        owner = profile.salesperson_id or profile.user_id
        return [('user_id', '=', owner.id)]

    # ── Diagnóstico ───────────────────────────────────────────────────────────

    def _diagnose(self, metrics):
        """Aplica la lógica del router. Devuelve (diagnóstico, etapa, motivo).

        El orden importa. Se comprueba primero si hay atención (engagement),
        luego si esa atención se convierte en conversación, y solo al final el
        pipeline: no tiene sentido señalar un problema de conversión cuando lo
        que falta es gente entrando.
        """
        self.ensure_one()
        content = metrics['content']
        pipeline = metrics['pipeline']
        bench = metrics['benchmarks']

        if not content['posts'] and not pipeline['leads_created']:
            return 'no_data', None, (
                'No hay publicaciones ni leads registrados en el periodo: sin '
                'datos no hay diagnóstico posible.'
            )

        low_engagement = content['avg_engagement_rate'] < bench['min_engagement_rate']
        few_leads = pipeline['leads_created'] < bench['min_leads']
        few_signals = content['signals'] < bench['min_signals']
        low_signal_conversion = (
            content['signal_to_lead_rate'] < bench['min_signal_to_lead_rate']
        )

        # 1. Posicionamiento: no hay atención y tampoco entra nadie.
        if low_engagement and (few_leads or few_signals):
            return 'positioning', None, (
                f"Engagement medio {content['avg_engagement_rate']}% por debajo "
                f"del objetivo ({bench['min_engagement_rate']}%) y solo "
                f"{content['signals']} señales / {pipeline['leads_created']} leads "
                f"nuevos en {metrics['period_days']} días. El contenido no está "
                f"generando atención suficiente."
            )

        # 2. Captación: hay atención, pero no se convierte en conversación.
        if not low_engagement and (few_leads or low_signal_conversion):
            return 'capture', None, (
                f"Engagement medio {content['avg_engagement_rate']}% por encima "
                f"del objetivo, con {content['signals']} señales, pero solo "
                f"{content['signals_with_lead']} acabaron en lead "
                f"({content['signal_to_lead_rate']}%) y entraron "
                f"{pipeline['leads_created']} leads. La atención no se está "
                f"convirtiendo en conversación."
            )

        # 3. Conversión: entra gente, pero se atasca en una etapa concreta.
        stage_data = self._find_bottleneck_stage(pipeline)
        if stage_data:
            stage = self.env['crm.stage'].browse(stage_data['stage_id'])
            return 'conversion', stage, (
                f"Entran leads con normalidad ({pipeline['leads_created']} en el "
                f"periodo), pero {stage_data['stuck']} de los {stage_data['open']} "
                f"leads abiertos en «{stage_data['name']}» llevan más de "
                f"{bench['stage_stuck_days']} días sin moverse. El cuello de "
                f"botella está en esa etapa."
            )

        return 'healthy', None, (
            f"Engagement {content['avg_engagement_rate']}%, "
            f"{content['signals']} señales, {pipeline['leads_created']} leads "
            f"nuevos y {pipeline['win_rate']}% de cierre. Ninguna fase está "
            f"claramente por debajo de sus umbrales."
        )

    def _find_bottleneck_stage(self, pipeline):
        """La etapa con más leads parados, si es que hay alguna significativa.

        Se ignora la última etapa ganada y se exige que al menos la mitad de
        los leads abiertos de la etapa estén parados: dos leads quietos en una
        etapa con veinte no es un cuello de botella.
        """
        candidates = [
            s for s in pipeline['stages']
            if not s['is_won'] and s['stuck'] >= 2 and s['stuck'] >= s['open'] / 2.0
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda s: (s['stuck'], s['open']))

    # ── Informe ───────────────────────────────────────────────────────────────

    def _build_report(self, metrics, reasoning):
        """Redacta el informe con IA, o con reglas si la IA no está disponible."""
        self.ensure_one()
        mode = self.profile_id.ai_mode

        if mode == 'n8n':
            payload = self._get_n8n_payload(metrics, reasoning)
            ok, message = self.profile_id.call_n8n('router', payload)
            if ok:
                # El informe definitivo llegará por el endpoint de retorno;
                # mientras tanto queda el de reglas para que el registro nunca
                # se quede vacío si n8n tarda o falla.
                self.write({
                    'report': self._render_rules_report(metrics, reasoning),
                    'generated_by': 'rules',
                })
                return
            _logger.warning('LinkedIn: router delegado a n8n sin éxito — %s', message)

        report_html = ''
        if mode == 'odoo':
            report_html = self._render_ai_report(metrics, reasoning)
        if report_html:
            self.write({'report': report_html, 'generated_by': 'claude'})
            return
        self.write({
            'report': self._render_rules_report(metrics, reasoning),
            'generated_by': 'rules',
        })

    def _render_ai_report(self, metrics, reasoning):
        """Pide a Claude el informe corto. Devuelve '' si no se puede."""
        self.ensure_one()
        icp = self.env['ir.config_parameter'].sudo()
        api_key = icp.get_param(f'{_P}ai_anthropic_key', '') or \
            icp.get_param('crm_marketing_and_comunications.ai_anthropic_key', '')
        if not self.env['marketing.ai.service'].proveedores_disponibles():
            _logger.info('LinkedIn: sin clave de Anthropic, informe por reglas.')
            return ''

        prompt = self._build_router_prompt(metrics, reasoning)
        try:
            text = self.env['marketing.campaign']._call_claude(prompt=prompt, contexto='diagnóstico linkedin')
        except Exception as exc:
            _logger.warning('LinkedIn: Claude no pudo redactar el informe — %s', exc)
            return ''
        if not text:
            return ''
        if '<' not in text:
            text = '<p>' + text.replace('\n\n', '</p><p>').replace('\n', '<br/>') + '</p>'
        return text

    def _build_router_prompt(self, metrics, reasoning):
        """Prompt del router, con el rol y contexto del perfil por delante."""
        self.ensure_one()
        labels = dict(FUNNEL_DIAGNOSES)
        return f"""{self.profile_id.get_ai_context()}

=== TAREA ===
Redacta un informe de diagnóstico de embudo de captación. Máximo 200 palabras.
El diagnóstico ya está calculado: NO lo cambies, explícalo.

Diagnóstico calculado: {labels.get(self.diagnosis, self.diagnosis)}
Motivo del cálculo: {reasoning}
Etapa señalada: {self.bottleneck_stage_id.name or '—'}
Siguiente skill recomendada: {self.next_action or '—'}

=== DATOS DEL PERIODO ({metrics['period_days']} días) ===
{json.dumps(metrics, ensure_ascii=False, indent=2, default=str)}

=== FORMATO ===
HTML simple (<p>, <ul>, <li>, <b>). Sin <html> ni <body>. Estructura:
1. Una frase con el diagnóstico y el dato que lo sustenta.
2. Tres viñetas como máximo con lo que está fallando, cada una con su número.
3. Una frase final con la acción concreta de esta semana y la skill a ejecutar.
Tono directo, sin relleno ni felicitaciones. Devuelve SOLO el HTML."""

    def _render_rules_report(self, metrics, reasoning):
        """Informe de reglas: el que se usa cuando no hay IA disponible.

        No es un texto de relleno: lleva los mismos números y la misma
        recomendación, para que el router siga siendo útil sin clave de API.
        """
        self.ensure_one()
        content = metrics['content']
        pipeline = metrics['pipeline']
        labels = dict(FUNNEL_DIAGNOSES)
        recommendations = {
            'positioning': 'Trabaja el posicionamiento: revisa ganchos y ángulos de '
                           'contenido antes de prospectar más.',
            'capture': 'Trabaja la captación: convierte en conversación a quien ya '
                       'está interactuando contigo.',
            'conversion': 'Trabaja la conversión: desatasca los leads parados con una '
                          'preparación de llamada por cada uno.',
            'healthy': 'Sin cuello de botella claro: mantén el ritmo y vuelve a medir '
                       'la semana que viene.',
            'no_data': 'Faltan datos. Registra publicaciones y deja que entren señales '
                       'antes de volver a diagnosticar.',
        }
        stage_line = ''
        if self.bottleneck_stage_id:
            stage_line = f'<li>Etapa atascada: <b>{self.bottleneck_stage_id.name}</b></li>'
        action_line = ''
        if self.next_action:
            action_line = (f'<p><b>Siguiente paso:</b> ejecutar '
                           f'<code>{self.next_action}</code>.</p>')
        return f"""
<p><b>Diagnóstico: {labels.get(self.diagnosis, '—')}</b> — {reasoning}</p>
<ul>
  <li>Contenido: {content['posts']} publicaciones, {content['impressions']} impresiones,
      engagement medio {content['avg_engagement_rate']}%.</li>
  <li>Señales: {content['signals']} ({content['hot_signals']} calientes),
      {content['signal_to_lead_rate']}% acabaron en lead.</li>
  <li>Pipeline: {pipeline['leads_created']} leads nuevos
      ({pipeline['leads_from_linkedin']} desde LinkedIn), {pipeline['open_leads']} abiertos,
      {pipeline['win_rate']}% de cierre.</li>
  {stage_line}
</ul>
<p>{recommendations.get(self.diagnosis, '')}</p>
{action_line}
"""

    def _get_n8n_payload(self, metrics, reasoning):
        """Lo que se manda al webhook del router en n8n."""
        self.ensure_one()
        return {
            'action': 'router_embudo',
            'diagnostic_id': self.id,
            'diagnosis': self.diagnosis,
            'diagnosis_reasoning': reasoning,
            'bottleneck_stage': self.bottleneck_stage_id.name or '',
            'next_action': self.next_action or '',
            'period_days': self.period_days,
            'metrics': metrics,
            'ai_context': self.profile_id.get_ai_context(),
            'notify_phone': self.profile_id.notify_phone or '',
            'callback_url': '/vantis/linkedin/diagnostic',
        }

    # ── Avisos y acciones ─────────────────────────────────────────────────────

    def action_notify(self):
        """Manda el informe al dueño del perfil."""
        for diagnostic in self:
            if not diagnostic.report:
                continue
            labels = dict(FUNNEL_DIAGNOSES)
            body = (
                f'<p><b>Router de embudo — {diagnostic.profile_id.name}</b><br/>'
                f'Diagnóstico: {labels.get(diagnostic.diagnosis, "—")} '
                f'({diagnostic.period_days} días)</p>'
                f'{diagnostic.report}'
            )
            diagnostic.profile_id.notify(body)
            diagnostic.notified = True
        return True

    def action_rerun(self):
        """Vuelve a ejecutar el router con la misma configuración."""
        self.ensure_one()
        new = self.run_for_profile(
            self.profile_id, period_days=self.period_days, trigger='manual',
        )
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'marketing.linkedin.diagnostic',
            'res_id': new.id,
            'view_mode': 'form',
        }

    def action_open_bottleneck_leads(self):
        """Abre los leads parados en la etapa señalada."""
        self.ensure_one()
        if not self.bottleneck_stage_id:
            raise UserError('Este diagnóstico no señala ninguna etapa concreta.')
        stuck_before = self.run_date - timedelta(
            days=self.profile_id.router_stage_stuck_days or 21
        )
        domain = self._get_pipeline_scope_domain() + [
            ('stage_id', '=', self.bottleneck_stage_id.id),
            ('write_date', '<', stuck_before),
        ]
        return {
            'type': 'ir.actions.act_window',
            'name': f'Leads parados en {self.bottleneck_stage_id.name}',
            'res_model': 'crm.lead',
            'view_mode': 'list,kanban,form',
            'domain': domain,
        }

    # ── Cron ──────────────────────────────────────────────────────────────────

    @api.model
    def _cron_weekly_router(self):
        """Ejecuta el router semanal de todos los perfiles activos.

        Un perfil que falle no puede impedir que se diagnostiquen los demás:
        cada uno va en su propio try, y `run_for_profile` ya deja el error
        registrado en su diagnóstico.
        """
        profiles = self.env['marketing.linkedin.profile'].search([
            ('router_cron_enabled', '=', True),
        ])
        for profile in profiles:
            try:
                self.run_for_profile(profile, trigger='cron')
                self.env.cr.commit()
            except Exception:
                _logger.exception(
                    'LinkedIn: router semanal fallido para el perfil %s', profile.name,
                )
                self.env.cr.rollback()
        return True
