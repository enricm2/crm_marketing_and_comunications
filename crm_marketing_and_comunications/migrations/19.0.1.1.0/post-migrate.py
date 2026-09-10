"""Convierte las plantillas de email guardadas como documento HTML completo
en fragmentos editables.

Motivo: en Odoo 19 el widget `html` deja el campo en SOLO LECTURA en cuanto el
contenido produce un `<head>` no vacío (computeContainsComplexHTML, en
addons/html_editor/static/src/fields/html_field.js). Como el generador escribía
`<!DOCTYPE html><html><head>…`, toda plantilla nacía bloqueada y solo se podía
tocar por vista de código.

Aquí se les quita el envoltorio. El documento completo se vuelve a añadir en el
momento del envío (`_wrap_email_document`), que es donde de verdad hace falta.

Se rescata además el `<title>` al campo `subject` cuando está vacío, porque la
IA solía poner ahí el asunto y de lo contrario se perdería.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    cr.execute("""
        SELECT id, base_html, subject
        FROM marketing_ai_template
        WHERE base_html IS NOT NULL
          AND base_html <> ''
          AND base_html ILIKE '%%<html%%'
    """)
    rows = cr.fetchall()
    if not rows:
        _logger.info('marketing_ai_template: no hay plantillas que convertir')
        return

    # Se importa la clase para reutilizar exactamente la misma lógica que usa
    # el módulo en caliente; duplicarla aquí acabaría divergiendo.
    from odoo.addons.crm_marketing_and_comunications.models.marketing_ai_template import (
        MarketingAiTemplate,
    )

    convertidas = 0
    for tpl_id, base_html, subject in rows:
        try:
            fragmento = MarketingAiTemplate._strip_email_document(base_html)
            if fragmento == base_html:
                continue
            nuevo_subject = subject
            if not subject:
                nuevo_subject = MarketingAiTemplate._extract_doc_title(base_html) or None
            cr.execute(
                "UPDATE marketing_ai_template SET base_html = %s, subject = %s WHERE id = %s",
                (fragmento, nuevo_subject, tpl_id),
            )
            convertidas += 1
        except Exception:  # noqa: BLE001 — una plantilla rara no debe abortar la migración
            _logger.exception(
                'marketing_ai_template %s: no se pudo convertir a fragmento; '
                'se deja como estaba', tpl_id,
            )

    _logger.info(
        'marketing_ai_template: %s de %s plantillas convertidas a fragmento editable',
        convertidas, len(rows),
    )
