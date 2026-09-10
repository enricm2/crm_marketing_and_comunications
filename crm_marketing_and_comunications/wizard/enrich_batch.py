"""Confirmación antes de enriquecer un lote de leads.

Enriquecer no es gratis ni instantáneo: cada lead son 15-40 segundos entre
scraping de su web y llamada a la IA, y consume cuota de la API. Lanzarlo sobre
una selección de cien leads sin avisar es caro y difícil de parar. Este
asistente dice exactamente qué va a pasar antes de empezar.
"""

from odoo import api, fields, models
from odoo.exceptions import UserError


class CrmLeadEnrichBatch(models.TransientModel):
    _name = 'crm.lead.enrich.batch'
    _description = 'Enriquecer lote de leads'

    lead_ids = fields.Many2many('crm.lead', string='Leads')
    alcance = fields.Selection([
        ('pendientes', 'Solo los que faltan'),
        ('todos', 'Todos los seleccionados (rehacer los ya enriquecidos)'),
    ], string='Qué enriquecer', default='pendientes', required=True)

    n_total = fields.Integer(compute='_compute_resumen')
    n_procesar = fields.Integer(compute='_compute_resumen')
    n_ya_hechos = fields.Integer(compute='_compute_resumen')
    n_sin_datos = fields.Integer(compute='_compute_resumen')
    resumen = fields.Html(compute='_compute_resumen')

    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        ids = self.env.context.get('active_ids') or []
        if ids:
            vals['lead_ids'] = [(6, 0, ids)]
        return vals

    @api.depends('lead_ids', 'alcance')
    def _compute_resumen(self):
        for w in self:
            leads = w.lead_ids
            # Sin URL ni dominio de email no hay nada que raspar: se descartan
            # antes de gastar una llamada que va a fallar igual.
            sin_datos = leads.filtered(lambda l: not l._puede_enriquecerse())
            utiles = leads - sin_datos
            ya = utiles.filtered(lambda l: l.enrichment_state == 'done')
            objetivo = utiles if w.alcance == 'todos' else (utiles - ya)

            w.n_total = len(leads)
            w.n_ya_hechos = len(ya)
            w.n_sin_datos = len(sin_datos)
            w.n_procesar = len(objetivo)

            minutos = max(1, round(len(objetivo) * 25 / 60))
            partes = [f'<b>Se van a enriquecer {len(objetivo)} lead(s)</b> '
                      f'de los {len(leads)} seleccionados.']
            if ya and w.alcance != 'todos':
                partes.append(f'{len(ya)} ya estaban enriquecidos y se omiten.')
            if sin_datos:
                partes.append(
                    f'<span style="color:#c0392b">{len(sin_datos)} no tienen URL '
                    'ni email con dominio propio: no se pueden enriquecer.</span>')
            if objetivo:
                partes.append(
                    f'<i>Tardará unos {minutos} minuto(s). Se hace en segundo '
                    'plano: puedes seguir trabajando y recibirás un aviso por '
                    'cada lead.</i>')
            w.resumen = '<br/>'.join(partes)

    def action_enriquecer(self):
        self.ensure_one()
        leads = self.lead_ids.filtered(lambda l: l._puede_enriquecerse())
        if self.alcance != 'todos':
            leads = leads.filtered(lambda l: l.enrichment_state != 'done')
        if not leads:
            raise UserError(
                'No queda ningún lead que enriquecer con esos criterios.\n\n'
                'O ya están todos hechos, o ninguno tiene URL ni email con '
                'dominio propio del que sacar información.'
            )
        # Los que no se pueden tocar quedan marcados, para que no vuelvan a
        # aparecer como "pendientes" eternamente.
        sin_datos = self.lead_ids.filtered(
            lambda l: not l._puede_enriquecerse() and l.enrichment_state != 'skipped')
        if sin_datos:
            sin_datos.write({
                'enrichment_state': 'skipped',
                'enrichment_error': 'Sin URL de empresa ni email con dominio propio.',
            })
        return leads.action_enrich_leads_bulk()
