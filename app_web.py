import os
import random
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

import pandas as pd
import requests
import streamlit as st
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from streamlit_gsheets import GSheetsConnection


# -----------------------------
# Configuración Mercado Pago
# -----------------------------

def obtener_config(nombre: str, default: str = "") -> str:
    try:
        if hasattr(st, "secrets") and nombre in st.secrets:
            valor = st.secrets[nombre]
            if valor is not None:
                return str(valor)
    except Exception:
        pass
    return os.getenv(nombre, default)


MP_ACCESS_TOKEN = obtener_config("MP_ACCESS_TOKEN")
MP_NOTIFICATION_URL = obtener_config("MP_NOTIFICATION_URL")
MP_RETURN_URL = obtener_config("MP_RETURN_URL")
MP_CURRENCY_ID = obtener_config("MP_CURRENCY_ID", "MXN")
MP_WEBHOOK_SECRET = obtener_config("MP_WEBHOOK_SECRET")  # opcional, para validar firma si lo implementas fuera de Streamlit


# -----------------------------
# PDF del boleto
# -----------------------------

def dibujar_fondo_autenticidad(canvas, doc):
    """Dibuja un fondo sutil tipo certificado para dar apariencia de autenticidad."""
    width, height = doc.pagesize

    canvas.saveState()

    # Fondo general
    canvas.setFillColor(colors.HexColor("#F8FAFC"))
    canvas.rect(0, 0, width, height, fill=1, stroke=0)

    # Marco exterior minimalista
    canvas.setStrokeColor(colors.HexColor("#CBD5E1"))
    canvas.setLineWidth(1)
    canvas.rect(24, 24, width - 48, height - 48, fill=0, stroke=1)

    # Patrón diagonal sutil
    canvas.setStrokeColor(colors.HexColor("#E2E8F0"))
    canvas.setLineWidth(0.35)
    step = 34
    limite = int(width + height)
    for x in range(-int(height), limite, step):
        canvas.line(x, 24, x + height, height - 24)

    # Sello de autenticidad
    sello_x = width - 78
    sello_y = height - 82
    canvas.setStrokeColor(colors.HexColor("#94A3B8"))
    canvas.circle(sello_x, sello_y, 30, stroke=1, fill=0)
    canvas.circle(sello_x, sello_y, 22, stroke=1, fill=0)
    canvas.setFont("Helvetica-Bold", 6.5)
    canvas.setFillColor(colors.HexColor("#64748B"))
    canvas.drawCentredString(sello_x, sello_y + 4, "BOLETO")
    canvas.drawCentredString(sello_x, sello_y - 4, "AUTÉNTICO")

    # Marca de agua suave
    canvas.saveState()
    canvas.translate(width * 0.55, height * 0.52)
    canvas.rotate(28)
    canvas.setFillColor(colors.HexColor("#E5E7EB"))
    canvas.setFont("Helvetica-Bold", 34)
    canvas.drawCentredString(0, 0, "AUTENTICIDAD")
    canvas.restoreState()

    canvas.restoreState()


def generar_pdf_boleto(datos_boleto: Dict[str, Any]) -> str:
    nombre_archivo = datos_boleto.get("Comprobante", f"Boleto_{datos_boleto['ID_Boleto']}.pdf")
    doc = SimpleDocTemplate(
        nombre_archivo,
        pagesize=letter,
        rightMargin=32,
        leftMargin=32,
        topMargin=40,
        bottomMargin=32,
    )
    story = []

    styles = getSampleStyleSheet()
    estilo_titulo = ParagraphStyle(
        "TituloBoleto",
        parent=styles["Heading1"],
        fontSize=19,
        leading=23,
        textColor=colors.HexColor("#0F172A"),
        alignment=1,
        spaceAfter=8,
    )
    estilo_normal = ParagraphStyle(
        "TextoBoleto",
        parent=styles["Normal"],
        fontSize=10.5,
        leading=13,
        textColor=colors.HexColor("#334155"),
    )
    estilo_pequeno = ParagraphStyle(
        "TextoPequeno",
        parent=styles["Normal"],
        fontSize=8.5,
        leading=10,
        textColor=colors.HexColor("#64748B"),
        alignment=1,
    )

    story.append(Spacer(1, 14))
    story.append(Paragraph("BOLETO OFICIAL", estilo_titulo))
    story.append(Spacer(1, 16))

    data = [
        [Paragraph("<b>ID de Boleto:</b>", estilo_normal), Paragraph(str(datos_boleto["ID_Boleto"]), estilo_normal)],
        [Paragraph("<b>Nombre:</b>", estilo_normal), Paragraph(datos_boleto["Nombre"], estilo_normal)],
        [Paragraph("<b>Correo:</b>", estilo_normal), Paragraph(datos_boleto["Correo"], estilo_normal)],
        [Paragraph("<b>Número telefónico:</b>", estilo_normal), Paragraph(str(datos_boleto["Numero_Telefonico"]), estilo_normal)],
        [Paragraph("<b>Evento:</b>", estilo_normal), Paragraph(datos_boleto["Evento"], estilo_normal)],
        [Paragraph("<b>N° de Boleto:</b>", estilo_normal), Paragraph(str(datos_boleto["Numero_Boleto"]), estilo_normal)],
        [Paragraph("<b>Precio Pagado:</b>", estilo_normal), Paragraph(f"${datos_boleto['Precio']:.2f} MXN", estilo_normal)],
        [Paragraph("<b>Método de Pago:</b>", estilo_normal), Paragraph(str(datos_boleto["Metodo_Pago"]), estilo_normal)],
        [Paragraph("<b>ID de Pago Mercado Pago:</b>", estilo_normal), Paragraph(str(datos_boleto.get("MercadoPago_Payment_ID", "")), estilo_normal)],
        [Paragraph("<b>Fecha de Emisión:</b>", estilo_normal), Paragraph(str(datos_boleto["Fecha_Compra"]), estilo_normal)],
    ]

    t = Table(data, colWidths=[165, 300])
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FFFFFF")),
                ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#CBD5E1")),
                ("INNERGRID", (0, 0), (-1, -1), 0.6, colors.HexColor("#E2E8F0")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.HexColor("#FFFFFF"), colors.HexColor("#F8FAFC")]),
            ]
        )
    )

    story.append(t)
    story.append(Spacer(1, 18))
    story.append(
        Paragraph(
            "<b>Verificación:</b> Este comprobante corresponde a un registro único y oficial de la rifa. Conserva este archivo para cualquier validación posterior.",
            estilo_normal,
        )
    )
    story.append(Spacer(1, 16))
    story.append(Paragraph("Presenta este boleto para participar en la Rifa de celular. ¡Mucha suerte!", estilo_pequeno))

    doc.build(story, onFirstPage=dibujar_fondo_autenticidad, onLaterPages=dibujar_fondo_autenticidad)
    return nombre_archivo


# -----------------------------
# Google Sheets
# -----------------------------

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
    ]


def columnas_reservas() -> list:
    return [
        "External_Reference",
        "MercadoPago_Preference_ID",
        "MercadoPago_Payment_ID",
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


def asegurar_columnas(df: pd.DataFrame, cols: list) -> pd.DataFrame:
    for col in cols:
        if col not in df.columns:
            df[col] = ""
    return df[cols]


def obtener_datos_gsheets() -> Tuple[Optional[GSheetsConnection], Optional[pd.DataFrame], Optional[pd.DataFrame]]:
    """Obtiene ventas y reservas desde Google Sheets con manejo de errores."""
    try:
        conn = st.connection("gsheets", type=GSheetsConnection)

        try:
            df_ventas = conn.read(worksheet="Ventas", ttl=0)
            if df_ventas is None or df_ventas.empty:
                df_ventas = pd.DataFrame(columns=columnas_ventas())
            else:
                df_ventas = df_ventas.dropna(how="all")
                df_ventas = asegurar_columnas(df_ventas, columnas_ventas())
        except Exception:
            df_ventas = pd.DataFrame(columns=columnas_ventas())

        try:
            df_reservas = conn.read(worksheet="Reservas", ttl=0)
            if df_reservas is None or df_reservas.empty:
                df_reservas = pd.DataFrame(columns=columnas_reservas())
            else:
                df_reservas = df_reservas.dropna(how="all")
                df_reservas = asegurar_columnas(df_reservas, columnas_reservas())
        except Exception:
            df_reservas = pd.DataFrame(columns=columnas_reservas())

        return conn, df_ventas, df_reservas
    except Exception as e:
        st.error(f"⚠️ Error al conectar con Google Sheets: {e}")
        return None, None, None


# -----------------------------
# Mercado Pago
# -----------------------------

def mp_headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {MP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }


def crear_preferencia_mercado_pago(*, nombre: str, correo: str, telefono: str, numero_boleto: str, monto: float, external_reference: str) -> Tuple[str, str]:
    if not MP_ACCESS_TOKEN:
        raise RuntimeError("Falta configurar MP_ACCESS_TOKEN.")
    if not MP_RETURN_URL:
        raise RuntimeError("Falta configurar MP_RETURN_URL con una URL pública HTTPS.")

    payload: Dict[str, Any] = {
        "items": [
            {
                "title": f"Rifa de celular - Boleto {numero_boleto}",
                "quantity": 1,
                "unit_price": float(monto),
                "currency_id": MP_CURRENCY_ID,
            }
        ],
        "payer": {
            "name": nombre,
            "email": correo,
        },
        "external_reference": external_reference,
        "back_urls": {
            "success": MP_RETURN_URL,
            "pending": MP_RETURN_URL,
            "failure": MP_RETURN_URL,
        },
        "auto_return": "approved",
        "statement_descriptor": "RIFA CELULAR",
        "metadata": {
            "telefono": telefono,
            "numero_boleto": numero_boleto,
        },
        "binary_mode": True,
    }
    if MP_NOTIFICATION_URL:
        payload["notification_url"] = MP_NOTIFICATION_URL

    respuesta = requests.post(
        "https://api.mercadopago.com/checkout/preferences",
        headers=mp_headers(),
        json=payload,
        timeout=30,
    )
    if respuesta.status_code >= 400:
        raise RuntimeError(f"Mercado Pago devolvió {respuesta.status_code}: {respuesta.text}")

    data = respuesta.json()
    preference_id = data.get("id", "")
    init_point = data.get("init_point") or data.get("sandbox_init_point") or ""
    if not preference_id or not init_point:
        raise RuntimeError("Mercado Pago no devolvió la preference o el enlace de pago.")
    return preference_id, init_point


def obtener_pago_mercado_pago(payment_id: str) -> Dict[str, Any]:
    if not MP_ACCESS_TOKEN:
        raise RuntimeError("Falta configurar MP_ACCESS_TOKEN.")

    respuesta = requests.get(
        f"https://api.mercadopago.com/v1/payments/{payment_id}",
        headers=mp_headers(),
        timeout=30,
    )
    if respuesta.status_code >= 400:
        raise RuntimeError(f"No se pudo consultar el pago {payment_id}: {respuesta.status_code} {respuesta.text}")
    return respuesta.json()


# -----------------------------
# Reservas y registro final
# -----------------------------

def parse_datetime(valor: Any) -> Optional[datetime]:
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return None
    texto = str(valor).strip()
    if not texto:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(texto, fmt)
        except Exception:
            pass
    try:
        return pd.to_datetime(texto).to_pydatetime()
    except Exception:
        return None


def reserva_activa(row: pd.Series) -> bool:
    estado = str(row.get("Estado_Reserva", "")).strip().upper()
    if estado != "PENDIENTE":
        return False
    expira = parse_datetime(row.get("Expira_En"))
    if expira is None:
        return True
    return datetime.now() <= expira


def obtener_boletos_bloqueados(df_ventas: pd.DataFrame, df_reservas: pd.DataFrame) -> set:
    vendidos = set()
    if not df_ventas.empty and "Numero_Boleto" in df_ventas.columns:
        if "Estado_Pago" in df_ventas.columns:
            df_aprobadas = df_ventas[df_ventas["Estado_Pago"].astype(str).str.upper() == "APROBADO"]
        else:
            df_aprobadas = df_ventas
        for x in df_aprobadas["Numero_Boleto"].dropna().values:
            try:
                vendidos.add(f"{int(x):03d}")
            except Exception:
                vendidos.add(str(x).strip())

    reservados = set()
    if not df_reservas.empty and "Numero_Boleto" in df_reservas.columns:
        for _, row in df_reservas.iterrows():
            if reserva_activa(row):
                try:
                    reservados.add(f"{int(row['Numero_Boleto']):03d}")
                except Exception:
                    reservados.add(str(row["Numero_Boleto"]).strip())

    return vendidos.union(reservados)


def registrar_reserva_cobro(conn: GSheetsConnection, orden: Dict[str, Any]) -> bool:
    try:
        try:
            df_reservas = conn.read(worksheet="Reservas", ttl=0)
            if df_reservas is None or df_reservas.empty:
                df_reservas = pd.DataFrame(columns=columnas_reservas())
        except Exception:
            df_reservas = pd.DataFrame(columns=columnas_reservas())

        df_reservas = asegurar_columnas(df_reservas.dropna(how="all"), columnas_reservas()) if not df_reservas.empty else pd.DataFrame(columns=columnas_reservas())
        df_nueva = pd.DataFrame([orden])
        df_nueva = asegurar_columnas(df_nueva, columnas_reservas())
        df_actualizado = pd.concat([df_reservas, df_nueva], ignore_index=True)
        conn.update(worksheet="Reservas", data=df_actualizado)
        return True
    except Exception as e:
        st.error(f"⚠️ No se pudo registrar la reserva de cobro: {e}")
        return False


def actualizar_reserva(conn: GSheetsConnection, external_reference: str, updates: Dict[str, Any]) -> bool:
    try:
        df_reservas = conn.read(worksheet="Reservas", ttl=0)
        if df_reservas is None or df_reservas.empty:
            return False
        df_reservas = asegurar_columnas(df_reservas.dropna(how="all"), columnas_reservas())
        mask = df_reservas["External_Reference"].astype(str).str.strip() == str(external_reference).strip()
        if not mask.any():
            return False
        for key, value in updates.items():
            if key in df_reservas.columns:
                df_reservas.loc[mask, key] = value
        conn.update(worksheet="Reservas", data=df_reservas)
        return True
    except Exception:
        return False


def buscar_venta_por_pago(df_ventas: pd.DataFrame, payment_id: str) -> bool:
    if df_ventas.empty or "MercadoPago_Payment_ID" not in df_ventas.columns:
        return False
    return df_ventas["MercadoPago_Payment_ID"].astype(str).str.strip().eq(str(payment_id).strip()).any()


def registrar_venta_aprobada(
    conn: GSheetsConnection,
    df_ventas: pd.DataFrame,
    datos_nuevo_boleto: Dict[str, Any],
) -> pd.DataFrame:
    columnas_estandar = columnas_ventas()
    df_nuevo_registro = pd.DataFrame([datos_nuevo_boleto])
    df_nuevo_registro = asegurar_columnas(df_nuevo_registro, columnas_estandar)

    if df_ventas.empty:
        df_actualizado = df_nuevo_registro[columnas_estandar]
    else:
        df_ventas = asegurar_columnas(df_ventas, columnas_estandar)
        df_actualizado = pd.concat([df_ventas, df_nuevo_registro[columnas_estandar]], ignore_index=True)

    conn.update(worksheet="Ventas", data=df_actualizado)
    return df_actualizado


# -----------------------------
# UI / Proceso principal
# -----------------------------

def obtener_query_params() -> Dict[str, str]:
    try:
        qp = st.query_params
        try:
            return {k: (v[0] if isinstance(v, list) else v) for k, v in qp.to_dict().items()}
        except Exception:
            return {k: (v[0] if isinstance(v, list) else v) for k, v in dict(qp).items()}
    except Exception:
        try:
            qp = st.experimental_get_query_params()
            return {k: v[0] if isinstance(v, list) and v else v for k, v in qp.items()}
        except Exception:
            return {}


def limpiar_query_params():
    try:
        st.query_params.clear()
    except Exception:
        try:
            st.experimental_set_query_params()
        except Exception:
            pass


def confirmar_pago_si_corresponde(conn, df_ventas, df_reservas):
    qp = obtener_query_params()
    payment_id = qp.get("payment_id") or qp.get("collection_id") or qp.get("id")
    status = (qp.get("status") or qp.get("collection_status") or "").strip().lower()
    external_reference = qp.get("external_reference") or qp.get("preference_id") or ""

    if not payment_id:
        return df_ventas, df_reservas

    try:
        pago = obtener_pago_mercado_pago(str(payment_id))
    except Exception as e:
        st.warning(f"No pude verificar el pago con Mercado Pago: {e}")
        return df_ventas, df_reservas

    mp_status = str(pago.get("status", "")).strip().lower()
    mp_external_reference = str(pago.get("external_reference", external_reference)).strip()
    preference_id = str(pago.get("order", {}).get("id", "") or pago.get("preference_id", "") or "")
    transaction_amount = float(pago.get("transaction_amount") or 0)
    payment_ref = str(payment_id)

    pending_match = None
    if not df_reservas.empty and "External_Reference" in df_reservas.columns:
        mask = df_reservas["External_Reference"].astype(str).str.strip() == mp_external_reference
        if mask.any():
            pending_match = df_reservas[mask].iloc[0].to_dict()

    if mp_status == "approved" and pending_match:
        if not buscar_venta_por_pago(df_ventas, payment_ref):
            datos_nuevo_boleto = {
                "ID_Boleto": f"RIFA-{int(datetime.now().timestamp())}",
                "Nombre": pending_match.get("Nombre", ""),
                "Correo": pending_match.get("Correo", ""),
                "Evento": "Rifa de celular",
                "Numero_Boleto": str(pending_match.get("Numero_Boleto", "")),
                "Precio": float(transaction_amount or pending_match.get("Monto", 0) or 0),
                "Metodo_Pago": "Mercado Pago",
                "Codigo_Pago": f"MP-{random.randint(10000, 99999)}",
                "Fecha_Compra": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "Numero_Telefonico": pending_match.get("Numero_Telefonico", ""),
                "Estado_Pago": "APROBADO",
                "Referencia_Pago": mp_external_reference or payment_ref,
                "MercadoPago_Payment_ID": payment_ref,
                "MercadoPago_Preference_ID": preference_id or pending_match.get("MercadoPago_Preference_ID", ""),
                "Comprobante": f"Boleto_RIFA-{int(datetime.now().timestamp())}.pdf",
            }
            df_ventas = registrar_venta_aprobada(conn, df_ventas, datos_nuevo_boleto)
            actualizar_reserva(
                conn,
                mp_external_reference,
                {
                    "MercadoPago_Payment_ID": payment_ref,
                    "Estado_Reserva": "APROBADA",
                    "Fecha_Actualizacion": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                },
            )

            archivo_pdf = generar_pdf_boleto(datos_nuevo_boleto)
            with open(archivo_pdf, "rb") as pdf_file:
                st.session_state.generated_pdf_bytes = pdf_file.read()
            st.session_state.generated_pdf_name = archivo_pdf
            st.session_state.success_message = f"✅ Pago aprobado por Mercado Pago y boleto generado. ID de pago: **{payment_ref}**"
            st.session_state.pending_mp_reference = None
            st.session_state.selected_ticket = None
            st.session_state.form_id += 1
            limpiar_query_params()
            st.rerun()

    if mp_status in {"rejected", "cancelled", "refunded"}:
        st.warning(f"El pago fue recibido con estado '{mp_status}'. No se registró el boleto.")
        if mp_external_reference:
            actualizar_reserva(
                conn,
                mp_external_reference,
                {
                    "Estado_Reserva": "CANCELADA",
                    "Fecha_Actualizacion": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                },
            )
    elif status and mp_status != "approved":
        st.info(f"Mercado Pago devolvió el estado '{status}'. El registro queda pendiente hasta que el pago se confirme.")

    return df_ventas, df_reservas


def main():
    st.set_page_config(page_title="Rifa de Celular", page_icon="📱", layout="wide")
    st.title("📱 Sistema de Registro y Venta - Rifa de Celular")

    conn, df_ventas, df_reservas = obtener_datos_gsheets()
    if conn is None or df_ventas is None or df_reservas is None:
        return

    if "form_id" not in st.session_state:
        st.session_state.form_id = 0
    if "selected_ticket" not in st.session_state:
        st.session_state.selected_ticket = None
    if "generated_pdf_bytes" not in st.session_state:
        st.session_state.generated_pdf_bytes = None
    if "generated_pdf_name" not in st.session_state:
        st.session_state.generated_pdf_name = None
    if "success_message" not in st.session_state:
        st.session_state.success_message = None
    if "pending_mp_reference" not in st.session_state:
        st.session_state.pending_mp_reference = None
    if "pending_mp_checkout_url" not in st.session_state:
        st.session_state.pending_mp_checkout_url = None

    # Si Mercado Pago regresó con la confirmación, registramos automáticamente.
    df_ventas, df_reservas = confirmar_pago_si_corresponde(conn, df_ventas, df_reservas)

    precio_base = 100.00
    boletos_bloqueados = obtener_boletos_bloqueados(df_ventas, df_reservas)

    matriz_boletos = []
    for i in range(100):
        num_str = f"{i:03d}"
        esta_vendido = num_str in boletos_bloqueados
        estado_label = f"{num_str} ❌" if esta_vendido else f"{num_str} ✅"
        matriz_boletos.append(estado_label)

    columnas_unicas = [f"{' ' * i}" for i in range(10)]
    filas_grid = [matriz_boletos[i:i + 10] for i in range(0, 100, 10)]
    df_grid = pd.DataFrame(filas_grid, columns=columnas_unicas)

    col1, col2 = st.columns([1.2, 1])

    with col1:
        st.markdown("### Disponibilidad")
        st.write("Estado actual de todos los 100 boletos de la rifa:")
        st.dataframe(df_grid, use_container_width=True, height=450, hide_index=True)
        st.caption("✅ = Disponible | ❌ = Vendido o reservado")

    with col2:
        st.markdown("### 🛒 Registro de Compra")

        if st.session_state.success_message:
            st.success(st.session_state.success_message)
            st.download_button(
                label="📥 Descargar Comprobante PDF del Boleto",
                data=st.session_state.generated_pdf_bytes,
                file_name=st.session_state.generated_pdf_name,
                mime="application/pdf",
                key="btn_descarga_persistente",
            )
            if st.button("✖️ Ocultar aviso y continuar"):
                st.session_state.generated_pdf_bytes = None
                st.session_state.generated_pdf_name = None
                st.session_state.success_message = None
                st.rerun()
            st.markdown("---")

        boletos_libres_count = 100 - len(boletos_bloqueados)
        if boletos_libres_count <= 0:
            st.warning("⚠️ ¡Lo sentimos! Todos los boletos de la rifa han sido vendidos o reservados.")
            return

        nombre = st.text_input("Nombre completo:", key=f"nombre_{st.session_state.form_id}")
        correo = st.text_input("Correo electrónico:", key=f"correo_{st.session_state.form_id}")
        telefono = st.text_input("Número telefónico:", key=f"telefono_{st.session_state.form_id}")
        evento = st.text_input("Evento:", value="Rifa de celular", disabled=True, key=f"evento_{st.session_state.form_id}")

        selected_ticket = st.session_state.selected_ticket

        with st.expander("🎫 Desplegar tabla para seleccionar número de boleto", expanded=(selected_ticket is None)):
            st.markdown("Los boletos no disponibles aparecen en color más claro y no se pueden seleccionar:")
            for fila in range(10):
                cols_btn = st.columns(10)
                for col_idx in range(10):
                    num = fila * 10 + col_idx
                    num_str = f"{num:03d}"
                    bloqueado = num_str in boletos_bloqueados

                    with cols_btn[col_idx]:
                        if bloqueado:
                            st.button(f"{num_str}", key=f"sel_{num_str}", disabled=True, use_container_width=True)
                        else:
                            is_current = selected_ticket == num_str
                            label = f"[{num_str}]" if is_current else f"{num_str}"
                            if st.button(label, key=f"sel_{num_str}", use_container_width=True):
                                st.session_state.selected_ticket = num_str
                                st.rerun()

        if selected_ticket:
            st.success(f"🎯 Boleto seleccionado: **N° {selected_ticket}**")
        else:
            st.info("👆 Haz clic en el recuadro superior para desplegar y seleccionar tu número.")

        st.write(f"**Monto a pagar:** ${precio_base:.2f} MXN")
        st.caption("El boleto se registrará y el PDF se desbloqueará únicamente después de que Mercado Pago devuelva el pago como APROBADO.")

        submit_compra = st.button("💳 Pagar con Mercado Pago y generar boleto")

        if submit_compra:
            if not selected_ticket:
                st.error("⚠️ Debes seleccionar un número de boleto disponible.")
            elif not nombre or not correo or not telefono:
                st.error("⚠️ Por favor completa tu nombre, correo y número telefónico antes de continuar.")
            elif not MP_ACCESS_TOKEN or not MP_RETURN_URL:
                st.error("⚠️ Falta configurar Mercado Pago. Define MP_ACCESS_TOKEN y MP_RETURN_URL en secrets o variables de entorno.")
            else:
                external_reference = f"RIFA-{datetime.now().strftime('%Y%m%d%H%M%S')}-{selected_ticket}"
                fecha_creacion = datetime.now()
                expira = fecha_creacion + timedelta(minutes=20)

                reserva = {
                    "External_Reference": external_reference,
                    "MercadoPago_Preference_ID": "",
                    "MercadoPago_Payment_ID": "",
                    "Numero_Boleto": str(selected_ticket),
                    "Nombre": nombre,
                    "Correo": correo,
                    "Numero_Telefonico": telefono,
                    "Monto": float(precio_base),
                    "Estado_Reserva": "PENDIENTE",
                    "Fecha_Creacion": fecha_creacion.strftime("%Y-%m-%d %H:%M:%S"),
                    "Expira_En": expira.strftime("%Y-%m-%d %H:%M:%S"),
                    "Fecha_Actualizacion": fecha_creacion.strftime("%Y-%m-%d %H:%M:%S"),
                }

                if registrar_reserva_cobro(conn, reserva):
                    try:
                        preference_id, checkout_url = crear_preferencia_mercado_pago(
                            nombre=nombre,
                            correo=correo,
                            telefono=telefono,
                            numero_boleto=str(selected_ticket),
                            monto=precio_base,
                            external_reference=external_reference,
                        )
                        reserva["MercadoPago_Preference_ID"] = preference_id
                        actualizar_reserva(
                            conn,
                            external_reference,
                            {
                                "MercadoPago_Preference_ID": preference_id,
                                "Fecha_Actualizacion": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            },
                        )
                        st.session_state.pending_mp_reference = external_reference
                        st.session_state.pending_mp_checkout_url = checkout_url
                        st.success("✅ Se creó la orden de cobro en Mercado Pago. Completa el pago para que el boleto se registre automáticamente.")
                        st.link_button("Ir a Mercado Pago", checkout_url)
                        st.info("Cuando Mercado Pago te regrese a esta misma app, el pago se verificará y el boleto se generará automáticamente si el estado es APROBADO.")
                    except Exception as e:
                        st.error(f"⚠️ No se pudo crear la preferencia de Mercado Pago: {e}")
                else:
                    st.error("⚠️ No fue posible crear la reserva del boleto.")

        if st.session_state.pending_mp_reference:
            st.caption(f"Referencia pendiente: {st.session_state.pending_mp_reference}")


if __name__ == "__main__":
    main()
