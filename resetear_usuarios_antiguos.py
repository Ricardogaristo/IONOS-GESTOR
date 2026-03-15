"""
resetear_usuarios_antiguos.py
==============================
Resetea la contraseña de todos los usuarios con pw_format=0
(formato antiguo incompatible con el nuevo sistema sha256).

Uso:
    py resetear_usuarios_antiguos.py

Resultado: imprime usuario → nueva contraseña temporal
El admin puede comunicar las contraseñas o cambiarlas desde /usuarios
"""
import hashlib
import secrets
from werkzeug.security import generate_password_hash
from db_mysql import get_tareas_conn

conn   = get_tareas_conn()
cursor = conn.cursor()

cursor.execute(
    "SELECT id, username FROM usuarios WHERE pw_format = 0 OR pw_format IS NULL"
)
usuarios = cursor.fetchall()

if not usuarios:
    print("✅ No hay usuarios con formato antiguo. Todo correcto.")
    conn.close()
    exit()

print(f"Usuarios con formato antiguo: {len(usuarios)}\n")
print(f"{'Usuario':<20} {'Nueva contraseña'}")
print("-" * 40)

for u in usuarios:
    uid      = u["id"]
    username = u["username"]

    # Contraseña temporal: 8 chars aleatorios legibles
    nueva = secrets.token_urlsafe(8)

    sha      = hashlib.sha256(nueva.encode()).hexdigest()
    new_hash = generate_password_hash(sha)

    cursor.execute(
        "UPDATE usuarios SET password=%s, pw_format=1 WHERE id=%s",
        (new_hash, uid)
    )
    print(f"{username:<20} {nueva}")

conn.commit()
conn.close()

print("\n✅ Listo. Comunica las contraseñas a cada usuario.")
print("   También puedes cambiarlas desde el panel /usuarios")
