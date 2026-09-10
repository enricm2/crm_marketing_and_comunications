from odoo.tests.common import TransactionCase
from unittest.mock import patch

class TestInboundClassification(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Create a campaign and a lead to test with
        cls.campaign = cls.env['marketing.campaign'].create({
            'name': 'Test Campaign',
            'purpose': 'Test B2B marketing campaign',
            'target_audience': 'Test Target Segment',
        })
        cls.lead = cls.env['crm.lead'].create({
            'name': 'Test Lead Partner',
            'partner_name': 'Test Lead Partner',
            'email_from': 'test_lead@example.com',
        })
        cls.campaign_lead = cls.env['marketing.campaign.lead'].create({
            'campaign_id': cls.campaign.id,
            'lead_id': cls.lead.id,
        })

    def test_message_post_inbound_capture(self):
        # Verify that posting a message with author matching the lead's email or not a res.users partner
        # updates last_inbound_message and triggers classification.
        
        # Let's mock action_classify_response so we don't call actual Claude/OpenAI APIs during unit tests
        with patch('odoo.addons.crm_marketing_and_comunications.models.marketing_campaign_lead.MarketingCampaignLead.action_classify_response') as mock_classify:
            # Post a message from an external author (not a user partner)
            external_partner = self.env['res.partner'].create({
                'name': 'External Author',
                'email': 'external@example.com',
            })
            
            self.campaign_lead.message_post(
                body='<p>Me interesa mucho vuestro servicio, por favor, llamadme cuanto antes.</p>',
                author_id=external_partner.id,
            )
            
            self.assertEqual(self.campaign_lead.last_inbound_message, 'Me interesa mucho vuestro servicio, por favor, llamadme cuanto antes.')
            self.assertTrue(self.campaign_lead.last_inbound_at)
            self.assertTrue(self.campaign_lead.replied_at)
            mock_classify.assert_called_once()
