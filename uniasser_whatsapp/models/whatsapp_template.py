import json
import logging

from odoo import models, fields, api
from odoo.exceptions import UserError
from .graph_config import get_graph_base

_logger = logging.getLogger(__name__)


class WhatsAppTemplate(models.Model):
    _name = 'whatsapp.template'
    _description = 'Plantilla WhatsApp'
    _order = 'name'

    name = fields.Char(string='Nombre de la plantilla', required=True,
                       help='Solo letras minúsculas, números y guiones bajos. Ej: bienvenida_cliente')
    account_id = fields.Many2one(
        'whatsapp.account', string='Cuenta', required=True, ondelete='cascade',
    )
    language_code = fields.Char(string='Idioma', default='es')
    category = fields.Selection(
        [
            ('marketing', 'Marketing'),
            ('utility', 'Utilidad'),
            ('authentication', 'Autenticación'),
        ],
        string='Categoría', default='utility',
        help='Utility: confirmaciones y avisos. Marketing: promociones. Authentication: códigos de verificación.',
    )
    status = fields.Selection(
        [
            ('draft', 'Borrador'),
            ('pending', 'Enviada a Meta — pendiente'),
            ('approved', 'Aprobada'),
            ('rejected', 'Rechazada'),
            ('paused', 'Pausada'),
        ],
        string='Estado', default='draft', readonly=True,
    )

    # ── Texto de la plantilla (campos simples para el usuario) ────────────────
    header_text = fields.Char(
        string='Encabezado (opcional)',
        help='Título breve que aparece encima del mensaje. Máx 60 caracteres. '
             'Usa {{1}} para una variable.',
    )
    body_text = fields.Text(
        string='Cuerpo del mensaje',
        required=True,
        help='Texto principal del mensaje. Usa {{1}}, {{2}}… para variables. '
             'Ej: Hola {{1}}, tu cita es el {{2}}.',
    )
    footer_text = fields.Char(
        string='Pie de página (opcional)',
        help='Texto pequeño al final. Sin variables.',
    )
    sample_values = fields.Char(
        string='Valores de ejemplo para variables',
        help='Valores de muestra separados por comas para Meta. Ej: Juan,15/09/2026',
    )

    # ── JSON generado automáticamente (solo lectura/técnico) ─────────────────
    components = fields.Text(
        string='JSON generado',
        readonly=True,
        help='Generado automáticamente al guardar.',
    )
    wa_template_id = fields.Char(string='ID en Meta', index=True, readonly=True)
    rejection_reason = fields.Char(string='Motivo de rechazo', readonly=True)

    # ── Generar JSON al guardar ───────────────────────────────────────────────

    def _build_components(self):
        """Construye la lista de componentes Meta a partir de los campos de texto."""
        self.ensure_one()
        comps = []
        if self.header_text:
            header = {'type': 'HEADER', 'format': 'TEXT', 'text': self.header_text}
            if '{{1}}' in self.header_text:
                header['example'] = {'header_text': [self._get_sample(0)]}
            comps.append(header)

        if self.body_text:
            body = {'type': 'BODY', 'text': self.body_text}
            # Detectar variables {{N}} y añadir ejemplos
            import re
            vars_found = re.findall(r'\{\{(\d+)\}\}', self.body_text)
            if vars_found:
                samples = self._get_all_samples()
                body_samples = [samples[int(n) - 1] if int(n) - 1 < len(samples) else f'ejemplo{n}'
                                for n in vars_found]
                body['example'] = {'body_text': [body_samples]}
            comps.append(body)

        if self.footer_text:
            comps.append({'type': 'FOOTER', 'text': self.footer_text})

        return comps

    def _get_all_samples(self):
        raw = (self.sample_values or '').strip()
        if raw:
            return [s.strip() for s in raw.split(',')]
        return ['ejemplo1', 'ejemplo2', 'ejemplo3', 'ejemplo4', 'ejemplo5']

    def _get_sample(self, idx):
        samples = self._get_all_samples()
        return samples[idx] if idx < len(samples) else f'ejemplo{idx + 1}'

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for r in records:
            r.components = json.dumps(r._build_components(), ensure_ascii=False, indent=2)
        return records

    def write(self, vals):
        res = super().write(vals)
        if any(f in vals for f in ('header_text', 'body_text', 'footer_text', 'sample_values')):
            for r in self:
                r.components = json.dumps(r._build_components(), ensure_ascii=False, indent=2)
        return res

    # ── Envío a Meta ──────────────────────────────────────────────────────────

    def action_submit_to_meta(self):
        """Envía la plantilla a Meta para su aprobación."""
        self.ensure_one()
        account = self.account_id
        if account.connection_type != 'meta_business':
            raise UserError('Solo se pueden enviar plantillas a través de cuentas Meta Business API.')
        if not account.waba_id:
            raise UserError('Configura el WhatsApp Business Account ID (WABA ID) en la cuenta.')
        if not self.body_text:
            raise UserError('El cuerpo del mensaje es obligatorio.')

        # Validar nombre (Meta exige snake_case en minúsculas)
        import re
        if not re.match(r'^[a-z0-9_]+$', self.name):
            raise UserError(
                'El nombre de la plantilla solo puede contener letras minúsculas, '
                'números y guiones bajos. Ej: bienvenida_cliente'
            )

        # Regenerar components antes de enviar
        comps = self._build_components()
        self.components = json.dumps(comps, ensure_ascii=False, indent=2)

        category_map = {
            'marketing': 'MARKETING',
            'utility': 'UTILITY',
            'authentication': 'AUTHENTICATION',
        }

        payload = {
            'name': self.name,
            'language': self.language_code or 'es',
            'category': category_map.get(self.category, 'UTILITY'),
            'components': comps,
        }

        try:
            import urllib.request
            import urllib.error
            url = f'{get_graph_base(self.env)}/{account.waba_id}/message_templates'
            data = json.dumps(payload).encode()
            req = urllib.request.Request(
                url, data=data,
                headers={
                    'Authorization': f'Bearer {account.access_token}',
                    'Content-Type': 'application/json',
                },
                method='POST',
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                result = json.loads(resp.read().decode())

            wa_id = result.get('id', '')
            status = result.get('status', 'PENDING').lower()
            self.write({
                'wa_template_id': wa_id,
                'status': 'pending' if status == 'pending' else status,
            })
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Plantilla enviada a Meta',
                    'message': f'Estado: {status.upper()}. Meta tardará entre minutos y 24h en aprobarla.',
                    'type': 'success',
                    'sticky': False,
                },
            }
        except urllib.error.HTTPError as exc:
            body = exc.read().decode('utf-8', errors='replace')
            try:
                err = json.loads(body)
                msg = err.get('error', {}).get('message', body[:300])
            except Exception:
                msg = body[:300]
            raise UserError(f'Error de Meta API: {msg}')
        except Exception as exc:
            raise UserError(f'Error enviando plantilla: {exc}')

    @property
    def body_text_preview(self) -> str:
        return self.body_text or ''
