"""Constantes compartidas del flujo LinkedIn Growth.

Este flujo es independiente del flujo de campañas de marketing (email/WhatsApp
en frío). Vive en el mismo módulo porque comparte CRM, IA y WhatsApp, pero no
cruza datos con `marketing.campaign` ni con las exclusiones RGPD de envío en
frío: aquí no se envía nada al contacto, solo se registra una señal que él ha
producido voluntariamente en LinkedIn.
"""
import re

# Tipos de señal que puede producir un contacto en LinkedIn.
# El orden importa: se usa tal cual en los selects de las vistas.
SIGNAL_TYPES = [
    ('comment', 'Comentario'),
    ('reaction', 'Reacción / Like'),
    ('share', 'Compartido'),
    ('mention', 'Mención'),
    ('profile_view', 'Vista de perfil'),
    ('connection', 'Nueva conexión'),
    ('follow', 'Nuevo seguidor'),
    ('message', 'Mensaje directo'),
]

SIGNAL_TYPE_CODES = [code for code, _label in SIGNAL_TYPES]

SIGNAL_TYPE_LABELS = dict(SIGNAL_TYPES)

# Campo de `marketing.linkedin.profile` que guarda la puntuación de cada tipo.
# Tener el mapa aquí evita repartir `if signal_type == ...` por medio módulo.
SCORE_FIELD_BY_TYPE = {
    'comment': 'score_comment',
    'reaction': 'score_reaction',
    'share': 'score_share',
    'mention': 'score_mention',
    'profile_view': 'score_profile_view',
    'connection': 'score_connection',
    'follow': 'score_follow',
    'message': 'score_message',
}

# Campo de `marketing.linkedin.profile` que dice si ese tipo se sigue o se
# ignora. Va en paralelo a SCORE_FIELD_BY_TYPE: seguir y puntuar son dos
# decisiones distintas, y un tipo puede valer 0 puntos y aun así interesar
# para el histórico.
TRACK_FIELD_BY_TYPE = {
    'comment': 'track_comment',
    'reaction': 'track_reaction',
    'share': 'track_share',
    'mention': 'track_mention',
    'profile_view': 'track_profile_view',
    'connection': 'track_connection',
    'follow': 'track_follow',
    'message': 'track_message',
}

# Sinónimos que aceptamos en la ingesta desde n8n / CSV de LinkedIn.
# LinkedIn exporta en varios idiomas y n8n puede mandar el término del prompt
# original ("like", "vista_perfil"...). Normalizamos aquí en vez de exigir que
# el flujo externo conozca nuestros códigos internos.
SIGNAL_TYPE_ALIASES = {
    'comentario': 'comment',
    'comment': 'comment',
    'comments': 'comment',
    'like': 'reaction',
    'likes': 'reaction',
    'reaccion': 'reaction',
    'reaccion_like': 'reaction',
    'reaction': 'reaction',
    'reactions': 'reaction',
    'me_gusta': 'reaction',
    'compartido': 'share',
    'share': 'share',
    'repost': 'share',
    'mencion': 'mention',
    'mention': 'mention',
    'vista_perfil': 'profile_view',
    'vista-perfil': 'profile_view',
    'profile_view': 'profile_view',
    'profileview': 'profile_view',
    'viewer': 'profile_view',
    'conexion': 'connection',
    'connection': 'connection',
    'invite': 'connection',
    'invitacion': 'connection',
    'seguidor': 'follow',
    'follow': 'follow',
    'follower': 'follow',
    'mensaje': 'message',
    'message': 'message',
    'dm': 'message',
    'inmail': 'message',
}

# Formatos de publicación de LinkedIn.
POST_FORMATS = [
    ('text', 'Texto'),
    ('image', 'Imagen'),
    ('carousel', 'Carrusel'),
    ('video', 'Vídeo'),
    ('poll', 'Encuesta'),
    ('article', 'Artículo'),
    ('document', 'Documento'),
    ('repost', 'Repost con comentario'),
]

# Diagnósticos posibles del router de embudo.
FUNNEL_DIAGNOSES = [
    ('positioning', 'Posicionamiento'),
    ('capture', 'Captación'),
    ('conversion', 'Conversión'),
    ('healthy', 'Sin cuello de botella claro'),
    ('no_data', 'Datos insuficientes'),
]

# Skill / agente recomendado por diagnóstico. Lo consume tanto el informe
# como el payload que se manda a n8n.
NEXT_ACTION_BY_DIAGNOSIS = {
    'positioning': 'post-viral-linkedin',
    'capture': 'minero-de-red',
    'conversion': 'ventas-ligero',
    'healthy': '',
    'no_data': '',
}


def normalize_signal_type(raw_value):
    """Devuelve un código de SIGNAL_TYPES a partir de lo que mande el origen.

    Acepta el código interno, los alias de `SIGNAL_TYPE_ALIASES` y variaciones
    de mayúsculas/acentos/espacios. Devuelve None si no reconoce el valor, para
    que el llamante decida (descartar la fila o guardarla como genérica).
    """
    if not raw_value:
        return None
    value = str(raw_value).strip().lower().replace(' ', '_').replace('-', '_')
    # Quitar acentos sin depender de unicodedata para las vocales habituales.
    for accented, plain in (('á', 'a'), ('é', 'e'), ('í', 'i'), ('ó', 'o'), ('ú', 'u')):
        value = value.replace(accented, plain)
    if value in SIGNAL_TYPE_LABELS:
        return value
    return SIGNAL_TYPE_ALIASES.get(value)


def normalize_linkedin_url(raw_url):
    """Normaliza una URL de perfil de LinkedIn para poder deduplicar por ella.

    LinkedIn devuelve la misma persona como `linkedin.com/in/juan-perez`,
    `www.linkedin.com/in/juan-perez/`, `https://es.linkedin.com/in/juan-perez?
    trk=xxx`... Sin normalizar, cada exportación crearía un lead nuevo.
    """
    if not raw_url:
        return ''
    url = str(raw_url).strip().lower()
    if not url:
        return ''
    for prefix in ('https://', 'http://'):
        if url.startswith(prefix):
            url = url[len(prefix):]
    url = url.split('?')[0].split('#')[0]
    if url.startswith('www.'):
        url = url[4:]
    # Subdominios de país: es.linkedin.com, uk.linkedin.com...
    if '.linkedin.com' in url:
        url = 'linkedin.com' + url.split('.linkedin.com', 1)[1]
    return url.rstrip('/')


# Separadores de «cargo en empresa» dentro de un titular de LinkedIn.
COMPANY_SPLIT_RE = re.compile(r'\s+(?:en|at|@)\s+', re.IGNORECASE)


def split_headline(headline):
    """Separa cargo y empresa de un titular. Devuelve (cargo, empresa).

    Solo se afirma una empresa cuando el titular la nombra explícitamente con
    «en», «at» o «@». La barra vertical de los titulares de LinkedIn separa
    reclamos publicitarios, no cargo y empresa: partir por ahí produciría
    empresas como «GTM Content Creator | Influencer Marketing for Tech
    Startups», que ensucian el CRM y estropean la deduplicación por
    nombre+empresa. Ante la duda, empresa vacía.
    """
    if not headline:
        return '', ''
    headline = headline.strip()

    parts = COMPANY_SPLIT_RE.split(headline, maxsplit=1)
    if len(parts) == 2:
        job, company = parts[0].strip(), parts[1].strip()
        # La empresa termina donde empieza el siguiente reclamo.
        for separator in ('|', '•', '–', ' - ', ','):
            if separator in company:
                company = company.split(separator, 1)[0].strip()
        return job[:90], company[:80]

    # Sin «en»/«at», la barra puede separar cargo y empresa («Fundadora |
    # Agencia Ferrer») o encadenar reclamos («Community | Content Creator |
    # Influencer Marketing | …»). Se distinguen por la forma: UNA sola barra
    # con un tramo derecho corto es una empresa; varias barras son un anuncio.
    if headline.count('|') == 1:
        left, right = (part.strip() for part in headline.split('|'))
        if left and right and len(right) <= 40 and len(right.split()) <= 4:
            return left[:90], right[:80]

    for separator in ('|', '•', '–', ' - '):
        if separator in headline:
            return headline.split(separator, 1)[0].strip()[:90], ''
    return headline[:90], ''
