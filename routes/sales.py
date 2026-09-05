from flask import Blueprint, request, jsonify, flash, redirect, render_template, abort, url_for
from flask_login import login_required, current_user
from models import db, Product, ProductVariant, Sale, SaleDetail, SalePayment, Expense, obtener_hora_bogota, ArqueoCaja, PriceApproval
from decorators import admin_required
from decimal import Decimal
from datetime import datetime, timedelta
from sqlalchemy import or_
from sqlalchemy.orm import joinedload
import unicodedata
import re

def normalizar_texto(texto):
    """Normaliza texto eliminando acentos, tildes y diacríticos y convirtiendo a minúsculas."""
    if not texto:
        return ''
    return ''.join(
        c for c in unicodedata.normalize('NFD', str(texto))
        if unicodedata.category(c) != 'Mn'
    ).lower()

def comodin_vocales(texto):
    """Reemplaza vocales (con o sin tilde) por el comodín SQL '_' para coincidencia flexible en BD."""
    return re.sub(r'[aeiouáéíóúüÁÉÍÓÚÜ]', '_', str(texto))

sales_bp = Blueprint('sales_bp', __name__)

@sales_bp.route('/nueva', methods=['GET', 'POST'])
@login_required # Importante: Te bloqueará el acceso si no hay current_user logeado (Flask-Login)
def procesar_venta():
    if request.method == 'GET':
        return render_template('sales/nueva.html')

    """
    Se espera que los datos vengan en el cuerpo de la petición (JSON)
    Ej: {'items': [{ 'product_id': 1, 'cantidad': 2, 'precio_final': 15.50}, ...], 'metodo_pago': 'transferencia'}
    """
    data = request.get_json()
    items = data.get('items', [])
    pagos_data = data.get('pagos', [])  # Nuevo: array de pagos mixtos
    metodo_pago_legacy = data.get('metodo_pago', 'efectivo')  # Retrocompatibilidad
    
    if not items:
        return jsonify({'error': 'No se enviaron productos para la venta'}), 400

    # Si no se envían pagos en el nuevo formato, crear uno único con el método legacy
    if not pagos_data:
        pagos_data = [{'metodo_pago': metodo_pago_legacy, 'monto': None}]  # monto=None se llenará con el total

    try:
        # Determinar el método de pago principal (para la columna legacy de retrocompatibilidad)
        if len(pagos_data) == 1:
            metodo_pago_principal = pagos_data[0].get('metodo_pago', 'efectivo')
        else:
            metodo_pago_principal = 'mixto'

        # Manejar Fecha de Venta para registros de fechas anteriores
        fecha_venta_str = data.get('fecha_venta')
        fecha_venta_obj = obtener_hora_bogota()
        if fecha_venta_str:
            try:
                fecha_seleccionada = datetime.strptime(fecha_venta_str, '%Y-%m-%d').date()
                if fecha_seleccionada != fecha_venta_obj.date():
                    # Si no es hoy, combinamos la fecha seleccionada con la hora actual para conservar secuencialidad de hora de registro
                    fecha_venta_obj = datetime.combine(fecha_seleccionada, fecha_venta_obj.time())
            except ValueError:
                pass # Fallback silencioso a la hora actual si el formato falla

        # Validar si la caja para la jornada seleccionada ya fue cerrada
        caja_cerrada = ArqueoCaja.query.filter_by(fecha_arqueo=fecha_venta_obj.date()).first()
        if caja_cerrada:
            return jsonify({
                'error': f'La caja del día {fecha_venta_obj.date().strftime("%Y-%m-%d")} ya se encuentra cerrada. No es posible registrar nuevas ventas.'
            }), 400

        nueva_venta = Sale(
            vendedor_id=current_user.id,
            monto_total=Decimal('0.00'),
            metodo_pago=metodo_pago_principal,
            fecha_venta=fecha_venta_obj
        )
        db.session.add(nueva_venta)
        db.session.flush()

        monto_total = Decimal('0.00')

        for item in items:
            product_id = item.get('product_id')
            variant_id = item.get('variant_id') # Posible variante
            cantidad_vendida = int(item.get('cantidad', 0))
            precio_venta_final = Decimal(str(item.get('precio_final', '0.00')))
            es_manual = item.get('es_manual', False)
            es_obsequio = item.get('es_obsequio', False)

            if cantidad_vendida <= 0:
                raise ValueError("La cantidad vendida debe ser mayor a 0.")

            if es_manual:
                # Producto manual (prestado de otro local) — no descuenta stock
                nombre_manual = item.get('nombre_manual', 'Producto Externo')
                precio_costo_manual = Decimal(str(item.get('precio_costo', '0.00')))

                detalle = SaleDetail(
                    sale_id=nueva_venta.id,
                    product_id=None,
                    variant_id=None,
                    cantidad_vendida=cantidad_vendida,
                    precio_venta_final=precio_venta_final,
                    nombre_manual=nombre_manual,
                    precio_costo_manual=precio_costo_manual
                )
                db.session.add(detalle)
                monto_total += (precio_venta_final * cantidad_vendida)

                # Crear el gasto automático para descontar el ingreso prestado del balance final
                if precio_costo_manual > 0:
                    gasto_externo = Expense(
                        usuario_id=current_user.id,
                        tipo_gasto='Gasto Diario',
                        categoria='Pago Prod. Externo',
                        descripcion=f"Pago por producto manual prestado: {nombre_manual}",
                        monto=(precio_costo_manual * cantidad_vendida),
                        fecha_gasto=fecha_venta_obj
                    )
                    db.session.add(gasto_externo)
            else:
                # Producto del inventario propio
                producto = Product.query.with_for_update().get(product_id)
                
                if not producto:
                    raise ValueError(f"El producto con ID {product_id} no existe.")

                if variant_id:
                    variante = ProductVariant.query.with_for_update().get(variant_id)
                    if not variante:
                        raise ValueError(f"La variante con ID {variant_id} no existe.")
                    if cantidad_vendida > variante.cantidad_stock:
                        raise ValueError(f"Stock insuficiente para la variante '{variante.nombre_variante}' de '{producto.nombre}'. Solicitado: {cantidad_vendida}, Disponible: {variante.cantidad_stock}.")
                    
                    stock_anterior = variante.cantidad_stock
                    variante.cantidad_stock -= cantidad_vendida
                    producto.cantidad_stock -= cantidad_vendida # Sincronizar producto base
                    precio_limite_autorizado = variante.precio_costo if current_user.rol == 'admin' else variante.precio_minimo
                    
                    from models import StockAdjustment
                    ajuste = StockAdjustment(
                        product_id=producto.id,
                        admin_id=current_user.id,
                        tipo_movimiento=f"Venta Tienda (Subcat: {variante.nombre_variante})",
                        stock_anterior=stock_anterior,
                        stock_nuevo=variante.cantidad_stock
                    )
                    db.session.add(ajuste)
                else:
                    if cantidad_vendida > producto.cantidad_stock:
                        raise ValueError(f"Stock insuficiente para el producto '{producto.nombre}'. Solicitado: {cantidad_vendida}, Disponible: {producto.cantidad_stock}.")
                    
                    stock_anterior = producto.cantidad_stock
                    producto.cantidad_stock -= cantidad_vendida
                    precio_limite_autorizado = producto.precio_costo if current_user.rol == 'admin' else producto.precio_minimo
                    
                    from models import StockAdjustment
                    ajuste = StockAdjustment(
                        product_id=producto.id,
                        admin_id=current_user.id,
                        tipo_movimiento="Venta Tienda",
                        stock_anterior=stock_anterior,
                        stock_nuevo=producto.cantidad_stock
                    )
                    db.session.add(ajuste)

                if not es_obsequio and precio_venta_final < precio_limite_autorizado:
                    # Si no es admin, verificar si cuenta con una aprobación remota activa
                    if current_user.rol != 'admin':
                        aprobacion = PriceApproval.query.filter(
                            PriceApproval.vendedor_id == current_user.id,
                            PriceApproval.product_id == (producto.id if producto else None),
                            PriceApproval.variant_id == variant_id,
                            PriceApproval.estado == 'aprobado',
                            PriceApproval.precio_aprobado <= precio_venta_final
                        ).order_by(PriceApproval.id.desc()).first()

                        if not aprobacion:
                            raise ValueError(f"No autorizado: El precio (${precio_venta_final:,.0f}) del producto '{producto.nombre}' está por debajo del mínimo permitido (${precio_limite_autorizado:,.0f}) y no cuenta con autorización remota aprobada por el administrador.")
                        
                        # Consumir la aprobación para que no sea reutilizable y vincular a la venta
                        aprobacion.estado = 'utilizada'
                        aprobacion.fecha_resolucion = obtener_hora_bogota()
                        aprobacion.sale_id = nueva_venta.id

                detalle = SaleDetail(
                    sale_id=nueva_venta.id,
                    product_id=producto.id,
                    variant_id=variant_id,
                    cantidad_vendida=cantidad_vendida,
                    precio_venta_final=precio_venta_final
                )
                db.session.add(detalle)
                db.session.flush() # Importante para tener el id de la venta si se quisiera, pero ya lo tenemos en nueva_venta.id
                
                # Para añadir el ID de la venta al tipo de movimiento ahora que la venta tiene ID asignado:
                ajuste.tipo_movimiento = f"{ajuste.tipo_movimiento} #{nueva_venta.id}"
                
                monto_total += (precio_venta_final * cantidad_vendida)

        nueva_venta.monto_total = monto_total

        # Registrar los pagos mixtos en la tabla sale_payments
        total_pagos = Decimal('0.00')
        for pago_info in pagos_data:
            metodo = pago_info.get('metodo_pago', 'efectivo')
            monto_pago = pago_info.get('monto')
            
            if monto_pago is None:
                # Si solo hay un pago sin monto explícito, asignar el total completo
                monto_pago = monto_total
            else:
                monto_pago = Decimal(str(monto_pago))
            
            if monto_pago <= 0:
                raise ValueError(f"El monto del pago por '{metodo}' debe ser mayor a 0.")
            
            pago = SalePayment(
                sale_id=nueva_venta.id,
                metodo_pago=metodo,
                monto=monto_pago
            )
            db.session.add(pago)
            total_pagos += monto_pago

        # Sincronización y tolerancia inteligente a redondeos de centavos (evita rechazos por $0.01)
        diferencia = monto_total - total_pagos

        if len(pagos_data) == 1:
            # En pago único, el monto del pago se ajusta al monto total exacto de la venta
            if nueva_venta.pagos:
                nueva_venta.pagos[0].monto = monto_total
                total_pagos = monto_total
        elif abs(diferencia) <= Decimal('0.50'):
            # En pagos mixtos, diferencias ínfimas por redondeo se absorben en el último pago
            if nueva_venta.pagos:
                nueva_venta.pagos[-1].monto += diferencia
                total_pagos = monto_total

        # Validar que la suma de pagos cubra el total de la venta
        if total_pagos != monto_total:
            raise ValueError(f"La suma de los pagos (${total_pagos}) no coincide con el total de la venta (${monto_total}). Diferencia: ${monto_total - total_pagos}.")


        db.session.commit()
        
        return jsonify({
            'success': True, 
            'message': 'Venta registrada e inventario descontado con éxito.',
            'sale_id': nueva_venta.id,
            'total': str(monto_total)
        }), 201

    except ValueError as val_err:
        db.session.rollback()
        return jsonify({'error': str(val_err)}), 400
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': 'Ocurrió un error interno al procesar la venta.'}), 500

@sales_bp.route('/api/search_products')
@login_required
def api_search_products():
    query = request.args.get('q', '').strip()
    
    if not query:
        return jsonify([])
    
    is_admin = (current_user.rol == 'admin')
    query_norm = normalizar_texto(query)
    
    # 1. Búsqueda exacta de SKU o código de barras (prioridad máxima para pistolas lectoras)
    exacto = Product.query.filter(
        Product.tipo_inventario == 'tienda',
        Product.sku.ilike(query)
    ).first()
    
    # 2. Búsqueda por palabras/términos ignorando mayúsculas y tildes
    tokens = [t for t in query.split() if len(t) > 0]
    tokens_norm = [normalizar_texto(t) for t in tokens]
    
    # Filtro preliminar en SQL con comodín de vocales para optimizar lectura en BD
    base_query = Product.query.filter(Product.tipo_inventario == 'tienda')
    for token in tokens:
        wildcard_v = comodin_vocales(token)
        base_query = base_query.filter(
            or_(
                Product.sku.ilike(f'%{token}%'),
                Product.nombre.ilike(f'%{token}%'),
                Product.nombre.ilike(f'%{wildcard_v}%'),
                Product.variantes.any(ProductVariant.nombre_variante.ilike(f'%{token}%')),
                Product.variantes.any(ProductVariant.nombre_variante.ilike(f'%{wildcard_v}%'))
            )
        )
    
    candidatos = base_query.limit(40).all()
    
    # Si la búsqueda SQL no trajo suficientes por caracteres especiales, incorporar catálogo de tienda
    if len(candidatos) < 5:
        todos_tienda = Product.query.filter_by(tipo_inventario='tienda').all()
        for p in todos_tienda:
            if p not in candidatos:
                candidatos.append(p)
                
    # Filtrado estricto en memoria normalizando 100% tildes, diacríticos y mayúsculas
    productos_filtrados = []
    for p in candidatos:
        p_nombre_norm = normalizar_texto(p.nombre)
        p_sku_norm = normalizar_texto(p.sku)
        variantes_norms = [normalizar_texto(v.nombre_variante) for v in p.variantes]
        
        # Cada token buscado debe estar presente en el nombre, sku o alguna variante
        coincide_todo = True
        for tn in tokens_norm:
            en_nombre = (tn in p_nombre_norm)
            en_sku = (tn in p_sku_norm)
            en_variante = any(tn in vn for vn in variantes_norms)
            if not (en_nombre or en_sku or en_variante):
                coincide_todo = False
                break
                
        if coincide_todo:
            productos_filtrados.append(p)
    
    # Asegurar que la coincidencia exacta de SKU vaya de primera si existe
    if exacto:
        if exacto in productos_filtrados:
            productos_filtrados.remove(exacto)
        productos_filtrados.insert(0, exacto)
    
    results = []
    for p in productos_filtrados[:20]:
        variantes_data = []
        matched_variant_id = None
        if p.variantes:
            for v in p.variantes:
                v_norm = normalizar_texto(v.nombre_variante)
                if any(tn in v_norm for tn in tokens_norm):
                    if not matched_variant_id:
                        matched_variant_id = v.id
                
                v_costo = float(v.precio_costo) if v.precio_costo is not None else float(p.precio_costo or 0)
                v_minimo = float(v.precio_minimo) if v.precio_minimo is not None else float(p.precio_minimo or 0)
                v_sugerido = float(v.precio_sugerido) if v.precio_sugerido is not None else float(p.precio_sugerido or 0)
                v_limite = v_costo if is_admin else v_minimo
                
                variantes_data.append({
                    'id': v.id,
                    'nombre': v.nombre_variante,
                    'stock': v.cantidad_stock,
                    'precio_costo': v_costo,
                    'precio_minimo': v_minimo,
                    'precio_sugerido': v_sugerido,
                    'precio_limite': v_limite
                })
        
        p_costo = float(p.precio_costo or 0)
        p_minimo = float(p.precio_minimo or 0)
        p_sugerido = float(p.precio_sugerido or 0)
        p_limite = p_costo if is_admin else p_minimo
        
        results.append({
            'id': p.id,
            'nombre': p.nombre,
            'sku': p.sku,
            'tipo_inventario': p.tipo_inventario,
            'cantidad_stock': p.total_stock,
            'precio_minimo': p_minimo,
            'precio_sugerido': p_sugerido,
            'precio_costo': p_costo,
            'precio_limite': p_limite,
            'variantes': variantes_data,
            'matched_variant_id': matched_variant_id
        })
    
    return jsonify(results)

# Endpoint API asíncrono para el escáner del Punto de Venta
@sales_bp.route('/api/producto/<path:sku>', methods=['GET'])
@login_required
def api_buscar_producto(sku):
    sku_clean = sku.strip()
    is_admin = (current_user.rol == 'admin')
    
    # 1. Búsqueda exacta
    producto = Product.query.filter(Product.sku == sku_clean, Product.tipo_inventario == 'tienda').first()
    
    # 2. Si no encuentra exacto, intentar case-insensitive
    if not producto:
        producto = Product.query.filter(Product.sku.ilike(sku_clean), Product.tipo_inventario == 'tienda').first()
        
    # 3. Si aún no, buscar ignorando tildes y mayúsculas en nombre o SKU
    if not producto:
        sku_norm = normalizar_texto(sku_clean)
        todos_tienda = Product.query.filter_by(tipo_inventario='tienda').all()
        for p in todos_tienda:
            if normalizar_texto(p.sku) == sku_norm or sku_norm in normalizar_texto(p.nombre):
                producto = p
                break
    
    if not producto:
        return jsonify({'error': f"Código o producto '{sku_clean}' no encontrado en la tienda"}), 404
        
    variantes_data = []
    for v in producto.variantes:
        v_costo = float(v.precio_costo) if v.precio_costo is not None else float(producto.precio_costo or 0)
        v_minimo = float(v.precio_minimo) if v.precio_minimo is not None else float(producto.precio_minimo or 0)
        v_sugerido = float(v.precio_sugerido) if v.precio_sugerido is not None else float(producto.precio_sugerido or 0)
        v_limite = v_costo if is_admin else v_minimo
        variantes_data.append({
            'id': v.id,
            'nombre': v.nombre_variante,
            'stock': v.cantidad_stock,
            'precio_minimo': v_minimo,
            'precio_limite': v_limite,
            'precio_sugerido': v_sugerido,
            'precio_costo': v_costo
        })

    return jsonify({
        'id': producto.id,
        'nombre': producto.nombre,
        'sku': producto.sku,
        'tipo_inventario': producto.tipo_inventario,
        'cantidad_stock': producto.total_stock,
        'precio_minimo': float(producto.precio_minimo or 0),
        'precio_limite': float(producto.precio_costo or 0) if is_admin else float(producto.precio_minimo or 0),
        'precio_sugerido': float(producto.precio_sugerido or 0),
        'variantes': variantes_data,
        'auto_select_variant': None
    })

# Ruta para la Impresión del formato Térmico (Ticket)
@sales_bp.route('/recibo/<int:sale_id>', methods=['GET'])
@login_required # Proteger confidencialidad del cajero
def imprimir_ticket(sale_id):
    # Regla: Retorna 404 si alguien ingresa un ID falso
    venta = Sale.query.get_or_404(sale_id)
    return render_template('sales/ticket.html', venta=venta)

# Endpoint Historial de Ventas (Administradores)
@sales_bp.route('/historial', methods=['GET'])
@login_required
@admin_required
def historial():
    # Calcular el valor exacto de 'HOY' en Bogotá
    hoy_bogota = obtener_hora_bogota().strftime('%Y-%m-%d')
    
    # Si existen los args, los usa, de lo contrario colapsa a HOY por defecto
    fecha_inicio = request.args.get('fecha_inicio', hoy_bogota)
    fecha_fin = request.args.get('fecha_fin', hoy_bogota)
    
    # Optimización: eager loading (evita N+1 con joinedload)
    query = Sale.query.options(joinedload(Sale.vendedor))
    
    # Motor de búsqueda por Rango Restricto
    if fecha_inicio:
        inicio_dt = datetime.strptime(fecha_inicio, '%Y-%m-%d')
        query = query.filter(Sale.fecha_venta >= inicio_dt)
        
    if fecha_fin:
        fin_dt = datetime.strptime(fecha_fin, '%Y-%m-%d')
        # Sumar 1 día matemáticamente para incluir los registros hasta las 23:59:59 del último día
        query = query.filter(Sale.fecha_venta < fin_dt + timedelta(days=1))
        
    ventas = query.order_by(Sale.fecha_venta.desc()).all()
    
    # Auditar y cruzar sumatorios de métricas de pago
    # Sistema híbrido: usa SalePayment si existe, caso contrario cae al metodo_pago legacy
    total_efectivo = Decimal('0')
    total_nequi = Decimal('0')
    total_bancolombia = Decimal('0')
    total_daviplata = Decimal('0')
    total_bolt = Decimal('0')
    total_transferencia_legacy = Decimal('0')
    total_mixto = 0  # Contador de ventas con pago mixto

    for v in ventas:
        if v.pagos:  # Pagos nuevos con tabla sale_payments
            for pago in v.pagos:
                metodo = (pago.metodo_pago or '').lower().strip()
                if metodo == 'efectivo':
                    total_efectivo += pago.monto
                elif metodo == 'nequi':
                    total_nequi += pago.monto
                elif metodo == 'bancolombia':
                    total_bancolombia += pago.monto
                elif metodo == 'daviplata':
                    total_daviplata += pago.monto
                elif metodo in ['bolt', 'bold']:
                    total_bolt += pago.monto
                elif metodo == 'transferencia':
                    total_transferencia_legacy += pago.monto
                else:
                    # En caso de otro método digital no mapeado
                    total_transferencia_legacy += pago.monto
            if len(v.pagos) > 1:
                total_mixto += 1
        else:  # Retrocompatibilidad con ventas antiguas sin SalePayment
            metodo_v = (v.metodo_pago or '').lower().strip()
            if metodo_v == 'efectivo':
                total_efectivo += v.monto_total
            elif metodo_v == 'nequi':
                total_nequi += v.monto_total
            elif metodo_v == 'bancolombia':
                total_bancolombia += v.monto_total
            elif metodo_v == 'daviplata':
                total_daviplata += v.monto_total
            elif metodo_v in ['bolt', 'bold']:
                total_bolt += v.monto_total
            elif metodo_v == 'transferencia':
                total_transferencia_legacy += v.monto_total
            else:
                total_transferencia_legacy += v.monto_total

    # Métricas consolidadas de alto impacto
    total_general = total_efectivo + total_nequi + total_bancolombia + total_daviplata + total_bolt + total_transferencia_legacy
    total_digital = total_nequi + total_bancolombia + total_daviplata + total_bolt + total_transferencia_legacy
    total_operaciones = len(ventas)
    ticket_promedio = (total_general / Decimal(total_operaciones)) if total_operaciones > 0 else Decimal('0')

    # Envío al Engine de HTML
    return render_template('sales/historial.html', 
                           ventas=ventas, 
                           total_general=total_general,
                           total_digital=total_digital,
                           total_operaciones=total_operaciones,
                           ticket_promedio=ticket_promedio,
                           total_efectivo=total_efectivo,
                           total_nequi=total_nequi,
                           total_bancolombia=total_bancolombia,
                           total_daviplata=total_daviplata,
                           total_bolt=total_bolt,
                           total_transferencia_legacy=total_transferencia_legacy,
                           total_mixto=total_mixto,
                           fecha_inicio=fecha_inicio,
                           fecha_fin=fecha_fin)


# Endpoint Visor de Ventas del Día para Cajeros (Solo lectura, se resetea cada día)
@sales_bp.route('/ventas_hoy', methods=['GET'])
@login_required
def ventas_hoy():
    # Obtener la fecha de hoy
    hoy_bogota = obtener_hora_bogota().date()
    # Para la consulta requerimos abarcar desde las 00:00:00 hasta las 23:59:59
    inicio_dt = datetime.combine(hoy_bogota, datetime.min.time())
    fin_dt = datetime.combine(hoy_bogota, datetime.max.time())
    
    # Consultar todas las ventas de este día (sin importar si es admin o vendedor)
    ventas = Sale.query.options(joinedload(Sale.vendedor)).filter(
        Sale.fecha_venta >= inicio_dt,
        Sale.fecha_venta <= fin_dt
    ).order_by(Sale.fecha_venta.desc()).all()
    
    # Acumuladores de las ventas de hoy
    total_efectivo = Decimal('0')
    total_transferencias = Decimal('0')
    total_mixto = 0
    
    for v in ventas:
        if v.pagos:
            for pago in v.pagos:
                if pago.metodo_pago == 'efectivo':
                    total_efectivo += pago.monto
                else: 
                    total_transferencias += pago.monto
            if len(v.pagos) > 1:
                total_mixto += 1
        else:
            if v.metodo_pago == 'efectivo':
                total_efectivo += v.monto_total
            else:
                total_transferencias += v.monto_total
                
    return render_template('sales/ventas_hoy.html',
                           ventas=ventas,
                           total_efectivo=total_efectivo,
                           total_transferencias=total_transferencias,
                           total_mixto=total_mixto,
                           hoy=hoy_bogota.strftime('%Y-%m-%d'))


# Endpoint para Anular/Eliminar Venta Histórica
@sales_bp.route('/eliminar/<int:sale_id>', methods=['POST'])
@login_required
@admin_required
def eliminar_venta(sale_id):
    venta = Sale.query.get_or_404(sale_id)
    
    # Validar si la caja de la fecha de la venta ya está cerrada
    caja_cerrada = ArqueoCaja.query.filter_by(fecha_arqueo=venta.fecha_venta.date()).first()
    if caja_cerrada:
        flash(f'No se puede anular la venta #{venta.id} porque la caja del día {venta.fecha_venta.date().strftime("%Y-%m-%d")} ya fue cerrada.', 'danger')
        return redirect(url_for('sales_bp.historial'))
    
    try:
        # Revertir Stock
        from models import StockAdjustment
        for detalle in venta.detalles:
            if detalle.variant_id:
                variante = ProductVariant.query.with_for_update().get(detalle.variant_id)
                if variante:
                    stock_anterior = variante.cantidad_stock
                    variante.cantidad_stock += detalle.cantidad_vendida
                    
                    ajuste = StockAdjustment(
                        product_id=detalle.product_id,
                        admin_id=current_user.id,
                        tipo_movimiento=f"Anulación Venta #{venta.id} (Subcat: {variante.nombre_variante})",
                        stock_anterior=stock_anterior,
                        stock_nuevo=variante.cantidad_stock
                    )
                    db.session.add(ajuste)
                    
                producto = Product.query.with_for_update().get(detalle.product_id)
                if producto:
                    producto.cantidad_stock += detalle.cantidad_vendida
            elif detalle.product_id:
                producto = Product.query.with_for_update().get(detalle.product_id)
                if producto:
                    stock_anterior = producto.cantidad_stock
                    producto.cantidad_stock += detalle.cantidad_vendida
                    
                    ajuste = StockAdjustment(
                        product_id=producto.id,
                        admin_id=current_user.id,
                        tipo_movimiento=f"Anulación Venta #{venta.id}",
                        stock_anterior=stock_anterior,
                        stock_nuevo=producto.cantidad_stock
                    )
                    db.session.add(ajuste)
                    
        # Eliminar Venta y Detalles (Cascada)
        db.session.delete(venta)
        db.session.commit()
        flash('Venta anulada y stock devuelto exitosamente.', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash('Ocurrió un error al anular la venta.', 'danger')
        
# Endpoint para Editar Método/Distribución de Pago de una Venta Histórica
@sales_bp.route('/editar_pago/<int:sale_id>', methods=['POST'])
@login_required
@admin_required
def editar_pago(sale_id):
    venta = Sale.query.get_or_404(sale_id)
    
    fecha_inicio = request.form.get('fecha_inicio', '')
    fecha_fin = request.form.get('fecha_fin', '')
    
    # Validar si la caja de la fecha de la venta ya está cerrada
    caja_cerrada = ArqueoCaja.query.filter_by(fecha_arqueo=venta.fecha_venta.date()).first()
    if caja_cerrada:
        flash(f'No se puede modificar el pago de la venta #{venta.id} porque la caja del día {venta.fecha_venta.date().strftime("%Y-%m-%d")} ya fue cerrada.', 'danger')
        return redirect(url_for('sales_bp.historial', fecha_inicio=fecha_inicio, fecha_fin=fecha_fin))
    
    try:
        tipo_pago = request.form.get('tipo_pago', 'unico')
        
        if tipo_pago == 'unico':
            metodo = request.form.get('metodo_pago_unico', 'efectivo').lower().strip()
            if metodo not in ['efectivo', 'nequi', 'bancolombia', 'daviplata', 'bolt']:
                metodo = 'efectivo'
            
            # Limpiar pagos previos
            SalePayment.query.filter_by(sale_id=venta.id).delete()
            
            nuevo_pago = SalePayment(
                sale_id=venta.id,
                metodo_pago=metodo,
                monto=venta.monto_total
            )
            db.session.add(nuevo_pago)
            venta.metodo_pago = metodo
            
        elif tipo_pago == 'mixto':
            metodos_posibles = ['efectivo', 'nequi', 'bancolombia', 'daviplata', 'bolt']
            nuevos_pagos = []
            suma_montos = Decimal('0.00')
            
            for m in metodos_posibles:
                raw_val = request.form.get(f'monto_{m}', '0').replace(',', '').replace('$', '').strip()
                if raw_val:
                    try:
                        monto_decimal = Decimal(raw_val)
                        if monto_decimal > Decimal('0'):
                            nuevos_pagos.append((m, monto_decimal))
                            suma_montos += monto_decimal
                    except Exception:
                        pass
            
            if not nuevos_pagos:
                flash('Debe especificar al menos un método con monto mayor a $0 en pago mixto.', 'warning')
                return redirect(url_for('sales_bp.historial', fecha_inicio=fecha_inicio, fecha_fin=fecha_fin))
                
            # Validar que la suma sea exactamente igual al total de la venta
            if abs(suma_montos - venta.monto_total) > Decimal('0.01'):
                flash(f'La suma de los pagos (${suma_montos:,.0f}) no coincide con el total de la factura (${venta.monto_total:,.0f}).', 'danger')
                return redirect(url_for('sales_bp.historial', fecha_inicio=fecha_inicio, fecha_fin=fecha_fin))
            
            # Limpiar pagos previos y crear los nuevos
            SalePayment.query.filter_by(sale_id=venta.id).delete()
            
            for m, monto_val in nuevos_pagos:
                p = SalePayment(
                    sale_id=venta.id,
                    metodo_pago=m,
                    monto=monto_val
                )
                db.session.add(p)
                
            if len(nuevos_pagos) == 1:
                venta.metodo_pago = nuevos_pagos[0][0]
            else:
                venta.metodo_pago = 'mixto'
        
        db.session.commit()
        flash(f'¡Método de pago de la venta #{venta.id} actualizado exitosamente!', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Ocurrió un error al actualizar el pago: {str(e)}', 'danger')
        
    return redirect(url_for('sales_bp.historial', fecha_inicio=fecha_inicio, fecha_fin=fecha_fin))

# Endpoint Catálogo Estricto de solo vista para Operarios
@sales_bp.route('/catalogo', methods=['GET'])
@login_required 
def catalogo():
    query_str = request.args.get('q', '').strip()
    
    if query_str:
        # Motor de similitud Case-Insensitive (Like)
        search_term = f"%{query_str}%"
        productos = Product.query.filter(Product.tipo_inventario == 'tienda').filter(
            or_(
                Product.sku.ilike(search_term), 
                Product.nombre.ilike(search_term)
            )
        ).limit(50).all()
    else:
        # Límite pasivo de 50 ítems para ahorrar memoria RAM de BD en carga inicial
        productos = Product.query.filter(Product.tipo_inventario == 'tienda').limit(50).all()
        
    return render_template('sales/catalogo.html', productos=productos, q=query_str)

@sales_bp.route('/caja_visual', methods=['GET'])
@login_required
def caja_visual():
    from models import obtener_hora_bogota
    hoy_bogota = obtener_hora_bogota()
    productos = Product.query.filter(Product.tipo_inventario == 'tienda').order_by(Product.nombre.asc()).all()
    return render_template('sales/caja_visual.html', productos=productos, hoy=hoy_bogota.strftime('%Y-%m-%d'))

