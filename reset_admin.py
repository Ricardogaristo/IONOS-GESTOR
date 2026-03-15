"""
reset_admin.py — Resetear contraseña de admin a "1234"
=======================================================
Uso:
    cd C:\\Users\\RGaristo\\Desktop\\IONOS\\IONOS-GESTOR
    py reset_admin.py
"""
import hashlib
from werkzeug.security import generate_password_hash
from db_mysql import get_tareas_conn

# El cliente envía sha256(password), el servidor almacena bcrypt(sha256(password))
sha = hashlib.sha256("1234".encode()).hexdigest()
nuevo_hash = generate_password_hash(sha)
print(f"sha256('1234') = {sha}")
print(f"bcrypt hash    = {nuevo_hash[:40]}...")

conn   = get_tareas_conn()
cursor = conn.cursor()
cursor.execute(
    "UPDATE usuarios SET password=%s, perfil=20, es_admin=2 WHERE username='admin'",
    (nuevo_hash,)
)
conn.commit()

cursor.execute(
    "SELECT id, username, LEFT(password,40) AS pw, perfil FROM usuarios WHERE username='admin'"
)
print(f"BD actualizada: {dict(cursor.fetchone())}")
conn.close()
print("\n✅ Entra con: admin / 1234")