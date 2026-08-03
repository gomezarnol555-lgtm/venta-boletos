import os
import json
import hmac
import hashlib
import random
import time
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd
import stripe
import requests
import gspread
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from google.oauth2.service_account import Credentials

try:
    from supabase import create_client, Client
except Exception:
    create_client = None
    Client = None

app = FastAPI(title="Webhook Pagos Rifa", version="4.2.0-stripe-fast-ack")

# ============================================================
# VARIABLES DE ENTORNO
# ============================================================
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "").strip()
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
GOOGLE_SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "").strip()
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()

MP_ACCESS_TOKEN = os.getenv("MP_ACCESS_TOKEN", "").strip()
MP_WEBHOOK_SECRET = os.getenv("MP_WEBHOOK_SECRET", "").strip()

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()

DEFAULT_EVENTO = os.getenv("DEFAULT_EVENTO", "Rifa de Celular").strip()
DEFAULT_CURRENCY_MP = os.getenv("MP_CURRENCY_ID", "MXN").strip().upper()
DEFAULT_CURRENCY_STRIPE = os.getenv("STRIPE_CURRENCY_ID", "mxn").strip().lower()
TOTAL_BOLETOS = int(os.getenv("TOTAL_BOLETOS", "100"))
DIGITOS_BOLETO = max(1, len(str(max(TOTAL_BOLETOS - 1, 0))))
MP_SIGNATURE_TOLERANCE_SECONDS = int(os.getenv("MP_SIGNATURE_TOLERANCE_SECONDS", "300"))
COPIAR_A_SHEETS = os.getenv("COPIAR_A_SHEETS", "true").strip().lower() in ["1", "true", "si", "yes", "on"]
SUPABASE_FALLBACK_SHEETS = os.getenv("SUPABASE_FALLBACK_SHEETS", "true").strip().lower() in ["1", "true", "si", "yes", "on"]

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

supabase: Optional[Client] = None
if create_client is not None and SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

SHEETS_LOCK = threading.Lock()


# ============================================================
# COLUMNAS GOOGLE SHEETS
# ============================================================
def columnas_reservas() -> list:
    return [
        "External_Reference",
        "MercadoPago_Preference_ID",
        "MercadoPago_Payment_ID",
        "Stripe_Session_ID",
        "Stripe_Payment_ID",
        "Numero_Boleto",
        "Nombre",
        "Correo",
        "Numero_Telefonico",
        "Monto",
        "Estado_Reserva",
        "Fecha_Creacion",
        "Expira_En",
        "Fecha_Actualizacion",
    ]


def columnas_ventas() -> list:
    return [
        "ID_Boleto",
        "Nombre",
        "Correo",
        "Evento",
        "Numero_Boleto",
        "Precio",
        "Metodo_Pago",
        "Codigo_Pago",
        "Fecha_Compra",
        "Numero_Telefonico",
        "Estado_Pago",
        "Referencia_Pago",
        "MercadoPago_Payment_ID",
        "MercadoPago_Preference_ID",
        "Stripe_Payment_ID",
        "Stripe_Session_ID",
        "Proveedor_Pago",
    ]


def columnas_auditoria() -> list:
    return [
        "Fecha_Hora",
        "Proveedor",
        "Evento",
        "External_Reference",
        "Payment_ID",
        "Session_ID",
        "Monto",
        "Moneda",
        "Resultado",
        "Mensaje",
        "Request_ID",
        "Data_ID",
    ]


# ============================================================
# UTILIDADES
# ============================================================
def ahora_txt() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def limpiar_valor(valor: Any) -> str:
    valor = str(valor or "").strip()
    if valor.lower() in ["nan", "none", "null", "nat"]:
        return ""
    return valor


def parse_ticket_number(valor: Any) -> str:
    if pd.isna(valor) or str(valor).strip() == "":
        return ""
    try:
        return f"{int(float(valor)):0{DIGITOS_BOLETO}d}"
    except Exception:
        return str(valor).strip().zfill(DIGITOS_BOLETO)


def normalizar_float(valor: Any) -> float:
    try:
        return round(float(valor or 0), 2)
    except Exception:
        return 0.0


def safe_to_dict(obj: Any) -> Dict[str, Any]:
    try:
        if obj is None:
            return {}
        if isinstance(obj, dict):
            return obj
        if hasattr(obj, "to_dict_recursive"):
            data = obj.to_dict_recursive()
            return data if isinstance(data, dict) else {}
        if hasattr(obj, "to_dict"):
            data = obj.to_dict()
            return data if isinstance(data, dict) else {}
        return dict(obj)
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


def asegurar_columnas(df: pd.DataFrame, columnas: list) -> pd.DataFrame:
    df = df.copy()
    for col in columnas:
        if col not in df.columns:
            df[col] = ""
    df = df[columnas].astype("object")
    df = df.where(pd.notna(df), "")
    for col in df.columns:
        if col not in ["Monto", "Precio"]:
            df[col] = df[col].apply(limpiar_valor)
    return df


def es_error_cuota(error: Exception) -> bool:
    texto = str(error).lower()
    return "429" in texto or "quota" in texto or "rate" in texto or "resource_exhausted" in texto


def ejecutar_con_reintento(func, *args, **kwargs):
    ultimo_error = None
    for intento in range(5):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            ultimo_error = e
            if not es_error_cuota(e):
                raise
            time.sleep(min(2 ** intento, 16))
    raise ultimo_error


# ============================================================
# GOOGLE SHEETS
# ============================================================
def cliente_gspread():
    if not SPREADSHEET_ID:
        raise RuntimeError("Falta SPREADSHEET_ID")

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    if GOOGLE_SERVICE_ACCOUNT_JSON:
        info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
        creds = Credentials.from_service_account_info(info, scopes=scopes)
    elif GOOGLE_SERVICE_ACCOUNT_FILE:
        creds = Credentials.from_service_account_file(GOOGLE_SERVICE_ACCOUNT_FILE, scopes=scopes)
    else:
        raise RuntimeError("Falta GOOGLE_SERVICE_ACCOUNT_JSON o GOOGLE_SERVICE_ACCOUNT_FILE")

    return gspread.authorize(creds)


def abrir_sheet():
    return ejecutar_con_reintento(cliente_gspread().open_by_key, SPREADSHEET_ID)


def obtener_worksheet(nombre: str, columnas: list):
    sh = abrir_sheet()
    try:
        return ejecutar_con_reintento(sh.worksheet, nombre)
    except gspread.WorksheetNotFound:
        ws = ejecutar_con_reintento(
            sh.add_worksheet,
            title=nombre,
            rows=1000,
            cols=max(len(columnas), 1),
        )
        ejecutar_con_reintento(ws.update, [columnas])
        return ws


def leer_worksheet(nombre: str, columnas: list) -> pd.DataFrame:
    ws = obtener_worksheet(nombre, columnas)
    registros = ejecutar_con_reintento(ws.get_all_records)
    df = pd.DataFrame(registros)
    if df.empty:
        df = pd.DataFrame(columns=columnas)
    return asegurar_columnas(df, columnas)


def append_worksheet(nombre: str, filas: List[Dict[str, Any]], columnas: list) -> None:
    if not filas:
        return
    ws = obtener_worksheet(nombre, columnas)
    df = asegurar_columnas(pd.DataFrame(filas), columnas)
    ejecutar_con_reintento(
        ws.append_rows,
        df.astype("object").where(pd.notna(df), "").values.tolist(),
        value_input_option="USER_ENTERED",
    )


def escribir_worksheet(nombre: str, df: pd.DataFrame, columnas: list) -> None:
    ws = obtener_worksheet(nombre, columnas)
    df = asegurar_columnas(df, columnas)
    valores = [columnas] + df.astype("object").where(pd.notna(df), "").values.tolist()
    ejecutar_con_reintento(ws.clear)
    ejecutar_con_reintento(ws.update, valores, value_input_option="USER_ENTERED")


def leer_reservas() -> pd.DataFrame:
    return leer_worksheet("Reservas", columnas_reservas())


def leer_ventas() -> pd.DataFrame:
    return leer_worksheet("Ventas", columnas_ventas())


def escribir_reservas(df: pd.DataFrame) -> None:
    escribir_worksheet("Reservas", df, columnas_reservas())


def append_ventas(filas: List[Dict[str, Any]]) -> None:
    append_worksheet("Ventas", filas, columnas_ventas())


def registrar_auditoria_sheets(
    proveedor: str,
    evento: str,
    external_reference: str = "",
    payment_id: str = "",
    session_id: str = "",
    monto: Any = "",
    moneda: str = "",
    resultado: str = "",
    mensaje: str = "",
    request_id: str = "",
    data_id: str = "",
) -> None:
    if not COPIAR_A_SHEETS or not SPREADSHEET_ID:
        return
    try:
        append_worksheet(
            "Auditoria",
            [
                {
                    "Fecha_Hora": ahora_txt(),
                    "Proveedor": proveedor,
                    "Evento": evento,
                    "External_Reference": external_reference,
                    "Payment_ID": payment_id,
                    "Session_ID": session_id,
                    "Monto": monto,
                    "Moneda": moneda,
                    "Resultado": resultado,
                    "Mensaje": str(mensaje)[:500],
                    "Request_ID": request_id,
                    "Data_ID": data_id,
                }
            ],
            columnas_auditoria(),
        )
    except Exception:
        pass


# ============================================================
# MERCADO PAGO FIRMA
# ============================================================
def parsear_header_x_signature(x_signature: str) -> Dict[str, str]:
    partes = {}
    for parte in str(x_signature or "").split(","):
        if "=" in parte:
            k, v = parte.split("=", 1)
            partes[k.strip()] = v.strip()
    return partes


def normalizar_mp_data_id(data_id: str) -> str:
    data_id = limpiar_valor(data_id)
    if data_id and any(c.isalpha() for c in data_id):
        return data_id.lower()
    return data_id


def extraer_data_id_mp(payload: Dict[str, Any], request: Request) -> str:
    data = payload.get("data") or {}
    opciones = [
        request.query_params.get("data.id", ""),
        request.query_params.get("id", ""),
        request.query_params.get("data_id", ""),
        data.get("id", "") if isinstance(data, dict) else "",
        payload.get("id", ""),
    ]
    for opcion in opciones:
        opcion = limpiar_valor(opcion)
        if opcion:
            return opcion
    return ""


def construir_manifest_mp(data_id: str, x_request_id: str, ts: str) -> str:
    manifest = ""
    data_id = normalizar_mp_data_id(data_id)
    if data_id:
        manifest += f"id:{data_id};"
    if x_request_id:
        manifest += f"request-id:{limpiar_valor(x_request_id)};"
    if ts:
        manifest += f"ts:{limpiar_valor(ts)};"
    return manifest


def validar_timestamp_mp(ts: str) -> None:
    try:
        ts_int = int(ts)
    except Exception:
        raise HTTPException(status_code=401, detail="Timestamp Mercado Pago invalido")
    ahora = int(datetime.now().timestamp())
    if abs(ahora - ts_int) > MP_SIGNATURE_TOLERANCE_SECONDS:
        raise HTTPException(status_code=401, detail="Timestamp Mercado Pago fuera de tolerancia")


def validar_firma_mercadopago(payload: Dict[str, Any], request: Request) -> str:
    if not MP_WEBHOOK_SECRET:
        raise HTTPException(status_code=500, detail="MP_WEBHOOK_SECRET no configurado en Render")

    x_signature = request.headers.get("x-signature", "")
    x_request_id = request.headers.get("x-request-id", "")

    if not x_signature or not x_request_id:
        raise HTTPException(status_code=401, detail="Firma Mercado Pago ausente")

    partes = parsear_header_x_signature(x_signature)
    ts = partes.get("ts", "")
    firma_recibida = partes.get("v1", "")

    if not ts or not firma_recibida:
        raise HTTPException(status_code=401, detail="Firma Mercado Pago invalida")

    validar_timestamp_mp(ts)
    data_id = extraer_data_id_mp(payload, request)
    manifest = construir_manifest_mp(data_id, x_request_id, ts)
    firma_calculada = hmac.new(
        MP_WEBHOOK_SECRET.encode("utf-8"),
        manifest.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(firma_calculada, firma_recibida):
        raise HTTPException(status_code=401, detail="Firma Mercado Pago no coincide")

    return data_id


# ============================================================
# PAGOS Y SUPABASE
# ============================================================
def obtener_monto_pagado(payment_info: Dict[str, Any]) -> float:
    proveedor = limpiar_valor(payment_info.get("provider", "")).upper()
    if proveedor == "STRIPE":
        return round(float(payment_info.get("amount_total", 0) or 0) / 100, 2)
    if proveedor == "MERCADO_PAGO":
        return normalizar_float(payment_info.get("transaction_amount", 0))
    return 0.0


def obtener_moneda_pago(payment_info: Dict[str, Any]) -> str:
    proveedor = limpiar_valor(payment_info.get("provider", "")).upper()
    if proveedor == "STRIPE":
        return limpiar_valor(payment_info.get("currency", DEFAULT_CURRENCY_STRIPE)).upper()
    if proveedor == "MERCADO_PAGO":
        return limpiar_valor(payment_info.get("currency_id", DEFAULT_CURRENCY_MP)).upper()
    return ""


def debe_reconstruir_reserva_supabase(error: Exception) -> bool:
    texto = str(error).lower()
    return any(
        p in texto
        for p in [
            "no hay reservas pendientes",
            "no existe reserva",
            "reservas pendientes",
            "external_reference",
        ]
    )


def crear_reserva_supabase_desde_sheets(external_reference: str) -> Dict[str, Any]:
    if supabase is None:
        raise RuntimeError("Supabase no configurado para fallback")
    if not SPREADSHEET_ID:
        raise RuntimeError("Falta SPREADSHEET_ID para fallback")

    ext_ref = limpiar_valor(external_reference)
    df_r = leer_reservas()
    grupo = df_r[df_r["External_Reference"].astype(str) == ext_ref].copy()

    if grupo.empty:
        raise ValueError(f"No existe reserva en Sheets para External_Reference={ext_ref}")

    boletos = []
    for _, row in grupo.iterrows():
        boleto = parse_ticket_number(row.get("Numero_Boleto", ""))
        if boleto and boleto not in boletos:
            boletos.append(boleto)

    fila = grupo.iloc[0]
    nombre = limpiar_valor(fila.get("Nombre", "")) or "Cliente"
    correo = limpiar_valor(fila.get("Correo", "")) or "sin-correo@local"
    telefono = limpiar_valor(fila.get("Numero_Telefonico", ""))
    precio = float(grupo["Monto"].astype(float).iloc[0])

    params = {
        "p_external_reference": ext_ref,
        "p_boletos": boletos,
        "p_nombre": nombre,
        "p_correo": correo,
        "p_telefono": telefono,
        "p_precio": precio,
        "p_minutos_reserva": int(os.getenv("SUPABASE_RESERVA_MINUTOS_FALLBACK", "1440")),
    }

    result = supabase.rpc("reservar_boletos", params).execute()
    return {"ok": True, "data": getattr(result, "data", None), "boletos": boletos}


def confirmar_pago_en_supabase(payment_info: Dict[str, Any], payload_resumen: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if supabase is None:
        raise RuntimeError(
            "Supabase no configurado. Falta SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY o libreria supabase."
        )

    proveedor = limpiar_valor(payment_info.get("provider", "")).upper()
    ext_ref = limpiar_valor(payment_info.get("external_reference", ""))
    payment_id = limpiar_valor(payment_info.get("id", ""))
    session_id = limpiar_valor(payment_info.get("stripe_session_id", ""))
    preference_id = limpiar_valor(payment_info.get("mp_preference_id", ""))

    params = {
        "p_provider": proveedor,
        "p_external_reference": ext_ref,
        "p_payment_id": payment_id,
        "p_session_id": session_id,
        "p_preference_id": preference_id,
        "p_monto_pagado": obtener_monto_pagado(payment_info),
        "p_moneda": obtener_moneda_pago(payment_info),
        "p_metodo_pago": limpiar_valor(payment_info.get("payment_type_id", proveedor.lower())),
        "p_evento": DEFAULT_EVENTO,
        "p_payload": payload_resumen or {},
    }

    try:
        result = supabase.rpc("confirmar_pago_y_vender", params).execute()
    except Exception as e:
        if SUPABASE_FALLBACK_SHEETS and debe_reconstruir_reserva_supabase(e):
            crear_reserva_supabase_desde_sheets(ext_ref)
            result = supabase.rpc("confirmar_pago_y_vender", params).execute()
        else:
            raise

    data = getattr(result, "data", None)
    if isinstance(data, dict):
        return data
    return {"ok": True, "data": data}


def copiar_pago_a_sheets(payment_info: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not COPIAR_A_SHEETS or not SPREADSHEET_ID:
        return []

    ext_ref = limpiar_valor(payment_info.get("external_reference", ""))
    pago_id = limpiar_valor(payment_info.get("id", ""))
    proveedor = limpiar_valor(payment_info.get("provider", "")).upper()
    metodo_pago = limpiar_valor(payment_info.get("payment_type_id", proveedor.lower()))
    stripe_session_id = limpiar_valor(payment_info.get("stripe_session_id", ""))
    mp_preference_id = limpiar_valor(payment_info.get("mp_preference_id", ""))

    with SHEETS_LOCK:
        df_r = leer_reservas()
        df_v = leer_ventas()

        ventas_ref = df_v[df_v["Referencia_Pago"].astype(str) == ext_ref]
        if not ventas_ref.empty:
            return ventas_ref.to_dict(orient="records")

        filtro = df_r["External_Reference"].astype(str) == ext_ref
        if not filtro.any():
            return []

        grupo = df_r[filtro].copy()
        df_r.loc[filtro, "Estado_Reserva"] = "PAGADO"
        df_r.loc[filtro, "Fecha_Actualizacion"] = ahora_txt()

        if proveedor == "STRIPE":
            df_r.loc[filtro, "Stripe_Payment_ID"] = pago_id
            if stripe_session_id:
                df_r.loc[filtro, "Stripe_Session_ID"] = stripe_session_id
        else:
            df_r.loc[filtro, "MercadoPago_Payment_ID"] = pago_id
            if mp_preference_id:
                df_r.loc[filtro, "MercadoPago_Preference_ID"] = mp_preference_id

        nuevas = []
        for _, r in grupo.iterrows():
            nuevas.append(
                {
                    "ID_Boleto": f"BOL-{random.randint(10000, 99999)}",
                    "Nombre": r.get("Nombre", ""),
                    "Correo": r.get("Correo", ""),
                    "Evento": DEFAULT_EVENTO,
                    "Numero_Boleto": parse_ticket_number(r.get("Numero_Boleto", "")),
                    "Precio": r.get("Monto", ""),
                    "Metodo_Pago": metodo_pago,
                    "Codigo_Pago": pago_id,
                    "Fecha_Compra": ahora_txt(),
                    "Numero_Telefonico": r.get("Numero_Telefonico", ""),
                    "Estado_Pago": "VENDIDO",
                    "Referencia_Pago": ext_ref,
                    "MercadoPago_Payment_ID": pago_id if proveedor == "MERCADO_PAGO" else "",
                    "MercadoPago_Preference_ID": mp_preference_id if proveedor == "MERCADO_PAGO" else r.get("MercadoPago_Preference_ID", ""),
                    "Stripe_Payment_ID": pago_id if proveedor == "STRIPE" else "",
                    "Stripe_Session_ID": stripe_session_id if proveedor == "STRIPE" else "",
                    "Proveedor_Pago": proveedor,
                }
            )

        escribir_reservas(df_r)
        append_ventas(nuevas)
        return nuevas


def procesar_pago_confirmado(payment_info: Dict[str, Any], evento: str, payload_resumen: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    ext_ref = limpiar_valor(payment_info.get("external_reference", ""))
    pago_id = limpiar_valor(payment_info.get("id", ""))
    proveedor = limpiar_valor(payment_info.get("provider", "")).upper()
    session_id = limpiar_valor(payment_info.get("stripe_session_id", ""))
    monto = obtener_monto_pagado(payment_info)
    moneda = obtener_moneda_pago(payment_info)

    supabase_result = confirmar_pago_en_supabase(payment_info, payload_resumen)

    sheets_result = []
    sheets_error = ""
    if COPIAR_A_SHEETS and SPREADSHEET_ID:
        try:
            sheets_result = copiar_pago_a_sheets(payment_info)
        except Exception as e:
            sheets_error = str(e)

    registrar_auditoria_sheets(
        proveedor,
        evento,
        external_reference=ext_ref,
        payment_id=pago_id,
        session_id=session_id,
        monto=monto,
        moneda=moneda,
        resultado="OK",
        mensaje=f"Supabase OK. Sheets={len(sheets_result)}. {sheets_error}"[:500],
    )

    return {
        "ok": True,
        "provider": proveedor,
        "supabase": supabase_result,
        "sheets_ventas": len(sheets_result),
        "sheets_error": sheets_error,
    }


# ============================================================
# STRIPE
# ============================================================
def payment_info_desde_checkout_session(session: Any) -> Optional[Dict[str, Any]]:
    s = safe_to_dict(session)
    if str(safe_get(s, "payment_status", "")).lower() != "paid":
        return None

    metadata = safe_get(s, "metadata", {}) or {}
    payment_intent = safe_get(s, "payment_intent", "")
    if not isinstance(payment_intent, str):
        payment_intent = safe_get(payment_intent, "id", "")

    return {
        "id": limpiar_valor(payment_intent),
        "external_reference": limpiar_valor(metadata.get("external_reference") or safe_get(s, "client_reference_id", "")),
        "payment_type_id": "stripe_card",
        "provider": "STRIPE",
        "stripe_session_id": limpiar_valor(safe_get(s, "id", "")),
        "amount_total": safe_get(s, "amount_total", 0),
        "currency": limpiar_valor(safe_get(s, "currency", DEFAULT_CURRENCY_STRIPE)).lower(),
    }


def payment_info_desde_payment_intent(intent: Any) -> Optional[Dict[str, Any]]:
    i = safe_to_dict(intent)
    if str(safe_get(i, "status", "")).lower() != "succeeded":
        return None

    metadata = safe_get(i, "metadata", {}) or {}
    return {
        "id": limpiar_valor(safe_get(i, "id", "")),
        "external_reference": limpiar_valor(metadata.get("external_reference", "")),
        "payment_type_id": "stripe_card",
        "provider": "STRIPE",
        "stripe_session_id": "",
        "amount_total": safe_get(i, "amount", 0),
        "currency": limpiar_valor(safe_get(i, "currency", DEFAULT_CURRENCY_STRIPE)).lower(),
    }


def procesar_evento_stripe_en_segundo_plano(event_type: str, data_obj_dict: Dict[str, Any]):
    """
    Procesa Stripe después de responder 200 a Stripe.
    Esto evita timeouts y evita que Stripe deshabilite el webhook por tardanza.
    No cambia la lógica funcional de venta: sigue confirmando en Supabase y, si está activo, copia a Sheets.
    """
    try:
        info = None

        if event_type in ["checkout.session.completed", "checkout.session.async_payment_succeeded"]:
            info = payment_info_desde_checkout_session(data_obj_dict)
        elif event_type == "payment_intent.succeeded":
            info = payment_info_desde_payment_intent(data_obj_dict)

        if info and info.get("external_reference"):
            procesar_pago_confirmado(info, event_type, data_obj_dict)
            return

        registrar_auditoria_sheets(
            "STRIPE",
            event_type,
            resultado="IGNORADO",
            mensaje="Evento recibido sin accion requerida",
        )

    except Exception as e:
        registrar_auditoria_sheets(
            "STRIPE",
            event_type,
            resultado="ERROR",
            mensaje=f"Error segundo plano Stripe: {e}",
        )


@app.post("/webhook/stripe")
async def webhook_stripe(request: Request, background_tasks: BackgroundTasks):
    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=500, detail="STRIPE_WEBHOOK_SECRET no configurado en Render")

    payload = await request.body()
    sig = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except Exception as e:
        registrar_auditoria_sheets("STRIPE", "signature_error", resultado="ERROR", mensaje=str(e))
        raise HTTPException(status_code=400, detail=f"Webhook Stripe invalido: {e}")

    event_type = safe_get(event, "type", "")
    data_obj = safe_get(safe_get(event, "data", {}), "object", {})
    data_obj_dict = safe_to_dict(data_obj)

    eventos_procesables = [
        "checkout.session.completed",
        "checkout.session.async_payment_succeeded",
        "payment_intent.succeeded",
    ]

    if event_type not in eventos_procesables:
        return JSONResponse(
            {"ok": True, "ignored": True, "event_type": event_type},
            status_code=200,
        )

    background_tasks.add_task(
        procesar_evento_stripe_en_segundo_plano,
        event_type,
        data_obj_dict,
    )

    return JSONResponse(
        {"ok": True, "received": True, "event_type": event_type},
        status_code=200,
    )


# ============================================================
# MERCADO PAGO
# ============================================================
def obtener_payment_id_mp(payload: Dict[str, Any], request: Request) -> str:
    return extraer_data_id_mp(payload, request)


def consultar_pago_mp(payment_id: str) -> Optional[Dict[str, Any]]:
    if not MP_ACCESS_TOKEN:
        raise RuntimeError("Falta MP_ACCESS_TOKEN")

    url = f"https://api.mercadopago.com/v1/payments/{payment_id}"
    resp = requests.get(url, headers={"Authorization": f"Bearer {MP_ACCESS_TOKEN}"}, timeout=20)

    if resp.status_code >= 400:
        raise RuntimeError(f"Mercado Pago API error {resp.status_code}: {resp.text[:300]}")

    pago = resp.json()
    if pago.get("status") != "approved":
        return None
    return pago


def payment_info_desde_mp(pago: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not pago or pago.get("status") != "approved":
        return None

    return {
        "id": limpiar_valor(pago.get("id", "")),
        "external_reference": limpiar_valor(pago.get("external_reference", "")),
        "payment_type_id": limpiar_valor(pago.get("payment_type_id", "mercado_pago")),
        "provider": "MERCADO_PAGO",
        "mp_preference_id": limpiar_valor(pago.get("preference_id", "")),
        "transaction_amount": pago.get("transaction_amount", 0),
        "currency_id": limpiar_valor(pago.get("currency_id", DEFAULT_CURRENCY_MP)).upper(),
    }


@app.post("/webhook/mercadopago")
async def webhook_mercadopago(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    data_id = ""
    try:
        data_id = validar_firma_mercadopago(payload, request)
    except HTTPException as e:
        registrar_auditoria_sheets(
            "MERCADO_PAGO",
            "signature_error",
            resultado="ERROR",
            mensaje=str(e.detail),
            data_id=data_id,
        )
        raise

    payment_id = obtener_payment_id_mp(payload, request)
    topic = limpiar_valor(payload.get("type") or payload.get("topic") or request.query_params.get("topic", ""))

    if topic and topic not in ["payment", "payments"]:
        return JSONResponse({"ok": True, "ignored": True, "topic": topic})

    if not payment_id:
        return JSONResponse({"ok": True, "ignored": True, "reason": "sin payment_id"})

    try:
        pago = consultar_pago_mp(payment_id)
        info = payment_info_desde_mp(pago) if pago else None

        if info and info.get("external_reference"):
            res = procesar_pago_confirmado(info, topic or "payment", pago or payload)
            res["data_id"] = data_id
            return JSONResponse(res)

        return JSONResponse({"ok": True, "ignored": True, "payment_id": payment_id})

    except Exception as e:
        registrar_auditoria_sheets(
            "MERCADO_PAGO",
            topic or "payment",
            payment_id=payment_id,
            resultado="ERROR",
            mensaje=str(e),
            data_id=data_id,
        )
        raise HTTPException(status_code=500, detail=f"Error procesando Mercado Pago: {e}")


# ============================================================
# HEALTHCHECK
# ============================================================
@app.get("/")
def root():
    return {"ok": True, "service": "webhook-pagos-rifa-supabase", "version": app.version}


@app.get("/health")
def health():
    return {
        "ok": True,
        "spreadsheet_configurado": bool(SPREADSHEET_ID),
        "copiar_a_sheets": COPIAR_A_SHEETS,
        "fallback_reserva_desde_sheets": SUPABASE_FALLBACK_SHEETS,
        "stripe_configurado": bool(STRIPE_SECRET_KEY),
        "stripe_webhook_secret_configurado": bool(STRIPE_WEBHOOK_SECRET),
        "mp_configurado": bool(MP_ACCESS_TOKEN),
        "mp_webhook_secret_configurado": bool(MP_WEBHOOK_SECRET),
        "supabase_url_configurado": bool(SUPABASE_URL),
        "supabase_service_role_configurado": bool(SUPABASE_SERVICE_ROLE_KEY),
        "supabase_cliente_activo": supabase is not None,
        "total_boletos": TOTAL_BOLETOS,
        "digitos_boleto": DIGITOS_BOLETO,
    }
