-- ══════════════════════════════════════════════════════════════════════════════
-- migracion_perfiles.sql
-- Ejecutar UNA SOLA VEZ sobre la base de datos existente
-- ══════════════════════════════════════════════════════════════════════════════

-- 1. Añadir columna perfil (si no existe)
ALTER TABLE usuarios
  ADD COLUMN IF NOT EXISTS perfil    INT          NOT NULL DEFAULT 2,
  ADD COLUMN IF NOT EXISTS google_id VARCHAR(128)          DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS avatar    VARCHAR(512)          DEFAULT NULL;

-- 2. Migrar valores de es_admin → perfil
--    perfil 2  = Tareas       (equivale a es_admin = 0)
--    perfil 10 = Admin        (equivale a es_admin = 1)
--    perfil 20 = SuperAdmin   (equivale a es_admin = 2)
UPDATE usuarios SET perfil = 2  WHERE es_admin = 0 AND (perfil IS NULL OR perfil = 0);
UPDATE usuarios SET perfil = 10 WHERE es_admin = 1;
UPDATE usuarios SET perfil = 20 WHERE es_admin = 2;

-- 3. Verificación rápida
SELECT id, username, es_admin, perfil FROM usuarios ORDER BY perfil DESC;


-- ══════════════════════════════════════════════════════════════════════════════
-- FRAGMENTO PYTHON para app_web.py
-- Sustituye la sección de inicialización de la tabla usuarios
-- y los decoradores existentes
-- ══════════════════════════════════════════════════════════════════════════════

/*
─────────────────────────────────────────────────────
PASO 1 · En app_web.py (arriba del todo, imports)
─────────────────────────────────────────────────────

# Elimina o comenta los bloques de login_required / admin_required /
# superadmin_required que estén definidos en app_web.py y usa los de auth_v2:

from auth_v2 import (
    auth_bp,
    init_oauth,
    login_required,
    admin_required,
    superadmin_required,
    formacion_required,
    tareas_required,
    coordinador_required,
    puede_ver_formacion,
    puede_ver_tareas,
    es_admin_o_superior,
    es_superadmin,
    PERFIL_FORMADOR,
    PERFIL_TAREAS,
    PERFIL_FORMADOR_TAREAS,
    PERFIL_COORDINADOR,
    PERFIL_ADMIN,
    PERFIL_SUPERADMIN,
    PERFIL_LABELS,
    PERFILES_TODOS,
)

app.register_blueprint(auth_bp)
init_oauth(app)


─────────────────────────────────────────────────────
PASO 2 · Añadir la columna perfil en inicializar_todo()
─────────────────────────────────────────────────────

for col, ddl in [
    ("email",     "VARCHAR(150)"),
    ("es_admin",  "INT DEFAULT 0"),
    ("perfil",    "INT DEFAULT 2"),      # ← NUEVO
    ("google_id", "VARCHAR(128)"),       # ← NUEVO
    ("avatar",    "VARCHAR(512)"),       # ← NUEVO
]:
    if not column_exists(cursor, 'usuarios', col):
        cursor.execute(f"ALTER TABLE usuarios ADD COLUMN {col} {ddl}")


─────────────────────────────────────────────────────
PASO 3 · Actualizar el login en app_web.py
         (si mantienes la ruta ahí en lugar de usar el blueprint)
─────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        ident    = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        conn     = get_connection()
        usuario  = conn.execute(
            "SELECT * FROM usuarios WHERE username=? OR email=?",
            (ident, ident)
        ).fetchone()
        conn.close()
        if usuario and check_password_hash(dict(usuario)["password"], password):
            u = dict(usuario)
            session["user_id"]         = u["id"]
            session["user"]            = u["username"]
            session["perfil"]          = u.get("perfil", PERFIL_TAREAS)
            session["es_admin"]        = u.get("es_admin", 0)
            session["puede_formacion"] = puede_ver_formacion(u.get("perfil", PERFIL_TAREAS))
            session["puede_tareas"]    = puede_ver_tareas(u.get("perfil", PERFIL_TAREAS))
            session["es_coordinador"]  = u.get("perfil", 0) in (
                                            PERFIL_COORDINADOR, PERFIL_ADMIN, PERFIL_SUPERADMIN)
            return redirect("/")
        error = "Usuario o contraseña incorrectos."
    return render_template("login.html", error=error)


─────────────────────────────────────────────────────
PASO 4 · Proteger rutas con los nuevos decoradores
─────────────────────────────────────────────────────

# Ruta de tareas (solo quien puede ver tareas)
@app.route("/")
@tareas_required
def index(): ...

# Ruta de formación (solo quien puede ver formación)
@app.route("/formacion")
@formacion_required
def formacion(): ...

# Ruta de coordinador/admin
@app.route("/equipo")
@coordinador_required
def equipo(): ...

# Ruta de administración
@app.route("/usuarios")
@admin_required
def usuarios(): ...

# Ruta exclusiva SuperAdmin
@app.route("/superadmin")
@superadmin_required
def superadmin(): ...


─────────────────────────────────────────────────────
PASO 5 · Pasar perfiles disponibles a la plantilla usuarios.html
─────────────────────────────────────────────────────

@app.route("/usuarios")
@admin_required
def usuarios():
    ...
    return render_template(
        "usuarios.html",
        usuarios=lista_usuarios,
        categorias=cats,
        perfiles=PERFILES_TODOS,          # ← NUEVO: lista de (valor, nombre, desc)
        perfil_labels=PERFIL_LABELS,      # ← NUEVO: {valor: etiqueta}
    )


─────────────────────────────────────────────────────
PASO 6 · Navbar en base.html — usar session.perfil
─────────────────────────────────────────────────────

En base.html reemplaza las condiciones es_admin por perfil:

  {% if session.get('puede_formacion') %}
  <li class="nav-item">
    <a class="nav-link" href="/formacion">🎓 Formación</a>
  </li>
  {% endif %}

  {% if session.get('puede_tareas') %}
  <li class="nav-item">
    <a class="nav-link" href="/">⊞ Tareas</a>
  </li>
  {% endif %}

  {% if session.get('es_admin', 0) >= 1 %}
  <li class="nav-item">
    <a class="nav-link" href="/usuarios">👥 Usuarios</a>
  </li>
  {% endif %}

  {% if session.get('es_admin', 0) == 2 %}
  <li class="nav-item">
    <a class="nav-link" href="/superadmin">📊 Gráficos Globales</a>
  </li>
  {% endif %}

*/
