# -*- coding: utf-8 -*-
import os
import json
import xmlrpc.client
import pandas as pd
import time
import pickle
import math
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')


# ---------------------------------------------
# CONFIGURACIÓN GENERAL Y CONSTANTES
# ---------------------------------------------

EXCLUIR_PALABRAS = ["urna", "ropa mascota", "(copia)", "halloween", "navidad"]

RUTAS = {
    "R1": ["brisas del golf", "brisas norte", "villa zaita", "condado del rey"],
    "R2": ["albrook fields", "bella vista", "plaza emporio", "ocean mall", "santa maria"],
    "R3": ["calle 50", "coco del mar", "versalles", "costa verde"]
}

TIENDAS_REGULARES = {"ocean mall", "calle 50", "albrook fields", "brisas del golf", "santa maria", "bella vista", "costa verde", "villa zaita", "condado del rey", "brisas norte", "versalles", "coco del mar", "david"}
TIENDAS_CHICAS = {"plaza emporio"}

# TIENDAS CON CLÍNICA (NUEVO)
TIENDAS_CON_CLINICA = {
    "plaza emporio", "ocean mall", "bella vista", "albrook fields", 
    "brisas del golf", "calle 50", "santa maria", "villa zaita"
}

COLUMNS_OUT = ["Código", "Referencia Interna", "Descripción", "Cantidad", "Categoría"]

CATEGORIAS_EXCLUIR = ["insumos", "otros"]

# CONFIGURACIÓN EMBEBIDA
CONFIG_DEFAULT = {
    "meses_inventario": {
        "general": 1,
        "categorias": {
            "alimento": {"regular": 1, "chica": 1},
            "accesorio": {"regular": 1, "chica": 1},
            "medicamento": {"regular": 2, "chica": 1}
        }
    },
    "minimos_alimentos": {"regular": 1, "chica": 1},
    "minimos_accesorios": {
        "regular": {"default": 3, "correa": 2, "collar": 2, "juguete": 1},
        "chica": {"default": 2, "correa": 1, "collar": 1, "juguete": 1}
    },
    "minimos_medicamentos": {"regular": 1, "chica": 1},
    "opciones_productos_grandes": {"excluir_de_tiendas_chicas": True},
    "reglas_stock_cero": {
        "aplicar_minimo_sin_ventas": True,
        "considerar_ventas_minimas": True,
        "umbral_ventas_minimas": 1
    }
}

# ---------------------------------------------
# UTILIDADES GENERALES
# ---------------------------------------------

def limpiar_nombre_producto(nombre):
    if not nombre:
        return ""
    nombre = nombre.replace("(copia)", "").strip()
    while "  " in nombre:
        nombre = nombre.replace("  ", " ")
    return nombre

def obtener_ruta(tienda):
    tienda = tienda.lower()
    for ruta, tiendas in RUTAS.items():
        if tienda in [t.lower() for t in tiendas]:
            return ruta
    return "SIN_RUTA"

def crear_item_producto(product_info, cantidad, categoria_nombre):
    return {
        "Código": product_info.get("barcode", ""),
        "Referencia Interna": product_info.get("default_code", ""),
        "Descripción": product_info.get("nombre_correcto", ""),
        "Cantidad": cantidad,
        "Categoría": categoria_nombre
    }

def determinar_tipo_producto(categoria_nombre, nombre_producto):
    categoria = str(categoria_nombre).lower()
    nombre = str(nombre_producto).lower()
    
    # VERIFICAR PALABRAS DE EXCLUSIÓN PRIMERO
    if any(palabra in nombre or palabra in categoria for palabra in EXCLUIR_PALABRAS):
        return "otros"
    
    if "insumo" in categoria or "gasto" in categoria:
        return "insumos"
    if "alimento" in categoria or "medicado" in categoria or "treat" in categoria:
        return "alimentos"
    elif "accesorio" in categoria:
        return "accesorios"
    elif "medicamento" in categoria or "vacuna" in categoria or "vacunas" in categoria:
        return "medicamentos"
    return "otros"

def es_producto_halloween_o_navidad(product_info):
    """FUNCIÓN DEFINITIVA: Detecta productos de Halloween y Navidad por MÚLTIPLES CRITERIOS"""
    
    # 1. Verificar campos específicos de Odoo
    if product_info.get("x_studio_halloween", False):
        return True, "Campo x_studio_halloween = True"
    
    if product_info.get("x_studio_navidad", False):
        return True, "Campo x_studio_navidad = True"
    
    # 2. Verificar en el nombre del producto
    nombre = str(product_info.get("nombre_correcto", "")).lower()
    if "halloween" in nombre:
        return True, "Palabra 'halloween' en nombre"
    if "navidad" in nombre:
        return True, "Palabra 'navidad' en nombre"
    
    # 3. Verificar en la categoría
    categoria = ""
    if product_info.get("categ_id") and len(product_info["categ_id"]) > 1:
        categoria = str(product_info["categ_id"][1]).lower()
    
    if "halloween" in categoria:
        return True, "Palabra 'halloween' en categoría"
    if "navidad" in categoria:
        return True, "Palabra 'navidad' en categoría"
    
    # 4. Verificar en template
    template = product_info.get("product_template", {})
    if template.get("x_studio_halloween", False):
        return True, "Campo x_studio_halloween en template = True"
    if template.get("x_studio_navidad", False):
        return True, "Campo x_studio_navidad en template = True"
    
    template_name = str(template.get("name", "")).lower()
    if "halloween" in template_name:
        return True, "Palabra 'halloween' en nombre template"
    if "navidad" in template_name:
        return True, "Palabra 'navidad' en nombre template"
    
    return False, ""

def es_producto_solo_clinica(product_info):
    """NUEVA FUNCIÓN: Verifica si un producto es solo para clínica"""
    
    # Verificar en la variante del producto
    if product_info.get("x_studio_solo_clinica", False):
        return True, "Campo x_studio_solo_clinica = True en variante"
    
    # Verificar en el template del producto
    template = product_info.get("product_template", {})
    if template.get("x_studio_solo_clinica", False):
        return True, "Campo x_studio_solo_clinica = True en template"
    
    return False, ""

def tienda_tiene_clinica(tienda):
    """NUEVA FUNCIÓN: Verifica si una tienda tiene clínica"""
    return tienda.lower() in TIENDAS_CON_CLINICA

def sugerido_top2_6meses(linea):
    ventas = [
        linea.get('qty_month0', 0),
        linea.get('qty_month1', 0),
        linea.get('qty_month2', 0),
        linea.get('qty_month3', 0),
        linea.get('qty_month4', 0),
        linea.get('qty_month5', 0),
    ]
    ventas = [float(v) for v in ventas if v is not None]
    if not ventas:
        return 0
    top2 = sorted(ventas, reverse=True)[:2]
    return int(round(sum(top2) / 2))

def obtener_unidad_reposicion(product_info):
    try:
        unidad_variante = int(product_info.get("x_studio_unidad_de_reposicin", 0))
        if unidad_variante > 0:
            return unidad_variante
    except Exception:
        pass
    plantilla = product_info.get("product_template", {})
    try:
        unidad_plantilla = int(plantilla.get("x_studio_unidad_de_reposicin", 0))
        if unidad_plantilla > 0:
            return unidad_plantilla
    except Exception:
        pass
    return 1

# ---------------------------------------------
# FUNCION PARA NORMALIZAR CATEGORIAS
# ---------------------------------------------

def normalizar_categoria(categoria):
    if not categoria:
        return ""
    categoria = categoria.lower().strip()
    categoria = ''.join(c for c in categoria if c.isalnum() or c.isspace())
    return categoria

# ---------------------------------------------
# CARGA DE CONFIGURACIÓN
# ---------------------------------------------

def cargar_configuracion(path="config_ajustes.json"):
    """Carga configuración desde JSON o usa configuración por defecto"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            config = json.load(f)
        print(f" Configuración cargada desde {path}")
        return config
    except Exception as e:
        print(f" No se pudo cargar {path}, usando configuración por defecto: {e}")
        return CONFIG_DEFAULT

# ---------------------------------------------
# FUNCIONES PARA USAR CONFIGURACIÓN
# ---------------------------------------------

def obtener_meses_inventario_por_categoria_y_tienda(categoria_nombre, tipo_tienda, config):
    categoria = categoria_nombre.lower() if categoria_nombre else ""
    meses_generales = config.get("meses_inventario", {}).get("general", 1)
    categorias_config = config.get("meses_inventario", {}).get("categorias", {})

    # Construir clave combinada para buscar en JSON
    clave_combinada = f"{categoria}_{tipo_tienda}".strip()

    # Buscar coincidencia exacta (ejemplo: "bowls y feeders_regular")
    if clave_combinada in categorias_config:
        return categorias_config[clave_combinada]

    # Buscar coincidencia solo por categoría (ejemplo: "bowls y feeders")
    if categoria in categorias_config:
        return categorias_config[categoria]

    # Usar default si existe
    if "default" in categorias_config:
        return categorias_config["default"]

    return meses_generales

def obtener_minimo_categoria(subcategoria, tipo_tienda, config):
    minimos_accesorios = config.get("minimos_accesorios", {})
    if not subcategoria:
        return minimos_accesorios.get(tipo_tienda, {}).get("default", 3)
    subcategoria = subcategoria.lower()
    minimos_tipo = minimos_accesorios.get(tipo_tienda, {})
    for clave, valor in minimos_tipo.items():
        if clave.lower() in subcategoria:
            return valor
    return minimos_tipo.get("default", 3)

def obtener_minimo_alimento(tipo_tienda, config):
    minimos_alimentos = config.get("minimos_alimentos", {})
    return minimos_alimentos.get(tipo_tienda, 1)

def obtener_minimo_medicamento(tipo_tienda, config):
    """Obtiene mínimo específico para medicamentos"""
    minimos_medicamentos = config.get("minimos_medicamentos", {})
    return minimos_medicamentos.get(tipo_tienda, 1)

def obtener_minimo_para_pedir(subcategoria, tipo_tienda, config):
    minimos_pedir = config.get("minimos_para_pedir", {})
    if not subcategoria:
        return minimos_pedir.get(tipo_tienda, {}).get("default", 2)
    subcategoria = subcategoria.lower()
    minimos_tipo = minimos_pedir.get(tipo_tienda, {})
    for clave, valor in minimos_tipo.items():
        if clave.lower() in subcategoria:
            return valor
    return minimos_tipo.get("default", 2)

def debe_excluir_producto_grande(product_info, tienda, config):
    """Determina si un producto grande debe excluirse de tienda chica"""
    opciones = config.get("opciones_productos_grandes", {})
    
    if not opciones.get("excluir_de_tiendas_chicas", True):
        return False
    
    es_producto_grande = product_info.get("product_template", {}).get("x_studio_producto_grande", False)
    if not es_producto_grande:
        return False
    
    tipo_tienda = "regular"
    if tienda.lower() in TIENDAS_CHICAS:
        tipo_tienda = "chica"
    
    return tipo_tienda == "chica"

def aplicar_reglas_stock_cero_mejoradas(promedio_top2, config):
    """Aplica reglas mejoradas para productos con stock cero"""
    reglas = config.get("reglas_stock_cero", {})
    
    if not reglas.get("aplicar_minimo_sin_ventas", True):
        return False
    
    if reglas.get("considerar_ventas_minimas", True):
        umbral = reglas.get("umbral_ventas_minimas", 1)
        return promedio_top2 <= umbral
    
    return promedio_top2 == 0

# ---------------------------------------------
# CONEXIÓN Y CACHÉ ODOO
# ---------------------------------------------

class OdooConnection:
    def __init__(self):
        self.url = "https://blackdogpanama.odoo.com"
        self.db = "dev-psdc-blackdogpanama-prod-3782039"
        self.username = "mercadeo@blackdogpanama.com"
        self.password = "Emanuel1010."
        self.uid = None
        self.models = None
        self.connect()

    def connect(self):
        print(" Conectando a Odoo...")
        if not self.url.startswith("http://") and not self.url.startswith("https://"):
            raise ValueError("La URL de Odoo debe empezar con http:// o https://")
        try:
            common = xmlrpc.client.ServerProxy(f'{self.url}/xmlrpc/2/common')
            self.uid = common.authenticate(self.db, self.username, self.password, {})
            self.models = xmlrpc.client.ServerProxy(f'{self.url}/xmlrpc/2/object')
            print(" Conexión exitosa con Odoo")
        except Exception as e:
            print(f" Error conectando a Odoo: {e}")
            raise

    def execute(self, model, method, *args, **kwargs):
        try:
            return self.models.execute_kw(
                self.db, self.uid, self.password,
                model, method, *args, **kwargs
            )
        except Exception as e:
            print(f" Error ejecutando {method} en {model}: {e}")
            raise

def cargar_datos_reposicion():
    odoo = OdooConnection()
    print("\n Buscando órdenes de reposición en estado borrador...")
    orders = odoo.execute(
        'estimated.replenishment.order',
        'search_read',
        [[('state', '=', 'draft')]],
        {'fields': ['id', 'shop_pos_ids']}
    )
    print(f"└── Encontradas {len(orders)} órdenes en estado borrador\n")

    all_lines = []
    all_product_ids = set()

    for order in orders:
        order_id = order['id']
        try:
            lines = odoo.execute(
                'estimated.replenishment.order.line',
                'search_read',
                [[('order_id', '=', order_id)]],
                {
                    'fields': [
                        'product_id',
                        'qty_to_order',
                        'qty_to_order_recommend',
                        'qty_in_wh',
                        'shop_pos_id',
                        'total_avg',
                        'uom_po_id',
                        'qty_to_hand',
                        'qty_month0', 'qty_month1', 'qty_month2',
                        'qty_month3', 'qty_month4', 'qty_month5'
                    ]
                }
            )
            for line in lines:
                if line.get('product_id') and line.get('shop_pos_id'):
                    all_lines.append(line)
                    all_product_ids.add(line['product_id'][0])
        except Exception as e:
            print(f"Error procesando orden {order_id}: {e}")
            continue

    return odoo, all_lines, all_product_ids

def get_cache_path():
    return Path("cache/products_cache.pkl")

def get_cache_metadata_path():
    return Path("cache/cache_metadata.json")

def is_cache_valid():
    metadata_path = get_cache_metadata_path()
    if not metadata_path.exists():
        return False
    try:
        with open(metadata_path, 'r') as f:
            metadata = json.load(f)
        last_update = datetime.fromisoformat(metadata['last_update'])
        return datetime.now() - last_update < timedelta(days=15)
    except Exception as e:
        print(f"Error verificando metadata del caché: {e}")
        return False

def save_products_cache(products_info):
    cache_path = get_cache_path()
    metadata_path = get_cache_metadata_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, 'wb') as f:
        pickle.dump(products_info, f)
    metadata = {
        'last_update': datetime.now().isoformat(),
        'products_count': len(products_info)
    }
    with open(metadata_path, 'w') as f:
        json.dump(metadata, f)
    print(f" Caché actualizado con {len(products_info)} productos")

def load_products_cache():
    cache_path = get_cache_path()
    try:
        with open(cache_path, 'rb') as f:
            return pickle.load(f)
    except Exception as e:
        print(f"Error cargando caché: {e}")
        return None

def get_product_info_with_cache(odoo, product_ids):
    if is_cache_valid():
        print(" Usando caché de productos...")
        cached_products = load_products_cache()
        if cached_products is not None:
            print(f" Caché cargado con {len(cached_products)} productos")
            return cached_products
    print(" Caché no válido o no existe, consultando productos desde Odoo...")
    products_info = get_product_info_in_batches(odoo, product_ids)
    save_products_cache(products_info)
    return products_info

def get_product_info_in_batches(odoo, product_ids, batch_size=100):
    context_en = {'lang': 'en_US'}
    print("\n Descargando todas las plantillas de productos (en inglés)...")

    all_templates = odoo.execute(
        'product.template',
        'search_read',
        [[]],
        {'fields': [
            'id', 'name', 'barcode', 'default_code',
            'x_studio_unidad_de_reposicin',
            'x_studio_halloween', 'x_studio_navidad', 'x_studio_solo_clinica',
            'x_studio_inventario_maximo', 'x_studio_inventario_minimo', 'x_studio_producto_grande'
        ],
         'context': context_en}
    )

    template_by_barcode = {}
    template_by_ref = {}
    template_by_id = {}
    for template in all_templates:
        if template.get('barcode'):
            template_by_barcode[template['barcode']] = template
        if template.get('default_code'):
            template_by_ref[template['default_code']] = template
        template_by_id[template['id']] = template

    print(f" {len(all_templates)} plantillas descargadas")
    products_info = {}
    total_products = len(product_ids)
    product_ids_list = list(product_ids)
    total_batches = (total_products + batch_size - 1) // batch_size

    print(f"\n Progreso de consulta de productos:")
    print(f"   Total productos: {total_products}")
    print(f"   Total lotes: {total_batches}")

    for batch_num, i in enumerate(range(0, total_products, batch_size), 1):
        batch = product_ids_list[i:i + batch_size]
        progress = (batch_num / total_batches) * 100
        print(f"   [{batch_num}/{total_batches}] {progress:.1f}% completado", end='\r')

        try:
            batch_products = odoo.execute(
                'product.product',
                'read',
                [batch],
                {'fields': [
                    'id',
                    'barcode',
                    'default_code',
                    'name',
                    'display_name',
                    'categ_id',
                    'create_date',
                    'product_tmpl_id',
                    'uom_po_id',
                    'x_studio_unidad_de_reposicin',
                    'x_studio_navidad',
                    'x_studio_halloween',
                    'x_studio_solo_clinica'
                ],
                'context': context_en}
            )

            for product in batch_products:
                template = None
                if product.get('product_tmpl_id'):
                    tmpl_id = product['product_tmpl_id'][0]
                    template = template_by_id.get(tmpl_id)
                if not template and product.get('barcode'):
                    template = template_by_barcode.get(product['barcode'])
                if not template and product.get('default_code'):
                    template = template_by_ref.get(product['default_code'])

                if template:
                    product['nombre_correcto'] = limpiar_nombre_producto(template['name'])
                    product['x_studio_halloween'] = template.get('x_studio_halloween', False)
                    product['x_studio_navidad'] = template.get('x_studio_navidad', False)
                    product['x_studio_solo_clinica'] = template.get('x_studio_solo_clinica', False)
                    product['product_template'] = template
                else:
                    product['nombre_correcto'] = limpiar_nombre_producto(product.get('name', ''))
                    product['x_studio_halloween'] = False
                    product['x_studio_navidad'] = False
                    product['x_studio_solo_clinica'] = False
                    product['product_template'] = {}

                products_info[product['id']] = product

        except Exception as e:
            print(f"\nError procesando lote {batch_num}: {e}")
            continue

        time.sleep(0.05)

    print("\n Consulta de productos completada")
    return products_info

# ---------------------------------------------
# CÁLCULO DE CANTIDADES
# ---------------------------------------------

def aplicar_reglas_cantidad_corregida(
    product_info, promedio_top2, stock_tienda, tienda, tipo, subcategoria=None,
    meses_inventario=1, disponible=0, productos_unidad_repos_invalida=None, config=None
):
    try:
        unidad_repos = obtener_unidad_reposicion(product_info)
        if not isinstance(unidad_repos, int) or unidad_repos < 1:
            if productos_unidad_repos_invalida is not None:
                productos_unidad_repos_invalida.append({
                    "producto": product_info.get("nombre_correcto", ""),
                    "codigo": product_info.get("default_code", "SIN CODIGO"),
                    "categoria": str(product_info.get("categ_id", ["", ""])[1]) if product_info.get("categ_id") else ""
                })
            return 0, "Unidad de reposición inválida"

        if disponible < unidad_repos:
            return 0, "Stock en bodega insuficiente"

        tipo_tienda = "regular"
        tienda_l = tienda.lower()
        if tienda_l in TIENDAS_REGULARES:
            tipo_tienda = "regular"
        elif tienda_l in TIENDAS_CHICAS:
            tipo_tienda = "chica"

        # Normalizar categoría
        categoria_nombre = str(product_info["categ_id"][1]) if len(product_info["categ_id"]) > 1 else ""
        categoria_normalizada = normalizar_categoria(categoria_nombre)

        # Validación estricta de categoría
        meses_inventario_categoria = obtener_meses_inventario_por_categoria_y_tienda(categoria_normalizada, tipo_tienda, config)
        print(f"DEBUG: Producto '{product_info.get('nombre_correcto', '')}', Categoría normalizada: '{categoria_normalizada}', Meses inventario: {meses_inventario_categoria}")
        # Para depuración, comentar la siguiente línea para no bloquear todo:
        # if meses_inventario_categoria == 0:
        #     print(f"  [VALIDACIÓN] Producto {product_info.get('nombre_correcto', '')} (Categoría: {categoria_normalizada}) FORZADO A 0 por meses_inventario = 0")
        #     return 0, "Categoría con meses de inventario = 0"

        # REGLA MEJORADA: Stock cero o ventas muy bajas
        if stock_tienda == 0 and aplicar_reglas_stock_cero_mejoradas(promedio_top2, config) and disponible >= unidad_repos:
            cantidad_minima = 0
            motivo = "Pedido mínimo por stock 0"
            
            if tipo == "accesorios":
                cantidad_minima = obtener_minimo_categoria(subcategoria, tipo_tienda, config)
                motivo = "Pedido mínimo por stock 0 (accesorios)"
            elif tipo == "alimentos":
                cantidad_minima = obtener_minimo_alimento(tipo_tienda, config)
                motivo = "Pedido mínimo por stock 0 (alimentos)"
            elif tipo == "medicamentos":
                cantidad_minima = obtener_minimo_medicamento(tipo_tienda, config)
                motivo = "Pedido mínimo por stock 0 (medicamentos)"

            minimo_producto = product_info.get("product_template", {}).get("x_studio_inventario_minimo", 0)
            if minimo_producto and minimo_producto > 0:
                if minimo_producto > cantidad_minima:
                    cantidad_minima = minimo_producto
                    motivo = "Pedido mínimo por stock 0 (mínimo producto)"

            cantidad = int(math.ceil(float(cantidad_minima) / unidad_repos) * unidad_repos)

            if cantidad > disponible:
                cantidad = (disponible // unidad_repos) * unidad_repos
                motivo += " - ajustado por stock bodega"
            
            if cantidad >= unidad_repos:
                # Aplicar mínimo para pedir
                minimo_pedir = obtener_minimo_para_pedir(subcategoria, tipo_tienda, config)
                if cantidad < minimo_pedir:
                    return 0, f"Cantidad {cantidad} menor que mínimo para pedir {minimo_pedir}"
                return cantidad, motivo

        cantidad_objetivo = promedio_top2 * meses_inventario
        cantidad_a_pedir = max(0, cantidad_objetivo - stock_tienda)

        cantidad_minima_categoria = 0
        motivo = "Pedido basado en ventas"
        
        if tipo == "accesorios" and subcategoria:
            minimo_categoria = obtener_minimo_categoria(subcategoria, tipo_tienda, config)
            if stock_tienda < minimo_categoria:
                cantidad_minima_categoria = minimo_categoria - stock_tienda
                motivo = "Pedido ajustado por mínimo categoría (accesorios)"
        elif tipo == "alimentos":
            minimo_alimento = obtener_minimo_alimento(tipo_tienda, config)
            if stock_tienda < minimo_alimento:
                cantidad_minima_categoria = minimo_alimento - stock_tienda
                motivo = "Pedido ajustado por mínimo categoría (alimentos)"
        elif tipo == "medicamentos":
            minimo_medicamento = obtener_minimo_medicamento(tipo_tienda, config)
            if stock_tienda < minimo_medicamento:
                cantidad_minima_categoria = minimo_medicamento - stock_tienda
                motivo = "Pedido ajustado por mínimo categoría (medicamentos)"

        minimo_producto = product_info.get("product_template", {}).get("x_studio_inventario_minimo", 0)
        cantidad_minima_producto = 0
        if minimo_producto and minimo_producto > 0:
            if stock_tienda < minimo_producto:
                cantidad_minima_producto = minimo_producto - stock_tienda
                motivo = "Pedido ajustado para alcanzar mínimo de inventario (producto)"

        cantidad = max(cantidad_a_pedir, cantidad_minima_categoria, cantidad_minima_producto)

        if cantidad > 0:
            cantidad = int(math.ceil(float(cantidad) / unidad_repos) * unidad_repos)

        maximo_producto = product_info.get("product_template", {}).get("x_studio_inventario_maximo", 0)
        if maximo_producto and maximo_producto > 0:
            maximo_pedido_posible = maximo_producto - stock_tienda
            if maximo_pedido_posible <= 0:
                return 0, "Stock en sucursal supera máximo permitido"
            else:
                maximo_pedido_redondeado = int(math.ceil(float(maximo_pedido_posible) / unidad_repos) * unidad_repos)
                if cantidad > maximo_pedido_redondeado:
                    cantidad = maximo_pedido_redondeado
                    motivo = "Pedido ajustado por inventario máximo"

        if cantidad <= 0:
            return 0, "Cantidad calculada <= 0"

        if cantidad > disponible:
            cantidad = (disponible // unidad_repos) * unidad_repos
            motivo += " - ajustado por stock bodega"

        # Aplicar mínimo para pedir
        minimo_pedir = obtener_minimo_para_pedir(subcategoria, tipo_tienda, config)
        if cantidad < minimo_pedir:
            return 0, f"Cantidad {cantidad} menor que mínimo para pedir {minimo_pedir}"

        if cantidad < unidad_repos:
            return 0, "Cantidad menor que unidad de reposición tras ajuste"

        print(f"DEBUG: Producto '{product_info.get('nombre_correcto', '')}', Cantidad final: {cantidad}, Motivo: {motivo}")
        return int(cantidad), motivo
        
    except Exception as e:
        print(f"Error en aplicar_reglas_cantidad para producto {product_info.get('default_code', '')}: {e}")
        return 0, f"Error: {e}"

# ---------------------------------------------
# EXPORTACIÓN Y LOGS
# ---------------------------------------------

def exportar_excel_pedido(df, path):
    try:
        df = df.sort_values(["Categoría", "Descripción"])
        df.to_excel(path, index=False)
    except Exception as e:
        print(f"Error exportando Excel {path}: {e}")

def generar_master_consolidado(productos):
    consolidado = {}
    for producto in productos:
        key = (producto["Código"], producto["Referencia Interna"], producto["Descripción"], producto["Categoría"])
        if key in consolidado:
            consolidado[key]["Cantidad"] += producto["Cantidad"]
        else:
            consolidado[key] = producto.copy()
    return list(consolidado.values())

def escribir_log_mejorado(log_path, productos_no_suplidos, resumen_tiendas, productos_unidad_repos_invalida, 
                         detalle_pedidos, productos_excluidos, estadisticas):
    try:
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(f"LOG DE PEDIDOS SUGERIDOS - {datetime.now()}\n")
            f.write("=" * 80 + "\n\n")

            # ESTADÍSTICAS GENERALES
            f.write(" ESTADÍSTICAS GENERALES\n")
            f.write("-" * 50 + "\n")
            f.write(f"Productos procesados: {estadisticas.get('productos_procesados', 0)}\n")
            f.write(f"Productos con pedidos: {estadisticas.get('productos_con_pedidos', 0)}\n")
            f.write(f"Productos excluidos: {len(productos_excluidos)}\n")
            f.write(f"Productos con unidad repos inválida: {len(productos_unidad_repos_invalida)}\n")
            f.write(f"Productos solo clínica excluidos: {estadisticas.get('productos_solo_clinica_excluidos', 0)}\n")

            f.write("\n" + "=" * 80 + "\n\n")

            # PRODUCTOS EXCLUIDOS CON DETALLE
            if productos_excluidos:
                f.write(" PRODUCTOS EXCLUIDOS DEL PROCESAMIENTO\n")
                f.write("-" * 50 + "\n")
                halloween_count = 0
                navidad_count = 0
                solo_clinica_count = 0
                otros_count = 0
                
                for p in productos_excluidos:
                    f.write(f" {p['producto']} - Motivo: {p['motivo']}\n")
                    if "halloween" in p['motivo'].lower():
                        halloween_count += 1
                    elif "navidad" in p['motivo'].lower():
                        navidad_count += 1
                    elif "solo clínica" in p['motivo'].lower():
                        solo_clinica_count += 1
                    else:
                        otros_count += 1
                
                f.write(f"\n RESUMEN DE EXCLUSIONES:\n")
                f.write(f"    Productos de Halloween: {halloween_count}\n")
                f.write(f"    Productos de Navidad: {navidad_count}\n")
                f.write(f"    Productos solo clínica: {solo_clinica_count}\n")
                f.write(f"    Otros motivos: {otros_count}\n")
                f.write("\n" + "=" * 80 + "\n\n")

            f.write(" PRODUCTOS NO SUPLIDOS POR FALTA DE STOCK EN BODEGA\n")
            f.write("-" * 50 + "\n")
            for p in productos_no_suplidos:
                f.write(f" {p['producto']} ({p['categoria']}) en {p['tienda'].title()}: Solicitado {p['solicitado']}, Entregado {p['entregado']} ({p['motivo']})\n")

            f.write("\n" + "=" * 80 + "\n\n")

            f.write(" RESUMEN DE PRODUCTOS ENVIADOS POR TIENDA\n")
            f.write("-" * 50 + "\n")
            for tienda, resumen in resumen_tiendas.items():
                f.write(f"\n {tienda.upper()}\n")
                for tipo, cantidad in resumen.items():
                    f.write(f"   {tipo.title()}: {cantidad} unidades\n")

            f.write("\n" + "=" * 80 + "\n\n")

            f.write(" DETALLE DE MOTIVOS DE PEDIDO POR TIENDA Y PRODUCTO\n")
            f.write("-" * 50 + "\n")
            for tienda, productos in detalle_pedidos.items():
                f.write(f"\n {tienda.upper()}\n")
                for p in productos:
                    f.write(f" {p['producto']} ({p['categoria']}): Cantidad {p['cantidad']} - Motivo: {p['motivo']}\n")

            if productos_unidad_repos_invalida:
                f.write("\n" + "=" * 80 + "\n\n")
                f.write(" PRODUCTOS CON UNIDAD DE REPOSICIÓN INVÁLIDA\n")
                f.write("-" * 50 + "\n")
                for p in productos_unidad_repos_invalida:
                    f.write(f" {p['producto']} ({p['codigo']}) - {p['categoria']}\n")
    except Exception as e:
        print(f"Error escribiendo log {log_path}: {e}")

# ---------------------------------------------
# PROCESO PRINCIPAL CON LÓGICA DE SOLO CLÍNICA
# ---------------------------------------------

def procesar_pedidos_odoo_con_solo_clinica(output_dir="Pedidos_Sugeridos", config=None):
    """VERSIÓN CON LÓGICA DE SOLO CLÍNICA - Excluye productos de Halloween y aplica filtro de clínica"""
    try:
        print(" INICIANDO PROCESO DE PEDIDOS SUGERIDOS")
        print(" PRODUCTOS DE HALLOWEEN Y NAVIDAD SERÁN EXCLUIDOS AUTOMÁTICAMENTE")
        print(" PRODUCTOS SOLO CLÍNICA SOLO IRÁN A TIENDAS CON CLÍNICA")
        print("=" * 80)
        os.makedirs(output_dir, exist_ok=True)

        odoo, all_lines, all_product_ids = cargar_datos_reposicion()
        print(f" Consultando información de {len(all_product_ids)} productos únicos...")
        product_dict = get_product_info_with_cache(odoo, all_product_ids)

        agrupado = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
        masters = defaultdict(lambda: defaultdict(list))
        resumen_tiendas = defaultdict(lambda: defaultdict(int))
        productos_no_suplidos = []
        productos_unidad_repos_invalida = []
        detalle_pedidos = defaultdict(list)
        productos_excluidos = []

        print("\n Aplicando reglas de negocio y FILTROS DE EXCLUSIÓN...")

        lineas_por_producto = defaultdict(list)
        for line in all_lines:
            product_id = line['product_id'][0]
            lineas_por_producto[product_id].append(line)

        productos_procesados = 0
        productos_con_pedidos = 0
        productos_halloween_excluidos = 0
        productos_navidad_excluidos = 0
        productos_solo_clinica_excluidos = 0

        for product_id, lineas in lineas_por_producto.items():
            productos_procesados += 1
            product_info = product_dict.get(product_id, {})
            
            if not product_info or not product_info.get("categ_id"):
                productos_excluidos.append({
                    "producto": f"ID: {product_id}",
                    "motivo": "Sin información de producto o categoría"
                })
                continue

            categoria_nombre = str(product_info["categ_id"][1]) if len(product_info["categ_id"]) > 1 else ""
            nombre = product_info.get("nombre_correcto", "")
            tipo = determinar_tipo_producto(categoria_nombre, nombre)

            # VERIFICACIÓN DEFINITIVA DE HALLOWEEN Y NAVIDAD
            es_estacional, motivo_estacional = es_producto_halloween_o_navidad(product_info)
            if es_estacional:
                productos_excluidos.append({
                    "producto": nombre,
                    "motivo": motivo_estacional
                })
                if "halloween" in motivo_estacional.lower():
                    productos_halloween_excluidos += 1
                elif "navidad" in motivo_estacional.lower():
                    productos_navidad_excluidos += 1
                continue

            # NUEVA VERIFICACIÓN: PRODUCTOS SOLO CLÍNICA
            es_solo_clinica, motivo_clinica = es_producto_solo_clinica(product_info)

            # Verificar exclusiones por categoría
            if tipo in CATEGORIAS_EXCLUIR:
                productos_excluidos.append({
                    "producto": nombre,
                    "motivo": f"Categoría excluida: {tipo}"
                })
                continue

            stock_bodega = float(lineas[0].get('qty_in_wh', 0) or 0)
            if stock_bodega <= 0:
                productos_excluidos.append({
                    "producto": nombre,
                    "motivo": "Sin stock en bodega"
                })
                continue

            # Ordenar líneas por ventas para priorizar tiendas con más movimiento
            lineas.sort(key=lambda l: sugerido_top2_6meses(l), reverse=True)
            disponible = int(stock_bodega)
            producto_tuvo_pedidos = False

            for l in lineas:
                tienda = l['shop_pos_id'][1].strip().lower()
                stock_tienda = int(l.get('qty_to_hand') or 0)
                promedio_top2 = sugerido_top2_6meses(l)

                # NUEVA LÓGICA: Si el producto es solo clínica, verificar si la tienda tiene clínica
                if es_solo_clinica and not tienda_tiene_clinica(tienda):
                    # No agregar a productos_excluidos aquí porque es por tienda específica
                    continue

                # Verificar si debe excluirse por ser producto grande en tienda chica
                if debe_excluir_producto_grande(product_info, tienda, config):
                    continue

                tipo_tienda = "regular"
                if tienda in TIENDAS_REGULARES:
                    tipo_tienda = "regular"
                elif tienda in TIENDAS_CHICAS:
                    tipo_tienda = "chica"

                # Obtener meses de inventario específicos
                meses = obtener_meses_inventario_por_categoria_y_tienda(categoria_nombre, tipo_tienda, config)

                # Aplicar reglas corregidas
                cantidad_final, motivo = aplicar_reglas_cantidad_corregida(
                    product_info=product_info,
                    promedio_top2=promedio_top2,
                    stock_tienda=stock_tienda,
                    tienda=tienda,
                    tipo=tipo,
                    subcategoria=categoria_nombre,
                    meses_inventario=meses,
                    disponible=disponible,
                    productos_unidad_repos_invalida=productos_unidad_repos_invalida,
                    config=config
                )

                if cantidad_final > disponible:
                    productos_no_suplidos.append({
                        "tienda": tienda,
                        "producto": nombre,
                        "categoria": categoria_nombre,
                        "solicitado": cantidad_final,
                        "entregado": disponible,
                        "motivo": "Stock insuficiente en bodega"
                    })
                    cantidad_final = disponible
                    motivo = "Ajustado por stock insuficiente en bodega"

                if cantidad_final <= 0:
                    continue

                disponible -= cantidad_final
                producto_tuvo_pedidos = True

                # Crear item y agregarlo a las estructuras
                item = crear_item_producto(product_info, cantidad_final, categoria_nombre)
                ruta = obtener_ruta(tienda)
                agrupado[ruta][tienda][tipo].append(item)
                
                # INCLUIR MEDICAMENTOS EN MASTERS
                if tipo in ["alimentos", "accesorios", "medicamentos"]:
                    masters[ruta][tipo].append(item)
                
                resumen_tiendas[tienda][tipo] += cantidad_final

                detalle_pedidos[tienda].append({
                    "producto": nombre,
                    "categoria": categoria_nombre,
                    "cantidad": cantidad_final,
                    "motivo": motivo
                })

                # Si no hay suficiente stock para la siguiente tienda, salir del loop
                unidad_repos = obtener_unidad_reposicion(product_info)
                if disponible < unidad_repos:
                    break

            if producto_tuvo_pedidos:
                productos_con_pedidos += 1
            elif es_solo_clinica:
                # Contar productos solo clínica que no tuvieron pedidos
                productos_solo_clinica_excluidos += 1

        estadisticas = {
            "productos_procesados": productos_procesados,
            "productos_con_pedidos": productos_con_pedidos,
            "productos_halloween_excluidos": productos_halloween_excluidos,
            "productos_navidad_excluidos": productos_navidad_excluidos,
            "productos_solo_clinica_excluidos": productos_solo_clinica_excluidos
        }

        print(f"\n RESUMEN DE PROCESAMIENTO:")
        print(f"    Productos procesados: {productos_procesados}")
        print(f"    Productos con pedidos: {productos_con_pedidos}")
        print(f"    Productos de Halloween excluidos: {productos_halloween_excluidos}")
        print(f"    Productos de Navidad excluidos: {productos_navidad_excluidos}")
        print(f"    Productos solo clínica limitados: {productos_solo_clinica_excluidos}")
        print(f"    Total productos excluidos: {len(productos_excluidos)}")

        secuencia_global = get_next_global_sequence()
        print(f"\n Secuencia global para esta ejecución: {secuencia_global}")

        # Crear carpeta global para medicamentos
        medicamentos_dir = os.path.join(output_dir, "medicamentos")
        os.makedirs(medicamentos_dir, exist_ok=True)

        # Generar archivos por ruta y tienda
        for ruta, tiendas in agrupado.items():
            ruta_dir = os.path.join(output_dir, f"{ruta}_PEDIDO_{secuencia_global}")
            os.makedirs(ruta_dir, exist_ok=True)

            for tienda, tipos in tiendas.items():
                nombre_tienda = tienda.title().replace(" ", "_")
                carpeta_tienda = os.path.join(ruta_dir, nombre_tienda)
                os.makedirs(carpeta_tienda, exist_ok=True)

                for tipo, productos in tipos.items():
                    if tipo in CATEGORIAS_EXCLUIR:
                        continue
                    if not productos:
                        continue

                    df = pd.DataFrame(productos)
                    print(f"DEBUG: Exportando {len(productos)} productos para tienda '{tienda}', tipo '{tipo}'")
                    if not all(col in df.columns for col in COLUMNS_OUT):
                        print(f"ERROR: Columnas esperadas no están en DataFrame: {df.columns.tolist()}")
                        print(f"Ejemplo producto: {productos[0] if productos else 'N/A'}")
                        continue

                    df = df[COLUMNS_OUT]
                    nombre_archivo = f"{nombre_tienda}_{ruta}_{tipo.upper()}_{secuencia_global}.xlsx"

                    if tipo == "medicamentos":
                        # Guardar medicamentos en carpeta global
                        exportar_excel_pedido(df, os.path.join(medicamentos_dir, nombre_archivo))
                        print(f"    └─ {nombre_archivo} ({len(df)} productos) [Medicamentos en carpeta global]")
                    else:
                        # Guardar otros tipos en carpeta por tienda y ruta
                        exportar_excel_pedido(df, os.path.join(carpeta_tienda, nombre_archivo))
                        print(f"    └─ {nombre_archivo} ({len(df)} productos)")

        # Generar MASTER INCLUYENDO MEDICAMENTOS
        for ruta, tipos in masters.items():
            ruta_dir = os.path.join(output_dir, f"{ruta}_PEDIDO_{secuencia_global}")
            os.makedirs(ruta_dir, exist_ok=True)
            
            # INCLUIR MEDICAMENTOS EN MASTERS
            for tipo_master in ["alimentos", "accesorios", "medicamentos"]:
                if tipo_master in tipos:
                    productos_master = [p for p in tipos[tipo_master] if p["Cantidad"] > 0]
                    if productos_master:
                        productos_consolidados = generar_master_consolidado(productos_master)
                        df_master = pd.DataFrame(productos_consolidados)
                        if not all(col in df_master.columns for col in COLUMNS_OUT):
                            print(f"ERROR: Columnas esperadas no están en DataFrame master: {df_master.columns.tolist()}")
                            continue
                        df_master = df_master[COLUMNS_OUT]
                        master_filename = f"MASTER_{tipo_master.upper()}_{ruta}_{secuencia_global}.xlsx"
                        master_path = os.path.join(ruta_dir, master_filename)
                        exportar_excel_pedido(df_master, master_path)
                        print(f"   {master_filename} ({len(df_master)} productos únicos)")

        # Generar MASTER GLOBAL
        todos_los_productos = []
        for ruta, tiendas in agrupado.items():
            for tienda, tipos in tiendas.items():
                for tipo, productos in tipos.items():
                    if tipo not in CATEGORIAS_EXCLUIR:
                        todos_los_productos.extend(productos)

        master_global = generar_master_consolidado(todos_los_productos)
        df_master_global = pd.DataFrame(master_global)
        if not all(col in df_master_global.columns for col in COLUMNS_OUT):
            print(f"ERROR: Columnas esperadas no están en DataFrame master global: {df_master_global.columns.tolist()}")
        else:
            df_master_global = df_master_global[COLUMNS_OUT]
            master_global_path = os.path.join(output_dir, f"MASTER_GLOBAL_{secuencia_global}.xlsx")
            exportar_excel_pedido(df_master_global, master_global_path)
            print(f"\n MASTER_GLOBAL generado: {master_global_path} ({len(df_master_global)} productos únicos)")

        # Generar log mejorado
        log_path = os.path.join(output_dir, f"log_pedidos_{secuencia_global}.txt")
        escribir_log_mejorado(
            log_path,
            productos_no_suplidos,
            resumen_tiendas,
            productos_unidad_repos_invalida,
            detalle_pedidos,
            productos_excluidos,
            estadisticas
        )

        print("\n" + "=" * 80)
        print(" PROCESO COMPLETADO EXITOSAMENTE")
        print(f" {productos_halloween_excluidos} productos de Halloween fueron EXCLUIDOS")
        print(f" {productos_navidad_excluidos} productos de Navidad fueron EXCLUIDOS")
        print(f" Productos solo clínica LIMITADOS a tiendas con clínica")
        print(f" Log detallado generado en: {log_path}")
        print("=" * 80)
        
        return estadisticas

    except Exception as e:
        print(f" Error crítico en proceso principal: {e}")
        return None

# ---------------------------------------------
# SECUENCIA GLOBAL PARA ARCHIVOS
# ---------------------------------------------

def get_next_global_sequence():
    now = datetime.now()
    return now.strftime("%Y%m%d_%H%M%S")

# ---------------------------------------------
# EJECUCIÓN PRINCIPAL
# ---------------------------------------------

if __name__ == "__main__":
    print(" SISTEMA DE PEDIDOS BLACK DOG - CON LÓGICA DE SOLO CLÍNICA")
    print(" Los productos de Halloween y Navidad serán excluidos automáticamente")
    print(" Los productos solo clínica solo irán a tiendas con clínica")
    print("=" * 80)
    
    print("\n TIENDAS CON CLÍNICA:")
    for tienda in sorted(TIENDAS_CON_CLINICA):
        print(f"    {tienda.title()}")
    
    config = cargar_configuracion("config_ajustes.json")
    
    # Usar la función CON LÓGICA DE SOLO CLÍNICA
    estadisticas = procesar_pedidos_odoo_con_solo_clinica(config=config)
    if estadisticas:
        print(f"\n PROCESO COMPLETADO EXITOSAMENTE!")
        print(f" ESTADÍSTICAS FINALES:")
        print(f"   - Productos procesados: {estadisticas['productos_procesados']}")
        print(f"   - Productos con pedidos: {estadisticas['productos_con_pedidos']}")
        print(f"   - Productos de Halloween excluidos: {estadisticas['productos_halloween_excluidos']}")
        print(f"   - Productos de Navidad excluidos: {estadisticas['productos_navidad_excluidos']}")
        print(f"   - Productos solo clínica limitados: {estadisticas['productos_solo_clinica_excluidos']}")
        print("\n CONFIRMADO: Los productos de Halloween fueron EXCLUIDOS del proceso")
        print(" CONFIRMADO: Los productos solo clínica solo van a tiendas con clínica")
    else:
        print(" El proceso falló. Revisa los errores anteriores.")