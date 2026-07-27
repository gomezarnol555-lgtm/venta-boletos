import os
import random
import re
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode, urlparse, parse_qsl, urlunparse

import pandas as pd
import streamlit as st
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle, PageBreak
from streamlit_gsheets import GSheetsConnection

import mercadopago
import stripe

# -----------------------------
# Configuración del Sistema y Proveedores de Pago
# -----------------------------
TIEMPO_RESERVA_MINUTOS = 1440   # 24 hrs (Reserva formal en Base de Datos)
TIEMPO_PRERESERVA_MINUTOS = 15  # 15 mins (Carrito temporal en Memoria)


def obtener_config(nombre: str, default: str = "") -> str:
    try:
        if hasattr(st, "secrets") and nombre in st.secrets:
            return str(st.secrets[nombre]).strip()
    except Exception:
        pass

    env_valor = os.getenv(nombre)
    if env_valor is not None:
        return str(env_valor).strip()

    return default


# Mercado Pago
MP_ACCESS_TOKEN = obtener_config("MP_ACCESS_TOKEN")
MP_NOTIFICATION_URL = obtener_config("MP_NOTIFICATION_URL")
MP_RETURN_URL = obtener_config("MP_RETURN_URL")
MP_CURRENCY_ID = obtener_config("MP_CURRENCY_ID", "MXN")

# Stripe
STRIPE_SECRET_KEY = obtener_config("STRIPE_SECRET_KEY")
STRIPE_RETURN_URL = obtener_config("STRIPE_RETURN_URL")
STRIPE_CURRENCY_ID = obtener_config("STRIPE_CURRENCY_ID", "mxn").lower()

# Diagnóstico seguro
DEBUG_PAGOS = obtener_config("DEBUG_PAGOS", "true").lower() in ["1", "true", "si", "sí", "yes", "on"]

sdk = mercadopago.SDK(MP_ACCESS_TOKEN) if MP_ACCESS_TOKEN else None
if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY


# -----------------------------
# Utilidades generales
# -----------------------------
def enmascarar_clave(valor: str, visibles_inicio: int = 7, visibles_fin: int = 4) -> str:
    valor = str(valor or "").strip()

    if not valor:
        return "NO CONFIGURADO"

    if len(valor) <= visibles_inicio + visibles_fin:
        return "*" * len(valor)

    return f"{valor[:visibles_inicio]}...{valor[-visibles_fin:]}"


def diagnostico_configuracion_pagos() -> Dict[str, Any]:
    return {
        "MP_ACCESS_TOKEN_configurado": bool(MP_ACCESS_TOKEN),
        "MP_ACCESS_TOKEN_mascara": enmascarar_clave(MP_ACCESS_TOKEN),
        "MP_RETURN_URL": MP_RETURN_URL or "NO CONFIGURADO",
        "MP_CURRENCY_ID": MP_CURRENCY_ID,
        "STRIPE_SECRET_KEY_configurado": bool(STRIPE_SECRET_KEY),
        "STRIPE_SECRET_KEY_mascara": enmascarar_clave(STRIPE_SECRET_KEY),
        "STRIPE_SECRET_KEY_es_sk": str(STRIPE_SECRET_KEY).startswith("sk_"),
        "STRIPE_SECRET_KEY_es_pk_error": str(STRIPE_SECRET_KEY).startswith("pk_"),
        "STRIPE_RETURN_URL": STRIPE_RETURN_URL or "NO CONFIGURADO",
        "STRIPE_RETURN_URL_es_http": normalizar_url(STRIPE_RETURN_URL).startswith(("http://", "https://")),
        "STRIPE_CURRENCY_ID": STRIPE_CURRENCY_ID,
        "DEBUG_PAGOS": DEBUG_PAGOS
    }


def normalizar_url(url: str) -> str:
    url = str(url or "").strip()
    if url and not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    return url


def agregar_parametros_url(url: str, parametros: Dict[str, str]) -> str:
    """Agrega parámetros sin romper los existentes. Mantiene intacto {CHECKOUT_SESSION_ID}."""
    url = normalizar_url(url)
    partes = urlparse(url)
    query_actual = dict(parse_qsl(partes.query, keep_blank_values=True))
    query_actual.update({k: str(v) for k, v in parametros.items() if v is not None})
    nueva_query = urlencode(query_actual)
    nueva_query = nueva_query.replace("%7BCHECKOUT_SESSION_ID%7D", "{CHECKOUT_SESSION_ID}")
    return urlunparse((partes.scheme, partes.netloc, partes.path, partes.params, nueva_query, partes.fragment))


def qp_get(qp: Any, nombre: str, default: str = "") -> str:
    try:
        valor = qp.get(nombre, default)
        if isinstance(valor, list):
            return str(valor[0]) if valor else default
        return str(valor)
    except Exception:
        return default


def mostrar_diagnostico_pagos():
    if not DEBUG_PAGOS:
        return

    with st.expander("🔎 Ver diagnóstico técnico de pagos"):
        st.write("**Configuración detectada:**")
        st.json(diagnostico_configuracion_pagos())

        if st.session_state.get("ultimo_error_pago"):
            st.write("**Último error real:**")
            st.code(st.session_state.ultimo_error_pago)

        if st.session_state.get("errores_proveedores"):
            st.write("**Errores registrados:**")
            st.code("\n".join(st.session_state.errores_proveedores))

        st.info(
            "Si Stripe usa sk_test_, prueba con tarjetas de prueba. "
            "Si Mercado Pago o Stripe regresan a la app sin PDF, revisa que RETURN_URL sea la URL pública de Streamlit y no GitHub."
        )


# -----------------------------
# Caché Global para Pre-Reservas (Memoria RAM)
# -----------------------------
@st.cache_resource
def obtener_pre_reservas_globales() -> dict:
    return {}


def limpiar_pre_reservas_expiradas(pre_reservas: dict):
    ahora = datetime.now()
    expirados = [k for k, v in list(pre_reservas.items()) if v["expires_at"] < ahora]
    for k in expirados:
        del pre_reservas[k]


# -----------------------------
# Estilos CSS
# -----------------------------
CSS_CUSTOM = """
<style>
   [data-testid="column"] { padding: 0 4px !important; }
   [data-testid="stButton"] button {
       width: 100%; height: 55px; padding: 0;
       font-weight: 700; font-size: 14px; transition: all 0.2s;
   }
   [data-testid="stButton"] button:hover {
       transform: scale(1.02); border-color: #004481;
   }
   .metric-container { display: flex; justify-content: space-between; gap: 10px; margin-bottom: 20px; flex-wrap: wrap; }
   .metric-box {
       flex: 1; min-width: 120px; background: white; padding: 10px; border-radius: 8px; text-align: center;
       box-shadow: 0 2px 5px rgba(0,0,0,0.05); border-top: 4px solid;
   }
   .metric-box h2 { margin: 0; font-size: 20px; font-weight: 800; color: #0A2540; }
   .metric-box p { margin: 0; font-size: 12px; color: #64748B; font-weight: 600; }
   .m-green { border-color: #20C997; }
   .m-gray { border-color: #94A3B8; }
   .m-yellow { border-color: #F59E0B; }
   .m-red { border-color: #EF4444; }
</style>
"""


# -----------------------------
# PDF
# -----------------------------
def dibujar_fondo_autenticidad(canvas, doc):
    width, height = doc.pagesize
    canvas.saveState()
    canvas.setFillColor(colors.HexColor("#F8FAFC"))
    canvas.rect(0, 0, width, height, fill=1, stroke=0)
    canvas.setStrokeColor(colors.HexColor("#0A2540"))
    canvas.setLineWidth(1)
    canvas.rect(24, 24, width - 48, height - 48, fill=0, stroke=1)
    canvas.restoreState()


def generar_pdf_boleto(datos_boletos: List[Dict[str, Any]]) -> str:
    codigo_pago = datos_boletos[0].get("Codigo_Pago", "Generico")
    nombre_archivo = f"Boletos_Oficiales_{codigo_pago}.pdf"
    doc = SimpleDocTemplate(nombre_archivo, pagesize=letter, rightMargin=32, leftMargin=32, topMargin=40, bottomMargin=32)
    story, styles = [], getSampleStyleSheet()
    estilo_titulo = ParagraphStyle("Titulo", parent=styles["Heading1"], fontSize=19, textColor=colors.HexColor("#0A2540"), alignment=1)
    estilo_normal = ParagraphStyle("Texto", parent=styles["Normal"], fontSize=10.5, leading=13, textColor=colors.HexColor("#334155"))

    for idx, boleto in enumerate(datos_boletos):
        story.append(Paragraph("BOLETO OFICIAL DE COMPRA", estilo_titulo))
        story.append(Spacer(1, 16))
        precio_float = float(boleto.get("Precio", 0))
        data = [
            [Paragraph("<b>ID de Boleto:</b>", estilo_normal), Paragraph(str(boleto.get("ID_Boleto", "")), estilo_normal)],
            [Paragraph("<b>Nombre:</b>", estilo_normal), Paragraph(str(boleto.get("Nombre", "")), estilo_normal)],
            [Paragraph("<b>N° de Boleto:</b>", estilo_normal), Paragraph(str(boleto.get("Numero_Boleto", "")), estilo_normal)],
            [Paragraph("<b>Precio Pagado:</b>", estilo_normal), Paragraph(f"${precio_float:.2f} {MP_CURRENCY_ID}", estilo_normal)],
            [Paragraph("<b>Método de Pago:</b>", estilo_normal), Paragraph(str(boleto.get("Metodo_Pago", "Pago electrónico")).upper(), estilo_normal)],
            [Paragraph("<b>Ref / ID Pago:</b>", estilo_normal), Paragraph(str(boleto.get("Codigo_Pago", "N/A")), estilo_normal)],
            [Paragraph("<b>Fecha:</b>", estilo_normal), Paragraph(str(boleto.get("Fecha_Compra", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))), estilo_normal)]
        ]
        t = Table(data, colWidths=[165, 300])
        t.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#CBD5E1")),
            ("INNERGRID", (0, 0), (-1, -1), 0.6, colors.HexColor("#E2E8F0")),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]))
        story.append(t)
        if idx < len(datos_boletos) - 1:
            story.append(PageBreak())

    doc.build(story, onFirstPage=dibujar_fondo_autenticidad, onLaterPages=dibujar_fondo_autenticidad)
    return nombre_archivo


def procesar_descarga_pdf(datos_boletos: List[dict]):
    if not datos_boletos:
        st.warning("No hay boletos disponibles para generar PDF.")
        return

    archivo_pdf = generar_pdf_boleto(datos_boletos)
    with open(archivo_pdf, "rb") as pdf_file:
        pdf_bytes = pdf_file.read()

    label = "⬇️ Descargar mis Boletos Oficiales (PDF)" if len(datos_boletos) > 1 else "⬇️ Descargar mi Boleto Oficial (PDF)"
    st.download_button(label=label, data=pdf_bytes, file_name=archivo_pdf, mime="application/pdf", type="primary", use_container_width=True)


# -----------------------------
# Proveedores de pago
# -----------------------------
def crear_preferencia_mercado_pago(
    nombre,
    apellidos,
    correo,
    telefono,
    numeros_boletos: list,
    monto_unitario,
    external_reference,
    custom_return_scheme: Optional[str] = None
):
    if not sdk:
        return "", ""

    url_retorno_base = custom_return_scheme if custom_return_scheme else MP_RETURN_URL
    url_retorno_base = normalizar_url(url_retorno_base)
    titulos_boletos = ", ".join(numeros_boletos)

    preference_data = {
        "items": [{
            "title": f"Rifa celular - Boletos: {titulos_boletos}",
            "quantity": len(numeros_boletos),
            "unit_price": float(monto_unitario),
            "currency_id": MP_CURRENCY_ID
        }],
        "payer": {
            "name": nombre.strip(),
            "surname": apellidos.strip() or "Sin Apellido",
            "email": correo,
            "phone": {"area_code": "52", "number": telefono}
        },
        "external_reference": external_reference,
        "payment_methods": {"excluded_payment_methods": [], "excluded_payment_types": [], "installments": 1},
        "statement_descriptor": "RIFA CELULAR"
    }

    if MP_NOTIFICATION_URL:
        preference_data["notification_url"] = MP_NOTIFICATION_URL

    if url_retorno_base:
        preference_data["back_urls"] = {
            "success": agregar_parametros_url(url_retorno_base, {"mp_return": "success", "external_reference": external_reference}),
            "pending": agregar_parametros_url(url_retorno_base, {"mp_return": "pending", "external_reference": external_reference}),
            "failure": agregar_parametros_url(url_retorno_base, {"mp_return": "failure", "external_reference": external_reference})
        }
        preference_data["auto_return"] = "approved"

    preference = sdk.preference().create(preference_data).get("response", {})
    if "id" not in preference:
        raise Exception(f"Rechazado por MP: {preference.get('message', 'Error en credenciales o URL de retorno')}")

    return preference.get("id", ""), preference.get("init_point") or preference.get("sandbox_init_point", "")


def crear_sesion_stripe(
    nombre: str,
    apellidos: str,
    correo: str,
    numeros_boletos: List[str],
    monto_unitario: float,
    external_reference: str
) -> Tuple[str, str]:
    if not STRIPE_SECRET_KEY:
        raise ValueError("STRIPE_SECRET_KEY no está configurado en Secrets o variables de entorno.")
    if STRIPE_SECRET_KEY.startswith("pk_"):
        raise ValueError("STRIPE_SECRET_KEY contiene una llave pública pk_. Debes usar una llave secreta sk_test_ o sk_live_.")
    if not STRIPE_SECRET_KEY.startswith("sk_"):
        raise ValueError("STRIPE_SECRET_KEY no parece ser una llave secreta válida. Debe iniciar con sk_test_ o sk_live_.")
    if not STRIPE_RETURN_URL:
        raise ValueError("STRIPE_RETURN_URL no está configurado. Debe ser la URL pública de tu app Streamlit, no el enlace de GitHub.")

    url_base = normalizar_url(STRIPE_RETURN_URL)
    success_url = agregar_parametros_url(url_base, {"stripe_session_id": "{CHECKOUT_SESSION_ID}", "external_reference": external_reference})
    cancel_url = agregar_parametros_url(url_base, {"stripe_cancelled": "true", "external_reference": external_reference})
    descripcion_boletos = ", ".join(numeros_boletos)

    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            customer_email=correo.strip().lower(),
            client_reference_id=external_reference,
            line_items=[{
                "price_data": {
                    "currency": STRIPE_CURRENCY_ID,
                    "unit_amount": int(round(float(monto_unitario) * 100)),
                    "product_data": {
                        "name": "Boletos Rifa de Celular",
                        "description": f"Boletos seleccionados: {descripcion_boletos}"[:500]
                    }
                },
                "quantity": len(numeros_boletos)
            }],
            metadata={
                "external_reference": external_reference,
                "boletos": descripcion_boletos,
                "nombre_cliente": f"{nombre.strip()} {apellidos.strip()}"[:500]
            },
            payment_intent_data={"metadata": {"external_reference": external_reference}},
            success_url=success_url,
            cancel_url=cancel_url
        )

        session_id = str(session.id or "")
        checkout_url = str(session.url or "")
        if not session_id or not checkout_url:
            raise RuntimeError("Stripe respondió, pero no devolvió session.id o session.url.")

        return session_id, checkout_url

    except Exception as e:
        raise Exception(f"{type(e).__name__}: {e}") from e


def obtener_pago_stripe(
    stripe_session_id: str,
    external_reference_esperada: Optional[str] = None,
    monto_esperado: Optional[float] = None
) -> Optional[Dict[str, Any]]:
    if not STRIPE_SECRET_KEY or not stripe_session_id:
        return None

    try:
        session = stripe.checkout.Session.retrieve(stripe_session_id, expand=["payment_intent"])
        if session.get("payment_status") != "paid":
            return None

        metadata = dict(session.get("metadata") or {})
        external_reference = metadata.get("external_reference") or session.get("client_reference_id") or ""

        if external_reference_esperada and external_reference != external_reference_esperada:
            return None

        if str(session.get("currency", "")).lower() != STRIPE_CURRENCY_ID:
            return None

        if monto_esperado is not None:
            monto_recibido_centavos = int(session.get("amount_total") or 0)
            monto_esperado_centavos = int(round(float(monto_esperado) * 100))
            if monto_recibido_centavos != monto_esperado_centavos:
                return None

        payment_intent = session.get("payment_intent")
        payment_intent_id = ""
        if isinstance(payment_intent, str):
            payment_intent_id = payment_intent
        elif payment_intent:
            payment_intent_id = str(payment_intent.get("id", ""))

        return {
            "id": payment_intent_id or str(session.get("id", "")),
            "external_reference": external_reference,
            "payment_type_id": "stripe_card",
            "status": "approved",
            "provider": "STRIPE",
            "provider_session_id": str(session.get("id", ""))
        }

    except Exception as e:
        st.session_state.ultimo_error_pago = f"Stripe retrieve: {e}"
        return None



def buscar_pago_stripe_por_referencia_o_correo(
    external_reference: str = "",
    correo: str = "",
    monto_esperado: Optional[float] = None
) -> Optional[Dict[str, Any]]:
    """
    Fallback experto para recuperar una compra de Stripe cuando:
    - El usuario pagó correctamente, pero Streamlit no procesó el retorno.
    - La sesión de Stripe no quedó guardada en Google Sheets.
    - La compra fue de varios boletos y se consulta solo uno.

    Busca sesiones recientes completadas y pagadas, preferentemente por
    client_reference_id / metadata['external_reference'] y, si hace falta, por correo.
    """
    if not STRIPE_SECRET_KEY:
        return None

    try:
        parametros = {
            "limit": 100,
            "status": "complete",
            "expand": ["data.payment_intent"]
        }

        sesiones = stripe.checkout.Session.list(**parametros)

        for session in sesiones.get("data", []):
            if session.get("payment_status") != "paid":
                continue

            metadata = dict(session.get("metadata") or {})
            ref_sesion = metadata.get("external_reference") or session.get("client_reference_id") or ""

            correo_sesion = str(session.get("customer_email") or "").strip().lower()
            customer_details = session.get("customer_details") or {}
            correo_sesion_detalle = str(customer_details.get("email") or "").strip().lower()
            correo_buscado = str(correo or "").strip().lower()

            coincide_ref = bool(external_reference and ref_sesion == str(external_reference))
            coincide_correo = bool(correo_buscado and correo_buscado in [correo_sesion, correo_sesion_detalle])

            if external_reference:
                if not coincide_ref:
                    continue
            elif correo:
                if not coincide_correo:
                    continue
            else:
                continue

            if monto_esperado is not None:
                monto_recibido_centavos = int(session.get("amount_total") or 0)
                monto_esperado_centavos = int(round(float(monto_esperado) * 100))
                if monto_recibido_centavos != monto_esperado_centavos:
                    continue

            payment_intent = session.get("payment_intent")
            payment_intent_id = ""
            if isinstance(payment_intent, str):
                payment_intent_id = payment_intent
            elif payment_intent:
                payment_intent_id = str(payment_intent.get("id", ""))

            return {
                "id": payment_intent_id or str(session.get("id", "")),
                "external_reference": ref_sesion,
                "payment_type_id": "stripe_card",
                "status": "approved",
                "provider": "STRIPE",
                "provider_session_id": str(session.get("id", ""))
            }

        return None

    except Exception as e:
        st.session_state.ultimo_error_pago = f"Stripe list fallback: {e}"
        return None

def buscar_pago_en_mercadopago(external_reference: str) -> Optional[Dict]:
    if not sdk or not external_reference:
        return None
    try:
        pagos = sdk.payment().search({"external_reference": external_reference}).get("response", {}).get("results", [])
        pago = next((p for p in pagos if p.get("status") == "approved"), None)
        if pago:
            pago["provider"] = "MERCADO_PAGO"
        return pago
    except Exception as e:
        st.session_state.ultimo_error_pago = f"MP search: {e}"
        return None


def obtener_pago_mercadopago_por_id(payment_id: str, external_reference_fallback: str = "") -> Optional[Dict[str, Any]]:
    if not sdk:
        return None

    try:
        if payment_id:
            pago = sdk.payment().get(payment_id).get("response", {})
            if pago and pago.get("status") == "approved":
                pago["provider"] = "MERCADO_PAGO"
                if not pago.get("external_reference") and external_reference_fallback:
                    pago["external_reference"] = external_reference_fallback
                return pago

        if external_reference_fallback:
            return buscar_pago_en_mercadopago(external_reference_fallback)

        return None
    except Exception as e:
        st.session_state.ultimo_error_pago = f"MP get: {e}"
        return None


# -----------------------------
# Google Sheets
# -----------------------------
def columnas_ventas() -> list:
    return [
        "ID_Boleto", "Nombre", "Correo", "Evento", "Numero_Boleto", "Precio", "Metodo_Pago", "Codigo_Pago",
        "Fecha_Compra", "Numero_Telefonico", "Estado_Pago", "Referencia_Pago", "MercadoPago_Payment_ID",
        "MercadoPago_Preference_ID", "Stripe_Payment_ID", "Stripe_Session_ID", "Proveedor_Pago"
    ]


def columnas_reservas() -> list:
    return [
        "External_Reference", "MercadoPago_Preference_ID", "MercadoPago_Payment_ID", "Stripe_Session_ID",
        "Stripe_Payment_ID", "Numero_Boleto", "Nombre", "Correo", "Numero_Telefonico", "Monto",
        "Estado_Reserva", "Fecha_Creacion", "Expira_En", "Fecha_Actualizacion"
    ]


def asegurar_columnas(df: pd.DataFrame, cols: list) -> pd.DataFrame:
    for col in cols:
        if col not in df.columns:
            df[col] = ""
    return df[cols]


def parse_ticket_number(val: Any) -> str:
    if pd.isna(val) or str(val).strip() == "":
        return ""
    try:
        return f"{int(float(val)):03d}"
    except Exception:
        return str(val).strip().zfill(3)


def leer_reservas(conn: GSheetsConnection) -> pd.DataFrame:
    try:
        return asegurar_columnas(conn.read(worksheet="Reservas", ttl=0).dropna(how="all"), columnas_reservas())
    except Exception:
        return pd.DataFrame(columns=columnas_reservas())


def leer_ventas(conn: GSheetsConnection) -> pd.DataFrame:
    try:
        return asegurar_columnas(conn.read(worksheet="Ventas", ttl=0).dropna(how="all"), columnas_ventas())
    except Exception:
        return pd.DataFrame(columns=columnas_ventas())


def obtener_estado_boletos_bd(df_ventas: pd.DataFrame, df_reservas: pd.DataFrame) -> dict:
    estados = {}

    if not df_reservas.empty and "Numero_Boleto" in df_reservas.columns:
        for _, row in df_reservas.iterrows():
            if str(row.get("Estado_Reserva", "")).strip().upper() == "PENDIENTE":
                try:
                    expira = pd.to_datetime(str(row.get("Expira_En"))).to_pydatetime()
                except Exception:
                    expira = None
                if expira is None or datetime.now() <= expira:
                    num = parse_ticket_number(row["Numero_Boleto"])
                    if num:
                        estados[num] = "reservado_db"

    if not df_ventas.empty and "Numero_Boleto" in df_ventas.columns:
        for _, row in df_ventas.iterrows():
            if str(row.get("Estado_Pago", "")).strip().upper() in ["APROBADO", "VENDIDO"]:
                num = parse_ticket_number(row["Numero_Boleto"])
                if num:
                    estados[num] = "vendido_db"

    return estados


def registrar_reserva_cobro(conn: GSheetsConnection, ordenes: List[Dict[str, Any]]) -> Tuple[bool, str]:
    try:
        df_r = leer_reservas(conn)
        df_actualizado = pd.concat([
            asegurar_columnas(df_r.dropna(how="all"), columnas_reservas()),
            asegurar_columnas(pd.DataFrame(ordenes), columnas_reservas())
        ], ignore_index=True)
        conn.update(worksheet="Reservas", data=df_actualizado)
        return True, "Éxito"
    except Exception as e:
        return False, str(e)


def actualizar_ids_proveedores_reserva(
    conn: GSheetsConnection,
    external_reference: str,
    mercado_pago_preference_id: str = "",
    stripe_session_id: str = ""
) -> Tuple[bool, str]:
    try:
        df_r = leer_reservas(conn)
        filtro = df_r["External_Reference"].astype(str) == str(external_reference)
        if not filtro.any():
            return False, "No se encontró la reserva para actualizar."
        if mercado_pago_preference_id:
            df_r.loc[filtro, "MercadoPago_Preference_ID"] = mercado_pago_preference_id
        if stripe_session_id:
            df_r.loc[filtro, "Stripe_Session_ID"] = stripe_session_id
        df_r.loc[filtro, "Fecha_Actualizacion"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.update(worksheet="Reservas", data=df_r)
        return True, "Éxito"
    except Exception as e:
        return False, str(e)


def liberar_reserva_por_rechazo_o_cancelacion(
    conn: GSheetsConnection,
    external_reference: str,
    motivo: str = "CANCELADO_PAGO"
) -> Tuple[bool, str]:
    try:
        if not external_reference:
            return False, "No se recibió External_Reference para liberar la reserva."

        df_r = leer_reservas(conn)
        filtro = (
            (df_r["External_Reference"].astype(str) == str(external_reference))
            & (df_r["Estado_Reserva"].astype(str).str.upper().isin(["PENDIENTE", "ERROR_CONFIRMACION_STRIPE"]))
        )
        if not filtro.any():
            return True, "No había reservas pendientes por liberar."

        df_r.loc[filtro, "Estado_Reserva"] = motivo
        df_r.loc[filtro, "Fecha_Actualizacion"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.update(worksheet="Reservas", data=df_r)
        return True, "Reserva liberada correctamente."
    except Exception as e:
        return False, str(e)


def limpiar_carrito_local():
    pre_reservas = obtener_pre_reservas_globales()
    for t in list(st.session_state.get("selected_tickets", [])):
        if t in pre_reservas and pre_reservas[t]["session_id"] == st.session_state.session_id:
            del pre_reservas[t]

    st.session_state.selected_tickets = []
    st.session_state.pago_generado_url = None
    st.session_state.stripe_pago_url = None
    st.session_state.stripe_session_id = None
    st.session_state.payment_provider = None
    st.session_state.external_ref_activa = None


def actualizar_pago_en_hojas(conn: GSheetsConnection, payment_info: Dict[str, Any]) -> List[Dict[str, Any]]:
    ext_ref = str(payment_info.get("external_reference", "")).strip()
    pago_id = str(payment_info.get("id", "")).strip()
    metodo_pago = str(payment_info.get("payment_type_id", "desconocido")).strip()
    proveedor = str(payment_info.get("provider", "MERCADO_PAGO")).strip().upper()
    provider_session_id = str(payment_info.get("provider_session_id", "")).strip()

    if not ext_ref:
        return []

    df_r = leer_reservas(conn)
    df_v = leer_ventas(conn)

    if proveedor == "STRIPE":
        filtro_existente = df_v["Stripe_Payment_ID"].astype(str) == pago_id
    else:
        filtro_existente = df_v["MercadoPago_Payment_ID"].astype(str) == pago_id

    if filtro_existente.any() and pago_id:
        return df_v[filtro_existente].to_dict(orient="records")

    filtro_reserva = df_r["External_Reference"].astype(str) == ext_ref
    if not filtro_reserva.any():
        return []

    df_r.loc[filtro_reserva, "Estado_Reserva"] = "PAGADO"
    df_r.loc[filtro_reserva, "Fecha_Actualizacion"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if proveedor == "STRIPE":
        df_r.loc[filtro_reserva, "Stripe_Payment_ID"] = pago_id
        if provider_session_id:
            df_r.loc[filtro_reserva, "Stripe_Session_ID"] = provider_session_id
    else:
        df_r.loc[filtro_reserva, "MercadoPago_Payment_ID"] = pago_id

    conn.update(worksheet="Reservas", data=df_r)

    nuevas_ventas = []
    for _, r in df_r[filtro_reserva].iterrows():
        nuevas_ventas.append({
            "ID_Boleto": f"BOL-{random.randint(10000, 99999)}",
            "Nombre": r["Nombre"],
            "Correo": r["Correo"],
            "Evento": "Rifa de Celular",
            "Numero_Boleto": parse_ticket_number(r["Numero_Boleto"]),
            "Precio": r["Monto"],
            "Metodo_Pago": metodo_pago,
            "Codigo_Pago": pago_id,
            "Fecha_Compra": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "Numero_Telefonico": r["Numero_Telefonico"],
            "Estado_Pago": "APROBADO",
            "Referencia_Pago": ext_ref,
            "MercadoPago_Payment_ID": pago_id if proveedor != "STRIPE" else "",
            "MercadoPago_Preference_ID": r.get("MercadoPago_Preference_ID", ""),
            "Stripe_Payment_ID": pago_id if proveedor == "STRIPE" else "",
            "Stripe_Session_ID": provider_session_id if proveedor == "STRIPE" else "",
            "Proveedor_Pago": proveedor
        })

    df_final = pd.concat([df_v, asegurar_columnas(pd.DataFrame(nuevas_ventas), columnas_ventas())], ignore_index=True)
    conn.update(worksheet="Ventas", data=df_final)
    return nuevas_ventas


def buscar_ventas_por_correo_boleto(conn: GSheetsConnection, numero_boleto: str, correo: str) -> List[Dict[str, Any]]:
    df_v = leer_ventas(conn)
    if df_v.empty:
        return []

    filtro = (
        (df_v["Numero_Boleto"].astype(str).apply(parse_ticket_number) == parse_ticket_number(numero_boleto))
        & (df_v["Correo"].astype(str).str.lower() == correo.strip().lower())
        & (df_v["Estado_Pago"].astype(str).str.upper().isin(["APROBADO", "VENDIDO"]))
    )

    if not filtro.any():
        return []

    ref = str(df_v[filtro].iloc[-1].get("Referencia_Pago", ""))
    if ref:
        return df_v[df_v["Referencia_Pago"].astype(str) == ref].to_dict(orient="records")

    return df_v[filtro].to_dict(orient="records")


def verificar_y_recuperar_boletos_desde_reserva(conn: GSheetsConnection, numero_boleto: str, correo: str) -> List[Dict[str, Any]]:
    """
    Recupera compras pagadas aunque la venta no se haya registrado automáticamente.

    Funciona para compras de uno o varios boletos:
    - Si consultas cualquiera de los boletos de la compra, obtiene el External_Reference.
    - Verifica Mercado Pago por external_reference.
    - Verifica Stripe por Stripe_Session_ID.
    - Si no hay Stripe_Session_ID o falló el retorno, busca sesiones recientes pagadas en Stripe por external_reference/correo.
    - Si el pago está aprobado, registra todos los boletos de esa compra en Ventas y genera el PDF.
    """
    df_r = leer_reservas(conn)
    if df_r.empty:
        return []

    correo_limpio = correo.strip().lower()
    numero_normalizado = parse_ticket_number(numero_boleto)

    filtro_exact = (
        (df_r["Numero_Boleto"].astype(str).apply(parse_ticket_number) == numero_normalizado)
        & (df_r["Correo"].astype(str).str.lower() == correo_limpio)
    )

    reservas = df_r[filtro_exact]

    # Si por alguna razón el número no coincide por formato, intenta solo por correo y busca una compra que contenga el boleto.
    if reservas.empty:
        candidatas = df_r[df_r["Correo"].astype(str).str.lower() == correo_limpio]
        refs_candidatas = candidatas["External_Reference"].astype(str).dropna().unique().tolist()
        for ref in refs_candidatas:
            grupo = candidatas[candidatas["External_Reference"].astype(str) == str(ref)]
            boletos_grupo = [parse_ticket_number(x) for x in grupo["Numero_Boleto"].tolist()]
            if numero_normalizado in boletos_grupo:
                reservas = grupo
                break

    if reservas.empty:
        return []

    # Toma la última referencia encontrada para ese boleto/correo.
    reserva = reservas.iloc[-1]
    ext_ref = str(reserva.get("External_Reference", "")).strip()
    if not ext_ref:
        return []

    grupo_reserva = df_r[df_r["External_Reference"].astype(str) == ext_ref]
    total_reserva = float(grupo_reserva["Monto"].astype(float).sum()) if not grupo_reserva.empty else None

    # 1) Mercado Pago por external_reference.
    pago_confirmado = buscar_pago_en_mercadopago(ext_ref)

    # 2) Stripe por Session ID guardado en cualquiera de los boletos de la misma compra.
    if not pago_confirmado:
        stripe_session_id = ""
        for _, r in grupo_reserva.iterrows():
            posible = str(r.get("Stripe_Session_ID", "")).strip()
            if posible:
                stripe_session_id = posible
                break

        if stripe_session_id:
            pago_confirmado = obtener_pago_stripe(
                stripe_session_id,
                external_reference_esperada=ext_ref,
                monto_esperado=total_reserva
            )

    # 3) Stripe fallback: busca sesiones recientes pagadas por external_reference o correo.
    if not pago_confirmado:
        pago_confirmado = buscar_pago_stripe_por_referencia_o_correo(
            external_reference=ext_ref,
            correo=correo_limpio,
            monto_esperado=total_reserva
        )

    if pago_confirmado:
        datos = actualizar_pago_en_hojas(conn, pago_confirmado)
        if datos:
            return datos

        # Si ya existían ventas por idempotencia, intenta leerlas por referencia.
        df_v = leer_ventas(conn)
        ventas_ref = df_v[df_v["Referencia_Pago"].astype(str) == ext_ref]
        if not ventas_ref.empty:
            return ventas_ref.to_dict(orient="records")

    return []


# -----------------------------
# Componentes UI
# -----------------------------
@st.fragment(run_every=5)
def renderizar_mapa_interactivo():
    mi_sesion = st.session_state.session_id
    pre_reservas = obtener_pre_reservas_globales()
    limpiar_pre_reservas_expiradas(pre_reservas)

    if not (st.session_state.get("pago_generado_url") or st.session_state.get("stripe_pago_url")):
        st.session_state.selected_tickets = [
            t for t in st.session_state.selected_tickets
            if t in pre_reservas and pre_reservas[t]["session_id"] == mi_sesion
        ]

    conn = st.connection("gsheets", type=GSheetsConnection)
    try:
        df_v = conn.read(worksheet="Ventas", ttl=5).dropna(how="all")
        df_r = conn.read(worksheet="Reservas", ttl=5).dropna(how="all")
    except Exception:
        df_v = pd.DataFrame(columns=columnas_ventas())
        df_r = pd.DataFrame(columns=columnas_reservas())

    estados_bd = obtener_estado_boletos_bd(df_v, df_r)
    estados_pantalla = {}
    vendidos, reservados_bd, pre_reservados_otros = 0, 0, 0

    for i in range(100):
        num = f"{i:03d}"
        if num in estados_bd:
            estados_pantalla[num] = estados_bd[num]
            if estados_bd[num] == "vendido_db":
                vendidos += 1
            elif estados_bd[num] == "reservado_db":
                reservados_bd += 1
        elif num in pre_reservas:
            if pre_reservas[num]["session_id"] == mi_sesion:
                estados_pantalla[num] = "pre_reservado_mio"
            else:
                estados_pantalla[num] = "pre_reservado_otros"
                pre_reservados_otros += 1
        else:
            estados_pantalla[num] = "disponible"

    disponibles = 100 - vendidos - reservados_bd - pre_reservados_otros - len(st.session_state.selected_tickets)

    st.markdown(f"""
    <div class="metric-container">
        <div class="metric-box m-green"><h2>🟢 {disponibles}</h2><p>Libres</p></div>
        <div class="metric-box m-gray"><h2>🔒 {pre_reservados_otros}</h2><p>En otro carrito</p></div>
        <div class="metric-box m-yellow"><h2>🟡 {reservados_bd}</h2><p>Por Pagar (24h)</p></div>
        <div class="metric-box m-red"><h2>🔴 {vendidos}</h2><p>Vendidos</p></div>
    </div>
    <p>Haz clic en los boletos 🟢 <b>Libres</b> para apartarlos temporalmente (tienes 15 min para pagar).</p>
    """, unsafe_allow_html=True)

    for fila in range(10):
        cols = st.columns(10)
        for col_idx in range(10):
            num = f"{(fila * 10 + col_idx):03d}"
            estado = estados_pantalla[num]
            with cols[col_idx]:
                if estado == "vendido_db":
                    st.button(f"🔴\n{num}", disabled=True, key=f"btn_{num}")
                elif estado == "reservado_db":
                    st.button(f"🟡\n{num}", disabled=True, key=f"btn_{num}")
                elif estado == "pre_reservado_otros":
                    st.button(f"🔒\n{num}", disabled=True, key=f"btn_{num}", help="Alguien más tiene este boleto en su carrito ahora mismo.")
                else:
                    is_selected = estado == "pre_reservado_mio" or num in st.session_state.selected_tickets
                    etiqueta = f"✅\n{num}" if is_selected else f"🟢\n{num}"
                    if st.button(etiqueta, key=f"btn_{num}", type="primary" if is_selected else "secondary"):
                        if is_selected:
                            if num in pre_reservas:
                                del pre_reservas[num]
                            if num in st.session_state.selected_tickets:
                                st.session_state.selected_tickets.remove(num)
                        else:
                            pre_reservas[num] = {"session_id": mi_sesion, "expires_at": datetime.now() + timedelta(minutes=TIEMPO_PRERESERVA_MINUTOS)}
                            if num not in st.session_state.selected_tickets:
                                st.session_state.selected_tickets.append(num)
                        st.rerun()


def procesar_retorno_pago(conn: GSheetsConnection):
    qp = st.query_params

    # Mercado Pago cancelado/rechazado
    mp_status = qp_get(qp, "status", "") or qp_get(qp, "collection_status", "")
    mp_return = qp_get(qp, "mp_return", "")
    mp_status = mp_status.lower()
    mp_return = mp_return.lower()

    if mp_return == "failure" or mp_status in ["rejected", "cancelled", "canceled", "failure", "failed"]:
        ext_ref = qp_get(qp, "external_reference", "") or st.session_state.get("external_ref_activa", "")
        liberar_reserva_por_rechazo_o_cancelacion(conn, ext_ref, motivo="CANCELADO_MERCADO_PAGO")
        limpiar_carrito_local()
        st.query_params.clear()
        st.warning("El pago con Mercado Pago fue cancelado o rechazado. La reserva fue liberada y los boletos ya pueden seleccionarse nuevamente.")
        st.rerun()

    if mp_return == "pending" or mp_status == "pending":
        st.warning("El pago con Mercado Pago quedó pendiente. Tus boletos permanecerán reservados hasta que se confirme el pago o expire la reserva.")
        st.query_params.clear()

    # Mercado Pago aprobado. Algunos retornos usan payment_id y otros collection_id.
    payment_id = qp_get(qp, "payment_id", "") or qp_get(qp, "collection_id", "")
    ext_ref_mp = qp_get(qp, "external_reference", "") or st.session_state.get("external_ref_activa", "")
    if payment_id and mp_status == "approved":
        pago_mp = obtener_pago_mercadopago_por_id(payment_id, ext_ref_mp)
        if pago_mp:
            datos = actualizar_pago_en_hojas(conn, pago_mp)
            st.session_state.boletos_confirmados = datos
            st.session_state.payment_success_id = str(pago_mp.get("id", payment_id))
            st.session_state.payment_provider = "MERCADO_PAGO"
            limpiar_carrito_local()
            st.session_state.boletos_confirmados = datos
            st.query_params.clear()
            st.rerun()
        else:
            st.error("Mercado Pago regresó aprobado, pero no se pudo confirmar el pago por API. Intenta en 'Buscar mis Boletos / Verificar Pago'.")
            mostrar_diagnostico_pagos()

    # Stripe cancelado/rechazado
    if "stripe_cancelled" in qp:
        ext_ref = qp_get(qp, "external_reference", "") or st.session_state.get("external_ref_activa", "")
        liberar_reserva_por_rechazo_o_cancelacion(conn, ext_ref, motivo="CANCELADO_STRIPE")
        limpiar_carrito_local()
        st.query_params.clear()
        st.warning("El pago con Stripe fue cancelado o rechazado. La reserva fue liberada y los boletos ya pueden seleccionarse nuevamente.")
        st.rerun()

    # Stripe aprobado
    stripe_session_id = qp_get(qp, "stripe_session_id", "")
    if stripe_session_id:
        ext_ref = qp_get(qp, "external_reference", "") or st.session_state.get("external_ref_activa", "")
        pago_stripe = obtener_pago_stripe(stripe_session_id, external_reference_esperada=ext_ref if ext_ref else None)
        if pago_stripe:
            datos = actualizar_pago_en_hojas(conn, pago_stripe)
            st.session_state.boletos_confirmados = datos
            st.session_state.payment_success_id = str(pago_stripe.get("id", stripe_session_id))
            st.session_state.payment_provider = "STRIPE"
            limpiar_carrito_local()
            st.session_state.boletos_confirmados = datos
            st.query_params.clear()
            st.rerun()
        else:
            # No libera si no se confirmó como pagado, salvo que Stripe regrese por cancel_url.
            st.error("Stripe regresó a la app, pero el pago aún no aparece como pagado. Intenta en 'Buscar mis Boletos / Verificar Pago'.")
            mostrar_diagnostico_pagos()


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
        "boletos_confirmados": []
    }

    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def main():
    st.set_page_config(page_title="Rifa de Celular", page_icon="🎟️", layout="wide")
    st.markdown(CSS_CUSTOM, unsafe_allow_html=True)
    inicializar_estado()

    conn = st.connection("gsheets", type=GSheetsConnection)
    procesar_retorno_pago(conn)

    st.title("📱 Plataforma de Boletos - Gran Rifa")
    tab1, tab2 = st.tabs(["🛒 Comprar Boletos", "🔍 Buscar mis Boletos / Verificar Pago"])

    with tab2:
        st.markdown("### ¿Pagaste con Mercado Pago o Stripe y cerraste la ventana?")
        col_b1, col_b2 = st.columns(2)
        with col_b1:
            buscar_num = st.text_input("Ingresa un número de boleto (ej. 005):")
        with col_b2:
            buscar_correo = st.text_input("Ingresa tu correo asociado:")

        if st.button("🔍 Verificar Pago y Descargar PDF", type="primary"):
            if not buscar_num or not buscar_correo:
                st.warning("Por favor, llena ambos campos.")
            else:
                with st.spinner("Buscando boletos y confirmando pago..."):
                    datos = buscar_ventas_por_correo_boleto(conn, buscar_num, buscar_correo)
                    if not datos:
                        datos = verificar_y_recuperar_boletos_desde_reserva(conn, buscar_num, buscar_correo)

                    if datos:
                        st.success("✅ Boletos encontrados. Puedes descargar tu PDF.")
                        procesar_descarga_pdf(datos)
                    else:
                        st.error("No encontramos boletos pagados con esos datos. Si acabas de pagar, espera unos segundos y vuelve a intentar.")
                        mostrar_diagnostico_pagos()

    with tab1:
        if st.session_state.get("boletos_confirmados"):
            st.balloons()
            st.success(f"🎉 ¡Compra Confirmada! (ID: {st.session_state.get('payment_success_id', 'N/A')})")
            procesar_descarga_pdf(st.session_state.boletos_confirmados)
            st.write("---")
            if st.button("⬅️ Realizar otra compra", use_container_width=True):
                st.session_state.boletos_confirmados = []
                st.session_state.payment_success_id = None
                limpiar_carrito_local()
                st.rerun()
            st.stop()

        col_mapa, col_form = st.columns([1.5, 1], gap="large")
        with col_mapa:
            st.subheader("🎟️ Mapa de Disponibilidad")
            renderizar_mapa_interactivo()

        with col_form:
            st.subheader("🛒 Finalizar Compra")
            precio_base = 15.00
            boletos = st.session_state.selected_tickets

            with st.container(border=True):
                if not boletos:
                    st.info("👆 Selecciona uno o más boletos disponibles.")
                    st.session_state.pago_generado_url = None
                    st.session_state.stripe_pago_url = None
                else:
                    total_pagar = precio_base * len(boletos)
                    st.success(f"🎫 **En tu carrito:** {', '.join(boletos)} (Tienes 15 min para pagar)")

                    if st.session_state.pago_generado_url or st.session_state.stripe_pago_url:
                        st.write(f"### Total a pagar: ${total_pagar:.2f} MXN")
                        opcion_pago = st.radio("Elige tu método de pago seguro:", ["💳 Mercado Pago", "💳 Stripe"], horizontal=True)

                        if "Mercado Pago" in opcion_pago:
                            if st.session_state.pago_generado_url:
                                st.info("Serás redirigido a Mercado Pago para completar el pago.")
                                st.link_button("💳 Pagar en Mercado Pago ➔", url=st.session_state.pago_generado_url, type="primary", use_container_width=True)
                            else:
                                st.error("Mercado Pago no está disponible. Revisa MP_ACCESS_TOKEN y MP_RETURN_URL.")
                                mostrar_diagnostico_pagos()
                        else:
                            if st.session_state.stripe_pago_url:
                                st.info("Serás redirigido al Checkout seguro hospedado por Stripe.")
                                st.link_button("💳 Pagar con Stripe ➔", url=st.session_state.stripe_pago_url, type="primary", use_container_width=True)
                            else:
                                st.error("Stripe no está disponible. Revisa STRIPE_SECRET_KEY y STRIPE_RETURN_URL.")
                                mostrar_diagnostico_pagos()

                        st.write("---")
                        if st.button("❌ Cancelar reserva y vaciar carrito"):
                            ext_ref_cancelada = st.session_state.get("external_ref_activa", "")
                            if ext_ref_cancelada:
                                liberado, mensaje_liberacion = liberar_reserva_por_rechazo_o_cancelacion(conn, ext_ref_cancelada, motivo="CANCELADO_USUARIO")
                                if not liberado:
                                    st.error(f"No se pudo liberar la reserva en la base de datos: {mensaje_liberacion}")
                                    st.stop()
                            limpiar_carrito_local()
                            st.success("Reserva cancelada. Los boletos fueron liberados correctamente.")
                            st.rerun()
                    else:
                        col_nom, col_ape = st.columns(2)
                        with col_nom:
                            nombre = st.text_input("Nombre(s):")
                        with col_ape:
                            apellidos = st.text_input("Apellidos:")

                        col_usr, col_dom = st.columns([3, 2.5])
                        with col_usr:
                            correo_usuario = st.text_input("Correo (sin @):", placeholder="ej. juanperez")
                        with col_dom:
                            dominio = st.selectbox("Extensión:", ["@gmail.com", "@hotmail.com", "@outlook.com", "@yahoo.com", "Otro..."])

                        if dominio == "Otro...":
                            correo = st.text_input("Correo completo:", placeholder="usuario@empresa.com")
                        else:
                            correo = f"{correo_usuario.replace('@', '').strip()}{dominio}" if correo_usuario else ""

                        telefono = st.text_input("WhatsApp (10 dígitos):", max_chars=10)
                        st.write(f"**Total a Pagar:** ${total_pagar:.2f} MXN")

                        if st.button("🔒 Confirmar y Elegir Método de Pago", type="primary", use_container_width=True):
                            pre_reservas = obtener_pre_reservas_globales()
                            ahora = datetime.now()
                            siguen_validos = all(
                                t in pre_reservas
                                and pre_reservas[t]["session_id"] == st.session_state.session_id
                                and pre_reservas[t]["expires_at"] > ahora
                                for t in boletos
                            )
                            correo_valido = re.match(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z]{2,}$", correo.strip().lower())

                            if not siguen_validos:
                                st.error("⚠️ El tiempo de carrito (15 min) expiró. Por favor, selecciona los boletos de nuevo.")
                                st.session_state.selected_tickets = []
                            elif not nombre or not apellidos or not correo or not telefono:
                                st.error("⚠️ Completa todos los campos.")
                            elif not correo_valido:
                                st.error("⚠️ El formato del correo NO es válido.")
                            elif not (telefono.isdigit() and len(telefono) == 10):
                                st.error("⚠️ El número debe contener 10 dígitos numéricos.")
                            else:
                                ref = f"RIFA-{datetime.now().strftime('%Y%m%d%H%M%S')}-{random.randint(1000, 9999)}"
                                st.session_state.external_ref_activa = ref
                                st.session_state.errores_proveedores = []
                                st.session_state.ultimo_error_pago = ""

                                ordenes = [{
                                    "External_Reference": ref,
                                    "MercadoPago_Preference_ID": "",
                                    "MercadoPago_Payment_ID": "",
                                    "Stripe_Session_ID": "",
                                    "Stripe_Payment_ID": "",
                                    "Numero_Boleto": str(t),
                                    "Nombre": f"{nombre.strip()} {apellidos.strip()}",
                                    "Correo": correo.strip().lower(),
                                    "Numero_Telefonico": telefono,
                                    "Monto": float(precio_base),
                                    "Estado_Reserva": "PENDIENTE",
                                    "Fecha_Creacion": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                    "Expira_En": (datetime.now() + timedelta(minutes=TIEMPO_RESERVA_MINUTOS)).strftime("%Y-%m-%d %H:%M:%S"),
                                    "Fecha_Actualizacion": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                } for t in boletos]

                                exito, msg = registrar_reserva_cobro(conn, ordenes)
                                if exito:
                                    errores_proveedores = []
                                    pref_id, init_point = "", ""

                                    try:
                                        pref_id, init_point = crear_preferencia_mercado_pago(nombre, apellidos, correo, telefono, boletos, precio_base, ref)
                                    except Exception as e:
                                        errores_proveedores.append(f"Mercado Pago: {e}")

                                    st.session_state.pago_generado_url = init_point
                                    stripe_session_id, stripe_checkout_url = "", ""

                                    try:
                                        stripe_session_id, stripe_checkout_url = crear_sesion_stripe(
                                            nombre=nombre,
                                            apellidos=apellidos,
                                            correo=correo,
                                            numeros_boletos=boletos,
                                            monto_unitario=precio_base,
                                            external_reference=ref
                                        )
                                    except Exception as e:
                                        st.session_state.ultimo_error_pago = str(e)
                                        errores_proveedores.append(f"Stripe: {e}")

                                    st.session_state.errores_proveedores = errores_proveedores
                                    st.session_state.stripe_session_id = stripe_session_id
                                    st.session_state.stripe_pago_url = stripe_checkout_url

                                    actualizado, mensaje_actualizacion = actualizar_ids_proveedores_reserva(
                                        conn=conn,
                                        external_reference=ref,
                                        mercado_pago_preference_id=pref_id,
                                        stripe_session_id=stripe_session_id
                                    )
                                    if not actualizado:
                                        st.warning(f"La reserva se registró, pero no fue posible guardar los IDs de pago: {mensaje_actualizacion}")

                                    if not init_point and not stripe_checkout_url:
                                        liberar_reserva_por_rechazo_o_cancelacion(conn, ref, motivo="ERROR_GENERACION_PAGO")
                                        limpiar_carrito_local()
                                        st.error("No fue posible generar ningún enlace de pago. La reserva fue liberada. " + " | ".join(errores_proveedores))
                                        mostrar_diagnostico_pagos()
                                    else:
                                        if errores_proveedores:
                                            st.warning("Uno de los proveedores no estuvo disponible: " + " | ".join(errores_proveedores))
                                        st.rerun()
                                else:
                                    st.error(f"Error al registrar la reserva en la base de datos: {msg}")


if __name__ == "__main__":
    main()

