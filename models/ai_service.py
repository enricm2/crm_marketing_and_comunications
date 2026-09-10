"""Servicio de IA con conmutación entre proveedores.

Todo el módulo llamaba directamente a Anthropic (`_call_claude`). Si su API
fallaba —caída, límite de peticiones, clave caducada— se paraba la generación de
plantillas, la personalización por lead, el diagnóstico de LinkedIn y la
clasificación de respuestas. Sin alternativa.

Aquí se centraliza la llamada: se intenta con el proveedor preferido y, si
falla, se pasa al siguiente que tenga clave configurada. Solo se da error
cuando fallan todos.

Las claves se leen de `ir.config_parameter`, aceptando los prefijos de los dos
módulos porque históricamente se guardaron en sitios distintos:
    crm_marketing_and_comunications.ai_anthropic_key
    crm_marketing_and_comunications.ai_anthropic_key
"""

import json
import logging
import urllib.request

from odoo import api, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

TIMEOUT = 120

# Dónde buscar cada clave, en orden de preferencia. Se aceptan varios prefijos
# y varios nombres porque las claves se fueron guardando en sitios distintos:
# los módulos de marketing/CRM usan `ai_<proveedor>_key`, y los de tesorería
# `<proveedor>_api_key`. Reutilizarlas evita tener que pegar la misma clave
# tres veces; las de marketing mandan si están definidas.
_PREFIJOS = [
    'crm_marketing_and_comunications.',
    'crm_marketing_and_comunications.',
    'ia_agents_treasury_control.',
    'mcp_agents_financial_and_treasury_control.',
]

# Nombres alternativos del mismo parámetro entre módulos
_ALIAS = {
    'ai_anthropic_key': ['ai_anthropic_key', 'anthropic_api_key'],
    'ai_gemini_key':    ['ai_gemini_key', 'gemini_api_key'],
    'ai_openai_key':    ['ai_openai_key', 'openai_api_key'],
    'ai_preferred':     ['ai_preferred'],
}


def _post_json(url, payload, headers, timeout=TIMEOUT):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json', **headers}, method='POST',
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _call_anthropic(api_key, prompt):
    data = _post_json(
        'https://api.anthropic.com/v1/messages',
        {'model': 'claude-sonnet-4-6', 'max_tokens': 4096,
         'messages': [{'role': 'user', 'content': prompt}]},
        {'x-api-key': api_key, 'anthropic-version': '2023-06-01'},
    )
    return (data.get('content') or [{}])[0].get('text', '')


def _call_gemini(api_key, prompt):
    data = _post_json(
        'https://generativelanguage.googleapis.com/v1beta/models/'
        f'gemini-2.5-flash:generateContent?key={api_key}',
        {'contents': [{'parts': [{'text': prompt}]}]},
        {},
    )
    cands = data.get('candidates') or [{}]
    parts = (cands[0].get('content') or {}).get('parts') or [{}]
    return parts[0].get('text', '')


def _call_openai(api_key, prompt):
    data = _post_json(
        'https://api.openai.com/v1/chat/completions',
        {'model': 'gpt-4o', 'messages': [{'role': 'user', 'content': prompt}]},
        {'Authorization': f'Bearer {api_key}'},
    )
    return ((data.get('choices') or [{}])[0].get('message') or {}).get('content', '')


PROVEEDORES = {
    'anthropic': ('ai_anthropic_key', _call_anthropic),
    'gemini':    ('ai_gemini_key',    _call_gemini),
    'openai':    ('ai_openai_key',    _call_openai),
}


class MarketingAiService(models.AbstractModel):
    _name = 'marketing.ai.service'
    _description = 'Llamadas a IA con conmutación entre proveedores'

    @api.model
    def _param(self, sufijo):
        """Busca un parámetro probando prefijos y nombres alternativos."""
        icp = self.env['ir.config_parameter'].sudo()
        for nombre in _ALIAS.get(sufijo, [sufijo]):
            for pref in _PREFIJOS:
                valor = icp.get_param(f'{pref}{nombre}', '')
                if valor:
                    return valor
        return ''

    @api.model
    def proveedores_disponibles(self):
        """Lista de proveedores con clave configurada, en orden de intento."""
        preferido = self._param('ai_preferred') or 'anthropic'
        orden = [preferido] + [p for p in PROVEEDORES if p != preferido]
        return [p for p in orden if self._param(PROVEEDORES[p][0])]

    @api.model
    def generar(self, prompt, contexto=''):
        """Genera texto probando los proveedores en orden.

        :param prompt: el texto a enviar
        :param contexto: etiqueta para los logs, para saber qué proceso falló
        :raises UserError: solo si fallan TODOS los proveedores con clave
        """
        disponibles = self.proveedores_disponibles()
        if not disponibles:
            raise UserError(
                'No hay ninguna clave de IA configurada.\n'
                'Ve a Ajustes y define al menos una: Anthropic, Gemini u OpenAI.'
            )

        errores = []
        for nombre in disponibles:
            sufijo, fn = PROVEEDORES[nombre]
            clave = self._param(sufijo)
            try:
                texto = fn(clave, prompt)
                if not (texto or '').strip():
                    raise ValueError('respuesta vacía')
                if errores:
                    # Dejar constancia de que se usó un suplente: si el
                    # proveedor principal falla a menudo, conviene saberlo.
                    _logger.warning(
                        '[IA%s] %s respondió tras fallar: %s',
                        f' {contexto}' if contexto else '', nombre,
                        '; '.join(errores),
                    )
                return texto
            except Exception as exc:  # noqa: BLE001
                _logger.warning('[IA%s] %s falló: %s',
                                f' {contexto}' if contexto else '', nombre, exc)
                errores.append(f'{nombre}: {exc}')

        raise UserError(
            'Ningún proveedor de IA pudo responder'
            + (f' ({contexto})' if contexto else '') + '.\n\n'
            + '\n'.join(errores)
            + '\n\nRevisa las claves API en Ajustes.'
        )
