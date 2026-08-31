
from datetime import datetime, timedelta

import requests
import pandas as pd
import streamlit as st
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ------------------------------------------------------------------
# Datos fijos de la estación (tomados de la ficha en MARCO)
# ------------------------------------------------------------------
CODIGO_ESTACION = "28"
NOMBRE_ESTACION = "San Carlos, Río San Carlos"
RED_ESTACION = "Red Agua"
TIPO_ESTACION = "Hidrometeorológica"
UBICACION_ESTACION = "San Carlos, Río San Carlos, Puente Entrada San Carlos"
LAT_ESTACION = 6.1852
LON_ESTACION = -74.9971

# Ventana de consulta fija (últimos 7 días, calculados al momento de abrir la app)
DIAS_A_CONSULTAR = 7
FECHA_HASTA = datetime.now().strftime("%Y-%m-%d")
FECHA_DESDE = (datetime.now() - timedelta(days=DIAS_A_CONSULTAR)).strftime("%Y-%m-%d")
CALIDAD = 1  # 1 = solo datos validados
INCLUIR_PRECIPITACION = True

# Umbrales de alerta (cm). MARCO los muestra como líneas de color en la
# gráfica "Nivel corriente de agua" (Amarilla / Naranja / Roja) pero no
# vienen en la respuesta JSON de /nivel. Si logras identificar los
# valores exactos, reemplázalos aquí y se activa el semáforo solo.
UMBRAL_AMARILLA = None
UMBRAL_NARANJA = None
UMBRAL_ROJA = None

API_BASE_URL = "https://marco.cornare.gov.co/api/v1/estaciones"
LLAVE_FECHA = "level_date"
LLAVE_VALOR = "level"
ENDPOINT_PRECIPITACION = "precipitacion"

st.set_page_config(
    page_title=f"{NOMBRE_ESTACION} — CORNARE",
    page_icon="🌊",
    layout="wide",
)


# ------------------------------------------------------------------
# Funciones de consulta
# ------------------------------------------------------------------
def obtener_serie(recurso, desde, hasta, calidad=1, timeout=30):
    """Consulta /estaciones/{CODIGO_ESTACION}/{recurso} (nivel o precipitacion)."""
    url = f"{API_BASE_URL}/{CODIGO_ESTACION}/{recurso}"
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


def obtener_serie_nivel(desde, hasta, calidad=1, timeout=30):
    return obtener_serie("nivel", desde, hasta, calidad, timeout)


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


def construir_dataframe(registros, llave_fecha, llave_valor, nombre_col_valor):
    df = pd.DataFrame(registros)
    df = df.rename(columns={llave_fecha: "fecha", llave_valor: nombre_col_valor})
    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce")
    df[nombre_col_valor] = pd.to_numeric(df[nombre_col_valor], errors="coerce")
    df = df.dropna(subset=["fecha", nombre_col_valor]).sort_values("fecha").reset_index(drop=True)
    return df


def calcular_estado_alerta(nivel_actual, amarilla, naranja, roja):
    """Compara el nivel actual contra los umbrales y devuelve (etiqueta, ícono)."""
    if roja is not None and nivel_actual >= roja:
        return "Roja", "🔴"
    if naranja is not None and nivel_actual >= naranja:
        return "Naranja", "🟠"
    if amarilla is not None and nivel_actual >= amarilla:
        return "Amarilla", "🟡"
    if amarilla is not None:
        return "Segura", "🟢"
    return "Umbrales no definidos", "⚪"


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


# ------------------------------------------------------------------
# Encabezado — sin sidebar de parámetros de búsqueda
# ------------------------------------------------------------------
st.title(f"🌊 {NOMBRE_ESTACION}")
st.caption(
    f"Código: **{CODIGO_ESTACION}** ({RED_ESTACION}) · Tipo: **{TIPO_ESTACION}** · "
    f"Ubicación: **{UBICACION_ESTACION}**"
)
st.caption(f"Período consultado: **{FECHA_DESDE}** a **{FECHA_HASTA}** (últimos {DIAS_A_CONSULTAR} días)")

# ------------------------------------------------------------------
# Carga automática de datos: sin botones ni parámetros que ajustar.
# ------------------------------------------------------------------
with st.spinner("Consultando la API de CORNARE..."):
    datos_crudos, error = obtener_serie_nivel(FECHA_DESDE, FECHA_HASTA, CALIDAD)

if error:
    st.error(f"❌ {error}")
else:
    registros = obtener_todas_las_paginas(datos_crudos)

    if not registros:
        st.warning("No hay registros para los últimos días.")
    else:
        df = construir_dataframe(registros, LLAVE_FECHA, LLAVE_VALOR, "nivel")
        indice_calidad, huecos, n_outliers = calcular_indice_calidad(df)

        # Nivel actual y máximo: la API de /nivel ya los trae listos.
        nivel_actual = datos_crudos.get("current_level")
        fecha_ultimo = datos_crudos.get("current_level_date")
        nivel_maximo = datos_crudos.get("max_level")
        fecha_maximo = datos_crudos.get("max_level_date")

        if nivel_actual is None:
            nivel_actual = float(df["nivel"].iloc[-1])
            fecha_ultimo = df["fecha"].iloc[-1]
        if nivel_maximo is None:
            nivel_maximo = float(df["nivel"].max())
            fecha_maximo = df.loc[df["nivel"].idxmax(), "fecha"]

        nivel_actual = float(nivel_actual)
        nivel_maximo = float(nivel_maximo)

        etiqueta_alerta, icono_alerta = calcular_estado_alerta(
            nivel_actual, UMBRAL_AMARILLA, UMBRAL_NARANJA, UMBRAL_ROJA
        )

        # --- Métricas principales ---
        col1, col2, col3, col4, col5 = st.columns(5)
        col1.metric("Lecturas", len(df))
        col2.metric("Nivel actual (cm)", f"{nivel_actual:.1f}", help=f"Último registro: {fecha_ultimo}")
        col3.metric("Máximo del período (cm)", f"{nivel_maximo:.1f}", help=f"Registrado el: {fecha_maximo}")
        col4.metric("Índice de calidad", f"{indice_calidad} / 100")
        col5.metric("Outliers detectados", n_outliers)

        # --- Estado de alerta ---
        st.markdown(f"### Estado actual: {icono_alerta} **{etiqueta_alerta}**")
        if UMBRAL_AMARILLA or UMBRAL_NARANJA or UMBRAL_ROJA:
            cols_umbral = st.columns(3)
            cols_umbral[0].metric("Umbral amarilla", UMBRAL_AMARILLA if UMBRAL_AMARILLA else "—")
            cols_umbral[1].metric("Umbral naranja", UMBRAL_NARANJA if UMBRAL_NARANJA else "—")
            cols_umbral[2].metric("Umbral roja", UMBRAL_ROJA if UMBRAL_ROJA else "—")
        else:
            st.caption(
                "Los umbrales de alerta (Amarilla/Naranja/Roja) no vienen en la respuesta de "
                "/nivel. Complétalos en `UMBRAL_AMARILLA/NARANJA/ROJA` si los encuentras."
            )

        # --- Gráfico de la serie de nivel, con líneas de umbral si existen ---
        st.subheader("Nivel corriente de agua")
        df_grafico = df.set_index("fecha")[["nivel"]].copy()
        if UMBRAL_AMARILLA:
            df_grafico["Umbral amarilla"] = UMBRAL_AMARILLA
        if UMBRAL_NARANJA:
            df_grafico["Umbral naranja"] = UMBRAL_NARANJA
        if UMBRAL_ROJA:
            df_grafico["Umbral roja"] = UMBRAL_ROJA
        st.line_chart(df_grafico)

        # --- Precipitación ---
        if INCLUIR_PRECIPITACION:
            datos_precip, error_precip = obtener_serie(
                ENDPOINT_PRECIPITACION, FECHA_DESDE, FECHA_HASTA, CALIDAD
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

        # --- Mapa de la estación ---
        st.subheader("Ubicación de la estación")
        st.map(pd.DataFrame({"lat": [LAT_ESTACION], "lon": [LON_ESTACION]}), zoom=13)

        # --- Detalle de calidad ---
        with st.expander("Detalle del índice de calidad"):
            st.write(f"- Huecos de reporte detectados: **{huecos}**")
            st.write(f"- Outliers (IQR + nivel negativo): **{n_outliers}** de {len(df)} lecturas")
            st.write("El índice combina completitud de la serie (70%) y proporción de datos sin outliers (30%).")

        # --- Metadatos crudos (para depurar qué trae la API) ---
        with st.expander("Ver metadatos crudos de /nivel (depuración)"):
            claves_raiz = {k: v for k, v in datos_crudos.items() if k not in ("values", "next")} \
                if isinstance(datos_crudos, dict) else {}
            st.json(claves_raiz if claves_raiz else {"info": "Sin llaves raíz adicionales."})

        # --- Tabla y descarga ---
        with st.expander("Ver datos crudos de nivel"):
            st.dataframe(df, use_container_width=True)

        csv = df.to_csv(index=False).encode("utf-8")
        st.download_button(
            "⬇️ Descargar CSV",
            csv,
            file_name=f"nivel_estacion_{CODIGO_ESTACION}.csv",
            mime="text/csv",
        )
