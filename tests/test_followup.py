from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)


class TestFollowupLaunch(TransactionCase):
    """El wizard de seguimiento crea una campaña `followup` reutilizando toda
    la maquinaria de campañas (líneas, plantillas, envío)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.channel_email = cls.env['marketing.channel'].search(
            [('code', '=', 'email')], limit=1)
        if not cls.channel_email:
            cls.channel_email = cls.env['marketing.channel'].create({
                'name': 'Email Marketing', 'code': 'email',
            })

        cls.esp = cls.env['marketing.esp.provider'].create({
            'name': 'odoo_smtp',
            'from_email': 'enric@uniasser.com',
        })

        cls.stage = cls.env['crm.stage'].create({'name': 'Contactado - sin respuesta'})
        cls.stage_demo = cls.env['crm.stage'].create({'name': 'Reunión agendada'})

        def _lead(name, email):
            return cls.env['crm.lead'].create({
                'name': name, 'partner_name': name, 'email_from': email,
                'type': 'opportunity', 'stage_id': cls.stage.id, 'probability': 40,
            })

        cls.lead_silent_1 = _lead('Silent One', 'silent1@example.com')
        cls.lead_silent_2 = _lead('Silent Two', 'silent2@example.com')
        cls.lead_replied = _lead('Replied Co', 'replied@example.com')
        cls.lead_no_email = cls.env['crm.lead'].create({
            'name': 'No Email Co', 'partner_name': 'No Email Co',
            'type': 'opportunity', 'stage_id': cls.stage.id, 'probability': 40,
        })

        # Campaña previa de captación con envíos ya hechos.
        cls.prev_campaign = cls.env['marketing.campaign'].create({
            'name': 'Captación inicial',
            'purpose': 'Primer contacto',
            'target_audience': 'Test',
            'channel_ids': [(4, cls.channel_email.id)],
            'esp_provider_id': cls.esp.id,
        })
        for lead in (cls.lead_silent_1, cls.lead_silent_2, cls.lead_replied):
            cls.env['marketing.campaign.lead'].create({
                'campaign_id': cls.prev_campaign.id,
                'lead_id': lead.id,
                'channel_used': 'email',
                'sent_at': '2026-01-10 09:00:00',
            })
        cls.env['marketing.campaign.lead'].search([
            ('campaign_id', '=', cls.prev_campaign.id),
            ('lead_id', '=', cls.lead_replied.id),
        ]).replied_at = '2026-01-12 10:00:00'

    def _wizard(self, **overrides):
        vals = {
            'name': 'Seguimiento test',
            'stage_id': self.stage.id,
            'esp_provider_id': self.esp.id,
            'purpose': 'Reenganchar y cerrar reunión.',
            'target_audience': 'Leads sin respuesta.',
            'template_mode': 'ai',
            'only_no_reply': True,
            'skip_recent_days': 0,
            'source_campaign_id': self.prev_campaign.id,
            'stage_demo_id': self.stage_demo.id,
        }
        vals.update(overrides)
        return self.env['marketing.followup.launch'].create(vals)

    def test_candidates_exclude_replied_and_no_email(self):
        wizard = self._wizard()
        leads = wizard._candidate_leads()
        self.assertIn(self.lead_silent_1, leads)
        self.assertIn(self.lead_silent_2, leads)
        self.assertNotIn(self.lead_replied, leads, 'quien ya respondió no se sigue')
        self.assertNotIn(self.lead_no_email, leads, 'sin email no hay seguimiento')

    def test_skip_recent_days_filter(self):
        # Un envío reciente a lead_silent_1 lo saca de la lista.
        self.env['marketing.campaign.lead'].search([
            ('campaign_id', '=', self.prev_campaign.id),
            ('lead_id', '=', self.lead_silent_1.id),
        ]).sent_at = self.env.cr.now()
        wizard = self._wizard(skip_recent_days=15)
        leads = wizard._candidate_leads()
        self.assertNotIn(self.lead_silent_1, leads)
        self.assertIn(self.lead_silent_2, leads)

    def test_launch_creates_followup_campaign(self):
        wizard = self._wizard()
        action = wizard.action_launch()
        campaign = self.env['marketing.campaign'].browse(action['res_id'])

        self.assertEqual(campaign.campaign_type, 'followup')
        self.assertEqual(campaign.followup_source_campaign_id, self.prev_campaign)
        self.assertTrue(campaign.use_email)
        self.assertEqual(campaign.stage_source_id, self.stage)
        self.assertEqual(campaign.state, 'draft')

        lines = campaign.campaign_lead_ids
        self.assertEqual(set(lines.mapped('lead_id')),
                         {self.lead_silent_1, self.lead_silent_2})
        self.assertTrue(all(l.exclusion_reason == 'none' for l in lines))

    def test_prior_contact_context_mentions_previous_campaign(self):
        wizard = self._wizard()
        campaign = self.env['marketing.campaign'].browse(
            wizard.action_launch()['res_id'])
        ctx = campaign._followup_prior_contact_context(self.lead_silent_1)
        self.assertIn('Captación inicial', ctx)
        self.assertIn('2026-01-10', ctx)

    def test_launch_requires_template_when_copy_mode(self):
        wizard = self._wizard(template_mode='copy', source_template_id=False)
        with self.assertRaises(UserError):
            wizard.action_launch()

    # ── Plantilla siempre enlazada ──────────────────────────────────────────

    def _bare_email_campaign(self):
        return self.env['marketing.campaign'].create({
            'name': 'Sin plantilla',
            'purpose': 'x', 'target_audience': 'x',
            'channel_ids': [(4, self.channel_email.id)],
            'esp_provider_id': self.esp.id,
        })

    def test_template_created_from_list_autolinks_to_campaign(self):
        """Una plantilla creada 'a mano' (como desde la lista) se engancha sola."""
        campaign = self._bare_email_campaign()
        self.assertFalse(campaign.email_template_id)
        tpl = self.env['marketing.ai.template'].create({
            'campaign_id': campaign.id,
            'channel': 'email',
            'base_html': '<p>{{OPENING_PARAGRAPH}}</p>',
        })
        self.assertEqual(campaign.email_template_id, tpl)

    def test_ensure_channel_templates_heals_missing_link(self):
        campaign = self._bare_email_campaign()
        tpl_draft = self.env['marketing.ai.template'].create({
            'campaign_id': campaign.id, 'channel': 'email',
            'base_html': '<p>borrador {{OPENING_PARAGRAPH}}</p>',
        })
        tpl_ok = self.env['marketing.ai.template'].create({
            'campaign_id': campaign.id, 'channel': 'email',
            'base_html': '<p>buena {{OPENING_PARAGRAPH}}</p>',
        })
        tpl_ok.action_approve()
        # Simular el enlace perdido y comprobar que se recupera (prefiere aprobada)
        campaign.email_template_id = False
        campaign._ensure_channel_templates()
        self.assertEqual(campaign.email_template_id, tpl_ok)

    def test_approve_campaign_requires_email_template(self):
        campaign = self._bare_email_campaign()
        with self.assertRaises(UserError):
            campaign.action_approve_campaign()

    # ── Generación de contenido en segundo plano ────────────────────────────

    def _campaign_with_lines(self, n=2):
        campaign = self._bare_email_campaign()
        self.env['marketing.ai.template'].create({
            'campaign_id': campaign.id, 'channel': 'email',
            'base_html': '<p>{{OPENING_PARAGRAPH}}</p>',
        })  # se auto-enlaza
        leads = self.env['crm.lead']
        for i in range(n):
            leads |= self.env['crm.lead'].create({
                'name': f'BG{i}', 'partner_name': f'BG{i}',
                'email_from': f'bg{i}@example.com',
                'type': 'opportunity', 'probability': 40,
            })
        campaign.lead_ids = [(6, 0, leads.ids)]
        campaign.action_generate_campaign_leads()
        return campaign

    def test_generate_all_content_runs_in_background(self):
        campaign = self._campaign_with_lines()
        self.assertTrue(campaign.campaign_lead_ids)

        res = campaign.action_generate_all_content()
        self.assertTrue(campaign.content_gen_enabled)
        self.assertEqual(res['tag'], 'display_notification')
        cron = self.env.ref(
            'crm_marketing_and_comunications.ir_cron_marketing_content_gen')
        self.assertTrue(cron.active)

        campaign.action_stop_content_gen()
        self.assertFalse(campaign.content_gen_enabled)

    def test_content_gen_batch_finishes_when_nothing_pending(self):
        campaign = self._bare_email_campaign()
        campaign.content_gen_enabled = True
        self.assertFalse(campaign._content_gen_process_batch())
        self.assertFalse(campaign.content_gen_enabled)

    def test_generate_all_content_requires_template(self):
        campaign = self._bare_email_campaign()
        lead = self.env['crm.lead'].create({
            'name': 'NT', 'partner_name': 'NT', 'email_from': 'nt@example.com',
            'type': 'opportunity', 'probability': 40,
        })
        campaign.lead_ids = [(4, lead.id)]
        campaign.action_generate_campaign_leads()
        with self.assertRaises(UserError):
            campaign.action_generate_all_content()

    # ── No auto-clasificar las notas del propio sistema ─────────────────────

    def _one_line(self):
        campaign = self._campaign_with_lines(n=1)
        return campaign.campaign_lead_ids[0]

    def test_system_notification_is_not_treated_as_reply(self):
        line = self._one_line()
        line.message_post(body='Email enviado a bg0@example.com.',
                          message_type='notification')
        self.assertFalse(line.replied_at)
        self.assertFalse(line.last_inbound_message)
        self.assertEqual(line.ai_classification, 'no_response')

    def test_internal_user_comment_is_not_a_reply(self):
        line = self._one_line()
        line.message_post(body='nota interna del comercial', message_type='comment')
        self.assertFalse(line.replied_at)

    def test_regenerate_all_content_discards_and_requeues(self):
        campaign = self._campaign_with_lines(n=2)
        line = campaign.campaign_lead_ids[0]
        line.write({'personalized_content': '<p>viejo</p>',
                    'personalized_opening': 'viejo',
                    'content_approved': True})
        campaign.campaign_lead_ids[1].write({
            'personalized_content': '<p>viejo2</p>', 'content_approved': True})

        # "Generar" no hace nada: ya tienen contenido
        with self.assertRaises(UserError):
            campaign.action_generate_all_content()

        # "Regenerar" descarta y reencola
        campaign.action_regenerate_all_content()
        self.assertTrue(campaign.content_gen_enabled)
        self.assertFalse(any(campaign.campaign_lead_ids.mapped('personalized_content')))
        self.assertFalse(any(campaign.campaign_lead_ids.mapped('content_approved')))
        self.assertEqual(campaign.content_gen_pending, 2)

    def test_regenerate_all_content_skips_sent_lines(self):
        campaign = self._campaign_with_lines(n=2)
        sent, pending = campaign.campaign_lead_ids
        sent.write({'personalized_content': '<p>ya enviado</p>',
                    'content_approved': True, 'sent_at': self.env.cr.now()})
        pending.write({'personalized_content': '<p>pendiente</p>',
                       'content_approved': True})

        campaign.action_regenerate_all_content()
        self.assertEqual(sent.personalized_content, '<p>ya enviado</p>',
                         'una línea ya enviada no se regenera')
        self.assertFalse(pending.personalized_content)

    def test_dedupe_links_keeps_first_only(self):
        tpl = self.env['marketing.ai.template']
        url = 'https://tidycal.com/enric/reunion'
        html = (f'<p>hola</p><a href="{url}">Agendar</a>'
                f'<div><a href="{url}">Agendar</a></div>'
                f'<a href="{url}">Agendar</a>')
        nuevo, quitados = tpl._dedupe_links(html, url)
        self.assertEqual(quitados, 2)
        self.assertEqual(nuevo.count(f'href="{url}"'), 1)
