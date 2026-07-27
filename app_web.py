parse import urlencode, urlparse, parse_qsl, urlunparse

import pandas as pd
import streamlit as st
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle, PageBreak
from streamlit_gsheets import GSheetsConnection

import mercadopago
import stripe

TIEMPO_RESERVA_MINUTOS = 1440
TIEMPO_PRERESERVA_MINUTOS = 15
TOTAL_BOLETOS = 100
PRECIO_BOLETO = 15.00


def obtener_config(nombre: str, default: str = "") -> str:
    try:
        if hasattr(st, "secrets") and nombre in st.secrets:
            return str(st.secrets[nombre]).strip()
    except Exception:
        pass
    valor = os.getenv(nombre)
    if valor is not None:
        return str(valor).strip()
    return default


MP_ACCESS_TOKEN = obtener_config("MP_ACCESS_TOKEN")
MP_NOTIFICATION_URL = obtener_config("MP_NOTIFICATION_URL")
MP_RETURN_URL = obtener_config("MP_RETURN_URL")
MP_CURRENCY_ID = obtener_config("MP_CURRENCY_ID", "MXN")

STRIPE_SECRET_KEY = obtener_config("STRIPE_SECRET_KEY")
STRIPE_RETURN_URL = obtener_config("STRIPE_RETURN_URL")
STRIPE_CURRENCY_ID = obtener_config("STRIPE_CURRENCY_ID", "mxn").lower()
DEBUG_PAGOS = obtener_config("DEBUG_PAGOS", "false").lower() in ["1", "true", "si", "yes", "on"]

sdk = mercadopago.SDK(MP_ACCESS_TOKEN) if MP_ACCESS_TOKEN else None
if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

CSS_CUSTOM = """
<style>
[data-testid="column"] { padding: 0 4px !important; }
[data-testid="stButton"] button {
    width: 100%; height: 55px; padding: 0;
    font-weight: 700; font-size: 14px; transition: all 0.2s;
}
[data-testid="stButton"] button:hover { transform: scale(1.02); border-color: #004481; }
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


def normalizar_url(url: str) -> str:
    url = str(url or "").strip()
    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def agregar_parametros_url(url: str, parametros: Dict[str, str]) -> str:
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



def safe_get(obj: Any, key: str, default=None):
    try:
        if obj is None:
            return default
        if isinstance(obj, dict):
            return obj.get(key, default)
        if hasattr(obj, "get"):
            try:
                return obj.get(key, default)
            except Exception:
                pass
        if hasattr(obj, key):
            return getattr(obj, key)
        try:
            return obj[key]
        except Exception:
            return default
    except Exception:
        return default

def parse_ticket_number(valor: Any) -> str:
    if pd.isna(valor) or str(valor).strip() == "":
        return ""
    try:
        return f"{int(float(valor)):03d}"
    except Exception:
        return str(valor).strip().zfill(3)


def normalizar_fecha_unix(fecha_txt: str, dias_antes: int = 5, dias_despues: int = 5) -> dict:
    try:
        fecha = pd.to_datetime(str(fecha_txt)).to_pydatetime()
    except Exception:
        fecha = datetime.now()
    return {
        "gte": int((fecha - timedelta(days=dias_antes)).timestamp()),
        "lte": int((fecha + timedelta(days=dias_despues)).timestamp()),
    }


def mostrar_diagnostico_pagos():
    if not DEBUG_PAGOS:
        return
    with st.expander("Diagnostico tecnico"):
        st.json({
            "MP_ACCESS_TOKEN_configurado": bool(MP_ACCESS_TOKEN),
            "MP_RETURN_URL": MP_RETURN_URL or "NO CONFIGURADO",
            "STRIPE_SECRET_KEY_configurado": bool(STRIPE_SECRET_KEY),
            "STRIPE_SECRET_KEY_es_sk": str(STRIPE_SECRET_KEY).startswith("sk_"),
            "STRIPE_RETURN_URL": STRIPE_RETURN_URL or "NO CONFIGURADO",
            "STRIPE_CURRENCY_ID": STRIPE_CURRENCY_ID,
            "ultimo_error_pago": st.session_state.get("ultimo_error_pago", ""),
        })


@st.cache_resource
def obtener_pre_reservas_globales() -> dict:
    return {}


def limpiar_pre_reservas_expiradas(pre_reservas: dict):
    ahora = datetime.now()
    expirados = [k for k, v in list(pre_reservas.items()) if v["expires_at"] < ahora]
    for k in expirados:
        del pre_reservas[k]


def limpiar_carrito_local():
    pre_reservas = obtener_pre_reservas_globales()
    for boleto in list(st.session_state.get("selected_tickets", [])):
        if boleto in pre_reservas and pre_reservas[boleto]["session_id"] == st.session_state.session_id:
            del pre_reservas[boleto]
    st.session_state.selected_tickets = []
    st.session_state.pago_generado_url = None
    st.session_state.stripe_pago_url = None
    st.session_state.stripe_session_id = None
    st.session_state.payment_provider = None
    st.session_state.external_ref_activa = None


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


def registrar_reserva_cobro(conn: GSheetsConnection, ordenes: List[Dict[str, Any]]) -> Tuple[bool, str]:
    try:
        df_r = leer_reservas(conn)
        df_actualizado = pd.concat([
            asegurar_columnas(df_r.dropna(how="all"), columnas_reservas()),
            asegurar_columnas(pd.DataFrame(ordenes), columnas_reservas())
        ], ignore_index=True)
        conn.update(worksheet="Reservas", data=df_actualizado)
        return True, "Exito"
    except Exception as e:
        return False, str(e)


def actualizar_ids_proveedores_reserva(conn: GSheetsConnection, external_reference: str, mercado_pago_preference_id: str = "", stripe_session_id: str = "") -> Tuple[bool, str]:
    try:
        df_r = leer_reservas(conn)
        filtro = df_r["External_Reference"].astype(str) == str(external_reference)
        if not filtro.any():
            return False, "No se encontro la reserva."
        if mercado_pago_preference_id:
            df_r.loc[filtro, "MercadoPago_Preference_ID"] = mercado_pago_preference_id
        if stripe_session_id:
            df_r.loc[filtro, "Stripe_Session_ID"] = stripe_session_id
        df_r.loc[filtro, "Fecha_Actualizacion"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.update(worksheet="Reservas", data=df_r)
        return True, "Exito"
    except Exception as e:
        return False, str(e)


def marcar_reserva_estado(conn: GSheetsConnection, external_reference: str, estado: str) -> Tuple[bool, str]:
    try:
        if not external_reference:
            return False, "Sin referencia."
        df_r = leer_reservas(conn)
        filtro = df_r["External_Reference"].astype(str) == str(external_reference)
        if not filtro.any():
            return False, "No se encontro la reserva."
        df_r.loc[filtro, "Estado_Reserva"] = estado
        df_r.loc[filtro, "Fecha_Actualizacion"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.update(worksheet="Reservas", data=df_r)
        return True, "Exito"
    except Exception as e:
        return False, str(e)


def liberar_reserva_por_rechazo_o_cancelacion(conn: GSheetsConnection, external_reference: str, motivo: str = "CANCELADO_PAGO") -> Tuple[bool, str]:
    try:
        if not external_reference:
            return False, "Sin referencia."
        df_r = leer_reservas(conn)
        filtro = df_r["External_Reference"].astype(str) == str(external_reference)
        if not filtro.any():
            return True, "Sin reservas."
        df_r.loc[filtro, "Estado_Reserva"] = motivo
        df_r.loc[filtro, "Fecha_Actualizacion"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.update(worksheet="Reservas", data=df_r)
        return True, "Reserva liberada."
    except Exception as e:
        return False, str(e)


def actualizar_pago_en_hojas(conn: GSheetsConnection, payment_info: Dict[str, Any]) -> List[Dict[str, Any]]:
    ext_ref = str(payment_info.get("external_reference", "")).strip()
    pago_id = str(payment_info.get("id", "")).strip()
    proveedor = str(payment_info.get("provider", "MERCADO_PAGO")).strip().upper()
    metodo_pago = str(payment_info.get("payment_type_id", proveedor.lower())).strip()
    provider_session_id = str(payment_info.get("provider_session_id", "")).strip()
    if not ext_ref:
        return []

    df_r = leer_reservas(conn)
    df_v = leer_ventas(conn)
    ventas_ref = df_v[df_v["Referencia_Pago"].astype(str) == ext_ref]
    if not ventas_ref.empty:
        return ventas_ref.to_dict(orient="records")

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
            "Estado_Pago": "VENDIDO",
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
            [Paragraph("<b>No. de Boleto:</b>", estilo_normal), Paragraph(str(boleto.get("Numero_Boleto", "")), estilo_normal)],
            [Paragraph("<b>Precio Pagado:</b>", estilo_normal), Paragraph(f"${precio_float:.2f} {MP_CURRENCY_ID}", estilo_normal)],
            [Paragraph("<b>Metodo de Pago:</b>", estilo_normal), Paragraph(str(boleto.get("Metodo_Pago", "Pago electronico")).upper(), estilo_normal)],
            [Paragraph("<b>Ref / ID Pago:</b>", estilo_normal), Paragraph(str(boleto.get("Codigo_Pago", "N/A")), estilo_normal)],
            [Paragraph("<b>Fecha:</b>", estilo_normal), Paragraph(str(boleto.get("Fecha_Compra", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))), estilo_normal)]
        ]
        tabla = Table(data, colWidths=[165, 300])
        tabla.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#CBD5E1")),
            ("INNERGRID", (0, 0), (-1, -1), 0.6, colors.HexColor("#E2E8F0")),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]))
        story.append(tabla)
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
    label = "Descargar mis Boletos Oficiales (PDF)" if len(datos_boletos) > 1 else "Descargar mi Boleto Oficial (PDF)"
    st.download_button(label=label, data=pdf_bytes, file_name=archivo_pdf, mime="application/pdf", type="primary", use_container_width=True)


def construir_pago_stripe_desde_session(session: Any, external_reference_reserva: str = "", correo_reserva: str = "", monto_esperado: Optional[float] = None) -> Optional[Dict[str, Any]]:
    if session.get("payment_status") != "paid":
        return None
    metadata = dict(session.get("metadata") or {})
    ref_stripe = str(metadata.get("external_reference") or session.get("client_reference_id") or "").strip()
    customer_details = session.get("customer_details") or {}
    correo_stripe = str(session.get("customer_email") or customer_details.get("email") or "").strip().lower()
    correo_reserva = str(correo_reserva or "").strip().lower()
    monto_ok = True
    if monto_esperado is not None:
        monto_ok = int(session.get("amount_total") or 0) == int(round(float(monto_esperado) * 100))
    ref_ok = bool(external_reference_reserva and ref_stripe == external_reference_reserva)
    correo_ok = bool(correo_reserva and correo_stripe == correo_reserva)
    if external_reference_reserva and not (ref_ok or (correo_ok and monto_ok)):
        return None
    payment_intent = session.get("payment_intent")
    if isinstance(payment_intent, str):
        payment_intent_id = payment_intent
    elif payment_intent:
        payment_intent_id = str(payment_intent.get("id", ""))
    else:
        payment_intent_id = str(session.get("id", ""))
    return {
        "id": payment_intent_id,
        "external_reference": ref_stripe or external_reference_reserva,
        "payment_type_id": "stripe_card",
        "status": "approved",
        "provider": "STRIPE",
        "provider_session_id": str(session.get("id", ""))
    }


def obtener_pago_stripe(stripe_session_id: str, external_reference_esperada: Optional[str] = None, monto_esperado: Optional[float] = None, correo_reserva: str = "") -> Optional[Dict[str, Any]]:
    if not STRIPE_SECRET_KEY or not stripe_session_id:
        return None
    try:
        session = stripe.checkout.Session.retrieve(stripe_session_id, expand=["payment_intent"])
        return construir_pago_stripe_desde_session(session, external_reference_esperada or "", correo_reserva, monto_esperado)
    except Exception as e:
        st.session_state.ultimo_error_pago = f"Stripe retrieve: {e}"
        return None


def obtener_pago_stripe_por_payment_intent(payment_intent_id: str, external_reference_esperada: Optional[str] = None, monto_esperado: Optional[float] = None, correo_reserva: str = "") -> Optional[Dict[str, Any]]:
    if not STRIPE_SECRET_KEY or not payment_intent_id:
        st.session_state.ultimo_error_pago = "Stripe: falta STRIPE_SECRET_KEY o PaymentIntent."
        return None

    payment_intent_id = str(payment_intent_id or "").strip()
    external_reference_esperada = str(external_reference_esperada or "").strip()
    correo_reserva = str(correo_reserva or "").strip().lower()

    if not payment_intent_id.startswith("pi_"):
        st.session_state.ultimo_error_pago = f"Stripe: el ID no es PaymentIntent pi_: {payment_intent_id}"
        return None

    try:
        try:
            sesiones = stripe.checkout.Session.list(
                payment_intent=payment_intent_id,
                limit=1,
                expand=["data.payment_intent"]
            )
            data = safe_get(sesiones, "data", [])
            if data:
                pago_desde_sesion = construir_pago_stripe_desde_session(
                    session=data[0],
                    external_reference_reserva=external_reference_esperada,
                    correo_reserva=correo_reserva,
                    monto_esperado=monto_esperado
                )
                if pago_desde_sesion:
                    return pago_desde_sesion
        except Exception as e:
            st.session_state.ultimo_error_pago = f"Stripe: no se pudo validar sesion por PaymentIntent. Se intenta PI directo. Detalle: {e}"

        intent = stripe.PaymentIntent.retrieve(payment_intent_id, expand=["latest_charge"])
        estado_intent = str(safe_get(intent, "status", "")).strip().lower()
        if estado_intent != "succeeded":
            st.session_state.ultimo_error_pago = f"Stripe: PaymentIntent no aprobado. ID={payment_intent_id}, status={estado_intent}"
            return None

        monto_recibido_centavos = int(safe_get(intent, "amount", 0) or 0)
        if monto_esperado is not None:
            monto_esperado_centavos = int(round(float(monto_esperado) * 100))
            if monto_recibido_centavos != monto_esperado_centavos:
                st.session_state.ultimo_error_pago = f"Stripe: monto no coincide. Esperado={monto_esperado_centavos}, Stripe={monto_recibido_centavos}, ID={payment_intent_id}"
                return None

        metadata = dict(safe_get(intent, "metadata", {}) or {})
        ref_intent = str(metadata.get("external_reference") or "").strip()
        latest_charge = safe_get(intent, "latest_charge", {}) or {}
        billing_details = safe_get(latest_charge, "billing_details", {}) or {}
        correo_stripe = str(safe_get(billing_details, "email", "") or safe_get(intent, "receipt_email", "") or "").strip().lower()

        if external_reference_esperada and ref_intent and ref_intent != external_reference_esperada:
            st.session_state.ultimo_error_pago = f"Stripe: referencia no coincide. Esperada={external_reference_esperada}, Stripe={ref_intent}, ID={payment_intent_id}"
            return None
        if correo_reserva and correo_stripe and correo_stripe != correo_reserva:
            st.session_state.ultimo_error_pago = f"Stripe: correo no coincide. Esperado={correo_reserva}, Stripe={correo_stripe}, ID={payment_intent_id}"
            return None

        return {
            "id": payment_intent_id,
            "external_reference": ref_intent or external_reference_esperada,
            "payment_type_id": "stripe_card",
            "status": "approved",
            "provider": "STRIPE",
            "provider_session_id": ""
        }
    except Exception as e:
        st.session_state.ultimo_error_pago = f"Stripe PaymentIntent retrieve: {e}"
        return None

def buscar_pago_stripe_por_referencia_correo_fecha(external_reference: str, correo: str, monto_esperado: Optional[float], fecha_creacion_reserva: str) -> Optional[Dict[str, Any]]:
    if not STRIPE_SECRET_KEY:
        return None
    external_reference = str(external_reference or "").strip()
    correo = str(correo or "").strip().lower()
    rango_fecha = normalizar_fecha_unix(fecha_creacion_reserva)
    try:
        parametros = {"limit": 100, "status": "complete", "created": rango_fecha, "customer_details": {"email": correo}, "expand": ["data.payment_intent"]}
        sesiones = stripe.checkout.Session.list(**parametros)
        for session in sesiones.get("data", []):
            pago = construir_pago_stripe_desde_session(session, external_reference, correo, monto_esperado)
            if pago:
                return pago
        starting_after = None
        for _ in range(10):
            params = {"limit": 100, "status": "complete", "created": rango_fecha, "expand": ["data.payment_intent"]}
            if starting_after:
                params["starting_after"] = starting_after
            sesiones = stripe.checkout.Session.list(**params)
            data = sesiones.get("data", [])
            for session in data:
                pago = construir_pago_stripe_desde_session(session, external_reference, correo, monto_esperado)
                if pago:
                    return pago
            if not sesiones.get("has_more") or not data:
                break
            starting_after = str(data[-1].get("id"))
        return None
    except Exception as e:
        st.session_state.ultimo_error_pago = f"Stripe conciliacion: {e}"
        return None


def buscar_pago_stripe_payment_intent_por_correo_fecha(external_reference: str, correo: str, monto_esperado: Optional[float], fecha_creacion_reserva: str) -> Optional[Dict[str, Any]]:
    if not STRIPE_SECRET_KEY:
        st.session_state.ultimo_error_pago = "Stripe: STRIPE_SECRET_KEY no configurado."
        return None

    external_reference = str(external_reference or "").strip()
    correo = str(correo or "").strip().lower()
    rango_fecha = normalizar_fecha_unix(fecha_creacion_reserva)
    monto_centavos = int(round(float(monto_esperado) * 100)) if monto_esperado is not None else None

    try:
        starting_after = None
        for _ in range(20):
            params = {"limit": 100, "created": rango_fecha, "expand": ["data.latest_charge"]}
            if starting_after:
                params["starting_after"] = starting_after
            intents = stripe.PaymentIntent.list(**params)
            data = safe_get(intents, "data", [])
            for intent in data:
                estado_intent = str(safe_get(intent, "status", "")).strip().lower()
                if estado_intent != "succeeded":
                    continue
                amount_intent = int(safe_get(intent, "amount", 0) or 0)
                if monto_centavos is not None and amount_intent != monto_centavos:
                    continue
                metadata = dict(safe_get(intent, "metadata", {}) or {})
                ref_intent = str(metadata.get("external_reference") or "").strip()
                latest_charge = safe_get(intent, "latest_charge", {}) or {}
                billing = safe_get(latest_charge, "billing_details", {}) or {}
                correo_charge = str(safe_get(billing, "email", "") or safe_get(intent, "receipt_email", "") or "").strip().lower()
                ref_ok = bool(external_reference and ref_intent == external_reference)
                correo_ok = bool(correo and correo_charge == correo)
                if external_reference and not (ref_ok or correo_ok):
                    continue
                return {
                    "id": str(safe_get(intent, "id", "")),
                    "external_reference": ref_intent or external_reference,
                    "payment_type_id": "stripe_card",
                    "status": "approved",
                    "provider": "STRIPE",
                    "provider_session_id": ""
                }
            if not safe_get(intents, "has_more", False) or not data:
                break
            starting_after = str(safe_get(data[-1], "id", ""))
        st.session_state.ultimo_error_pago = f"Stripe: no se encontro PaymentIntent conciliable. Referencia={external_reference}, correo={correo}, monto_centavos={monto_centavos}"
        return None
    except Exception as e:
        st.session_state.ultimo_error_pago = f"Stripe PI conciliacion: {e}"
        return None

def buscar_pago_mercadopago_por_referencia_correo_fecha(external_reference: str, correo: str, monto_esperado: Optional[float], fecha_creacion_reserva: str) -> Optional[Dict[str, Any]]:
    if not sdk:
        return None
    external_reference = str(external_reference or "").strip()
    correo = str(correo or "").strip().lower()

    def monto_ok(pago: Dict[str, Any]) -> bool:
        if monto_esperado is None:
            return True
        try:
            return abs(float(pago.get("transaction_amount", 0)) - float(monto_esperado)) < 0.01
        except Exception:
            return False

    try:
        pagos = []
        if external_reference:
            respuesta = sdk.payment().search({"external_reference": external_reference, "status": "approved", "sort": "date_created", "criteria": "desc"}).get("response", {})
            pagos = respuesta.get("results", [])
        for pago in pagos:
            if pago.get("status") == "approved" and monto_ok(pago):
                pago["provider"] = "MERCADO_PAGO"
                if not pago.get("external_reference"):
                    pago["external_reference"] = external_reference
                return pago
        for consulta in [{"payer.email": correo}, {"payer_email": correo}]:
            try:
                respuesta = sdk.payment().search(consulta).get("response", {})
                pagos = respuesta.get("results", [])
            except Exception:
                pagos = []
            for pago in pagos:
                payer = pago.get("payer") or {}
                correo_pago = str(payer.get("email") or "").strip().lower()
                ref_pago = str(pago.get("external_reference") or "").strip()
                if pago.get("status") == "approved" and monto_ok(pago) and (ref_pago == external_reference or correo_pago == correo):
                    pago["provider"] = "MERCADO_PAGO"
                    if not pago.get("external_reference"):
                        pago["external_reference"] = external_reference
                    return pago
        return None
    except Exception as e:
        st.session_state.ultimo_error_pago = f"MP conciliacion: {e}"
        return None



def extraer_ids_pago_de_reserva(grupo_reserva: pd.DataFrame) -> Dict[str, str]:
    ids = {
        "stripe_session_id": "",
        "stripe_payment_id": "",
        "mp_payment_id": "",
        "mp_preference_id": ""
    }
    columnas_posibles = [
        "Stripe_Session_ID", "Stripe_Payment_ID", "MercadoPago_Payment_ID", "MercadoPago_Preference_ID",
        "External_Reference", "Numero_Boleto", "Nombre", "Correo", "Numero_Telefonico", "Monto",
        "Estado_Reserva", "Fecha_Creacion", "Expira_En", "Fecha_Actualizacion"
    ]
    for _, fila in grupo_reserva.iterrows():
        for columna in columnas_posibles:
            valor = str(fila.get(columna, "")).strip()
            if not valor or valor.lower() == "nan":
                continue
            if valor.startswith("cs_") and not ids["stripe_session_id"]:
                ids["stripe_session_id"] = valor
            elif valor.startswith("pi_") and not ids["stripe_payment_id"]:
                ids["stripe_payment_id"] = valor
            elif valor.isdigit() and len(valor) >= 6 and not ids["mp_payment_id"]:
                ids["mp_payment_id"] = valor
            elif not valor.startswith(("cs_", "pi_")) and len(valor) >= 10 and not ids["mp_preference_id"]:
                if columna == "MercadoPago_Preference_ID":
                    ids["mp_preference_id"] = valor
    return ids


def obtener_pago_mercadopago_por_payment_id(payment_id: str, external_reference_esperada: str = "", monto_esperado: Optional[float] = None, correo_reserva: str = "") -> Optional[Dict[str, Any]]:
    if not sdk or not payment_id:
        return None
    payment_id = str(payment_id or "").strip()
    if not payment_id.isdigit():
        return None
    try:
        pago = sdk.payment().get(payment_id).get("response", {})
        if not pago or pago.get("status") != "approved":
            return None
        if monto_esperado is not None:
            monto_pago = float(pago.get("transaction_amount", 0) or 0)
            if abs(monto_pago - float(monto_esperado)) >= 0.01:
                return None
        ref_pago = str(pago.get("external_reference") or "").strip()
        payer = pago.get("payer") or {}
        correo_pago = str(payer.get("email") or "").strip().lower()
        correo_reserva = str(correo_reserva or "").strip().lower()
        if external_reference_esperada and ref_pago and ref_pago != external_reference_esperada:
            return None
        if correo_reserva and correo_pago and correo_pago != correo_reserva:
            return None
        pago["provider"] = "MERCADO_PAGO"
        if not pago.get("external_reference") and external_reference_esperada:
            pago["external_reference"] = external_reference_esperada
        return pago
    except Exception as e:
        st.session_state.ultimo_error_pago = f"MP payment_id retrieve: {e}"
        return None


def buscar_pago_mercadopago_por_preference_id(preference_id: str, external_reference_esperada: str = "", monto_esperado: Optional[float] = None, correo_reserva: str = "") -> Optional[Dict[str, Any]]:
    if not sdk or not preference_id:
        return None
    preference_id = str(preference_id or "").strip()
    try:
        filtros = [
            {"preference_id": preference_id, "status": "approved", "sort": "date_created", "criteria": "desc"},
            {"external_reference": external_reference_esperada, "status": "approved", "sort": "date_created", "criteria": "desc"}
        ]
        for filtro in filtros:
            if not filtro.get("preference_id") and not filtro.get("external_reference"):
                continue
            respuesta = sdk.payment().search(filtro).get("response", {})
            pagos = respuesta.get("results", [])
            for pago in pagos:
                if pago.get("status") != "approved":
                    continue
                if monto_esperado is not None:
                    monto_pago = float(pago.get("transaction_amount", 0) or 0)
                    if abs(monto_pago - float(monto_esperado)) >= 0.01:
                        continue
                ref_pago = str(pago.get("external_reference") or "").strip()
                payer = pago.get("payer") or {}
                correo_pago = str(payer.get("email") or "").strip().lower()
                correo_reserva_limpio = str(correo_reserva or "").strip().lower()
                if external_reference_esperada and ref_pago and ref_pago != external_reference_esperada:
                    continue
                if correo_reserva_limpio and correo_pago and correo_pago != correo_reserva_limpio:
                    continue
                pago["provider"] = "MERCADO_PAGO"
                if not pago.get("external_reference") and external_reference_esperada:
                    pago["external_reference"] = external_reference_esperada
                return pago
        return None
    except Exception as e:
        st.session_state.ultimo_error_pago = f"MP preference_id search: {e}"
        return None

def crear_preferencia_mercado_pago(nombre, apellidos, correo, telefono, numeros_boletos: list, monto_unitario: float, external_reference: str):
    if not sdk:
        return "", ""
    url_retorno_base = normalizar_url(MP_RETURN_URL)
    titulos_boletos = ", ".join(numeros_boletos)
    preference_data = {
        "items": [{"title": f"Rifa celular - Boletos: {titulos_boletos}", "quantity": len(numeros_boletos), "unit_price": float(monto_unitario), "currency_id": MP_CURRENCY_ID}],
        "payer": {"name": nombre.strip(), "surname": apellidos.strip() or "Sin Apellido", "email": correo, "phone": {"area_code": "52", "number": telefono}},
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


def crear_sesion_stripe(nombre: str, apellidos: str, correo: str, numeros_boletos: List[str], monto_unitario: float, external_reference: str) -> Tuple[str, str]:
    if not STRIPE_SECRET_KEY:
        raise ValueError("STRIPE_SECRET_KEY no esta configurado.")
    if STRIPE_SECRET_KEY.startswith("pk_"):
        raise ValueError("STRIPE_SECRET_KEY contiene una llave publica pk_. Usa sk_test_ o sk_live_.")
    if not STRIPE_RETURN_URL:
        raise ValueError("STRIPE_RETURN_URL no esta configurado.")
    url_base = normalizar_url(STRIPE_RETURN_URL)
    success_url = agregar_parametros_url(url_base, {"stripe_session_id": "{CHECKOUT_SESSION_ID}", "external_reference": external_reference})
    cancel_url = agregar_parametros_url(url_base, {"stripe_cancelled": "true", "external_reference": external_reference})
    descripcion_boletos = ", ".join(numeros_boletos)
    session = stripe.checkout.Session.create(
        mode="payment",
        customer_email=correo.strip().lower(),
        client_reference_id=external_reference,
        line_items=[{"price_data": {"currency": STRIPE_CURRENCY_ID, "unit_amount": int(round(float(monto_unitario) * 100)), "product_data": {"name": "Boletos Rifa de Celular", "description": f"Boletos seleccionados: {descripcion_boletos}"[:500]}}, "quantity": len(numeros_boletos)}],
        metadata={"external_reference": external_reference, "boletos": descripcion_boletos, "nombre_cliente": f"{nombre.strip()} {apellidos.strip()}"[:500]},
        payment_intent_data={"metadata": {"external_reference": external_reference}},
        success_url=success_url,
        cancel_url=cancel_url
    )
    return str(session.id), str(session.url)


def recuperar_boletos_por_reserva(conn: GSheetsConnection, numero_boleto: str, correo: str) -> List[Dict[str, Any]]:
    df_v = leer_ventas(conn)
    correo_limpio = correo.strip().lower()
    num = parse_ticket_number(numero_boleto)
    if not df_v.empty:
        filtro_v = (df_v["Numero_Boleto"].astype(str).apply(parse_ticket_number) == num) & (df_v["Correo"].astype(str).str.lower() == correo_limpio)
        if filtro_v.any():
            ref = str(df_v[filtro_v].iloc[-1].get("Referencia_Pago", ""))
            return df_v[df_v["Referencia_Pago"].astype(str) == ref].to_dict(orient="records")
    df_r = leer_reservas(conn)
    filtro_r = (df_r["Numero_Boleto"].astype(str).apply(parse_ticket_number) == num) & (df_r["Correo"].astype(str).str.lower() == correo_limpio)
    reservas = df_r[filtro_r]
    if reservas.empty:
        return []
    reserva_base = reservas.iloc[-1]
    ext_ref = str(reserva_base.get("External_Reference", "")).strip()
    if not ext_ref:
        return []
    grupo = df_r[df_r["External_Reference"].astype(str) == ext_ref]
    total = float(grupo["Monto"].astype(float).sum()) if not grupo.empty else None
    fecha_creacion = str(reserva_base.get("Fecha_Creacion", ""))
    ids_pago = extraer_ids_pago_de_reserva(grupo)

    pago = None

    # Mercado Pago por payment_id directo, si existe o si quedo en otra columna.
    if ids_pago.get("mp_payment_id"):
        pago = obtener_pago_mercadopago_por_payment_id(ids_pago["mp_payment_id"], ext_ref, total, correo_limpio)

    # Mercado Pago por preference_id o external_reference.
    if not pago and ids_pago.get("mp_preference_id"):
        pago = buscar_pago_mercadopago_por_preference_id(ids_pago["mp_preference_id"], ext_ref, total, correo_limpio)

    if not pago:
        pago = buscar_pago_mercadopago_por_referencia_correo_fecha(ext_ref, correo_limpio, total, fecha_creacion)

    # Stripe por session id.
    if not pago and ids_pago.get("stripe_session_id"):
        pago = obtener_pago_stripe(ids_pago["stripe_session_id"], ext_ref, total, correo_limpio)

    # Stripe por payment intent pi_ directo.
    if not pago and ids_pago.get("stripe_payment_id", "").startswith("pi_"):
        pago = obtener_pago_stripe_por_payment_intent(ids_pago["stripe_payment_id"], ext_ref, total, correo_limpio)

    # Stripe por conciliacion de sesiones.
    if not pago:
        pago = buscar_pago_stripe_por_referencia_correo_fecha(ext_ref, correo_limpio, total, fecha_creacion)

    # Stripe por conciliacion de payment intents.
    if not pago:
        pago = buscar_pago_stripe_payment_intent_por_correo_fecha(ext_ref, correo_limpio, total, fecha_creacion)

    if pago:
        datos = actualizar_pago_en_hojas(conn, pago)
        if datos:
            return datos
        df_v = leer_ventas(conn)
        ventas_ref = df_v[df_v["Referencia_Pago"].astype(str) == ext_ref]
        if not ventas_ref.empty:
            return ventas_ref.to_dict(orient="records")
    return []

def obtener_estado_boletos_bd(df_ventas: pd.DataFrame, df_reservas: pd.DataFrame) -> dict:
    estados = {}
    estados_reserva_bloqueantes = ["PENDIENTE", "ERROR_CONFIRMACION_STRIPE", "ERROR_CONFIRMACION_MERCADO_PAGO"]
    if not df_reservas.empty and "Numero_Boleto" in df_reservas.columns:
        for _, row in df_reservas.iterrows():
            if str(row.get("Estado_Reserva", "")).strip().upper() in estados_reserva_bloqueantes:
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


def renderizar_mapa_interactivo():
    mi_sesion = st.session_state.session_id
    pre_reservas = obtener_pre_reservas_globales()
    limpiar_pre_reservas_expiradas(pre_reservas)
    if not (st.session_state.get("pago_generado_url") or st.session_state.get("stripe_pago_url")):
        st.session_state.selected_tickets = [t for t in st.session_state.selected_tickets if t in pre_reservas and pre_reservas[t]["session_id"] == mi_sesion]
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
    for i in range(TOTAL_BOLETOS):
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
    disponibles = TOTAL_BOLETOS - vendidos - reservados_bd - pre_reservados_otros - len(st.session_state.selected_tickets)
    st.markdown(f"""
    <div class="metric-container">
        <div class="metric-box m-green"><h2>🟢 {disponibles}</h2><p>Libres</p></div>
        <div class="metric-box m-gray"><h2>🔒 {pre_reservados_otros}</h2><p>En otro carrito</p></div>
        <div class="metric-box m-yellow"><h2>🟠 {reservados_bd}</h2><p>Reservados / validando</p></div>
        <div class="metric-box m-red"><h2>🔴 {vendidos}</h2><p>Vendidos</p></div>
    </div>
    """, unsafe_allow_html=True)
    for fila in range(10):
        cols = st.columns(10)
        for col_idx in range(10):
            num = f"{(fila * 10 + col_idx):03d}"
            estado = estados_pantalla[num]
            with cols[col_idx]:
                if estado == "vendido_db":
                    st.button(f"🔴\n{num}", disabled=True, key=f"btn_{num}", help="Vendido")
                elif estado == "reservado_db":
                    st.button(f"🟠\n{num}", disabled=True, key=f"btn_{num}", help="Reservado / validando pago")
                elif estado == "pre_reservado_otros":
                    st.button(f"🔒\n{num}", disabled=True, key=f"btn_{num}", help="En carrito de otro usuario")
                else:
                    seleccionado = estado == "pre_reservado_mio" or num in st.session_state.selected_tickets
                    etiqueta = f"✅\n{num}" if seleccionado else f"🟢\n{num}"
                    if st.button(etiqueta, key=f"btn_{num}", type="primary" if seleccionado else "secondary"):
                        if seleccionado:
                            pre_reservas.pop(num, None)
                            if num in st.session_state.selected_tickets:
                                st.session_state.selected_tickets.remove(num)
                        else:
                            pre_reservas[num] = {"session_id": mi_sesion, "expires_at": datetime.now() + timedelta(minutes=TIEMPO_PRERESERVA_MINUTOS)}
                            if num not in st.session_state.selected_tickets:
                                st.session_state.selected_tickets.append(num)
                        st.rerun()


def procesar_retorno_pago(conn: GSheetsConnection):
    qp = st.query_params
    mp_status = (qp_get(qp, "status", "") or qp_get(qp, "collection_status", "")).lower()
    mp_return = qp_get(qp, "mp_return", "").lower()
    payment_id = qp_get(qp, "payment_id", "") or qp_get(qp, "collection_id", "")
    ext_ref = qp_get(qp, "external_reference", "") or st.session_state.get("external_ref_activa", "")
    if mp_return == "failure" or mp_status in ["rejected", "cancelled", "canceled", "failure", "failed"]:
        liberar_reserva_por_rechazo_o_cancelacion(conn, ext_ref, "CANCELADO_MERCADO_PAGO")
        limpiar_carrito_local()
        st.query_params.clear()
        st.warning("Pago cancelado o rechazado. La reserva fue liberada.")
        st.rerun()
    if payment_id and mp_status == "approved":
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
            datos = actualizar_pago_en_hojas(conn, pago)
            st.session_state.boletos_confirmados = datos
            st.session_state.payment_success_id = str(pago.get("id", payment_id))
            limpiar_carrito_local()
            st.session_state.boletos_confirmados = datos
            st.query_params.clear()
            st.rerun()
        else:
            marcar_reserva_estado(conn, ext_ref, "ERROR_CONFIRMACION_MERCADO_PAGO")
            st.query_params.clear()
            st.warning("El pago esta en validacion. Puedes recuperarlo en Buscar mis Boletos / Verificar Pago.")
    if mp_return == "pending" or mp_status == "pending":
        st.query_params.clear()
        st.warning("Pago pendiente. La reserva se mantiene activa hasta confirmar el pago.")
    if "stripe_cancelled" in qp:
        liberar_reserva_por_rechazo_o_cancelacion(conn, ext_ref, "CANCELADO_STRIPE")
        limpiar_carrito_local()
        st.query_params.clear()
        st.warning("Pago cancelado o rechazado. La reserva fue liberada.")
        st.rerun()
    stripe_session_id = qp_get(qp, "stripe_session_id", "")
    if stripe_session_id:
        pago = obtener_pago_stripe(stripe_session_id, ext_ref if ext_ref else None)
        if pago:
            datos = actualizar_pago_en_hojas(conn, pago)
            st.session_state.boletos_confirmados = datos
            st.session_state.payment_success_id = str(pago.get("id", stripe_session_id))
            limpiar_carrito_local()
            st.session_state.boletos_confirmados = datos
            st.query_params.clear()
            st.rerun()
        else:
            marcar_reserva_estado(conn, ext_ref, "ERROR_CONFIRMACION_STRIPE")
            st.query_params.clear()
            st.warning("El pago esta en validacion. Puedes recuperarlo en Buscar mis Boletos / Verificar Pago.")


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
    if not MP_ACCESS_TOKEN:
        st.warning("Mercado Pago no esta configurado.")
    if not STRIPE_SECRET_KEY:
        st.warning("Stripe no esta configurado.")
    conn = st.connection("gsheets", type=GSheetsConnection)
    procesar_retorno_pago(conn)
    st.title("Plataforma de Boletos - Gran Rifa")
    tab1, tab2 = st.tabs(["Comprar Boletos", "Buscar mis Boletos / Verificar Pago"])
    with tab2:
        st.markdown("### Consulta tus boletos")
        c1, c2 = st.columns(2)
        with c1:
            buscar_num = st.text_input("Numero de boleto (ej. 005):")
        with c2:
            buscar_correo = st.text_input("Correo asociado:")
        if st.button("Verificar Pago y Descargar PDF", type="primary"):
            if not buscar_num or not buscar_correo:
                st.warning("Ingresa boleto y correo.")
            else:
                with st.spinner("Verificando pago y recuperando boletos..."):
                    datos = recuperar_boletos_por_reserva(conn, buscar_num, buscar_correo)
                    if datos:
                        st.success("Boletos encontrados. Puedes descargar tu PDF.")
                        procesar_descarga_pdf(datos)
                    else:
                        st.error("No encontramos boletos pagados con esos datos. Verifica correo y boleto o intenta nuevamente en unos segundos.")
                        mostrar_diagnostico_pagos()
    with tab1:
        if st.session_state.get("boletos_confirmados"):
            st.balloons()
            st.success(f"Compra confirmada. ID: {st.session_state.get('payment_success_id', 'N/A')}")
            procesar_descarga_pdf(st.session_state.boletos_confirmados)
            st.write("---")
            if st.button("Realizar otra compra", use_container_width=True):
                st.session_state.boletos_confirmados = []
                st.session_state.payment_success_id = None
                limpiar_carrito_local()
                st.rerun()
            st.stop()
        col_mapa, col_form = st.columns([1.5, 1], gap="large")
        with col_mapa:
            st.subheader("Mapa de Disponibilidad")
            renderizar_mapa_interactivo()
        with col_form:
            st.subheader("Finalizar Compra")
            boletos = st.session_state.selected_tickets
            with st.container(border=True):
                if not boletos:
                    st.info("Selecciona uno o mas boletos disponibles.")
                    st.session_state.pago_generado_url = None
                    st.session_state.stripe_pago_url = None
                else:
                    total_pagar = PRECIO_BOLETO * len(boletos)
                    st.success(f"En tu carrito: {', '.join(boletos)}")
                    if st.session_state.pago_generado_url or st.session_state.stripe_pago_url:
                        st.write(f"### Total a pagar: ${total_pagar:.2f} MXN")
                        opcion_pago = st.radio("Elige tu metodo de pago seguro:", ["Mercado Pago", "Stripe"], horizontal=True)
                        if "Mercado Pago" in opcion_pago:
                            if st.session_state.pago_generado_url:
                                st.link_button("Pagar en Mercado Pago", url=st.session_state.pago_generado_url, type="primary", use_container_width=True)
                            else:
                                st.error("Mercado Pago no esta disponible.")
                        else:
                            if st.session_state.stripe_pago_url:
                                st.link_button("Pagar con Stripe", url=st.session_state.stripe_pago_url, type="primary", use_container_width=True)
                            else:
                                st.error("Stripe no esta disponible.")
                        st.write("---")
                        if st.button("Cancelar reserva y vaciar carrito"):
                            ext_ref = st.session_state.get("external_ref_activa", "")
                            if ext_ref:
                                liberar_reserva_por_rechazo_o_cancelacion(conn, ext_ref, "CANCELADO_USUARIO")
                            limpiar_carrito_local()
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
                            dominio = st.selectbox("Extension:", ["@gmail.com", "@hotmail.com", "@outlook.com", "@yahoo.com", "Otro..."])
                        if dominio == "Otro...":
                            correo = st.text_input("Correo completo:", placeholder="usuario@empresa.com")
                        else:
                            correo = f"{correo_usuario.replace('@', '').strip()}{dominio}" if correo_usuario else ""
                        telefono = st.text_input("WhatsApp (10 digitos):", max_chars=10)
                        st.write(f"**Total a Pagar:** ${total_pagar:.2f} MXN")
                        if st.button("Confirmar y Elegir Metodo de Pago", type="primary", use_container_width=True):
                            pre_reservas = obtener_pre_reservas_globales()
                            ahora = datetime.now()
                            siguen_validos = all(t in pre_reservas and pre_reservas[t]["session_id"] == st.session_state.session_id and pre_reservas[t]["expires_at"] > ahora for t in boletos)
                            correo_valido = re.match(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z]{2,}$", correo.strip().lower())
                            if not siguen_validos:
                                st.error("El tiempo de carrito expiro. Selecciona nuevamente.")
                                st.session_state.selected_tickets = []
                            elif not nombre or not apellidos or not correo or not telefono:
                                st.error("Completa todos los campos.")
                            elif not correo_valido:
                                st.error("El formato del correo NO es valido.")
                            elif not (telefono.isdigit() and len(telefono) == 10):
                                st.error("El numero debe contener 10 digitos numericos.")
                            else:
                                ref = f"RIFA-{datetime.now().strftime('%Y%m%d%H%M%S')}-{random.randint(1000, 9999)}"
                                st.session_state.external_ref_activa = ref
                                ordenes = []
                                for t in boletos:
                                    ordenes.append({
                                        "External_Reference": ref,
                                        "MercadoPago_Preference_ID": "",
                                        "MercadoPago_Payment_ID": "",
                                        "Stripe_Session_ID": "",
                                        "Stripe_Payment_ID": "",
                                        "Numero_Boleto": str(t),
                                        "Nombre": f"{nombre.strip()} {apellidos.strip()}",
                                        "Correo": correo.strip().lower(),
                                        "Numero_Telefonico": telefono,
                                        "Monto": float(PRECIO_BOLETO),
                                        "Estado_Reserva": "PENDIENTE",
                                        "Fecha_Creacion": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                        "Expira_En": (datetime.now() + timedelta(minutes=TIEMPO_RESERVA_MINUTOS)).strftime("%Y-%m-%d %H:%M:%S"),
                                        "Fecha_Actualizacion": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                    })
                                exito, msg = registrar_reserva_cobro(conn, ordenes)
                                if exito:
                                    errores = []
                                    pref_id, init_point = "", ""
                                    try:
                                        pref_id, init_point = crear_preferencia_mercado_pago(nombre, apellidos, correo, telefono, boletos, PRECIO_BOLETO, ref)
                                    except Exception as e:
                                        errores.append(f"Mercado Pago: {e}")
                                    stripe_session_id, stripe_checkout_url = "", ""
                                    try:
                                        stripe_session_id, stripe_checkout_url = crear_sesion_stripe(nombre, apellidos, correo, boletos, PRECIO_BOLETO, ref)
                                    except Exception as e:
                                        errores.append(f"Stripe: {e}")
                                    st.session_state.pago_generado_url = init_point
                                    st.session_state.stripe_session_id = stripe_session_id
                                    st.session_state.stripe_pago_url = stripe_checkout_url
                                    actualizar_ids_proveedores_reserva(conn, ref, pref_id, stripe_session_id)
                                    if not init_point and not stripe_checkout_url:
                                        liberar_reserva_por_rechazo_o_cancelacion(conn, ref, "ERROR_GENERACION_PAGO")
                                        limpiar_carrito_local()
                                        st.error("No fue posible generar enlaces de pago. " + " | ".join(errores))
                                    else:
                                        if errores:
                                            st.warning("Uno de los proveedores no estuvo disponible: " + " | ".join(errores))
                                        st.rerun()
                                else:
                                    st.error(f"Error al registrar la reserva: {msg}")


if __name__ == "__main__":
    main()
