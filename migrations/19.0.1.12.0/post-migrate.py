"""Marca como «enriquecidos» los leads que ya tenían datos de la IA.

Los demás se dejan SIN estado a propósito, no como «pendientes»: hay miles de
leads antiguos que nadie pidió enriquecer, y marcarlos pendientes llenaría el
filtro de ruido y daría a entender que hay trabajo atrasado que no existe.
Pendiente es, a partir de ahora, lo que entra nuevo.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("""
        UPDATE crm_lead
           SET enrichment_state = 'done'
         WHERE enrichment_date IS NOT NULL
           AND enrichment_state IS NULL
    """)
    _logger.info('Enriquecimiento: %s leads marcados como ya enriquecidos', cr.rowcount)
