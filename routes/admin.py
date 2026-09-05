from flask import Blueprint, render_template, abort, request, redirect, url_for, flash
from flask_login import login_required, current_user
from models import db, Product, ProductVariant, Sale, User, Maneo, Cliente, SaleDetail, SalePayment, StockAdjustment, Expense, ArqueoCaja, ProviderPayment, FacturaBodega, AbonoBodega, obtener_hora_bogota
from sqlalchemy.sql import func
from werkzeug.security import generate_password_hash
from decorators import admin_required
from decimal import Decimal
from datetime import datetime, timedelta
import calendar

admin_bp = Blueprint('admin_bp', __name__)

@admin_bp.route('/vendedores', methods=['GET', 'POST'])
@login_required
@admin_required
def vendedores():
    if request.method == 'POST':
        nombre = request.form.get('nombre')
        email = request.form.get('email')
        telefono = request.form.get('telefono')
        password = request.form.get('password')
        rol = request.form.get('rol', 'vendedor')
        
        # Validar que el rol sea uno de los permitidos por el sistema
        roles_permitidos = ['admin', 'vendedor', 'bodega', 'vendedor_bodega']
        if rol not in roles_permitidos:
            rol = 'vendedor'
        
        # Se previene registrar usuarios con un mismo email para preservar la unicidad de las credenciales de acceso
        if User.query.filter_by(email=email.strip()).first():
            flash('Acción Denegada: Ese correo ya le pertenece a otro usuario.', 'danger')
        else:
            try:
                # Se aplica un hash a la contraseña para evitar guardar texto plano, previniendo exposición en caso de brechas
                nuevo_usuario = User(
                    nombre=nombre.strip(),
                    email=email.strip(),
                    telefono=telefono.strip() if telefono else None,
                    password_hash=generate_password_hash(password),
                    rol=rol
                )
                db.session.add(nuevo_usuario)
                db.session.commit()
                rol_label = 'Administrador' if rol == 'admin' else ('Vendedor' if rol == 'vendedor' else ('Encargado de Bodega' if rol == 'bodega' else 'Vendedor de Bodega'))
                flash(f"¡Usuario '{nombre}' registrado como '{rol_label}' exitosamente!", "success")
            except Exception as e:
                db.session.rollback()
                flash('Ocurrió un error en la base de datos al intentar registrar al usuario.', 'danger')
            
        return redirect(url_for('admin_bp.vendedores'))
        
    # Se pasa la lista para poblar la tabla HTML de gestión de personal
    # Mostramos todos los usuarios activos del sistema (excluyendo solo registros eliminados)
    lista_vendedores = User.query.filter(User.rol != 'eliminado').order_by(User.rol == 'admin', User.nombre).all()
    return render_template('admin/vendedores.html', vendedores=lista_vendedores)

@admin_bp.route('/vendedores/<int:id>/eliminar', methods=['POST'])
@login_required
@admin_required
def eliminar_vendedor(id):
    usuario = User.query.get_or_404(id)
    if usuario.id == current_user.id:
        flash("Acción Denegada: No puedes eliminar tu propia cuenta de usuario.", "danger")
        return redirect(url_for('admin_bp.vendedores'))
        
    try:
        # En lugar de hacer un delete() duro que rompe las llaves foráneas (ventas, facturas), hacemos un soft delete
        usuario.rol = 'eliminado'
        usuario.email = f"eliminado_{usuario.id}_{usuario.email}"
        db.session.commit()
        flash(f"¡Usuario '{usuario.nombre}' eliminado exitosamente!", "success")
    except Exception as e:
        db.session.rollback()
        flash("Ocurrió un error al intentar eliminar el usuario.", "danger")
        
    return redirect(url_for('admin_bp.vendedores'))

@admin_bp.route('/dashboard')
@login_required
@admin_required
def dashboard():
    hoy = obtener_hora_bogota()

    meses_nombres = {
        1: 'Enero', 2: 'Febrero', 3: 'Marzo', 4: 'Abril',
        5: 'Mayo', 6: 'Junio', 7: 'Julio', 8: 'Agosto',
        9: 'Septiembre', 10: 'Octubre', 11: 'Noviembre', 12: 'Diciembre'
    }

    # Determinar el tipo de filtro
    filtro_tipo = request.args.get('filtro_tipo')
    if not filtro_tipo:
        if 'fecha_dia' in request.args:
            filtro_tipo = 'dia'
        elif 'fecha_semana' in request.args:
            filtro_tipo = 'semana'
        elif 'quincena_num' in request.args:
            filtro_tipo = 'quincena'
        else:
            filtro_tipo = 'mes'

    fecha_dia_str = request.args.get('fecha_dia', hoy.strftime('%Y-%m-%d'))
    fecha_semana_str = request.args.get('fecha_semana', hoy.strftime('%Y-%m-%d'))

    try:
        mes = int(request.args.get('mes', hoy.month))
        if not (1 <= mes <= 12):
            mes = hoy.month
    except (ValueError, TypeError):
        mes = hoy.month

    try:
        anio = int(request.args.get('anio', hoy.year))
        if anio < 2020 or anio > 2035:
            anio = hoy.year
    except (ValueError, TypeError):
        anio = hoy.year

    try:
        quincena_num = int(request.args.get('quincena_num', 1 if hoy.day <= 15 else 2))
        if quincena_num not in [1, 2]:
            quincena_num = 1 if hoy.day <= 15 else 2
    except (ValueError, TypeError):
        quincena_num = 1 if hoy.day <= 15 else 2

    # Cálculo de fechas según filtro
    if filtro_tipo == 'dia':
        try:
            dt_dia = datetime.strptime(fecha_dia_str, '%Y-%m-%d')
        except ValueError:
            dt_dia = hoy
            fecha_dia_str = hoy.strftime('%Y-%m-%d')
        inicio_filtro = datetime(dt_dia.year, dt_dia.month, dt_dia.day, 0, 0, 0)
        fin_filtro = datetime(dt_dia.year, dt_dia.month, dt_dia.day, 23, 59, 59, 999999)
        es_hoy = (dt_dia.date() == hoy.date())
        label_periodo = f"{'Hoy, ' if es_hoy else ''}{dt_dia.day} de {meses_nombres.get(dt_dia.month)} {dt_dia.year}"
        badge_periodo = "Hoy" if es_hoy else dt_dia.strftime('%d/%m/%Y')
        tipo_periodo_nombre = "Día"

    elif filtro_tipo == 'semana':
        try:
            dt_sem = datetime.strptime(fecha_semana_str, '%Y-%m-%d')
        except ValueError:
            dt_sem = hoy
            fecha_semana_str = hoy.strftime('%Y-%m-%d')
        lunes = dt_sem.date() - timedelta(days=dt_sem.weekday())
        domingo = lunes + timedelta(days=6)
        inicio_filtro = datetime(lunes.year, lunes.month, lunes.day, 0, 0, 0)
        fin_filtro = datetime(domingo.year, domingo.month, domingo.day, 23, 59, 59, 999999)
        label_periodo = f"Semana del {lunes.day} {meses_nombres.get(lunes.month)[:3]} al {domingo.day} {meses_nombres.get(domingo.month)[:3]} {domingo.year}"
        badge_periodo = f"{lunes.strftime('%d/%m')} - {domingo.strftime('%d/%m')}"
        tipo_periodo_nombre = "Semana"

    elif filtro_tipo == 'quincena':
        ultimo_dia_mes = calendar.monthrange(anio, mes)[1]
        if quincena_num == 1:
            inicio_filtro = datetime(anio, mes, 1, 0, 0, 0)
            fin_filtro = datetime(anio, mes, 15, 23, 59, 59, 999999)
            label_periodo = f"1ª Quincena de {meses_nombres.get(mes)} {anio} (1 al 15)"
            badge_periodo = f"1ª Q. {meses_nombres.get(mes)[:3]}"
        else:
            inicio_filtro = datetime(anio, mes, 16, 0, 0, 0)
            fin_filtro = datetime(anio, mes, ultimo_dia_mes, 23, 59, 59, 999999)
            label_periodo = f"2ª Quincena de {meses_nombres.get(mes)} {anio} (16 al {ultimo_dia_mes})"
            badge_periodo = f"2ª Q. {meses_nombres.get(mes)[:3]}"
        tipo_periodo_nombre = "Quincena"

    else: # mes
        filtro_tipo = 'mes'
        ultimo_dia_mes = calendar.monthrange(anio, mes)[1]
        inicio_filtro = datetime(anio, mes, 1, 0, 0, 0)
        fin_filtro = datetime(anio, mes, ultimo_dia_mes, 23, 59, 59, 999999)
        label_periodo = f"{meses_nombres.get(mes)} {anio}"
        badge_periodo = f"{meses_nombres.get(mes)} {anio}"
        tipo_periodo_nombre = "Mes"

    # 1. Ventas e Ingresos del Periodo
    ventas_en_rango = Sale.query.filter(Sale.fecha_venta >= inicio_filtro, Sale.fecha_venta <= fin_filtro).all()
    total_ventas = sum((v.monto_total or 0) for v in ventas_en_rango)
    conteo_ventas = len(ventas_en_rango)
    ventas_efectivo = sum((v.monto_total or 0) for v in ventas_en_rango if v.metodo_pago == 'efectivo')
    ventas_transferencia = sum((v.monto_total or 0) for v in ventas_en_rango if v.metodo_pago in ['transferencia', 'nequi', 'bancolombia', 'daviplata'])
    ticket_promedio = (float(total_ventas) / conteo_ventas) if conteo_ventas > 0 else 0.0

    # 2. Unidades y Mercancía Vendida en el Periodo
    detalles_en_rango = SaleDetail.query.join(Sale).filter(
        Sale.fecha_venta >= inicio_filtro,
        Sale.fecha_venta <= fin_filtro
    ).all()
    unidades_vendidas = sum(d.cantidad_vendida for d in detalles_en_rango)
    referencias_vendidas = len(set(d.product_id for d in detalles_en_rango if d.product_id))
    total_productos = Product.query.count()

    # 3. Gastos Operativos del Periodo
    gastos_en_rango = Expense.query.filter(
        Expense.fecha_gasto >= inicio_filtro,
        Expense.fecha_gasto <= fin_filtro
    ).all()
    total_gastos = sum((g.monto or 0) for g in gastos_en_rango)
    conteo_gastos = len(gastos_en_rango)
    gastos_diarios = sum((g.monto or 0) for g in gastos_en_rango if g.tipo_gasto == 'Gasto Diario')
    costos_indirectos = sum((g.monto or 0) for g in gastos_en_rango if g.tipo_gasto == 'Costo Indirecto')

    # 4. Utilidad / Ganancia Estimada del Periodo (COGS)
    costos_directos = Decimal('0.00')
    for d in detalles_en_rango:
        if d.nombre_manual:
            costos_directos += Decimal(str(d.precio_costo_manual or 0)) * d.cantidad_vendida
        elif d.variant_id:
            v = d.variante
            p = d.producto
            if v and p:
                costo_u = v.precio_costo if v.precio_costo is not None else (p.precio_costo or 0)
                costos_directos += Decimal(str(costo_u)) * d.cantidad_vendida
        elif d.product_id:
            p = d.producto
            if p:
                costos_directos += Decimal(str(p.precio_costo or 0)) * d.cantidad_vendida
    utilidad_neta = float(total_ventas) - float(costos_directos) - float(total_gastos)

    # 5. Maneos (Préstamos) del Periodo y Estado Global
    maneos_en_rango = Maneo.query.filter(
        Maneo.fecha_prestamo >= inicio_filtro,
        Maneo.fecha_prestamo <= fin_filtro
    ).all()
    maneos_periodo_count = len(maneos_en_rango)
    maneos_periodo_monto = sum(m.subtotal_calculado for m in maneos_en_rango)
    maneos_activos = Maneo.query.filter_by(estado='PENDIENTE').count()

    # 6. Alertas de Stock y Ajustes del Periodo
    productos_bajo_stock = Product.query.filter(Product.cantidad_stock <= 10).count()
    ajustes_periodo = StockAdjustment.query.filter(
        StockAdjustment.fecha_ajuste >= inicio_filtro,
        StockAdjustment.fecha_ajuste <= fin_filtro
    ).count()

    # 7. Proveedores del Periodo
    pagos_prov_en_rango = ProviderPayment.query.filter(
        ProviderPayment.fecha_pago >= inicio_filtro,
        ProviderPayment.fecha_pago <= fin_filtro
    ).all()
    pagos_proveedores_periodo = sum((p.monto_abonado or 0) for p in pagos_prov_en_rango)
    conteo_pagos_prov = len(pagos_prov_en_rango)

    return render_template('admin/dashboard.html',
                           filtro_tipo=filtro_tipo,
                           fecha_dia=fecha_dia_str,
                           fecha_semana=fecha_semana_str,
                           quincena_num=quincena_num,
                           mes=mes,
                           anio=anio,
                           label_periodo=label_periodo,
                           badge_periodo=badge_periodo,
                           tipo_periodo_nombre=tipo_periodo_nombre,
                           inicio_filtro=inicio_filtro,
                           fin_filtro=fin_filtro,
                           # Tarjeta 1: Ingresos
                           total_ventas=total_ventas,
                           conteo_ventas=conteo_ventas,
                           ventas_efectivo=ventas_efectivo,
                           ventas_transferencia=ventas_transferencia,
                           ticket_promedio=ticket_promedio,
                           # Tarjeta 2: Unidades vendidas y catálogo
                           unidades_vendidas=unidades_vendidas,
                           referencias_vendidas=referencias_vendidas,
                           total_productos=total_productos,
                           # Tarjeta 3: Gastos
                           total_gastos=total_gastos,
                           conteo_gastos=conteo_gastos,
                           gastos_diarios=gastos_diarios,
                           costos_indirectos=costos_indirectos,
                           # Tarjeta 4: Utilidad
                           utilidad_neta=utilidad_neta,
                           costos_directos=float(costos_directos),
                           # Tarjeta 5: Maneos
                           maneos_periodo_count=maneos_periodo_count,
                           maneos_periodo_monto=maneos_periodo_monto,
                           maneos_activos=maneos_activos,
                           # Tarjeta 6: Alertas de Stock
                           productos_bajo_stock=productos_bajo_stock,
                           ajustes_periodo=ajustes_periodo,
                           # Tarjeta 7: Proveedores
                           pagos_proveedores_periodo=pagos_proveedores_periodo,
                           conteo_pagos_prov=conteo_pagos_prov,
                           # Retrocompatibilidad
                           nombre_mes=meses_nombres.get(mes, 'Mes Actual'))

@admin_bp.route('/maneos')
@login_required
def maneos():
    lista_maneos = Maneo.query.order_by(Maneo.fecha_prestamo.desc()).all()
    # Priorizar PENDIENTE temporalmente
    lista_maneos.sort(key=lambda m: 0 if m.estado == 'PENDIENTE' else 1)
    
    productos = Product.query.order_by(Product.nombre).all()
    clientes = Cliente.query.order_by(Cliente.nombre_o_razon_social.asc()).all()
    selected_cliente_id = request.args.get('cliente_id', type=int)
    return render_template(
        'admin/maneos.html', 
        maneos=lista_maneos, 
        productos=productos, 
        clientes=clientes, 
        selected_cliente_id=selected_cliente_id
    )

@admin_bp.route('/maneos/prestar', methods=['POST'])
@login_required
def maneos_prestar():
    cliente_id_raw = request.form.get('cliente_id', '').strip()
    local_vecino = request.form.get('local_vecino', '').strip()

    cliente = None
    if cliente_id_raw:
        try:
            cliente = Cliente.query.get(int(cliente_id_raw))
        except (ValueError, TypeError):
            cliente = None

    if cliente:
        local_vecino = cliente.nombre_o_razon_social
    elif local_vecino:
        # Si se escribió manualmente, sincronizar con el directorio de Clientes
        cliente = Cliente.query.filter(Cliente.nombre_o_razon_social.ilike(local_vecino)).first()
        if not cliente:
            cliente = Cliente(nombre_o_razon_social=local_vecino, creado_por_id=current_user.id)
            db.session.add(cliente)
            db.session.flush()
    else:
        flash('Debes seleccionar o indicar el Local Vecino o Persona a quien se le presta.', 'danger')
        return redirect(url_for('admin_bp.maneos'))

    # Obtener listas de campos del formulario
    skus = request.form.getlist('sku[]') or request.form.getlist('sku')
    cantidades = request.form.getlist('cantidad[]') or request.form.getlist('cantidad')
    valores_fijos = request.form.getlist('valor_fijo[]') or request.form.getlist('valor_fijo')
    variant_ids = request.form.getlist('variant_id[]') or request.form.getlist('variant_id')

    # Fallback si se envió como campo simple
    if not skus and request.form.get('sku'):
        skus = [request.form.get('sku')]
        cantidades = [request.form.get('cantidad', '1')]
        valores_fijos = [request.form.get('valor_fijo', '')]
        variant_ids = [request.form.get('variant_id', '')]

    items_a_procesar = []
    for idx, raw_sku in enumerate(skus):
        sku_str = (raw_sku or '').strip()
        if not sku_str:
            continue
        
        cant_str = cantidades[idx] if idx < len(cantidades) else ''
        try:
            cant = int(cant_str) if cant_str else 1
        except ValueError:
            cant = 1
        if cant < 1:
            cant = 1

        val_str = valores_fijos[idx] if idx < len(valores_fijos) else ''
        valor_fijo = None
        if val_str and str(val_str).strip():
            try:
                clean_v = str(val_str).strip().replace('$', '').replace(' ', '')
                if '.' in clean_v and ',' in clean_v:
                    clean_v = clean_v.replace('.', '').replace(',', '.')
                elif ',' in clean_v:
                    clean_v = clean_v.replace(',', '.')
                val_num = float(clean_v)
                # Si el usuario ingresa un número menor a 1000 (ej: 15), se interpreta en miles ($15.000)
                if 0 < val_num < 1000:
                    val_num = val_num * 1000
                valor_fijo = val_num
            except ValueError:
                valor_fijo = None

        var_id = variant_ids[idx] if idx < len(variant_ids) else ''
        items_a_procesar.append({
            'sku': sku_str,
            'cantidad': cant,
            'valor_fijo': valor_fijo,
            'variant_id': var_id
        })

    if not items_a_procesar:
        flash('Debes ingresar al menos un producto a prestar.', 'warning')
        return redirect(url_for('admin_bp.maneos'))

    try:
        registrados = 0
        for item in items_a_procesar:
            sku = item['sku']
            cantidad = item['cantidad']
            valor_fijo = item['valor_fijo']
            variant_id_str = item['variant_id']

            producto = Product.query.filter_by(sku=sku).first()
            if not producto:
                flash(f'Error: El producto con SKU "{sku}" no existe en el catálogo.', 'danger')
                db.session.rollback()
                return redirect(url_for('admin_bp.maneos'))

            variante = None
            if variant_id_str and str(variant_id_str).strip():
                try:
                    variante = ProductVariant.query.get(int(variant_id_str))
                except (ValueError, TypeError):
                    variante = None

            if variante and variante.product_id != producto.id:
                flash(f'La subcategoría seleccionada no pertenece a "{producto.nombre}".', 'danger')
                db.session.rollback()
                return redirect(url_for('admin_bp.maneos'))

            if variante:
                if variante.cantidad_stock < cantidad:
                    flash(f'Stock insuficiente en "{variante.nombre_variante}" para prestar {cantidad} uds (Stock actual: {variante.cantidad_stock}).', 'danger')
                    db.session.rollback()
                    return redirect(url_for('admin_bp.maneos'))
                stock_anterior = variante.cantidad_stock
                variante.cantidad_stock -= cantidad
                stock_nuevo = variante.cantidad_stock
            else:
                if producto.cantidad_stock < cantidad:
                    flash(f'Stock insuficiente en "{producto.nombre}" para prestar {cantidad} uds (Stock actual: {producto.cantidad_stock}).', 'danger')
                    db.session.rollback()
                    return redirect(url_for('admin_bp.maneos'))
                stock_anterior = producto.cantidad_stock
                producto.cantidad_stock -= cantidad
                stock_nuevo = producto.cantidad_stock

            nuevo_maneo = Maneo(
                product_id=producto.id,
                variant_id=variante.id if variante else None,
                cliente_id=cliente.id if cliente else None,
                local_vecino=local_vecino,
                cantidad=cantidad,
                valor_fijo=valor_fijo,
                estado='PENDIENTE'
            )
            db.session.add(nuevo_maneo)

            ajuste = StockAdjustment(
                product_id=producto.id,
                admin_id=current_user.id,
                tipo_movimiento=f'Préstamo (Maneo) a {local_vecino}' + (f' [{variante.nombre_variante}]' if variante else ''),
                stock_anterior=stock_anterior,
                stock_nuevo=stock_nuevo
            )
            db.session.add(ajuste)
            registrados += 1

        db.session.commit()
        if registrados == 1:
            flash(f'Maneo registrado y stock descontado exitosamente para {local_vecino}.', 'success')
        else:
            flash(f'Se registraron exitosamente {registrados} productos de maneo para {local_vecino}.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error al registrar el maneo: {str(e)}', 'danger')

    return redirect(url_for('admin_bp.maneos'))

@admin_bp.route('/maneos/facturar/<int:id>', methods=['POST'])
@login_required
def maneos_facturar(id):
    maneo = Maneo.query.get_or_404(id)
    origen = request.form.get('origen', '')
    cliente_redirect_id = request.form.get('cliente_id') or (maneo.cliente_id if maneo.cliente_id else None)

    def _redirigir():
        if origen == 'estado_cuenta' and cliente_redirect_id:
            return redirect(url_for('clientes_bp.estado_cuenta', id=cliente_redirect_id))
        return redirect(url_for('admin_bp.maneos'))

    if maneo.estado != 'PENDIENTE':
        flash('Este maneo ya fue resuelto.', 'warning')
        return _redirigir()
    
    # Determinar precios según variante o producto base
    if maneo.variante:
        precio_sugerido_ref = float(maneo.variante.precio_sugerido or maneo.producto.precio_sugerido or 0)
        precio_costo_ref = float(maneo.variante.precio_costo or maneo.producto.precio_costo or 0)
        precio_minimo_ref = float(maneo.variante.precio_minimo or maneo.producto.precio_minimo or 0)
    else:
        precio_sugerido_ref = float(maneo.producto.precio_sugerido or 0)
        precio_costo_ref = float(maneo.producto.precio_costo or 0)
        precio_minimo_ref = float(maneo.producto.precio_minimo or 0)

    try:
        raw_pv = request.form.get('precio_venta')
        precio_venta = float(raw_pv) if raw_pv and str(raw_pv).strip() else float(maneo.valor_unitario_calculado or precio_sugerido_ref)
    except (ValueError, TypeError):
        precio_venta = float(maneo.valor_unitario_calculado or precio_sugerido_ref)

    if 0 < precio_venta < 1000:
        precio_venta = precio_venta * 1000

    try:
        raw_cant = request.form.get('cantidad_vendida')
        cantidad_vendida = int(raw_cant) if raw_cant and str(raw_cant).strip() else maneo.cantidad
    except (ValueError, TypeError):
        cantidad_vendida = maneo.cantidad

    if cantidad_vendida <= 0 or cantidad_vendida > maneo.cantidad:
        flash(f'Operación rechazada: La cantidad vendida ({cantidad_vendida}) es inválida.', 'danger')
        return _redirigir()

    precio_limite = precio_costo_ref if current_user.rol == 'admin' else precio_minimo_ref

    # Si el maneo tiene un valor_fijo asignado y el cobro es >= a ese valor acordado, permitirlo (precio especial pactado con el local vecino)
    es_precio_pactado = (maneo.valor_fijo is not None and float(precio_venta) >= float(maneo.valor_fijo))

    if not es_precio_pactado and float(precio_venta) < float(precio_limite):
        flash(f'Operación rechazada: El precio ingresado (${precio_venta:,.0f}) es menor al límite autorizado para tu perfil de usuario (${precio_limite:,.0f}).', 'danger')
        return _redirigir()

    try:
        cantidad_no_vendida = maneo.cantidad - cantidad_vendida

        maneo.estado = 'FACTURADO'
        maneo.fecha_resolucion = obtener_hora_bogota()

        # Si hubo un cobro parcial, las unidades restantes vuelven al inventario
        if cantidad_no_vendida > 0:
            if maneo.variante:
                stock_anterior = maneo.variante.cantidad_stock
                maneo.variante.cantidad_stock += cantidad_no_vendida
                stock_nuevo = maneo.variante.cantidad_stock
            else:
                stock_anterior = maneo.producto.cantidad_stock
                maneo.producto.cantidad_stock += cantidad_no_vendida
                stock_nuevo = maneo.producto.cantidad_stock

            variante_label = f' [{maneo.variante.nombre_variante}]' if maneo.variante else ''
            ajuste_retorno = StockAdjustment(
                product_id=maneo.product_id,
                admin_id=current_user.id,
                tipo_movimiento=f'Dev. Parcial de Maneo ({maneo.local_vecino}){variante_label}',
                stock_anterior=stock_anterior,
                stock_nuevo=stock_nuevo
            )
            db.session.add(ajuste_retorno)
            
            # Actualizamos la cantidad del maneo a la realmente facturada para que el historial sea claro
            maneo.cantidad = cantidad_vendida

        metodo_pago_seleccionado = request.form.get('metodo_pago', 'efectivo')
        
        # Registrar la venta real del Maneo
        nueva_venta = Sale(
            vendedor_id=current_user.id,
            monto_total=(precio_venta * cantidad_vendida),
            metodo_pago=metodo_pago_seleccionado
        )
        db.session.add(nueva_venta)
        db.session.flush() # forzar DB a darnos un ID para nueva_venta
        
        detalle = SaleDetail(
            sale_id=nueva_venta.id,
            product_id=maneo.product_id,
            variant_id=maneo.variant_id,
            cantidad_vendida=cantidad_vendida,
            precio_venta_final=precio_venta
        )
        db.session.add(detalle)

        # Registrar el pago en SalePayment para consistencia con pagos mixtos
        pago = SalePayment(
            sale_id=nueva_venta.id,
            metodo_pago=metodo_pago_seleccionado,
            monto=(precio_venta * cantidad_vendida)
        )
        db.session.add(pago)
        
        db.session.commit()

        if cantidad_no_vendida > 0:
            flash(f'Maneo facturado parcialmente. Se registró la venta de ${precio_venta * cantidad_vendida:,.0f} y se devolvieron {cantidad_no_vendida} uds al inventario.', 'success')
        else:
            flash(f'Maneo facturado totalmente. Se registró la venta de ${precio_venta * cantidad_vendida:,.0f} en la caja.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error al facturar el maneo: {str(e)}', 'danger')

    return _redirigir()

@admin_bp.route('/maneos/devolver/<int:id>', methods=['POST'])
@login_required
def maneos_devolver(id):
    maneo = Maneo.query.get_or_404(id)
    origen = request.form.get('origen', '')
    cliente_redirect_id = request.form.get('cliente_id') or (maneo.cliente_id if maneo.cliente_id else None)

    def _redirigir():
        if origen == 'estado_cuenta' and cliente_redirect_id:
            return redirect(url_for('clientes_bp.estado_cuenta', id=cliente_redirect_id))
        return redirect(url_for('admin_bp.maneos'))

    if maneo.estado != 'PENDIENTE':
        flash('Este maneo ya fue resuelto.', 'warning')
        return _redirigir()

    try:
        raw_cant = request.form.get('cantidad_devuelta')
        cantidad_devuelta = int(raw_cant) if raw_cant and str(raw_cant).strip() else maneo.cantidad
    except (ValueError, TypeError):
        cantidad_devuelta = maneo.cantidad

    if cantidad_devuelta <= 0:
        flash('La cantidad a devolver debe ser mayor a 0.', 'danger')
        return _redirigir()

    if cantidad_devuelta > maneo.cantidad:
        flash(f'No puedes devolver más de {maneo.cantidad} unidades (las que están prestadas).', 'danger')
        return _redirigir()

    try:
        # Devolver stock a la variante o al producto base
        if maneo.variante:
            stock_anterior = maneo.variante.cantidad_stock
            maneo.variante.cantidad_stock += cantidad_devuelta
            stock_nuevo = maneo.variante.cantidad_stock
        else:
            stock_anterior = maneo.producto.cantidad_stock
            maneo.producto.cantidad_stock += cantidad_devuelta
            stock_nuevo = maneo.producto.cantidad_stock

        variante_label = f' [{maneo.variante.nombre_variante}]' if maneo.variante else ''

        # Registro en el Kardex del retorno
        ajuste = StockAdjustment(
            product_id=maneo.product_id,
            admin_id=current_user.id,
            tipo_movimiento=f'Devolución de Maneo ({maneo.local_vecino}){variante_label}',
            stock_anterior=stock_anterior,
            stock_nuevo=stock_nuevo
        )
        db.session.add(ajuste)

        # Determinar si es devolución total o parcial
        if cantidad_devuelta >= maneo.cantidad:
            # Devolución total: se cierra el maneo
            maneo.estado = 'DEVUELTO'
            maneo.fecha_resolucion = obtener_hora_bogota()
            db.session.commit()
            flash(f'Maneo cerrado. Se devolvieron {cantidad_devuelta} unidades al inventario.', 'success')
        else:
            # Devolución parcial: se reduce la cantidad y el maneo sigue PENDIENTE
            unidades_restantes = maneo.cantidad - cantidad_devuelta
            maneo.cantidad = unidades_restantes
            db.session.commit()
            flash(f'Devolución parcial registrada. Se devolvieron {cantidad_devuelta} uds al inventario. Quedan {unidades_restantes} uds pendientes de cobrar.', 'info')

    except Exception as e:
        db.session.rollback()
        flash(f'Error al procesar la devolución: {str(e)}', 'danger')

    return _redirigir()

@admin_bp.route('/balance-financiero', methods=['GET', 'POST'])
@login_required
@admin_required
def balance_financiero():
    if request.method == 'POST':
        fecha_inicio_str = request.form.get('fecha_inicio')
        fecha_fin_str = request.form.get('fecha_fin')
    else:
        fecha_inicio_str = request.args.get('fecha_inicio')
        fecha_fin_str = request.args.get('fecha_fin')

    hoy = obtener_hora_bogota()
    import calendar
    if not fecha_inicio_str or not fecha_fin_str:
        # Por defecto, el mes actual
        primer_dia = hoy.replace(day=1)
        ultimo_dia_mes = calendar.monthrange(hoy.year, hoy.month)[1]
        ultimo_dia = hoy.replace(day=ultimo_dia_mes)
        
        fecha_inicio_str = primer_dia.strftime('%Y-%m-%d')
        fecha_fin_str = ultimo_dia.strftime('%Y-%m-%d')

    from datetime import datetime, timedelta
    try:
        inicio_dt = datetime.strptime(fecha_inicio_str, '%Y-%m-%d')
        fin_dt = datetime.strptime(fecha_fin_str, '%Y-%m-%d')
        # Avanzamos límite al inicio del siguiente día matemáticamente
        fin_dt_query = fin_dt + timedelta(days=1)
    except ValueError:
        flash("Formato de fecha inválido.", "danger")
        return redirect(url_for('admin_bp.dashboard'))

    # 1. Ventas Totales
    ventas_query = Sale.query.filter(Sale.fecha_venta >= inicio_dt, Sale.fecha_venta < fin_dt_query).all()
    
    ventas_efectivo = sum(v.monto_total for v in ventas_query if v.metodo_pago == 'efectivo')
    ventas_transferencia = sum(v.monto_total for v in ventas_query if v.metodo_pago in ['transferencia', 'nequi', 'bancolombia', 'daviplata'])
    total_ingresos = ventas_efectivo + ventas_transferencia

    # 2. Costo de Mercancía Vendida (COGS)
    detalles_query = SaleDetail.query.join(Sale).filter(
        Sale.fecha_venta >= inicio_dt,
        Sale.fecha_venta < fin_dt_query
    ).all()
    
    costos_directos = Decimal('0.00')
    for d in detalles_query:
        if d.nombre_manual:
            # Producto manual prestado
            costos_directos += (d.precio_costo_manual or 0) * d.cantidad_vendida
        elif d.variant_id:
            # Producto con variante: Priorizar costo de variante, luego producto
            v = d.variante
            p = d.producto
            if v and p:
                costo_u = v.precio_costo if v.precio_costo is not None else (p.precio_costo or 0)
                costos_directos += Decimal(str(costo_u)) * d.cantidad_vendida
        elif d.product_id:
            # Producto base sin variante
            p = d.producto
            if p:
                costos_directos += (p.precio_costo or 0) * d.cantidad_vendida

    # 3. Costos Indirectos y Gastos Operativos
    gastos_query = Expense.query.filter(Expense.fecha_gasto >= inicio_dt, Expense.fecha_gasto < fin_dt_query).all()
    
    costos_indirectos = sum(g.monto for g in gastos_query if g.tipo_gasto == 'Costo Indirecto')
    gastos_operacionales = sum(g.monto for g in gastos_query if g.tipo_gasto == 'Gasto Diario')
    
    total_salidas = float(costos_directos) + float(costos_indirectos) + float(gastos_operacionales)
    balance_neto = float(total_ingresos) - total_salidas

    # 4. Desglose de Bodega y Cartera Mayorista (B2B)
    facturas_bodega_periodo = FacturaBodega.query.filter(
        FacturaBodega.fecha_subida >= inicio_dt,
        FacturaBodega.fecha_subida < fin_dt_query
    ).all()
    bodega_contado = sum((f.monto_total for f in facturas_bodega_periodo if f.modalidad == 'contado'), Decimal('0'))
    bodega_credito = sum((f.monto_total for f in facturas_bodega_periodo if f.modalidad == 'credito'), Decimal('0'))

    abonos_bodega_periodo = AbonoBodega.query.filter(
        AbonoBodega.fecha_abono >= inicio_dt,
        AbonoBodega.fecha_abono < fin_dt_query
    ).all()
    bodega_abonos = sum((a.monto for a in abonos_bodega_periodo), Decimal('0'))

    clientes_todos = Cliente.query.all()
    bodega_cartera_pendiente = sum((c.deuda_total for c in clientes_todos if c.deuda_total > 0), Decimal('0'))

    datos_financieros = {
        'ventas_efectivo': float(ventas_efectivo),
        'ventas_transferencia': float(ventas_transferencia),
        'total_ingresos': float(total_ingresos),
        'costos_directos': float(costos_directos),
        'costos_indirectos': float(costos_indirectos),
        'gastos_operacionales': float(gastos_operacionales),
        'total_salidas': total_salidas,
        'balance_neto': balance_neto,
        'bodega_contado': float(bodega_contado),
        'bodega_credito': float(bodega_credito),
        'bodega_abonos': float(bodega_abonos),
        'bodega_cartera_pendiente': float(bodega_cartera_pendiente)
    }

    return render_template(
        'admin/balance_reporte.html',
        fecha_inicio=fecha_inicio_str,
        fecha_fin=fecha_fin_str,
        fecha_generacion=hoy.strftime('%Y-%m-%d %H:%M'),
        datos=datos_financieros
    )

@admin_bp.route('/arqueo', methods=['GET'])
@login_required
@admin_required
def arqueo_caja():
    return redirect(url_for('arqueo_bp.nuevo'))

@admin_bp.route('/arqueo/cerrar', methods=['POST'])
@login_required
@admin_required
def cierre_caja():
    hoy = obtener_hora_bogota().date()
    
    # Prevent double closing
    if ArqueoCaja.query.filter(db.func.date(ArqueoCaja.fecha_arqueo) == hoy).first():
        flash('La caja ya fue cerrada el día de hoy.', 'warning')
        return redirect(url_for('admin_bp.arqueo_caja'))

    base_inicial = request.form.get('base_inicial', 0)
    gastos_dia = request.form.get('gastos_dia', 0)
    efectivo_fisico = request.form.get('efectivo_fisico', 0)
    observaciones_diferencia = request.form.get('observaciones_diferencia', '')

    # Re-calculate totals
    inicio_dia = datetime.combine(hoy, datetime.min.time())
    fin_dia = inicio_dia + timedelta(days=1)
    
    ventas = Sale.query.filter(Sale.fecha_venta >= inicio_dia, Sale.fecha_venta < fin_dia).all()
    total_efectivo = 0.0
    total_digital = 0.0
    for venta in ventas:
        for pago in venta.pagos:
            if pago.metodo_pago.lower() == 'efectivo':
                total_efectivo += float(pago.monto)
            else:
                total_digital += float(pago.monto)

    # Save to DB
    nuevo_arqueo = ArqueoCaja(
        vendedor_id=current_user.id,
        fecha_arqueo=hoy,
        base_inicial=float(base_inicial),
        gastos_del_dia=float(gastos_dia),
        total_efectivo_sistema=total_efectivo,
        total_transferencia_sistema=total_digital,
        efectivo_fisico_contado=float(efectivo_fisico),
        observaciones_diferencia=observaciones_diferencia.strip()
    )
    
    try:
        db.session.add(nuevo_arqueo)
        db.session.commit()
        flash('Caja cerrada exitosamente.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error al cerrar la caja: {str(e)}', 'danger')

    return redirect(url_for('admin_bp.arqueo_caja'))
