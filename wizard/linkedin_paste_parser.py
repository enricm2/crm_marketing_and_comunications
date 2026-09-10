"""Interpreta el bloque de texto que se copia de LinkedIn.

LinkedIn no ofrece exportación de quién comenta o reacciona a una publicación:
la analítica descargable trae totales, no personas. La única vía es abrir la
lista en pantalla, seleccionar y pegar. Lo que sale de ahí es ruidoso —el
nombre repetido, el grado de contacto, el titular, marcas de tiempo, los
botones «Me gusta» y «Responder»— y varía según el idioma de la cuenta y de si
se copió de comentarios o de reacciones.

Por eso esto es un heurístico declarado, no un parser: acierta la mayoría de
las veces y se equivoca en algunas. La decisión de diseño que lo hace seguro no
está aquí sino en el asistente: **lo interpretado se muestra en una lista
editable antes de importar nada**. Preferimos que se vea y se corrija a colar
en el CRM un lead llamado «Me gusta».
"""
import re

from ..models.linkedin_common import split_headline

# Grado de contacto: «· 2º», «• 3er», «· 1st», «- 2nd»…
DEGREE_RE = re.compile(
    r'^[·•\-–\*\s]*(1|2|3)\s*(º|°|er|ro|nd|rd|st|th|do)?\s*(grado|degree)?[·•\-–\s]*$',
    re.IGNORECASE,
)
# Marcas de tiempo relativas: «2 h», «3 d», «1 sem», «hace 2 horas», «2w», «1 mo»
TIME_RE = re.compile(
    r'^(hace\s+)?\d+\s*(s|m|h|d|w|y|min|mins|sem|sems|mes|meses|mo|a|años?|'
    r'segundos?|minutos?|horas?|d[ií]as?|semanas?)\.?$',
    re.IGNORECASE,
)
# Botones y etiquetas de la interfaz que se cuelan al copiar.
NOISE_EXACT = {
    'me gusta', 'like', 'responder', 'reply', 'seguir', 'follow', 'siguiendo',
    'following', 'conectar', 'connect', 'mensaje', 'message', 'celebrar',
    'celebrate', 'recomendar', 'recommend', 'apoyo', 'support', 'encantar',
    'love', 'interesante', 'insightful', 'me divierte', 'funny', 'curioso',
    'curious', 'ver más', 'see more', 'ver traducción', 'see translation',
    'traducir', 'translate', 'y otros', 'and others', 'autor', 'author',
    'premium', 'abrir menú', 'open menu', 'más', 'more', 'compartir', 'share',
    'enviar', 'send', '·', '•', 'respuestas', 'replies', 'respuesta', 'reply',
}
# Líneas que empiezan así son enlaces de accesibilidad, no personas.
NOISE_PREFIXES = (
    'ver el perfil de', "view ", 'ver perfil de', 'foto de', 'imagen de',
    'photo of', 'image of', 'estado:', 'status is',
)
LINKEDIN_URL_RE = re.compile(r'https?://\S*linkedin\.com/in/\S+', re.IGNORECASE)
# Grado de contacto PEGADO al nombre, que es como sale al copiar la lista de
# reacciones: «Kumud Deepali Rudraraju  • 2º», «Iain Eyre  • 3er+».
# Antes solo se descartaba cuando venía en su propia línea; con el sufijo
# dentro, la línea contenía un dígito y dejaba de parecer un nombre, así que
# no se reconocía a nadie.
DEGREE_SUFFIX_RE = re.compile(
    r'\s*[·•∙‧]\s*(?:1|2|3)\s*(?:º|°|er|ro|nd|rd|st|th|do)?\s*\+?\s*$',
    re.IGNORECASE,
)
# «1 respuesta», «3 replies», «2 comentarios»
COUNT_RE = re.compile(
    r'^\d+\s*(respuestas?|replies|reply|comentarios?|comments?|reacciones?|'
    r'reactions?|me gusta|likes?)$',
    re.IGNORECASE,
)
# Un nombre no termina en punto ni lleva interrogaciones: eso es texto de un
# comentario. Sin esta distinción, «Nos pasa exactamente lo mismo.» entraba en
# el CRM como si fuera una persona.
SENTENCE_RE = re.compile(r'[?!¿¡,;:]\s*$|[¿¡?!]')


def _ends_like_sentence(text):
    """¿El texto termina como una frase y no como un nombre?

    El punto final se trata aparte: «Nos pasa lo mismo.» es una frase, pero
    «Sundeep K. B.» es un nombre con iniciales. Se mira la última palabra —si
    tiene una o dos letras es una abreviatura, no el final de una oración.
    """
    if SENTENCE_RE.search(text):
        return True
    if not text.rstrip().endswith('.'):
        return False
    last = text.rstrip().rstrip('.').split()[-1] if text.rstrip().rstrip('.').split() else ''
    return len(last) > 2
# «Conectó el 26 de agosto de 2026», «Connected on August 26, 2026», «Sois
# contactos desde el 26 de agosto de 2026». La página "Mi red" pone esta línea
# bajo cada persona: no es su titular ni un comentario suyo, es la fecha en que
# se conectasteis — el dato exacto que necesita la señal.
CONNECTED_ON_RE = re.compile(
    r'^(?:conect(?:ó|o|aste|asteis)\s+el|sois\s+contactos?\s+desde\s+el|'
    r'contactos?\s+desde\s+el|connected\s+on|connected)\s+(?P<date>.+?)\.?$',
    re.IGNORECASE,
)
# Un titular suele decir dónde trabaja la persona.
HEADLINE_HINTS = (' en ', ' at ', ' @ ', ' | ', ' • ', ' – ', ' -- ')


def _clean_lines(raw_text):
    """Quita del pegado todo lo que no es ni nombre ni titular ni URL."""
    lines, previous = [], None
    for line in (raw_text or '').splitlines():
        text = line.strip().replace('​', '')
        text = re.sub(r'\s{2,}', ' ', text)
        if not text:
            previous = None
            continue
        lowered = text.lower().rstrip('.:')
        if lowered in NOISE_EXACT:
            continue
        if any(lowered.startswith(prefix) for prefix in NOISE_PREFIXES):
            continue
        if DEGREE_RE.match(text) or TIME_RE.match(text) or COUNT_RE.match(text):
            continue
        stripped = DEGREE_SUFFIX_RE.sub('', text).strip()
        if stripped:
            text = stripped
        # LinkedIn repite el nombre (enlace + texto visible) en líneas seguidas.
        if previous is not None and text == previous:
            continue
        lines.append(text)
        previous = text
    return lines


def _looks_like_name(text):
    """¿Esta línea nombra a una persona?

    Deliberadamente estricta: es preferible dejar fuera un nombre raro —que se
    añade a mano en la lista de revisión— a meter en el CRM la frase de un
    comentario como si fuera un contacto.
    """
    if not text or len(text) > 60:
        return False
    if _ends_like_sentence(text):
        return False
    words = text.split()
    if not 1 < len(words) <= 5:
        return False
    if any(char.isdigit() for char in text):
        return False
    # La mayoría de las palabras de un nombre empiezan en mayúscula.
    capitalised = sum(1 for word in words if word[:1].isupper())
    return capitalised >= max(2, len(words) - 1)


def _looks_like_headline(text):
    """¿Esta línea describe un puesto en vez de nombrar a una persona?"""
    if len(text) > 45:
        return True
    if any(hint in f' {text} ' for hint in HEADLINE_HINTS):
        return True
    # Los nombres rara vez pasan de cinco palabras.
    return len(text.split()) > 5


def parse_linkedin_paste(raw_text):
    """Convierte el texto pegado en una lista de personas candidatas.

    Cada línea se clasifica en una de tres cosas: nombre (abre una persona),
    titular (describe a la anterior) o texto suelto (el cuerpo de un
    comentario, que se guarda como contexto de esa persona).

    Devuelve dicts con `contact_name`, `job_title`, `company_name`,
    `linkedin_url`, `comment_text`, `signal_date` y `source_line`. Nunca
    levanta excepción.
    """
    lines = _clean_lines(raw_text)
    people, index, skipped = [], 0, []

    while index < len(lines):
        line = lines[index]

        connected = CONNECTED_ON_RE.match(line)
        if connected:
            if people and not people[-1]['signal_date']:
                people[-1]['signal_date'] = connected.group('date').strip()
            index += 1
            continue

        url_match = LINKEDIN_URL_RE.search(line)
        if url_match and len(line.split()) == 1:
            if people:
                people[-1]['linkedin_url'] = url_match.group(0)
            index += 1
            continue

        if _looks_like_name(line):
            person = {
                'contact_name': line,
                'job_title': '',
                'company_name': '',
                'linkedin_url': '',
                'comment_text': '',
                'signal_date': '',
                'source_line': line,
            }
            if index + 1 < len(lines) and _looks_like_headline(lines[index + 1]):
                job, company = split_headline(lines[index + 1])
                person['job_title'] = job
                person['company_name'] = company
                person['source_line'] = f'{line} — {lines[index + 1]}'
                index += 1
            inline_url = LINKEDIN_URL_RE.search(person['source_line'])
            if inline_url:
                person['linkedin_url'] = inline_url.group(0)
            people.append(person)
            index += 1
            continue

        if people and _looks_like_headline(line) and not people[-1]['job_title']:
            job, company = split_headline(line)
            people[-1]['job_title'] = job
            people[-1]['company_name'] = company
            index += 1
            continue

        # Texto suelto: casi siempre el cuerpo del comentario de la última
        # persona. Se guarda como contexto en vez de tirarlo: es lo mejor que
        # hay para preparar la primera conversación.
        if people and not people[-1]['comment_text']:
            people[-1]['comment_text'] = line
        else:
            skipped.append(line)
        index += 1

    # Sin URL de perfil, la identidad es nombre+empresa: dos filas iguales son
    # la misma persona repetida por el pegado, no dos contactos distintos.
    unique, seen = [], set()
    for person in people:
        key = (person['contact_name'].lower(), person['company_name'].lower())
        if key in seen:
            continue
        seen.add(key)
        unique.append(person)
    return unique
