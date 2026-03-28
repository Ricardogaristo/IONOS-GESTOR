# calendario_bp.py  ─ Blueprint del Calendario de Tareas
# Regístrate en app_web.py con:
#
#   from calendario_bp import calendario_bp
#   app.register_blueprint(calendario_bp)
#
# Y añade a inicializar_todo() (o en el propio blueprint) la creación de
# la tabla 'tareas_calendario' si usas tareas propias de este módulo.
# Si prefieres reutilizar la tabla 'tareas' existente, ya está listo.

from flask import Blueprint, render_template, request, session, jsonify, redirect, url_for
from datetime import datetime, date, timedelta
from auth import login_required          # mismo decorador que en app_web.py
from db_mysql import get_tareas_conn     # misma función de conexión

calendario_bp = Blueprint("calendario", __name__)

# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_conn():
    return get_tareas_conn()


def _tareas_rango(user_id: int, es_admin: bool, fecha_ini: str, fecha_fin: str):
    """Devuelve tareas entre fecha_ini y fecha_fin.
    Admin: solo usuarios con tipo='A'. Usuario normal: solo las suyas."""
    conn = _get_conn()
    try:
        if es_admin:
            rows = conn.execute(
                """
                SELECT t.id, t.descripcion, t.categoria, t.fecha, t.completada,
                       t.codigo, t.prioridad, t.notas, u.username
                FROM tareas t
                INNER JOIN usuarios u ON t.usuario_id = u.id
                WHERE COALESCE(u.tipo, 'A') = 'A'
                  AND t.fecha BETWEEN %s AND %s
                ORDER BY t.fecha, t.prioridad, t.id
                """,
                (fecha_ini, fecha_fin)
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT t.id, t.descripcion, t.categoria, t.fecha, t.completada,
                       t.codigo, t.prioridad, t.notas, u.username
                FROM tareas t
                LEFT JOIN usuarios u ON t.usuario_id = u.id
                WHERE t.usuario_id = %s AND t.fecha BETWEEN %s AND %s
                ORDER BY t.fecha, t.prioridad, t.id
                """,
                (user_id, fecha_ini, fecha_fin)
            ).fetchall()
    finally:
        conn.close()
    return rows


def _categorias(user_id: int, es_admin: bool):
    """Lista de categorías disponibles. Admin: usuarios tipo='A'. Normal: las suyas."""
    conn = _get_conn()
    try:
        if es_admin:
            rows = conn.execute(
                """SELECT DISTINCT t.categoria FROM tareas t
                   INNER JOIN usuarios u ON t.usuario_id = u.id
                   WHERE COALESCE(u.tipo,'A') = 'A' AND t.categoria IS NOT NULL
                   ORDER BY t.categoria"""
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT DISTINCT categoria FROM tareas WHERE usuario_id=%s AND categoria IS NOT NULL ORDER BY categoria",
                (user_id,)
            ).fetchall()
    finally:
        conn.close()
    return [r["categoria"] for r in rows if r["categoria"]]


# ── Ruta principal ─────────────────────────────────────────────────────────────

@calendario_bp.route("/calendario")
@login_required
def calendario():
    hoy      = date.today()
    anio     = int(request.args.get("anio", hoy.year))
    mes      = int(request.args.get("mes",  hoy.month))

    # Primer y último día del mes
    primer_dia = date(anio, mes, 1)
    if mes == 12:
        ultimo_dia = date(anio + 1, 1, 1) - timedelta(days=1)
    else:
        ultimo_dia = date(anio, mes + 1, 1) - timedelta(days=1)

    user_id  = session.get("user_id")
    es_admin = session.get("es_admin", 0) >= 2

    rows = _tareas_rango(user_id, es_admin,
                         primer_dia.strftime("%Y-%m-%d"),
                         ultimo_dia.strftime("%Y-%m-%d"))

    # Agrupar por fecha
    tareas_por_dia = {}
    for r in rows:
        key = r["fecha"]   # 'YYYY-MM-DD'
        if key not in tareas_por_dia:
            tareas_por_dia[key] = []
        tareas_por_dia[key].append({
            "id":          r["id"],
            "descripcion": r["descripcion"],
            "categoria":   r["categoria"] or "General",
            "completada":  r["completada"],
            "codigo":      r["codigo"] or "",
            "prioridad":   r["prioridad"] if r["prioridad"] else 2,
            "notas":       r["notas"] or "",
            "username":    r["username"] or "",
        })

    # Mes anterior / siguiente para navegación
    if mes == 1:
        mes_ant, anio_ant = 12, anio - 1
    else:
        mes_ant, anio_ant = mes - 1, anio
    if mes == 12:
        mes_sig, anio_sig = 1, anio + 1
    else:
        mes_sig, anio_sig = mes + 1, anio

    MESES_ES = ["Enero","Febrero","Marzo","Abril","Mayo","Junio",
                "Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]

    # Semanas del mes (lista de listas de 7 dates, None = fuera del mes)
    semanas = []
    dia_semana_inicio = primer_dia.weekday()  # 0=Lunes
    dias_en_mes = (ultimo_dia - primer_dia).days + 1
    celda = [None] * dia_semana_inicio
    for d in range(1, dias_en_mes + 1):
        celda.append(date(anio, mes, d))
        if len(celda) == 7:
            semanas.append(celda)
            celda = []
    if celda:
        celda += [None] * (7 - len(celda))
        semanas.append(celda)

    categorias = _categorias(user_id, es_admin)

    return render_template(
        "calendario.html",
        semanas       = semanas,
        tareas_por_dia= tareas_por_dia,
        hoy           = hoy.strftime("%Y-%m-%d"),
        anio          = anio,
        mes           = mes,
        mes_nombre    = MESES_ES[mes - 1],
        mes_ant       = mes_ant,
        anio_ant      = anio_ant,
        mes_sig       = mes_sig,
        anio_sig      = anio_sig,
        categorias    = categorias,
    )


# ── API: tareas de un mes (JSON, para recargar sin refrescar la página) ────────

@calendario_bp.route("/calendario/api/mes")
@login_required
def api_mes():
    hoy     = date.today()
    anio    = int(request.args.get("anio", hoy.year))
    mes     = int(request.args.get("mes",  hoy.month))

    primer_dia = date(anio, mes, 1)
    if mes == 12:
        ultimo_dia = date(anio + 1, 1, 1) - timedelta(days=1)
    else:
        ultimo_dia = date(anio, mes + 1, 1) - timedelta(days=1)

    user_id  = session.get("user_id")
    es_admin = session.get("es_admin", 0) >= 2
    rows = _tareas_rango(user_id, es_admin,
                         primer_dia.strftime("%Y-%m-%d"),
                         ultimo_dia.strftime("%Y-%m-%d"))

    result = {}
    for r in rows:
        key = r["fecha"]
        if key not in result:
            result[key] = []
        result[key].append({
            "id":          r["id"],
            "descripcion": r["descripcion"],
            "categoria":   r["categoria"] or "General",
            "completada":  r["completada"],
            "codigo":      r["codigo"] or "",
            "prioridad":   r["prioridad"] if r["prioridad"] else 2,
            "notas":       r["notas"] or "",
        })
    return jsonify(result)


# ── Agregar tarea desde el calendario ──────────────────────────────────────────

@calendario_bp.route("/calendario/agregar", methods=["POST"])
@login_required
def agregar_tarea():
    user_id     = session.get("user_id")
    descripcion = request.form.get("descripcion", "").strip()
    fecha       = request.form.get("fecha", "").strip()
    categoria   = request.form.get("categoria", "General").strip()
    prioridad   = int(request.form.get("prioridad", 2))
    notas       = request.form.get("notas", "").strip()

    if not descripcion or not fecha:
        return redirect(url_for("calendario.calendario"))

    conn = _get_conn()
    try:
        conn.execute(
            """
            INSERT INTO tareas (descripcion, categoria, fecha, completada, prioridad, notas, usuario_id)
            VALUES (%s, %s, %s, 0, %s, %s, %s)
            """,
            (descripcion, categoria or "General", fecha, prioridad, notas, user_id)
        )
        conn.commit()
    finally:
        conn.close()

    # Volver al mes de la tarea creada
    try:
        d = datetime.strptime(fecha, "%Y-%m-%d")
        return redirect(url_for("calendario.calendario", anio=d.year, mes=d.month))
    except ValueError:
        return redirect(url_for("calendario.calendario"))


# ── Cambiar estado completada/pendiente desde el calendario ───────────────────

@calendario_bp.route("/calendario/toggle/<int:tarea_id>", methods=["POST"])
@login_required
def toggle_tarea(tarea_id: int):
    user_id  = session.get("user_id")
    es_admin = session.get("es_admin", 0) >= 2

    conn = _get_conn()
    try:
        if es_admin:
            row = conn.execute(
                """SELECT t.id, t.completada FROM tareas t
                   INNER JOIN usuarios u ON t.usuario_id = u.id
                   WHERE t.id=%s AND COALESCE(u.tipo,'A')='A'""",
                (tarea_id,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT id, completada FROM tareas WHERE id=%s AND usuario_id=%s",
                (tarea_id, user_id)
            ).fetchone()

        if row:
            nuevo = 0 if row["completada"] == 1 else 1
            now   = datetime.now().strftime("%Y-%m-%d %H:%M:%S") if nuevo == 1 else None
            conn.execute(
                "UPDATE tareas SET completada=%s, fecha_completada=%s WHERE id=%s",
                (nuevo, now, tarea_id)
            )
            conn.commit()
            return jsonify({"ok": True, "completada": nuevo})
    finally:
        conn.close()

    return jsonify({"ok": False}), 403


# ── Eliminar tarea desde el calendario ────────────────────────────────────────

@calendario_bp.route("/calendario/eliminar/<int:tarea_id>", methods=["POST"])
@login_required
def eliminar_tarea_cal(tarea_id: int):
    user_id  = session.get("user_id")
    es_admin = session.get("es_admin", 0) >= 2

    conn = _get_conn()
    try:
        if es_admin:
            conn.execute(
                """DELETE t FROM tareas t
                   INNER JOIN usuarios u ON t.usuario_id = u.id
                   WHERE t.id=%s AND COALESCE(u.tipo,'A')='A'""",
                (tarea_id,)
            )
        else:
            conn.execute(
                "DELETE FROM tareas WHERE id=%s AND usuario_id=%s",
                (tarea_id, user_id)
            )
        conn.commit()
    finally:
        conn.close()

    return jsonify({"ok": True})