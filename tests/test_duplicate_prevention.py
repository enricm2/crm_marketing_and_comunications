from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)

class TestDuplicatePrevention(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Find or create email channel
        cls.channel_email = cls.env['marketing.channel'].search([('code', '=', 'email')], limit=1)
        if not cls.channel_email:
            cls.channel_email = cls.env['marketing.channel'].create({
                'name': 'Email Marketing',
                'code': 'email',
            })

        # Create a campaign
        cls.campaign = cls.env['marketing.campaign'].create({
            'name': 'Test Duplicate Campaign',
            'purpose': 'Test duplicate prevention logic',
            'target_audience': 'Uniasser testing',
            'channel_ids': [(4, cls.channel_email.id)],
        })

        # Create multiple leads with same email
        cls.lead_1 = cls.env['crm.lead'].create({
            'name': 'Lead 1',
            'partner_name': 'Company 1',
            'email_from': 'duplicate_test@example.com',
        })
        cls.lead_2 = cls.env['crm.lead'].create({
            'name': 'Lead 2',
            'partner_name': 'Company 2',
            'email_from': 'duplicate_test@example.com',
        })
        cls.lead_3 = cls.env['crm.lead'].create({
            'name': 'Lead 3',
            'partner_name': 'Company 3',
            'email_from': 'other_test@example.com',
        })

    def test_01_action_generate_campaign_leads_excludes_duplicates(self):
        """Verificar que al sincronizar las líneas de campaña, los correos duplicados se excluyen."""
        # Asociar los leads a la campaña
        self.campaign.write({
            'lead_ids': [(6, 0, [self.lead_1.id, self.lead_2.id, self.lead_3.id])]
        })

        # Generar las líneas de campaña
        self.campaign.action_generate_campaign_leads()

        # Obtener las líneas generadas
        cl_1 = self.env['marketing.campaign.lead'].search([
            ('campaign_id', '=', self.campaign.id),
            ('lead_id', '=', self.lead_1.id),
        ])
        cl_2 = self.env['marketing.campaign.lead'].search([
            ('campaign_id', '=', self.campaign.id),
            ('lead_id', '=', self.lead_2.id),
        ])
        cl_3 = self.env['marketing.campaign.lead'].search([
            ('campaign_id', '=', self.campaign.id),
            ('lead_id', '=', self.lead_3.id),
        ])

        self.assertTrue(cl_1, "Debería existir la línea para el Lead 1")
        self.assertTrue(cl_2, "Debería existir la línea para el Lead 2")
        self.assertTrue(cl_3, "Debería existir la línea para el Lead 3")

        # El primero procesado no debe estar excluido (exclusion_reason = 'none')
        # El segundo con el mismo email debe estar excluido como 'duplicate'
        # El tercero con diferente email no debe estar excluido
        results = {cl_1.exclusion_reason, cl_2.exclusion_reason}
        self.assertIn('none', results, "Al menos uno de los leads con correo duplicado debe tener 'none'")
        self.assertIn('duplicate', results, "Al menos uno de los leads con correo duplicado debe tener 'duplicate'")
        self.assertEqual(cl_3.exclusion_reason, 'none', "El lead con correo único no debe estar excluido")

    def test_02_send_guard_blocks_duplicate_sends(self):
        """Verificar que el guardián de envíos bloquea el envío si ya existe un correo enviado en la misma campaña."""
        # Forzar la creación de dos líneas sin exclusión con el mismo correo electrónico
        campaign_2 = self.env['marketing.campaign'].create({
            'name': 'Test Guard Campaign',
            'purpose': 'Testing send guard',
            'target_audience': 'Uniasser testing',
            'channel_ids': [(4, self.channel_email.id)],
        })
        
        cl_a = self.env['marketing.campaign.lead'].create({
            'campaign_id': campaign_2.id,
            'lead_id': self.lead_1.id,
            'channel_used': 'email',
            'exclusion_reason': 'none',
        })
        cl_b = self.env['marketing.campaign.lead'].create({
            'campaign_id': campaign_2.id,
            'lead_id': self.lead_2.id,
            'channel_used': 'email',
            'exclusion_reason': 'none',
        })

        # Comprobar que inicialmente ambos están permitidos
        guard = self.env['marketing.send.guard']
        allowed, reason = guard.check(cl_a)
        self.assertTrue(allowed, "Inicialmente cl_a debería estar permitido")

        # Marcar cl_a como enviado
        cl_a.write({'sent_at': '2026-09-07 12:00:00'})

        # Comprobar que cl_b ahora es rechazado por duplicado
        allowed, reason = guard.check(cl_b)
        self.assertFalse(allowed, "cl_b debería ser bloqueado debido a que cl_a ya fue enviado")
        self.assertEqual(cl_b.exclusion_reason, 'duplicate', "cl_b debería tener motivo de exclusión 'duplicate'")

    def test_03_opt_out_synchronization_and_campaign_exclusion(self):
        """Verificar la sincronización bidireccional de la Baja de Marketing / RGPD y su exclusión en campañas."""
        # 1. Crear un lead y marcarlo manualmente con marketing_opt_out = True
        opt_out_lead = self.env['crm.lead'].create({
            'name': 'Lead Opt-Out Manual',
            'partner_name': 'Company Opt-Out',
            'email_from': 'manual_optout@example.com',
            'marketing_opt_out': True,
        })

        # Verificar que se creó la exclusión RGPD correspondiente
        exclusion = self.env['marketing.rgpd.exclusion'].search([
            ('partner_email', '=', 'manual_optout@example.com'),
        ])
        self.assertTrue(exclusion, "Debería crearse un registro en marketing.rgpd.exclusion automáticamente")

        # 2. Registrar baja en la lista de exclusión RGPD directamente
        other_lead = self.env['crm.lead'].create({
            'name': 'Lead Opt-Out Automatico',
            'partner_name': 'Company Auto-Opt-Out',
            'email_from': 'auto_optout@example.com',
        })
        self.assertFalse(other_lead.marketing_opt_out, "Inicialmente no debería tener la baja de marketing marcada")

        self.env['marketing.rgpd.exclusion'].add_exclusion(
            email='auto_optout@example.com',
            reason='unsubscribe_email',
        )

        # Verificar que se sincronizó la baja al lead del CRM de forma automática
        other_lead.invalidate_recordset(['marketing_opt_out'])
        self.assertTrue(other_lead.marketing_opt_out, "El lead en CRM debería haberse marcado con marketing_opt_out = True")

        # 3. Intentar añadir este lead a una campaña y verificar que se genera como excluido 'rgpd_internal'
        campaign_3 = self.env['marketing.campaign'].create({
            'name': 'Test Campaign Opt-Out Exclusion',
            'purpose': 'Testing opt-out campaign exclusion',
            'target_audience': 'Uniasser testing',
            'channel_ids': [(4, self.channel_email.id)],
        })
        
        campaign_3.write({
            'lead_ids': [(6, 0, [other_lead.id])]
        })
        campaign_3.action_generate_campaign_leads()

        cl_line = self.env['marketing.campaign.lead'].search([
            ('campaign_id', '=', campaign_3.id),
            ('lead_id', '=', other_lead.id),
        ])
        self.assertTrue(cl_line, "La línea de campaña debería haberse creado")
        self.assertEqual(cl_line.exclusion_reason, 'rgpd_internal', "El lead con baja solicitada debe ser excluido con motivo 'rgpd_internal' inmediatamente")

