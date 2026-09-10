from odoo import models, fields, api
from odoo.exceptions import UserError


ALLOWED_MIMETYPES = frozenset([
    'application/pdf',
    'application/msword',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/vnd.ms-powerpoint',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    'application/vnd.ms-excel',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
])

ACTION_TYPE_SELECTION = [
    ('email_sent', 'Email enviado'),
    ('email_received', 'Email recibido'),
    ('call', 'Llamada telefónica'),
    ('meeting', 'Reunión'),
    ('note', 'Nota interna'),
    ('document', 'Documento enviado'),
    ('whatsapp_sent', 'WhatsApp enviado'),
    ('whatsapp_received', 'WhatsApp recibido'),
    ('other', 'Otro'),
]


class CrmCommunication(models.Model):
    _name = 'crm.communication'
    _description = 'Historial de Comunicación CRM'
    _order = 'date desc, id desc'

    lead_id = fields.Many2one(
        'crm.lead',
        string='Oportunidad / Lead',
        required=True,
        ondelete='cascade',
        index=True,
    )
    date = fields.Datetime(
        string='Fecha',
        default=fields.Datetime.now,
        required=True,
    )
    action_type = fields.Selection(
        ACTION_TYPE_SELECTION,
        string='Tipo',
        default='email_sent',
        required=True,
    )
    subject = fields.Char(string='Asunto', required=True)
    user_id = fields.Many2one(
        'res.users',
        string='Responsable interno',
        default=lambda self: self.env.user,
        required=True,
    )
    partner_id = fields.Many2one(
        'res.partner',
        string='Contacto cliente',
        help='Persona del lado del cliente con la que se realizó el contacto.',
    )
    description = fields.Html(
        string='Descripción / Cuerpo',
        sanitize=True,
        sanitize_tags=False,   # mantener etiquetas Gmail (<table>, <div>, etc.)
        sanitize_style=False,  # mantener estilos inline (colores, fuentes del email)
        strip_style=False,
    )
    email_template_id = fields.Many2one(
        'crm.email.template',
        string='Plantilla de email',
        help='Al aplicarla genera el cuerpo con el logo, los colores y el '
             'recuadro definidos en la plantilla. Después puedes seguir '
             'editando el texto a mano si hace falta.',
    )
    attachment_ids = fields.Many2many(
        'ir.attachment',
        'crm_communication_attachment_rel',
        'communication_id',
        'attachment_id',
        string='Documentos adjuntos',
    )

    @api.onchange('email_template_id')
    def _onchange_email_template_id(self):
        """Al elegir plantilla, vuelca su contenido en el cuerpo.

        Se hace por onchange y no con un botón porque un botón `type="object"`
        dentro de un diálogo (`target: 'new'`) CIERRA el diálogo al devolver
        True, que es justo lo que pasaba: elegías plantilla, pulsabas aplicar y
        te devolvía a la ficha del lead perdiendo lo escrito.

        Solo rellena si el cuerpo está vacío: si ya has escrito algo, no se
        pisa en silencio. Para sobrescribir a propósito está el botón.
        """
        for rec in self:
            if not rec.email_template_id:
                continue
            cuerpo = (rec.description or '').strip()
            # Odoo deja '<p><br></p>' al vaciar el editor: eso es "vacío"
            vacio = cuerpo in ('', '<p><br></p>', '<p></p>', '<br>')
            if vacio:
                rec.description = rec.email_template_id.render_html(rec.lead_id)

    def action_apply_email_template(self):
        """Sobrescribe el cuerpo con la plantilla, aunque ya hubiera texto.

        Devuelve una acción que reabre este mismo registro en el diálogo. Si
        devolviera True, Odoo cerraría el diálogo y perderías el trabajo.
        """
        self.ensure_one()
        if not self.email_template_id:
            raise UserError('Selecciona primero una plantilla de email.')
        self.description = self.email_template_id.render_html(self.lead_id)
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
            'context': self.env.context,
        }
    attachment_count = fields.Integer(
        string='Adjuntos',
        compute='_compute_attachment_count',
    )
    email_sent_ok = fields.Boolean(
        string='Email enviado',
        default=False,
        copy=False,
    )
    recipient_email = fields.Char(
        string='Email destinatario',
        help='Dirección de email a la que se enviará el mensaje. '
             'Se rellena automáticamente del lead, pero puedes modificarla.',
        copy=False,
    )
    message_id = fields.Char(
        string='Message-ID (email)',
        index=True,
        copy=False,
        help='Identificador único del correo en Gmail. Se usa para no importar '
             'dos veces el mismo email en la sincronización automática.',
    )

    # ── Campos WhatsApp ───────────────────────────────────────────────────────
    wa_account_id = fields.Many2one(
        'whatsapp.account',
        string='Cuenta WhatsApp',
        domain="[('company_id', '=', company_id)]",
        copy=False,
    )
    wa_template_id = fields.Many2one(
        'whatsapp.template',
        string='Plantilla WhatsApp',
        domain="[('account_id', '=', wa_account_id), ('status', '=', 'approved')]",
        copy=False,
    )
    wa_body = fields.Text(string='Mensaje WhatsApp', copy=False)
    wa_send_mode = fields.Selection(
        [('text', 'Texto libre'), ('template', 'Plantilla')],
        string='Modo envío WA', default='text',
    )
    wa_sent_ok = fields.Boolean(string='WhatsApp enviado', default=False, copy=False)
    company_id = fields.Many2one(
        related='lead_id.company_id', store=False,
    )

    # Campos relacionados del lead para facilitar vistas
    lead_partner_id = fields.Many2one(
        related='lead_id.partner_id',
        string='Cliente',
        store=False,
    )

    @api.depends('attachment_ids')
    def _compute_attachment_count(self):
        for rec in self:
            rec.attachment_count = len(rec.attachment_ids)

    @api.onchange('lead_id', 'partner_id')
    def _onchange_recipient_email(self):
        """Auto-rellena el email del destinatario desde el lead o el contacto."""
        for rec in self:
            if rec.recipient_email:
                continue
            # Prioridad: email_from del lead > email del partner seleccionado > email del partner del lead
            partner = rec.partner_id or rec.lead_id.partner_id
            rec.recipient_email = (
                rec.lead_id.email_from
                or (partner.email if partner else False)
            )

    def action_send_email(self):
        self.ensure_one()
        if not self.recipient_email:
            raise UserError(
                'El campo "Email destinatario" está vacío. '
                'Introduce una dirección de email antes de enviar.'
            )
        invalid = [
            att.name
            for att in self.attachment_ids
            if att.mimetype not in ALLOWED_MIMETYPES
        ]
        if invalid:
            allowed_str = 'PDF, Word (.doc/.docx), PowerPoint (.ppt/.pptx), Excel (.xls/.xlsx)'
            raise UserError(
                f'Los siguientes adjuntos tienen un formato no permitido: {", ".join(invalid)}.\n'
                f'Tipos permitidos: {allowed_str}.'
            )
        # Construir email via mail.mail para enviar a dirección libre (no solo partners)
        mail_values = {
            'subject': self.subject,
            'body_html': self.description or '',
            'email_to': self.recipient_email,
            'auto_delete': False,
            'res_id': self.lead_id.id,
            'model': 'crm.lead',
        }
        if self.attachment_ids:
            mail_values['attachment_ids'] = [(6, 0, self.attachment_ids.ids)]
        mail = self.env['mail.mail'].create(mail_values)
        mail.send()
        # Registrar también en el chatter del lead
        self.lead_id.message_post(
            body=self.description or f'<p>Email enviado a {self.recipient_email}</p>',
            subject=self.subject,
            message_type='email',
            subtype_xmlid='mail.mt_note',
            attachment_ids=self.attachment_ids.ids,
        )
        self.email_sent_ok = True
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Email enviado',
                'message': f'Email enviado a {self.recipient_email}',
                'type': 'success',
                'sticky': False,
            },
        }

    def action_send_whatsapp(self):
        """Envía el mensaje WhatsApp y registra el resultado."""
        self.ensure_one()
        if not self.wa_account_id:
            from odoo.exceptions import UserError
            raise UserError('Selecciona una cuenta WhatsApp.')

        partner = self.partner_id or self.lead_id.partner_id
        phone = partner.phone if partner else ''
        if not phone:
            from odoo.exceptions import UserError
            raise UserError('El contacto no tiene teléfono registrado.')

        from odoo.addons.uniasser_whatsapp.models.whatsapp_api import WhatsAppAPI
        normalized = WhatsAppAPI.normalize_phone(phone)
        account = self.wa_account_id
        is_qr = account.connection_type == 'qr_code'

        if self.wa_send_mode == 'template':
            if not self.wa_template_id:
                from odoo.exceptions import UserError
                raise UserError('Selecciona una plantilla.')
            if is_qr:
                result = account._evo_send_text(normalized, self.wa_template_id.body_text or '')
                wa_id = (result.get('key') or {}).get('id', '')
            else:
                api = account._get_api()
                result = api.send_template(normalized, self.wa_template_id.name,
                                           self.wa_template_id.language_code or 'es')
                wa_id = result.get('messages', [{}])[0].get('id')
            body_saved = f'[Plantilla: {self.wa_template_id.name}]'
        else:
            if not self.wa_body:
                from odoo.exceptions import UserError
                raise UserError('Escribe un mensaje antes de enviar.')
            if is_qr:
                result = account._evo_send_text(normalized, self.wa_body)
                wa_id = (result.get('key') or {}).get('id', '')
            else:
                api = account._get_api()
                result = api.send_text(normalized, self.wa_body)
                wa_id = result.get('messages', [{}])[0].get('id')
            body_saved = self.wa_body

        # Guardar mensaje en historial WhatsApp
        msg = self.env['whatsapp.message'].create({
            'account_id': account.id,
            'partner_id': partner.id if partner else False,
            'lead_id': self.lead_id.id,
            'wa_message_id': wa_id,
            'direction': 'outbound',
            'body': body_saved,
            'message_type': 'template' if self.wa_send_mode == 'template' else 'text',
            'status': 'sent',
            'phone': normalized,
            'template_id': self.wa_template_id.id if self.wa_template_id else False,
        })
        msg._post_to_chatter()
        self.wa_sent_ok = True
        self.action_type = 'whatsapp_sent'

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'WhatsApp enviado',
                'message': f'Mensaje enviado a {phone}',
                'type': 'success',
                'sticky': False,
            },
        }

    def action_type_label(self):
        """Devuelve la etiqueta del tipo de acción."""
        return dict(ACTION_TYPE_SELECTION).get(self.action_type, '')
