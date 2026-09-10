"""Asistente para cargar un CSV de LinkedIn directamente en Odoo.

Es la vía sin intermediarios: exportas la analítica desde LinkedIn y sueltas el
fichero aquí. No hace falta n8n, ni Google Drive, ni credenciales de terceros,
porque toda la lógica —deduplicar, puntuar, cruzar con el CRM— ya vive en el
módulo. El asistente solo traduce el CSV a filas y llama al mismo `ingest()`
que usa el endpoint HTTP, así que las dos vías se comportan igual y no hay una
segunda implementación que mantener sincronizada.
"""
import base64
import csv
import io
import logging

from odoo import models, fields, api
from odoo.exceptions import UserError

from ..models.linkedin_common import SIGNAL_TYPES, normalize_signal_type
from .linkedin_paste_parser import parse_linkedin_paste

_logger = logging.getLogger(__name__)

# Cabeceras reales de las exportaciones de LinkedIn y Sales Navigator,
# traducidas a las claves que entiende `marketing.linkedin.signal._row_to_vals`.
# LinkedIn cambia estos nombres cada pocos meses y varían por idioma de la
# cuenta, así que la lista es deliberadamente generosa: es más barato aceptar
# un sinónimo de más que obligar a renombrar columnas a mano antes de importar.
HEADER_ALIASES = {
    # Nombre
    'first name': 'first_name',
    'nombre': 'first_name',
    'firstname': 'first_name',
    'last name': 'last_name',
    'apellidos': 'last_name',
    'apellido': 'last_name',
    'lastname': 'last_name',
    'full name': 'contact_name',
    'name': 'contact_name',
    'nombre completo': 'contact_name',
    'member': 'contact_name',
    'miembro': 'contact_name',
    # Perfil
    'profile link': 'linkedin_url',
    'profile url': 'linkedin_url',
    'linkedin profile': 'linkedin_url',
    'perfil': 'linkedin_url',
    'url del perfil': 'linkedin_url',
    'enlace del perfil': 'linkedin_url',
    'public profile url': 'linkedin_url',
    # Empresa y cargo
    'company': 'company_name',
    'company name': 'company_name',
    'empresa': 'company_name',
    'current company': 'company_name',
    'organization': 'company_name',
    'position': 'job_title',
    'title': 'job_title',
    'job title': 'job_title',
    'cargo': 'job_title',
    'puesto': 'job_title',
    'headline': 'job_title',
    'titular': 'job_title',
    'current position': 'job_title',
    # Otros datos
    'location': 'location',
    'ubicacion': 'location',
    'ubicación': 'location',
    'email address': 'email',
    'email': 'email',
    'correo': 'email',
    'phone': 'phone',
    'telefono': 'phone',
    'teléfono': 'phone',
    # La señal
    'date': 'signal_date',
    'fecha': 'signal_date',
    'viewed on': 'signal_date',
    'connected on': 'signal_date',
    'fecha de conexion': 'signal_date',
    'fecha de conexión': 'signal_date',
    'interaction date': 'signal_date',
    'type': 'signal_type',
    'tipo': 'signal_type',
    'interaction': 'signal_type',
    'interaction type': 'signal_type',
    'comment': 'comment_text',
    'comentario': 'comment_text',
    'comment text': 'comment_text',
    'message': 'comment_text',
}


class LinkedinImportWizard(models.TransientModel):
    _name = 'linkedin.import.wizard'
    _description = 'Importar señales de LinkedIn desde CSV'

    profile_id = fields.Many2one(
        'marketing.linkedin.profile',
        string='Perfil',
        required=True,
        default=lambda self: self.env['marketing.linkedin.profile']._get_default_profile(),
        help='Perfil al que se atribuyen las señales importadas.',
    )
    input_mode = fields.Selection([
        ('file', 'Fichero CSV'),
        ('text', 'Texto copiado de LinkedIn'),
    ], string='Origen de los datos', default='file', required=True,
        help='LinkedIn no exporta quién comenta o reacciona a una publicación: '
             'la analítica descargable trae totales, no personas. Para eso hay '
             'que abrir la lista, seleccionarla y pegarla aquí.',
    )
    # `required` no va en el campo sino en la vista: en modo texto no hay fichero.
    csv_file = fields.Binary(string='Fichero CSV', attachment=False)
    raw_text = fields.Text(
        string='Pega aquí lo copiado de LinkedIn',
        help='Abre la lista de reacciones o la sección de comentarios de tu '
             'publicación, selecciónala entera y pégala. Da igual que venga con '
             'ruido: se limpia y se muestra para revisar antes de importar nada.',
    )
    line_ids = fields.One2many(
        'linkedin.import.line', 'wizard_id', string='Personas detectadas',
    )
    skipped_note = fields.Char(string='Aviso', readonly=True)
    csv_filename = fields.Char(string='Nombre del fichero')

    default_signal_type = fields.Selection(
        SIGNAL_TYPES,
        string='Tipo de señal del fichero',
        help='Qué representa este fichero. Las exportaciones de LinkedIn no '
             'suelen traer columna de tipo: el listado de "quién vio tu perfil" '
             'son todo vistas de perfil, el de reacciones de un post son todo '
             'reacciones. Si el CSV sí trae columna de tipo, esta se usa solo '
             'para las filas que la tengan vacía.',
    )
    post_id = fields.Many2one(
        'marketing.linkedin.post',
        string='Publicación',
        domain="[('profile_id', '=', profile_id)]",
        help='Rellénalo si el fichero son los comentarios o reacciones de una '
             'publicación concreta. Vincula todas las señales a ella.',
    )
    delimiter = fields.Selection([
        ('auto', 'Detectar automáticamente'),
        (',', 'Coma (,)'),
        (';', 'Punto y coma (;)'),
        ('\t', 'Tabulador'),
    ], string='Separador', default='auto', required=True)

    state = fields.Selection([
        ('choose', 'Origen'),
        ('review', 'Revisar'),
        ('done', 'Resultado'),
    ], default='choose')
    result_html = fields.Html(string='Resultado', readonly=True, sanitize=False)
    batch_id = fields.Many2one('marketing.linkedin.batch', string='Lote', readonly=True)

    # ── Lectura del fichero ───────────────────────────────────────────────────

    def _decode(self):
        """Devuelve el texto del CSV probando codificaciones habituales.

        LinkedIn exporta en UTF-8 con BOM, pero un fichero que haya pasado por
        Excel en un Windows español acaba en cp1252. Probar en orden evita el
        clásico "Cerámicas" convertido en "CerÃ¡micas" en el nombre del lead.
        """
        self.ensure_one()
        raw = base64.b64decode(self.csv_file or b'')
        if not raw:
            raise UserError('El fichero está vacío.')
        for encoding in ('utf-8-sig', 'utf-8', 'cp1252', 'latin-1'):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        raise UserError(
            'No se pudo leer el fichero: la codificación no es ninguna de las '
            'habituales (UTF-8, Windows-1252). Vuelve a exportarlo o guárdalo '
            'como CSV UTF-8.'
        )

    def _get_delimiter(self, text):
        self.ensure_one()
        if self.delimiter != 'auto':
            return self.delimiter
        # Se mira la línea con más separadores de entre las diez primeras, no
        # la primera: el Connections.csv de LinkedIn empieza con un bloque de
        # texto corrido donde no hay ningún separador.
        candidates = [line for line in text.splitlines()[:10] if line.strip()]
        counts = {
            sep: max((line.count(sep) for line in candidates), default=0)
            for sep in (',', ';', '\t')
        }
        best = max(counts, key=lambda sep: counts[sep])
        return best if counts[best] else ','

    @staticmethod
    def _normalize_header(name):
        """Traduce una cabecera del CSV a nuestra clave interna."""
        key = (name or '').strip().lower().lstrip('﻿')
        return HEADER_ALIASES.get(key, key)

    def _strip_preamble(self, text, delimiter):
        """Descarta las líneas anteriores a la cabecera real.

        El `Connections.csv` de "Obtener una copia de tus datos" no empieza por
        la cabecera: LinkedIn antepone un bloque «Notes:» advirtiendo de que
        faltan algunos emails. Sin saltarlo, `DictReader` toma «Notes:» como
        única columna y un fichero perfectamente válido falla entero.

        Se busca la primera línea con al menos dos columnas reconocibles: es
        más fiable que contar líneas, porque el preámbulo cambia de tamaño
        según el idioma y la versión de la exportación.
        """
        known_keys = set(HEADER_ALIASES.values())
        lines = text.splitlines()
        for index, line in enumerate(lines):
            fields = [self._normalize_header(part) for part in line.split(delimiter)]
            if sum(1 for field in fields if field in known_keys) >= 2:
                return '\n'.join(lines[index:])
        return text

    def _parse(self, text):
        """Convierte el CSV en la lista de filas que espera `ingest()`."""
        self.ensure_one()
        delimiter = self._get_delimiter(text)
        text = self._strip_preamble(text, delimiter)
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        if not reader.fieldnames:
            raise UserError('El fichero no tiene cabecera de columnas.')

        rows, skipped = [], 0
        for raw_row in reader:
            row = {}
            for header, value in raw_row.items():
                if header is None:
                    continue
                key = self._normalize_header(header)
                if value not in (None, ''):
                    row[key] = str(value).strip()

            # Nombre y apellidos en columnas separadas, que es lo normal.
            if not row.get('contact_name'):
                parts = [row.pop('first_name', ''), row.pop('last_name', '')]
                joined = ' '.join(p for p in parts if p).strip()
                if joined:
                    row['contact_name'] = joined
            row.pop('first_name', None)
            row.pop('last_name', None)

            # Tipo: el de la fila si viene y se reconoce; si no, el general del
            # asistente. Si tampoco hay, la fila se queda SIN tipo y se manda a
            # la pantalla de revisión para rellenarlo. Antes esto abortaba la
            # importación entera con un error, que es lo peor que puede hacer:
            # el usuario ya tenía los datos delante y se quedaba sin nada.
            resolved = normalize_signal_type(row.get('signal_type'))
            row['signal_type'] = resolved or self.default_signal_type or ''

            if self.post_id:
                row.setdefault('post_id', self.post_id.external_id or '')

            if not row.get('contact_name') and not row.get('linkedin_url'):
                skipped += 1
                continue
            rows.append(row)

        if not rows:
            raise UserError(
                'No se ha podido leer ninguna fila con nombre o URL de '
                'LinkedIn.\n\nCabeceras encontradas: '
                f'{", ".join(reader.fieldnames)}\n\n'
                'Comprueba el separador y que el fichero sea la exportación '
                'de LinkedIn y no otra cosa.'
            )
        return rows, skipped

    # ── Importación ───────────────────────────────────────────────────────────

    def action_parse_text(self):
        """Interpreta el texto pegado y pasa a la pantalla de revisión.

        No se importa directamente a propósito. La lectura del pegado es
        heurística —LinkedIn no da un formato, da una pantalla— y meter en el
        CRM lo que salga sin que nadie lo mire acabaría creando contactos
        llamados «Me gusta». Aquí se ve, se corrige y se descarta lo que sobre.
        """
        self.ensure_one()
        if not (self.raw_text or '').strip():
            raise UserError('Pega primero el texto copiado de LinkedIn.')
        if not self.default_signal_type:
            raise UserError(
                'Elige "Tipo de señal del fichero": si has copiado la lista de '
                'reacciones son reacciones, y si has copiado los comentarios '
                'son comentarios. Sin eso no se puede puntuar la señal.'
            )

        people = parse_linkedin_paste(self.raw_text)
        if not people:
            # Enseñar lo que sí llegó: sin esto, un formato de LinkedIn que no
            # sepamos leer deja al usuario sin ninguna pista de qué corregir.
            muestra = [
                linea.strip() for linea in (self.raw_text or '').splitlines()
                if linea.strip()
            ][:5]
            detalle = '\n'.join(f'  · {linea[:90]}' for linea in muestra)
            raise UserError(
                'No he reconocido ninguna persona en el texto pegado.\n\n'
                'Esto es lo primero que he leído:\n'
                f'{detalle}\n\n'
                'Asegúrate de copiar la lista con los nombres visibles (la '
                'ventana de reacciones o la sección de comentarios), no solo '
                'el texto de los mensajes. Si el formato parece correcto, '
                'pásaselo a soporte con estas líneas: puede ser una variante '
                'de LinkedIn que todavía no se lee.'
            )

        self.line_ids.unlink()
        self.write({
            'state': 'review',
            'line_ids': [(0, 0, {
                'contact_name': person['contact_name'],
                'job_title': person['job_title'],
                'company_name': person['company_name'],
                'linkedin_url': person['linkedin_url'],
                'comment_text': person['comment_text'],
                'signal_date': self._parse_paste_date(person.get('signal_date')),
                'source_line': person['source_line'],
            }) for person in people],
            'skipped_note': f'{len(people)} personas detectadas. Revisa la lista, '
                            f'corrige lo que haga falta y desmarca lo que no sea '
                            f'un contacto.',
        })
        return self._reopen()

    @api.model
    def _parse_paste_date(self, raw):
        """Fecha del «Conectó el …» en formato de Odoo, o False si no se entiende.

        Reutiliza el analizador de fechas de las señales, que ya sabe de meses
        escritos con letras en español e inglés. Si no lo reconoce se devuelve
        False y la señal usará la fecha de importación: es peor para deduplicar,
        pero nunca motivo para rechazar la fila.
        """
        if not raw:
            return False
        parsed = self.env['marketing.linkedin.signal']._parse_datetime(raw)
        return parsed.date() if parsed else False

    def action_back_to_choose(self):
        self.ensure_one()
        self.write({'state': 'choose'})
        return self._reopen()

    def _reopen(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'linkedin.import.wizard',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def _to_review_from_rows(self, rows, skipped):
        """Lleva las filas del CSV a la pantalla de revisión editable.

        Se usa cuando el fichero no dice qué tipo de interacción representa y
        tampoco se ha elegido uno general. En vez de rechazar el fichero, se
        enseña lo leído y se pide el dato que falta sobre los datos ya cargados.
        """
        self.ensure_one()
        self.line_ids.unlink()
        faltan = sum(1 for row in rows if not row.get('signal_type'))
        aviso = (
            f'{len(rows)} filas leídas del fichero. '
            f'{faltan} no dicen qué tipo de interacción son: rellena la columna '
            f'"Tipo de señal", o elige uno general y se aplicará a las vacías. '
            f'Sin tipo no se puede puntuar la señal, así que esas filas no se '
            f'importarán.'
        )
        self.write({
            'state': 'review',
            'skipped_note': aviso,
            'line_ids': [(0, 0, {
                'contact_name': row.get('contact_name') or '',
                'company_name': row.get('company_name') or '',
                'job_title': row.get('job_title') or '',
                'linkedin_url': row.get('linkedin_url') or '',
                'comment_text': row.get('comment_text') or '',
                'signal_type': row.get('signal_type') or False,
                'source_line': row.get('contact_name') or '',
            }) for row in rows],
        })
        return self._reopen()

    def _rows_from_lines(self):
        """Filas a importar a partir de la lista revisada."""
        self.ensure_one()
        rows = []
        for line in self.line_ids.filtered('include'):
            tipo = line.signal_type or self.default_signal_type
            if not (line.contact_name or '').strip() or not tipo:
                continue
            row = {
                'contact_name': line.contact_name,
                'tipo_senal': tipo,
            }
            if line.company_name:
                row['empresa'] = line.company_name
            if line.job_title:
                row['cargo'] = line.job_title
            if line.linkedin_url:
                row['linkedin_url'] = line.linkedin_url
            if line.comment_text:
                row['comentario'] = line.comment_text
            if line.signal_date:
                row['fecha_senal'] = fields.Date.to_string(line.signal_date)
            if self.post_id:
                row['post_id'] = self.post_id.external_id or ''
            rows.append(row)
        return rows

    def action_import(self):
        self.ensure_one()
        if self.state == 'review':
            rows, skipped = self._rows_from_lines(), 0
            if not rows:
                marcadas = len(self.line_ids.filtered('include'))
                raise UserError(
                    'No hay ninguna fila lista para importar.\n\n'
                    + (f'Tienes {marcadas} filas marcadas, pero les falta el '
                       'nombre o el tipo de señal. Rellena la columna "Tipo de '
                       'señal", o elige un tipo general arriba para aplicarlo a '
                       'todas las vacías.' if marcadas else
                       'No queda ninguna fila marcada.')
                )
        else:
            if not self.csv_file:
                raise UserError('Elige un fichero CSV o cambia a "Texto copiado '
                                'de LinkedIn" y pega la lista.')
            rows, skipped = self._parse(self._decode())
            sin_tipo = [row for row in rows if not row.get('signal_type')]
            if sin_tipo:
                return self._to_review_from_rows(rows, skipped)

        batch = self.env['marketing.linkedin.batch'].create({
            'profile_id': self.profile_id.id,
            'source': 'manual',
            'source_file': self.csv_filename or (
                'texto pegado de LinkedIn' if self.input_mode == 'text'
                else 'importación manual'
            ),
        })
        summary = self.env['marketing.linkedin.signal'].ingest(
            self.profile_id, rows, batch=batch,
        )
        batch.apply_summary(summary)

        # Las señales de un fichero de una publicación concreta se enganchan
        # aquí: `post_id` puede no haberse resuelto por referencia externa si
        # la publicación todavía no tiene URN de LinkedIn.
        if self.post_id and summary.get('signal_ids'):
            self.env['marketing.linkedin.signal'].browse(
                summary['signal_ids']
            ).write({'post_id': self.post_id.id})

        _logger.info(
            'LinkedIn: importación manual (%s) de %s — %s filas, %s nuevas, '
            '%s duplicadas, %s ignoradas, %s errores.',
            self.input_mode, self.csv_filename or '(pegado)',
            summary['received'], summary['created'],
            summary['duplicated'], summary['ignored'], summary['errors'],
        )

        self.write({
            'state': 'done',
            'batch_id': batch.id,
            'result_html': self._render_result(summary, skipped),
        })
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'linkedin.import.wizard',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def _render_result(self, summary, skipped):
        """Resumen legible de la importación."""
        self.ensure_one()
        rows = [
            ('Filas leídas', summary['received']),
            ('Señales nuevas', summary['created']),
            ('Ya estaban (duplicadas)', summary['duplicated']),
            ('Ignoradas por tipo no seguido', summary['ignored']),
            ('Leads creados', summary['leads_created']),
            ('Leads actualizados', summary['leads_updated']),
            ('Señales calientes', summary['hot']),
            ('Errores', summary['errors']),
        ]
        body = ''.join(
            f'<tr><td>{label}</td><td class="text-end"><b>{value}</b></td></tr>'
            for label, value in rows
        )
        extra = ''
        if skipped:
            extra += (f'<p class="text-muted">{skipped} filas sin nombre ni URL '
                      f'de LinkedIn se han saltado.</p>')
        if summary['errors']:
            detail = ''.join(
                f'<li>Fila {e["row"]}: {e["error"]}</li>'
                for e in summary['error_details'][:15]
            )
            extra += (f'<div class="alert alert-warning mt8">'
                      f'<b>Filas que no se pudieron procesar:</b><ul>{detail}</ul></div>')
        if summary['duplicated'] and not summary['created']:
            extra += ('<p class="text-muted">Todo el fichero estaba ya importado. '
                      'Reimportar es inocuo: no duplica señales ni infla puntuaciones.</p>')
        return f'<table class="table table-sm">{body}</table>{extra}'

    def action_view_signals(self):
        """Abre las señales del lote recién importado.

        Con guarda explícita: si el botón se pulsa sin lote —por un estado de
        la interfaz que no habíamos previsto— es preferible un mensaje que
        explique qué pasa a un error de servidor con traza.
        """
        self.ensure_one()
        if not self.batch_id:
            raise UserError(
                'Todavía no hay ninguna importación que mirar. Elige un fichero '
                'CSV y pulsa "Importar" primero.'
            )
        return self.batch_id.action_view_signals()

    def action_import_another(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'linkedin.import.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_profile_id': self.profile_id.id},
        }


class LinkedinImportLine(models.TransientModel):
    """Una persona detectada en el texto pegado, antes de importarla.

    Existe para que la interpretación heurística del pegado sea revisable: se
    ve lo que se ha entendido, se corrige y se desmarca lo que no toca. Es la
    diferencia entre una herramienta en la que se puede confiar y una que
    ensucia el CRM en silencio.
    """
    _name = 'linkedin.import.line'
    _description = 'Persona detectada en un pegado de LinkedIn'
    _order = 'id'

    wizard_id = fields.Many2one(
        'linkedin.import.wizard', required=True, ondelete='cascade', index=True,
    )
    include = fields.Boolean(
        string='Importar', default=True,
        help='Desmarca lo que no sea un contacto real.',
    )
    contact_name = fields.Char(string='Nombre')
    signal_type = fields.Selection(
        SIGNAL_TYPES, string='Tipo de señal',
        help='Qué interacción representa esta fila. Si lo dejas vacío se usa '
             'el tipo general elegido arriba.',
    )
    company_name = fields.Char(string='Empresa')
    job_title = fields.Char(string='Cargo')
    linkedin_url = fields.Char(string='URL de LinkedIn')
    comment_text = fields.Text(string='Qué escribió')
    signal_date = fields.Date(
        string='Fecha de la señal',
        help='Cuándo ocurrió. Se lee del «Conectó el …» del pegado. Si queda '
             'vacía se usa la fecha de hoy, y entonces dos importaciones en '
             'días distintos crearían dos señales para la misma persona.',
    )
    source_line = fields.Char(
        string='Texto original', readonly=True,
        help='La línea del pegado de la que salió esta persona. Sirve para '
             'comprobar de un vistazo si la interpretación fue correcta.',
    )
