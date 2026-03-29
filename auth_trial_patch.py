"""
PARCHE TRIAL — auth.py
======================
Copia estos bloques en los lugares indicados dentro de tu auth.py actual.
Cada sección tiene un comentario que indica DÓNDE va.

MIGRACIÓN SQL (ejecutar UNA VEZ en tu base de datos):
──────────────────────────────────────────────────────
    ALTER TABLE usuarios
        ADD COLUMN fecha_registro DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        ADD COLUMN suscrito       TINYINT(1) NOT NULL DEFAULT 0;

    -- Usuarios ya existentes: fecha de hoy como punto de partida
    UPDATE usuarios SET fecha_registro = NOW() WHERE fecha_registro IS NULL;
"""

import datetime  # añadir al bloque de imports si no está ya

# ══════════════════════════════════════════════════════════════════════════════
# [1]  CONSTANTE TRIAL — justo después de las constantes de perfil (línea ~130)
# ══════════════════════════════════════════════════════════════════════════════

TRIAL_DIAS = 15  # días de acceso completo gratuito tras el registro


# ══════════════════════════════════════════════════════════════════════════════
# [2]  HELPERS DE TRIAL — añadir junto a los helpers de permisos (~línea 160)
# ══════════════════════════════════════════════════════════════════════════════

def dias_trial_restantes(user: dict) -> int:
    """Devuelve días enteros que le quedan de trial (mínimo 0)."""
    fecha = user.get("fecha_registro")
    if not fecha:
        return TRIAL_DIAS          # usuario antiguo sin fecha → gracia completa
    if isinstance(fecha, str):
        fecha = datetime.datetime.fromisoformat(fecha)
    # Eliminar zona horaria si la tiene, para comparar con datetime.now() (naive)
    if hasattr(fecha, "tzinfo") and fecha.tzinfo is not None:
        fecha = fecha.replace(tzinfo=None)
    transcurridos = (datetime.datetime.now() - fecha).days
    return max(0, TRIAL_DIAS - transcurridos)


def trial_activo(user: dict) -> bool:
    """True mientras el trial no haya expirado."""
    return dias_trial_restantes(user) > 0


def acceso_completo(user: dict) -> bool:
    """True si el usuario tiene acceso completo (trial vigente O suscrito)."""
    return trial_activo(user) or bool(user.get("suscrito", 0))


# ══════════════════════════════════════════════════════════════════════════════
# [3]  _create_user — sustituye el INSERT actual por este (incluye fecha_registro)
#      Busca la función _create_user en tu auth.py y reemplaza el cursor.execute
# ══════════════════════════════════════════════════════════════════════════════

def _create_user_NUEVO(username, email, password_plain=None,
                       perfil=2, google_id=None) -> int:
    """
    REEMPLAZA _create_user en auth.py.
    El único cambio respecto al original es añadir fecha_registro=NOW().
    """
    from werkzeug.security import generate_password_hash
    if password_plain:
        pw_hash = generate_password_hash(password_plain)
        pw_fmt  = 1
    else:
        pw_hash = ""
        pw_fmt  = 1
    conn   = get_tareas_conn()   # usa tu función _conn() existente
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO usuarios
               (username, email, password, perfil, google_id, pw_format, fecha_registro)
           VALUES (%s, %s, %s, %s, %s, %s, NOW())""",
        (username, email, pw_hash, perfil, google_id, pw_fmt)
    )
    new_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return new_id


# ══════════════════════════════════════════════════════════════════════════════
# [4]  _set_session — añade este bloque AL FINAL de la función _set_session
#      (después de session["es_coordinador"] = ...)
# ══════════════════════════════════════════════════════════════════════════════

def _set_session_BLOQUE_TRIAL(user, perfil, session):
    """
    AÑADE estas líneas al final de _set_session(), dentro de la función,
    sustituyendo 'user', 'perfil' y 'session' por las variables reales.
    """
    # ── Trial ──────────────────────────────────────────────────────────────────
    # Admin y SuperAdmin nunca están limitados por el trial
    if perfil >= 10:   # PERFIL_ADMIN o PERFIL_SUPERADMIN
        session["trial_dias"]       = 999
        session["trial_activo"]     = True
        session["acceso_completo"]  = True
        session["suscrito"]         = True
    else:
        dias = dias_trial_restantes(user)
        session["trial_dias"]       = dias
        session["trial_activo"]     = dias > 0
        session["suscrito"]         = bool(user.get("suscrito", 0))
        session["acceso_completo"]  = dias > 0 or bool(user.get("suscrito", 0))


# ══════════════════════════════════════════════════════════════════════════════
# [5]  DECORADOR trial_required — añadir junto a los demás decoradores
# ══════════════════════════════════════════════════════════════════════════════

def trial_required(f):
    """
    Permite el acceso solo si el usuario tiene trial activo O está suscrito.
    Los admins/superadmins siempre pasan.
    Úsalo en rutas que quieras restringir tras expirar el trial:

        @app.route("/dashboard")
        @login_required
        @trial_required
        def dashboard(): ...
    """
    from functools import wraps
    from flask import session, redirect, url_for, flash
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("auth.login"))
        if not session.get("acceso_completo", False):
            flash("Tu período de prueba ha finalizado. Activa tu cuenta por 15 € con Ricardo Garisto.", "warning")
            return redirect("/suscripcion")
        return f(*args, **kwargs)
    return decorated


# ══════════════════════════════════════════════════════════════════════════════
# [6]  RUTA /registro — cambia la línea del redirect tras registro exitoso:
#
#      ANTES:  return redirect(url_for("auth.login") + "?registered=1")
#      DESPUÉS:
# ══════════════════════════════════════════════════════════════════════════════

# return redirect(url_for("auth.login") + "?registered=1&trial=1")


# ══════════════════════════════════════════════════════════════════════════════
# [7]  inicializar_todo() en app_web.py — añade estas columnas al bucle existente
#      Busca el bucle "for col, ddl in [..." y añade las dos líneas siguientes:
# ══════════════════════════════════════════════════════════════════════════════

# ("fecha_registro", "DATETIME DEFAULT CURRENT_TIMESTAMP"),
# ("suscrito",       "TINYINT(1) DEFAULT 0"),


# ══════════════════════════════════════════════════════════════════════════════
# [8]  RUTA /suscripcion en app_web.py — añadir esta nueva ruta
# ══════════════════════════════════════════════════════════════════════════════

# @app.route("/suscripcion")
# @login_required
# def suscripcion():
#     return render_template("suscripcion.html")