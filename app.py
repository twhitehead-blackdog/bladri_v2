from flask import Flask, render_template, request, redirect, url_for, session, send_file, jsonify, abort
import os
import json
import subprocess
import zipfile
import time
import re
from datetime import datetime, timedelta
import pandas as pd
import xmlrpc.client
import io
import traceback
from functools import wraps
import logging
from pathlib import Path
import signal
import sys
import threading
from werkzeug.middleware.proxy_fix import ProxyFix

# ===== CONFIGURACIÓN DE LOGGING ROBUSTA =====
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('app.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# ===== CONFIGURACIÓN DE FLASK PARA PRODUCCIÓN =====
app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'bladri123-super-secret-key-2024-production')
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=45)
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('FLASK_ENV') == 'production'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# ===== CONSTANTES Y DIRECTORIOS =====
CONFIG_FILE = 'config_ajustes.json'
CONFIG_DEFAULT = 'config_default.json'
HISTORIAL_FILE = 'historial_zips.json'
HISTORIAL_TXT_FILE = 'historial_txt.json'
ZIPS_DIR = 'ZIPS_GENERADOS'
PEDIDOS_DIR = 'pedidos_sugeridos'
TXT_DIR = 'txt_subidos'
LOGS_DIR = 'logs'

# Crear directorios con permisos correctos
for directory in [ZIPS_DIR, PEDIDOS_DIR, TXT_DIR, LOGS_DIR]:
    try:
        os.makedirs(directory, exist_ok=True)
        os.chmod(directory, 0o755)
    except Exception as e:
        logger.error(f"Error creando directorio {directory}: {e}")

# ===== MAPAS DE UBICACIONES Y TIPOS DE PICKING =====
location_map = {
    "BELLA VISTA": 41, "BRISAS DEL GOLF": 66, "ALBROOK FIELDS": 58,
    "CALLE 50": 74, "VERSALLES": 987, "COCO DEL MAR": 1029,
    "COSTA VERDE": 576, "VILLA ZAITA": 652, "CONDADO DEL REY": 660,
    "SANTA MARÍA": 99, "BRISAS NORTE": 668, "OCEAN MALL": 28,
    "PLAZA EMPORIO": 8, "BODEGA": 18, "DAVID": 1079
}

picking_type_map = {
    "BELLA VISTA": 154, "BRISAS DEL GOLF": 158, "ALBROOK FIELDS": 154,
    "CALLE 50": 160, "VERSALLES": 1821, "COCO DEL MAR": 1957,
    "COSTA VERDE": 329, "VILLA ZAITA": 398, "CONDADO DEL REY": 399,
    "SANTA MARÍA": 164, "BRISAS NORTE": 400, "OCEAN MALL": 152,
    "PLAZA EMPORIO": 126, "DAVID": 2034
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

# ===== USUARIOS CON CONTRASEÑAS SEGURAS =====
users = {
    "admin": os.environ.get('ADMIN_PASS', "admin"),
    "seria_bd": os.environ.get('SERIA_PASS', "A1b2C3d4"),
    "ricardo_bd": os.environ.get('RICARDO_PASS', "E5f6G7h8"),
    "angie_bd": os.environ.get('ANGIE_PASS', "I9j0K1l2"),
    "daniel_bd": os.environ.get('DANIEL_PASS', "M3n4O5p6"),
    "hernan_bd": os.environ.get('HERNAN_PASS', "Q7r8S9t0"),
    "luis_bd": os.environ.get('LUIS_PASS', "U1v2W3x4"),
    "tristan_bd": os.environ.get('TRISTAN_PASS', "Y5z6A7b8"),
    "yesi_bd": os.environ.get('YESI_PASS', "C9d0E1f2"),
    "roman_bd": os.environ.get('ROMAN_PASS', "G3h4I5j6")
}

# ===== DECORADORES Y FUNCIONES AUXILIARES =====
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            logger.warning(f"Acceso no autorizado desde {request.remote_addr} a {request.endpoint}")
            if request.is_json:
                return jsonify({"error": "No autorizado"}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def log_user_action(action, details=""):
    user = session.get('user', 'anonymous')
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    logger.info(f"Usuario: {user} | Acción: {action} | Detalles: {details} | IP: {ip} | UA: {request.headers.get('User-Agent', 'Unknown')[:100]}")

def get_odoo_connection():
    """Conexión robusta a Odoo con manejo de errores"""
    try:
        url = os.environ.get('ODOO_URL', "https://blackdogpanama.odoo.com")
        db = os.environ.get('ODOO_DB', "dev-psdc-blackdogpanama-prod-3782039")
        username = os.environ.get('ODOO_USER', "mercadeo@blackdogpanama.com")
        password = os.environ.get('ODOO_PASS', "Emanuel1010.")
        
        common = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/common')
        uid = common.authenticate(db, username, password, {})
        
        if not uid:
            raise Exception("Autenticación fallida en Odoo")
            
        models = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/object')
        logger.info(f"Conexión exitosa a Odoo - UID: {uid}")
        return db, uid, password, models
        
    except Exception as e:
        logger.error(f"Error conectando a Odoo: {e}")
        raise

def validate_and_process_file(uploaded_file, db, uid, password, models):
    """Validación robusta de archivos CSV con manejo de errores"""
    validation_results = {
        'is_valid': False,
        'format_detected': None,
        'total_items': 0,
        'errors': [],
        'data_by_location': {}
    }
    
    try:
        content_bytes = uploaded_file.read()
        uploaded_file.seek(0)
        
        if not content_bytes:
            validation_results['errors'].append({'message': 'Archivo vacío'})
            return validation_results
            
        # Detectar encoding
        try:
            content = content_bytes.decode('utf-8')
        except UnicodeDecodeError:
            try:
                content = content_bytes.decode('latin-1')
            except UnicodeDecodeError:
                content = content_bytes.decode('cp1252', errors='ignore')
        
        # Procesar CSV
        df = pd.read_csv(io.StringIO(content), sep=";", dtype=str)
        df.columns = df.columns.str.replace('\ufeff', '', regex=False).str.strip().str.upper()
        columnas = list(df.columns)
        
        validation_results['total_items'] = len(df)
        validation_results['format_detected'] = 'CSV con separador ;'
        
        # Mapeo de columnas
        columnas_formato1 = {
            'COD_BARRA': ['COD_BARRA', 'CODBARRA', 'CODIGO_BARRA', 'CODIGOBARRAS', 'BARCODE'],
            'CANTIDAD': ['CANTIDAD', 'CANT', 'QTY', 'QUANTITY'],
            'TIENDA': ['NBR_CLIENTE', 'TIENDA', 'SUCURSAL']
        }
        
        def encontrar_columna(posibles_nombres, columnas_df):
            for col in posibles_nombres:
                if col in columnas_df:
                    return col
            return None
        
        formato1_cols = {k: encontrar_columna(v, columnas) for k, v in columnas_formato1.items()}
        
        if all(formato1_cols.values()):
            validation_results['format_detected'] = 'FORMATO1'
            df = df.rename(columns={v: k for k, v in formato1_cols.items()})
            
            # Procesar por ubicación
            for location, items in df.groupby('TIENDA'):
                location = str(location).strip().upper()
                destino = alias_map.get(location, location)
                
                location_data = {
                    'valid_items': [],
                    'invalid_items': [],
                    'total_items': len(items),
                    'location_valid': destino in location_map,
                    'original_name': location
                }
                
                if not location_data['location_valid']:
                    location_data['error'] = f"Ubicación no válida: {location}"
                    validation_results['errors'].append({'message': location_data['error']})
                else:
                    # Validar productos
                    for idx, row in items.iterrows():
                        item_validation = {
                            'row_index': idx + 2,
                            'is_valid': True,
                            'errors': []
                        }
                        
                        try:
                            codigo = str(row['COD_BARRA']).strip().replace(" ", "").replace("-", "")
                            cantidad = float(row['CANTIDAD'])
                            
                            if cantidad <= 0:
                                raise ValueError("La cantidad debe ser mayor que 0")
                            
                            # Buscar producto en Odoo
                            productos = models.execute_kw(db, uid, password,
                                'product.product', 'search_read',
                                [[['barcode', '=', codigo]]],
                                {'fields': ['id', 'name', 'uom_id'], 'limit': 1})
                            
                            if not productos:
                                referencia = str(row.get('REFERENCIA INTERNA', '')).strip()
                                if referencia:
                                    productos = models.execute_kw(db, uid, password,
                                        'product.product', 'search_read',
                                        [[['default_code', '=', referencia]]],
                                        {'fields': ['id', 'name', 'uom_id'], 'limit': 1})
                            
                            if not productos:
                                item_validation['is_valid'] = False
                                item_validation['errors'].append(f"Producto no encontrado - Código de barras: {codigo}")
                                location_data['invalid_items'].append(item_validation)
                            else:
                                item_validation['product_data'] = productos[0]
                                item_validation['quantity'] = cantidad
                                location_data['valid_items'].append(item_validation)
                                
                        except Exception as e:
                            item_validation['is_valid'] = False
                            item_validation['errors'].append(str(e))
                            location_data['invalid_items'].append(item_validation)
                
                validation_results['data_by_location'][destino] = location_data
            
            validation_results['is_valid'] = any(
                data['valid_items'] for data in validation_results['data_by_location'].values()
            )
        else:
            validation_results['errors'].append({
                'message': f'Formato no reconocido. Columnas encontradas: {columnas}'
            })
            
    except Exception as e:
        logger.error(f"Error validando archivo: {e}")
        validation_results['errors'].append({'message': f'Error procesando CSV: {str(e)}'})
    
    return validation_results

def create_transfers(validation_results, db, uid, password, models):
    """Creación robusta de transferencias en Odoo"""
    transfer_results = {
        'success': False,
        'transfers_created': [],
        'errors': []
    }
    
    try:
        for destino, location_data in validation_results.get('data_by_location', {}).items():
            if not location_data.get('valid_items'):
                continue
            
            # Crear picking
            picking_id = models.execute_kw(db, uid, password, 'stock.picking', 'create', [{
                'picking_type_id': picking_type_map[destino],
                'location_id': location_map["BODEGA"],
                'location_dest_id': location_map[destino],
                'origin': f"Auto-importación {location_data['original_name']} - {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            }])
            
            transfer_info = {
                'picking_id': picking_id,
                'location': destino,
                'original_name': location_data['original_name'],
                'items_processed': 0,
                'items_failed': 0
            }
            
            # Crear movimientos
            for item in location_data['valid_items']:
                try:
                    models.execute_kw(db, uid, password, 'stock.move', 'create', [{
                        'name': item['product_data']['name'],
                        'product_id': item['product_data']['id'],
                        'product_uom_qty': item['quantity'],
                        'product_uom': item['product_data']['uom_id'][0],
                        'picking_id': picking_id,
                        'location_id': location_map["BODEGA"],
                        'location_dest_id': location_map[destino],
                    }])
                    transfer_info['items_processed'] += 1
                except Exception as e:
                    transfer_info['items_failed'] += 1
                    transfer_results['errors'].append({
                        'picking_id': picking_id,
                        'product_id': item['product_data']['id'],
                        'error': str(e)
                    })
            
            transfer_results['transfers_created'].append(transfer_info)
        
        transfer_results['success'] = len(transfer_results['transfers_created']) > 0
        
    except Exception as e:
        logger.error(f"Error creando transferencias: {e}")
        transfer_results['errors'].append({'message': str(e)})
    
    return transfer_results

def ensure_config_exists():
    """Asegurar que existe configuración por defecto"""
    try:
        if not os.path.exists(CONFIG_FILE):
            default_config = {
                "meses_inventario": {"general": 1.0, "categorias": {}},
                "minimos_alimentos": {"regular": 1, "chica": 1},
                "minimos_accesorios": {
                    "regular": {"default": 3, "correa": 2, "collar": 2, "juguete": 1},
                    "chica": {"default": 2, "correa": 1, "collar": 1, "juguete": 1}
                },
                "minimos_medicamentos": {"regular": 1, "chica": 1},
                "minimos_para_pedir": {}
            }
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(default_config, f, indent=2, ensure_ascii=False)
            logger.info("Configuración por defecto creada")
    except Exception as e:
        logger.error(f"Error creando configuración: {e}")

# ===== MIDDLEWARE Y HOOKS =====
@app.before_request
def before_request():
    """Logging de todas las requests"""
    if request.endpoint and request.endpoint != 'static':
        ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        logger.info(f"Request: {request.method} {request.path} from {ip}")

@app.after_request
def after_request(response):
    """Headers de seguridad"""
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    return response

# ===== RUTAS PRINCIPALES =====
@app.route('/')
def home():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        try:
            data = request.get_json() if request.is_json else request.form
            username = data.get('username', '').strip()
            password = data.get('password', '').strip()
            
            if username in users and users[username] == password:
                session['user'] = username
                session.permanent = True
                log_user_action("LOGIN_SUCCESS", f"Usuario: {username}")
                
                if request.is_json:
                    return jsonify({"status": "ok", "user": username})
                return redirect(url_for('index'))
            else:
                log_user_action("LOGIN_FAILED", f"Usuario: {username}")
                error_msg = "Credenciales incorrectas"
                
                if request.is_json:
                    return jsonify({"status": "error", "message": error_msg}), 401
                return render_template('login.html', error=error_msg)
                
        except Exception as e:
            logger.error(f"Error en login: {e}")
            error_msg = "Error interno del servidor"
            
            if request.is_json:
                return jsonify({"status": "error", "message": error_msg}), 500
            return render_template('login.html', error=error_msg)
    
    return render_template('login.html')

@app.route('/logout', methods=['POST', 'GET'])
def logout():
    user = session.get('user', 'unknown')
    session.clear()
    log_user_action("LOGOUT", f"Usuario: {user}")
    
    if request.is_json:
        return jsonify({"status": "ok", "message": "Logout exitoso"})
    return redirect(url_for('login'))

@app.route('/index')
@login_required
def index():
    try:
        ensure_config_exists()
        
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            config = json.load(f)
            
        log_user_action("ACCESS_INDEX")
        return render_template('index.html', ajustes=config)
        
    except Exception as e:
        logger.error(f"Error en index: {e}")
        return render_template('index.html', ajustes={})

@app.route('/ajustes')
@login_required
def ajustes():
    try:
        ensure_config_exists()
        
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            config = json.load(f)
            
        log_user_action("ACCESS_AJUSTES")
        return render_template('ajustes.html', ajustes=config)
        
    except Exception as e:
        logger.error(f"Error en ajustes: {e}")
        return render_template('ajustes.html', ajustes={})

@app.route('/guardar_ajustes', methods=['POST'])
@login_required
def guardar_ajustes():
    try:
        data = request.get_json() if request.is_json else request.form
        
        config = {
            "meses_inventario": {"general": 1.0, "categorias": {}},
            "minimos_alimentos": {},
            "minimos_accesorios": {},
            "minimos_medicamentos": {},
            "minimos_para_pedir": {}
        }
        
        # Procesar datos
        for key, val in data.items():
            if not str(val).strip():
                continue
                
            try:
                if key == 'meses_inventario_general':
                    config['meses_inventario']['general'] = float(val)
                elif key.startswith('meses_inventario_categorias['):
                    nombre = key[len('meses_inventario_categorias['):-1]
                    config['meses_inventario']['categorias'][nombre] = float(val)
                elif key.startswith('minimos_alimentos_'):
                    config['minimos_alimentos'][key.replace('minimos_alimentos_', '')] = int(val)
                elif key.startswith('minimos_medicamentos_'):
                    config['minimos_medicamentos'][key.replace('minimos_medicamentos_', '')] = int(val)
                elif key.startswith('minimos_accesorios_'):
                    tipo, nombre = key.replace('minimos_accesorios_', '').split('_', 1)
                    config['minimos_accesorios'].setdefault(tipo, {})[nombre.replace('_', ' ')] = int(val)
                elif key.startswith('minimos_para_pedir_'):
                    tipo, nombre = key.replace('minimos_para_pedir_', '').split('_', 1)
                    config['minimos_para_pedir'].setdefault(tipo, {})[nombre.replace('_', ' ')] = int(val)
            except Exception as e:
                logger.error(f"Error procesando {key}={val}: {e}")
        
        # Guardar configuración
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        
        log_user_action("SAVE_CONFIG", "Configuración guardada")
        
        if request.is_json:
            return jsonify({"status": "ok", "message": "Guardado"})
        return 'Guardado', 200
        
    except Exception as e:
        logger.error(f"Error guardando configuración: {e}")
        
        if request.is_json:
            return jsonify({"status": "error", "message": str(e)}), 400
        return str(e), 400

@app.route('/resetear_ajustes', methods=['POST'])
@login_required
def resetear_ajustes():
    try:
        if os.path.exists(CONFIG_DEFAULT):
            with open(CONFIG_DEFAULT, 'r', encoding='utf-8') as fsrc:
                with open(CONFIG_FILE, 'w', encoding='utf-8') as fdst:
                    fdst.write(fsrc.read())
        else:
            default_config = {
                "meses_inventario": {"general": 1.0, "categorias": {}},
                "minimos_alimentos": {},
                "minimos_accesorios": {},
                "minimos_medicamentos": {},
                "minimos_para_pedir": {}
            }
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(default_config, f, indent=2, ensure_ascii=False)
        
        log_user_action("RESET_CONFIG", "Configuración reseteada")
        
        if request.is_json:
            return jsonify({"status": "ok", "message": "Reseteado"})
        return 'Reset ok', 200
        
    except Exception as e:
        logger.error(f"Error reseteando configuración: {e}")
        
        if request.is_json:
            return jsonify({"status": "error", "message": str(e)}), 400
        return str(e), 400

# ===== FUNCIÓN GENERAR CORREGIDA PARA ARCHIVOS CON FECHA =====
@app.route('/generar', methods=['POST'])
@login_required
def generar():
    try:
        user = session['user']
        log_user_action("START_GENERATE_ORDERS")
        start_time = datetime.now()
        logger.info(f"Hora de inicio (servidor): {start_time.isoformat()}")
        
        # Ejecutar generar.py con timeout robusto
        try:
            resultado = subprocess.run(
                [sys.executable, 'generar.py'],
                capture_output=True,
                text=True,
                timeout=300,  # 5 minutos
                cwd=os.getcwd(),
                encoding='utf-8',
                errors='replace'
            )
        except subprocess.TimeoutExpired:
            log_user_action("GENERATE_ORDERS_ERROR", "Timeout ejecutando generar.py")
            return abort(500, description="Timeout al ejecutar generar.py (>5 minutos)")
        except Exception as e:
            log_user_action("GENERATE_ORDERS_ERROR", f"Error subprocess: {str(e)}")
            return abort(500, description=f"Error ejecutando generar.py: {str(e)}")
        
        if resultado.returncode != 0:
            error_msg = resultado.stderr or "Error desconocido en generar.py"
            log_user_action("GENERATE_ORDERS_ERROR", error_msg)
            return abort(500, description=error_msg)
        
        # Esperar a que se generen los archivos
        time.sleep(3)
        
        # ===== BUSCAR ARCHIVOS POR SUFIJO DE FECHA =====
        archivos = []
        sufijos = []
        patron = re.compile(r'_(\d{8}_\d{6})')  # Busca _YYYYMMDD_HHMMSS
        
        logger.info(f"Buscando archivos en: {PEDIDOS_DIR}")
        logger.info(f"Directorio de trabajo actual: {os.getcwd()}")
        logger.info(f"Ruta absoluta de PEDIDOS_DIR: {os.path.abspath(PEDIDOS_DIR)}")
        
        if not os.path.exists(PEDIDOS_DIR):
            return abort(500, description=f"Directorio {PEDIDOS_DIR} no existe")
        
        # Buscar todos los archivos y extraer sufijos de fecha
        for root, dirs, files in os.walk(PEDIDOS_DIR):
            for file in files:
                filepath = os.path.join(root, file)
                if not os.path.isfile(filepath):
                    continue
                    
                match = patron.search(file)
                if match:
                    sufijos.append(match.group(1))
                    logger.info(f"Archivo con fecha encontrado: {file} | Sufijo: {match.group(1)}")
                
                archivos.append((filepath, file))
        
        if not sufijos:
            log_user_action("GENERATE_ORDERS_ERROR", "No se encontraron archivos con sufijo de fecha")
            return abort(500, description="No se encontraron archivos con sufijo de fecha en el nombre")
        
        # Obtener el sufijo más reciente
        sufijo_mas_reciente = max(sufijos)
        logger.info(f"Sufijo de fecha más reciente detectado: {sufijo_mas_reciente}")
        
        # Filtrar archivos que tengan el sufijo más reciente
        archivos_a_zip = []
        for filepath, filename in archivos:
            if sufijo_mas_reciente in filename:
                arcname = os.path.relpath(filepath, PEDIDOS_DIR)
                archivos_a_zip.append((filepath, arcname))
                logger.info(f"Incluyendo en ZIP: {filename}")
        
        if not archivos_a_zip:
            log_user_action("GENERATE_ORDERS_ERROR", "No se encontraron archivos para el ZIP con el sufijo más reciente")
            return abort(500, description="No se encontraron archivos para el ZIP con el sufijo más reciente")
        
        # Crear ZIP con nombre basado en el sufijo
        zip_name = f"pedido_{sufijo_mas_reciente}.zip"
        zip_path = os.path.join(ZIPS_DIR, zip_name)
        
        try:
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zipf:
                for filepath, arcname in archivos_a_zip:
                    zipf.write(filepath, arcname)
        except Exception as e:
            logger.error(f"Error creando ZIP: {e}")
            return abort(500, description=f"Error creando ZIP: {str(e)}")
        
        # Validar ZIP
        zip_size = os.path.getsize(zip_path)
        if zip_size < 100:
            os.remove(zip_path)
            return abort(500, description="ZIP generado vacío")
        
        try:
            with zipfile.ZipFile(zip_path, 'r') as zipf:
                bad_file = zipf.testzip()
                if bad_file:
                    os.remove(zip_path)
                    return abort(500, description=f"ZIP corrupto: {bad_file}")
        except Exception as e:
            logger.error(f"Error validando ZIP: {e}")
            os.remove(zip_path)
            return abort(500, description=f"Error validando ZIP: {str(e)}")
        
        # Actualizar historial
        try:
            historial = []
            if os.path.exists(HISTORIAL_FILE):
                with open(HISTORIAL_FILE, 'r', encoding='utf-8') as f:
                    historial = json.load(f)
            
            historial.insert(0, {
                "usuario": user,
                "nombre": zip_name,
                "fecha": start_time.strftime('%Y-%m-%d %H:%M:%S'),
                "tamaño_kb": round(zip_size / 1024, 2),
                "archivos_incluidos": len(archivos_a_zip),
                "sufijo_fecha": sufijo_mas_reciente
            })
            
            historial = historial[:10]  # Mantener solo los últimos 10
            
            with open(HISTORIAL_FILE, 'w', encoding='utf-8') as f:
                json.dump(historial, f, indent=2, ensure_ascii=False)
                
        except Exception as e:
            logger.error(f"Error actualizando historial: {e}")
        
        log_user_action("GENERATE_ORDERS_SUCCESS", f"ZIP: {zip_name}, Archivos: {len(archivos_a_zip)}, Sufijo: {sufijo_mas_reciente}")
        
        return send_file(
            zip_path,
            as_attachment=True,
            download_name=zip_name,
            mimetype='application/zip'
        )
        
    except Exception as e:
        logger.error(f"Error en generar: {e}")
        traceback.print_exc()
        log_user_action("GENERATE_ORDERS_ERROR", str(e))
        return abort(500, description=str(e))

@app.route('/historial')
@login_required
def historial():
    try:
        if not os.path.exists(HISTORIAL_FILE):
            return jsonify([])
        
        with open(HISTORIAL_FILE, 'r', encoding='utf-8') as f:
            historial = json.load(f)
        
        # Agregar URLs de descarga
        for item in historial:
            if isinstance(item, dict) and 'nombre' in item:
                item['url_descarga'] = url_for('descargar_zip', nombre=item['nombre'])
        
        log_user_action("ACCESS_HISTORIAL")
        return jsonify(historial)
        
    except Exception as e:
        logger.error(f"Error obteniendo historial: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/descargar_zip/<nombre>')
@login_required
def descargar_zip(nombre):
    try:
        # Validación de seguridad
        if not nombre.endswith('.zip') or '/' in nombre or '\\' in nombre or '..' in nombre:
            log_user_action("DOWNLOAD_ERROR", f"Nombre inválido: {nombre}")
            return abort(400, description="Nombre de archivo inválido")
        
        path = os.path.join(ZIPS_DIR, nombre)
        
        if not os.path.isfile(path):
            log_user_action("DOWNLOAD_ERROR", f"Archivo no encontrado: {nombre}")
            return abort(404, description="Archivo no encontrado")
        
        log_user_action("DOWNLOAD_ZIP", f"Archivo: {nombre}")
        
        return send_file(
            path,
            as_attachment=True,
            download_name=nombre,
            mimetype='application/zip'
        )
        
    except Exception as e:
        logger.error(f"Error descargando {nombre}: {e}")
        return abort(500, description=f"Error al descargar: {str(e)}")

@app.route('/txt', methods=['GET', 'POST'])
@login_required
def txt():
    try:
        historial = []
        if os.path.exists(HISTORIAL_TXT_FILE):
            with open(HISTORIAL_TXT_FILE, 'r', encoding='utf-8') as f:
                historial = json.load(f)
        
        if request.method == 'POST':
            archivos = request.files.getlist('archivos_txt')
            ahora = datetime.now()
            
            for archivo in archivos:
                if archivo.filename and archivo.filename.lower().endswith('.txt'):
                    try:
                        nombre = archivo.filename
                        contenido = archivo.read()
                        
                        # Guardar archivo
                        ruta_guardado = os.path.join(TXT_DIR, nombre)
                        with open(ruta_guardado, 'wb') as f:
                            f.write(contenido)
                        
                        # Contar líneas
                        try:
                            lineas = len(contenido.decode('utf-8', errors='ignore').splitlines())
                        except:
                            lineas = 0
                        
                        # Actualizar historial
                        historial.insert(0, {
                            "nombre": nombre,
                            "usuario": session['user'],
                            "fecha": ahora.strftime('%Y-%m-%d %H:%M:%S'),
                            "tamano_kb": round(len(contenido) / 1024, 2),
                            "lineas": lineas,
                            "error": False
                        })
                        
                    except Exception as e:
                        logger.error(f"Error procesando archivo {archivo.filename}: {e}")
            
            # Mantener solo los últimos 10
            historial = historial[:10]
            
            with open(HISTORIAL_TXT_FILE, 'w', encoding='utf-8') as f:
                json.dump(historial, f, indent=2, ensure_ascii=False)
            
            log_user_action("UPLOAD_TXT", f"Archivos: {len(archivos)}")
            return redirect(url_for('txt'))
        
        log_user_action("ACCESS_TXT")
        return render_template('txt.html', historial_txt=historial)
        
    except Exception as e:
        logger.error(f"Error en txt: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/subir_txt', methods=['POST'])
@login_required
def subir_txt():
    logs = []
    
    try:
        log_user_action("START_UPLOAD_TXT")
        
        # Conectar a Odoo
        try:
            db, uid, password, models = get_odoo_connection()
            logs.append("✅ Conectado a Odoo correctamente.")
        except Exception as e:
            logs.append(f"❌ Error de conexión a Odoo: {str(e)}")
            log_user_action("UPLOAD_TXT_ERROR", f"Conexión Odoo: {str(e)}")
            return jsonify({'logs': logs}), 500
        
        # Obtener archivos
        files = request.files.getlist('archivos_txt')
        if not files or not any(f.filename for f in files):
            logs.append("❌ No se recibieron archivos.")
            return jsonify({'logs': logs}), 400
        
        # Procesar cada archivo
        for file in files:
            if not file.filename:
                continue
            
            logs.append(f"📄 Procesando archivo: {file.filename}")
            
            try:
                # Leer contenido
                content_bytes = file.read()
                file.seek(0)
                
                # Guardar archivo para historial
                ruta_guardado = os.path.join(TXT_DIR, file.filename)
                with open(ruta_guardado, 'wb') as f_hist:
                    f_hist.write(content_bytes)
                
                # Validar archivo
                validation_results = validate_and_process_file(file, db, uid, password, models)
                
                hubo_error = not validation_results.get('is_valid', False) or validation_results.get('errors')
                
                # Contar líneas
                try:
                    lineas = content_bytes.decode('utf-8', errors='ignore').count('\n') + 1
                except:
                    lineas = 0
                
                # Actualizar historial
                ahora = datetime.now()
                historial = []
                if os.path.exists(HISTORIAL_TXT_FILE):
                    with open(HISTORIAL_TXT_FILE, 'r', encoding='utf-8') as f_hist:
                        historial = json.load(f_hist)
                
                historial.insert(0, {
                    "nombre": file.filename,
                    "usuario": session.get('user', 'desconocido'),
                    "fecha": ahora.strftime('%Y-%m-%d %H:%M:%S'),
                    "tamano_kb": round(len(content_bytes) / 1024, 2),
                    "lineas": lineas,
                    "error": hubo_error
                })
                
                historial = historial[:10]
                
                with open(HISTORIAL_TXT_FILE, 'w', encoding='utf-8') as f_hist:
                    json.dump(historial, f_hist, indent=2, ensure_ascii=False)
                
                # Mostrar resultados de validación
                if validation_results.get('format_detected'):
                    logs.append(f"✅ Formato detectado: {validation_results['format_detected']}")
                    logs.append(f"📊 Total registros: {validation_results.get('total_items', 0)}")
                
                if validation_results.get('errors'):
                    for error in validation_results['errors'][:5]:  # Mostrar solo los primeros 5 errores
                        logs.append(f"❌ Error: {error['message']}")
                
                # Mostrar resumen por ubicación
                for destino, location_data in validation_results.get('data_by_location', {}).items():
                    logs.append(f"📍 Ubicación: {location_data['original_name']}")
                    logs.append(f"   Total: {location_data['total_items']}")
                    logs.append(f"   Válidos: {len(location_data['valid_items'])}")
                    logs.append(f"   Errores: {len(location_data['invalid_items'])}")
                
                # Crear transferencias si es válido
                if validation_results['is_valid']:
                    logs.append("⏳ Creando transferencias en Odoo...")
                    
                    transfer_results = create_transfers(validation_results, db, uid, password, models)
                    
                    if transfer_results['success']:
                        for transfer in transfer_results['transfers_created']:
                            logs.append(f"✅ Transferencia {transfer['picking_id']} creada para {transfer['location']}")
                            logs.append(f"   Productos procesados: {transfer['items_processed']}")
                            if transfer['items_failed'] > 0:
                                logs.append(f"   Productos con error: {transfer['items_failed']}")
                    else:
                        logs.append("❌ Error al crear transferencias:")
                        for error in transfer_results['errors'][:3]:  # Mostrar solo los primeros 3 errores
                            logs.append(f"   {error.get('message', 'Error desconocido')}")
                else:
                    logs.append("❌ No se pueden crear transferencias debido a errores en la validación.")
                
            except Exception as e:
                logger.error(f"Error procesando {file.filename}: {e}")
                logs.append(f"❌ Error inesperado procesando {file.filename}: {str(e)}")
        
        log_user_action("UPLOAD_TXT_COMPLETE", f"Archivos procesados: {len([f for f in files if f.filename])}")
        return jsonify({'logs': logs})
        
    except Exception as e:
        logger.error(f"Error general en subir_txt: {e}")
        logs.append(f"❌ Error general: {str(e)}")
        return jsonify({'logs': logs}), 500

# ===== RUTAS DE ESTADO Y SALUD =====
@app.route('/status')
def status():
    return jsonify({
        "status": "ok",
        "message": "Panel Interactivo de Pedidos – Black Dog",
        "timestamp": datetime.now().isoformat(),
        "authenticated": 'user' in session,
        "user": session.get('user'),
        "version": "3.0.0-production-supercargado",
        "python_version": sys.version,
        "directories": {
            "zips": os.path.exists(ZIPS_DIR),
            "pedidos": os.path.exists(PEDIDOS_DIR),
            "txt": os.path.exists(TXT_DIR),
            "logs": os.path.exists(LOGS_DIR)
        }
    })

@app.route('/health')
def health():
    try:
        dirs_ok = all(os.path.exists(d) for d in [ZIPS_DIR, PEDIDOS_DIR, TXT_DIR, LOGS_DIR])
        config_ok = os.path.exists(CONFIG_FILE) or os.path.exists(CONFIG_DEFAULT)
        generar_ok = os.path.exists('generar.py')
        
        return jsonify({
            "status": "healthy" if dirs_ok and config_ok else "degraded",
            "directories": dirs_ok,
            "config": config_ok,
            "generar_script": generar_ok,
            "timestamp": datetime.now().isoformat(),
            "uptime": time.time() - start_time.timestamp() if 'start_time' in globals() else 0
        })
        
    except Exception as e:
        return jsonify({
            "status": "unhealthy",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }), 500

# ===== MANEJO DE ERRORES =====
@app.errorhandler(404)
def not_found(error):
    logger.warning(f"404 - Página no encontrada: {request.path} desde {request.remote_addr}")
    
    if request.is_json:
        return jsonify({"error": "Endpoint no encontrado"}), 404
    return render_template('login.html'), 404

@app.errorhandler(500)
def internal_error(error):
    logger.error(f"500 - Error interno: {error}")
    
    if request.is_json:
        return jsonify({"error": "Error interno del servidor"}), 500
    return render_template('login.html'), 500

@app.errorhandler(413)
def request_entity_too_large(error):
    logger.warning(f"413 - Archivo demasiado grande desde {request.remote_addr}")
    
    if request.is_json:
        return jsonify({"error": "Archivo demasiado grande (máximo 100MB)"}), 413
    return "Archivo demasiado grande", 413

@app.errorhandler(Exception)
def handle_exception(e):
    logger.error(f"Excepción no manejada: {e}")
    traceback.print_exc()
    
    if request.is_json:
        return jsonify({"error": "Error interno del servidor"}), 500
    return render_template('login.html'), 500

# ===== MANEJO DE SEÑALES =====
def signal_handler(sig, frame):
    logger.info('Recibida señal de shutdown, cerrando aplicación...')
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# ===== INICIALIZACIÓN =====
if __name__ == '__main__':
    start_time = datetime.now()
    print("🚀 Panel Interactivo de Pedidos – Black Dog")
    print("🔥 VERSIÓN SUPERCARGADA PARA PRODUCCIÓN")
    print(f"📁 Directorio de trabajo: {os.getcwd()}")
    print(f"📊 Directorios: {[ZIPS_DIR, PEDIDOS_DIR, TXT_DIR, LOGS_DIR]}")
    print(f"🐍 Python: {sys.version}")
    print("=" * 80)
    
    if not os.path.exists('generar.py'):
        print("⚠️  ADVERTENCIA: generar.py no encontrado")
    
    ensure_config_exists()
    logger.info("Servidor Flask production iniciado")
    
    # Para desarrollo local
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)
else:
    # Para Gunicorn
    start_time = datetime.now()
    ensure_config_exists()
    logger.info("Aplicación Flask cargada para Gunicorn - VERSIÓN SUPERCARGADA")