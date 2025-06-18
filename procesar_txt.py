import xmlrpc.client
import pandas as pd
import os
import shutil
import traceback

# === CONEXIÓN A ODOO PRODUCCIÓN ===
url = 'https://blackdogpanama.odoo.com'
db = 'dev-psdc-blackdogpanama-prod-3782039'
username = 'mercadeo@blackdogpanama.com'
password = 'Emanuel1010.'

common = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/common')
uid = common.authenticate(db, username, password, {})
models = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/object')

# === MAPAS ===
location_map = {
    "BELLA VISTA": 41, "BRISAS DEL GOLF": 66, "ALBROOK FIELDS": 58,
    "CALLE 50": 74, "VERSALLES": 987, "COCO DEL MAR": 1029,
    "COSTA VERDE": 576, "VILLA ZAITA": 652, "CONDADO DEL REY": 660,
    "SANTA MARÍA": 99, "BRISAS NORTE": 668, "OCEAN MALL": 28,
    "PLAZA EMPORIO": 8, "BODEGA": 18
}

picking_type_map = {
    "BELLA VISTA": 154, "BRISAS DEL GOLF": 158, "ALBROOK FIELDS": 154,
    "CALLE 50": 160, "VERSALLES": 1821, "COCO DEL MAR": 1957,
    "COSTA VERDE": 329, "VILLA ZAITA": 398, "CONDADO DEL REY": 399,
    "SANTA MARÍA": 164, "BRISAS NORTE": 400, "OCEAN MALL": 152,
    "PLAZA EMPORIO": 126
}

alias_map = {
    "PARK PLAZA MALL (BELLA VISTA)": "BELLA VISTA",
    "ALTAPLZ BRISAS DEL GOLF": "BRISAS DEL GOLF",
    "AF - ALBROOK FIELDS": "ALBROOK FIELDS",
    "ALBROOK": "ALBROOK FIELDS",
    "C50 - CALLE 50": "CALLE 50",
    "SANTA MARIA": "SANTA MARÍA",
    "PH OCEAN MALL": "OCEAN MALL"
}

# === CARPETAS ===
folder = os.path.dirname(os.path.abspath(__file__))
procesados = os.path.join(folder, "procesados")
os.makedirs(procesados, exist_ok=True)

# === PROCESAMIENTO ===
for archivo in os.listdir(folder):
    if archivo.lower().endswith(".txt"):
        path = os.path.join(folder, archivo)
        print(f"\n📥 Procesando: {archivo}")

        try:
            df = pd.read_csv(path, sep=";", encoding="latin-1", dtype=str)
            df.columns = df.columns.str.replace('\ufeff', '', regex=False).str.strip()
            columnas = list(df.columns)
            print(f"🧾 Columnas detectadas: {columnas}")

            total_productos = 0  # Contador por archivo

            # === FORMATO 1 ===
            if "COD_BARRA" in columnas and "CANTIDAD" in columnas and "BODEGA" in columnas:
                grupo = df.groupby("BODEGA")

                for cliente, items in grupo:
                    destino = alias_map.get(cliente.strip().upper(), cliente.strip().upper())

                    if destino not in location_map or destino not in picking_type_map:
                        print(f"❌ No se encontró ubicación o picking type para: {destino}")
                        continue

                    picking_id = models.execute_kw(db, uid, password, 'stock.picking', 'create', [{
                        'picking_type_id': picking_type_map[destino],
                        'location_id': location_map["BODEGA"],
                        'location_dest_id': location_map[destino],
                        'origin': f"Auto-importación IMPA {cliente}",
                    }])

                    errores = 0

                    for _, row in items.iterrows():
                        row = row.apply(lambda x: x.strip() if isinstance(x, str) else x)
                        codigo = str(row['COD_BARRA']).replace(" ", "").replace("-", "")
                        cantidad = float(row['CANTIDAD'])
                        descripcion = row.get('DESCRIPCION', 'Sin descripción')

                        productos = models.execute_kw(db, uid, password,
                            'product.product', 'search_read',
                            [[['barcode', '=', codigo]]],
                            {'fields': ['id', 'uom_id'], 'limit': 1})

                        if not productos:
                            print(f"❌ Producto no encontrado (barcode: {codigo})")
                            errores += 1
                            continue

                        producto = productos[0]

                        models.execute_kw(db, uid, password, 'stock.move', 'create', [{
                            'name': descripcion,
                            'product_id': producto['id'],
                            'product_uom_qty': cantidad,
                            'product_uom': producto['uom_id'][0],
                            'picking_id': picking_id,
                            'location_id': location_map["BODEGA"],
                            'location_dest_id': location_map[destino],
                        }])
                        total_productos += 1

                    print(f"📝 Transferencia (formato 1) creada: ID {picking_id} para {cliente}")
                    if errores == 0:
                        print("✅ Todo se procesó sin problemas")

            # === FORMATO 2 ===
            elif "Código" in columnas and "Referencia Interna" in columnas and "SUCURSAL" in columnas:
                grupo = df.groupby("SUCURSAL")

                for cliente, items in grupo:
                    destino = alias_map.get(cliente.strip().upper(), cliente.strip().upper())

                    if destino not in location_map or destino not in picking_type_map:
                        print(f"❌ No se encontró ubicación o picking type para: {destino}")
                        continue

                    picking_id = models.execute_kw(db, uid, password, 'stock.picking', 'create', [{
                        'picking_type_id': picking_type_map[destino],
                        'location_id': location_map["BODEGA"],
                        'location_dest_id': location_map[destino],
                        'origin': f"IMPA-DOEL A BD: {cliente}",
                    }])

                    errores = 0

                    for _, row in items.iterrows():
                        row = row.apply(lambda x: x.strip() if isinstance(x, str) else x)
                        codigo = str(row['Código']).replace(" ", "").replace("-", "")
                        referencia = row['Referencia Interna']
                        cantidad = float(row['Surtido'])
                        descripcion = row['Descripción']

                        productos = models.execute_kw(db, uid, password,
                            'product.product', 'search_read',
                            [[['barcode', '=', codigo]]],
                            {'fields': ['id', 'uom_id'], 'limit': 1})

                        if not productos:
                            productos = models.execute_kw(db, uid, password,
                                'product.product', 'search_read',
                                [[['default_code', '=', referencia]]],
                                {'fields': ['id', 'uom_id'], 'limit': 1})

                        if not productos:
                            print(f"❌ Producto no encontrado (barcode: {codigo}, código: {referencia})")
                            errores += 1
                            continue

                        producto = productos[0]

                        models.execute_kw(db, uid, password, 'stock.move', 'create', [{
                            'name': descripcion,
                            'product_id': producto['id'],
                            'product_uom_qty': cantidad,
                            'product_uom': producto['uom_id'][0],
                            'picking_id': picking_id,
                            'location_id': location_map["BODEGA"],
                            'location_dest_id': location_map[destino],
                        }])
                        total_productos += 1

                    print(f"📝 Transferencia (formato 2) creada: ID {picking_id} para {cliente}")
                    if errores == 0:
                        print("✅ Todo se procesó sin problemas")

            else:
                print(f"⚠️ Formato no reconocido en: {archivo}")

            shutil.move(path, os.path.join(procesados, archivo))
            print(f"📂 Archivo movido a /procesados")
            print(f"📦 Total de productos transferidos: {total_productos}")

        except Exception:
            print(f"🔥 Error procesando archivo: {archivo}")
            traceback.print_exc()
