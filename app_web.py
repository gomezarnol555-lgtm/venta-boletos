import random
from datetime import datetime
import pandas as pd
import streamlit as st
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from streamlit_gsheets import GSheetsConnection

def generar_pdf_boleto(datos_boleto):
    nombre_archivo = f"Boleto_{datos_boleto['ID_Boleto']}.pdf"
    doc = SimpleDocTemplate(nombre_archivo, pagesize=letter)
    story = []
    
    styles = getSampleStyleSheet()
    estilo_titulo = ParagraphStyle(
        'TituloBoleto', parent=styles['Heading1'], fontSize=20,
        textColor=colors.HexColor("#1A365D"), alignment=1
    )
    estilo_normal = ParagraphStyle(
        'TextoBoleto', parent=styles['Normal'], fontSize=12,
        textColor=colors.HexColor("#2D3748")
    )
    
    story.append(Paragraph("COMPROBANTE OFICIAL DE BOLETO", estilo_titulo))
    story.append(Spacer(1, 20))
    
    data = [
        [Paragraph("<b>ID de Boleto:</b>", estilo_normal), Paragraph(str(datos_boleto['ID_Boleto']), estilo_normal)],
        [Paragraph("<b>Asistente:</b>", estilo_normal), Paragraph(datos_boleto['Nombre'], estilo_normal)],
        [Paragraph("<b>Correo:</b>", estilo_normal), Paragraph(datos_boleto['Correo'], estilo_normal)],
        [Paragraph("<b>Evento:</b>", estilo_normal), Paragraph(datos_boleto['Evento'], estilo_normal)],
        [Paragraph("<b>N° de Boleto:</b>", estilo_normal), Paragraph(str(datos_boleto['Numero_Boleto']), estilo_normal)],
        [Paragraph("<b>Precio Pagado:</b>", estilo_normal), Paragraph(f"${datos_boleto['Precio']:.2f} MXN", estilo_normal)],
        [Paragraph("<b>Código de Pago:</b>", estilo_normal), Paragraph(datos_boleto['Codigo_Pago'], estilo_normal)],
        [Paragraph("<b>Fecha de Emisión:</b>", estilo_normal), Paragraph(str(datos_boleto['Fecha_Compra']), estilo_normal)]
    ]
    
    t = Table(data, colWidths=[150, 300])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#F7FAFC")),
        ('GRID', (0,0), (-1,-1), 1, colors.HexColor("#E2E8F0")),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('PADDING', (0,0), (-1,-1), 10),
    ]))
    
    story.append(t)
    story.append(Spacer(1, 30))
    story.append(Paragraph("<i>Presenta este comprobante para participar en la Rifa de celular. ¡Mucha suerte!</i>", ParagraphStyle('Footer', parent=estilo_normal, fontSize=10, alignment=1, textColor=colors.HexColor("#718096"))))
    
    doc.build(story)
    return nombre_archivo

def obtener_datos_gsheets():
    """Obtiene el inventario y ventas desde Google Sheets con manejo de errores detallado."""
    try:
        conn = st.connection("gsheets", type=GSheetsConnection)
        df_ventas = conn.read(worksheet="Ventas", ttl=0)
        
        if df_ventas is not None and not df_ventas.empty:
            df_ventas = df_ventas.dropna(how="all")
        else:
            df_ventas = pd.DataFrame(columns=[
                "ID_Boleto", "Nombre", "Correo", "Evento", 
                "Numero_Boleto", "Precio", "Codigo_Pago", "Fecha_Compra"
            ])
            
        return conn, df_ventas
    except Exception as e:
        st.error(f"⚠️ Error detallado de conexión con Google Sheets: {e}")
        return None, None

def main():
    st.set_page_config(page_title="Rifa de Celular", page_icon="📱", layout="wide")
    st.title("📱 Sistema de Registro y Venta - Rifa de Celular")
    
    conn, df_ventas = obtener_datos_gsheets()
    if conn is None or df_ventas is None:
        return

    # --- CONFIGURACIÓN DE 100 BOLETOS (000 al 099) ---
    precio_base = 100.00 # Puedes cambiar el precio del boleto aquí si lo deseas
    
    lista_inventario = []
    boletos_vendidos = []
    if not df_ventas.empty and "Numero_Boleto" in df_ventas.columns:
        # Aseguramos limpiar y leer los números de boletos ya comprados
        boletos_vendidos = df_ventas["Numero_Boleto"].dropna().astype(str).values

    for i in range(100):
        num_str = f"{i:03d}" # Formato de 3 dígitos (000, 001, ..., 099)
        estado = "Vendido ❌" if num_str in boletos_vendidos else "Disponible ✅"
        lista_inventario.append({
            "Boleto N°": num_str,
            "Precio": f"${precio_base:.2f} MXN",
            "Estado": estado
        })
    
    df_inventario = pd.DataFrame(lista_inventario)

    col1, col2 = st.columns([1.2, 1])

    with col1:
        st.subheader("📋 Tabla de Disponibilidad (000 al 099)")
        st.dataframe(df_inventario, use_container_width=True, height=400, hide_index=True)
        st.info("💡 Los boletos marcados como 'Vendido ❌' ya están ocupados. Elige uno disponible.")

    with col2:
        st.subheader("🛒 Registro de Compra")
        
        boletos_libres = [item["Boleto N°"] for item in lista_inventario if item["Estado"] == "Disponible ✅"]
        
        if not boletos_libres:
            st.warning("⚠️ ¡Lo sentimos! Todos los boletos de la rifa están agotados.")
            return

        with st.form("form_compra", clear_on_submit=True):
            nombre = st.text_input("Nombre completo:")
            correo = st.text_input("Correo electrónico:")
            evento = st.text_input("Nombre del evento:", value="Rifa de celular")
            boleto_seleccionado = st.selectbox("Selecciona tu número de boleto disponible:", boletos_libres)
            
            st.markdown("---")
            st.write(f"**Monto a pagar:** ${precio_base:.2f} MXN")
            metodo_pago = st.radio("Método de Pago:", ["Transferencia SPEI", "Efectivo / Enlace directo"])
            
            pago_realizado = st.checkbox("Confirmo que el pago ha sido efectuado correctamente.")
            
            submit_compra = st.form_submit_button("💳 Registrar Compra y Generar Boleto")

        if submit_compra:
            if not nombre or not correo or not evento:
                st.error("⚠️ Por favor completa todos los campos del formulario.")
            elif not pago_realizado:
                st.warning("⚠️ Debes confirmar que el pago ha sido efectuado para poder registrar el boleto.")
            else:
                with st.spinner('Validando pago y registrando en la nube...'):
                    id_boleto = f"RIFA-{int(datetime.now().timestamp())}"
                    codigo_pago = f"PAY-{random.randint(10000, 99999)}"
                    fecha_compra = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    
                    datos_nuevo_boleto = {
                        "ID_Boleto": id_boleto,
                        "Nombre": nombre,
                        "Correo": correo,
                        "Evento": evento,
                        "Numero_Boleto": str(boleto_seleccionado),
                        "Precio": float(precio_base),
                        "Codigo_Pago": codigo_pago,
                        "Fecha_Compra": fecha_compra
                    }
                    
                    # Guardar permanentemente en Google Sheets
                    df_actualizado = pd.concat([df_ventas, pd.DataFrame([datos_nuevo_boleto])], ignore_index=True)
                    conn.update(worksheet="Ventas", data=df_actualizado)
                    
                    # Generar PDF
                    archivo_pdf = generar_pdf_boleto(datos_nuevo_boleto)

                st.success("✅ ¡Boleto registrado con éxito en Google Sheets!")
                st.info(f"Tu código de confirmación es: **{codigo_pago}**")
                
                with open(archivo_pdf, "rb") as pdf_file:
                    pdf_bytes = pdf_file.read()
                    
                st.download_button(
                    label="📥 Descargar mi Comprobante en PDF",
                    data=pdf_bytes,
                    file_name=archivo_pdf,
                    mime="application/pdf",
                    key="btn_descarga"
                )
                
                st.success("🔄 *Los campos del formulario se han reiniciado. Ya puedes registrar otro boleto si lo deseas.*")
                st.rerun()

if __name__ == "__main__":
    main()
