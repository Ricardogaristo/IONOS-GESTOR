"""
historial_alumno_bp.py  —  Historial completo de un alumno
Registra en la tabla formacion.db el historial por alumno_id y también
permite buscar por nombre para ver todos sus cursos (activos + archivados).

Rutas expuestas:
  GET  /formacion/historial/alumno/<int:alumno_id>   → historial de 1 alumno (por ID, desde tabla)
  GET  /formacion/historial/buscar                   → buscador por nombre (botón de formacion.html)

Registro en app.py:
  from historial_alumno_bp import historial_alumno_bp
  app.register_blueprint(historial_alumno_bp)
"""

from flask import Blueprint, render_template, request, redirect, session, url_for
from functools import wraps
from db_mysql import get_form_conn

# ── Blueprint ──────────────────────────────────────────────────────────────────
historial_alumno_bp = Blueprint(
    "historial_alumno", __name__,
    template_folder="templates"
)

# ── Decorador de login (igual que en formacion.py) ────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)
    return decorated

# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt_examenes(val):
    """Garantiza formato R/S/T."""
    if val is None:
        return "0/0/0"
    s = str(val).strip()
    if "/" in s and len(s.split("/")) == 3:
        return s
    try:
        return f"{int(float(s))}/0/0"
    except Exception:
        return "0/0/0"


def _parse_examenes(val):
    """Devuelve (realizados, superados, totales) como ints."""
    parts = _fmt_examenes(val).split("/")
    try:
        return int(parts[0]), int(parts[1]), int(parts[2])
    except Exception:
        return 0, 0, 0


def _enriquecer_fila(fila_dict, tutor_id, conn):
    """
    Dado un dict de alumno, agrega:
      - progreso_historial  (lista de snapshots ordenados por fecha)
      - eventos_recientes   (últimos 5 del expediente)
      - ex_realizados / ex_superados / ex_total
    """
    alumno_id = fila_dict["id"]

    ph = conn.execute("""
        SELECT fecha_import, progreso, examenes, delta_progreso, avanzo
        FROM progreso_historial
        WHERE alumno_id=%s AND tutor_id=%s
        ORDER BY fecha_import ASC
    """, (alumno_id, tutor_id)).fetchall()
    fila_dict["progreso_historial"] = [dict(r) for r in ph]

    ev = conn.execute("""
        SELECT tipo, texto, created_at
        FROM expediente_eventos
        WHERE alumno_id=%s AND tutor_id=%s
        ORDER BY created_at DESC LIMIT 5
    """, (alumno_id, tutor_id)).fetchall()
    fila_dict["eventos_recientes"] = [dict(r) for r in ev]

    ex_r, ex_s, ex_t = _parse_examenes(fila_dict.get("examenes"))
    fila_dict["ex_realizados"] = ex_r
    fila_dict["ex_superados"]  = ex_s
    fila_dict["ex_total"]      = ex_t

    return fila_dict


# ══════════════════════════════════════════════════════════════════════════════
# RUTA 1 — Historial directo por alumno_id
# Llamada desde el botón 📊 de cada fila de la tabla de alumnos
# ══════════════════════════════════════════════════════════════════════════════

@historial_alumno_bp.route("/formacion/historial/alumno/<int:alumno_id>")
@login_required
def historial_por_id(alumno_id):
    """
    Muestra el historial completo de UN alumno identificado por su ID.
    Carga todos sus registros (mismo nombre, todos los cursos, activos + archivados).
    """
    tutor_id = session["user_id"]
    conn     = get_form_conn()

    # Obtener datos del alumno solicitado
    alumno_base = conn.execute(
        "SELECT id, nombre FROM alumnos WHERE id=%s AND tutor_id=%s",
        (alumno_id, tutor_id)
    ).fetchone()

    if not alumno_base:
        conn.close()
        return redirect(url_for("formacion.formacion"))

    nombre_alumno = alumno_base["nombre"]

    # Todos los registros del alumno con ese nombre (todos los cursos)
    filas = conn.execute("""
        SELECT id, curso, progreso, examenes,
               fecha_inicio, fecha_fin, supera_75,
               archivado, ultima_importacion,
               delta_progreso, avanzo, gestionado,
               tipo_gestion, comentario
        FROM alumnos
        WHERE tutor_id=%s AND LOWER(TRIM(nombre)) = LOWER(TRIM(%s))
        ORDER BY fecha_inicio ASC
    """, (tutor_id, nombre_alumno)).fetchall()

    cursos_alumno = [
        _enriquecer_fila(dict(f), tutor_id, conn)
        for f in filas
    ]

    conn.close()

    return render_template(
        "historial_alumno.html",
        q=nombre_alumno,
        alumno_nombre=nombre_alumno,
        cursos_alumno=cursos_alumno,
        nombres_sugeridos=[],   # No necesario en vista directa
        origen="id",            # Para que la plantilla sepa el contexto
    )


# ══════════════════════════════════════════════════════════════════════════════
# RUTA 2 — Buscador por nombre
# Llamada desde el botón "📊 Historial alumno" del header de formacion.html
# ══════════════════════════════════════════════════════════════════════════════

@historial_alumno_bp.route("/formacion/historial/buscar")
@login_required
def historial_buscar():
    """
    Buscador de alumnos por nombre.
    Con ?q=Nombre muestra el historial completo; sin q, muestra el formulario vacío.
    """
    tutor_id = session["user_id"]
    q        = request.args.get("q", "").strip()

    conn = get_form_conn()

    # Lista de alumnos con datos para filtros en cliente
    # MIN(nombre) en lugar de ANY_VALUE(): compatible con MariaDB y MySQL < 5.7.5
    _rows = conn.execute("""
        SELECT MIN(nombre)                                                 AS nombre,
               GROUP_CONCAT(DISTINCT curso ORDER BY curso SEPARATOR '||') AS cursos,
               MAX(supera_75)                                              AS supera_alguno,
               COUNT(*)                                                    AS n_cursos,
               SUM(CASE WHEN archivado = 0 OR archivado IS NULL THEN 1 ELSE 0 END) AS n_activos,
               SUM(CASE WHEN archivado = 1 THEN 1 ELSE 0 END)             AS n_archivados
        FROM alumnos
        WHERE tutor_id=%s
        GROUP BY LOWER(TRIM(nombre))
        ORDER BY MIN(nombre)
    """, (tutor_id,)).fetchall()

    # nombres_sugeridos: lista de dicts con nombre, cursos, supera, activos, archivados
    nombres_sugeridos = []
    for r in _rows:
        cursos_lista = [c.strip() for c in (r["cursos"] or "").split("||") if c.strip()]
        supera = int(r["supera_alguno"] or 0)
        nombres_sugeridos.append({
            "nombre":      r["nombre"],
            "cursos":      cursos_lista,
            "supera":      supera,
            "n_cursos":    int(r["n_cursos"]    or 0),
            "n_activos":   int(r["n_activos"]   or 0),
            "n_archivados": int(r["n_archivados"] or 0),
        })

    # Lista plana de cursos únicos para el filtro de curso
    todos_cursos = sorted(set(
        c for a in nombres_sugeridos for c in a["cursos"]
    ))

    cursos_alumno = []
    alumno_nombre = ""

    if q:
        alumno_nombre = q
        filas = conn.execute("""
            SELECT id, curso, progreso, examenes,
                   fecha_inicio, fecha_fin, supera_75,
                   archivado, ultima_importacion,
                   delta_progreso, avanzo, gestionado,
                   tipo_gestion, comentario
            FROM alumnos
            WHERE tutor_id=%s AND LOWER(TRIM(nombre)) = LOWER(TRIM(%s))
            ORDER BY fecha_inicio ASC
        """, (tutor_id, q)).fetchall()

        cursos_alumno = [
            _enriquecer_fila(dict(f), tutor_id, conn)
            for f in filas
        ]

    conn.close()

    return render_template(
        "historial_alumno.html",
        q=q,
        alumno_nombre=alumno_nombre,
        cursos_alumno=cursos_alumno,
        nombres_sugeridos=nombres_sugeridos,
        todos_cursos=todos_cursos,
        origen="buscar",
    )