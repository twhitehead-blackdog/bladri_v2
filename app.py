from flask import Flask, render_template, request, redirect, url_for, session, send_file, jsonify
import os
import json
import subprocess
import zipfile
from datetime import datetime
import pandas as pd
import xmlrpc.client
import io
import traceback

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'bladri123')

CONFIG_FILE = 'config_ajustes.json'
CONFIG_DEFAULT = 'config_default.json'
HISTORIAL_FILE = 'historial_zips.json'
HISTORIAL_TXT_FILE = 'historial_txt.json'
ZIPS_DIR = 'ZIPS_GENERADOS'
PEDIDOS_DIR = 'pedidos_sugeridos'
TXT_DIR = 'txt_subidos'

os.makedirs(ZIPS_DIR, exist_ok=True)
os.makedirs(PEDIDOS_DIR, exist_ok=True)
os.makedirs(TXT_DIR, exist_ok=True)

users = {
    "admin": "admin", "seria_bd": "A1b2C3d4", "ricardo_bd": "E5f6G7h8",
    "angie_bd": "I9j0K1l2", "daniel_bd": "M3n4O5p6", "hernan_bd": "Q7r8S9t0",
    "luis_bd": "U1v2W3x4", "tristan_bd": "Y5z6A7b8", "yesi_bd": "C9d0E1f2",
    "roman_bd": "G3h4I5j6"
}

@app.route('/')
def home():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        if username in users and users[username] == password:
            session['user'] = username
            return redirect(url_for('index'))
        else:
            return render_template('login.html', error='Credenciales incorrectas.')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('login'))

@app.route('/index')
def index():
    if 'user' not in session:
        return redirect(url_for('login'))
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_DEFAULT, 'r', encoding='utf-8') as fsrc, open(CONFIG_FILE, 'w', encoding='utf-8') as fdst:
            fdst.write(fsrc.read())
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        config = json.load(f)
    return render_template('index.html', ajustes=config)

@app.route('/ajustes')
def ajustes():
    if 'user' not in session:
        return redirect(url_for('login'))
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_DEFAULT, 'r', encoding='utf-8') as fsrc, open(CONFIG_FILE, 'w', encoding='utf-8') as fdst:
            fdst.write(fsrc.read())
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        config = json.load(f)
    return render_template('ajustes.html', ajustes=config)

@app.route('/guardar_ajustes', methods=['POST'])
def guardar_ajustes():
    try:
        form = request.form
        config = {
            "meses_inventario": {
                "general": 1.0,
                "categorias": {}
            },
            "minimos_alimentos": {},
            "minimos_accesorios": {},
            "minimos_medicamentos": {},
            "minimos_para_pedir": {}
        }

        for key, val in form.items():
            if not val.strip():
                continue
            try:
                if key == 'meses_inventario_general':
                    config['meses_inventario']['general'] = float(val)
                elif key.startswith('meses_inventario_categorias['):
                    nombre = key[len('meses_inventario_categorias['):-1]
                    config['meses_inventario']['categorias'][nombre] = float(val)
                elif key.startswith('minimos_alimentos_'):
                    tipo = key.replace('minimos_alimentos_', '')
                    config['minimos_alimentos'][tipo] = int(val)
                elif key.startswith('minimos_medicamentos_'):
                    tipo = key.replace('minimos_medicamentos_', '')
                    config['minimos_medicamentos'][tipo] = int(val)
                elif key.startswith('minimos_accesorios_'):
                    partes = key.replace('minimos_accesorios_', '').split('_', 1)
                    if len(partes) == 2:
                        tipo, nombre = partes
                        nombre = nombre.replace('_', ' ')
                        config['minimos_accesorios'].setdefault(tipo, {})[nombre] = int(val)
                elif key.startswith('minimos_para_pedir_'):
                    partes = key.replace('minimos_para_pedir_', '').split('_', 1)
                    if len(partes) == 2:
                        tipo, nombre = partes
                        nombre = nombre.replace('_', ' ')
                        config['minimos_para_pedir'].setdefault(tipo, {})[nombre] = int(val)
            except Exception as e:
                print(f"Error en campo {key} con valor {val}: {e}")

        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

        return 'Guardado', 200
    except Exception as e:
        traceback.print_exc()
        return f'Error al guardar: {e}', 400

@app.route('/resetear_ajustes', methods=['POST'])
def resetear_ajustes():
    with open(CONFIG_DEFAULT, 'r', encoding='utf-8') as fsrc, open(CONFIG_FILE, 'w', encoding='utf-8') as fdst:
        fdst.write(fsrc.read())
    return 'Reset ok', 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
