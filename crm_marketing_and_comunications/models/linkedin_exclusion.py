"""Categorías de clientes a excluir de la prospección.

Antes la exclusión era un único campo de texto libre (`icp_exclude_keywords`),
que sigue existiendo para exclusiones sueltas y rápidas. Este modelo permite
algo distinto: agrupar por CATEGORÍA (desarrollo de software e IA, ONG, función
pública…), activar o desactivar cada una de un clic, y ver cuántos leads afecta.

Un lead que caiga en una categoría activa recibe **puntuación 0**, salvo que se
marque la excepción manual en su ficha.
"""

from odoo import api, fields, models


class MarketingLinkedinExclusion(models.Model):
    _name = 'marketing.linkedin.exclusion'
    _description = 'Categoría de clientes a excluir'
    _order = 'sequence, name'

    profile_id = fields.Many2one(
        'marketing.linkedin.profile',
        string='Perfil',
        required=True,
        ondelete='cascade',
        index=True,
    )
    sequence = fields.Integer(default=10)
    name = fields.Char(
        string='Categoría',
        required=True,
        help='Ej: "Desarrollo de software e IA", "ONG y tercer sector", '
             '"Administración pública".',
    )
    keywords = fields.Char(
        string='Palabras clave',
        required=True,
        help='Separadas por comas. Se buscan en el NOMBRE DE EMPRESA y en el '
             'CARGO del contacto, sin distinguir mayúsculas ni acentos. '
             'Ej: "software, saas, inteligencia artificial, machine learning".',
    )
    active = fields.Boolean(
        string='Activa',
        default=True,
        help='Desactívala para dejar de excluir esta categoría sin borrarla.',
    )
    note = fields.Char(string='Nota', help='Por qué se excluye. Solo informativo.')

    lead_count = fields.Integer(
        string='Leads afectados',
        compute='_compute_lead_count',
        help='Leads actualmente excluidos por esta categoría.',
    )

    _sql_constraints = [
        ('unique_name_per_profile', 'UNIQUE(profile_id, name)',
         'Ya existe una categoría de exclusión con ese nombre en este perfil.'),
    ]

    def _compute_lead_count(self):
        for rec in self:
            rec.lead_count = self.env['crm.lead'].search_count([
                ('x_linkedin_excluded_by', '=', rec.id),
            ]) if rec.id else 0

    def action_view_leads(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Leads excluidos — {self.name}',
            'res_model': 'crm.lead',
            'view_mode': 'list,form',
            'domain': [('x_linkedin_excluded_by', '=', self.id)],
        }

    # ── Categorías por defecto ────────────────────────────────────────────────

    @api.model
    def _default_categories(self):
        """Punto de partida razonable. El usuario las ajusta después."""
        return [
            {
                'name': 'Desarrollo de software e IA',
                'keywords': 'software, saas, desarrollo web, programación, '
                            'ia, ai, inteligencia artificial, machine learning, '
                            'data science, devops, cloud, ciberseguridad, '
                            'consultora tecnológica, it services',
                'note': 'Competencia directa o proveedores del mismo servicio',
                'sequence': 10,
            },
            {
                'name': 'ONG y tercer sector',
                'keywords': 'ong, fundación, fundacion, asociación, asociacion, '
                            'sin ánimo de lucro, sin animo de lucro, ngo, '
                            'organización benéfica, cooperación, voluntariado',
                'note': 'Sin presupuesto para este tipo de servicio',
                'sequence': 20,
            },
            {
                'name': 'Administración pública',
                'keywords': 'ayuntamiento, diputación, diputacion, generalitat, '
                            'ministerio, consellería, conselleria, junta de, '
                            'gobierno de, administración pública, '
                            'administracion publica, organismo público, '
                            'universidad pública, universidad publica',
                'note': 'Requiere licitación pública; ciclo de venta incompatible',
                'sequence': 30,
            },
            {
                'name': 'Educación y formación',
                'keywords': 'universidad, colegio, instituto, escuela, academia, '
                            'centro de formación, centro de formacion, fp, '
                            'máster, master',
                'note': '',
                'sequence': 40,
            },
            {
                'name': 'Búsqueda de empleo y RRHH',
                'keywords': 'recruiter, headhunter, selección de personal, '
                            'seleccion de personal, ett, recursos humanos, '
                            'talent acquisition, estudiante, becario',
                'note': 'No son compradores del servicio',
                'sequence': 50,
            },
        ]

    @api.model
    def crear_categorias_por_defecto(self, profile):
        """Siembra las categorías por defecto en un perfil que no tenga ninguna."""
        if self.search_count([('profile_id', '=', profile.id)]):
            return self.browse()
        return self.create([
            dict(c, profile_id=profile.id) for c in self._default_categories()
        ])
