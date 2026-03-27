# utilidades_bp.py  ─ Buscador · Vista usuarios · Informe · Archivar
#
# Registrar en app_web.py:
#   from utilidades_bp import utilidades_bp
#   app.register_blueprint(utilidades_bp)

from flask import Blueprint, render_template, request, session, jsonify, redirect
from datetime import datetime, date, timedelta
from collections import defaultdict

from auth import login_required
from db_mysql import get_tareas_conn

utilidades_bp = Blueprint("utilidades", __name__)


def _conn():
    return get_tareas_conn()


# ── BUSCADOR (API JSON) ────────────────────────────────────────────────────────

@utilidades_bp.route("/buscar")
@login_required
def buscar():
    q        = request.args.get("q", "").strip().lower()
    user_id  = session.get("user_id")
    es_admin = session.get("es_admin", 0) >= 2

    if len(q) < 2:
        return jsonify([])

    like = f"%{q}%"
    db   = _conn()
    try:
        if es_admin:
            rows = db.execute(
                """SELECT t.id, t.descripcion, t.categoria, t.fecha,
                          t.completada, t.codigo, t.prioridad, u.username
                   FROM tareas t LEFT JOIN usuarios u ON t.usuario_id=u.id
                   WHERE LOWER(t.descripcion) LIKE %s
                      OR LOWER(COALESCE(t.codigo,'')) LIKE %s
                      OR LOWER(COALESCE(t.categoria,'')) LIKE %s
                   ORDER BY t.completada ASC, t.fecha DESC
                   LIMIT 25""",
                (like, like, like),
            ).fetchall()
        else:
            rows = db.execute(
                """SELECT t.id, t.descripcion, t.categoria, t.fecha,
                          t.completada, t.codigo, t.prioridad, u.username
                   FROM tareas t LEFT JOIN usuarios u ON t.usuario_id=u.id
                   WHERE t.usuario_id=%s
                     AND (LOWER(t.descripcion) LIKE %s
                          OR LOWER(COALESCE(t.codigo,'')) LIKE %s
                          OR LOWER(COALESCE(t.categoria,'')) LIKE %s)
                   ORDER BY t.completada ASC, t.fecha DESC
                   LIMIT 25""",
                (user_id, like, like, like),
            ).fetchall()
    finally:
        db.close()

    return jsonify([
        {
            "id":          r["id"],
            "descripcion": r["descripcion"],
            "categoria":   r["categoria"] or "General",
            "fecha":       r["fecha"] or "",
            "completada":  r["completada"],
            "codigo":      r["codigo"] or "",
            "prioridad":   r["prioridad"] or 2,
            "username":    r["username"] or "",
        }
        for r in rows
    ])


# ── VISTA POR USUARIO (solo admin) ────────────────────────────────────────────

@utilidades_bp.route("/usuarios/vista")
@login_required
def vista_usuarios():
    if session.get("es_admin", 0) < 1:
        return redirect("/")

    hoy = date.today().strftime("%Y-%m-%d")
    db  = _conn()
    try:
        rows = db.execute(
            """SELECT u.id, u.username, u.email,
                      COUNT(t.id)                                          AS total,
                      SUM(t.completada = 1)                                AS completadas,
                      SUM(t.completada = 0)                                AS pendientes,
                      SUM(t.completada = 0 AND t.fecha IS NOT NULL
                          AND t.fecha < %s)                                AS vencidas,
                      MAX(t.fecha_completada)                              AS ultima_act,
                      SUM(t.prioridad = 1 AND t.completada = 0)           AS alta_pend
               FROM usuarios u
               LEFT JOIN tareas t ON t.usuario_id = u.id
               GROUP BY u.id, u.username, u.email
               ORDER BY vencidas DESC, pendientes DESC, total DESC""",
            (hoy,),
        ).fetchall()
    finally:
        db.close()

    usuarios = []
    for r in rows:
        total = r["total"] or 0
        comp  = r["completadas"] or 0
        usuarios.append(
            {
                "id":        r["id"],
                "username":  r["username"],
                "email":     r["email"] or "—",
                "total":     total,
                "comp":      comp,
                "pend":      r["pendientes"] or 0,
                "vencidas":  r["vencidas"] or 0,
                "alta_pend": r["alta_pend"] or 0,
                "pct":       round(comp / total * 100) if total else 0,
                "ultima":    r["ultima_act"],
            }
        )

    return render_template("vista_usuarios.html", usuarios=usuarios, hoy=hoy)


# ── INFORME DE PRODUCTIVIDAD ───────────────────────────────────────────────────

@utilidades_bp.route("/informe")
@login_required
def informe():
    user_id  = session.get("user_id")
    es_admin = session.get("es_admin", 0) >= 2
    hoy      = date.today()

    # Mes actual
    p_mes = date(hoy.year, hoy.month, 1)
    u_mes = (date(hoy.year, hoy.month % 12 + 1, 1) - timedelta(days=1)
             if hoy.month < 12 else date(hoy.year + 1, 1, 1) - timedelta(days=1))

    # Mes anterior
    p_ant = (date(hoy.year - 1, 12, 1) if hoy.month == 1
             else date(hoy.year, hoy.month - 1, 1))
    u_ant = p_mes - timedelta(days=1)

    hoy_s   = hoy.strftime("%Y-%m-%d")
    p_mes_s = p_mes.strftime("%Y-%m-%d")
    u_mes_s = u_mes.strftime("%Y-%m-%d")
    p_ant_s = p_ant.strftime("%Y-%m-%d")
    u_ant_s = u_ant.strftime("%Y-%m-%d")

    db = _conn()
    try:
        def _q(sql, *args):
            return db.execute(sql, args).fetchall()

        admin_uid = () if es_admin else (user_id,)
        uid_filt  = "" if es_admin else "AND t.usuario_id=%s"

        # Tareas del mes con fecha asignada
        tareas_mes = _q(
            f"""SELECT t.id, t.descripcion, t.categoria, t.fecha, t.completada,
                       t.prioridad, t.fecha_completada, t.fecha_creacion, u.username
                FROM tareas t LEFT JOIN usuarios u ON t.usuario_id=u.id
                WHERE t.fecha BETWEEN %s AND %s {uid_filt}
                ORDER BY t.fecha""",
            p_mes_s, u_mes_s, *admin_uid,
        )

        # Resumen mes anterior
        ant_row = _q(
            f"""SELECT COUNT(*) AS total, SUM(completada=1) AS comp
                FROM tareas t
                WHERE t.fecha BETWEEN %s AND %s {uid_filt}""",
            p_ant_s, u_ant_s, *admin_uid,
        )[0]

        # Días con al menos 1 tarea completada (para racha)
        dias_ok = {
            r["dia"] for r in _q(
                f"""SELECT DATE(fecha_completada) AS dia
                    FROM tareas t
                    WHERE completada=1 AND fecha_completada IS NOT NULL {uid_filt}
                    GROUP BY DATE(fecha_completada)""",
                *admin_uid,
            )
        }

        # Por categoría este mes
        cats_rows = _q(
            f"""SELECT COALESCE(categoria,'General') AS cat,
                       COUNT(*) AS total, SUM(completada=1) AS comp
                FROM tareas t
                WHERE t.fecha BETWEEN %s AND %s {uid_filt}
                GROUP BY cat ORDER BY total DESC""",
            p_mes_s, u_mes_s, *admin_uid,
        )

        # Próximas tareas (7 días)
        proximas = _q(
            f"""SELECT t.id, t.descripcion, t.categoria, t.fecha,
                       t.prioridad, t.codigo, u.username
                FROM tareas t LEFT JOIN usuarios u ON t.usuario_id=u.id
                WHERE t.completada=0 AND t.fecha BETWEEN %s AND %s {uid_filt}
                ORDER BY t.fecha, t.prioridad
                LIMIT 12""",
            hoy_s, (hoy + timedelta(days=7)).strftime("%Y-%m-%d"), *admin_uid,
        )

        # Vencidas pendientes
        vencidas_rows = _q(
            f"""SELECT t.id, t.descripcion, t.categoria, t.fecha,
                       t.prioridad, t.codigo, u.username
                FROM tareas t LEFT JOIN usuarios u ON t.usuario_id=u.id
                WHERE t.completada=0 AND t.fecha < %s {uid_filt}
                ORDER BY t.fecha, t.prioridad
                LIMIT 10""",
            hoy_s, *admin_uid,
        )
    finally:
        db.close()

    # Métricas principales
    total_mes = len(tareas_mes)
    comp_mes  = sum(1 for t in tareas_mes if t["completada"] == 1)
    pend_mes  = total_mes - comp_mes
    venc_mes  = sum(1 for t in tareas_mes if t["completada"] != 1
                    and t["fecha"] and t["fecha"] < hoy_s)
    pct_mes   = round(comp_mes / total_mes * 100) if total_mes else 0

    total_ant = ant_row["total"] or 0
    comp_ant  = ant_row["comp"]  or 0
    pct_ant   = round(comp_ant / total_ant * 100) if total_ant else 0

    # Racha de días consecutivos completando algo
    racha = 0
    cur   = hoy
    while cur in dias_ok:
        racha += 1
        cur -= timedelta(days=1)

    # Tareas por día del mes (para sparkline)
    por_dia = {}
    for d in range((u_mes - p_mes).days + 1):
        dia_s = (p_mes + timedelta(days=d)).strftime("%Y-%m-%d")
        por_dia[dia_s] = {"total": 0, "comp": 0}
    for t in tareas_mes:
        if t["fecha"] in por_dia:
            por_dia[t["fecha"]]["total"] += 1
            if t["completada"] == 1:
                por_dia[t["fecha"]]["comp"] += 1

    MESES = ["Enero","Febrero","Marzo","Abril","Mayo","Junio",
             "Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]

    return render_template(
        "informe.html",
        hoy        = hoy_s,
        mes_nombre = MESES[hoy.month - 1],
        mes_ant_n  = MESES[hoy.month - 2] if hoy.month > 1 else MESES[11],
        anio       = hoy.year,
        # KPIs
        total_mes  = total_mes,
        comp_mes   = comp_mes,
        pend_mes   = pend_mes,
        venc_mes   = venc_mes,
        pct_mes    = pct_mes,
        # Comparativa
        total_ant  = total_ant,
        comp_ant   = comp_ant,
        pct_ant    = pct_ant,
        # Racha
        racha      = racha,
        # Charts
        por_dia    = por_dia,
        cats       = [{"cat": r["cat"], "total": r["total"], "comp": r["comp"] or 0}
                      for r in cats_rows],
        # Tablas
        proximas   = [dict(r) for r in proximas],
        vencidas   = [dict(r) for r in vencidas_rows],
    )


# ── ARCHIVAR COMPLETADAS ───────────────────────────────────────────────────────

@utilidades_bp.route("/archivar-completadas", methods=["POST"])
@login_required
def archivar_completadas():
    user_id  = session.get("user_id")
    es_admin = session.get("es_admin", 0) >= 2

    db     = _conn()
    cursor = db.cursor()
    try:
        # Crear tabla archivo si no existe
        cursor.execute(
            """CREATE TABLE IF NOT EXISTS tareas_archivo (
                   id               INT PRIMARY KEY,
                   descripcion      TEXT,
                   categoria        VARCHAR(100),
                   fecha            VARCHAR(20),
                   codigo           VARCHAR(100),
                   usuario_id       INT,
                   prioridad        INT DEFAULT 2,
                   notas            TEXT,
                   usuario          VARCHAR(100),
                   fecha_creacion   DATETIME,
                   fecha_completada DATETIME,
                   fecha_archivado  DATETIME DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        db.commit()

        # Seleccionar tareas completadas
        if es_admin:
            tareas = db.execute("SELECT * FROM tareas WHERE completada=1").fetchall()
        else:
            tareas = db.execute(
                "SELECT * FROM tareas WHERE completada=1 AND usuario_id=%s", (user_id,)
            ).fetchall()

        archivadas, ids = 0, []
        for t in tareas:
            try:
                db.execute(
                    """INSERT IGNORE INTO tareas_archivo
                       (id,descripcion,categoria,fecha,codigo,usuario_id,
                        prioridad,notas,usuario,fecha_creacion,fecha_completada)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        t["id"], t["descripcion"], t["categoria"], t["fecha"],
                        t.get("codigo"), t.get("usuario_id"),
                        t.get("prioridad", 2), t.get("notas"), t.get("usuario"),
                        t.get("fecha_creacion"), t.get("fecha_completada"),
                    ),
                )
                ids.append(t["id"])
                archivadas += 1
            except Exception:
                pass

        db.commit()

        # Borrar de la tabla principal
        for tid in ids:
            db.execute("DELETE FROM tareas WHERE id=%s", (tid,))
        db.commit()

    finally:
        db.close()

    return jsonify({"ok": True, "archivadas": archivadas})


# ── LISTAR TAREAS ARCHIVADAS (API JSON) ───────────────────────────────────────

@utilidades_bp.route("/tareas-archivadas")
@login_required
def listar_archivadas():
    user_id  = session.get("user_id")
    es_admin = session.get("es_admin", 0) >= 2
    db = _conn()
    try:
        db.execute(
            """CREATE TABLE IF NOT EXISTS tareas_archivo (
                   id               INT PRIMARY KEY,
                   descripcion      TEXT,
                   categoria        VARCHAR(100),
                   fecha            VARCHAR(20),
                   codigo           VARCHAR(100),
                   usuario_id       INT,
                   prioridad        INT DEFAULT 2,
                   notas            TEXT,
                   usuario          VARCHAR(100),
                   fecha_creacion   DATETIME,
                   fecha_completada DATETIME,
                   fecha_archivado  DATETIME DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        try:
            db.execute("ALTER TABLE tareas_archivo ADD COLUMN fecha_archivado DATETIME DEFAULT CURRENT_TIMESTAMP")
            db.commit()
        except Exception:
            pass
        if es_admin:
            rows = db.execute(
                """SELECT id, descripcion, categoria, fecha, codigo,
                          usuario_id, prioridad, notas, usuario,
                          fecha_creacion, fecha_completada, fecha_archivado
                   FROM tareas_archivo ORDER BY fecha_archivado DESC LIMIT 200"""
            ).fetchall()
        else:
            rows = db.execute(
                """SELECT id, descripcion, categoria, fecha, codigo,
                          usuario_id, prioridad, notas, usuario,
                          fecha_creacion, fecha_completada, fecha_archivado
                   FROM tareas_archivo WHERE usuario_id=%s
                   ORDER BY fecha_archivado DESC LIMIT 200""",
                (user_id,),
            ).fetchall()
    except Exception as exc:
        db.close()
        return jsonify({"error": str(exc)}), 500
    finally:
        db.close()
    return jsonify([
        {
            "id":               r["id"],
            "descripcion":      r["descripcion"] or "",
            "categoria":        r["categoria"] or "General",
            "fecha":            str(r["fecha"]) if r["fecha"] else "",
            "codigo":           r["codigo"] or "",
            "prioridad":        r["prioridad"] or 2,
            "notas":            r["notas"] or "",
            "usuario":          r["usuario"] or "",
            "fecha_completada": str(r["fecha_completada"]) if r["fecha_completada"] else "",
            "fecha_archivado":  str(r["fecha_archivado"])  if r["fecha_archivado"]  else "",
        }
        for r in rows
    ])


# ── RESTAURAR TAREAS ARCHIVADAS ────────────────────────────────────────────────

@utilidades_bp.route("/restaurar-archivadas", methods=["POST"])
@login_required
def restaurar_archivadas():
    user_id  = session.get("user_id")
    es_admin = session.get("es_admin", 0) >= 2
    data      = request.get_json(silent=True) or {}
    ids_param = data.get("ids")
    if not ids_param:
        return jsonify({"ok": False, "error": "Sin IDs indicados"}), 400
    db     = _conn()
    cursor = db.cursor()
    try:
        if ids_param == "all":
            tareas = db.execute("SELECT * FROM tareas_archivo" + ("" if es_admin else " WHERE usuario_id=%s"),
                                () if es_admin else (user_id,)).fetchall()
        else:
            if not isinstance(ids_param, list):
                return jsonify({"ok": False, "error": "ids debe ser lista o 'all'"}), 400
            ph = ",".join(["%s"] * len(ids_param))
            if es_admin:
                tareas = db.execute(f"SELECT * FROM tareas_archivo WHERE id IN ({ph})", tuple(ids_param)).fetchall()
            else:
                tareas = db.execute(f"SELECT * FROM tareas_archivo WHERE id IN ({ph}) AND usuario_id=%s",
                                    (*ids_param, user_id)).fetchall()
        restauradas, ids_ok = 0, []
        for t in tareas:
            try:
                cursor.execute(
                    """INSERT IGNORE INTO tareas
                       (id,descripcion,categoria,fecha,completada,codigo,
                        usuario_id,prioridad,notas,usuario,fecha_creacion,fecha_completada)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (t["id"],t["descripcion"],t["categoria"],t["fecha"],1,
                     t.get("codigo"),t.get("usuario_id"),t.get("prioridad",2),
                     t.get("notas"),t.get("usuario"),t.get("fecha_creacion"),t.get("fecha_completada")),
                )
                ids_ok.append(t["id"])
                restauradas += 1
            except Exception:
                pass
        db.commit()
        for tid in ids_ok:
            cursor.execute("DELETE FROM tareas_archivo WHERE id=%s", (tid,))
        db.commit()
    except Exception as exc:
        db.close()
        return jsonify({"ok": False, "error": str(exc)}), 500
    finally:
        db.close()
    return jsonify({"ok": True, "restauradas": restauradas})