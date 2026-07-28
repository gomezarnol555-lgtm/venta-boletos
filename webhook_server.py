import os
import json
import hmac
import hashlib
import random
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd
import stripe
import requests
import gspread
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from google.oauth2.service_account import Credentials

app = FastAPI(title="Webhook Pagos Rifa", version="2.0.0")

# ============================================================
# CONFIGURACION POR VARIABLES DE ENTORNO
# ============================================================
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "").strip()
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
GOOGLE_SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "").strip()
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()

MP_ACCESS_TOKEN = os.getenv("MP_ACCESS_TOKEN", "").strip()
MP_WEBHOOK_SECRET = os.getenv("MP_WEBHOOK_SECRET", "").strip()

DEFAULT_EVENTO = os.getenv("DEFAULT_EVENTO", "Rifa de Celular").strip()

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

# ============================================================
# COLUMNAS
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

# ============================================================
# UTILIDADES GENERALES
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
        return f"{int(float(valor)):03d}"
    except Exception:
        return str(valor).strip().zfill(3)


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
    return cliente_gspread().open_by_key(SPREADSHEET_ID)


def leer_worksheet(nombre: str, columnas: list) -> pd.DataFrame:
    sh = abrir_sheet()
    try:
        ws = sh.worksheet(nombre)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=nombre, rows=1000, cols=len(columnas))
        ws.update([columnas])

    registros = ws.get_all_records()
    df = pd.DataFrame(registros)
    if df.empty:
        df = pd.DataFrame(columns=columnas)
    return asegurar_columnas(df, columnas)


def escribir_worksheet(nombre: str, df: pd.DataFrame, columnas: list) -> None:
    sh = abrir_sheet()
    try:
        ws = sh.worksheet(nombre)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=nombre, rows=1000, cols=len(columnas))

    df = asegurar_columnas(df, columnas)
    valores = [columnas] + df.astype("object").where(pd.notna(df), "").values.tolist()
    ws.clear()
    ws.update(valores, value_input_option="USER_ENTERED")


def leer_reservas() -> pd.DataFrame:
    return leer_worksheet("Reservas", columnas_reservas())


def leer_ventas() -> pd.DataFrame:
    return leer_worksheet("Ventas", columnas_ventas())


def escribir_reservas(df: pd.DataFrame) -> None:
    escribir_worksheet("Reservas", df, columnas_reservas())


def escribir_ventas(df: pd.DataFrame) -> None:
    escribir_worksheet("Ventas", df, columnas_ventas())

# ============================================================
# FIRMA DE MERCADO PAGO
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


def construir_manifest_mp(data_id: str, x_request_id: str, ts: str) -> str:
    manifest = ""
    data_id = normalizar_mp_data_id(data_id)
    x_request_id = limpiar_valor(x_request_id)
    ts = limpiar_valor(ts)

    if data_id:
        manifest += f"id:{data_id};"
    if x_request_id:
        manifest += f"request-id:{x_request_id};"
    if ts:
        manifest += f"ts:{ts};"
    return manifest


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


def validar_firma_mercadopago(payload: Dict[str, Any], request: Request) -> None:
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

    data_id = extraer_data_id_mp(payload, request)
    manifest = construir_manifest_mp(data_id, x_request_id, ts)
    firma_calculada = hmac.new(
        MP_WEBHOOK_SECRET.encode("utf-8"),
        manifest.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(firma_calculada, firma_recibida):
        raise HTTPException(status_code=401, detail="Firma Mercado Pago no coincide")

# ============================================================
# ACTUALIZACION CENTRAL DE PAGO
# ============================================================
def registrar_pago_confirmado(payment_info: Dict[str, Any]) -> List[Dict[str, Any]]:
    ext_ref = limpiar_valor(payment_info.get("external_reference", ""))
    pago_id = limpiar_valor(payment_info.get("id", ""))
    proveedor = limpiar_valor(payment_info.get("provider", "")).upper()
    metodo_pago = limpiar_valor(payment_info.get("payment_type_id", proveedor.lower()))
    stripe_session_id = limpiar_valor(payment_info.get("stripe_session_id", ""))
    mp_preference_id = limpiar_valor(payment_info.get("mp_preference_id", ""))

    if not ext_ref:
        raise ValueError("No se recibio external_reference")

    df_r = leer_reservas()
    df_v = leer_ventas()

    ventas_ref = df_v[df_v["Referencia_Pago"].astype(str) == ext_ref]
    if not ventas_ref.empty:
        return ventas_ref.to_dict(orient="records")

    filtro = df_r["External_Reference"].astype(str) == ext_ref
    if not filtro.any():
        raise ValueError(f"No existe reserva con External_Reference={ext_ref}")

    df_r.loc[filtro, "Estado_Reserva"] = "PAGADO"
    df_r.loc[filtro, "Fecha_Actualizacion"] = ahora_txt()

    if proveedor == "STRIPE":
        df_r.loc[filtro, "Stripe_Payment_ID"] = pago_id
        if stripe_session_id:
            df_r.loc[filtro, "Stripe_Session_ID"] = stripe_session_id
    elif proveedor == "MERCADO_PAGO":
        df_r.loc[filtro, "MercadoPago_Payment_ID"] = pago_id
        if mp_preference_id:
            df_r.loc[filtro, "MercadoPago_Preference_ID"] = mp_preference_id

    escribir_reservas(df_r)

    nuevas_ventas = []
    for _, r in df_r[filtro].iterrows():
        nuevas_ventas.append(
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

    df_nuevas = asegurar_columnas(pd.DataFrame(nuevas_ventas), columnas_ventas())
    df_final = pd.concat([df_v, df_nuevas], ignore_index=True)
    escribir_ventas(df_final)
    return nuevas_ventas

# ============================================================
# STRIPE
# ============================================================
def payment_info_desde_checkout_session(session: Any) -> Optional[Dict[str, Any]]:
    session_dict = safe_to_dict(session)
    if str(safe_get(session_dict, "payment_status", "")).lower() != "paid":
        return None

    metadata = safe_get(session_dict, "metadata", {}) or {}
    payment_intent = safe_get(session_dict, "payment_intent", "")
    if not isinstance(payment_intent, str):
        payment_intent = safe_get(payment_intent, "id", "")

    return {
        "id": limpiar_valor(payment_intent),
        "external_reference": limpiar_valor(metadata.get("external_reference") or safe_get(session_dict, "client_reference_id", "")),
        "payment_type_id": "stripe_card",
        "provider": "STRIPE",
        "stripe_session_id": limpiar_valor(safe_get(session_dict, "id", "")),
    }


def payment_info_desde_payment_intent(intent: Any) -> Optional[Dict[str, Any]]:
    intent_dict = safe_to_dict(intent)
    if str(safe_get(intent_dict, "status", "")).lower() != "succeeded":
        return None

    metadata = safe_get(intent_dict, "metadata", {}) or {}
    return {
        "id": limpiar_valor(safe_get(intent_dict, "id", "")),
        "external_reference": limpiar_valor(metadata.get("external_reference", "")),
        "payment_type_id": "stripe_card",
        "provider": "STRIPE",
        "stripe_session_id": "",
    }


@app.post("/webhook/stripe")
async def webhook_stripe(request: Request):
    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=500, detail="STRIPE_WEBHOOK_SECRET no configurado en Render")

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Webhook Stripe invalido: {e}")

    event_type = safe_get(event, "type", "")
    data_obj = safe_get(safe_get(event, "data", {}), "object", {})

    try:
        payment_info = None
        if event_type in ["checkout.session.completed", "checkout.session.async_payment_succeeded"]:
            payment_info = payment_info_desde_checkout_session(data_obj)
        elif event_type == "payment_intent.succeeded":
            payment_info = payment_info_desde_payment_intent(data_obj)

        if payment_info and payment_info.get("external_reference"):
            ventas = registrar_pago_confirmado(payment_info)
            return JSONResponse({"ok": True, "provider": "STRIPE", "ventas": len(ventas)})

        return JSONResponse({"ok": True, "ignored": True, "event_type": event_type})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error procesando Stripe: {e}")

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
    }


@app.post("/webhook/mercadopago")
async def webhook_mercadopago(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    validar_firma_mercadopago(payload, request)

    payment_id = obtener_payment_id_mp(payload, request)
    topic = limpiar_valor(payload.get("type") or payload.get("topic") or request.query_params.get("topic", ""))

    if topic and topic not in ["payment", "payments"]:
        return JSONResponse({"ok": True, "ignored": True, "topic": topic})

    if not payment_id:
        return JSONResponse({"ok": True, "ignored": True, "reason": "sin payment_id"})

    try:
        pago = consultar_pago_mp(payment_id)
        payment_info = payment_info_desde_mp(pago) if pago else None

        if payment_info and payment_info.get("external_reference"):
            ventas = registrar_pago_confirmado(payment_info)
            return JSONResponse({"ok": True, "provider": "MERCADO_PAGO", "ventas": len(ventas)})

        return JSONResponse({"ok": True, "ignored": True, "payment_id": payment_id})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error procesando Mercado Pago: {e}")

# ============================================================
# HEALTHCHECK
# ============================================================
@app.get("/")
def root():
    return {"ok": True, "service": "webhook-pagos-rifa-seguro"}


@app.get("/health")
def health():
    return {
        "ok": True,
        "spreadsheet_configurado": bool(SPREADSHEET_ID),
        "stripe_configurado": bool(STRIPE_SECRET_KEY),
        "stripe_webhook_secret_configurado": bool(STRIPE_WEBHOOK_SECRET),
        "mp_configurado": bool(MP_ACCESS_TOKEN),
        "mp_webhook_secret_configurado": bool(MP_WEBHOOK_SECRET),
    }
