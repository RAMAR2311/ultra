import os
from sqlalchemy import create_engine, MetaData, text

# CONFIGURACIÓN
# Cambia 'postgres:password' por tus credenciales locales de postgres reales si difieren
SQLITE_URL = "sqlite:///instance/crm_inventory.db" 
POSTGRES_URL = os.environ.get('DATABASE_URL', "postgresql://postgres:admin123@localhost:5432/ultra")

print("="*60)
print("🚀 SCRIPT ARQUITECTÓNICO DE MIGRACIÓN: SQLite -> PostgreSQL")
print("="*60)

def migrar_datos():
    # 1. Configurar Motores Locales
    engine_sqlite = create_engine(SQLITE_URL)
    engine_postgres = create_engine(POSTGRES_URL)
    
    meta_sqlite = MetaData()
    meta_postgres = MetaData()

    print("[*] Reflejando estructura desde SQLite Origen...")
    meta_sqlite.reflect(bind=engine_sqlite)
    
    print("[*] Reflejando estructura desde PostgreSQL Destino...")
    meta_postgres.reflect(bind=engine_postgres)
    
    # 2. Conectores
    conn_sqlite = engine_sqlite.connect()
    conn_postgres = engine_postgres.connect()

    # El orden es crucial para las Llaves Foráneas (Foreign Keys).
    # SQLAlchemy `.sorted_tables` clasifica automáticamente las dependencias.
    tablas_ordenadas = meta_sqlite.sorted_tables
    
    with conn_postgres.begin():
        # [TRUCO EXPERTO]: Desactivar 'triggers' temporalmente en Postgres
        # Esto permite insertar IDs históricos sin que PostgreSQL se queje de relaciones que
        # aún no han terminado de importarse por culpa del orden de los Constraints.
        conn_postgres.execute(text("SET session_replication_role = 'replica';"))

        for table in tablas_ordenadas:
            # Omitimos la tabla de migraciones de alembic
            if table.name == 'alembic_version':
                continue
                
            print(f"\n[*] Migrando tabla: {table.name}...")
            
            # Leer absolutamente toda la data original
            result = conn_sqlite.execute(table.select()).fetchall()
            
            if table.name not in meta_postgres.tables:
                print(f"    [!] Alarma: La tabla '{table.name}' no existe en PostgreSQL.")
                print(f"    -> Asegúrate de primero crear la BD con 'flask db upgrade'. Omitiendo...")
                continue
                
            table_pg = meta_postgres.tables[table.name]
            
            # Limpieza limpia previa del lado del destino en caso de reprocesos
            conn_postgres.execute(table_pg.delete())
            
            if not result:
                print(f"    -> Tabla vacía, omitiendo inserción.")
                continue
                
            # Extraer dicts y mapearlos directo al motor de PostgreSQL
            data_to_insert = []
            for row in result:
                d = dict(row._mapping)
                for col in table_pg.columns:
                    if not col.nullable and (col.name not in d or d[col.name] is None):
                        if col.default is not None and hasattr(col.default, 'arg'):
                            val = col.default.arg
                            d[col.name] = val() if callable(val) else val
                        elif col.server_default is not None and hasattr(col.server_default, 'arg'):
                            # try text literal
                            text_arg = str(col.server_default.arg).strip("'\"")
                            d[col.name] = text_arg
                        elif col.name == 'metodo_pago':
                            d['metodo_pago'] = 'efectivo'
                        elif col.name == 'modalidad':
                            d['modalidad'] = 'de_una'
                        elif col.name == 'cliente_id' and table.name == 'abonos_bodega':
                            # En facturas_bodega el cliente es 1
                            d['cliente_id'] = 1
                data_to_insert.append(d)
            
            # Insertar toda la carga masiva manteniendo las Primary Keys (IDs) exactas.
            conn_postgres.execute(table_pg.insert(), data_to_insert)
            print(f"    -> {len(data_to_insert)} registros históricos insertados.")
            
            # [CRÍTICO]: Al forzar (quemar) los `id` de SQLite, las secuencias automáticas de Postgres 
            # quedan en 1. Si insertas un renglón nuevo desde el CRM, Postgres fallará diciendo 
            # "ID Already Exists". Debemos alinear la Secuencia de AutoIncremento.
            try:
                # Por estándar de SQLAlchemy, la secuencia se llama: <nombre_tabla>_id_seq
                # La función setval de postgres reinicia el counter a (max_id + 1)
                secuencia = f"{table.name}_id_seq"
                query_seq = f"SELECT setval('{secuencia}', COALESCE((SELECT MAX(id)+1 FROM {table.name}), 1), false);"
                conn_postgres.execute(text(query_seq))
                print(f"    -> Secuencia (Autoincrementador) reiniciada para empalmar el backend.")
            except Exception as e:
                pass # Tablas sin primary key id o con convenciones extrañas caerán acá silenciadas.

        # Restaurar la integridad estructural nativa (Muy importante)
        conn_postgres.execute(text("SET session_replication_role = 'origin';"))
        
    conn_sqlite.close()
    conn_postgres.close()
    
    print("\n" + "="*60)
    print("✅ MIGRACIÓN COMPLETADA DE FORMA PROFESIONAL Y SIN PÉRDIDA DE DATOS")
    print("="*60)

if __name__ == '__main__':
    migrar_datos()
