import base64
import email
import imaplib
import logging
import re
from email.utils import parseaddr

from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class CrmLead(models.Model):
    _inherit = 'crm.lead'

    communication_ids = fields.One2many(
        'crm.communication',
        'lead_id',
        string='Historial de comunicaciones',
    )
    communication_count = fields.Integer(
        string='Comunicaciones',
        compute='_compute_communication_count',
    )

    def _compute_communication_count(self):
        for lead in self:
            lead.communication_count = len(lead.communication_ids)

    def action_open_communications(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Comunicaciones — {self.name}',
            'res_model': 'crm.communication',
            'view_mode': 'list,form',
            'domain': [('lead_id', '=', self.id)],
            'context': {'default_lead_id': self.id},
        }

    def action_new_email_communication(self):
        """Abre una comunicación nueva de tipo email con el destinatario puesto.

        El email se pasa por contexto en lugar de fiarlo al onchange existente:
        ese solo dispara cuando cambia `lead_id` en el formulario, y aquí el
        registro nace ya con el lead asignado, así que no llegaría a saltar.

        Prioridad del destinatario: email del lead > email del contacto asociado.
        """
        self.ensure_one()
        destinatario = (self.email_from or '').strip()
        if not destinatario and self.partner_id:
            destinatario = (self.partner_id.email or '').strip()

        return {
            'type': 'ir.actions.act_window',
            'name': f'Nuevo email — {self.partner_name or self.name}',
            'res_model': 'crm.communication',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_lead_id': self.id,
                'default_action_type': 'email_sent',
                'default_recipient_email': destinatario,
                'default_partner_id': self.partner_id.id if self.partner_id else False,
            },
        }

    # ── Gmail sync ─────────────────────────────────────────────────────────────

    gmail_sync_enabled = fields.Boolean(
        string='Sincronizar Gmail automáticamente', default=True,
        help='Si está activo, el proceso en segundo plano (cada 10 min) busca '
             'en Gmail correos de/para el email de este lead y los añade al '
             'historial de comunicaciones.',
    )
    gmail_last_sync = fields.Datetime(
        string='Última sincronización Gmail', readonly=True, copy=False,
    )

    def _get_imap_config(self, user=None):
        """Devuelve (host, user, password, folder_sent, folder_inbox) desde la configuración del usuario."""
        if not user:
            user = self.env.user
        host = user.crm_imap_host
        user_email = user.crm_imap_user
        password = user.crm_imap_password
        folder_sent = user.crm_imap_folder_sent or '[Gmail]/Sent Mail'
        folder_inbox = user.crm_imap_folder_inbox or 'INBOX'
        if not all([host, user_email, password]):
            raise UserError(
                f'El usuario {user.name} no tiene configurada la sincronización de Gmail.\n'
                'Ve a Mi Perfil (o Ajustes de Usuario) y configura el servidor IMAP, '
                'usuario y contraseña de aplicación.'
            )
        return host, user_email, password, folder_sent, folder_inbox

    @api.model
    def _gmail_imap_config_ok(self, user=None):
        """¿Están puestas las 3 credenciales mínimas? (sin lanzar excepción)."""
        if not user:
            user = self.env.user
        return all([user.crm_imap_host, user.crm_imap_user, user.crm_imap_password])

    def _gmail_sync_folder(self):
        """Etiqueta/carpeta de Gmail a la que restringir la búsqueda de ESTE lead.

        Prioridad: etiqueta de la campaña (la de la campaña modificada más
        recientemente si hay varias) → etiqueta de sincronización del comercial → todo el buzón.
        """
        self.ensure_one()
        campana = self.env['marketing.campaign'].sudo().search([
            ('campaign_lead_ids.lead_id', '=', self.id),
            ('gmail_sync_label', '!=', False),
        ], order='write_date desc', limit=1)
        etiqueta = (campana.gmail_sync_label or '').strip() if campana else ''
        if etiqueta:
            return etiqueta
        user = self.user_id or self.env.user
        return (user.crm_imap_sync_folder or '').strip()

    def _gmail_imap_connect(self, user=None):
        """Abre y autentica una conexión IMAP. El llamador hace logout()."""
        if not user:
            user = self.user_id or self.env.user
        host, user_email, password, _fs, _fi = self._get_imap_config(user)
        try:
            mail = imaplib.IMAP4_SSL(host)
            mail.login(user_email, password)
        except Exception as exc:
            raise UserError(f'No se pudo conectar a Gmail (IMAP) para {user.name}: {exc}')
        return mail

    @staticmethod
    def _gmail_all_mail_folders(mail, folder_inbox, folder_sent, diag):
        """Carpetas a barrer cuando no se pide una etiqueta concreta."""
        for cand in ('[Gmail]/All Mail', '[Gmail]/Todos'):
            try:
                st, _ = mail.select(cand, readonly=True)
                if st == 'OK':
                    diag.append(f'✓ {cand} (todo el buzón)')
                    return [cand]
            except Exception:
                pass
        diag.append('⚠ Sin [Gmail]/All Mail: se usa INBOX + Enviados')
        return [folder_inbox, folder_sent, '[Gmail]/Enviados']

    @staticmethod
    def _extract_email_address(header_value):
        """Extrae la dirección de email pura de un campo From/To (ignora el nombre)."""
        if not header_value:
            return ''
        _, addr = parseaddr(header_value)
        return addr.lower().strip()

    @staticmethod
    def _decode_header(value):
        """Decodifica cabecera de email (puede ser bytes o str codificado)."""
        if not value:
            return ''
        parts = email.header.decode_header(value)
        decoded = []
        for chunk, charset in parts:
            if isinstance(chunk, bytes):
                try:
                    decoded.append(chunk.decode(charset or 'utf-8', errors='replace'))
                except Exception:
                    decoded.append(chunk.decode('latin-1', errors='replace'))
            else:
                decoded.append(chunk)
        return ' '.join(decoded)

    @staticmethod
    def _parse_date(date_str):
        """Convierte la cabecera Date del email a datetime UTC sin tzinfo."""
        from email.utils import parsedate_to_datetime
        import pytz
        try:
            dt = parsedate_to_datetime(date_str)
            if dt.tzinfo:
                dt = dt.astimezone(pytz.utc).replace(tzinfo=None)
            return dt
        except Exception:
            return None

    @staticmethod
    def _extract_body_and_attachments(msg):
        """
        Extrae el cuerpo (html preferido, fallback text) y los adjuntos de un email.
        Devuelve (body_text, body_html, [(filename, data_bytes, mimetype), ...])
        """
        import base64 as _b64
        body_text = ''
        body_html = ''
        attachments = []

        if msg.is_multipart():
            for part in msg.walk():
                ct = part.get_content_type()
                cd = part.get('Content-Disposition', '')
                fname = part.get_filename()

                # Adjunto explícito o parte con nombre de archivo
                if fname or 'attachment' in cd:
                    fname = fname or f'adjunto_{len(attachments)+1}'
                    # Decodificar nombre si está codificado
                    decoded_parts = email.header.decode_header(fname)
                    fname_clean = ''
                    for chunk, charset in decoded_parts:
                        if isinstance(chunk, bytes):
                            fname_clean += chunk.decode(charset or 'utf-8', errors='replace')
                        else:
                            fname_clean += chunk
                    try:
                        data = part.get_payload(decode=True)
                        if data:
                            attachments.append((fname_clean, data, ct or 'application/octet-stream'))
                    except Exception:
                        pass
                    continue

                # Partes de cuerpo (inline)
                if ct == 'text/html' and not body_html:
                    try:
                        payload = part.get_payload(decode=True)
                        charset = part.get_content_charset() or 'utf-8'
                        body_html = payload.decode(charset, errors='replace')
                    except Exception:
                        pass
                elif ct == 'text/plain' and not body_text:
                    try:
                        payload = part.get_payload(decode=True)
                        charset = part.get_content_charset() or 'utf-8'
                        body_text = payload.decode(charset, errors='replace')
                    except Exception:
                        pass
        else:
            # Mensaje simple (no multipart)
            ct = msg.get_content_type()
            try:
                payload = msg.get_payload(decode=True)
                charset = msg.get_content_charset() or 'utf-8'
                if ct == 'text/html':
                    body_html = payload.decode(charset, errors='replace')
                else:
                    body_text = payload.decode(charset, errors='replace')
            except Exception:
                pass

        return body_text, body_html, attachments

    @staticmethod
    def _extract_html_body(html):
        """
        Si el HTML es un documento completo (<html>...</html>), extrae solo el
        contenido del <body> para evitar que el sanitizador de Odoo elimine
        cabeceras <head>/<style> y deje el campo vacío.
        """
        if not html:
            return html
        try:
            from lxml import etree
            doc = etree.fromstring(
                html.encode('utf-8') if isinstance(html, str) else html,
                parser=etree.HTMLParser(encoding='utf-8'),
            )
            body = doc.find('.//body')
            if body is not None:
                # Serializar hijos del body
                parts = []
                if body.text:
                    parts.append(body.text)
                for child in body:
                    parts.append(etree.tostring(child, encoding='unicode', method='html'))
                return ''.join(parts)
        except Exception:
            pass
        return html

    @staticmethod
    def _imap_search(mail, criteria):
        """Ejecuta búsqueda IMAP y devuelve lista de IDs o []."""
        try:
            _, msg_ids = mail.search(None, criteria)
            if msg_ids and msg_ids[0]:
                return msg_ids[0].split()
        except Exception as exc:
            _logger.debug('IMAP search error (%s): %s', criteria, exc)
        return []

    def _sync_gmail(self, folder=None, mail=None, user=None):
        """Motor de sincronización de Gmail para UN lead. Reutilizable por el
        botón manual y por el cron.

        :param folder: etiqueta/carpeta Gmail a la que limitar la búsqueda.
                       None → se resuelve con self._gmail_sync_folder().
                       '' (tras resolver) → todo el buzón.
        :param mail:   conexión IMAP ya abierta y autenticada (el cron la
                       comparte entre leads). Si es None se abre y se cierra
                       aquí.
        :param user:   usuario (res.users) cuyas credenciales se usarán.
                       Si es None, se usa self.user_id o self.env.user.
        :returns: dict(created, skipped, diag, error)
        """
        self.ensure_one()
        if not user:
            user = self.user_id or self.env.user

        lead_email = self._extract_email_address(self.email_from or '') \
            or (self.email_from or '').strip().lower()
        if not lead_email:
            return {'created': 0, 'skipped': 0, 'diag': [], 'error': 'sin email'}

        if folder is None:
            folder = self._gmail_sync_folder()
        folder = (folder or '').strip()

        host, user_email, password, folder_sent, folder_inbox = self._get_imap_config(user)
        imap_user_email = self._extract_email_address(user_email) or user_email.lower()

        _logger.info('CRM Gmail sync: lead=%s email=%s carpeta=%s usuario=%s',
                     self.id, lead_email, folder or '(todo el buzón)', user.name)

        # ── Dedup: Message-ID ya guardados + fechas (minuto) como respaldo ────
        existing_msgids = set(
            self.communication_ids.filtered('message_id').mapped('message_id'))
        existing_minutes = set()
        for comm in self.communication_ids:
            if comm.action_type in ('email_sent', 'email_received') and comm.date:
                existing_minutes.add(comm.date.replace(second=0, microsecond=0))

        own_conn = mail is None
        if own_conn:
            mail = self._gmail_imap_connect(user)

        created = skipped = 0
        diag = []
        try:
            # ── Carpetas a barrer ───────────────────────────────────────────
            if folder and folder not in ('[Gmail]/All Mail', '[Gmail]/Todos'):
                try:
                    st, _ = mail.select(folder, readonly=True)
                except Exception:
                    st = 'NO'
                if st == 'OK':
                    diag.append(f'✓ Buscando en «{folder}»')
                    search_folders = [folder]
                else:
                    diag.append(f'⚠ No se pudo abrir «{folder}»: se usa todo el buzón')
                    search_folders = self._gmail_all_mail_folders(
                        mail, folder_inbox, folder_sent, diag)
            else:
                search_folders = self._gmail_all_mail_folders(
                    mail, folder_inbox, folder_sent, diag)

            # ── Buscar ids en cada carpeta ──────────────────────────────────
            work = []          # [(carpeta, msg_id)]
            seen = set()
            for sf in search_folders:
                try:
                    s, _ = mail.select(sf, readonly=True)
                    if s != 'OK':
                        continue
                except Exception:
                    continue
                criteria = f'OR FROM "{lead_email}" TO "{lead_email}"'
                ids = self._imap_search(mail, criteria)
                diag.append(f'✓ {sf}: {len(ids)} correos')
                _logger.info('CRM Gmail sync: %s → %d emails', sf, len(ids))
                for mid in ids:
                    key = (sf, mid)
                    if key not in seen:
                        seen.add(key)
                        work.append(key)

            work = work[-300:]

            current_folder = None
            for sf, msg_id in work:
                try:
                    if sf != current_folder:
                        mail.select(sf, readonly=True)
                        current_folder = sf

                    _, msg_data = mail.fetch(msg_id, '(RFC822)')
                    raw = msg_data[0][1]
                    msg = email.message_from_bytes(raw)

                    rfc_msgid = (msg.get('Message-ID') or msg.get('Message-Id') or '').strip()
                    if rfc_msgid and rfc_msgid in existing_msgids:
                        skipped += 1
                        continue

                    date_str = msg.get('Date', '')
                    dt = self._parse_date(date_str)
                    if not dt:
                        skipped += 1
                        continue

                    dt_minute = dt.replace(second=0, microsecond=0)
                    # Sin Message-ID fiable, respaldo por coincidencia de minuto.
                    if not rfc_msgid and any(
                        abs((dt_minute - ex).total_seconds()) <= 60
                        for ex in existing_minutes
                    ):
                        skipped += 1
                        continue

                    subject = self._decode_header(msg.get('Subject', '')) or '(sin asunto)'
                    from_addr = self._decode_header(msg.get('From', ''))
                    to_addr = self._decode_header(msg.get('To', ''))
                    cc_addr = self._decode_header(msg.get('Cc', ''))

                    from_pure = self._extract_email_address(from_addr)
                    real_type = 'email_sent' if from_pure == imap_user_email else 'email_received'

                    body_text, body_html, attachments = self._extract_body_and_attachments(msg)

                    desc = (
                        f'<p><strong>De:</strong> {from_addr}<br/>'
                        f'<strong>Para:</strong> {to_addr}'
                    )
                    if cc_addr:
                        desc += f'<br/><strong>CC:</strong> {cc_addr}'
                    desc += '</p>'
                    if body_html:
                        desc += self._extract_html_body(body_html)
                    elif body_text:
                        escaped = body_text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                        desc += f'<pre>{escaped}</pre>'

                    att_ids = []
                    for fname, fdata, fmime in attachments:
                        try:
                            att = self.env['ir.attachment'].create({
                                'name': fname,
                                'datas': base64.b64encode(fdata).decode('ascii'),
                                'mimetype': fmime,
                                'res_model': 'crm.communication',
                            })
                            att_ids.append(att.id)
                        except Exception as att_exc:
                            _logger.warning('CRM Gmail sync: adjunto %s: %s', fname, att_exc)

                    comm = self.env['crm.communication'].create({
                        'lead_id': self.id,
                        'date': dt,
                        'action_type': real_type,
                        'subject': subject,
                        'user_id': self.user_id.id or self.env.user.id,
                        'description': desc,
                        'message_id': rfc_msgid or False,
                    })
                    if att_ids:
                        comm.attachment_ids = [(6, 0, att_ids)]

                    if rfc_msgid:
                        existing_msgids.add(rfc_msgid)
                    existing_minutes.add(dt_minute)
                    created += 1

                except Exception as exc:
                    _logger.warning('CRM Gmail sync: error procesando mensaje %s: %s',
                                    msg_id, exc)
        finally:
            if own_conn:
                try:
                    mail.logout()
                except Exception:
                    pass

        _logger.info('CRM Gmail sync lead=%s: creados=%d omitidos=%d',
                     self.id, created, skipped)
        return {'created': created, 'skipped': skipped, 'diag': diag, 'error': ''}

    def action_sync_gmail(self):
        """Botón manual: sincroniza Gmail para este lead y muestra el resultado.

        La carpeta se resuelve así: contexto `gmail_folder` (si viene) →
        `_gmail_sync_folder()` (etiqueta de campaña o global) → todo el buzón.
        """
        self.ensure_one()
        user = self.env.user
        if not self._gmail_imap_config_ok(user):
            raise UserError(
                f'El usuario {user.name} no tiene configurada la sincronización de Gmail.\n'
                'Por favor, ve a tu perfil y rellena los campos de conexión IMAP.'
            )

        folder = self.env.context.get('gmail_folder')
        if folder is None:
            folder = self._gmail_sync_folder()

        res = self._sync_gmail(folder=folder, user=user)
        self.gmail_last_sync = fields.Datetime.now()

        if res.get('error') == 'sin email':
            raise UserError(
                'El lead no tiene email (campo "Email"). '
                'Rellénalo para poder buscar correos relacionados.'
            )

        diag_str = '\n'.join(res['diag'])
        if not res['created']:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Gmail sincronizado — sin novedades',
                    'message': f'{diag_str}\nYa registrados: {res["skipped"]}',
                    'type': 'warning',
                    'sticky': True,
                },
            }
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Gmail sincronizado',
                'message': f'Se añadieron {res["created"]} comunicación(es) nueva(s) desde Gmail.',
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.client', 'tag': 'reload'},
            },
        }

    @api.model
    def _cron_gmail_sync(self):
        """Cada 10 min: sincroniza Gmail para un lote de leads.

        Recorre primero los que no se han sincronizado nunca y luego los más
        antiguos, así en varias pasadas cubre toda la base sin saturar Gmail.
        Agrupa los leads por usuario asignado para compartir una única conexión
        IMAP por cada vendedor.
        """
        icp = self.env['ir.config_parameter'].sudo()
        try:
            batch = int(icp.get_param('crm_marketing_and_comunications.gmail_sync_batch', '25') or 25)
        except (TypeError, ValueError):
            batch = 25
        batch = max(1, min(batch, 200))

        dominio = [
            ('gmail_sync_enabled', '=', True),
            ('email_from', '!=', False),
            ('active', '=', True),
        ]
        leads = self.search(
            dominio + [('gmail_last_sync', '=', False)], limit=batch, order='id')
        if len(leads) < batch:
            leads |= self.search(
                dominio + [('gmail_last_sync', '!=', False)],
                limit=batch - len(leads), order='gmail_last_sync asc, id')
        if not leads:
            return

        # Agrupar leads por vendedor (vendedor debe tener IMAP configurado)
        from collections import defaultdict
        leads_by_user = defaultdict(lambda: self.env['crm.lead'])

        for lead in leads:
            user = lead.user_id
            if user and self._gmail_imap_config_ok(user):
                leads_by_user[user] |= lead
            else:
                _logger.info(
                    'CRM Gmail cron: lead %s (%s) omitido porque no tiene vendedor o '
                    'el vendedor no tiene configurado Gmail IMAP.',
                    lead.id, lead.name
                )
                # Actualizamos su fecha de última sincronización para no quedar atrapados en un bucle infinito
                lead.gmail_last_sync = fields.Datetime.now()

        total_created = 0
        total_processed_leads = 0

        for user, user_leads in leads_by_user.items():
            try:
                mail = self._gmail_imap_connect(user)
            except Exception as exc:
                _logger.warning('CRM Gmail cron: no se pudo conectar para el usuario %s: %s', user.name, exc)
                # Marcar los leads como sincronizados para que el cron no se atasque
                for lead in user_leads:
                    lead.gmail_last_sync = fields.Datetime.now()
                continue

            try:
                for lead in user_leads:
                    try:
                        res = lead._sync_gmail(folder=lead._gmail_sync_folder(), mail=mail, user=user)
                        total_created += res.get('created', 0)
                        total_processed_leads += 1
                    except Exception:
                        _logger.exception('CRM Gmail cron: lead %s (usuario %s)', lead.id, user.name)
                    finally:
                        lead.gmail_last_sync = fields.Datetime.now()
                        self.env.cr.commit()
            finally:
                try:
                    mail.logout()
                except Exception:
                    pass

        _logger.info('CRM Gmail cron: %d lead(s) procesados, %d comunicación(es) nuevas',
                     total_processed_leads, total_created)


    # ── MÉTODOS DE MARKETING INTEGRADOS ──
    _inherit = 'crm.lead'

    marketing_campaign_lead_ids = fields.One2many(
        'marketing.campaign.lead',
        'lead_id',
        string='Campañas de marketing',
    )
    marketing_campaign_count = fields.Integer(
        string='Campañas',
        compute='_compute_marketing_campaign_count',
    )
    marketing_opt_out = fields.Boolean(
        string='Baja de Marketing / RGPD',
        default=False,
        tracking=True,
        help='Si está marcado, este contacto ha solicitado la baja de marketing y no puede ser incluido en ninguna campaña.',
    )

    def _compute_marketing_campaign_count(self):
        for lead in self:
            lead.marketing_campaign_count = len(lead.marketing_campaign_lead_ids)

    

    @api.model_create_multi
    def create(self, vals_list):
        leads = super().create(vals_list)
        for lead in leads:
            if lead.marketing_opt_out:
                self.env['marketing.rgpd.exclusion'].sudo().add_exclusion(
                    email=lead.email_from,
                    phone=lead.phone,
                    lead_id=lead.id,
                    reason='manual',
                    excluded_by='user_manual',
                )
        return leads

    def write(self, vals):
        res = super().write(vals)
        if 'marketing_opt_out' in vals and vals['marketing_opt_out']:
            for lead in self:
                if lead.marketing_opt_out:
                    self.env['marketing.rgpd.exclusion'].sudo().add_exclusion(
                        email=lead.email_from,
                        phone=lead.phone,
                        lead_id=lead.id,
                        reason='manual',
                        excluded_by='user_manual',
                    )
        return res

    def action_create_marketing_campaign(self):
        """Abre el wizard de creación de campaña con este lead preseleccionado."""
        return {
            'type': 'ir.actions.act_window',
            'name': 'Nueva campaña de marketing',
            'res_model': 'campaign.create.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_selected_lead_ids': [(6, 0, self.ids)],
                'default_stage_source_id': self.stage_id.id if len(self) == 1 else False,
            },
        }

    def action_view_marketing_campaigns(self):
        """Abre las campañas en las que participa este lead."""
        self.ensure_one()
        campaign_ids = self.marketing_campaign_lead_ids.mapped('campaign_id').ids
        return {
            'type': 'ir.actions.act_window',
            'name': f'Campañas de {self.partner_name or self.name}',
            'res_model': 'marketing.campaign',
            'view_mode': 'list,form',
            'domain': [('id', 'in', campaign_ids)],
        }

    def action_add_to_existing_campaign(self):
        """Abre el wizard en modo 'añadir a campaña existente'."""
        return {
            'type': 'ir.actions.act_window',
            'name': 'Añadir a campaña existente',
            'res_model': 'campaign.create.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_mode': 'add_to_existing',
                'default_selected_lead_ids': [(6, 0, self.ids)],
                'default_stage_source_id': self.stage_id.id if len(self) == 1 else False,
            },
        }
