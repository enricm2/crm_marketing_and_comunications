import logging

from odoo import http, fields
from odoo.http import request

_logger = logging.getLogger(__name__)


class WhatsAppChatApiController(http.Controller):
    """Endpoints JSON para el componente OWL de bandeja WhatsApp."""

    @http.route('/whatsapp/api/conversations', type='jsonrpc', auth='user', methods=['POST'])
    def get_conversations(self, account_id=None, search='', offset=0, limit=30):
        """Devuelve lista de conversaciones agrupadas por contacto o grupo.

        Prioridad de nombre por tipo:
          - Contacto individual: nombre Odoo del partner → profile.name de WA → teléfono formateado
          - Grupo: subject del grupo → "Grupo sin nombre (ID: ...)"
        """
        base_domain = []
        if account_id:
            base_domain.append(('account_id', '=', account_id))

        # ── Conversaciones 1-a-1 (sin grupo) ─────────────────────────────────
        ind_domain = base_domain + [
            ('partner_id', '!=', False),
            ('wa_group_id', '=', False),
        ]
        ind_msgs = request.env['whatsapp.message'].search(ind_domain, order='timestamp desc')
        seen_partners = {}
        for m in ind_msgs:
            pid = m.partner_id.id
            if pid not in seen_partners:
                seen_partners[pid] = m

        convs = []
        WA_AUTO_PREFIX = 'WhatsApp '

        for partner_id, last_msg in seen_partners.items():
            partner = last_msg.partner_id

            # Nombre: Odoo partner → profile.name → teléfono formateado
            odoo_name = partner.name or ''
            is_auto = odoo_name.startswith(WA_AUTO_PREFIX)
            if not is_auto:
                display_name = odoo_name
            else:
                # Auto-generado: preferir profile.name guardado
                display_name = last_msg.wa_profile_name or odoo_name

            # Teléfono como dato secundario
            phone = partner.phone or partner.mobile or ''

            if search and search.lower() not in display_name.lower() \
                    and search.lower() not in phone.lower():
                continue

            unread = request.env['whatsapp.message'].search_count([
                ('partner_id', '=', partner_id),
                ('wa_group_id', '=', False),
                ('direction', '=', 'inbound'),
                ('status', '!=', 'read'),
            ])
            convs.append({
                'conv_key': f'p-{partner_id}',
                'partner_id': partner_id,
                'group_id': False,
                'is_group': False,
                'display_name': display_name,
                'secondary_info': phone,       # teléfono como subtítulo
                'last_message': (last_msg.body or '')[:80],
                'last_message_type': last_msg.message_type,
                'last_timestamp': (
                    last_msg.timestamp.isoformat() if last_msg.timestamp else ''
                ),
                'direction': last_msg.direction,
                'unread_count': unread,
                'lead_id': last_msg.lead_id.id if last_msg.lead_id else False,
                'lead_name': last_msg.lead_id.name if last_msg.lead_id else '',
                # Campos heredados (compatibilidad con código existente)
                'partner_name': display_name,
                'partner_phone': phone,
            })

        # ── Conversaciones de grupo ───────────────────────────────────────────
        grp_domain = base_domain + [('wa_group_id', '!=', False)]
        grp_msgs = request.env['whatsapp.message'].search(grp_domain, order='timestamp desc')
        seen_groups = {}
        for m in grp_msgs:
            gid = m.wa_group_id.id
            if gid not in seen_groups:
                seen_groups[gid] = m

        for group_id, last_msg in seen_groups.items():
            group = last_msg.wa_group_id
            short_id = (group.wa_group_id or '')[:24]
            display_name = group.name or f'Grupo sin nombre (ID: {short_id})'

            if search and search.lower() not in display_name.lower():
                continue

            unread = request.env['whatsapp.message'].search_count([
                ('wa_group_id', '=', group_id),
                ('direction', '=', 'inbound'),
                ('status', '!=', 'read'),
            ])
            convs.append({
                'conv_key': f'g-{group_id}',
                'partner_id': False,
                'group_id': group_id,
                'is_group': True,
                'display_name': display_name,
                'secondary_info': group.wa_group_id or '',   # ID como subtítulo
                'last_message': (last_msg.body or '')[:80],
                'last_message_type': last_msg.message_type,
                'last_timestamp': (
                    last_msg.timestamp.isoformat() if last_msg.timestamp else ''
                ),
                'direction': last_msg.direction,
                'unread_count': unread,
                'lead_id': False,
                'lead_name': '',
                # Compatibilidad
                'partner_name': display_name,
                'partner_phone': '',
            })

        # Ordenar por último mensaje más reciente
        convs.sort(key=lambda c: c['last_timestamp'], reverse=True)
        return {
            'conversations': convs[offset:offset + limit],
            'total': len(convs),
        }

    @http.route('/whatsapp/api/messages', type='jsonrpc', auth='user', methods=['POST'])
    def get_messages(self, partner_id=None, group_id=None, account_id=None, offset=0, limit=50):
        """Devuelve mensajes paginados de una conversación (partner o grupo)."""
        if group_id:
            domain = [('wa_group_id', '=', group_id)]
        else:
            domain = [('partner_id', '=', partner_id)]
        if account_id:
            domain.append(('account_id', '=', account_id))

        total = request.env['whatsapp.message'].search_count(domain)
        msgs = request.env['whatsapp.message'].search(
            domain, order='timestamp asc', offset=offset, limit=limit,
        )

        # Marcar entrantes como leídos
        inbound_unread = msgs.filtered(
            lambda m: m.direction == 'inbound' and m.status != 'read'
        )
        if inbound_unread:
            inbound_unread.write({'status': 'read'})

        result = []
        for m in msgs:
            atts = [
                {
                    'id': att.id,
                    'name': att.name,
                    'mimetype': att.mimetype,
                    'url': f'/web/content/{att.id}?download=true',
                }
                for att in m.attachment_ids
            ]
            result.append({
                'id': m.id,
                'wa_message_id': m.wa_message_id or '',
                'direction': m.direction,
                'body': m.body or '',
                'message_type': m.message_type,
                'status': m.status,
                'timestamp': m.timestamp.isoformat() if m.timestamp else '',
                'attachments': atts,
                'lead_id': m.lead_id.id if m.lead_id else False,
                'lead_name': m.lead_id.name if m.lead_id else '',
            })

        return {'messages': result, 'total': total}

    @http.route('/whatsapp/api/send', type='jsonrpc', auth='user', methods=['POST'])
    def send_message(
        self, partner_id=None, body='', account_id=None, message_type='text',
        template_name=None, language_code='es', phone_direct=None,
    ):
        """Envía un mensaje de texto o plantilla y devuelve el mensaje creado."""
        from ..models.whatsapp_api import WhatsAppAPI

        if account_id:
            account = request.env['whatsapp.account'].browse(account_id)
        elif partner_id:
            # Usar la cuenta del último mensaje con este contacto
            last_msg = request.env['whatsapp.message'].search(
                [('partner_id', '=', partner_id)],
                order='id desc', limit=1,
            )
            account = last_msg.account_id if last_msg else request.env['whatsapp.account'].browse()
            if not account:
                account = request.env['whatsapp.account'].search(
                    [('company_id', '=', request.env.company.id), ('active', '=', True)], limit=1,
                )
        else:
            account = request.env['whatsapp.account'].search(
                [('company_id', '=', request.env.company.id), ('active', '=', True)], limit=1,
            )
        if not account:
            return {'error': 'No hay cuenta WhatsApp configurada.'}

        if phone_direct:
            # Número introducido manualmente — buscar o crear partner
            phone_raw = phone_direct
            partner = request.env['whatsapp.message']._find_or_create_partner(phone_direct)
        elif partner_id:
            partner = request.env['res.partner'].browse(partner_id)
            phone_raw = partner.phone or ''
            if not phone_raw:
                return {'error': 'El contacto no tiene teléfono registrado.'}
        else:
            return {'error': 'Se requiere un contacto o número de teléfono.'}

        normalized = WhatsAppAPI.normalize_phone(phone_raw)
        try:
            if account.connection_type == 'qr_code':
                result = account._evo_send_text(normalized, body)
                # Evolution API devuelve {"key": {"id": "..."}, ...}
                wa_id = (result.get('key') or {}).get('id', '')
                body_saved = body
            elif message_type == 'template' and template_name:
                api = account._get_api()
                result = api.send_template(normalized, template_name, language_code)
                wa_id = result.get('messages', [{}])[0].get('id')
                body_saved = f'[Plantilla: {template_name}]'
            else:
                api = account._get_api()
                result = api.send_text(normalized, body)
                wa_id = result.get('messages', [{}])[0].get('id')
                body_saved = body
            lead = request.env['whatsapp.message']._find_lead(partner)
            msg = request.env['whatsapp.message'].create({
                'account_id': account.id,
                'partner_id': partner.id,
                'lead_id': lead.id if lead else False,
                'wa_message_id': wa_id,
                'direction': 'outbound',
                'body': body_saved,
                'message_type': message_type,
                'status': 'sent',
                'phone': normalized,
            })
            msg._post_to_chatter()
            return {
                'id': msg.id,
                'partner_id': partner.id,
                'wa_message_id': wa_id or '',
                'direction': 'outbound',
                'body': body_saved,
                'message_type': message_type,
                'status': 'sent',
                'timestamp': msg.timestamp.isoformat(),
                'attachments': [],
            }
        except Exception as exc:
            _logger.exception('WhatsApp send error')
            return {'error': str(exc)}

    @http.route('/whatsapp/api/accounts', type='jsonrpc', auth='user', methods=['POST'])
    def get_accounts(self):
        """Lista las cuentas disponibles para el usuario."""
        accounts = request.env['whatsapp.account'].search([
            ('company_id', '=', request.env.company.id),
            ('active', '=', True),
        ])
        result = []
        for a in accounts:
            status = 'disconnected'
            try:
                if a.connection_type == 'qr_code' and a.evolution_instance:
                    state = a._evo_get_state()
                    status = 'connected' if state == 'open' else 'disconnected'
                elif a.connection_type == 'meta_business':
                    status = 'connected'
            except Exception:
                status = 'disconnected'
            result.append({
                'id': a.id,
                'name': a.name,
                'connection_type': a.connection_type,
                'status': status,
            })
        return result

    @http.route('/whatsapp/api/mark_read', type='jsonrpc', auth='user', methods=['POST'])
    def mark_read(self, partner_id, account_id=None):
        """Marca los mensajes entrantes de un partner como leídos."""
        domain = [('partner_id', '=', partner_id), ('direction', '=', 'inbound'), ('status', '!=', 'read')]
        if account_id:
            domain.append(('account_id', '=', account_id))
        msgs = request.env['whatsapp.message'].search(domain)
        if msgs:
            msgs.write({'status': 'read'})
        return {'marked': len(msgs)}

    @http.route('/whatsapp/api/account_status', type='jsonrpc', auth='user', methods=['POST'])
    def get_account_status(self, account_id):
        """Devuelve el estado de conexión de una cuenta."""
        account = request.env['whatsapp.account'].browse(account_id)
        if not account.exists():
            return {'error': 'Cuenta no encontrada'}
        status = 'disconnected'
        phone = ''
        try:
            if account.connection_type == 'qr_code' and account.evolution_instance:
                state = account._evo_get_state()
                status = 'connected' if state == 'open' else 'disconnected'
            elif account.connection_type == 'meta_business':
                status = 'connected'
            phone = account.phone_number_id or ''
        except Exception as e:
            _logger.warning('account_status error: %s', e)
        return {
            'id': account.id,
            'name': account.name,
            'connection_type': account.connection_type,
            'connection_status': status,
            'phone': phone,
            'phone_number_id': account.phone_number_id or '',
        }

    @http.route('/whatsapp/api/qr_code', type='jsonrpc', auth='user', methods=['POST'])
    def get_qr_code(self, account_id):
        """Obtiene el código QR para vincular un dispositivo (cuentas QR)."""
        import base64
        account = request.env['whatsapp.account'].browse(account_id)
        if not account.exists() or account.connection_type != 'qr_code':
            return {'error': 'Cuenta no válida'}
        try:
            # Trigger QR generation via Evolution API
            account.action_connect_qr()
            state = account._evo_get_state()
            # qr_code_image is stored as binary (base64 encoded bytes)
            qr_b64 = ''
            if account.qr_code_image:
                raw = account.qr_code_image
                if isinstance(raw, bytes):
                    qr_b64 = raw.decode('utf-8', errors='ignore')
                else:
                    qr_b64 = str(raw)
            return {
                'qr_base64': qr_b64,
                'status': 'connected' if state == 'open' else 'disconnected',
            }
        except Exception as e:
            _logger.exception('qr_code error')
            return {'error': str(e)}

    @http.route('/whatsapp/api/poll', type='jsonrpc', auth='user', methods=['POST'])
    def poll_new_messages(self, partner_id=None, group_id=None, after_id=0, account_id=None):
        """Devuelve mensajes nuevos posteriores a after_id para un partner o grupo."""
        if group_id:
            domain = [('wa_group_id', '=', group_id), ('id', '>', after_id)]
        else:
            domain = [('partner_id', '=', partner_id), ('id', '>', after_id)]
        if account_id:
            domain.append(('account_id', '=', account_id))
        msgs = request.env['whatsapp.message'].search(
            domain, order='timestamp asc', limit=50,
        )
        result = []
        for m in msgs:
            atts = [
                {
                    'id': a.id,
                    'name': a.name,
                    'mimetype': a.mimetype,
                    'url': f'/web/content/{a.id}?download=true',
                }
                for a in m.attachment_ids
            ]
            result.append({
                'id': m.id,
                'direction': m.direction,
                'body': m.body or '',
                'message_type': m.message_type,
                'status': m.status,
                'timestamp': m.timestamp.isoformat() if m.timestamp else '',
                'attachments': atts,
            })
        return result

    @http.route('/whatsapp/api/templates', type='jsonrpc', auth='user', methods=['POST'])
    def get_templates(self, account_id=None):
        """Lista las plantillas aprobadas disponibles."""
        domain = [('status', '=', 'approved')]
        if account_id:
            domain.append(('account_id', '=', account_id))
        templates = request.env['whatsapp.template'].search(domain, order='name asc')
        return [
            {
                'id': t.id,
                'name': t.name,
                'body': t.body_text or '',
                'language_code': t.language_code or 'es',
            }
            for t in templates
        ]

    @http.route('/whatsapp/api/search_partners', type='jsonrpc', auth='user', methods=['POST'])
    def search_partners(self, query='', limit=10):
        """Busca contactos por nombre o teléfono para iniciar una conversación."""
        if not query or len(query) < 2:
            return []
        domain = [
            '|',
            ('name', 'ilike', query),
            ('phone', 'ilike', query),
        ]
        partners = request.env['res.partner'].search(domain, limit=limit)
        return [
            {
                'id': p.id,
                'name': p.name or '',
                'phone': p.phone or '',
            }
            for p in partners
            if p.phone  # Solo contactos con teléfono
        ]

    @http.route('/whatsapp/api/profile_pic', type='http', auth='user', methods=['GET'])
    def get_profile_pic(self, partner_id=None, account_id=None):
        """Devuelve la foto de perfil de un contacto como imagen binaria.

        Delega en whatsapp.contact.profile.get_contact_avatar(), que:
          - Para cuentas QR: cachea el binario con hash SHA-256 (TTL 7 días).
          - Para cuentas cloud_api: devuelve 404 siempre (limitación de ToS de Meta).
        """
        NO_PHOTO = request.make_response('', status=404)

        if not partner_id:
            return NO_PHOTO
        try:
            partner_id = int(partner_id)
        except (ValueError, TypeError):
            return NO_PHOTO

        partner = request.env['res.partner'].sudo().browse(partner_id)
        if not partner.exists():
            return NO_PHOTO

        # Resolver la cuenta: preferir la del último mensaje QR con este partner
        if account_id:
            account = request.env['whatsapp.account'].sudo().browse(int(account_id))
        else:
            last_msg = request.env['whatsapp.message'].sudo().search(
                [('partner_id', '=', partner_id), ('account_id.connection_type', '=', 'qr_code')],
                order='id desc', limit=1,
            )
            account = last_msg.account_id if last_msg else request.env['whatsapp.account'].sudo().search(
                [('connection_type', '=', 'qr_code'), ('active', '=', True)], limit=1,
            )

        if not account or not account.exists():
            return NO_PHOTO

        img_bytes, content_type = request.env['whatsapp.contact.profile'].sudo().get_contact_avatar(
            account, partner,
        )
        if not img_bytes:
            return NO_PHOTO

        return request.make_response(
            img_bytes,
            headers=[
                ('Content-Type', content_type or 'image/jpeg'),
                ('Cache-Control', 'public, max-age=86400'),
            ],
        )

    @http.route('/whatsapp/api/sync_business_profile', type='jsonrpc', auth='user', methods=['POST'])
    def sync_business_profile(self, account_id):
        """Sincroniza el perfil de negocio de una cuenta cloud_api con Meta.

        Acción manual — equivalente al botón "Sincronizar perfil ahora".
        Solo aplicable a cuentas con connection_type = 'meta_business'.
        """
        account = request.env['whatsapp.account'].browse(int(account_id))
        if not account.exists():
            return {'error': 'Cuenta no encontrada'}
        if account.connection_type != 'meta_business':
            return {'error': 'Solo aplicable a cuentas Meta Cloud API'}

        jid = f'business:{account.id}'
        existing = request.env['whatsapp.contact.profile'].search(
            [('account_id', '=', account.id), ('wa_jid', '=', jid)], limit=1,
        )
        profile = request.env['whatsapp.contact.profile']._sync_business_profile(
            account, existing=existing or None,
        )
        return {
            'has_photo': profile.has_photo if profile else False,
            'about': profile.about or '' if profile else '',
            'photo_updated_at': profile.photo_updated_at.isoformat() if profile and profile.photo_updated_at else '',
        }
