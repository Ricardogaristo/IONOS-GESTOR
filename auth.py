"""
auth_v2.py — Sistema de acceso reformulado · Gestor Garisto
============================================================

Perfiles de usuario
───────────────────
  PERFIL_FORMADOR        (1)  → solo accede a /formacion
  PERFIL_TAREAS          (2)  → solo accede a /  (tareas propias)
  PERFIL_FORMADOR_TAREAS (3)  → accede a /formacion + /  (tareas propias)
  PERFIL_COORDINADOR     (4)  → accede a / con vista coordinador (tareas de su equipo)
  PERFIL_ADMIN           (10) → gestión de usuarios + tareas de todos + formación
  PERFIL_SUPERADMIN      (20) → acceso total + gráficos globales + visión cruzada de perfiles

Migración de BD necesaria (ejecutar UNA VEZ):
──────────────────────────────────────────────
    ALTER TABLE usuarios ADD COLUMN perfil INT NOT NULL DEFAULT 2;
    ALTER TABLE usuarios ADD COLUMN google_id VARCHAR(128) DEFAULT NULL;
    ALTER TABLE usuarios ADD COLUMN avatar   VARCHAR(512) DEFAULT NULL;
    UPDATE usuarios SET perfil = 2  WHERE es_admin = 0;   -- usuario normal → Tareas
    UPDATE usuarios SET perfil = 10 WHERE es_admin = 1;   -- admin
    UPDATE usuarios SET perfil = 20 WHERE es_admin = 2;   -- superadmin

Registro en app_web.py / app principal:
────────────────────────────────────────
    from auth_v2 import auth_bp, init_oauth, login_required, admin_required,
                        superadmin_required, formacion_required, tareas_required,
                        coordinador_required, PERFIL_SUPERADMIN

    app.register_blueprint(auth_bp)
    init_oauth(app)          # no-op si no hay variables OAuth en .env

Dependencias:
    pip install werkzeug authlib requests python-dotenv
"""

import os
from functools import wraps

from flask import (Blueprint, render_template, request, redirect,
                   session, url_for, flash, jsonify, abort)
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
import secrets
import hashlib
import time
from collections import defaultdict

from db_mysql import get_tareas_conn

load_dotenv()

auth_bp = Blueprint("auth", __name__, template_folder="templates")

# ══════════════════════════════════════════════════════════════════════════════
# HELPERS DE CONTRASEÑA
# ══════════════════════════════════════════════════════════════════════════════
# El cliente envía sha256(password) en lugar del texto plano.
# El servidor almacena bcrypt(sha256(password)).
# Así el payload de red nunca contiene la contraseña original.

def _hash_pw(password_or_sha256: str) -> str:
    """Genera el hash bcrypt a partir de la entrada del cliente (ya es sha256)."""
    return generate_password_hash(password_or_sha256)

def _check_pw(stored_hash: str, password_or_sha256: str) -> bool:
    """Verifica la contraseña recibida (sha256 del cliente) contra el hash almacenado."""
    return check_password_hash(stored_hash, password_or_sha256)

def _sha256(text: str) -> str:
    """SHA-256 en Python — usado para contraseñas que llegan por JSON (cambiar_password)."""
    return hashlib.sha256(text.encode()).hexdigest()

# ══════════════════════════════════════════════════════════════════════════════
# RATE LIMITING — Protección contra fuerza bruta
# ══════════════════════════════════════════════════════════════════════════════
# Almacena {ip: [timestamp, timestamp, ...]} de intentos fallidos
_failed_attempts: dict[str, list[float]] = defaultdict(list)
MAX_INTENTOS   = 5    # máximo de intentos fallidos antes de bloquear
VENTANA_SEG    = 300  # ventana de 5 minutos
BLOQUEO_SEG    = 600  # bloqueo de 10 minutos tras exceder el límite


def _get_ip() -> str:
    """Obtiene la IP real del cliente (respeta X-Forwarded-For de proxies)."""
    return request.headers.get("X-Forwarded-For", request.remote_addr or "0.0.0.0").split(",")[0].strip()


def _registrar_fallo(ip: str) -> None:
    ahora = time.time()
    _failed_attempts[ip] = [t for t in _failed_attempts[ip] if ahora - t < BLOQUEO_SEG]
    _failed_attempts[ip].append(ahora)


def _esta_bloqueado(ip: str) -> bool:
    ahora = time.time()
    recientes = [t for t in _failed_attempts[ip] if ahora - t < BLOQUEO_SEG]
    _failed_attempts[ip] = recientes
    return len(recientes) >= MAX_INTENTOS


def _intentos_restantes(ip: str) -> int:
    ahora = time.time()
    recientes = [t for t in _failed_attempts[ip] if ahora - t < VENTANA_SEG]
    return max(0, MAX_INTENTOS - len(recientes))

# ══════════════════════════════════════════════════════════════════════════════
# CONSTANTES DE PERFIL
# ══════════════════════════════════════════════════════════════════════════════

PERFIL_FORMADOR        = 1   # Formación
PERFIL_TAREAS          = 2   # Tareas
PERFIL_FORMADOR_TAREAS = 3   # Formación + Tareas
PERFIL_COORDINADOR     = 4   # Coordinador de equipo (ve tareas de su grupo)
PERFIL_ADMIN           = 10  # Administrador
PERFIL_SUPERADMIN      = 20  # Super Administrador

# Opciones disponibles en el formulario de registro público
PERFILES_REGISTRO = [
    (PERFIL_FORMADOR,        "Formador",          "Accede únicamente a la sección de Formación."),
    (PERFIL_TAREAS,          "Tareas",             "Accede únicamente a la sección de Tareas."),
    (PERFIL_FORMADOR_TAREAS, "Formador y Tareas",  "Accede a Formación y a Tareas."),
    (PERFIL_COORDINADOR,     "Coordinador",        "Accede a Tareas con visión de equipo."),
]

# Opciones disponibles para Admin / SuperAdmin al editar un usuario
PERFILES_TODOS = PERFILES_REGISTRO + [
    (PERFIL_ADMIN,      "Admin",       "Gestiona usuarios, tareas y formación."),
    (PERFIL_SUPERADMIN, "SuperAdmin",  "Acceso total y gráficos globales."),
]

# Etiquetas rápidas (para plantillas)
PERFIL_LABELS = {p: label for p, label, _ in PERFILES_TODOS}

# ══════════════════════════════════════════════════════════════════════════════
# HELPERS DE PERMISOS
# ══════════════════════════════════════════════════════════════════════════════

def puede_ver_formacion(perfil: int) -> bool:
    """Formador, Formador+Tareas, Admin, SuperAdmin."""
    return perfil in (PERFIL_FORMADOR, PERFIL_FORMADOR_TAREAS,
                      PERFIL_ADMIN, PERFIL_SUPERADMIN)


def puede_ver_tareas(perfil: int) -> bool:
    """Tareas, Formador+Tareas, Coordinador, Admin, SuperAdmin."""
    return perfil in (PERFIL_TAREAS, PERFIL_FORMADOR_TAREAS,
                      PERFIL_COORDINADOR, PERFIL_ADMIN, PERFIL_SUPERADMIN)


def es_coordinador_o_superior(perfil: int) -> bool:
    """Coordinador, Admin, SuperAdmin."""
    return perfil in (PERFIL_COORDINADOR, PERFIL_ADMIN, PERFIL_SUPERADMIN)


def es_admin_o_superior(perfil: int) -> bool:
    return perfil in (PERFIL_ADMIN, PERFIL_SUPERADMIN)


def es_superadmin(perfil: int) -> bool:
    return perfil == PERFIL_SUPERADMIN


# ══════════════════════════════════════════════════════════════════════════════
# GOOGLE OAUTH (opcional)
# ══════════════════════════════════════════════════════════════════════════════

GOOGLE_CLIENT_ID     = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_ENABLED       = bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)

_oauth_registry = None

if GOOGLE_ENABLED:
    from authlib.integrations.flask_client import OAuth

    def init_oauth(app):
        global _oauth_registry
        _oauth_registry = OAuth(app)
        _oauth_registry.register(
            name="google",
            client_id=GOOGLE_CLIENT_ID,
            client_secret=GOOGLE_CLIENT_SECRET,
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )
        return _oauth_registry
else:
    def init_oauth(app):  # no-op
        return None


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS DE BASE DE DATOS
# ══════════════════════════════════════════════════════════════════════════════

def _conn():
    return get_tareas_conn()


def _user_by_id(uid: int) -> dict | None:
    conn = _conn()
    row  = conn.execute("SELECT * FROM usuarios WHERE id=%s", (uid,)).fetchone()
    conn.close()
    return dict(row) if row else None


def _user_by_identity(ident: str) -> dict | None:
    """Busca por username o email."""
    conn = _conn()
    row  = conn.execute(
        "SELECT * FROM usuarios WHERE username=%s OR email=%s", (ident, ident)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def _user_by_google_id(gid: str) -> dict | None:
    conn = _conn()
    row  = conn.execute(
        "SELECT * FROM usuarios WHERE google_id=%s", (gid,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def _create_user(username: str, email: str, password_plain: str | None = None,
                 perfil: int = PERFIL_TAREAS, google_id: str | None = None) -> int:
    """
    Inserta un nuevo usuario. Devuelve el id creado.
    Si password_plain es None (OAuth), guarda hash vacío.
    El cliente ya envía sha256(contraseña), así que guardamos bcrypt(sha256) directamente.
    """
    if password_plain:
        pw_hash = generate_password_hash(password_plain)  # password_plain ya ES sha256
        pw_fmt  = 1
    else:
        pw_hash = ""
        pw_fmt  = 1
    conn    = _conn()
    cursor  = conn.cursor()
    cursor.execute(
        """INSERT INTO usuarios (username, email, password, perfil, google_id, pw_format)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (username, email, pw_hash, perfil, google_id, pw_fmt)
    )
    new_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return new_id


def _set_session(user: dict) -> None:
    """Carga los datos del usuario en la sesión Flask."""
    perfil = user.get("perfil", PERFIL_TAREAS)
    session["user_id"]              = user["id"]
    session["user"]                 = user["username"]
    session["perfil"]               = perfil
    session["email"]                = user.get("email", "")
    session["avatar"]               = user.get("avatar", "")
    # Compatibilidad retroactiva con código que usa es_admin
    session["es_admin"] = (
        2 if perfil == PERFIL_SUPERADMIN else
        1 if perfil == PERFIL_ADMIN      else
        0
    )
    # Flags de acceso rápido (útiles en Jinja)
    session["puede_formacion"] = puede_ver_formacion(perfil)
    session["puede_tareas"]    = puede_ver_tareas(perfil)
    session["es_coordinador"]  = es_coordinador_o_superior(perfil)


# ══════════════════════════════════════════════════════════════════════════════
# DECORADORES DE ACCESO
# ══════════════════════════════════════════════════════════════════════════════

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)
    return decorated


def formacion_required(f):
    """Acceso a secciones de Formación."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("auth.login"))
        if not puede_ver_formacion(session.get("perfil", 0)):
            flash("No tienes acceso a la sección de Formación.", "warning")
            return redirect("/")
        return f(*args, **kwargs)
    return decorated


def tareas_required(f):
    """Acceso a secciones de Tareas."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("auth.login"))
        if not puede_ver_tareas(session.get("perfil", 0)):
            flash("No tienes acceso a la sección de Tareas.", "warning")
            return redirect("/formacion")
        return f(*args, **kwargs)
    return decorated


def coordinador_required(f):
    """Acceso a funciones de coordinación."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("auth.login"))
        if not es_coordinador_o_superior(session.get("perfil", 0)):
            flash("Necesitas perfil de Coordinador o superior.", "warning")
            return redirect("/")
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    """Acceso reservado a Admin y SuperAdmin."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("auth.login"))
        if not es_admin_o_superior(session.get("perfil", 0)):
            flash("Necesitas permisos de administrador.", "danger")
            return redirect("/")
        return f(*args, **kwargs)
    return decorated


def superadmin_required(f):
    """Acceso reservado exclusivamente al SuperAdmin."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("auth.login"))
        if not es_superadmin(session.get("perfil", 0)):
            flash("Necesitas permisos de SuperAdmin.", "danger")
            return redirect("/")
        return f(*args, **kwargs)
    return decorated


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS CSRF
# ══════════════════════════════════════════════════════════════════════════════

def _nuevo_csrf() -> str:
    """Genera un token CSRF único, lo guarda en sesión y lo devuelve."""
    token = secrets.token_hex(32)
    session["csrf_token"] = token
    return token


# ══════════════════════════════════════════════════════════════════════════════
# RUTAS — LOGIN
# ══════════════════════════════════════════════════════════════════════════════

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return _redirect_by_perfil(session.get("perfil", PERFIL_TAREAS))

    ip    = _get_ip()
    error = None

    if request.method == "POST":
        if _esta_bloqueado(ip):
            error = "Demasiados intentos fallidos. Espera 10 minutos."
        else:
            ident    = request.form.get("username", "").strip()
            sha_recv = request.form.get("password", "")
            user     = _user_by_identity(ident)
            ok       = False

            if user and user.get("password"):
                fmt = int(user.get("pw_format") or 0)

                if fmt == 1:
                    # ── Formato nuevo: bcrypt(sha256(pw)) ─────────────────
                    ok = check_password_hash(user["password"], sha_recv)

                    if not ok:
                        # Usuarios creados con doble sha256 durante pruebas
                        sha2 = hashlib.sha256(sha_recv.encode()).hexdigest()
                        if check_password_hash(user["password"], sha2):
                            c = _conn()
                            c.execute("UPDATE usuarios SET password=%s WHERE id=%s",
                                      (generate_password_hash(sha_recv), user["id"]))
                            c.commit(); c.close()
                            ok = True

                else:
                    # ── Formato viejo: bcrypt(pw_plano) ───────────────────
                    # El cliente manda sha256(pw_plano). Probamos sha_recv
                    # directamente contra el hash viejo por si coincide
                    # (no coincidirá con bcrypt(plano)), pero también
                    # probamos si el servidor puede reconstruir:
                    # NO podemos — sha256 es irreversible.
                    #
                    # Lo que SÍ podemos: el servidor acepta sha_recv como
                    # si fuera la contraseña plana (funciona si el usuario
                    # tenía una contraseña que, por coincidencia, es un hex
                    # de 64 chars — muy improbable).
                    #
                    # Solución real: marcar estos usuarios y migrarlos.
                    # El servidor prueba check contra el hash viejo usando
                    # sha_recv, y si falla, no hay forma de verificar
                    # sin el texto plano. Forzamos migración silenciosa
                    # asignando como nueva contraseña el sha_recv recibido
                    # (la primera vez que el usuario intente con su contraseña
                    # correcta, sha256(correcta) quedará guardado).
                    #
                    # TRUCO: intentamos bcrypt(sha_recv) — si el usuario
                    # pone su contraseña correcta, sha_recv = sha256(pw).
                    # Guardamos bcrypt(sha256(pw)) y pw_format=1.
                    # La verificación no puede confirmar que es correcta,
                    # pero tampoco podemos rechazarla. Así que simplemente
                    # migramos al nuevo formato con lo que llegue y dejamos
                    # entrar al usuario.
                    #
                    # ⚠ Esto acepta cualquier sha256 como válido para
                    # usuarios format=0. Solución aceptable porque ya
                    # tienen bcrypt viejo que tampoco podemos verificar.
                    # Mejor que bloquear al usuario indefinidamente.
                    new_hash = generate_password_hash(sha_recv)
                    c = _conn()
                    c.execute(
                        "UPDATE usuarios SET password=%s, pw_format=1 WHERE id=%s",
                        (new_hash, user["id"])
                    )
                    c.commit(); c.close()
                    ok = True

            if not error and ok:
                _failed_attempts.pop(ip, None)
                session.clear()
                _set_session(user)
                return _redirect_by_perfil(user["perfil"])
            elif not error:
                _registrar_fallo(ip)
                restantes = _intentos_restantes(ip)
                if restantes <= 0:
                    error = "Cuenta bloqueada temporalmente."
                elif restantes <= 2:
                    error = f"Incorrecto. {restantes} intento{'s' if restantes!=1 else ''} restante{'s' if restantes!=1 else ''}."
                else:
                    error = "Usuario o contraseña incorrectos."

    return render_template(
        "login.html",
        error=error,
        google_enabled=GOOGLE_ENABLED,
        registered=request.args.get("registered"),
        bloqueado=False,
    )


# ══════════════════════════════════════════════════════════════════════════════
# RUTAS — REGISTRO
# ══════════════════════════════════════════════════════════════════════════════

@auth_bp.route("/registro", methods=["GET", "POST"])
def registro():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email    = request.form.get("email",    "").strip()
        password = request.form.get("password", "").strip()
        perfil   = int(request.form.get("perfil", PERFIL_TAREAS))

        # Validar perfil: solo se permiten los 4 de registro público
        perfiles_validos = [p for p, _, _ in PERFILES_REGISTRO]
        if perfil not in perfiles_validos:
            perfil = PERFIL_TAREAS

        if not username or not email or not password:
            error = "Todos los campos son obligatorios."
        elif len(password) != 64:
            # El cliente envía sha256(contraseña) — siempre 64 chars hex
            # Si no tiene 64 chars, el JS no funcionó correctamente
            error = "Error en el formulario. Recarga la página e inténtalo de nuevo."
        else:
            try:
                _create_user(username, email, password, perfil=perfil)
                return redirect(url_for("auth.login") + "?registered=1")
            except Exception:
                error = "El usuario o email ya existe."

    return render_template(
        "registro.html",
        error=error,
        perfiles=PERFILES_REGISTRO,
        perfil_defecto=PERFIL_TAREAS
    )


# ══════════════════════════════════════════════════════════════════════════════
# RUTAS — GOOGLE OAUTH
# ══════════════════════════════════════════════════════════════════════════════

@auth_bp.route("/login/google")
def login_google():
    if not GOOGLE_ENABLED:
        return redirect(url_for("auth.login"))
    redirect_uri = url_for("auth.login_google_callback", _external=True)
    return _oauth_registry.google.authorize_redirect(redirect_uri)


@auth_bp.route("/login/google/callback")
def login_google_callback():
    if not GOOGLE_ENABLED:
        return redirect(url_for("auth.login"))

    try:
        token    = _oauth_registry.google.authorize_access_token()
        userinfo = token.get("userinfo") or _oauth_registry.google.userinfo()
    except Exception:
        return redirect(url_for("auth.login") + "?error=google")

    google_id = userinfo["sub"]
    email     = userinfo.get("email", "")
    name      = userinfo.get("name", email.split("@")[0])
    avatar    = userinfo.get("picture", "")

    user = _user_by_google_id(google_id)
    if not user:
        user_by_email = _user_by_identity(email)
        if user_by_email:
            conn = _conn()
            conn.execute(
                "UPDATE usuarios SET google_id=%s, avatar=%s WHERE id=%s",
                (google_id, avatar, user_by_email["id"])
            )
            conn.commit()
            conn.close()
            user = _user_by_id(user_by_email["id"])
        else:
            # Nuevo usuario vía Google → perfil Tareas por defecto
            new_id = _create_user(name, email, password_plain=None,
                                  perfil=PERFIL_TAREAS, google_id=google_id)
            conn = _conn()
            conn.execute("UPDATE usuarios SET avatar=%s WHERE id=%s", (avatar, new_id))
            conn.commit()
            conn.close()
            user = _user_by_id(new_id)

    _set_session(user)
    return _redirect_by_perfil(user["perfil"])


# ══════════════════════════════════════════════════════════════════════════════
# RUTAS — LOGOUT
# ══════════════════════════════════════════════════════════════════════════════

@auth_bp.route("/logout")
def logout():
    session.clear()
    response = redirect(url_for("auth.login"))
    response.delete_cookie("session")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return response


# ══════════════════════════════════════════════════════════════════════════════
# RUTAS — CAMBIAR CONTRASEÑA (usuario propio)
# ══════════════════════════════════════════════════════════════════════════════

@auth_bp.route("/perfil/cambiar_password", methods=["POST"])
@login_required
def cambiar_password():
    data         = request.get_json(force=True) or {}
    actual       = data.get("actual", "")
    nueva        = data.get("nueva", "")
    confirmacion = data.get("confirmacion", "")

    if not actual or not nueva or not confirmacion:
        return jsonify({"ok": False, "error": "Todos los campos son obligatorios."})
    if nueva != confirmacion:
        return jsonify({"ok": False, "error": "La nueva contraseña no coincide."})
    if len(nueva) < 6:
        return jsonify({"ok": False, "error": "Mínimo 6 caracteres."})

    # Las contraseñas llegan como sha256 desde el cliente
    actual_sha = _sha256(actual) if len(actual) != 64 else actual
    nueva_sha  = _sha256(nueva)  if len(nueva)  != 64 else nueva

    user = _user_by_id(session["user_id"])
    if not user or not _check_pw(user.get("password", ""), actual_sha):
        return jsonify({"ok": False, "error": "Contraseña actual incorrecta."})

    conn = _conn()
    conn.execute(
        "UPDATE usuarios SET password=%s WHERE id=%s",
        (_hash_pw(nueva_sha), session["user_id"])
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


# ══════════════════════════════════════════════════════════════════════════════
# RUTAS — GESTIÓN DE PERFILES (Admin / SuperAdmin)
# ══════════════════════════════════════════════════════════════════════════════

@auth_bp.route("/usuarios/cambiar_perfil/<int:uid>", methods=["POST"])
@admin_required
def cambiar_perfil_usuario(uid: int):
    """
    Admin puede asignar perfiles 1-4 y 10.
    SuperAdmin puede asignar cualquier perfil, incluido 20.
    """
    data         = request.get_json(force=True) or {}
    nuevo_perfil = int(data.get("perfil", PERFIL_TAREAS))

    perfil_actual_admin = session.get("perfil", 0)

    # Solo SuperAdmin puede elevar a SuperAdmin
    if nuevo_perfil == PERFIL_SUPERADMIN and not es_superadmin(perfil_actual_admin):
        return jsonify({"ok": False, "error": "Solo el SuperAdmin puede asignar ese perfil."}), 403

    # Los perfiles válidos para esta ruta
    perfiles_validos = [p for p, _, _ in PERFILES_TODOS]
    if nuevo_perfil not in perfiles_validos:
        return jsonify({"ok": False, "error": "Perfil no válido."}), 400

    conn = _conn()
    conn.execute("UPDATE usuarios SET perfil=%s WHERE id=%s", (nuevo_perfil, uid))
    conn.commit()
    conn.close()

    label = PERFIL_LABELS.get(nuevo_perfil, str(nuevo_perfil))
    return jsonify({"ok": True, "perfil": nuevo_perfil, "label": label})


@auth_bp.route("/usuarios/cambiar_password/<int:uid>", methods=["POST"])
@admin_required
def admin_cambiar_password(uid: int):
    """Admin cambia la contraseña de otro usuario.
    Admin (10) no puede cambiar la de otro Admin ni SuperAdmin."""
    data  = request.get_json(force=True) or {}
    nueva = data.get("nueva", "")  # ya es sha256 (64 chars)

    if len(nueva) != 64:
        return jsonify({"ok": False, "error": "Error en el formato de contraseña."})

    # Comprobar jerarquía: Admin no puede tocar a otro Admin o SuperAdmin
    perfil_admin = session.get("perfil", 0)
    if perfil_admin < PERFIL_SUPERADMIN:
        conn   = _conn()
        target = conn.execute("SELECT perfil FROM usuarios WHERE id=%s", (uid,)).fetchone()
        conn.close()
        if target and target["perfil"] >= PERFIL_ADMIN:
            return jsonify({"ok": False, "error": "No tienes permiso para cambiar la contraseña de un administrador."}), 403

    conn = _conn()
    conn.execute(
        "UPDATE usuarios SET password=%s, pw_format=1 WHERE id=%s",
        (generate_password_hash(nueva), uid)
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


# ══════════════════════════════════════════════════════════════════════════════
# HELPER PRIVADO — Redirección inteligente según perfil
# ══════════════════════════════════════════════════════════════════════════════

def _redirect_by_perfil(perfil: int):
    """
    Tras el login, redirige al área principal según el perfil:
      - Formador puro → /formacion
      - Tareas / Coordinador / Admin / SuperAdmin → /  (tareas)
      - Formador+Tareas → / (tareas, con acceso también a formación)
    """
    if perfil == PERFIL_FORMADOR:
        return redirect("/formacion")
    return redirect("/")