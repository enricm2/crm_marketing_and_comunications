from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError
from unittest.mock import patch


class TestGmailSyncMultiuser(TransactionCase):
    """Pruebas unitarias para la sincronización multiusuario de Gmail."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        # Crear dos usuarios comerciales
        cls.user_salesman_1 = cls.env['res.users'].create({
            'name': 'Comercial 1',
            'login': 'comercial1@example.com',
            'email': 'comercial1@example.com',
            'crm_imap_host': 'imap.gmail.com',
            'crm_imap_user': 'comercial1@gmail.com',
            'crm_imap_password': 'pass1234password',
            'crm_imap_sync_folder': 'Etiqueta1',
        })

        cls.user_salesman_2 = cls.env['res.users'].create({
            'name': 'Comercial 2',
            'login': 'comercial2@example.com',
            'email': 'comercial2@example.com',
            'crm_imap_host': 'imap.gmail.com',
            'crm_imap_user': 'comercial2@gmail.com',
            'crm_imap_password': '',  # Configuración incompleta sin contraseña
        })

        # Crear leads asociados a los comerciales
        cls.lead_1 = cls.env['crm.lead'].create({
            'name': 'Lead de Comercial 1',
            'user_id': cls.user_salesman_1.id,
            'email_from': 'cliente1@example.com',
            'gmail_sync_enabled': True,
        })

        cls.lead_2 = cls.env['crm.lead'].create({
            'name': 'Lead de Comercial 2',
            'user_id': cls.user_salesman_2.id,
            'email_from': 'cliente2@example.com',
            'gmail_sync_enabled': True,
        })

        cls.lead_no_user = cls.env['crm.lead'].create({
            'name': 'Lead sin Comercial',
            'user_id': False,
            'email_from': 'cliente3@example.com',
            'gmail_sync_enabled': True,
        })

    def test_01_imap_config_ok(self):
        """Verifica la validación de configuración IMAP para cada usuario."""
        # Comercial 1 tiene configuración completa
        self.assertTrue(self.env['crm.lead']._gmail_imap_config_ok(self.user_salesman_1))
        
        # Comercial 2 tiene configuración incompleta (falta password)
        self.assertFalse(self.env['crm.lead']._gmail_imap_config_ok(self.user_salesman_2))

    def test_02_gmail_sync_folder(self):
        """Verifica que la carpeta a sincronizar se resuelva según el comercial del lead."""
        # Lead de Comercial 1 debe usar 'Etiqueta1'
        self.assertEqual(self.lead_1._gmail_sync_folder(), 'Etiqueta1')

        # Lead de Comercial 2 debe usar la del comercial (vacío por defecto)
        self.assertEqual(self.lead_2._gmail_sync_folder(), '')

    def test_03_get_imap_config_error(self):
        """Verifica que se lance una excepción si las credenciales del usuario están incompletas."""
        # Para Comercial 1 no debe dar error y debe retornar sus credenciales
        host, user_email, password, folder_sent, folder_inbox = self.lead_1._get_imap_config(self.user_salesman_1)
        self.assertEqual(host, 'imap.gmail.com')
        self.assertEqual(user_email, 'comercial1@gmail.com')
        self.assertEqual(password, 'pass1234password')
        self.assertEqual(folder_sent, '[Gmail]/Sent Mail')
        self.assertEqual(folder_inbox, 'INBOX')

        # Para Comercial 2 debe lanzar UserError por falta de contraseña
        with self.assertRaises(UserError):
            self.lead_2._get_imap_config(self.user_salesman_2)

    @patch('odoo.addons.crm_marketing_and_comunications.models.crm_lead.imaplib.IMAP4_SSL')
    def test_04_gmail_sync_body_peek(self, mock_imap_class):
        """Verifica que la sincronización use BODY.PEEK[] para evitar marcar correos como leídos."""
        mock_imap = mock_imap_class.return_value
        mock_imap.select.return_value = ('OK', [b'1'])
        mock_imap.search.return_value = ('OK', [b'123'])
        
        dummy_msg = (
            b'Subject: Test Subject\n'
            b'From: cliente1@example.com\n'
            b'To: comercial1@gmail.com\n'
            b'Date: Thu, 17 Sep 2026 08:00:00 +0000\n'
            b'Message-ID: <abc@def>\n\n'
            b'This is a test message.'
        )
        mock_imap.fetch.return_value = ('OK', [(b'123 (BODY[] {123}', dummy_msg), b')'])
        
        # Eliminar comunicaciones previas del lead para asegurar que se crea la nueva
        self.env['crm.communication'].search([('lead_id', '=', self.lead_1.id)]).unlink()
        
        res = self.lead_1._sync_gmail(user=self.user_salesman_1)
        
        self.assertEqual(res['created'], 1)
        self.assertEqual(res['skipped'], 0)
        
        # Verificar que select se llamó con readonly=True y fetch con BODY.PEEK[]
        mock_imap.select.assert_any_call('Etiqueta1', readonly=True)
        mock_imap.fetch.assert_called_with(b'123', '(BODY.PEEK[])')
