"""
migrar_usuarios.py — Migra TODOS los usuarios al formato sha256
===============================================================
Este script asigna una contraseña temporal a cada usuario que
tenga pw_format=0 (formato viejo) y la convierte a bcrypt(sha256).

Uso:
    py migrar_usuarios.py

Después de ejecutar:
- admin         → contraseña: 1234  (pw_format=1)
- demás usuarios → contraseña: Usuario1234  (pw_format=1)
  (el admin deberá comunicarles su nueva contraseña o resetearla
   individualmente desde el panel /usuarios)
"""
import hashlib
from werkzeug.security import generate_password_hash
from db_mysql import get_tareas_conn

PASS_ADMIN   = "1234"
PASS_DEFAULT = "1234"  # contraseña temporal para el resto

conn   = get_tareas_conn()
cursor = conn.cursor()

# 1. Añadir columna pw_format si no existe
try:
    cursor.execute("ALTER TABLE usuarios ADD COLUMN pw_format INT NOT NULL DEFAULT 0")
    conn.commit()
    print("✅ Columna pw_format añadida")
except Exception as e:
    if "duplicate" in str(e).lower() or "already exists" in str(e).lower() or "1060" in str(e):
        print("ℹ  Columna pw_format ya existe")
    else:
        print(f"⚠  {e}")

# 2. Obtener usuarios con formato viejo
cursor.execute("SELECT id, username, pw_format FROM usuarios")
usuarios = cursor.fetchall()

migrados = 0
for u in usuarios:
    uid      = u["id"]
    username = u["username"]
    fmt      = u.get("pw_format", 0) or 0

    if fmt == 1:
        print(f"  ✓ {username} — ya migrado, sin cambios")
        continue

    # Elegir contraseña temporal
    pw_plain = PASS_ADMIN if username == "admin" else PASS_DEFAULT
    sha      = hashlib.sha256(pw_plain.encode()).hexdigest()
    new_hash = generate_password_hash(sha)

    cursor.execute(
        "UPDATE usuarios SET password=%s, pw_format=1 WHERE id=%s",
        (new_hash, uid)
    )
    print(f"  🔑 {username} → contraseña temporal: '{pw_plain}' (pw_format=1)")
    migrados += 1

conn.commit()
conn.close()

print(f"\n✅ Migración completada — {migrados} usuario(s) actualizados")
print("\nContraseñas asignadas:")
print(f"  admin    → {PASS_ADMIN}")
print(f"  resto    → {PASS_DEFAULT}")
print("\nEl administrador puede cambiar contraseñas individuales desde /usuarios")
