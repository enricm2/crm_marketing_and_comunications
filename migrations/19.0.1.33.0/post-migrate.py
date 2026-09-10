"""Corrige las capturas de prueba de Prosp guardadas como eventos reales.

Las primeras capturas se grabaron antes de que se supiera reconocer el envío de
prueba que Prosp manda al guardar un webhook (`eventType: "event_name"`, la
plantilla sin sustituir). Quedaron con `is_test = False` y en estado «nueva»,
así que aparecían en la bandeja como eventos pendientes de mapear — y no había
nada que mapear: son relleno.
"""
import json
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    from odoo import api, SUPERUSER_ID
    from odoo.addons.crm_marketing_and_comunications.models.linkedin_prosp import (
        is_test_ping,
    )

    env = api.Environment(cr, SUPERUSER_ID, {})
    capturas = env['marketing.linkedin.capture'].search([
        ('is_test', '=', False),
        ('state', '!=', 'ignored'),
    ])
    corregidas = env['marketing.linkedin.capture']
    for captura in capturas:
        try:
            payload = json.loads(captura.payload or '{}')
        except ValueError:
            continue
        if is_test_ping(payload):
            corregidas |= captura

    if corregidas:
        corregidas.write({
            'is_test': True,
            'state': 'ignored',
            'reprocess_result': 'Envío de prueba de Prosp: sin datos reales.',
        })
        _logger.info('Prosp: %s capturas de prueba reclasificadas', len(corregidas))
