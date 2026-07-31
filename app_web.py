import os
import math
import random
import re
import uuid
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode, urlparse, parse_qsl, urlunparse

import pandas as pd
import streamlit as st
from streamlit_gsheets import GSheetsConnection
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle, PageBreak

try:
    import mercadopago
except Exception:
    mercadopago = None

try:
    import stripe
except Exception:
    stripe = None

try:
    from supabase import create_client, Client
except Exception:
    create_client = None
    Client = None

# ============================================================
# CONFIGURACION GENERAL
# Cambia solamente estas variables.
# ============================================================
TOTAL_BOLETOS = 100
PRECIO_BOLETO = 15.00
NOMBRE_EVENTO = "Gran Rifa"
FECHA_VIGENCIA_BOLETO = "31/12/2026"
MENSAJE_GENERAL_BOLETO = (
    "Este documento acredita la participacion del boleto indicado en el evento. "
    "La vigencia aplica hasta la fecha señalada y el boleto debe conservarse como comprobante. "
    "La validez del boleto queda sujeta a que el pago se encuentre confirmado en el sistema."
)

TIEMPO_RESERVA_MINUTOS = 1440
TIEMPO_PRERESERVA_MINUTOS = 15
CLIENT_ID_PARAM = "cid"
SHEETS_TTL_MAPA_SEGUNDOS = 25
SHEETS_TTL_LECTURA_SEGUNDOS = 6

DIGITOS_BOLETO = max(1, len(str(max(int(TOTAL_BOLETOS) - 1, 0))))
COLUMNAS_MAPA = max(1, math.ceil(math.sqrt(max(int(TOTAL_BOLETOS), 1))))
FILAS_MAPA = COLUMNAS_MAPA


def obtener_config(nombre: str, default: str = "") -> str:
    try:
        if hasattr(st, "secrets") and nombre in st.secrets:
            return str(st.secrets[nombre]).strip()
    except Exception:
        pass
    return str(os.getenv(nombre, default)).strip()


def normalizar_supabase_project_url(url: str) -> str:
    """Normaliza SUPABASE_URL para que sea solo la URL raiz del proyecto."""
    url = str(url or "").strip().strip(' "\'')
    if not url:
        return ""
    url = url.rstrip("/")
    if url.endswith("/rest/v1"):
        url = url[:-8]
    return url.rstrip("/")


MP_ACCESS_TOKEN = obtener_config("MP_ACCESS_TOKEN")
MP_NOTIFICATION_URL = obtener_config("MP_NOTIFICATION_URL")
MP_RETURN_URL = obtener_config("MP_RETURN_URL")
MP_CURRENCY_ID = obtener_config("MP_CURRENCY_ID", "MXN").upper()
STRIPE_SECRET_KEY = obtener_config("STRIPE_SECRET_KEY")
STRIPE_RETURN_URL = obtener_config("STRIPE_RETURN_URL")
STRIPE_CURRENCY_ID = obtener_config("STRIPE_CURRENCY_ID", "mxn").lower()
DEBUG_PAGOS = obtener_config("DEBUG_PAGOS", "false").lower() in ["1", "true", "si", "yes", "on"]
SUPABASE_URL = normalizar_supabase_project_url(obtener_config("SUPABASE_URL"))
SUPABASE_SERVICE_ROLE_KEY = obtener_config("SUPABASE_SERVICE_ROLE_KEY")
USAR_SUPABASE_TRANSACCIONAL = obtener_config("USAR_SUPABASE_TRANSACCIONAL", "true").lower() in ["1", "true", "si", "yes", "on"]
SUPABASE_TTL_MAPA_SEGUNDOS = int(obtener_config("SUPABASE_TTL_MAPA_SEGUNDOS", "5"))
COPIAR_SHEETS_DESDE_APP = obtener_config("COPIAR_SHEETS_DESDE_APP", "false").lower() in ["1", "true", "si", "yes", "on"]

sdk = mercadopago.SDK(MP_ACCESS_TOKEN) if (MP_ACCESS_TOKEN and mercadopago is not None) else None
if STRIPE_SECRET_KEY and stripe is not None:
    stripe.api_key = STRIPE_SECRET_KEY

supabase: Optional[Client] = None
if USAR_SUPABASE_TRANSACCIONAL and create_client is not None and SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    except Exception:
        supabase = None


CSS_CUSTOM = """
<style>
[data-testid="column"] { padding: 0 4px !important; }
[data-testid="stButton"] button,
[data-testid="stLinkButton"] a {
    width: 100%; min-height: 52px; border-radius: 13px !important; font-weight: 850 !important;
    transition: transform .16s ease, box-shadow .16s ease, filter .16s ease !important;
    box-shadow: 0 3px 9px rgba(15, 23, 42, .12) !important;
}
[data-testid="stButton"] button:hover,
[data-testid="stLinkButton"] a:hover {
    transform: translateY(-2px) scale(1.01) !important;
    box-shadow: 0 8px 18px rgba(15, 23, 42, .18) !important;
    filter: brightness(1.03) !important;
}
.metric-container { display:flex; gap:10px; margin-bottom:20px; flex-wrap:wrap; }
.metric-box { flex:1; min-width:120px; background:white; padding:10px; border-radius:8px; text-align:center; box-shadow:0 2px 5px rgba(0,0,0,.05); border-top:4px solid; }
.metric-box h2 { margin:0; font-size:20px; font-weight:800; color:#0A2540; }
.metric-box p { margin:0; font-size:12px; color:#64748B; font-weight:600; }
.m-green { border-color:#20C997; }.m-gray { border-color:#94A3B8; }.m-yellow { border-color:#F59E0B; }.m-red { border-color:#EF4444; }
.st-key-btn_confirmar_metodo_pago button,
.st-key-btn_verificar_pago_pdf button,
.st-key-btn_generar_link_pago button {
    background:linear-gradient(135deg,#DC2626,#7F1D1D)!important; color:#fff!important; border:0!important;
    border-radius:14px!important; box-shadow:0 7px 18px rgba(220,38,38,.36)!important; font-weight:900!important; min-height:54px!important;
}
.st-key-mapa_boletos_grid [data-testid="stHorizontalBlock"] { display:grid!important; grid-template-columns:repeat({COLUMNAS_MAPA}, minmax(44px,1fr))!important; gap:7px!important; align-items:stretch!important; }
.st-key-mapa_boletos_grid [data-testid="column"] { width:100%!important; min-width:0!important; flex:unset!important; padding:0!important; }
.st-key-mapa_boletos_grid [data-testid="stButton"] button { min-height:58px!important; border-radius:14px!important; font-size:13px!important; white-space:pre-line!important; }
@media(max-width:900px){
    .st-key-mapa_boletos_grid [data-testid="stHorizontalBlock"] { grid-template-columns:repeat({COLUMNAS_MAPA}, minmax(32px,1fr))!important; gap:5px!important; }
    .st-key-mapa_boletos_grid [data-testid="stButton"] button { min-height:48px!important; font-size:10.5px!important; padding:1px!important; }
    .metric-container { display:grid!important; grid-template-columns:repeat(2,minmax(120px,1fr))!important; gap:8px!important; }
}
@media(max-width:540px){
    .st-key-mapa_boletos_grid [data-testid="stHorizontalBlock"] { grid-template-columns:repeat({COLUMNAS_MAPA}, minmax(24px,1fr))!important; gap:3px!important; }
    .st-key-mapa_boletos_grid [data-testid="stButton"] button { min-height:40px!important; font-size:9px!important; padding:0!important; }
}

.st-key-btn_elegir_mp button,
.st-key-btn_elegir_stripe button {
    min-height: 64px !important;
    border-radius: 16px !important;
    border-width: 1.8px !important;
    border-style: dashed !important;
    font-weight: 950 !important;
    letter-spacing: .2px !important;
}
.st-key-btn_elegir_mp button {
    background: linear-gradient(145deg,#D1FAE5,#ECFDF5) !important;
    border-color: #10B981 !important;
    color: #064E3B !important;
}
.st-key-btn_elegir_stripe button {
    background: linear-gradient(145deg,#DBEAFE,#EFF6FF) !important;
    border-color: #2563EB !important;
    color: #1E3A8A !important;
}
.st-key-btn_elegir_mp button:hover,
.st-key-btn_elegir_stripe button:hover {
    transform: translateY(-2px) scale(1.01) !important;
    box-shadow: 0 10px 22px rgba(15,23,42,.18) !important;
}
.st-key-btn_cancelar_checkout_pendiente button {
    background: linear-gradient(145deg,#F8FAFC,#E2E8F0) !important;
    border-color: #94A3B8 !important;
    color: #334155 !important;
    border-style: dashed !important;
}


/* ============================================================
   BOLETO SELECCIONADO EN NEGRO Y PAGO RAPIDO
   ============================================================ */
.st-key-mapa_boletos_grid [data-testid="stButton"] button[kind="primary"] {
    background: linear-gradient(135deg, #111827, #000000) !important;
    color: #FFFFFF !important;
    border: 2px dashed #000000 !important;
    box-shadow: 0 5px 12px rgba(0,0,0,0.30) !important;
}
.st-key-mapa_boletos_grid [data-testid="stButton"] button[kind="primary"]:hover {
    background: linear-gradient(135deg, #000000, #1F2937) !important;
    transform: translateY(-2px) scale(1.03) !important;
}
.st-key-btn_realizar_pago_directo a,
.st-key-btn_realizar_pago_directo button,
[data-testid="stLinkButton"] a {
    min-height: 56px !important;
    border-radius: 14px !important;
    background: linear-gradient(135deg,#DC2626,#7F1D1D) !important;
    color: #FFFFFF !important;
    border: 0 !important;
    font-weight: 950 !important;
    box-shadow: 0 7px 18px rgba(220,38,38,.32) !important;
    transition: all .14s ease-in-out !important;
}

</style>
"""

# ============================================================
# UTILIDADES
# ============================================================

def formatear_numero_boleto(valor: Any) -> str:
    try:
        return f"{int(float(valor)):0{DIGITOS_BOLETO}d}"
    except Exception:
        return str(valor).strip().zfill(DIGITOS_BOLETO)


def parse_ticket_number(valor: Any) -> str:
    if pd.isna(valor) or str(valor).strip() == "":
        return ""
    return formatear_numero_boleto(valor)


def limpiar_valor_id(valor: Any) -> str:
    valor = str(valor or "").strip()
    return "" if valor.lower() in ["nan", "none", "null", "nat"] else valor


def normalizar_importe(valor: Any) -> float:
    try:
        return round(float(valor or 0), 2)
    except Exception:
        return 0.0


def normalizar_url(url: str) -> str:
    url = str(url or "").strip().strip(' \"\'()')
    while url.startswith("("):
        url = url[1:].strip()
    while url.endswith(")"):
        url = url[:-1].strip()
    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def agregar_parametros_url(url: str, parametros: Dict[str, str]) -> str:
    url = normalizar_url(url)
    partes = urlparse(url)
    query_actual = dict(parse_qsl(partes.query, keep_blank_values=True))
    query_actual.update({k: str(v) for k, v in parametros.items() if v is not None})
    nueva_query = urlencode(query_actual).replace("%7BCHECKOUT_SESSION_ID%7D", "{CHECKOUT_SESSION_ID}")
    return urlunparse((partes.scheme, partes.netloc, partes.path, partes.params, nueva_query, partes.fragment))


def qp_get(qp: Any, nombre: str, default: str = "") -> str:
    try:
        valor = qp.get(nombre, default)
        return str(valor[0]) if isinstance(valor, list) and valor else str(valor)
    except Exception:
        return default


def obtener_client_id_url() -> str:
    try:
        valor = st.query_params.get(CLIENT_ID_PARAM, "")
        if isinstance(valor, list):
            valor = valor[0] if valor else ""
        return limpiar_valor_id(valor)
    except Exception:
        return ""


def asegurar_client_id_en_url() -> str:
    cid_url = obtener_client_id_url()
    if cid_url:
        st.session_state.session_id = cid_url
        return cid_url
    cid_actual = limpiar_valor_id(st.session_state.get("session_id", "")) or str(uuid.uuid4())
    st.session_state.session_id = cid_actual
    try:
        st.query_params[CLIENT_ID_PARAM] = cid_actual
    except Exception:
        pass
    return cid_actual


def parametros_retorno_pago(extra: Dict[str, str]) -> Dict[str, str]:
    params = dict(extra or {})
    cid = limpiar_valor_id(st.session_state.get("session_id", "")) or obtener_client_id_url()
    if cid:
        params[CLIENT_ID_PARAM] = cid
    params["return_to"] = "carrito"
    return params


def limpiar_query_manteniendo_cid():
    cid = limpiar_valor_id(st.session_state.get("session_id", "")) or obtener_client_id_url()
    try:
        st.query_params.clear()
        if cid:
            st.query_params[CLIENT_ID_PARAM] = cid
    except Exception:
        pass



def normalizar_fecha_unix(fecha_txt: str, dias_antes: int = 5, dias_despues: int = 5) -> dict:
    try:
        fecha = pd.to_datetime(str(fecha_txt)).to_pydatetime()
    except Exception:
        fecha = datetime.now()
    return {"gte": int((fecha - timedelta(days=dias_antes)).timestamp()), "lte": int((fecha + timedelta(days=dias_despues)).timestamp())}


def safe_to_dict(obj: Any) -> Dict[str, Any]:
    try:
        if obj is None:
            return {}
        if isinstance(obj, dict):
            return obj
        if hasattr(obj, "to_dict_recursive"):
            return obj.to_dict_recursive()
        if hasattr(obj, "to_dict"):
            return obj.to_dict()
        return dict(obj) if obj else {}
    except Exception:
        return {}


def safe_get(obj: Any, key: str, default=None):
    data = safe_to_dict(obj)
    if data:
        return data.get(key, default)
    try:
        return getattr(obj, key)
    except Exception:
        return default


def safe_data_list(obj: Any) -> list:
    data = safe_get(obj, "data", [])
    if data is None:
        return []
    if isinstance(data, list):
        return data
    try:
        return list(data)
    except Exception:
        return []


# ============================================================
# SUPABASE TRANSACCIONAL, CANCELACION Y OPTIMIZACION DE VELOCIDAD
# ============================================================

def supabase_activo() -> bool:
    return bool(USAR_SUPABASE_TRANSACCIONAL and supabase is not None)


def limpiar_cache_mapa():
    for key in ["_mapa_cache_ts", "_mapa_cache_ventas", "_mapa_cache_reservas"]:
        if key in st.session_state:
            del st.session_state[key]


def normalizar_venta_supabase(v: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "ID_Boleto": v.get("id_boleto", ""),
        "Nombre": v.get("nombre", ""),
        "Correo": v.get("correo", ""),
        "Evento": v.get("evento", NOMBRE_EVENTO),
        "Numero_Boleto": parse_ticket_number(v.get("numero_boleto", "")),
        "Precio": v.get("precio", ""),
        "Metodo_Pago": v.get("metodo_pago", ""),
        "Codigo_Pago": v.get("codigo_pago", ""),
        "Fecha_Compra": v.get("fecha_compra", ""),
        "Numero_Telefonico": v.get("telefono", ""),
        "Estado_Pago": v.get("estado_pago", "VENDIDO"),
        "Referencia_Pago": v.get("external_reference", ""),
        "MercadoPago_Payment_ID": v.get("mercadopago_payment_id", ""),
        "MercadoPago_Preference_ID": v.get("mercadopago_preference_id", ""),
        "Stripe_Payment_ID": v.get("stripe_payment_id", ""),
        "Stripe_Session_ID": v.get("stripe_session_id", ""),
        "Proveedor_Pago": v.get("proveedor_pago", ""),
    }


def reservar_boletos_supabase(ordenes: List[Dict[str, Any]]) -> Tuple[bool, str]:
    if not supabase_activo():
        return False, "Supabase no esta configurado en Streamlit. Revisa SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY y requirements.txt."
    if not ordenes:
        return False, "No hay boletos para reservar en Supabase."

    ref = limpiar_valor_id(ordenes[0].get("External_Reference", ""))
    nombre = limpiar_valor_id(ordenes[0].get("Nombre", ""))
    correo = limpiar_valor_id(ordenes[0].get("Correo", "")).lower()
    telefono = limpiar_valor_id(ordenes[0].get("Numero_Telefonico", ""))
    boletos = []
    for orden in ordenes:
        boleto = parse_ticket_number(orden.get("Numero_Boleto", ""))
        if boleto and boleto not in boletos:
            boletos.append(boleto)
    try:
        precio = float(ordenes[0].get("Monto", PRECIO_BOLETO) or PRECIO_BOLETO)
    except Exception:
        precio = float(PRECIO_BOLETO)
    if not ref or not boletos:
        return False, "Reserva Supabase invalida: falta referencia o boletos."

    params = {
        "p_external_reference": ref,
        "p_boletos": boletos,
        "p_nombre": nombre,
        "p_correo": correo,
        "p_telefono": telefono,
        "p_precio": precio,
        "p_minutos_reserva": int(TIEMPO_RESERVA_MINUTOS),
    }
    try:
        supabase.rpc("reservar_boletos", params).execute()
        return True, "Exito Supabase"
    except Exception as e:
        return False, f"Error Supabase al reservar: {e}"


def cancelar_reserva_supabase_por_referencia(external_reference: str, motivo: str = "CANCELADO_USUARIO") -> Tuple[bool, str]:
    """
    Libera una reserva pendiente en Supabase cuando el usuario limpia carrito
    o regresa sin completar el pago.
    No toca boletos vendidos.
    """
    if not supabase_activo():
        return False, "Supabase no esta activo."

    ref = limpiar_valor_id(external_reference)
    if not ref:
        return False, "No hay external_reference para cancelar."

    try:
        reservas = (
            supabase.table("reservas")
            .select("numero_boleto,estado_reserva")
            .eq("external_reference", ref)
            .eq("estado_reserva", "PENDIENTE")
            .execute()
            .data
            or []
        )

        boletos = []
        for row in reservas:
            boleto = parse_ticket_number(row.get("numero_boleto", ""))
            if boleto and boleto not in boletos:
                boletos.append(boleto)

        ahora_iso = datetime.now().isoformat()

        if boletos:
            supabase.table("reservas").update({
                "estado_reserva": motivo,
                "fecha_actualizacion": ahora_iso,
            }).eq("external_reference", ref).eq("estado_reserva", "PENDIENTE").execute()

            for boleto in boletos:
                supabase.table("boletos").update({
                    "estado": "DISPONIBLE",
                    "external_reference_actual": None,
                    "fecha_actualizacion": ahora_iso,
                }).eq("numero_boleto", boleto).eq("estado", "RESERVADO").eq("external_reference_actual", ref).execute()

        limpiar_cache_mapa()
        return True, "Reserva liberada correctamente."
    except Exception as e:
        return False, f"Error liberando reserva en Supabase: {e}"


def leer_mapa_supabase() -> Tuple[pd.DataFrame, pd.DataFrame]:
    if not supabase_activo():
        return pd.DataFrame(columns=columnas_ventas()), pd.DataFrame(columns=columnas_reservas())
    try:
        boletos_data = supabase.table("boletos").select("numero_boleto,estado,external_reference_actual,fecha_actualizacion").execute().data or []
        ventas_rows = []
        reservas_rows = []
        for row in boletos_data:
            num = parse_ticket_number(row.get("numero_boleto", ""))
            estado = str(row.get("estado", "") or "").upper()
            ref = limpiar_valor_id(row.get("external_reference_actual", ""))
            if estado == "VENDIDO":
                ventas_rows.append({"Numero_Boleto": num, "Estado_Pago": "VENDIDO", "Referencia_Pago": ref})
            elif estado == "RESERVADO":
                reservas_rows.append({
                    "External_Reference": ref,
                    "Numero_Boleto": num,
                    "Estado_Reserva": "PENDIENTE",
                    "Expira_En": (datetime.now() + timedelta(minutes=TIEMPO_RESERVA_MINUTOS)).strftime("%Y-%m-%d %H:%M:%S"),
                })
        return asegurar_columnas(pd.DataFrame(ventas_rows), columnas_ventas()), asegurar_columnas(pd.DataFrame(reservas_rows), columnas_reservas())
    except Exception as e:
        st.session_state["_sheet_read_error"] = f"Error leyendo mapa desde Supabase: {e}. Revisa que SUPABASE_URL sea solo https://xxxx.supabase.co, sin /rest/v1 ni rutas del dashboard."
        return pd.DataFrame(columns=columnas_ventas()), pd.DataFrame(columns=columnas_reservas())


def obtener_ventas_supabase_por_referencia(external_reference: str) -> List[Dict[str, Any]]:
    if not supabase_activo():
        return []
    ref = limpiar_valor_id(external_reference)
    if not ref:
        return []
    try:
        data = (
            supabase.table("ventas")
            .select("id_boleto,nombre,correo,evento,numero_boleto,precio,metodo_pago,codigo_pago,fecha_compra,telefono,estado_pago,external_reference,stripe_payment_id,stripe_session_id,mercadopago_payment_id,mercadopago_preference_id,proveedor_pago")
            .eq("external_reference", ref)
            .order("numero_boleto")
            .execute()
            .data
            or []
        )
        return [normalizar_venta_supabase(v) for v in data]
    except Exception as e:
        st.session_state.ultimo_error_pago = f"Consulta ventas Supabase por referencia: {e}"
        return []


def consultar_ventas_supabase_por_boleto_correo(numero_boleto: str, correo: str) -> List[Dict[str, Any]]:
    if not supabase_activo():
        return []
    try:
        num = parse_ticket_number(numero_boleto)
        correo_limpio = str(correo or "").strip().lower()
        data = (
            supabase.table("ventas")
            .select("external_reference")
            .eq("numero_boleto", num)
            .eq("correo", correo_limpio)
            .order("fecha_compra", desc=True)
            .limit(1)
            .execute()
            .data
            or []
        )
        if not data:
            return []
        ref = data[0].get("external_reference", "")
        return obtener_ventas_supabase_por_referencia(ref)
    except Exception:
        return []


def confirmar_pago_supabase_desde_app(payment_info: Dict[str, Any]) -> Tuple[bool, str]:
    if not supabase_activo():
        return False, "Supabase no esta activo en la app."
    try:
        proveedor = obtener_proveedor_pago(payment_info)
        ext_ref = obtener_external_reference_pago(payment_info)
        pago_id = obtener_payment_id_pago(payment_info)
        monto_pagado = obtener_monto_pagado(payment_info)
        moneda = obtener_moneda_pago(payment_info)
        session_id = limpiar_valor_id(payment_info.get("provider_session_id", "")) or limpiar_valor_id(payment_info.get("stripe_session_id", ""))
        preference_id = limpiar_valor_id(payment_info.get("mp_preference_id", "")) or limpiar_valor_id(payment_info.get("preference_id", ""))
        metodo_pago = limpiar_valor_id(payment_info.get("payment_type_id", proveedor.lower()))
        if not ext_ref:
            return False, "No se recibio external_reference."
        if not pago_id:
            return False, "No se recibio payment_id."
        params = {
            "p_provider": proveedor,
            "p_external_reference": ext_ref,
            "p_payment_id": pago_id,
            "p_session_id": session_id,
            "p_preference_id": preference_id,
            "p_monto_pagado": monto_pagado,
            "p_moneda": moneda,
            "p_metodo_pago": metodo_pago,
            "p_evento": NOMBRE_EVENTO,
            "p_payload": {},
        }
        supabase.rpc("confirmar_pago_y_vender", params).execute()
        return True, "Pago confirmado en Supabase."
    except Exception as e:
        st.session_state.ultimo_error_pago = f"Confirmacion Supabase desde app: {e}"
        return False, str(e)


def finalizar_pago_confirmado_app(conn: GSheetsConnection, pago: Dict[str, Any], external_reference: str) -> List[Dict[str, Any]]:
    ref = limpiar_valor_id(external_reference) or obtener_external_reference_pago(pago)
    if supabase_activo():
        confirmar_pago_supabase_desde_app(pago)
        ventas_supabase = obtener_ventas_supabase_por_referencia(ref)
        if ventas_supabase:
            return ventas_supabase
    datos_sheets = actualizar_pago_en_hojas(conn, pago)
    if datos_sheets:
        return datos_sheets
    if supabase_activo():
        ventas_supabase = obtener_ventas_supabase_por_referencia(ref)
        if ventas_supabase:
            return ventas_supabase
    return []


def formatear_fecha_pdf(valor: Any) -> str:
    texto = str(valor or "").strip()
    if not texto:
        return datetime.now().strftime("%Y-%m-%d")
    texto = texto.replace("T", " ")
    return texto.split(" ")[0]

# ============================================================
# GOOGLE SHEETS CONTROL DE CUOTA
# ============================================================

def es_error_cuota_sheets(error: Exception) -> bool:
    texto = str(error).lower()
    return "quota exceeded" in texto or "rate_limit_exceeded" in texto or "resource_exhausted" in texto or "429" in texto


def registrar_error_lectura_sheets(error: Exception, worksheet: str):
    if es_error_cuota_sheets(error):
        st.session_state["_sheet_read_error"] = f"Google Sheets alcanzo el limite temporal de lecturas al consultar '{worksheet}'. Espera 60 segundos o intenta nuevamente."
    else:
        st.session_state["_sheet_read_error"] = f"Error leyendo hoja '{worksheet}': {error}"


def obtener_error_lectura_sheets() -> str:
    return str(st.session_state.get("_sheet_read_error", "") or "")


def limpiar_error_lectura_sheets():
    st.session_state["_sheet_read_error"] = ""

# ============================================================
# ESTADO LOCAL DE CARRITO Y CHECKOUT FLUIDO
# ============================================================

@st.cache_resource
def obtener_pre_reservas_globales() -> dict:
    return {}


def limpiar_pre_reservas_expiradas(pre_reservas: dict):
    ahora = datetime.now()
    for boleto, info in list(pre_reservas.items()):
        try:
            if info.get("expires_at") < ahora:
                del pre_reservas[boleto]
        except Exception:
            del pre_reservas[boleto]


def limpiar_enlaces_pago_sin_vaciar_carrito():
    st.session_state.pago_generado_url = None
    st.session_state.stripe_pago_url = None
    st.session_state.stripe_session_id = None
    st.session_state.payment_provider = None
    st.session_state.external_ref_activa = None


def limpiar_checkout_pendiente():
    st.session_state.checkout_pendiente = None
    st.session_state.checkout_proveedor = "Mercado Pago"
    st.session_state.checkout_metodo_elegido = ""



def alternar_boleto_mapa(numero_boleto: str):
    """Actualiza el carrito visual antes de renderizar, evitando un st.rerun extra por cada click."""
    asegurar_client_id_en_url()
    pre_reservas = obtener_pre_reservas_globales()
    limpiar_pre_reservas_expiradas(pre_reservas)
    mi_sesion = st.session_state.get("session_id", "")
    numero_boleto = parse_ticket_number(numero_boleto)

    if numero_boleto in pre_reservas and pre_reservas[numero_boleto].get("session_id") == mi_sesion:
        pre_reservas.pop(numero_boleto, None)
        if numero_boleto in st.session_state.selected_tickets:
            st.session_state.selected_tickets.remove(numero_boleto)
        return

    pre_reservas[numero_boleto] = {
        "session_id": mi_sesion,
        "expires_at": datetime.now() + timedelta(minutes=TIEMPO_PRERESERVA_MINUTOS)
    }
    if numero_boleto not in st.session_state.selected_tickets:
        st.session_state.selected_tickets.append(numero_boleto)


def limpiar_carrito_local(cancelar_reserva_supabase: bool = True):
    """
    Limpia carrito local y, si corresponde, libera tambien la reserva pendiente en Supabase.

    Si el pago ya fue confirmado, no cancela la reserva.
    Si el usuario limpia carrito antes de pagar, libera boletos en Supabase.
    """
    ref_para_cancelar = ""

    checkout = st.session_state.get("checkout_pendiente")
    if isinstance(checkout, dict):
        ref_para_cancelar = limpiar_valor_id(checkout.get("external_reference", ""))

    if not ref_para_cancelar:
        ref_para_cancelar = limpiar_valor_id(st.session_state.get("external_ref_activa", ""))

    pago_confirmado = st.session_state.get("payment_success_id") == "PAGO_CONFIRMADO"

    if cancelar_reserva_supabase and not pago_confirmado and ref_para_cancelar:
        try:
            cancelar_reserva_supabase_por_referencia(ref_para_cancelar, motivo="CANCELADO_USUARIO")
        except Exception:
            pass

    pre_reservas = obtener_pre_reservas_globales()
    for boleto in list(st.session_state.get("selected_tickets", [])):
        if boleto in pre_reservas and pre_reservas[boleto].get("session_id") == st.session_state.get("session_id"):
            del pre_reservas[boleto]

    st.session_state.selected_tickets = []
    limpiar_enlaces_pago_sin_vaciar_carrito()
    limpiar_checkout_pendiente()
    try:
        limpiar_cache_mapa()
    except Exception:
        pass


def guardar_checkout_pendiente(ref: str, nombre: str, apellidos: str, correo: str, telefono: str, boletos: List[str]):
    st.session_state.checkout_pendiente = {
        "external_reference": ref,
        "nombre": nombre,
        "apellidos": apellidos,
        "correo": correo,
        "telefono": telefono,
        "boletos": list(boletos),
        "total": float(PRECIO_BOLETO) * len(boletos)
    }
    st.session_state.pago_generado_url = None
    st.session_state.stripe_pago_url = None
    st.session_state.stripe_session_id = None


def hay_checkout_pendiente() -> bool:
    data = st.session_state.get("checkout_pendiente")
    return isinstance(data, dict) and bool(data.get("external_reference")) and bool(data.get("boletos"))


def restaurar_carrito_local_desde_reserva(conn: GSheetsConnection, external_reference: str) -> List[str]:
    """Reconstruye carrito, checkout y datos capturados desde Reservas al volver sin pagar."""
    boletos_restaurados = []
    try:
        ext_ref = limpiar_valor_id(external_reference)
        if not ext_ref:
            return []

        df_r = preparar_dataframe_para_update(leer_reservas(conn, ttl_segundos=3), columnas_reservas())
        if obtener_error_lectura_sheets() or df_r.empty:
            return []

        grupo = df_r[df_r["External_Reference"].astype(str) == ext_ref]
        if grupo.empty:
            return []

        pre_reservas = obtener_pre_reservas_globales()
        limpiar_pre_reservas_expiradas(pre_reservas)
        mi_sesion = asegurar_client_id_en_url()

        for _, row in grupo.iterrows():
            boleto = parse_ticket_number(row.get("Numero_Boleto", ""))
            if not boleto:
                continue
            if boleto not in boletos_restaurados:
                boletos_restaurados.append(boleto)
            pre_reservas[boleto] = {
                "session_id": mi_sesion,
                "expires_at": datetime.now() + timedelta(minutes=TIEMPO_PRERESERVA_MINUTOS)
            }

        fila = grupo.iloc[0]
        nombre_completo = limpiar_valor_id(fila.get("Nombre", ""))
        partes_nombre = nombre_completo.split()
        nombre = partes_nombre[0] if partes_nombre else ""
        apellidos = " ".join(partes_nombre[1:]) if len(partes_nombre) > 1 else ""
        correo = limpiar_valor_id(fila.get("Correo", ""))
        telefono = limpiar_valor_id(fila.get("Numero_Telefonico", ""))

        st.session_state.selected_tickets = boletos_restaurados
        st.session_state.external_ref_activa = ext_ref
        st.session_state.checkout_pendiente = {
            "external_reference": ext_ref,
            "nombre": nombre,
            "apellidos": apellidos,
            "correo": correo,
            "telefono": telefono,
            "boletos": boletos_restaurados,
            "total": float(PRECIO_BOLETO) * len(boletos_restaurados)
        }
        st.session_state.checkout_metodo_elegido = ""
        limpiar_enlaces_pago_sin_vaciar_carrito()
        return boletos_restaurados
    except Exception:
        return []


# ============================================================
# SHEETS
# ============================================================

def columnas_ventas() -> list:
    return ["ID_Boleto", "Nombre", "Correo", "Evento", "Numero_Boleto", "Precio", "Metodo_Pago", "Codigo_Pago", "Fecha_Compra", "Numero_Telefonico", "Estado_Pago", "Referencia_Pago", "MercadoPago_Payment_ID", "MercadoPago_Preference_ID", "Stripe_Payment_ID", "Stripe_Session_ID", "Proveedor_Pago"]


def columnas_reservas() -> list:
    return ["External_Reference", "MercadoPago_Preference_ID", "MercadoPago_Payment_ID", "Stripe_Session_ID", "Stripe_Payment_ID", "Numero_Boleto", "Nombre", "Correo", "Numero_Telefonico", "Monto", "Estado_Reserva", "Fecha_Creacion", "Expira_En", "Fecha_Actualizacion"]


def asegurar_columnas(df: pd.DataFrame, cols: list) -> pd.DataFrame:
    df = df.copy()
    for col in cols:
        if col not in df.columns:
            df[col] = ""
    df = df[cols].astype("object")
    return df.where(pd.notna(df), "")


def preparar_dataframe_para_update(df: pd.DataFrame, columnas: list) -> pd.DataFrame:
    df = asegurar_columnas(df, columnas).astype("object")
    df = df.where(pd.notna(df), "")
    for col in df.columns:
        if col not in ["Monto", "Precio"]:
            df[col] = df[col].apply(limpiar_valor_id)
    return df


def leer_reservas(conn: GSheetsConnection, ttl_segundos: int = SHEETS_TTL_LECTURA_SEGUNDOS) -> pd.DataFrame:
    try:
        df = conn.read(worksheet="Reservas", ttl=ttl_segundos).dropna(how="all")
        limpiar_error_lectura_sheets()
        return asegurar_columnas(df, columnas_reservas())
    except Exception as e:
        registrar_error_lectura_sheets(e, "Reservas")
        return pd.DataFrame(columns=columnas_reservas())


def leer_ventas(conn: GSheetsConnection, ttl_segundos: int = SHEETS_TTL_LECTURA_SEGUNDOS) -> pd.DataFrame:
    try:
        df = conn.read(worksheet="Ventas", ttl=ttl_segundos).dropna(how="all")
        limpiar_error_lectura_sheets()
        return asegurar_columnas(df, columnas_ventas())
    except Exception as e:
        registrar_error_lectura_sheets(e, "Ventas")
        return pd.DataFrame(columns=columnas_ventas())

# ============================================================
# ROBUSTEZ DE VENTA
# ============================================================

def obtener_external_reference_pago(payment_info: Dict[str, Any]) -> str:
    return limpiar_valor_id(payment_info.get("external_reference", ""))


def obtener_payment_id_pago(payment_info: Dict[str, Any]) -> str:
    return limpiar_valor_id(payment_info.get("id", ""))


def obtener_provider_session_id_pago(payment_info: Dict[str, Any]) -> str:
    return limpiar_valor_id(payment_info.get("provider_session_id", ""))


def obtener_proveedor_pago(payment_info: Dict[str, Any]) -> str:
    return limpiar_valor_id(payment_info.get("provider", "MERCADO_PAGO")).upper()


def obtener_monto_pagado(payment_info: Dict[str, Any]) -> float:
    proveedor = obtener_proveedor_pago(payment_info)
    if proveedor == "STRIPE":
        if "monto_pagado" in payment_info:
            return normalizar_importe(payment_info.get("monto_pagado"))
        if "amount_total" in payment_info:
            return round(float(payment_info.get("amount_total") or 0) / 100, 2)
        if "amount" in payment_info:
            return round(float(payment_info.get("amount") or 0) / 100, 2)
        return 0.0
    return normalizar_importe(payment_info.get("transaction_amount", payment_info.get("monto_pagado", 0)))


def obtener_moneda_pago(payment_info: Dict[str, Any]) -> str:
    proveedor = obtener_proveedor_pago(payment_info)
    return str(payment_info.get("currency", STRIPE_CURRENCY_ID) if proveedor == "STRIPE" else payment_info.get("currency_id", MP_CURRENCY_ID)).upper()


def obtener_reservas_activas(df_r: pd.DataFrame) -> pd.DataFrame:
    if df_r.empty:
        return pd.DataFrame(columns=columnas_reservas())
    df = preparar_dataframe_para_update(df_r, columnas_reservas())
    estados_bloqueantes = ["PENDIENTE", "ERROR_CONFIRMACION_STRIPE", "ERROR_CONFIRMACION_MERCADO_PAGO"]
    ahora = datetime.now()
    def reserva_activa(row):
        estado = str(row.get("Estado_Reserva", "")).strip().upper()
        if estado not in estados_bloqueantes:
            return False
        try:
            return ahora <= pd.to_datetime(str(row.get("Expira_En", ""))).to_pydatetime()
        except Exception:
            return True
    return df[df.apply(reserva_activa, axis=1)]


def boletos_disponibles_para_reservar(numeros_boletos: List[str], df_v: pd.DataFrame, df_r: pd.DataFrame, external_reference_actual: str = "") -> Tuple[bool, str]:
    boletos = [parse_ticket_number(b) for b in numeros_boletos]
    df_v = preparar_dataframe_para_update(df_v, columnas_ventas())
    df_r = preparar_dataframe_para_update(df_r, columnas_reservas())
    vendidos = set()
    if not df_v.empty:
        vendidos = set(df_v[df_v["Estado_Pago"].astype(str).str.upper().isin(["APROBADO", "VENDIDO"])] ["Numero_Boleto"].astype(str).apply(parse_ticket_number).tolist())
    reservas_activas = obtener_reservas_activas(df_r)
    if external_reference_actual and not reservas_activas.empty:
        reservas_activas = reservas_activas[reservas_activas["External_Reference"].astype(str) != str(external_reference_actual)]
    reservados = set(reservas_activas["Numero_Boleto"].astype(str).apply(parse_ticket_number).tolist()) if not reservas_activas.empty else set()
    vendidos_conf = sorted([b for b in boletos if b in vendidos])
    if vendidos_conf:
        return False, "Boletos ya vendidos: " + ", ".join(vendidos_conf)
    reservados_conf = sorted([b for b in boletos if b in reservados])
    if reservados_conf:
        return False, "Boletos ya reservados por otro usuario: " + ", ".join(reservados_conf)
    return True, "OK"


def existe_pago_ya_procesado(df_v: pd.DataFrame, payment_info: Dict[str, Any]) -> pd.DataFrame:
    if df_v.empty:
        return pd.DataFrame(columns=columnas_ventas())
    df = preparar_dataframe_para_update(df_v, columnas_ventas())
    ext_ref = obtener_external_reference_pago(payment_info)
    pago_id = obtener_payment_id_pago(payment_info)
    session_id = obtener_provider_session_id_pago(payment_info)
    proveedor = obtener_proveedor_pago(payment_info)
    filtros = []
    if ext_ref:
        filtros.append(df["Referencia_Pago"].astype(str) == ext_ref)
    if proveedor == "STRIPE":
        if pago_id:
            filtros.append(df["Stripe_Payment_ID"].astype(str) == pago_id)
        if session_id:
            filtros.append(df["Stripe_Session_ID"].astype(str) == session_id)
    else:
        if pago_id:
            filtros.append(df["MercadoPago_Payment_ID"].astype(str) == pago_id)
    if not filtros:
        return pd.DataFrame(columns=columnas_ventas())
    filtro_final = filtros[0]
    for f in filtros[1:]:
        filtro_final = filtro_final | f
    return df[filtro_final]


def validar_pago_contra_reserva(payment_info: Dict[str, Any], grupo_reserva: pd.DataFrame) -> Tuple[bool, str]:
    if grupo_reserva.empty:
        return False, "No existe reserva asociada al pago."
    ext_ref_pago = obtener_external_reference_pago(payment_info)
    ext_ref_reserva = limpiar_valor_id(grupo_reserva.iloc[0].get("External_Reference", ""))
    if not ext_ref_pago or ext_ref_pago != ext_ref_reserva:
        return False, "La referencia del pago no coincide con la reserva."
    monto_reservado = round(float(grupo_reserva["Monto"].astype(float).sum()), 2)
    monto_esperado = round(float(len(grupo_reserva)) * float(PRECIO_BOLETO), 2)
    monto_pagado = obtener_monto_pagado(payment_info)
    if abs(monto_pagado - monto_reservado) > 0.01:
        return False, f"Monto pagado incorrecto. Pagado={monto_pagado:.2f}, Reservado={monto_reservado:.2f}."
    if abs(monto_pagado - monto_esperado) > 0.01:
        return False, f"Monto pagado no coincide con precio vigente. Pagado={monto_pagado:.2f}, Esperado={monto_esperado:.2f}."
    moneda_pago = obtener_moneda_pago(payment_info)
    proveedor = obtener_proveedor_pago(payment_info)
    moneda_esperada = STRIPE_CURRENCY_ID.upper() if proveedor == "STRIPE" else MP_CURRENCY_ID.upper()
    if moneda_pago and moneda_pago != moneda_esperada:
        return False, f"Moneda incorrecta. Pago={moneda_pago}, Esperada={moneda_esperada}."
    return True, "OK"


def liberar_reservas_previas_mismo_cliente(df_r: pd.DataFrame, ordenes: List[Dict[str, Any]]) -> pd.DataFrame:
    if df_r.empty or not ordenes:
        return df_r
    df = preparar_dataframe_para_update(df_r, columnas_reservas())
    estados_reemplazables = ["PENDIENTE", "ERROR_CONFIRMACION_STRIPE", "ERROR_CONFIRMACION_MERCADO_PAGO", "ERROR_GENERACION_PAGO"]
    ahora_txt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for orden in ordenes:
        boleto = parse_ticket_number(orden.get("Numero_Boleto", ""))
        correo = str(orden.get("Correo", "")).strip().lower()
        telefono = str(orden.get("Numero_Telefonico", "")).strip()
        if not boleto or not correo:
            continue
        filtro = (
            (df["Numero_Boleto"].astype(str).apply(parse_ticket_number) == boleto) &
            (df["Correo"].astype(str).str.lower() == correo) &
            (df["Estado_Reserva"].astype(str).str.upper().isin(estados_reemplazables))
        )
        if telefono:
            filtro = filtro & (df["Numero_Telefonico"].astype(str).str.strip() == telefono)
        df.loc[filtro, "Estado_Reserva"] = "CANCELADO_REEMPLAZADO"
        df.loc[filtro, "Fecha_Actualizacion"] = ahora_txt
    return df

# ============================================================
# REGISTRO EN SHEETS
# ============================================================

def registrar_reserva_cobro(conn: GSheetsConnection, ordenes: List[Dict[str, Any]]) -> Tuple[bool, str]:
    try:
        if not ordenes:
            return False, "No hay boletos para reservar."

        if supabase_activo():
            ok_supabase, msg_supabase = reservar_boletos_supabase(ordenes)
            if not ok_supabase:
                return False, msg_supabase
            limpiar_cache_mapa()
            if not COPIAR_SHEETS_DESDE_APP:
                return True, "Exito Supabase"

        numeros_boletos = [parse_ticket_number(o.get("Numero_Boleto", "")) for o in ordenes]
        df_v_actual = preparar_dataframe_para_update(leer_ventas(conn, ttl_segundos=3), columnas_ventas())
        if obtener_error_lectura_sheets():
            if supabase_activo():
                return True, "Exito Supabase. Advertencia Sheets: " + obtener_error_lectura_sheets()
            return False, obtener_error_lectura_sheets()
        df_r_actual = preparar_dataframe_para_update(leer_reservas(conn, ttl_segundos=3), columnas_reservas())
        if obtener_error_lectura_sheets():
            if supabase_activo():
                return True, "Exito Supabase. Advertencia Sheets: " + obtener_error_lectura_sheets()
            return False, obtener_error_lectura_sheets()
        df_r_actual = liberar_reservas_previas_mismo_cliente(df_r_actual, ordenes)
        disponible, mensaje = boletos_disponibles_para_reservar(numeros_boletos, df_v_actual, df_r_actual)
        if not disponible and not supabase_activo():
            return False, mensaje
        df_nuevo = preparar_dataframe_para_update(pd.DataFrame(ordenes), columnas_reservas())
        df_actualizado = pd.concat([df_r_actual.dropna(how="all"), df_nuevo], ignore_index=True)
        conn.update(worksheet="Reservas", data=preparar_dataframe_para_update(df_actualizado, columnas_reservas()))
        limpiar_cache_mapa()
        return True, "Exito"
    except Exception as e:
        return False, str(e)


def actualizar_ids_proveedores_reserva(conn: GSheetsConnection, external_reference: str, mercado_pago_preference_id: str = "", stripe_session_id: str = "") -> Tuple[bool, str]:
    try:
        df_r = preparar_dataframe_para_update(leer_reservas(conn, ttl_segundos=3), columnas_reservas())
        if obtener_error_lectura_sheets():
            return False, obtener_error_lectura_sheets()
        filtro = df_r["External_Reference"].astype(str) == str(external_reference)
        if not filtro.any():
            return False, "No se encontro la reserva."
        if mercado_pago_preference_id:
            df_r.loc[filtro, "MercadoPago_Preference_ID"] = mercado_pago_preference_id
        if stripe_session_id:
            df_r.loc[filtro, "Stripe_Session_ID"] = stripe_session_id
        df_r.loc[filtro, "Fecha_Actualizacion"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.update(worksheet="Reservas", data=preparar_dataframe_para_update(df_r, columnas_reservas()))
        return True, "Exito"
    except Exception as e:
        return False, str(e)


def liberar_reserva_por_rechazo_o_cancelacion(conn: GSheetsConnection, external_reference: str, motivo: str = "CANCELADO_PAGO") -> Tuple[bool, str]:
    try:
        if not external_reference:
            return False, "Sin referencia."
        df_r = preparar_dataframe_para_update(leer_reservas(conn, ttl_segundos=3), columnas_reservas())
        if obtener_error_lectura_sheets():
            return False, obtener_error_lectura_sheets()
        filtro = df_r["External_Reference"].astype(str) == str(external_reference)
        if not filtro.any():
            return True, "Sin reservas."
        df_r.loc[filtro, "Estado_Reserva"] = motivo
        df_r.loc[filtro, "Fecha_Actualizacion"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.update(worksheet="Reservas", data=preparar_dataframe_para_update(df_r, columnas_reservas()))
        return True, "Reserva liberada."
    except Exception as e:
        return False, str(e)


def actualizar_pago_en_hojas(conn: GSheetsConnection, payment_info: Dict[str, Any]) -> List[Dict[str, Any]]:
    ext_ref = obtener_external_reference_pago(payment_info)
    pago_id = obtener_payment_id_pago(payment_info)
    proveedor = obtener_proveedor_pago(payment_info)
    metodo_pago = limpiar_valor_id(payment_info.get("payment_type_id", proveedor.lower()))
    provider_session_id = obtener_provider_session_id_pago(payment_info)
    if not ext_ref:
        return []
    df_r = preparar_dataframe_para_update(leer_reservas(conn, ttl_segundos=3), columnas_reservas())
    if obtener_error_lectura_sheets():
        return []
    df_v = preparar_dataframe_para_update(leer_ventas(conn, ttl_segundos=3), columnas_ventas())
    if obtener_error_lectura_sheets():
        return []
    ventas_existentes = existe_pago_ya_procesado(df_v, payment_info)
    if not ventas_existentes.empty:
        return ventas_existentes.to_dict(orient="records")
    filtro_reserva = df_r["External_Reference"].astype(str) == ext_ref
    grupo_reserva = df_r[filtro_reserva].copy()
    if grupo_reserva.empty:
        return []
    pago_valido, mensaje_pago = validar_pago_contra_reserva(payment_info, grupo_reserva)
    if not pago_valido:
        df_r.loc[filtro_reserva, "Estado_Reserva"] = f"ERROR_VALIDACION_PAGO: {mensaje_pago}"[:250]
        df_r.loc[filtro_reserva, "Fecha_Actualizacion"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.update(worksheet="Reservas", data=preparar_dataframe_para_update(df_r, columnas_reservas()))
        return []
    numeros_boletos = grupo_reserva["Numero_Boleto"].astype(str).apply(parse_ticket_number).tolist()
    disponible, mensaje_disponible = boletos_disponibles_para_reservar(numeros_boletos, df_v, df_r, external_reference_actual=ext_ref)
    if not disponible:
        df_r.loc[filtro_reserva, "Estado_Reserva"] = f"ERROR_CONCURRENCIA: {mensaje_disponible}"[:250]
        df_r.loc[filtro_reserva, "Fecha_Actualizacion"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.update(worksheet="Reservas", data=preparar_dataframe_para_update(df_r, columnas_reservas()))
        return []
    df_r.loc[filtro_reserva, "Estado_Reserva"] = "PAGADO"
    df_r.loc[filtro_reserva, "Fecha_Actualizacion"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if proveedor == "STRIPE":
        if pago_id:
            df_r.loc[filtro_reserva, "Stripe_Payment_ID"] = pago_id
        if provider_session_id:
            df_r.loc[filtro_reserva, "Stripe_Session_ID"] = provider_session_id
    else:
        if pago_id:
            df_r.loc[filtro_reserva, "MercadoPago_Payment_ID"] = pago_id
    conn.update(worksheet="Reservas", data=preparar_dataframe_para_update(df_r, columnas_reservas()))
    nuevas_ventas = []
    for _, r in grupo_reserva.iterrows():
        numero_boleto = parse_ticket_number(r.get("Numero_Boleto", ""))
        nuevas_ventas.append({
            "ID_Boleto": f"BOL-{random.randint(10000, 99999)}",
            "Nombre": r.get("Nombre", ""), "Correo": r.get("Correo", ""), "Evento": NOMBRE_EVENTO,
            "Numero_Boleto": numero_boleto, "Precio": r.get("Monto", ""), "Metodo_Pago": metodo_pago,
            "Codigo_Pago": pago_id, "Fecha_Compra": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "Numero_Telefonico": r.get("Numero_Telefonico", ""), "Estado_Pago": "VENDIDO",
            "Referencia_Pago": ext_ref, "MercadoPago_Payment_ID": pago_id if proveedor != "STRIPE" else "",
            "MercadoPago_Preference_ID": r.get("MercadoPago_Preference_ID", ""),
            "Stripe_Payment_ID": pago_id if proveedor == "STRIPE" else "",
            "Stripe_Session_ID": provider_session_id if proveedor == "STRIPE" else r.get("Stripe_Session_ID", ""),
            "Proveedor_Pago": proveedor
        })
    df_final = pd.concat([df_v, preparar_dataframe_para_update(pd.DataFrame(nuevas_ventas), columnas_ventas())], ignore_index=True)
    conn.update(worksheet="Ventas", data=preparar_dataframe_para_update(df_final, columnas_ventas()))
    return nuevas_ventas

# ============================================================
# PDF
# ============================================================

def texto_boletos(datos_boletos: List[Dict[str, Any]]) -> str:
    numeros = []
    for boleto in datos_boletos:
        numero = parse_ticket_number(boleto.get("Numero_Boleto", ""))
        if numero and numero not in numeros:
            numeros.append(numero)
    return ", ".join(numeros)


def dibujar_fondo_autenticidad(canvas, doc):
    width, height = doc.pagesize
    canvas.saveState()
    canvas.setFillColor(colors.HexColor("#F8FAFC")); canvas.rect(0, 0, width, height, fill=1, stroke=0)
    canvas.setFillColor(colors.Color(0.10, 0.18, 0.30, alpha=0.045)); canvas.setFont("Helvetica-Bold", 42)
    canvas.translate(width / 2, height / 2); canvas.rotate(32)
    for y in range(-360, 420, 95):
        canvas.drawCentredString(0, y, "BOLETO OFICIAL")
    canvas.rotate(-32); canvas.translate(-width / 2, -height / 2)
    canvas.setStrokeColor(colors.HexColor("#0A2540")); canvas.setLineWidth(1.15)
    canvas.roundRect(24, 24, width - 48, height - 48, 16, fill=0, stroke=1)
    canvas.setFillColor(colors.HexColor("#0A2540")); canvas.roundRect(42, height - 112, width - 84, 48, 10, fill=1, stroke=0); canvas.roundRect(42, 58, width - 84, 26, 8, fill=1, stroke=0)
    canvas.setDash(3, 4); canvas.setStrokeColor(colors.HexColor("#CBD5E1")); canvas.line(62, 118, width - 62, 118); canvas.line(62, height - 130, width - 62, height - 130); canvas.setDash(); canvas.restoreState()


def generar_pdf_boleto(datos_boletos: List[Dict[str, Any]]) -> str:
    """Genera un PDF con una pagina unica por cada boleto comprado."""
    if not datos_boletos:
        return "Boleto_Sin_Datos.pdf"
    boletos_unicos = []
    vistos = set()
    for boleto in datos_boletos:
        numero = parse_ticket_number(boleto.get("Numero_Boleto", ""))
        if numero and numero not in vistos:
            vistos.add(numero)
            boletos_unicos.append(boleto)
    if not boletos_unicos:
        return "Boleto_Sin_Datos.pdf"
    boletos_txt = texto_boletos(boletos_unicos) or "N/A"
    nombre_archivo = f"Boleto_{boletos_txt.replace(', ', '_')}.pdf"
    doc = SimpleDocTemplate(nombre_archivo, pagesize=letter, rightMargin=46, leftMargin=46, topMargin=70, bottomMargin=50)
    story = []
    styles = getSampleStyleSheet()
    estilo_titulo = ParagraphStyle("Titulo", parent=styles["Heading1"], fontSize=20, textColor=colors.white, alignment=1)
    estilo_sub = ParagraphStyle("Sub", parent=styles["Normal"], fontSize=9.5, textColor=colors.HexColor("#E2E8F0"), alignment=1)
    estilo_celda = ParagraphStyle("Celda", parent=styles["Normal"], fontSize=10.5, leading=13, textColor=colors.HexColor("#334155"))
    estilo_bold = ParagraphStyle("Bold", parent=styles["Normal"], fontSize=10.5, leading=13, textColor=colors.HexColor("#0A2540"))
    estilo_num = ParagraphStyle("Num", parent=styles["Heading1"], fontSize=30, leading=36, textColor=colors.HexColor("#991B1B"), alignment=1)
    estilo_evento = ParagraphStyle("Evento", parent=styles["Heading2"], fontSize=13, leading=16, textColor=colors.HexColor("#0A2540"), alignment=1)
    estilo_mensaje = ParagraphStyle("Mensaje", parent=styles["Normal"], fontSize=8.7, leading=11, textColor=colors.HexColor("#475569"), alignment=1)

    for idx, boleto in enumerate(boletos_unicos):
        numero_boleto = parse_ticket_number(boleto.get("Numero_Boleto", ""))
        id_visual = f"Bol-{numero_boleto}"
        try:
            precio_txt = f"${float(boleto.get('Precio', 0) or 0):.2f} {MP_CURRENCY_ID}"
        except Exception:
            precio_txt = str(boleto.get("Precio", ""))
        fecha_compra = formatear_fecha_pdf(boleto.get("Fecha_Compra", ""))
        encabezado = Table([[Paragraph("BOLETO OFICIAL", estilo_titulo)], [Paragraph(str(NOMBRE_EVENTO), estilo_sub)]], colWidths=[500])
        encabezado.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#0A2540")),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ]))
        story.append(encabezado)
        story.append(Spacer(1, 22))
        resumen = Table([
            [Paragraph("Nombre del evento", estilo_bold), Paragraph("Fecha de vigencia", estilo_bold)],
            [Paragraph(str(NOMBRE_EVENTO), estilo_evento), Paragraph(str(FECHA_VIGENCIA_BOLETO), estilo_evento)],
            [Paragraph("No. de Boleto", estilo_bold), Paragraph("ID de boleto", estilo_bold)],
            [Paragraph(numero_boleto, estilo_num), Paragraph(id_visual, estilo_num)],
        ], colWidths=[250, 250])
        resumen.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E0F2FE")),
            ("BACKGROUND", (0, 2), (-1, 2), colors.HexColor("#E0F2FE")),
            ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#0A2540")),
            ("INNERGRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#CBD5E1")),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]))
        story.append(resumen)
        story.append(Spacer(1, 18))
        mensaje = Table([[Paragraph(MENSAJE_GENERAL_BOLETO, estilo_mensaje)]], colWidths=[500])
        mensaje.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F1F5F9")),
            ("BOX", (0, 0), (-1, -1), 0.7, colors.HexColor("#CBD5E1")),
            ("TOPPADDING", (0, 0), (-1, -1), 9),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
        ]))
        story.append(mensaje)
        story.append(Spacer(1, 18))
        filas = [
            [Paragraph("<b>ID de Boleto:</b>", estilo_bold), Paragraph(id_visual, estilo_celda)],
            [Paragraph("<b>Nombre:</b>", estilo_bold), Paragraph(str(boleto.get("Nombre", "")), estilo_celda)],
            [Paragraph("<b>No. de Boleto:</b>", estilo_bold), Paragraph(numero_boleto, estilo_celda)],
            [Paragraph("<b>Precio Pagado:</b>", estilo_bold), Paragraph(precio_txt, estilo_celda)],
            [Paragraph("<b>Metodo de Pago:</b>", estilo_bold), Paragraph(str(boleto.get("Metodo_Pago", "Pago electronico")).upper(), estilo_celda)],
            [Paragraph("<b>Fecha:</b>", estilo_bold), Paragraph(fecha_compra, estilo_celda)],
            [Paragraph("<b>Vigencia:</b>", estilo_bold), Paragraph(str(FECHA_VIGENCIA_BOLETO), estilo_celda)],
        ]
        detalle = Table(filas, colWidths=[160, 340])
        detalle.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.9, colors.HexColor("#0A2540")),
            ("INNERGRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#E2E8F0")),
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F1F5F9")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ]))
        story.append(detalle)
        if idx < len(boletos_unicos) - 1:
            story.append(PageBreak())
    doc.build(story, onFirstPage=dibujar_fondo_autenticidad, onLaterPages=dibujar_fondo_autenticidad)
    return nombre_archivo


def procesar_descarga_pdf(datos_boletos: List[dict]):
    if not datos_boletos:
        st.warning("No hay boletos disponibles para generar PDF.")
        return
    boletos_unicos = []
    vistos = set()
    for boleto in datos_boletos:
        numero = parse_ticket_number(boleto.get("Numero_Boleto", ""))
        if numero and numero not in vistos:
            vistos.add(numero)
            boletos_unicos.append(boleto)
    if not boletos_unicos:
        st.warning("No hay boletos validos para generar PDF.")
        return
    archivo_pdf = generar_pdf_boleto(boletos_unicos)
    with open(archivo_pdf, "rb") as pdf_file:
        pdf_bytes = pdf_file.read()
    label = "🎟️ Descargar boleto en PDF" if len(boletos_unicos) == 1 else "🎟️ Descargar boletos en PDF"
    boletos_key = "_".join([parse_ticket_number(b.get("Numero_Boleto", "")) for b in boletos_unicos])
    ref_key = limpiar_valor_id(boletos_unicos[0].get("Referencia_Pago", "") if boletos_unicos else "")
    timestamp_key = datetime.now().strftime("%Y%m%d%H%M%S%f")
    st.download_button(
        label=label,
        data=pdf_bytes,
        file_name=archivo_pdf,
        mime="application/pdf",
        type="primary",
        use_container_width=True,
        key=f"download_pdf_{ref_key}_{boletos_key}_{timestamp_key}"
    )


# ============================================================
# PAGOS
# ============================================================

def construir_pago_stripe_desde_session(session: Any, external_reference_reserva: str = "", correo_reserva: str = "", monto_esperado: Optional[float] = None) -> Optional[Dict[str, Any]]:
    session_dict = safe_to_dict(session)
    if str(safe_get(session_dict, "payment_status", "")).strip().lower() != "paid":
        return None
    metadata = dict(safe_get(session_dict, "metadata", {}) or {})
    ref_stripe = str(metadata.get("external_reference") or safe_get(session_dict, "client_reference_id", "") or "").strip()
    payment_intent = safe_get(session_dict, "payment_intent", None)
    payment_intent_id = payment_intent if isinstance(payment_intent, str) else str(safe_get(payment_intent, "id", "") or safe_get(session_dict, "id", ""))
    return {"id": payment_intent_id, "external_reference": ref_stripe or external_reference_reserva, "payment_type_id": "stripe_card", "status": "approved", "provider": "STRIPE", "provider_session_id": str(safe_get(session_dict, "id", "")), "monto_pagado": round(float(safe_get(session_dict, "amount_total", 0) or 0) / 100, 2), "currency": str(safe_get(session_dict, "currency", STRIPE_CURRENCY_ID)).upper()}


def obtener_pago_stripe(stripe_session_id: str, external_reference_esperada: Optional[str] = None, monto_esperado: Optional[float] = None, correo_reserva: str = "") -> Optional[Dict[str, Any]]:
    if stripe is None or not STRIPE_SECRET_KEY or not stripe_session_id:
        return None
    try:
        session = stripe.checkout.Session.retrieve(stripe_session_id, expand=["payment_intent"])
        return construir_pago_stripe_desde_session(session, external_reference_esperada or "", correo_reserva, monto_esperado)
    except Exception as e:
        st.session_state.ultimo_error_pago = f"Stripe retrieve: {e}"
        return None


def obtener_pago_stripe_por_payment_intent(payment_intent_id: str, external_reference_esperada: Optional[str] = None, monto_esperado: Optional[float] = None, correo_reserva: str = "") -> Optional[Dict[str, Any]]:
    if stripe is None or not STRIPE_SECRET_KEY or not payment_intent_id or not str(payment_intent_id).startswith("pi_"):
        return None
    try:
        intent = stripe.PaymentIntent.retrieve(payment_intent_id, expand=["latest_charge"])
        if str(safe_get(intent, "status", "")).strip().lower() != "succeeded":
            return None
        metadata = dict(safe_get(intent, "metadata", {}) or {})
        return {"id": payment_intent_id, "external_reference": metadata.get("external_reference") or external_reference_esperada or "", "payment_type_id": "stripe_card", "status": "approved", "provider": "STRIPE", "provider_session_id": "", "monto_pagado": round(float(safe_get(intent, "amount", 0) or 0) / 100, 2), "currency": str(safe_get(intent, "currency", STRIPE_CURRENCY_ID)).upper()}
    except Exception as e:
        st.session_state.ultimo_error_pago = f"Stripe PaymentIntent retrieve: {e}"
        return None


def buscar_pago_stripe_por_referencia_correo_fecha(external_reference, correo, monto_esperado, fecha_creacion_reserva):
    if stripe is None or not STRIPE_SECRET_KEY:
        return None
    try:
        sesiones = stripe.checkout.Session.list(limit=100, status="complete", created=normalizar_fecha_unix(fecha_creacion_reserva), expand=["data.payment_intent"])
        for session in safe_data_list(sesiones):
            pago = construir_pago_stripe_desde_session(session, external_reference, correo, monto_esperado)
            if pago:
                return pago
    except Exception as e:
        st.session_state.ultimo_error_pago = f"Stripe conciliacion: {e}"
    return None


def buscar_pago_stripe_payment_intent_por_correo_fecha(external_reference, correo, monto_esperado, fecha_creacion_reserva):
    if stripe is None or not STRIPE_SECRET_KEY:
        return None
    try:
        cents = int(round(float(monto_esperado) * 100)) if monto_esperado is not None else None
        intents = stripe.PaymentIntent.list(limit=100, created=normalizar_fecha_unix(fecha_creacion_reserva), expand=["data.latest_charge"])
        for intent in safe_data_list(intents):
            if str(safe_get(intent, "status", "")).lower() != "succeeded":
                continue
            if cents is not None and int(safe_get(intent, "amount", 0) or 0) != cents:
                continue
            metadata = dict(safe_get(intent, "metadata", {}) or {})
            ref = str(metadata.get("external_reference") or "").strip()
            if ref and ref != external_reference:
                continue
            return {"id": str(safe_get(intent, "id", "")), "external_reference": ref or external_reference, "payment_type_id": "stripe_card", "status": "approved", "provider": "STRIPE", "provider_session_id": "", "monto_pagado": round(float(safe_get(intent, "amount", 0) or 0) / 100, 2), "currency": str(safe_get(intent, "currency", STRIPE_CURRENCY_ID)).upper()}
    except Exception as e:
        st.session_state.ultimo_error_pago = f"Stripe PI conciliacion: {e}"
    return None


def buscar_pago_mercadopago_por_referencia_correo_fecha(external_reference, correo, monto_esperado, fecha_creacion_reserva):
    if not sdk:
        return None
    try:
        respuesta = sdk.payment().search({"external_reference": str(external_reference or ""), "status": "approved", "sort": "date_created", "criteria": "desc"}).get("response", {})
        for pago in respuesta.get("results", []):
            if pago.get("status") == "approved" and (monto_esperado is None or abs(float(pago.get("transaction_amount", 0) or 0) - float(monto_esperado)) < 0.01):
                pago["provider"] = "MERCADO_PAGO"
                if not pago.get("external_reference"):
                    pago["external_reference"] = external_reference
                return pago
    except Exception as e:
        st.session_state.ultimo_error_pago = f"MP conciliacion: {e}"
    return None


def obtener_pago_mercadopago_por_payment_id(payment_id, external_reference_esperada="", monto_esperado=None, correo_reserva=""):
    if not sdk or not payment_id or not str(payment_id).isdigit():
        return None
    try:
        pago = sdk.payment().get(payment_id).get("response", {})
        if pago.get("status") != "approved":
            return None
        if monto_esperado is not None and abs(float(pago.get("transaction_amount", 0) or 0) - float(monto_esperado)) >= 0.01:
            return None
        pago["provider"] = "MERCADO_PAGO"
        if not pago.get("external_reference") and external_reference_esperada:
            pago["external_reference"] = external_reference_esperada
        return pago
    except Exception as e:
        st.session_state.ultimo_error_pago = f"MP payment_id retrieve: {e}"
        return None


def crear_preferencia_mercado_pago(nombre, apellidos, correo, telefono, numeros_boletos, monto_unitario, external_reference):
    if not sdk:
        return "", ""
    base = normalizar_url(MP_RETURN_URL)
    titulos = ", ".join(numeros_boletos)
    data = {
        "items": [{"title": f"{NOMBRE_EVENTO} - Boletos: {titulos}", "quantity": len(numeros_boletos), "unit_price": float(monto_unitario), "currency_id": MP_CURRENCY_ID}],
        "payer": {"name": nombre.strip(), "surname": apellidos.strip() or "Sin Apellido", "email": correo, "phone": {"area_code": "52", "number": telefono}},
        "external_reference": external_reference,
        "payment_methods": {"excluded_payment_methods": [], "excluded_payment_types": [], "installments": 1},
        "statement_descriptor": "RIFA"
    }
    if MP_NOTIFICATION_URL:
        data["notification_url"] = MP_NOTIFICATION_URL
    if base:
        data["back_urls"] = {
            "success": agregar_parametros_url(base, parametros_retorno_pago({"mp_return": "success", "external_reference": external_reference})),
            "pending": agregar_parametros_url(base, parametros_retorno_pago({"mp_return": "pending", "external_reference": external_reference})),
            "failure": agregar_parametros_url(base, parametros_retorno_pago({"mp_return": "failure", "external_reference": external_reference}))
        }
        data["auto_return"] = "approved"
    pref = sdk.preference().create(data).get("response", {})
    if "id" not in pref:
        raise Exception(f"Rechazado por MP: {pref.get('message', 'Error en credenciales o URL de retorno')}")
    return pref.get("id", ""), pref.get("init_point") or pref.get("sandbox_init_point", "")


def crear_sesion_stripe(nombre, apellidos, correo, numeros_boletos, monto_unitario, external_reference):
    if stripe is None:
        raise ValueError("La libreria stripe no esta instalada. Agrega stripe en requirements.txt.")
    if not STRIPE_SECRET_KEY:
        raise ValueError("STRIPE_SECRET_KEY no esta configurado.")
    if STRIPE_SECRET_KEY.startswith("pk_"):
        raise ValueError("STRIPE_SECRET_KEY contiene una llave publica pk_. Usa sk_test_ o sk_live_.")
    if not STRIPE_RETURN_URL:
        raise ValueError("STRIPE_RETURN_URL no esta configurado.")
    base = normalizar_url(STRIPE_RETURN_URL)
    boletos = ", ".join(numeros_boletos)
    session = stripe.checkout.Session.create(
        mode="payment",
        customer_email=correo.strip().lower(),
        client_reference_id=external_reference,
        line_items=[{"price_data": {"currency": STRIPE_CURRENCY_ID, "unit_amount": int(round(float(monto_unitario) * 100)), "product_data": {"name": f"Boletos {NOMBRE_EVENTO}", "description": f"Boletos seleccionados: {boletos}"[:500]}}, "quantity": len(numeros_boletos)}],
        metadata={"external_reference": external_reference, "boletos": boletos, "nombre_cliente": f"{nombre.strip()} {apellidos.strip()}"[:500]},
        payment_intent_data={"metadata": {"external_reference": external_reference}},
        success_url=agregar_parametros_url(base, parametros_retorno_pago({"stripe_session_id": "{CHECKOUT_SESSION_ID}", "external_reference": external_reference})),
        cancel_url=agregar_parametros_url(base, parametros_retorno_pago({"stripe_cancelled": "true", "external_reference": external_reference}))
    )
    return str(session.id), str(session.url)

# ============================================================
# RECUPERACION, MAPA Y RETORNOS
# ============================================================

def extraer_ids_pago_de_reserva(grupo_reserva):
    ids = {"stripe_session_id": "", "stripe_payment_id": "", "mp_payment_id": "", "mp_preference_id": ""}
    for _, fila in grupo_reserva.iterrows():
        for columna in grupo_reserva.columns:
            valor = str(fila.get(columna, "")).strip()
            if not valor or valor.lower() == "nan":
                continue
            if valor.startswith("cs_") and not ids["stripe_session_id"]:
                ids["stripe_session_id"] = valor
            elif valor.startswith("pi_") and not ids["stripe_payment_id"]:
                ids["stripe_payment_id"] = valor
            elif valor.isdigit() and len(valor) >= 6 and not ids["mp_payment_id"]:
                ids["mp_payment_id"] = valor
            elif columna == "MercadoPago_Preference_ID" and len(valor) >= 10 and not ids["mp_preference_id"]:
                ids["mp_preference_id"] = valor
    return ids


def recuperar_boletos_por_reserva(conn, numero_boleto, correo):
    datos_supabase = consultar_ventas_supabase_por_boleto_correo(numero_boleto, correo)
    if datos_supabase:
        return datos_supabase

    df_v = leer_ventas(conn, ttl_segundos=3)
    correo_limpio = correo.strip().lower()
    num = parse_ticket_number(numero_boleto)
    if not df_v.empty:
        filtro = (df_v["Numero_Boleto"].astype(str).apply(parse_ticket_number) == num) & (df_v["Correo"].astype(str).str.lower() == correo_limpio)
        if filtro.any():
            ref = str(df_v[filtro].iloc[-1].get("Referencia_Pago", ""))
            return df_v[df_v["Referencia_Pago"].astype(str) == ref].to_dict(orient="records")
    df_r = leer_reservas(conn, ttl_segundos=3)
    filtro = (df_r["Numero_Boleto"].astype(str).apply(parse_ticket_number) == num) & (df_r["Correo"].astype(str).str.lower() == correo_limpio)
    reservas = df_r[filtro]
    if reservas.empty:
        return []
    base = reservas.iloc[-1]
    ext_ref = str(base.get("External_Reference", "")).strip()
    grupo = df_r[df_r["External_Reference"].astype(str) == ext_ref]
    if not ext_ref or grupo.empty:
        return []
    total = float(grupo["Monto"].astype(float).sum())
    fecha = str(base.get("Fecha_Creacion", ""))
    ids = extraer_ids_pago_de_reserva(grupo)
    pago = None
    if ids.get("mp_payment_id"):
        pago = obtener_pago_mercadopago_por_payment_id(ids["mp_payment_id"], ext_ref, total, correo_limpio)
    if not pago:
        pago = buscar_pago_mercadopago_por_referencia_correo_fecha(ext_ref, correo_limpio, total, fecha)
    if not pago and ids.get("stripe_session_id"):
        pago = obtener_pago_stripe(ids["stripe_session_id"], ext_ref, total, correo_limpio)
    if not pago and ids.get("stripe_payment_id", "").startswith("pi_"):
        pago = obtener_pago_stripe_por_payment_intent(ids["stripe_payment_id"], ext_ref, total, correo_limpio)
    if not pago:
        pago = buscar_pago_stripe_por_referencia_correo_fecha(ext_ref, correo_limpio, total, fecha)
    if not pago:
        pago = buscar_pago_stripe_payment_intent_por_correo_fecha(ext_ref, correo_limpio, total, fecha)
    if pago:
        datos = finalizar_pago_confirmado_app(conn, pago, ext_ref)
        if datos:
            return datos
        df_v = leer_ventas(conn, ttl_segundos=3)
        ventas = df_v[df_v["Referencia_Pago"].astype(str) == ext_ref]
        if not ventas.empty:
            return ventas.to_dict(orient="records")
    return []


def obtener_ventas_por_referencia(conn: GSheetsConnection, external_reference: str) -> List[Dict[str, Any]]:
    try:
        ref = limpiar_valor_id(external_reference)
        if not ref:
            return []
        df_v = preparar_dataframe_para_update(leer_ventas(conn, ttl_segundos=3), columnas_ventas())
        if obtener_error_lectura_sheets():
            return []
        ventas = df_v[df_v["Referencia_Pago"].astype(str) == ref]
        return ventas.to_dict(orient="records") if not ventas.empty else []
    except Exception:
        return []


def confirmar_si_venta_ya_existe(conn: GSheetsConnection, external_reference: str) -> bool:
    ref = limpiar_valor_id(external_reference)
    ventas_supabase = obtener_ventas_supabase_por_referencia(ref)
    if ventas_supabase:
        st.session_state.boletos_confirmados = ventas_supabase
        st.session_state.payment_success_id = "PAGO_CONFIRMADO"
        limpiar_carrito_local(cancelar_reserva_supabase=False)
        st.session_state.boletos_confirmados = ventas_supabase
        limpiar_checkout_pendiente()
        limpiar_query_manteniendo_cid()
        return True
    ventas = obtener_ventas_por_referencia(conn, ref)
    if ventas:
        st.session_state.boletos_confirmados = ventas
        st.session_state.payment_success_id = "PAGO_CONFIRMADO"
        limpiar_carrito_local(cancelar_reserva_supabase=False)
        st.session_state.boletos_confirmados = ventas
        limpiar_checkout_pendiente()
        limpiar_query_manteniendo_cid()
        return True
    return False


def obtener_estado_boletos_bd(df_ventas, df_reservas):
    estados = {}

    checkout_actual = st.session_state.get("checkout_pendiente")
    ref_checkout_actual = ""
    if isinstance(checkout_actual, dict):
        ref_checkout_actual = limpiar_valor_id(checkout_actual.get("external_reference", ""))
    if not ref_checkout_actual:
        ref_checkout_actual = limpiar_valor_id(st.session_state.get("external_ref_activa", ""))

    if not df_reservas.empty and "Numero_Boleto" in df_reservas.columns:
        for _, row in df_reservas.iterrows():
            if str(row.get("Estado_Reserva", "")).strip().upper() in ["PENDIENTE", "ERROR_CONFIRMACION_STRIPE", "ERROR_CONFIRMACION_MERCADO_PAGO"]:
                try:
                    exp = pd.to_datetime(str(row.get("Expira_En"))).to_pydatetime()
                except Exception:
                    exp = None
                if exp is None or datetime.now() <= exp:
                    num = parse_ticket_number(row["Numero_Boleto"])
                    ref_reserva = limpiar_valor_id(row.get("External_Reference", ""))
                    if num:
                        if ref_checkout_actual and ref_reserva == ref_checkout_actual:
                            estados[num] = "pre_reservado_mio"
                        else:
                            estados[num] = "reservado_db"
    if not df_ventas.empty and "Numero_Boleto" in df_ventas.columns:
        for _, row in df_ventas.iterrows():
            if str(row.get("Estado_Pago", "")).strip().upper() in ["APROBADO", "VENDIDO"]:
                num = parse_ticket_number(row["Numero_Boleto"])
                if num:
                    estados[num] = "vendido_db"
    return estados


def limpiar_cache_mapa():
    st.session_state["_mapa_cache_ts"] = 0
    st.session_state["_mapa_cache_ventas"] = pd.DataFrame(columns=columnas_ventas())
    st.session_state["_mapa_cache_reservas"] = pd.DataFrame(columns=columnas_reservas())


def obtener_datos_mapa_cache(conn: GSheetsConnection) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Mapa rapido: si Supabase esta activo, lee Supabase con cache corto."""
    if supabase_activo():
        ahora = time.time()
        ts = float(st.session_state.get("_mapa_cache_ts", 0) or 0)
        df_v_cache = st.session_state.get("_mapa_cache_ventas")
        df_r_cache = st.session_state.get("_mapa_cache_reservas")
        cache_valida = (
            isinstance(df_v_cache, pd.DataFrame)
            and isinstance(df_r_cache, pd.DataFrame)
            and (ahora - ts) < SUPABASE_TTL_MAPA_SEGUNDOS
        )
        if cache_valida:
            return df_v_cache.copy(), df_r_cache.copy()
        df_v, df_r = leer_mapa_supabase()
        st.session_state["_mapa_cache_ts"] = ahora
        st.session_state["_mapa_cache_ventas"] = df_v.copy()
        st.session_state["_mapa_cache_reservas"] = df_r.copy()
        return df_v, df_r

    ahora = time.time()
    ts = float(st.session_state.get("_mapa_cache_ts", 0) or 0)
    df_v_cache = st.session_state.get("_mapa_cache_ventas")
    df_r_cache = st.session_state.get("_mapa_cache_reservas")

    cache_valida = (
        isinstance(df_v_cache, pd.DataFrame)
        and isinstance(df_r_cache, pd.DataFrame)
        and (ahora - ts) < SHEETS_TTL_MAPA_SEGUNDOS
    )
    if cache_valida:
        return df_v_cache.copy(), df_r_cache.copy()

    df_v = leer_ventas(conn, ttl_segundos=SHEETS_TTL_MAPA_SEGUNDOS)
    df_r = leer_reservas(conn, ttl_segundos=SHEETS_TTL_MAPA_SEGUNDOS)
    if not obtener_error_lectura_sheets():
        st.session_state["_mapa_cache_ts"] = ahora
        st.session_state["_mapa_cache_ventas"] = df_v.copy()
        st.session_state["_mapa_cache_reservas"] = df_r.copy()
    return df_v, df_r


def seleccionar_metodo_pago_checkout(metodo: str):
    st.session_state.checkout_metodo_elegido = metodo


def renderizar_mapa_interactivo():
    asegurar_client_id_en_url()
    mi_sesion = st.session_state.session_id
    pre_reservas = obtener_pre_reservas_globales()
    limpiar_pre_reservas_expiradas(pre_reservas)
    conn = st.connection("gsheets", type=GSheetsConnection)
    df_v, df_r = obtener_datos_mapa_cache(conn)
    error_mapa = obtener_error_lectura_sheets()
    if error_mapa:
        st.warning(error_mapa)
    estados_bd = obtener_estado_boletos_bd(df_v, df_r)
    boletos_checkout_actual = set()
    if isinstance(st.session_state.get("checkout_pendiente"), dict):
        boletos_checkout_actual = set([
            parse_ticket_number(b)
            for b in st.session_state.checkout_pendiente.get("boletos", [])
            if parse_ticket_number(b)
        ])

    for boleto_estado_bd in list(estados_bd.keys()):
        if boleto_estado_bd in boletos_checkout_actual:
            continue
        if boleto_estado_bd in pre_reservas:
            pre_reservas.pop(boleto_estado_bd, None)
        if boleto_estado_bd in st.session_state.selected_tickets:
            st.session_state.selected_tickets.remove(boleto_estado_bd)
    if not (st.session_state.get("pago_generado_url") or st.session_state.get("stripe_pago_url") or hay_checkout_pendiente()):
        st.session_state.selected_tickets = [t for t in st.session_state.selected_tickets if t in pre_reservas and pre_reservas[t].get("session_id") == mi_sesion]
    estados, vendidos, reservados, otros = {}, 0, 0, 0
    for i in range(TOTAL_BOLETOS):
        num = formatear_numero_boleto(i)
        if num in estados_bd:
            estados[num] = estados_bd[num]
            vendidos += 1 if estados_bd[num] == "vendido_db" else 0
            reservados += 1 if estados_bd[num] == "reservado_db" else 0
        elif num in pre_reservas:
            estados[num] = "pre_reservado_mio" if pre_reservas[num].get("session_id") == mi_sesion else "pre_reservado_otros"
            if estados[num] == "pre_reservado_otros":
                otros += 1
        else:
            estados[num] = "disponible"
    libres = TOTAL_BOLETOS - vendidos - reservados - otros - len(st.session_state.selected_tickets)
    st.markdown(f"""
    <div class="metric-container">
        <div class="metric-box m-green"><h2>🟢 {libres}</h2><p>Libres</p></div>
        <div class="metric-box m-gray"><h2>🔒 {otros}</h2><p>En otro carrito</p></div>
        <div class="metric-box m-yellow"><h2>🟠 {reservados}</h2><p>Reservados / validando</p></div>
        <div class="metric-box m-red"><h2>🔴 {vendidos}</h2><p>Vendidos</p></div>
    </div>
    """, unsafe_allow_html=True)
    estilos = ["""
    <style>div[class*="st-key-btn_"] button { min-height:58px!important; border-radius:14px!important; border-style:dashed!important; border-width:1.8px!important; font-weight:900!important; }</style>
    """]
    for num, estado in estados.items():
        if estado == "vendido_db":
            fondo, borde, color = "linear-gradient(145deg,#FECACA,#FEF2F2)", "#EF4444", "#7F1D1D"
        elif estado == "reservado_db":
            fondo, borde, color = "linear-gradient(145deg,#FED7AA,#FFF7ED)", "#F59E0B", "#7C2D12"
        elif estado == "pre_reservado_otros":
            fondo, borde, color = "linear-gradient(145deg,#E2E8F0,#F8FAFC)", "#64748B", "#334155"
        elif estado == "pre_reservado_mio" or num in st.session_state.selected_tickets:
            fondo, borde, color = "linear-gradient(145deg,#111827,#000000)", "#000000", "#FFFFFF"
        else:
            fondo, borde, color = "linear-gradient(145deg,#D1FAE5,#F0FDF4)", "#10B981", "#064E3B"
        estilos.append(f"<style>.st-key-btn_{num} button{{background:{fondo}!important;border-color:{borde}!important;color:{color}!important}}</style>")
    st.markdown("\n".join(estilos), unsafe_allow_html=True)
    with st.container(key="mapa_boletos_grid"):
        for fila in range(FILAS_MAPA):
            cols = st.columns(COLUMNAS_MAPA)
            for col_idx in range(COLUMNAS_MAPA):
                idx = fila * COLUMNAS_MAPA + col_idx
                if idx >= TOTAL_BOLETOS:
                    continue
                num = formatear_numero_boleto(idx)
                estado = estados[num]
                with cols[col_idx]:
                    if estado in ["vendido_db", "reservado_db", "pre_reservado_otros"]:
                        st.button(f"🎟️\n{num}", disabled=True, key=f"btn_{num}", help=estado)
                    else:
                        seleccionado = estado == "pre_reservado_mio" or num in st.session_state.selected_tickets
                        st.button(
                            f"🎟️\n{num}",
                            key=f"btn_{num}",
                            type="primary" if seleccionado else "secondary",
                            on_click=alternar_boleto_mapa,
                            args=(num,)
                        )


def procesar_retorno_pago(conn):
    qp = st.query_params
    mp_status = (qp_get(qp, "status", "") or qp_get(qp, "collection_status", "")).lower()
    mp_return = qp_get(qp, "mp_return", "").lower()
    payment_id = qp_get(qp, "payment_id", "") or qp_get(qp, "collection_id", "")
    ext_ref = qp_get(qp, "external_reference", "") or st.session_state.get("external_ref_activa", "")
    if mp_return == "failure" or mp_status in ["rejected", "cancelled", "canceled", "failure", "failed"]:
        cancelar_reserva_supabase_por_referencia(ext_ref, motivo="CANCELADO_USUARIO")
        limpiar_carrito_local(cancelar_reserva_supabase=False)
        limpiar_enlaces_pago_sin_vaciar_carrito()
        limpiar_query_manteniendo_cid()
        limpiar_cache_mapa()
        st.warning("Regresaste sin completar el pago. La reserva fue liberada para que puedas elegir otros boletos.")
        st.rerun()
    if payment_id and mp_status == "approved":
        if confirmar_si_venta_ya_existe(conn, ext_ref):
            st.rerun()
        pago = None
        try:
            respuesta = sdk.payment().get(payment_id).get("response", {}) if sdk else {}
            if respuesta.get("status") == "approved":
                respuesta["provider"] = "MERCADO_PAGO"
                if not respuesta.get("external_reference") and ext_ref:
                    respuesta["external_reference"] = ext_ref
                pago = respuesta
        except Exception as e:
            st.session_state.ultimo_error_pago = f"MP return get: {e}"
        if pago:
            datos = finalizar_pago_confirmado_app(conn, pago, ext_ref)
            st.session_state.boletos_confirmados = datos
            st.session_state.payment_success_id = "PAGO_CONFIRMADO"
            limpiar_carrito_local(cancelar_reserva_supabase=False)
            st.session_state.boletos_confirmados = datos
            limpiar_checkout_pendiente()
            limpiar_query_manteniendo_cid()
            st.rerun()
    if "stripe_cancelled" in qp:
        cancelar_reserva_supabase_por_referencia(ext_ref, motivo="CANCELADO_USUARIO")
        limpiar_carrito_local(cancelar_reserva_supabase=False)
        limpiar_enlaces_pago_sin_vaciar_carrito()
        limpiar_query_manteniendo_cid()
        limpiar_cache_mapa()
        st.warning("Regresaste sin completar el pago. La reserva fue liberada para que puedas elegir otros boletos.")
        st.rerun()
    stripe_session_id = qp_get(qp, "stripe_session_id", "")
    if stripe_session_id:
        if confirmar_si_venta_ya_existe(conn, ext_ref):
            st.rerun()
        pago = obtener_pago_stripe(stripe_session_id, ext_ref if ext_ref else None)
        if pago:
            datos = finalizar_pago_confirmado_app(conn, pago, ext_ref)
            st.session_state.boletos_confirmados = datos
            st.session_state.payment_success_id = "PAGO_CONFIRMADO"
            limpiar_carrito_local(cancelar_reserva_supabase=False)
            st.session_state.boletos_confirmados = datos
            limpiar_checkout_pendiente()
            limpiar_query_manteniendo_cid()
            st.rerun()

# ============================================================
# UI
# ============================================================

def inicializar_estado():
    defaults = {
        "session_id": str(uuid.uuid4()),
        "selected_tickets": [],
        "payment_success_id": None,
        "pago_generado_url": None,
        "stripe_pago_url": None,
        "stripe_session_id": None,
        "payment_provider": None,
        "errores_proveedores": [],
        "ultimo_error_pago": "",
        "external_ref_activa": None,
        "boletos_confirmados": [],
        "consulta_resultados": [],
        "consulta_status": "",
        "limpiar_consulta_campos": False,
        "checkout_pendiente": None,
        "checkout_proveedor": "Mercado Pago",
        "checkout_metodo_elegido": "",
        "_mapa_cache_ts": 0,
        "_mapa_cache_ventas": pd.DataFrame(columns=columnas_ventas()),
        "_mapa_cache_reservas": pd.DataFrame(columns=columnas_reservas())
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v
    asegurar_client_id_en_url()


def mostrar_diagnostico_pagos():
    if DEBUG_PAGOS:
        with st.expander("Diagnostico tecnico"):
            st.json({"MP_ACCESS_TOKEN_configurado": bool(MP_ACCESS_TOKEN), "STRIPE_SECRET_KEY_configurado": bool(STRIPE_SECRET_KEY), "ultimo_error_pago": st.session_state.get("ultimo_error_pago", "")})



def sincronizar_checkout_con_seleccion_local():
    """Permite agregar mas boletos al checkout usando los datos ya capturados."""
    checkout = st.session_state.get("checkout_pendiente")
    if not isinstance(checkout, dict):
        return

    boletos_actuales = [parse_ticket_number(b) for b in checkout.get("boletos", []) if parse_ticket_number(b)]
    seleccionados = [parse_ticket_number(b) for b in st.session_state.get("selected_tickets", []) if parse_ticket_number(b)]
    union = []
    for boleto in boletos_actuales + seleccionados:
        if boleto and boleto not in union:
            union.append(boleto)

    if union != boletos_actuales:
        checkout["boletos"] = union
        checkout["total"] = float(PRECIO_BOLETO) * len(union)
        st.session_state.checkout_pendiente = checkout
        # Si cambia el total por agregar boletos, cualquier link previo deja de ser valido.
        st.session_state.pago_generado_url = None
        st.session_state.stripe_pago_url = None
        st.session_state.stripe_session_id = None


def sincronizar_reserva_checkout_en_sheets(conn: GSheetsConnection) -> Tuple[bool, str]:
    """Antes de pagar, sincroniza todos los boletos del checkout.

    Modo rapido:
    - Si Supabase esta activo, actualiza/crea la reserva completa en Supabase.
    - Si COPIAR_SHEETS_DESDE_APP=false, no espera a Google Sheets.
    - Si COPIAR_SHEETS_DESDE_APP=true, deja Sheets como copia visual.
    """
    checkout = st.session_state.get("checkout_pendiente")
    if not isinstance(checkout, dict):
        return False, "No existe checkout pendiente."

    ref = limpiar_valor_id(checkout.get("external_reference", ""))
    boletos_checkout = [parse_ticket_number(b) for b in checkout.get("boletos", []) if parse_ticket_number(b)]
    if not ref or not boletos_checkout:
        return False, "La reserva no contiene referencia o boletos."

    nombre_completo = f"{checkout.get('nombre', '').strip()} {checkout.get('apellidos', '').strip()}".strip()
    correo = str(checkout.get("correo", "")).strip().lower()
    telefono = str(checkout.get("telefono", "")).strip()

    # 1. Fuente principal: Supabase.
    if supabase_activo():
        ordenes_supabase = []
        ahora_txt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        expira_txt = (datetime.now() + timedelta(minutes=TIEMPO_RESERVA_MINUTOS)).strftime("%Y-%m-%d %H:%M:%S")
        for boleto in boletos_checkout:
            ordenes_supabase.append({
                "External_Reference": ref,
                "MercadoPago_Preference_ID": "",
                "MercadoPago_Payment_ID": "",
                "Stripe_Session_ID": "",
                "Stripe_Payment_ID": "",
                "Numero_Boleto": boleto,
                "Nombre": nombre_completo,
                "Correo": correo,
                "Numero_Telefonico": telefono,
                "Monto": float(PRECIO_BOLETO),
                "Estado_Reserva": "PENDIENTE",
                "Fecha_Creacion": ahora_txt,
                "Expira_En": expira_txt,
                "Fecha_Actualizacion": ahora_txt,
            })

        ok_supabase, msg_supabase = reservar_boletos_supabase(ordenes_supabase)
        if not ok_supabase:
            return False, msg_supabase

        limpiar_cache_mapa()
        if not COPIAR_SHEETS_DESDE_APP:
            return True, "Reserva sincronizada en Supabase."

    # 2. Copia visual/fallback Google Sheets.
    df_r = preparar_dataframe_para_update(leer_reservas(conn, ttl_segundos=3), columnas_reservas())
    if obtener_error_lectura_sheets():
        if supabase_activo():
            return True, "Reserva sincronizada en Supabase. Advertencia Sheets: " + obtener_error_lectura_sheets()
        return False, obtener_error_lectura_sheets()

    df_v = preparar_dataframe_para_update(leer_ventas(conn, ttl_segundos=3), columnas_ventas())
    if obtener_error_lectura_sheets():
        if supabase_activo():
            return True, "Reserva sincronizada en Supabase. Advertencia Sheets: " + obtener_error_lectura_sheets()
        return False, obtener_error_lectura_sheets()

    ya_reservados_ref = []
    if not df_r.empty:
        ya_reservados_ref = df_r[df_r["External_Reference"].astype(str) == ref]["Numero_Boleto"].astype(str).apply(parse_ticket_number).tolist()

    boletos_nuevos = [b for b in boletos_checkout if b not in ya_reservados_ref]
    if not boletos_nuevos:
        return True, "Reserva sincronizada."

    disponible, mensaje = boletos_disponibles_para_reservar(boletos_nuevos, df_v, df_r, external_reference_actual=ref)
    if not disponible and not supabase_activo():
        return False, mensaje

    ahora_txt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    expira_txt = (datetime.now() + timedelta(minutes=TIEMPO_RESERVA_MINUTOS)).strftime("%Y-%m-%d %H:%M:%S")
    nuevas = []
    for boleto in boletos_nuevos:
        nuevas.append({
            "External_Reference": ref,
            "MercadoPago_Preference_ID": "",
            "MercadoPago_Payment_ID": "",
            "Stripe_Session_ID": "",
            "Stripe_Payment_ID": "",
            "Numero_Boleto": boleto,
            "Nombre": nombre_completo,
            "Correo": correo,
            "Numero_Telefonico": telefono,
            "Monto": float(PRECIO_BOLETO),
            "Estado_Reserva": "PENDIENTE",
            "Fecha_Creacion": ahora_txt,
            "Expira_En": expira_txt,
            "Fecha_Actualizacion": ahora_txt,
        })

    df_final = pd.concat([df_r.dropna(how="all"), preparar_dataframe_para_update(pd.DataFrame(nuevas), columnas_reservas())], ignore_index=True)
    conn.update(worksheet="Reservas", data=preparar_dataframe_para_update(df_final, columnas_reservas()))
    return True, "Reserva actualizada con boletos adicionales."


def renderizar_checkout_pendiente(conn: GSheetsConnection):
    sincronizar_checkout_con_seleccion_local()
    checkout = st.session_state.get("checkout_pendiente")
    if not isinstance(checkout, dict) or not checkout.get("external_reference"):
        limpiar_checkout_pendiente()
        st.warning("La reserva temporal no esta disponible. Selecciona tus boletos nuevamente.")
        return

    boletos_checkout = [parse_ticket_number(b) for b in checkout.get("boletos", []) if parse_ticket_number(b)]
    if not boletos_checkout:
        limpiar_checkout_pendiente()
        st.warning("La reserva no contiene boletos. Selecciona tus boletos nuevamente.")
        return

    total_checkout = float(PRECIO_BOLETO) * len(boletos_checkout)
    checkout["boletos"] = boletos_checkout
    checkout["total"] = total_checkout
    st.session_state.checkout_pendiente = checkout

    ref_checkout = checkout.get("external_reference", "")
    metodo = st.session_state.get("checkout_metodo_elegido", "")

    st.success(f"✅ Reserva lista para pago: {', '.join(boletos_checkout)}")
    st.write(f"### 💰 Total a pagar: ${total_checkout:.2f} MXN")
    st.caption("Puedes agregar o quitar boletos desde el mapa. Si quieres empezar de cero, usa Cancelar reserva y vaciar carrito para liberar los boletos en Supabase.")

    st.markdown(f"""
    <style>
    .st-key-btn_pago_opcion_mp button {{
        min-height:66px!important; border-radius:16px!important; border-style:dashed!important; border-width:2px!important;
        font-weight:950!important; background:{'linear-gradient(145deg,#10B981,#065F46)' if metodo == 'Mercado Pago' else 'linear-gradient(145deg,#D1FAE5,#ECFDF5)'}!important;
        border-color:{'#065F46' if metodo == 'Mercado Pago' else '#10B981'}!important; color:{'#FFFFFF' if metodo == 'Mercado Pago' else '#064E3B'}!important;
    }}
    .st-key-btn_pago_opcion_stripe button {{
        min-height:66px!important; border-radius:16px!important; border-style:dashed!important; border-width:2px!important;
        font-weight:950!important; background:{'linear-gradient(145deg,#2563EB,#1E3A8A)' if metodo == 'Stripe' else 'linear-gradient(145deg,#DBEAFE,#EFF6FF)'}!important;
        border-color:{'#1E3A8A' if metodo == 'Stripe' else '#2563EB'}!important; color:{'#FFFFFF' if metodo == 'Stripe' else '#1E3A8A'}!important;
    }}
    [data-testid="stLinkButton"] a {{
        min-height:58px!important; border-radius:14px!important; font-weight:950!important;
        background:linear-gradient(135deg,#DC2626,#7F1D1D)!important; color:#FFFFFF!important; border:0!important;
        box-shadow:0 7px 18px rgba(220,38,38,.36)!important;
    }}
    </style>
    """, unsafe_allow_html=True)

    col_mp, col_st = st.columns(2)
    with col_mp:
        st.button("🎟️ 💙 Mercado Pago", use_container_width=True, key="btn_pago_opcion_mp", on_click=seleccionar_metodo_pago_checkout, args=("Mercado Pago",))
    with col_st:
        st.button("🎟️ 💳 Stripe", use_container_width=True, key="btn_pago_opcion_stripe", on_click=seleccionar_metodo_pago_checkout, args=("Stripe",))

    if not metodo:
        st.warning("Selecciona un metodo de pago para activar Realizar pago.")
    else:
        st.info(f"Metodo seleccionado: {metodo}")

    url_pago = ""
    if metodo:
        if metodo == "Mercado Pago":
            url_pago = st.session_state.get("pago_generado_url") or ""
        elif metodo == "Stripe":
            url_pago = st.session_state.get("stripe_pago_url") or ""

        if not url_pago:
            ok_sync, msg_sync = sincronizar_reserva_checkout_en_sheets(conn)
            if not ok_sync:
                st.error(f"No fue posible actualizar la reserva antes del pago: {msg_sync}")
                return

            checkout = st.session_state.get("checkout_pendiente", {})
            boletos_checkout = [parse_ticket_number(b) for b in checkout.get("boletos", []) if parse_ticket_number(b)]
            errores = []
            if True:
                if metodo == "Mercado Pago":
                    try:
                        pref_id, init_point = crear_preferencia_mercado_pago(
                            checkout.get("nombre", ""),
                            checkout.get("apellidos", ""),
                            checkout.get("correo", ""),
                            checkout.get("telefono", ""),
                            boletos_checkout,
                            PRECIO_BOLETO,
                            ref_checkout
                        )
                    except Exception as e:
                        pref_id, init_point = "", ""
                        errores.append(f"Mercado Pago: {e}")

                    if init_point:
                        st.session_state.pago_generado_url = init_point
                        st.session_state.stripe_pago_url = None
                        st.session_state.stripe_session_id = None
                        actualizar_ids_proveedores_reserva(conn, ref_checkout, pref_id, "")
                        url_pago = init_point
                    else:
                        st.error("No fue posible preparar Mercado Pago. " + " | ".join(errores))
                        return

                elif metodo == "Stripe":
                    try:
                        sid, surl = crear_sesion_stripe(
                            checkout.get("nombre", ""),
                            checkout.get("apellidos", ""),
                            checkout.get("correo", ""),
                            boletos_checkout,
                            PRECIO_BOLETO,
                            ref_checkout
                        )
                    except Exception as e:
                        sid, surl = "", ""
                        errores.append(f"Stripe: {e}")

                    if surl:
                        st.session_state.stripe_session_id = sid
                        st.session_state.stripe_pago_url = surl
                        st.session_state.pago_generado_url = None
                        actualizar_ids_proveedores_reserva(conn, ref_checkout, "", sid)
                        url_pago = surl
                    else:
                        st.error("No fue posible preparar Stripe. " + " | ".join(errores))
                        return

    if url_pago:
        st.markdown('<div class="st-key-btn_realizar_pago_directo">', unsafe_allow_html=True)
        st.link_button("✅ Realizar pago", url=url_pago, type="primary", use_container_width=True)
        st.markdown("</div>", unsafe_allow_html=True)

    st.divider()
    if st.button("🧹 Cancelar reserva y vaciar carrito", use_container_width=True, key="btn_cancelar_checkout_pendiente"):
        if ref_checkout:
            cancelar_reserva_supabase_por_referencia(ref_checkout, motivo="CANCELADO_USUARIO")
            liberar_reserva_por_rechazo_o_cancelacion(conn, ref_checkout, "CANCELADO_USUARIO")
        limpiar_carrito_local(cancelar_reserva_supabase=False)
        limpiar_cache_mapa()
        limpiar_query_manteniendo_cid()
        st.success("Reserva liberada. Ya puedes elegir otros boletos.")
        st.rerun()


def main():
    st.set_page_config(page_title=NOMBRE_EVENTO, page_icon="🎟️", layout="wide")
    st.markdown(CSS_CUSTOM.replace("{COLUMNAS_MAPA}", str(COLUMNAS_MAPA)), unsafe_allow_html=True)
    inicializar_estado()
    conn = st.connection("gsheets", type=GSheetsConnection)
    procesar_retorno_pago(conn)
    st.title(f"🎟️ Plataforma de Boletos - {NOMBRE_EVENTO}")
    tab1, tab2 = st.tabs(["🛒 Comprar Boletos", "🎫 Buscar mis Boletos / Verificar Pago"])
    with tab2:
        st.markdown("### 🎫 Consulta tus boletos")
        if st.session_state.get("limpiar_consulta_campos"):
            st.session_state["buscar_numero_boleto"] = ""
            st.session_state["buscar_correo_boleto"] = ""
            st.session_state.limpiar_consulta_campos = False
        c1, c2 = st.columns(2)
        with c1:
            st.text_input(f"Numero de boleto (ej. {formatear_numero_boleto(min(5, TOTAL_BOLETOS - 1))}):", key="buscar_numero_boleto")
        with c2:
            st.text_input("Correo asociado:", key="buscar_correo_boleto")
        if st.button("🔴 Verificar Pago y Descargar PDF", type="primary", key="btn_verificar_pago_pdf"):
            buscar_num = st.session_state.get("buscar_numero_boleto", "")
            buscar_correo = st.session_state.get("buscar_correo_boleto", "")
            st.session_state.consulta_resultados = []
            st.session_state.consulta_status = ""
            if not buscar_num or not buscar_correo:
                st.session_state.consulta_status = "warning|Ingresa boleto y correo."
                st.rerun()
            with st.spinner("Verificando pago y recuperando boletos..."):
                datos = recuperar_boletos_por_reserva(conn, buscar_num, buscar_correo)
                if datos:
                    st.session_state.consulta_resultados = datos
                    st.session_state.consulta_status = "success|Boletos encontrados. Puedes descargar tu PDF."
                else:
                    st.session_state.consulta_status = "error|No encontramos boletos pagados con esos datos. Verifica correo y boleto o intenta nuevamente."
                st.session_state.limpiar_consulta_campos = True
                st.rerun()
        status = st.session_state.get("consulta_status", "")
        if status:
            tipo, mensaje = status.split("|", 1) if "|" in status else ("info", status)
            if tipo == "success": st.success(f"✅ {mensaje}")
            elif tipo == "warning": st.warning(mensaje)
            elif tipo == "error": st.error(mensaje); mostrar_diagnostico_pagos()
            else: st.info(mensaje)
        if st.session_state.get("consulta_resultados"):
            procesar_descarga_pdf(st.session_state.consulta_resultados)
            if st.button("🧹 Limpiar resultado de consulta", use_container_width=True):
                st.session_state.consulta_resultados = []
                st.session_state.consulta_status = ""
                st.session_state.limpiar_consulta_campos = True
                st.rerun()
    with tab1:
        if st.session_state.get("boletos_confirmados"):
            st.balloons()
            st.success(f"✅ Compra confirmada. Tus boletos están listos: {texto_boletos(st.session_state.boletos_confirmados)}")
            procesar_descarga_pdf(st.session_state.boletos_confirmados)
            if st.button("🛒 Realizar otra compra", use_container_width=True):
                st.session_state.boletos_confirmados = []
                limpiar_carrito_local()
                limpiar_query_manteniendo_cid()
                st.rerun()
            st.stop()
        col_mapa, col_form = st.columns([1.5, 1], gap="large")
        with col_mapa:
            st.subheader("🎫 Mapa de Disponibilidad")
            renderizar_mapa_interactivo()
        with col_form:
            st.subheader("🧾 Finalizar Compra")
            boletos = st.session_state.selected_tickets
            with st.container(border=True):
                if hay_checkout_pendiente():
                    renderizar_checkout_pendiente(conn)
                elif not boletos:
                    st.info("🟢 Selecciona uno o mas boletos disponibles.")
                    limpiar_enlaces_pago_sin_vaciar_carrito()
                else:
                    total_pagar = PRECIO_BOLETO * len(boletos)
                    st.success(f"🎟️ En tu carrito: {', '.join(boletos)}")
                    c1, c2 = st.columns(2)
                    with c1:
                        nombre = st.text_input("Nombre(s):")
                    with c2:
                        apellidos = st.text_input("Apellidos:")
                    cu, cd = st.columns([3, 2.5])
                    with cu:
                        correo_usuario = st.text_input("Correo (sin @):", placeholder="ej. juanperez")
                    with cd:
                        dominio = st.selectbox("Extension:", ["@gmail.com", "@hotmail.com", "@outlook.com", "@yahoo.com", "Otro..."])
                    correo = st.text_input("Correo completo:", placeholder="usuario@empresa.com") if dominio == "Otro..." else (f"{correo_usuario.replace('@','').strip()}{dominio}" if correo_usuario else "")
                    telefono = st.text_input("WhatsApp (10 digitos):", max_chars=10)
                    st.write(f"**💰 Total a Pagar:** ${total_pagar:.2f} MXN")
                    if st.button("🔴 Confirmar y Elegir Metodo de Pago", type="primary", use_container_width=True, key="btn_confirmar_metodo_pago"):
                        asegurar_client_id_en_url()
                        pre = obtener_pre_reservas_globales()
                        ahora = datetime.now()
                        siguen = all(t in pre and pre[t].get("session_id") == st.session_state.session_id and pre[t].get("expires_at") > ahora for t in boletos)
                        correo_valido = re.match(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z]{2,}$", correo.strip().lower())
                        if not siguen:
                            st.error("El tiempo de carrito expiro. Selecciona nuevamente.")
                            st.session_state.selected_tickets = []
                        elif not nombre or not apellidos or not correo or not telefono:
                            st.error("Completa todos los campos.")
                        elif not correo_valido:
                            st.error("El formato del correo NO es valido.")
                        elif not (telefono.isdigit() and len(telefono) == 10):
                            st.error("El numero debe contener 10 digitos numericos.")
                        else:
                            ref = f"RIFA-{datetime.now().strftime('%Y%m%d%H%M%S')}-{random.randint(1000,9999)}"
                            st.session_state.external_ref_activa = ref
                            ordenes = []
                            for t in boletos:
                                ordenes.append({"External_Reference": ref, "MercadoPago_Preference_ID": "", "MercadoPago_Payment_ID": "", "Stripe_Session_ID": "", "Stripe_Payment_ID": "", "Numero_Boleto": str(t), "Nombre": f"{nombre.strip()} {apellidos.strip()}", "Correo": correo.strip().lower(), "Numero_Telefonico": telefono, "Monto": float(PRECIO_BOLETO), "Estado_Reserva": "PENDIENTE", "Fecha_Creacion": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "Expira_En": (datetime.now() + timedelta(minutes=TIEMPO_RESERVA_MINUTOS)).strftime("%Y-%m-%d %H:%M:%S"), "Fecha_Actualizacion": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
                            exito, msg = registrar_reserva_cobro(conn, ordenes)
                            if not exito:
                                st.error(f"Error al registrar la reserva: {msg}")
                            else:
                                guardar_checkout_pendiente(ref, nombre, apellidos, correo, telefono, boletos)
                                limpiar_cache_mapa()
                                st.success("Reserva creada correctamente. Ahora elige el metodo de pago.")
                                st.rerun()

if __name__ == "__main__":
    main()
