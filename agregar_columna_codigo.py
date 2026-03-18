from db_mysql import get_tareas_conn, column_exists

conn   = get_tareas_conn()
cursor = conn.cursor()

if not column_exists(cursor, 'tareas', 'codigo'):
    cursor.execute("ALTER TABLE tareas ADD COLUMN codigo TEXT")
    conn.commit()
    print("✅ Columna 'codigo' agregada correctamente")
else:
    print("⚠️ La columna 'codigo' ya existe")

conn.close()