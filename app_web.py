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
    """Obtiene el inventario y ventas desde Google Sheets con manejo de errores."""
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
        st.error(f"⚠️ Error al conectar con Google Sheets: {e}")
        return None, None

def main():
    st.set_page_config(page_title="Rifa de Celular", page_icon="📱", layout="wide")
    st.title("📱 Sistema de Registro y Venta - Rifa de Celular")
    
    conn, df_ventas = obtener_datos_gsheets()
    if conn is None or df_ventas is None:
        return

    # Preservar el estado de los inputs si hay un intento fallido
    if "input_nombre" not in st.session_state:
        st.session_state.input_nombre = ""
    if "input_correo" not in st.session_state:
        st.session_state.input_correo = ""

    # --- CONFIGURACIÓN DE 100 BOLETOS (000 al 099) ---
    precio_base = 100.00 
    
    boletos_vendidos = []
    if not df_ventas.empty and "Numero_Boleto" in df_ventas.columns:
        boletos_vendidos = [f"{int(x):03d}" for x in df_ventas["Numero_Boleto"].dropna().values]

    # Construir mapa de disponibilidad
    matriz_boletos = []
    boletos_libres = []
    
    for i in range(100):
        num_str = f"{i:03d}"
        esta_vendido = num_str in boletos_vendidos
        if not esta_vendido:
            boletos_libres.append(num_str)
        
        # Etiqueta visual para la tabla
        estado_label = f"{num_str} ❌" if esta_vendido else f"{num_str} ✅"
        matriz_boletos.append(estado_label)

    # Crear formato de tabla de 10 columnas por 10 filas para visualización panorámica
    columnas_grid = [f"Col {i+1}" for i in range(10)]
    filas_grid = [matriz_boletos[i:i+10] for i in range(0, 100, 10)]
    df_grid = pd.DataFrame(filas_grid, columns=columnas_grid)

    col1, col2 = st.columns([1.3, 1])

    with col1:
        st.subheader("📋 Panel Visual de Boletos (000 al 099)")
        st.write("Observa el estado de todos los números disponibles (✅) y vendidos (❌):")
        st.dataframe(df_grid, use_container_width=True, hide_index=True)
        st.caption("✅ = Disponible | ❌ = No disponible / Vendido")

    with col2:
        st.subheader("🛒 Registro de Compra")
        
        if not boletos_libres:
            st.warning("⚠️ ¡Lo sentimos! Todos los boletos de la rifa han sido vendidos.")
            return

        # Formulario interactivo conservando la información
        nombre = st.text_input("Nombre completo:", value=st.session_state.input_nombre)
        correo = st.text_input("Correo electrónico:", value=st.session_state.input_correo)
        evento = st.text_input("Evento:", value="Rifa de celular", disabled=True)
        
        # Selección del número disponible en la lista desplegable
        boleto_seleccionado = st.selectbox(
            "Selecciona tu número de boleto disponible:", 
            options=boletos_libres
        )
        
        st.markdown("---")
        st.write(f"**Monto a pagar:** ${precio_base:.2f} MXN")
        
        # Opciones ajustadas a Transferencia y Tarjeta
        metodo_pago = st.radio("Método de Pago:", ["Transferencia", "Tarjeta"])
        
        pago_realizado = st.checkbox("Confirmo que el pago ha sido efectuado correctamente.")
        
        submit_compra = st.button("💳 Registrar Compra y Generar Boleto")

        # Guardar cambios inmediatamente en el estado
        st.session_state.input_nombre = nombre
        st.session_state.input_correo = correo

        if submit_compra:
            if not nombre or not correo:
                st.error("⚠️ Por favor completa tu nombre y correo antes de continuar. Tus datos no se perderán.")
            elif not pago_realizado:
                st.warning("⚠️ Debes marcar la casilla para confirmar que el pago fue realizado.")
            else:
                with st.spinner('Procesando pago y actualizando inventario...'):
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
                    
                    # Actualizar hoja de cálculo en la nube
                    df_actualizado = pd.concat([df_ventas, pd.DataFrame([datos_nuevo_boleto])], ignore_index=True)
                    conn.update(worksheet="Ventas", data=df_actualizado)
                    
                    # Generar comprobante PDF
                    archivo_pdf = generar_pdf_boleto(datos_nuevo_boleto)

                st.success("✅ ¡Registro completado exitosamente!")
                st.info(f"Tu código de pago asignado: **{codigo_pago}**")
                
                with open(archivo_pdf, "rb") as pdf_file:
                    pdf_bytes = pdf_file.read()
                    
                st.download_button(
                    label="📥 Descargar Comprobante PDF",
                    data=pdf_bytes,
                    file_name=archivo_pdf,
                    mime="application/pdf",
                    key="btn_descarga"
                )
                
                # Reseteo de campos tras completar la transacción exitosamente
                st.session_state.input_nombre = ""
                st.session_state.input_correo = ""
                
                st.info("🔄 Haz clic abajo para actualizar la vista y ver tu número deshabilitado en la tabla.")
                if st.button("🔄 Actualizar disponibilidad"):
                    st.rerun()

if __name__ == "__main__":
    main()
