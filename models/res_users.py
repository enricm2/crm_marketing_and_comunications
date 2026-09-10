import imaplib
from odoo import models, fields
from odoo.exceptions import UserError


class ResUsers(models.Model):
    _inherit = 'res.users'

    crm_imap_host = fields.Char(
        string='Servidor IMAP',
        default='imap.gmail.com',
        help='Servidor IMAP de tu cuenta de correo, e.g. imap.gmail.com'
    )
    crm_imap_user = fields.Char(
        string='Usuario / Email Gmail',
        help='Tu dirección de correo electrónico, e.g. tuempresa@gmail.com'
    )
    crm_imap_password = fields.Char(
        string='Contraseña de aplicación',
        help='Contraseña de aplicación generada desde la configuración de seguridad de tu cuenta de Google.'
    )
    crm_imap_folder_sent = fields.Char(
        string='Carpeta Enviados',
        default='[Gmail]/Sent Mail',
        help='Etiqueta o carpeta de Gmail para correos enviados.'
    )
    crm_imap_folder_inbox = fields.Char(
        string='Carpeta Recibidos',
        default='INBOX',
        help='Etiqueta o carpeta de Gmail para correos recibidos.'
    )
    crm_imap_sync_folder = fields.Char(
        string='Etiqueta a sincronizar',
        help='Etiqueta de Gmail opcional a la que restringir la sincronización.'
    )

    def action_test_crm_gmail_connection(self):
        """Prueba la conexión IMAP con las credenciales configuradas para este usuario."""
        self.ensure_one()
        if not all([self.crm_imap_host, self.crm_imap_user, self.crm_imap_password]):
            raise UserError(
                'Por favor, rellena los campos de Servidor IMAP, Usuario y Contraseña de aplicación antes de probar la conexión.'
            )

        try:
            mail = imaplib.IMAP4_SSL(self.crm_imap_host)
            mail.login(self.crm_imap_user, self.crm_imap_password)
            mail.logout()
        except Exception as exc:
            raise UserError(f'La conexión ha fallado:\n{exc}')

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Conexión Exitosa',
                'message': f'Se ha establecido conexión con Gmail (IMAP) para {self.crm_imap_user} correctamente.',
                'type': 'success',
                'sticky': False,
            }
        }
