# -*- coding: utf-8 -*-
from odoo import models, fields, api
from odoo.exceptions import UserError

class MarketingLinkedinConvertOpportunity(models.TransientModel):
    _name = 'marketing.linkedin.convert.opportunity'
    _description = 'Convertir Leads de LinkedIn a Oportunidades'

    lead_ids = fields.Many2many(
        'crm.lead', 
        string='Leads seleccionados', 
        required=True
    )
    stage_id = fields.Many2one(
        'crm.stage', 
        string='Etapa de destino', 
        required=True,
        help='Etapa a la que se moverán las oportunidades.'
    )
    user_id = fields.Many2one(
        'res.users', 
        string='Comercial',
        domain="[('share', '=', False)]",
        help='Opcional. Comercial al que se asignarán las oportunidades.'
    )
    team_id = fields.Many2one(
        'crm.team', 
        string='Equipo de ventas',
        help='Opcional. Equipo de ventas al que se asignarán las oportunidades.'
    )
    
    total_leads = fields.Integer(
        string='Total de leads', 
        compute='_compute_resumen'
    )
    num_leads_to_convert = fields.Integer(
        string='Nº de leads a convertir', 
        compute='_compute_resumen'
    )
    num_opps_to_move = fields.Integer(
        string='Oportunidades a mover', 
        compute='_compute_resumen'
    )
    resumen = fields.Html(
        string='Resumen de la acción', 
        compute='_compute_resumen'
    )

    @api.depends('lead_ids')
    def _compute_resumen(self):
        for w in self:
            leads = w.lead_ids
            w.total_leads = len(leads)
            leads_to_convert = leads.filtered(lambda l: l.type == 'lead')
            opps_to_move = leads - leads_to_convert
            w.num_leads_to_convert = len(leads_to_convert)
            w.num_opps_to_move = len(opps_to_move)

            partes = []
            if leads_to_convert:
                partes.append(f'Se convertirán <b>{len(leads_to_convert)}</b> lead(s) en oportunidad(es).')
            if opps_to_move:
                partes.append(f'Se actualizará la etapa/comercial de <b>{len(opps_to_move)}</b> oportunidad(es) existente(s).')
            
            w.resumen = '<br/>'.join(partes) if partes else 'No hay leads seleccionados.'

    def action_convert(self):
        self.ensure_one()
        if not self.lead_ids:
            raise UserError("No hay ningún lead seleccionado para convertir.")
        
        vals = {
            'stage_id': self.stage_id.id,
        }
        if self.user_id:
            vals['user_id'] = self.user_id.id
        if self.team_id:
            vals['team_id'] = self.team_id.id
        
        for lead in self.lead_ids:
            lead_vals = vals.copy()
            if lead.type == 'lead':
                lead_vals['type'] = 'opportunity'
            lead.write(lead_vals)
            
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Leads convertidos',
                'message': f'Se han procesado {len(self.lead_ids)} leads correctamente.',
                'type': 'success',
                'next': {'type': 'ir.actions.client', 'tag': 'reload'},
            },
        }
