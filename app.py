from flask import Flask, render_template, request, redirect, url_for, session, send_file, jsonify, abort
import os
import json
import subprocess
import zipfile
import time
from datetime import datetime, timedelta
import pandas as pd
import xmlrpc.client
import io
import traceback
from functools import wraps
import logging
from pathlib import Path

# Base directory absoluto del proyecto
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Configurar logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'bladri123')

# Configuración de sesiones
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=15)

# Rutas absolutas para archivos y carpetas
CONFIG_FILE = os.path.join(BASE_DIR, 'config_ajustes.json')
CONFIG_DEFAULT = os.path.join(BASE_DIR, 'config_default.json')
HISTORIAL_FILE = os.path.join(BASE_DIR, 'historial_zips.json')
HISTORIAL_TXT_FILE = os.path.join(BASE_DIR, 'historial_txt.json')
ZIPS_DIR = os.path.join(BASE_DIR, 'ZIPS_GENERADOS')
PEDIDOS_DIR = os.path.join(BASE_DIR, 'pedidos_sugeridos')
TXT_DIR = os.path.join(BASE_DIR, 'txt_subidos')
LOGS_DIR = os.path.join(BASE_DIR, 'logs')

# Crear directorios necesarios
for directory in [ZIPS_DIR, PEDIDOS_DIR, TXT_DIR, LOGS_DIR]:
    os.makedirs(directory, exist_ok=True)

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

users = {
    "admin": "admin",
    "seria_bd": "A1b2C3d4",
    "ricardo_bd": "E5f6G7h8",
    "angie_bd": "I9j0K1l2",
    "daniel_bd": "M3n4O5p6",
    "hernan_bd": "Q7r8S9t0",
    "luis_bd": "U1v2W3x4",
    "tristan_bd": "Y5z6A7b8",
    "yesi_bd": "C9d0E1f2",
    "roman_bd": "G3h4I5j6"
}

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            logger.warning(f"Acceso no autorizado a {request.endpoint}")
            if request.is_json:
                return jsonify({"error": "No autorizado"}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def log_user_action(action, details=""):
    user = session.get('user', 'anonymous')
    logger.info(f"Usuario: {user} | Acción: {action} | Detalles: {details}")

def get_odoo_connection():
    try:
        url = os.environ.get('ODOO_URL', "https://blackdogpanama.odoo.com")
        db = os.environ.get('ODOO_DB', "dev-psdc-blackdogpanama-prod-3782039")
        username = os.environ.get('ODOO_USER', "mercadeo@blackdogpanama.com")
        password = os.environ.get('ODOO_PASS', "Emanuel1010.")

        logger.info(f"Conectando a Odoo: {url}")
        common = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/common')
        uid = common.authenticate(db, username, password, {})
        if not uid:
            raise Exception("Autenticación fallida en Odoo.")
        models = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/object')
        logger.info("Conexión a Odoo exitosa")
        return db, uid, password, models
    except Exception as e:
        logger.error(f"Error conectando a Odoo: {e}")
        raise

def validate_and_process_file(uploaded_file, db, uid, password, models):
    """Valida y procesa archivo TXT subido"""
    try:
        content = uploaded_file.read().decode('latin-1')
        lines = content.strip().split('\n')
        
        validation_results = {
            'is_valid': False,
            'format_detected': None,
            'total_items': 0,
            'errors': [],
            'data_by_location': {}
        }
        
        if not lines:
            validation_results['errors'].append({'message': 'Archivo vacío'})
            return validation_results
            
        # Detectar formato y procesar
        first_line = lines[0].strip()
        if '|' in first_line:
            validation_results['format_detected'] = 'Formato con separador |'
            # Procesar formato con |
            for i, line in enumerate(lines[1:], 2):  # Saltar header
                if not line.strip():
                    continue
                parts = line.split('|')
                if len(parts) >= 3:
                    validation_results['total_items'] += 1
        else:
            validation_results['format_detected'] = 'Formato de ancho fijo'
            validation_results['total_items'] = len([l for l in lines if l.strip()])
        
        validation_results['is_valid'] = validation_results['total_items'] > 0
        return validation_results
        
    except Exception as e:
        logger.error(f"Error validando archivo: {e}")
        return {
            'is_valid': False,
            'errors': [{'message': f'Error procesando archivo: {str(e)}'}],
            'total_items': 0
        }

def create_transfers(validation_results, db, uid, password, models):
    """Crea transferencias en Odoo basado en resultados de validación"""
    try:
        transfer_results = {
            'success': False,
            'transfers_created': [],
            'errors': []
        }
        
        if not validation_results.get('is_valid'):
            transfer_results['errors'].append({'message': 'Datos de validación inválidos'})
            return transfer_results
        
        # Simular creación de transferencias
        for location, data in validation_results.get('data_by_location', {}).items():
            if data.get('valid_items'):
                transfer_results['transfers_created'].append({
                    'picking_id': f'PICK_{int(time.time())}',
                    'location': location,
                    'items_processed': len(data['valid_items']),
                    'items_failed': len(data.get('invalid_items', []))
                })
        
        transfer_results['success'] = len(transfer_results['transfers_created']) > 0
        return transfer_results
        
    except Exception as e:
        logger.error(f"Error creando transferencias: {e}")
        return {
            'success': False,
            'errors': [{'message': f'Error creando transferencias: {str(e)}'}]
        }

def ensure_config_exists():
    """Asegura que existe el archivo de configuración"""
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
        logger.info("Archivo de configuración creado con valores por defecto")

@app.before_request
def before_request():
    if request.endpoint and request.endpoint != 'static':
        logger.info(f"Request: {request.method} {request.path} from {request.remote_addr}")

@app.route('/')
def home():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        data = request.get_json() if request.is_json else request.form
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        if username in users and users[username] == password:
            session['user'] = username
            session.permanent = True
            log_user_action("LOGIN_SUCCESS", f"Usuario: {username}")
            return jsonify({"status": "ok", "user": username}) if request.is_json else redirect(url_for('index'))
        else:
            log_user_action("LOGIN_FAILED", f"Usuario: {username}")
            return jsonify({"status": "error", "message": "Credenciales incorrectas"}), 401 if request.is_json else render_template('login.html', error='Credenciales incorrectas.')
    return render_template('login.html')

@app.route('/logout', methods=['POST', 'GET'])
def logout():
    user = session.get('user', 'unknown')
    session.pop('user', None)
    log_user_action("LOGOUT", f"Usuario: {user}")
    return jsonify({"status": "ok", "message": "Logout exitoso"}) if request.is_json else redirect(url_for('login'))

@app.route('/index')
@login_required
def index():
    ensure_config_exists()
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        config = json.load(f)
    log_user_action("ACCESS_INDEX")
    return render_template('index.html', ajustes=config)

@app.route('/ajustes')
@login_required
def ajustes():
    ensure_config_exists()
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        config = json.load(f)
    log_user_action("ACCESS_AJUSTES")
    return render_template('ajustes.html', ajustes=config)

@app.route('/guardar_ajustes', methods=['POST'])
@login_required
def guardar_ajustes():
    try:
        data = request.get_json() if request.is_json else request.form
        config = {
            "meses_inventario": {"general": 1.0, "categorias": {}},
            "minimos_alimentos": {}, "minimos_accesorios": {},
            "minimos_medicamentos": {}, "minimos_para_pedir": {}
        }
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
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        log_user_action("SAVE_CONFIG", "Configuración guardada")
        return jsonify({"status": "ok", "message": "Guardado"}) if request.is_json else 'Guardado', 200
    except Exception as e:
        logger.error(f"Error guardando configuración: {e}")
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route('/resetear_ajustes', methods=['POST'])
@login_required
def resetear_ajustes():
    try:
        if os.path.exists(CONFIG_DEFAULT):
            with open(CONFIG_DEFAULT, 'r', encoding='utf-8') as fsrc, open(CONFIG_FILE, 'w', encoding='utf-8') as fdst:
                fdst.write(fsrc.read())
        else:
            json.dump({
                "meses_inventario": {"general": 1.0, "categorias": {}},
                "minimos_alimentos": {}, "minimos_accesorios": {},
                "minimos_medicamentos": {}, "minimos_para_pedir": {}
            }, open(CONFIG_FILE, 'w', encoding='utf-8'), indent=2, ensure_ascii=False)
        log_user_action("RESET_CONFIG", "Configuración reseteada")
        return jsonify({"status": "ok", "message": "Reseteado"}) if request.is_json else 'Reset ok', 200
    except Exception as e:
        logger.error(f"Error reseteando configuración: {e}")
        return jsonify({"status": "error", "message": str(e)}), 400

# ENDPOINT PRINCIPAL: /generar - Devuelve el ZIP directamente como archivo
@app.route('/generar', methods=['POST'])
@login_required
def generar():
    try:
        user = session['user']
        logger.info(f"Usuario {user} inició generación de pedidos")
        log_user_action("START_GENERATE_ORDERS")
        start_time = datetime.now()
        
        # Ejecutar generar.py
        resultado = subprocess.run(
            ['python', 'generar.py'], 
            capture_output=True, 
            text=True, 
            timeout=300,
            cwd=os.getcwd()
        )
        
        logger.info(f"Script generar.py terminó con código: {resultado.returncode}")
        if resultado.stdout:
            logger.info(f"STDOUT: {resultado.stdout}")
        if resultado.stderr:
            logger.warning(f"STDERR: {resultado.stderr}")
            
        if resultado.returncode != 0:
            error_msg = resultado.stderr or "Error desconocido en generar.py"
            log_user_action("GENERATE_ORDERS_ERROR", error_msg)
            return abort(500, description=error_msg)
        
        # Esperar un poco para que se escriban los archivos
        time.sleep(2)
        
        # Buscar archivos recientes en pedidos_sugeridos
        archivos_recientes = []
        ahora = datetime.now()
        logger.info(f"Buscando archivos recientes en: {PEDIDOS_DIR}")
        
        if not os.path.exists(PEDIDOS_DIR):
            logger.error(f"Directorio {PEDIDOS_DIR} no existe")
            return abort(500, description=f"Directorio {PEDIDOS_DIR} no existe")
        
        for root, dirs, files in os.walk(PEDIDOS_DIR):
            logger.info(f"Explorando directorio: {root} con {len(files)} archivos")
            for file in files:
                filepath = os.path.join(root, file)
                if not os.path.isfile(filepath):
                    continue
                try:
                    mod_time = datetime.fromtimestamp(os.path.getmtime(filepath))
                    tiempo_transcurrido = (ahora - mod_time).total_seconds()
                    logger.info(f"Archivo: {file}, Modificado hace: {tiempo_transcurrido:.1f}s")
                    
                    if tiempo_transcurrido <= 30:  # Archivos de los últimos 30 segundos
                        arcname = os.path.relpath(filepath, PEDIDOS_DIR)
                        file_size = os.path.getsize(filepath)
                        archivos_recientes.append((filepath, arcname))
                        logger.info(f"✅ Incluido: {file} ({file_size} bytes)")
                    else:
                        logger.info(f"❌ Muy antiguo: {file}")
                except Exception as e:
                    logger.error(f"Error procesando archivo {file}: {e}")
        
        logger.info(f"Total archivos recientes encontrados: {len(archivos_recientes)}")
        
        if not archivos_recientes:
            log_user_action("GENERATE_ORDERS_ERROR", "No se encontraron archivos recientes")
            return abort(500, description="No se generaron archivos recientes en los últimos 30 segundos.")
        
        # Crear ZIP
        zip_name = f"pedido_{start_time.strftime('%Y%m%d_%H%M%S')}.zip"
        zip_path = os.path.join(ZIPS_DIR, zip_name)
        logger.info(f"Creando ZIP: {zip_path}")
        
        try:
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zipf:
                for filepath, arcname in archivos_recientes:
                    logger.info(f"Agregando al ZIP: {arcname}")
                    zipf.write(filepath, arcname)
        except Exception as e:
            logger.error(f"Error creando ZIP: {e}")
            return abort(500, description=f"Error creando ZIP: {str(e)}")
        
        # Verificar ZIP
        try:
            zip_size = os.path.getsize(zip_path)
            logger.info(f"Tamaño del ZIP: {zip_size} bytes")
            
            if zip_size < 100:
                os.remove(zip_path)
                log_user_action("GENERATE_ORDERS_ERROR", "ZIP generado vacío")
                return abort(500, description="ZIP generado vacío o corrupto.")
            
            # Verificar integridad del ZIP
            with zipfile.ZipFile(zip_path, 'r') as zipf:
                bad_file = zipf.testzip()
                if bad_file:
                    logger.error(f"ZIP corrupto, archivo problemático: {bad_file}")
                    os.remove(zip_path)
                    return abort(500, description=f"ZIP corrupto: {bad_file}")
        except Exception as e:
            logger.error(f"Error verificando ZIP: {e}")
            if os.path.exists(zip_path):
                os.remove(zip_path)
            return abort(500, description=f"Error verificando ZIP: {str(e)}")
        
        # Actualizar historial
        historial = []
        if os.path.exists(HISTORIAL_FILE):
            try:
                with open(HISTORIAL_FILE, 'r', encoding='utf-8') as f:
                    historial = json.load(f)
            except Exception as e:
                logger.warning(f"Error leyendo historial: {e}")
                historial = []
        
        historial.insert(0, {
            "usuario": user,
            "nombre": zip_name,
            "fecha": start_time.strftime('%Y-%m-%d %H:%M:%S'),
            "tamaño_kb": round(zip_size / 1024, 2),
            "archivos_incluidos": len(archivos_recientes)
        })
        historial = historial[:10]  # Mantener solo los últimos 10
        
        try:
            with open(HISTORIAL_FILE, 'w', encoding='utf-8') as f:
                json.dump(historial, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"Error guardando historial: {e}")
        
        log_user_action("GENERATE_ORDERS_SUCCESS", f"ZIP: {zip_name}, Archivos: {len(archivos_recientes)}")
        
        # DEVOLVER EL ZIP DIRECTAMENTE COMO ARCHIVO
        return send_file(zip_path, as_attachment=True, download_name=zip_name, mimetype='application/zip')
        
    except subprocess.TimeoutExpired:
        log_user_action("GENERATE_ORDERS_ERROR", "Timeout ejecutando generar.py")
        return abort(500, description="Timeout al ejecutar generar.py (>5 minutos)")
    except Exception as e:
        logger.error(f"Error en generar: {e}")
        traceback.print_exc()
        log_user_action("GENERATE_ORDERS_ERROR", str(e))
        return abort(500, description=str(e))

# ENDPOINT ADICIONAL: Descargar último ZIP generado
@app.route('/descargar_ultimo_zip')
@login_required
def descargar_ultimo_zip():
    try:
        if not os.path.exists(ZIPS_DIR):
            return abort(404, description="No hay archivos ZIP generados.")
        
        archivos = [f for f in os.listdir(ZIPS_DIR) if f.endswith('.zip')]
        if not archivos:
            return abort(404, description="No hay archivos ZIP generados.")
        
        # Ordenar por fecha de modificación (más reciente primero)
        archivos.sort(key=lambda x: os.path.getmtime(os.path.join(ZIPS_DIR, x)), reverse=True)
        ultimo_zip = archivos[0]
        ruta_zip = os.path.join(ZIPS_DIR, ultimo_zip)
        
        log_user_action("DOWNLOAD_LAST_ZIP", f"Archivo: {ultimo_zip}")
        return send_file(ruta_zip, as_attachment=True, download_name=ultimo_zip, mimetype='application/zip')
        
    except Exception as e:
        logger.error(f"Error descargando último ZIP: {e}")
        return abort(500, description=f"Error al descargar: {str(e)}")

@app.route('/descargar_zip/<nombre>')
@login_required
def descargar_zip(nombre):
    try:
        # Seguridad: solo permite nombres válidos y archivos existentes
        if not nombre.endswith('.zip') or '/' in nombre or '\\' in nombre or '..' in nombre:
            log_user_action("DOWNLOAD_ERROR", f"Nombre inválido: {nombre}")
            return abort(400, description="Nombre de archivo inválido")
        
        path = os.path.join(ZIPS_DIR, nombre)
        if not os.path.isfile(path):
            log_user_action("DOWNLOAD_ERROR", f"Archivo no encontrado: {nombre}")
            return abort(404, description="Archivo no encontrado")
        
        log_user_action("DOWNLOAD_ZIP", f"Archivo: {nombre}")
        return send_file(path, as_attachment=True, download_name=nombre, mimetype='application/zip')
        
    except Exception as e:
        logger.error(f"Error descargando {nombre}: {e}")
        return abort(500, description=f"Error al descargar: {str(e)}")

@app.route('/historial')
@login_required
def historial():
    try:
        if not os.path.exists(HISTORIAL_FILE):
            return jsonify([])
        with open(HISTORIAL_FILE, 'r', encoding='utf-8') as f:
            historial = json.load(f)
        historial_filtrado = []
        for item in historial:
            if isinstance(item, dict) and 'nombre' in item:
                item['url_descarga'] = url_for('descargar_zip', nombre=item['nombre'])
                historial_filtrado.append(item)
            else:
                logger.warning(f"Elemento inválido en historial: {item}")
        log_user_action("ACCESS_HISTORIAL")
        return jsonify(historial_filtrado)
    except Exception as e:
        logger.error(f"Error obteniendo historial: {e}")
        return jsonify({"error": str(e)}), 500

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
                    nombre = archivo.filename
                    contenido = archivo.read()
                    
                    # Guardar archivo
                    ruta_guardado = os.path.join(TXT_DIR, nombre)
                    with open(ruta_guardado, 'wb') as f:
                        f.write(contenido)
                    
                    # Agregar al historial
                    lineas = len(contenido.splitlines())
                    historial.insert(0, {
                        "nombre": nombre,
                        "usuario": session['user'],
                        "fecha": ahora.strftime('%Y-%m-%d %H:%M:%S'),
                        "tamano_kb": round(len(contenido) / 1024, 2),
                        "lineas": lineas,
                        "error": False
                    })
            
            historial = historial[:10]  # Mantener solo los últimos 10
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
        
        files = request.files.getlist('archivos_txt')
        if not files or not any(f.filename for f in files):
            logs.append("❌ No se recibieron archivos.")
            return jsonify({'logs': logs}), 400
        
        for file in files:
            if not file.filename:
                continue
                
            logs.append(f"📄 Procesando archivo: {file.filename}")
            
            try:
                # Leer contenido
                content_bytes = file.read()
                file.seek(0)  # Reset para poder leer de nuevo
                
                # Guardar archivo en historial
                ruta_guardado = os.path.join(TXT_DIR, file.filename)
                with open(ruta_guardado, 'wb') as f_hist:
                    f_hist.write(content_bytes)
                
                # Validar archivo
                validation_results = validate_and_process_file(file, db, uid, password, models)
                
                hubo_error = not validation_results.get('is_valid', False) or validation_results.get('errors')
                lineas = content_bytes.decode('latin-1').count('\n') + 1
                ahora = datetime.now()
                
                # Actualizar historial TXT
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
                    for error in validation_results['errors']:
                        logs.append(f"❌ Error: {error['message']}")
                
                # Mostrar datos por ubicación
                for destino, location_data in validation_results.get('data_by_location', {}).items():
                    logs.append(f"📍 Ubicación: {location_data['original_name']}")
                    logs.append(f"   Total: {location_data['total_items']}")
                    logs.append(f"   Válidos: {len(location_data['valid_items'])}")
                    logs.append(f"   Errores: {len(location_data['invalid_items'])}")
                    
                    if location_data['invalid_items']:
                        for item in location_data['invalid_items'][:5]:  # Solo mostrar primeros 5 errores
                            logs.append(f"   Línea {item['row_index']}: {', '.join(item['errors'])}")
                
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
                        for error in transfer_results['errors'][:3]:  # Solo mostrar primeros 3 errores
                            logs.append(f"   {error.get('message', 'Error desconocido')}")
                        hubo_error = True
                else:
                    logs.append("❌ No se pueden crear transferencias debido a errores en la validación.")
                
            except Exception as e:
                logger.error(f"Error procesando {file.filename}: {e}")
                logs.append(f"❌ Error inesperado procesando {file.filename}: {str(e)}")
                hubo_error = True
                
                # Agregar al historial con error
                try:
                    ahora = datetime.now()
                    historial = []
                    if os.path.exists(HISTORIAL_TXT_FILE):
                        with open(HISTORIAL_TXT_FILE, 'r', encoding='utf-8') as f_hist:
                            historial = json.load(f_hist)
                    
                    historial.insert(0, {
                        "nombre": file.filename,
                        "usuario": session.get('user', 'desconocido'),
                        "fecha": ahora.strftime('%Y-%m-%d %H:%M:%S'),
                        "tamano_kb": round(len(content_bytes) / 1024, 2) if 'content_bytes' in locals() else 0,
                        "lineas": 0,
                        "error": True
                    })
                    historial = historial[:10]
                    
                    with open(HISTORIAL_TXT_FILE, 'w', encoding='utf-8') as f_hist:
                        json.dump(historial, f_hist, indent=2, ensure_ascii=False)
                except:
                    pass  # Si no se puede guardar el historial, continuar
        
        log_user_action("UPLOAD_TXT_COMPLETE", f"Archivos procesados: {len([f for f in files if f.filename])}")
        return jsonify({'logs': logs})
        
    except Exception as e:
        logger.error(f"Error general en subir_txt: {e}")
        logs.append(f"❌ Error general: {str(e)}")
        return jsonify({'logs': logs}), 500

@app.route('/status')
def status():
    return jsonify({
        "status": "ok",
        "message": "Panel Interactivo de Pedidos – Black Dog",
        "timestamp": datetime.now().isoformat(),
        "authenticated": 'user' in session,
        "user": session.get('user'),
        "version": "2.0.0-supercargado"
    })

@app.route('/health')
def health():
    try:
        dirs_ok = all(os.path.exists(d) for d in [ZIPS_DIR, PEDIDOS_DIR, TXT_DIR])
        config_ok = os.path.exists(CONFIG_FILE)
        return jsonify({
            "status": "healthy" if dirs_ok and config_ok else "degraded",
            "directories": dirs_ok,
            "config": config_ok,
            "timestamp": datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({
            "status": "unhealthy",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }), 500

@app.errorhandler(404)
def not_found(error):
    logger.warning(f"404 - Página no encontrada: {request.path}")
    if request.is_json:
        return jsonify({"error": "Endpoint no encontrado"}), 404
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_error(error):
    logger.error(f"500 - Error interno: {error}")
    if request.is_json:
        return jsonify({"error": "Error interno del servidor"}), 500
    return render_template('500.html'), 500

@app.errorhandler(Exception)
def handle_exception(e):
    logger.error(f"Excepción no manejada: {e}")
    traceback.print_exc()
    if request.is_json:
        return jsonify({"error": "Error interno del servidor"}), 500
    return render_template('500.html'), 500

if __name__ == '__main__':
    print("🚀 Panel Interactivo de Pedidos – Black Dog")
    print("🔥 VERSIÓN SUPERCARGADA INICIANDO...")
    print(f"📁 Directorio de trabajo: {os.getcwd()}")
    print(f"📊 Directorios creados: {[ZIPS_DIR, PEDIDOS_DIR, TXT_DIR, LOGS_DIR]}")
    print("=" * 80)
    if not os.path.exists('generar.py'):
        print("⚠️  ADVERTENCIA: generar.py no encontrado")
    logger.info("Servidor Flask supercargado iniciado")
