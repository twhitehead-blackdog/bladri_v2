# APP DE GESTIÓN DE PEDIDOS - BLACK DOG

Descripción
-----------
Aplicación web en Flask para gestionar ajustes de inventario, guardar configuraciones, generar pedidos y mantener un historial por usuario. Optimizada para entornos de operación de múltiples tiendas con control centralizado.

Características
---------------
- Login por usuario (con credenciales predefinidas).
- Editor visual de configuración (`config_ajustes.json`).
- Restauración a valores predeterminados (`config_default.json`).
- Gestión de archivos ZIP generados y archivos TXT subidos.
- Rutas protegidas por sesión.
- Diseño listo para personalización visual.

Estructura de Carpetas
----------------------
- app.py                      → Código principal Flask
- config_ajustes.json         → Ajustes actuales (editable)
- config_default.json         → Ajustes predeterminados (reseteables)
- historial_zips.json         → Historial de archivos ZIP por usuario
- historial_txt.json          → Historial de archivos TXT subidos
- /ZIPS_GENERADOS             → ZIPs listos para descargar
- /pedidos_sugeridos          → Archivos intermedios de pedidos
- /txt_subidos                → Archivos TXT cargados
- /templates                  → Vistas HTML (login, index, ajustes)
- /static                     → Archivos opcionales (CSS, JS)

Instalación y Ejecución
-----------------------
1. Clona el repositorio:
   git clone https://github.com/tu_usuario/app-pedidos-blackdog.git
   cd app-pedidos-blackdog

2. Instala dependencias:
   pip install flask pandas

3. Ejecuta la app:
   python app.py

4. Abre en tu navegador:
   http://localhost:5000

Rutas Disponibles
-----------------
/                   → Redirige a login
/login              → Formulario de inicio de sesión
/logout             → Cierra sesión actual
/index              → Vista principal con ajustes cargados
/ajustes            → Vista completa para editar configuración
/guardar_ajustes    → Endpoint para guardar configuración (POST)
/resetear_ajustes   → Restaura configuración original (POST)

Formato de Configuración
-------------------------
El archivo config_ajustes.json incluye:

{
  "meses_inventario": {
    "general": 1.0,
    "categorias": {
      "Alimentos": 1.5,
      "Accesorios": 2.0
    }
  },
  "minimos_alimentos": {
    "regular": 10,
    "chica": 5
  },
  "minimos_accesorios": {
    "grande": {
      "camas": 3
    }
  },
  "minimos_medicamentos": {
    "regular": 2
  },
  "minimos_para_pedir": {
    "mediana": {
      "juguetes": 1
    }
  }
}

Notas Finales
-------------
- Asegúrate de editar siempre desde la interfaz web `/ajustes` para mantener la integridad del archivo.
- No edites manualmente los archivos JSON a menos que sepas lo que haces.
- El sistema fue creado para el entorno operativo de Black Dog Panamá, pero puede adaptarse a cualquier empresa con lógica de pedidos por tiendas.

