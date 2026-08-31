"""
App básica de Streamlit — Nivel de ríos/quebradas (CORNARE / MARCO)
--------------------------------------------------------------------
Cada estudiante debe cambiar, como mínimo, el código de la estación
en el sidebar. Los valores de fecha y calidad también son ajustables.

NOVEDADES DE ESTA VERSIÓN
--------------------------------------------------------------------
- Se extraen y muestran el NOMBRE de la estación, su UBICACIÓN y TIPO
  (si la API los trae en la respuesta), igual que ya se hacía con
  lat/lon: se prueban varias "llaves candidatas" y si no aparece
  ninguna, se avisa en pantalla en vez de fallar en silencio.
- Se extraen los UMBRALES de alerta (seguro / amarilla / naranja / roja)
  cuando la API los entrega, y se calcula un ESTADO de alerta actual
  comparando el último nivel contra esos umbrales.
- Se agregan métricas de "Nivel actual" y "Máximo del período".
- Se agrega (opcional) una segunda consulta a un endpoint de
  precipitación, con el mismo esquema robusto: si no existe o falla,
  la app simplemente no muestra esa sección, sin romperse.

Para correrla:
    streamlit run app_nivel_cornare.py
"""

import requests
import pandas as pd
import numpy as np
import streamlit as st
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ------------------------------------------------------------------
# Coordenadas por defecto (Institución Universitaria Pascual Bravo)
# Se usan solo si la API no trae la latitud/longitud de la estación.
# ------------------------------------------------------------------
LAT_DEFECTO = 6.2766
LON_DEFECTO = -75.5901

API_BASE_URL = "https://marco.cornare.gov.co/api/v1/estaciones"

LLAVE_FECHA = "level_date"
LLAVE_VALOR = "level"

CANDIDATOS_LAT = ["lat", "latitude", "latitud"]
CANDIDATOS_LON = ["lng", "lon", "longitude", "longitud"]

# --- Nuevas llaves candidatas para metadatos de la estación --------
# Ajusta estas listas si inspeccionas la respuesta real de la API
# (por ejemplo con st.json(datos_crudos)) y encuentras otro nombre.
CANDIDATOS_NOMBRE = ["name", "nombre", "station_name", "estacion", "title"]
CANDIDATOS_UBICACION = ["location", "ubicacion", "address", "direccion", "site"]
CANDIDATOS_TIPO = ["type", "tipo", "station_type", "category"]

# Llaves candidatas para los umbrales de alerta (colores del semáforo)
CANDIDATOS_UMBRAL_AMARILLA = ["yellow_level", "umbral_amarilla", "warning_level", "alert_yellow"]
CANDIDATOS_UMBRAL_NARANJA = ["orange_level", "umbral_naranja", "alert_orange"]
CANDIDATOS_UMBRAL_ROJA = ["red_level", "umbral_roja", "danger_level", "alert_red"]

# Endpoint opcional de precipitación (mismo patrón que "nivel").
# Si tu API usa otro nombre de recurso, cámbialo aquí.
ENDPOINT_PRECIPITACION = "precipitacion"

st.set_page_config(page_title="Nivel de estación — CORNARE", page_icon="🌊", layout="wide")


# ------------------------------------------------------------------
# Funciones de consulta
# ------------------------------------------------------------------
def obtener_serie(codigo_estacion, recurso, desde, hasta, calidad=1, timeout=30):
    """Consulta genérica a /estaciones/{codigo}/{recurso} (nivel o precipitacion)."""
    url = f"{API_BASE_URL}/{codigo_estacion}/{recurso}"
    params = {"desde": desde, "hasta": hasta, "calidad": calidad}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
    }
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=timeout, verify=False)
        if resp.status_code == 200:
            return resp.json(), None
        return None, f"HTTP {resp.status_code}"
    except requests.exceptions.RequestException as e:
        return None, f"Error de red: {e}"


def obtener_serie_nivel(codigo_estacion, desde, hasta, calidad=1, timeout=30):
    return obtener_serie(codigo_estacion, "nivel", desde, hasta, calidad, timeout)


def obtener_todas_las_paginas(datos_json, timeout=30):
    registros = list(datos_json.get("values", []))
    siguiente_url = datos_json.get("next")
    while siguiente_url:
        try:
            resp = requests.get(siguiente_url, timeout=timeout, verify=False)
        except requests.exceptions.RequestException:
            break
        if resp.status_code != 200:
            break
        pagina = resp.json()
        registros.extend(pagina.get("values", []))
        siguiente_url = pagina.get("next")
    return registros


def _primer_valor_presente(datos_json, candidatos):
    """Devuelve el primer valor encontrado entre varias llaves candidatas."""
    if not isinstance(datos_json, dict):
        return None
    return next((datos_json[k] for k in candidatos if k in datos_json and datos_json[k]), None)


def detectar_coordenadas(datos_json):
    """Busca lat/lon en las llaves raíz de la respuesta. Si no las encuentra, usa el valor por defecto."""
    lat = _primer_valor_presente(datos_json, CANDIDATOS_LAT)
    lon = _primer_valor_presente(datos_json, CANDIDATOS_LON)

    if lat is not None and lon is not None:
        try:
            return float(lat), float(lon), True
        except (TypeError, ValueError):
            pass
    return LAT_DEFECTO, LON_DEFECTO, False


def detectar_metadatos_estacion(datos_json):
    """Extrae nombre, ubicación y tipo de estación si la API los trae."""
    nombre = _primer_valor_presente(datos_json, CANDIDATOS_NOMBRE)
    ubicacion = _primer_valor_presente(datos_json, CANDIDATOS_UBICACION)
    tipo = _primer_valor_presente(datos_json, CANDIDATOS_TIPO)
    return nombre, ubicacion, tipo


def detectar_umbrales(datos_json):
    """Extrae los umbrales de alerta (amarilla/naranja/roja) si existen."""
    amarilla = _primer_valor_presente(datos_json, CANDIDATOS_UMBRAL_AMARILLA)
    naranja = _primer_valor_presente(datos_json, CANDIDATOS_UMBRAL_NARANJA)
    roja = _primer_valor_presente(datos_json, CANDIDATOS_UMBRAL_ROJA)
    try:
        amarilla = float(amarilla) if amarilla is not None else None
        naranja = float(naranja) if naranja is not None else None
        roja = float(roja) if roja is not None else None
    except (TypeError, ValueError):
        pass
    return amarilla, naranja, roja


def calcular_estado_alerta(nivel_actual, amarilla, naranja, roja):
    """Compara el nivel actual contra los umbrales y devuelve (etiqueta, color)."""
    if roja is not None and nivel_actual >= roja:
        return "Roja", "🔴"
    if naranja is not None and nivel_actual >= naranja:
        return "Naranja", "🟠"
    if amarilla is not None and nivel_actual >= amarilla:
        return "Amarilla", "🟡"
    if amarilla is not None:  # hay al menos un umbral definido y no se superó
        return "Segura", "🟢"
    return "Sin umbrales definidos", "⚪"


def calcular_indice_calidad(df):
    """Índice simple (0-100) combinando completitud de la serie y proporción de outliers."""
    if df.empty or len(df) < 2:
        return 0.0, 0, 0

    df_idx = df.set_index("fecha")
    frecuencia_tipica = df["fecha"].diff().dropna().mode()
    if len(frecuencia_tipica) == 0:
        return 0.0, 0, 0
    frecuencia_tipica = frecuencia_tipica[0]

    rango_completo = pd.date_range(start=df_idx.index.min(), end=df_idx.index.max(), freq=frecuencia_tipica)
    esperados = len(rango_completo)
    huecos = esperados - len(df_idx)
    completitud = max(0.0, 1 - (huecos / esperados)) if esperados > 0 else 0.0

    Q1, Q3 = df["nivel"].quantile(0.25), df["nivel"].quantile(0.75)
    IQR = Q3 - Q1
    lim_inf, lim_sup = Q1 - 1.5 * IQR, Q3 + 1.5 * IQR
    es_outlier = (df["nivel"] < lim_inf) | (df["nivel"] > lim_sup) | (df["nivel"] < 0)
    proporcion_outliers = es_outlier.mean()

    indice = (completitud * 0.7 + (1 - proporcion_outliers) * 0.3) * 100
    return round(indice, 1), int(huecos), int(es_outlier.sum())


def construir_dataframe(registros, llave_fecha, llave_valor, nombre_col_valor):
    df = pd.DataFrame(registros)
    df = df.rename(columns={llave_fecha: "fecha", llave_valor: nombre_col_valor})
    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce")
    df[nombre_col_valor] = pd.to_numeric(df[nombre_col_valor], errors="coerce")
    df = df.dropna(subset=["fecha", nombre_col_valor]).sort_values("fecha").reset_index(drop=True)
    return df


# ------------------------------------------------------------------
# Sidebar — parámetros de la consulta (editables por cada estudiante)
# ------------------------------------------------------------------
st.sidebar.header("Parámetros de tu consulta")
nombre_estudiante = st.sidebar.text_input("Nombre del estudiante", "Tu Nombre Aquí")
codigo_estacion = st.sidebar.text_input("Código de estación", "42")
fecha_desde = st.sidebar.date_input("Desde", pd.to_datetime("2026-08-23")).strftime("%Y-%m-%d")
fecha_hasta = st.sidebar.date_input("Hasta", pd.to_datetime("2026-08-30")).strftime("%Y-%m-%d")
calidad = st.sidebar.selectbox("Calidad", [1, 0], index=0, help="1 = solo datos validados")
incluir_precipitacion = st.sidebar.checkbox("Incluir precipitación (si la API la ofrece)", value=True)
consultar = st.sidebar.button("🔍 Consultar", type="primary")

st.title("🌊 Nivel de ríos y quebradas — CORNARE")
st.caption(f"Estudiante: **{nombre_estudiante}** · Estación consultada: **{codigo_estacion}**")

# ------------------------------------------------------------------
# Consulta y procesamiento
# ------------------------------------------------------------------
if consultar:
    with st.spinner("Consultando la API..."):
        datos_crudos, error = obtener_serie_nivel(codigo_estacion, fecha_desde, fecha_hasta, calidad)

    if error:
        st.error(f"❌ {error}")
    else:
        registros = obtener_todas_las_paginas(datos_crudos)

        if not registros:
            st.warning("No hay registros para esta estación y rango de fechas. Prueba otro código u otro rango.")
        else:
            df = construir_dataframe(registros, LLAVE_FECHA, LLAVE_VALOR, "nivel")

            lat, lon, coords_reales = detectar_coordenadas(datos_crudos)
            nombre_est, ubicacion_est, tipo_est = detectar_metadatos_estacion(datos_crudos)
            umbral_amarilla, umbral_naranja, umbral_roja = detectar_umbrales(datos_crudos)
            indice_calidad, huecos, n_outliers = calcular_indice_calidad(df)

            nivel_actual = float(df["nivel"].iloc[-1])
            fecha_ultimo = df["fecha"].iloc[-1]
            nivel_maximo = float(df["nivel"].max())
            fecha_maximo = df.loc[df["nivel"].idxmax(), "fecha"]
            etiqueta_alerta, icono_alerta = calcular_estado_alerta(
                nivel_actual, umbral_amarilla, umbral_naranja, umbral_roja
            )

            # --- Encabezado con metadatos de la estación ---
            st.subheader(nombre_est if nombre_est else f"Estación {codigo_estacion}")
            info_bits = []
            if tipo_est:
                info_bits.append(f"Tipo: **{tipo_est}**")
            if ubicacion_est:
                info_bits.append(f"Ubicación: **{ubicacion_est}**")
            info_bits.append(f"Código: **{codigo_estacion}**")
            st.caption(" · ".join(info_bits))
            if not nombre_est and not ubicacion_est:
                st.caption(
                    "La API no trajo nombre/ubicación de la estación con las llaves probadas. "
                    "Revisa `datos_crudos` (por ejemplo con `st.json`) y ajusta "
                    "`CANDIDATOS_NOMBRE` / `CANDIDATOS_UBICACION`."
                )

            # --- Métricas principales ---
            col1, col2, col3, col4, col5 = st.columns(5)
            col1.metric("Lecturas", len(df))
            col2.metric(
                "Nivel actual",
                f"{nivel_actual:.1f}",
                help=f"Último registro: {fecha_ultimo}",
            )
            col3.metric(
                "Máximo del período",
                f"{nivel_maximo:.1f}",
                help=f"Registrado el: {fecha_maximo}",
            )
            col4.metric("Índice de calidad", f"{indice_calidad} / 100")
            col5.metric("Outliers detectados", n_outliers)

            # --- Estado de alerta ---
            st.markdown(f"### Estado actual: {icono_alerta} **{etiqueta_alerta}**")
            if umbral_amarilla or umbral_naranja or umbral_roja:
                cols_umbral = st.columns(3)
                cols_umbral[0].metric("Umbral amarilla", umbral_amarilla if umbral_amarilla else "—")
                cols_umbral[1].metric("Umbral naranja", umbral_naranja if umbral_naranja else "—")
                cols_umbral[2].metric("Umbral roja", umbral_roja if umbral_roja else "—")
            else:
                st.caption(
                    "La API no trajo umbrales de alerta con las llaves probadas. "
                    "Ajusta `CANDIDATOS_UMBRAL_*` si conoces el nombre real de esos campos."
                )

            # --- Gráfico de la serie de nivel, con líneas de umbral si existen ---
            st.subheader("Serie de nivel")
            df_grafico = df.set_index("fecha")[["nivel"]].copy()
            if umbral_amarilla:
                df_grafico["Umbral amarilla"] = umbral_amarilla
            if umbral_naranja:
                df_grafico["Umbral naranja"] = umbral_naranja
            if umbral_roja:
                df_grafico["Umbral roja"] = umbral_roja
            st.line_chart(df_grafico)

            # --- Precipitación (opcional) ---
            if incluir_precipitacion:
                datos_precip, error_precip = obtener_serie(
                    codigo_estacion, ENDPOINT_PRECIPITACION, fecha_desde, fecha_hasta, calidad
                )
                if datos_precip and not error_precip:
                    registros_precip = obtener_todas_las_paginas(datos_precip)
                    if registros_precip:
                        try:
                            df_precip = construir_dataframe(
                                registros_precip, LLAVE_FECHA, LLAVE_VALOR, "precipitacion"
                            )
                            if not df_precip.empty:
                                st.subheader("Precipitación")
                                st.bar_chart(df_precip.set_index("fecha")["precipitacion"])
                        except Exception:
                            st.caption(
                                "No se pudo interpretar la respuesta de precipitación con el "
                                "esquema de llaves actual (LLAVE_FECHA / LLAVE_VALOR)."
                            )
                # Si no hay endpoint de precipitación o falla, no se muestra nada
                # (no se interrumpe la app por una funcionalidad opcional).

            # --- Mapa de la estación ---
            st.subheader("Ubicación de la estación")
            if not coords_reales:
                st.caption(
                    "La API no trajo latitud/longitud de la estación — se muestra el punto de "
                    "partida (Pascual Bravo). Ajusta `CANDIDATOS_LAT` / `CANDIDATOS_LON` si "
                    "conoces el nombre real de esas llaves."
                )
            st.map(pd.DataFrame({"lat": [lat], "lon": [lon]}), zoom=10)

            # --- Detalle de calidad ---
            with st.expander("Detalle del índice de calidad"):
                st.write(f"- Huecos de reporte detectados: **{huecos}**")
                st.write(f"- Outliers (IQR + nivel negativo): **{n_outliers}** de {len(df)} lecturas")
                st.write("El índice combina completitud de la serie (70%) y proporción de datos sin outliers (30%).")

            # --- Metadatos crudos (para depurar qué trae la API) ---
            with st.expander("Ver metadatos crudos de la estación (depuración)"):
                claves_raiz = {k: v for k, v in datos_crudos.items() if k != "values" and k != "next"} \
                    if isinstance(datos_crudos, dict) else {}
                st.json(claves_raiz if claves_raiz else {"info": "Sin llaves raíz adicionales en la respuesta."})

            # --- Tabla y descarga ---
            with st.expander("Ver datos crudos de nivel"):
                st.dataframe(df, use_container_width=True)

            csv = df.to_csv(index=False).encode("utf-8")
            st.download_button(
                "⬇️ Descargar CSV",
                csv,
                file_name=f"nivel_estacion_{codigo_estacion}.csv",
                mime="text/csv",
            )
else:
    st.info("Ajusta los parámetros en el sidebar y presiona **Consultar**.")
