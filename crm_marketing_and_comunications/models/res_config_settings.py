from odoo import models, fields, api

_P = 'crm_marketing_and_comunications.'


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    # ── Configuración Comercial / Branding (Vantis CRM + Marketing + Communications) ──
    crm_marketing_company_name = fields.Char(
        string='Nombre de la empresa',
        config_parameter=f'{_P}company_name',
        default='Vantis CRM',
        help='Nombre comercial utilizado en las firmas, plantillas de correo y pie de página.'
    )
    crm_marketing_company_email = fields.Char(
        string='Email de contacto',
        config_parameter=f'{_P}company_email',
        default='comercial@vantis.com',
        help='Email de soporte o comercial utilizado como remitente o contacto.'
    )
    crm_marketing_company_phone = fields.Char(
        string='Teléfono de contacto',
        config_parameter=f'{_P}company_phone',
        help='Teléfono de soporte o comercial expuesto en las plantillas.'
    )
    crm_marketing_privacy_url = fields.Char(
        string='URL de Política de privacidad',
        config_parameter=f'{_P}privacy_url',
        help='Dirección de la página web de tu política de privacidad para cumplir con el RGPD.'
    )
    crm_marketing_logo_text = fields.Char(
        string='Texto del logotipo',
        config_parameter=f'{_P}logo_text',
        default='VANTIS',
        help='Texto en mayúsculas que se mostrará como logotipo estilizado en la cabecera de las plantillas.'
    )
    crm_marketing_license_key = fields.Char(
        string='Clave de Licencia',
        config_parameter=f'{_P}license_key',
        help='Introduce la clave de tu licencia de apps.odoo.com o de prueba de 15 días.'
    )

    # ── Gmail IMAP ────────────────────────────────────────────────────────────
    crm_gmail_sync_batch = fields.Integer(
        string='Leads por ejecución (sincronización automática)',
        config_parameter=f'{_P}gmail_sync_batch',
        default=25,
        help='Cuántos leads sincroniza cada pasada del proceso en segundo plano (cada 10 min).',
    )

    # ── IA — claves API ───────────────────────────────────────────────────────
    crm_ai_anthropic_key = fields.Char(
        string='Clave API Anthropic (Claude)',
        config_parameter=f'{_P}ai_anthropic_key',
    )
    crm_ai_gemini_key = fields.Char(
        string='Clave API Google Gemini',
        config_parameter=f'{_P}ai_gemini_key',
    )
    crm_ai_openai_key = fields.Char(
        string='Clave API OpenAI (ChatGPT)',
        config_parameter=f'{_P}ai_openai_key',
    )
    crm_ai_preferred = fields.Selection(
        selection=[
            ('anthropic', 'Anthropic Claude (primero)'),
            ('gemini', 'Google Gemini (primero)'),
            ('openai', 'OpenAI ChatGPT (primero)'),
        ],
        string='Proveedor preferido',
        config_parameter=f'{_P}ai_preferred',
        default='anthropic',
    )

    # ── Mi empresa (Para enriquecimiento de Leads) ────────────────────────────
    crm_my_company_description = fields.Char(
        string='Descripción de mi empresa',
        config_parameter=f'{_P}my_company_description',
    )
    crm_my_company_target_audience = fields.Char(
        string='Público objetivo',
        config_parameter=f'{_P}my_company_target_audience',
    )
    crm_my_company_pain_points = fields.Char(
        string='Problemas que resuelvo',
        config_parameter=f'{_P}my_company_pain_points',
    )
    crm_my_company_url_1 = fields.Char(
        string='URL mi empresa (1)',
        config_parameter=f'{_P}my_company_url_1',
    )
    crm_my_company_url_2 = fields.Char(
        string='URL mi empresa (2)',
        config_parameter=f'{_P}my_company_url_2',
    )
    crm_my_company_url_3 = fields.Char(
        string='URL mi empresa (3)',
        config_parameter=f'{_P}my_company_url_3',
    )

    # ── Lista Robinson ─────────────────────────────────────────────────────────
    mc_robinson_check_enabled = fields.Boolean(
        string='Activar consulta Lista Robinson',
        config_parameter=f'{_P}robinson_check_enabled',
        default=True,
    )
    mc_robinson_api_key = fields.Char(
        string='Lista Robinson — API Key',
        config_parameter=f'{_P}robinson_api_key',
        groups='base.group_system',
    )
    mc_robinson_api_secret = fields.Char(
        string='Lista Robinson — API Secret',
        config_parameter=f'{_P}robinson_api_secret',
        groups='base.group_system',
    )
    mc_robinson_cache_days = fields.Integer(
        string='Caducidad caché Robinson (días)',
        config_parameter=f'{_P}robinson_cache_days',
        default=90,
    )

    # ── Umbral IA ──────────────────────────────────────────────────────────────
    mc_ai_confidence_threshold = fields.Float(
        string='Umbral de confianza IA (defecto)',
        config_parameter=f'{_P}ai_confidence_threshold',
        default=0.85,
    )

    # ── Plantilla de mensaje de baja ───────────────────────────────────────────
    mc_unsubscribe_reply_email = fields.Char(
        string='Respuesta automática de baja (email)',
        config_parameter=f'{_P}unsubscribe_reply_email',
        default='Hemos recibido tu solicitud de baja y la hemos procesado correctamente. No volverás a recibir comunicaciones comerciales.',
    )
    mc_unsubscribe_reply_whatsapp = fields.Char(
        string='Respuesta automática de baja (WhatsApp)',
        config_parameter=f'{_P}unsubscribe_reply_whatsapp',
        default='Recibido. Hemos dado de baja tus datos de nuestras comunicaciones comerciales.',
    )

    # ── LinkedIn Growth ────────────────────────────────────────────────────────
    mc_linkedin_n8n_base_url = fields.Char(
        string='URL base de n8n',
        config_parameter=f'{_P}linkedin_n8n_base_url',
        default='https://n8n.uniasser.net',
    )
    mc_linkedin_webhook_call_prep = fields.Char(
        string='Ruta — preparación de llamada',
        config_parameter=f'{_P}linkedin_webhook_call_prep',
        default='/webhook/ventas-ligero',
    )
    mc_linkedin_webhook_router = fields.Char(
        string='Ruta — router de embudo',
        config_parameter=f'{_P}linkedin_webhook_router',
        default='/webhook/router-embudo',
    )
    mc_linkedin_webhook_hot_signal = fields.Char(
        string='Ruta — aviso de señal caliente',
        config_parameter=f'{_P}linkedin_webhook_hot_signal',
    )
    mc_linkedin_webhook_ping = fields.Char(
        string='Ruta — comprobación de conexión',
        config_parameter=f'{_P}linkedin_webhook_ping',
        default='/webhook/vantis-ping',
    )
    mc_linkedin_webhook_prosp_launch = fields.Char(
        string='Ruta — lanzar campaña en Prosp',
        config_parameter=f'{_P}linkedin_webhook_prosp_launch',
        default='/webhook/prosp-campaign-launch',
    )
    mc_linkedin_router_cron_active = fields.Boolean(
        string='Router de embudo semanal activo',
        compute='_compute_mc_linkedin_router_cron_active',
        inverse='_inverse_mc_linkedin_router_cron_active',
    )
    mc_linkedin_odoo_base_url = fields.Char(
        string='URL pública de Odoo para n8n',
        config_parameter=f'{_P}linkedin_odoo_base_url',
    )
    mc_linkedin_api_base = fields.Char(
        string='URL en uso ahora mismo',
        compute='_compute_mc_linkedin_api_base',
    )

    def action_open_linkedin_profiles(self):
        self.ensure_one()
        return self.env['ir.actions.act_window']._for_xml_id(
            'crm_marketing_and_comunications.action_linkedin_profile'
        )

    @api.depends('mc_linkedin_odoo_base_url')
    def _compute_mc_linkedin_api_base(self):
        for rec in self:
            base = rec.mc_linkedin_odoo_base_url or rec.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
            rec.mc_linkedin_api_base = base.rstrip('/')

    def _compute_mc_linkedin_router_cron_active(self):
        cron = self.env.ref('crm_marketing_and_comunications.ir_cron_linkedin_router_weekly', raise_if_not_found=False)
        for rec in self:
            rec.mc_linkedin_router_cron_active = cron.active if cron else False

    def _inverse_mc_linkedin_router_cron_active(self):
        cron = self.env.ref('crm_marketing_and_comunications.ir_cron_linkedin_router_weekly', raise_if_not_found=False)
        if cron:
            for rec in self:
                cron.active = rec.mc_linkedin_router_cron_active

    def action_analyze_my_company_urls(self):
        """Raspa las URLs de mi empresa y genera descripción, público y pains."""
        from .crm_lead_enrichment import _scrape_url, _call_ai, _extract_json

        anthropic_key = self.crm_ai_anthropic_key or ''
        gemini_key = self.crm_ai_gemini_key or ''
        openai_key = self.crm_ai_openai_key or ''

        if not gemini_key:
            icp = self.env['ir.config_parameter'].sudo()
            gemini_key = icp.get_param('ia_agents_treasury_control.gemini_api_key', '') or icp.get_param('mcp_agents_financial_and_treasury_control.gemini_api_key', '')

        preferred = self.crm_ai_preferred or 'anthropic'

        if not any([anthropic_key, gemini_key, openai_key]):
            raise UserError(
                'No hay ninguna clave API de IA configurada.\n'
                'Introduce al menos una clave antes de analizar.'
            )

        urls = [u for u in [
            self.crm_my_company_url_1,
            self.crm_my_company_url_2,
            self.crm_my_company_url_3,
        ] if u and u.strip()]

        if not urls:
            raise UserError(
                'Introduce al menos una URL de tu empresa antes de analizar.'
            )

        # Raspar todas las URLs y combinar el contenido
        web_context = ''
        for url in urls:
            d = _scrape_url(url.strip(), max_chars=2000)
            if d['text']:
                web_context += (
                    f"\n\n[{url}]\n"
                    f"Título: {d['title']}\n"
                    f"Descripción: {d['description']}\n"
                    f"Contenido: {d['text'][:2000]}"
                )

        if not web_context.strip():
            raise UserError(
                'No se pudo obtener contenido de ninguna de las URLs.\n'
                'Verifica que las URLs son accesibles y contienen texto.'
            )

        prompt = f"""Eres un experto en marketing B2B. Analiza el contenido de las webs de esta empresa y extrae información clave.

=== CONTENIDO WEB DE LA EMPRESA ===
{web_context}

=== TAREA ===
Basándote exclusivamente en el contenido anterior, genera un JSON con este formato EXACTO:

{{
  "description": "Descripción concisa de 2-3 frases de a qué se dedica la empresa, qué ofrece y cuál es su propuesta de valor.",
  "target_audience": "Descripción del público objetivo: tipo de empresa/persona, sector, tamaño, necesidades específicas.",
  "pain_points": "Lista de 3-5 problemas concretos que esta empresa resuelve a sus clientes, separados por punto y coma."
}}

REGLAS:
- Usa texto plano sin HTML ni markdown.
- Sé específico y concreto, no genérico.
- Responde SOLO el JSON, sin texto adicional, sin bloques de código.
"""

        raw = _call_ai(anthropic_key, gemini_key, openai_key, preferred, prompt)
        data = _extract_json(raw)

        if not data:
            raise UserError(
                'La IA no devolvió un JSON válido.\nRespuesta: ' + raw[:300]
            )

        # Guardar en ir.config_parameter (persistente) y en el registro transient
        icp = self.env['ir.config_parameter'].sudo()
        vals = {}

        if data.get('description'):
            icp.set_param(f'{_P}my_company_description', data['description'])
            vals['crm_my_company_description'] = data['description']

        if data.get('target_audience'):
            icp.set_param(f'{_P}my_company_target_audience', data['target_audience'])
            vals['crm_my_company_target_audience'] = data['target_audience']

        if data.get('pain_points'):
            icp.set_param(f'{_P}my_company_pain_points', data['pain_points'])
            vals['crm_my_company_pain_points'] = data['pain_points']

        if vals:
            self.write(vals)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Análisis completado',
                'message': 'Los campos de mi empresa han sido rellenados con IA. Revísalos y pulsa Guardar.',
                'type': 'success',
                'sticky': False,
            },
        }
