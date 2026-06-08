import streamlit as st
import pandas as pd
import folium
from streamlit_folium import folium_static
from folium.plugins import MarkerCluster
import sqlalchemy
from urllib.parse import quote_plus
import json

# =====================================================================
# 1. CONFIGURACIÓN DE LA PÁGINA WEB
# =====================================================================
st.set_page_config(page_title="Visor SIG de Infraestructura por Cuencas", layout="wide")

st.title("🗺️ Visor SIG Interactivo Bidireccional")
st.markdown("Selecciona filas en la tabla para enfocar el mapa, o usa los filtros para actualizar ambos componentes.")

# =====================================================================
# 2. CONEXIÓN CORREGIDA A SUPABASE
# =====================================================================
@st.cache_resource
def obtener_conexion():
    # Streamlit leerá esto de un entorno seguro en la nube
    CONTRASENA_REAL = st.secrets["DB_PASSWORD"]
    usuario_pooler = "postgres.lpsoplyffcbtesehrspy"
    password_segura = quote_plus(CONTRASENA_REAL)
    host_pooler = "aws-1-us-west-2.pooler.supabase.com:6543"
    db = "postgres"
    URI = f"postgresql://{usuario_pooler}:{password_segura}@{host_pooler}/{db}"
    return sqlalchemy.create_engine(URI)

try:
    engine = obtener_conexion()
except Exception as e:
    st.error(f"Error de inicialización del motor: {e}")
    st.stop()

# =====================================================================
# 3. FUNCIONES DE CARGA DE DATOS (CON TRANSFORMACIÓN DE PROYECCIÓN)
# =====================================================================
@st.cache_data
def obtener_catalogo_filtros(tipo_analisis):
    if tipo_analisis == "Por Estado (des25)":
        query = 'SELECT DISTINCT estado as nombre FROM reporte_estatal_cuenca WHERE estado IS NOT NULL ORDER BY estado;'
    else:
        query = 'SELECT DISTINCT nombre_cuenca as nombre FROM reporte_total_cuenca WHERE nombre_cuenca IS NOT NULL ORDER BY nombre_cuenca;'
    try:
        with engine.connect() as conn:
            df = pd.read_sql(sqlalchemy.text(query), con=conn)
            return df['nombre'].tolist()
    except Exception as e:
        st.sidebar.error(f"Error de catálogo: {e}")
        return []

@st.cache_data
def cargar_datos_por_estado(estado_nombre):
    nombre_limpio = estado_nombre.replace(" ", "").lower()
    query_geom = f"""
        SELECT ST_AsGeoJSON(ST_Transform(ST_SetSRID(geom, 6362), 4326))
        FROM des25
        WHERE LOWER(REPLACE("NOMGEO", ' ', '')) ILIKE '%{nombre_limpio}%'
        LIMIT 1;
    """
    query_loc = f"SELECT * FROM reporte_localidades_cuenca WHERE LOWER(REPLACE(nom_ent, ' ', '')) ILIKE '%{nombre_limpio}%';"
    try:
        with engine.connect() as conn:
            res_geom = conn.execute(sqlalchemy.text(query_geom)).scalar()
            geom = json.loads(res_geom) if res_geom else None
            df_loc = pd.read_sql(sqlalchemy.text(query_loc), con=conn)
            return geom, df_loc
    except Exception as e:
        st.sidebar.error(f"Error en reporte estatal: {e}")
        return None, pd.DataFrame()

@st.cache_data
def cargar_datos_por_cuenca(cuenca_nombre):
    query_geom = f'SELECT ST_AsGeoJSON(geom) FROM cuenca_hidrografica WHERE "NOMBRE" = \'{cuenca_nombre}\' LIMIT 1;'
    query_loc = f"SELECT * FROM reporte_localidades_cuenca WHERE nombre_cuenca = '{cuenca_nombre}';"
    try:
        with engine.connect() as conn:
            res_geom = conn.execute(sqlalchemy.text(query_geom)).scalar()
            geom = json.loads(res_geom) if res_geom else None
            df_loc = pd.read_sql(sqlalchemy.text(query_loc), con=conn)
            return geom, df_loc
    except Exception as e:
        st.sidebar.error(f"Error en reporte de cuenca: {e}")
        return None, pd.DataFrame()

# =====================================================================
# 4. BARRA LATERAL (FILTROS GENERALES)
# =====================================================================
st.sidebar.header("Filtros de Región")
tipo_analisis = st.sidebar.radio("1. Tipo de análisis:", ["Por Estado (des25)", "Por Cuenca Hidrográfica (rh)"])
opciones_seleccionables = obtener_catalogo_filtros(tipo_analisis)
seleccion_especifica = st.sidebar.selectbox("2. Selecciona una opción:", opciones_seleccionables if opciones_seleccionables else ["No se pudieron cargar datos"])

if st.sidebar.button("🔄 Forzar recarga de datos"):
    st.cache_data.clear()
    st.rerun()

# =====================================================================
# 5. PROCESAMIENTO Y LIMPIEZA DE DATOS
# =====================================================================
geom_poligono = None
df_localidades = pd.DataFrame()

if seleccion_especifica and seleccion_especifica != "No se pudieron cargar datos":
    if tipo_analisis == "Por Estado (des25)":
        geom_poligono, df_localidades = cargar_datos_por_estado(seleccion_especifica)
    else:
        geom_poligono, df_localidades = cargar_datos_por_cuenca(seleccion_especifica)

if not df_localidades.empty:
    df_localidades['longitud'] = pd.to_numeric(df_localidades['longitud'], errors='coerce')
    df_localidades['latitud'] = pd.to_numeric(df_localidades['latitud'], errors='coerce')
    columnas_censo = ['vphna', 'vphnd', 'vphns', 'inaa', 'inad', 'inas', 'poblacion_absoluta']
    for col in columnas_censo:
        if col in df_localidades.columns:
            df_localidades[col] = df_localidades[col].fillna(0)
    # Limpieza estricta de filas sin coordenadas para el mapa
    df_localidades = df_localidades.dropna(subset=['latitud', 'longitud'])
    df_localidades = df_localidades[(df_localidades['latitud'] != 0) & (df_localidades['longitud'] != 0)]

# =====================================================================
# 6. DISEÑO DE INTERFAZ: TABLA (ARRIBA/DETALLE) Y MAPA (ABAJO)
# =====================================================================
st.subheader("📊 1. Tabla de Datos Interactiva (Haz clic en el extremo izquierdo de una fila)")
st.markdown("<small>Tip: Puedes ordenar por población o carencias haciendo clic en los encabezados de las columnas.</small>", unsafe_allow_html=True)

df_filtrado = df_localidades.copy()
fila_seleccionada = None

if not df_localidades.empty:
    # Columnas clave a mostrar para que sea cómodo de leer
    columnas_visibles = ['cve_loc', 'nom_loc', 'nom_mun', 'poblacion_absoluta', 'vphna', 'vphnd', 'inaa', 'inad']

    # Renderizar st.dataframe con SELECCIÓN ACTIVA DE FILAS
    seleccion_tabla = st.dataframe(
        df_localidades[columnas_visibles],
        use_container_width=True,
        hide_index=True,
        selection_mode="single-row",
        on_select="rerun"
    )

    # Validar si el usuario seleccionó una fila en la tabla
    if seleccion_tabla and "rows" in seleccion_tabla["selection"] and seleccion_tabla["selection"]["rows"]:
        indice_fila = seleccion_tabla["selection"]["rows"][0]
        fila_seleccionada = df_localidades.iloc[indice_fila]
        st.success(f"📍 Enfocando localidad: **{fila_seleccionada['nom_loc']}** (Municipio: {fila_seleccionada['nom_mun']})")
else:
    st.info("No hay datos disponibles para mostrar en la tabla.")

# =====================================================================
# 7. CONSTRUCCIÓN DEL MAPA DINÁMICO CON ENFOQUE (ZOOM TO FEATURE)
# =====================================================================
st.subheader("🗺️ 2. Distribución Cartográfica Dinámica")

if not df_filtrado.empty:
    # LÓGICA DE ENFOQUE (TABLA -> MAPA)
    # Si hay una fila seleccionada en la tabla, el centro del mapa se amolda exactamente a esa localidad con zoom cerrado
    if fila_seleccionada is not None:
        centro_lat = float(fila_seleccionada['latitud'])
        centro_lon = float(fila_seleccionada['longitud'])
        zoom_inicial = 14  # Zoom detallado de calle/localidad
    else:
        # Si no hay selección, encuadra la cuenca o estado completo
        centro_lat = df_filtrado['latitud'].mean()
        centro_lon = df_filtrado['longitud'].mean()
        zoom_inicial = 8   # Vista regional

    url_carto_voyager = "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png"
    mapa = folium.Map(location=[centro_lat, centro_lon], zoom_start=zoom_inicial, tiles=url_carto_voyager, attr='&copy; CARTO')

    if geom_poligono:
        folium.GeoJson(
            geom_poligono,
            name="Límite Político/Hidrográfico",
            style_function=lambda x: {'color': '#ae52d4' if "Estado" in tipo_analisis else '#0288d1', 'weight': 2.5, 'fillOpacity': 0.01}
        ).add_to(mapa)

    cluster_localidades = MarkerCluster(name="Localidades").add_to(mapa)

    for _, fila in df_filtrado.iterrows():
        popup_html = f"""
        <div style='font-family: sans-serif; font-size: 12px; width: 190px;'>
            <b>Localidad:</b> {fila['nom_loc']}<br>
            <b>Municipio:</b> {fila['nom_mun']}<br><br>
            💧 Viv. Sin Agua: {int(fila['vphna'])} ({fila['inaa']}% )<br>
            🕳️ Viv. Sin Drenaje: {int(fila['vphnd'])} ({fila['inad']}% )
        </div>
        """
        # Si esta fila es la seleccionada, le cambiamos el color al marcador para destacarla
        es_la_seleccionada = (fila_seleccionada is not None and fila['cve_loc'] == fila_seleccionada['cve_loc'])

        if es_la_seleccionada:
            color_icono = "darkpurple"
            icono_tipo = "star"
        else:
            color_icono = "red" if float(fila['vphna']) > 0 else "blue"
            icono_tipo = "info-sign"

        folium.Marker(
            location=[fila['latitud'], fila['longitud']],
            popup=folium.Popup(popup_html, max_width=250),
            tooltip=str(fila['nom_loc']),
            icon=folium.Icon(color=color_icono, icon=icono_tipo)
        ).add_to(cluster_localidades)

    folium.LayerControl().add_to(mapa)
    folium_static(mapa, width=1100, height=550)
