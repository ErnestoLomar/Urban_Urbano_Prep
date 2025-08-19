from datetime import datetime, timedelta
import re
from escpos.printer import Usb
import logging
from time import strftime
import time
from PyQt5.QtCore import QSettings
import variables_globales as vg
import sys
import subprocess

sys.path.insert(1, '/home/pi/Urban_Urbano/db')

from operadores import obtener_operador_por_UID
from ventas_queries import obtener_ultimo_folio_de_item_venta, obtener_total_de_ventas_por_folioviaje, obtener_total_de_aforos_digitales_por_folioviaje, obtener_total_saldo_digital_por_folioviaje, obtener_ultimo_folio_de_venta_digital
from asignaciones_queries import obtener_asignacion_por_folio_de_viaje, obtener_ultima_asignacion

try:

    def sumar_dos_horas(hora1, hora2):
        try:
            formato = "%H:%M:%S"
            lista = hora2.split(":")
            hora=int(lista[0])
            minuto=int(lista[1])
            segundo=int(lista[2])
            h1 = datetime.strptime(hora1, formato)
            dh = timedelta(hours=hora) 
            dm = timedelta(minutes=minuto)          
            ds = timedelta(seconds=segundo) 
            resultado1 =h1 + ds
            resultado2 = resultado1 + dm
            resultado = resultado2 + dh
            resultado=resultado.strftime(formato)
            return str(resultado)
        except Exception as e:
            print("pasaje.py, linea 151: "+str(e))
        
    def imprimir_boleto_normal_con_servicio(ultimo_folio_de_venta, fecha, hora, idUnidad, servicio, tramo, qr):
        try:
            nc='0x04c5'
            ns='0x126e'

            n_creador_hex = int(nc, 16)
            n_serie_hex = int(ns, 16)

            instancia_impresora = Usb(n_creador_hex, n_serie_hex, 0)
            fecha = str(strftime('%d-%m-%Y')).replace('/', '-')
            settings = QSettings('/home/pi/Urban_Urbano/ventanas/settings.ini', QSettings.IniFormat)
            instancia_impresora.set(align='center')
            logging.info("Impresora encontrada")
            instancia_impresora.set(align='center')                                                                    
            instancia_impresora.text(f"Folio: {(ultimo_folio_de_venta)}            {fecha} {hora}\n")
            instancia_impresora.text(f"Unidad: {idUnidad}       IMPORTE {qr[6]}:  $ {0}\n")
            instancia_impresora.text(f"Servicio: {servicio}\n")
            tramo_servicio_actual = str(str(tramo).split("-")[0]) + "-" + str(str(servicio).split("-")[2])
            instancia_impresora.text(f"Tramo: {tramo_servicio_actual}\n")
            tipo_de_pasajero = str(qr[6]).lower()
            # Actualizamos el total de folios en el resumen (ticket) de liquidación dependiendo del tipo de pasajero                                      
            if tipo_de_pasajero != "normal":
                
                if tipo_de_pasajero == "estudiante":
                    # Si el pasajero es estudiante actualizamos los datos del settings de info_estudiantes
                    incremento_pasajero = float(settings.value('info_estudiantes').split(",")[0]) + 1
                    incremento_cantidad = float(settings.value('info_estudiantes').split(",")[1])
                    settings.setValue('info_estudiantes', f"{int(incremento_pasajero)},{incremento_cantidad}")
                    
                elif tipo_de_pasajero == "menor":
                    # Si el pasajero es menor actualizamos los datos del settings de info_chicos
                    incremento_pasajero = float(settings.value('info_chicos').split(",")[0]) + 1
                    incremento_cantidad = float(settings.value('info_chicos').split(",")[1])
                    settings.setValue('info_chicos', f"{int(incremento_pasajero)},{incremento_cantidad}")
                    
                elif tipo_de_pasajero == "mayor":
                    # Si el pasajero es mayor actualizamos los datos del settings de info_ad_mayores
                    incremento_pasajero = float(settings.value('info_ad_mayores').split(",")[0]) + 1
                    incremento_cantidad = float(settings.value('info_ad_mayores').split(",")[1])
                    settings.setValue('info_ad_mayores', f"{int(incremento_pasajero)},{incremento_cantidad}")
            else:
                incremento_pasajero = float(settings.value('info_normales').split(",")[0]) + 1
                incremento_cantidad = float(settings.value('info_normales').split(",")[1])
                settings.setValue('info_normales', f"{int(incremento_pasajero)},{incremento_cantidad}")
            instancia_impresora.cut()
            time.sleep(1)
            return True
        except Exception as e:
            print("Sucedio algo al imprimir ticket normal con servicio: "+str(e))
            logging.info(e)
            return False
        
    def imprimir_boleto_normal_sin_servicio(ultimo_folio_de_venta, fecha, hora, idUnidad, tramo, qr):
        try:
            nc='0x04c5'
            ns='0x126e'

            n_creador_hex = int(nc, 16)
            n_serie_hex = int(ns, 16)

            instancia_impresora = Usb(n_creador_hex, n_serie_hex, 0)
            fecha = str(strftime('%d-%m-%Y')).replace('/', '-')
            hora_actual = strftime('%H:%M:%S')
            settings = QSettings('/home/pi/Urban_Urbano/ventanas/settings.ini', QSettings.IniFormat)
            instancia_impresora.set(align='center')                                                                    
            instancia_impresora.text(f"Folio: {(ultimo_folio_de_venta)}            {fecha} {hora}\n")
            instancia_impresora.text(f"Unidad: {idUnidad}       IMPORTE {qr[6]}:  $ {0}\n")
            instancia_impresora.text(f"Aparentemente no estas en el servicio correcto\n")
            destino_del_qr = str(str(tramo).split("-")[1])
            instancia_impresora.text(f"No se encontro el destino {destino_del_qr}\n")
            tipo_de_pasajero = str(qr[6]).lower()
            # Actualizamos el total de folios en el resumen (ticket) de liquidación dependiendo del tipo de pasajero                                      
            if tipo_de_pasajero != "normal":
                
                if tipo_de_pasajero == "estudiante":
                    # Si el pasajero es estudiante actualizamos los datos del settings de info_estudiantes
                    incremento_pasajero = float(settings.value('info_estudiantes').split(",")[0]) + 1
                    incremento_cantidad = float(settings.value('info_estudiantes').split(",")[1])
                    settings.setValue('info_estudiantes', f"{int(incremento_pasajero)},{incremento_cantidad}")
                    
                elif tipo_de_pasajero == "menor":
                    # Si el pasajero es menor actualizamos los datos del settings de info_chicos
                    incremento_pasajero = float(settings.value('info_chicos').split(",")[0]) + 1
                    incremento_cantidad = float(settings.value('info_chicos').split(",")[1])
                    settings.setValue('info_chicos', f"{int(incremento_pasajero)},{incremento_cantidad}")
                    
                elif tipo_de_pasajero == "mayor":
                    # Si el pasajero es mayor actualizamos los datos del settings de info_ad_mayores
                    incremento_pasajero = float(settings.value('info_ad_mayores').split(",")[0]) + 1
                    incremento_cantidad = float(settings.value('info_ad_mayores').split(",")[1])
                    settings.setValue('info_ad_mayores', f"{int(incremento_pasajero)},{incremento_cantidad}")
            else:
                incremento_pasajero = float(settings.value('info_normales').split(",")[0]) + 1
                incremento_cantidad = float(settings.value('info_normales').split(",")[1])
                settings.setValue('info_normales', f"{int(incremento_pasajero)},{incremento_cantidad}")
            instancia_impresora.cut()
            time.sleep(1)
            return True
        except Exception as e:
            print("Sucedio algo al imprimir ticket normal sin servicio: "+str(e))
            logging.info(e)
            return False

    def imprimir_boleto_normal_pasaje(folio, fecha, hora, unidad, tipo_pasajero, importe, servicio, tramo):
        try:
            nc='0x04c5'
            ns='0x126e'

            n_creador_hex = int(nc, 16)
            n_serie_hex = int(ns, 16)

            instancia_impresora = Usb(n_creador_hex, n_serie_hex, 0)
            fecha = str(strftime('%d-%m-%Y')).replace('/', '-')
            instancia_impresora.set(align='center')
            logging.info("Impresora encontrada")
            instancia_impresora.text(f"Folio: {folio}            {fecha} {hora}\n")
            instancia_impresora.text(f"Unidad: {unidad}       IMPORTE {tipo_pasajero}:  $ {importe}\n")
            instancia_impresora.text(f"Servicio: {servicio}\n")
            instancia_impresora.text(f"Tramo: {tramo}\n")
            instancia_impresora.cut()
            time.sleep(1)
            return True
        except Exception as e:
            print(e)
            logging.info(e)
            return False
    
    def imprimir_boleto_con_qr_pasaje(folio, fecha, hora, unidad, tipo_pasajero, importe, servicio, tramo, servicio_o_transbordo):
        try:
            nc='0x04c5'
            ns='0x126e'

            n_creador_hex = int(nc, 16)
            n_serie_hex = int(ns, 16)

            instancia_impresora = Usb(n_creador_hex, n_serie_hex, 0)
            fecha = str(strftime('%d-%m-%Y')).replace('/', '-')
            instancia_impresora.set(align='center')
            logging.info("Impresora encontrada")
            instancia_impresora.text(f"Folio: {folio}            {fecha} {hora}\n")
            instancia_impresora.text(f"Unidad: {unidad}       IMPORTE {tipo_pasajero}:  $ {importe}\n")
            instancia_impresora.text(f"Servicio: {servicio}\n")
            instancia_impresora.text(f"Tramo: {tramo}\n")
            if 'NE' in servicio_o_transbordo[8]:
                unidad_a_transbordar = str(str(servicio_o_transbordo[7]).split("_")[0]).replace("'", "")
                instancia_impresora.text(f"Transbordar unidad en: {unidad_a_transbordar}\n")
                estimado = "02:00:00"
                hora_antes_de = sumar_dos_horas(hora, estimado)
                instancia_impresora.text(f"Antes de {fecha} {hora_antes_de}\n")
                instancia_impresora.qr(f"{fecha},{hora_antes_de},{unidad},{importe},{servicio},{tramo},{tipo_pasajero},{'st'},{unidad_a_transbordar}",0, 5)
                instancia_impresora.cut()
                time.sleep(1)
                return True
            else:
                unidad_a_transbordar1 = str(str(servicio_o_transbordo[7]).split("_")[0]).replace("'", "")
                unidad_a_transbordar2 = str(str(servicio_o_transbordo[8]).split("_")[0]).replace("'", "")
                instancia_impresora.text(f"Transbordar unidad en: {unidad_a_transbordar1}\n")
                instancia_impresora.text(f"Luego transbordar unidad en: {unidad_a_transbordar2}\n")
                estimado = "02:00:00"
                hora_antes_de = sumar_dos_horas(hora, estimado)
                instancia_impresora.text(f"Antes de {fecha} {hora_antes_de}\n")
                instancia_impresora.qr(f"{fecha},{hora_antes_de},{unidad},{importe},{servicio},{tramo},{tipo_pasajero},{'ct'},{unidad_a_transbordar1},{unidad_a_transbordar2}",0, 5)
                instancia_impresora.cut()
                time.sleep(1)
                return True
        except Exception as e:
            print(e)
            logging.info(e)
            return False
        
    def imprimir_ticket_de_corte(idUnidad, imprimir):
        try:
            settings = QSettings('/home/pi/Urban_Urbano/ventanas/settings.ini', QSettings.IniFormat)
            fecha = str(vg.fecha_actual).replace('/', '-') if vg.fecha_actual else subprocess.check_output(['date', '+%d-%m-%Y']).decode().strip()
            hora_actual = vg.hora_actual

            total_a_liquidar_bd = 0.0
            total_de_boletos_db = []
            total_boletos_digitales = 0
            total_digital_liquidar = 0.0
            ultima_venta_bd = obtener_ultimo_folio_de_item_venta()
            ultima_venta_bd_digital = obtener_ultimo_folio_de_venta_digital()
            logging.info(f"Última venta en la base de datos: {ultima_venta_bd}")

            folio_de_viaje = settings.value('folio_de_viaje', '')
            if folio_de_viaje:
                total_boletos_digitales = obtener_total_de_aforos_digitales_por_folioviaje(folio_de_viaje)
                total_digital_liquidar = obtener_total_saldo_digital_por_folioviaje(folio_de_viaje)
                total_de_boletos_db = obtener_total_de_ventas_por_folioviaje(folio_de_viaje)
            elif vg.folio_asignacion:
                total_boletos_digitales = obtener_total_de_aforos_digitales_por_folioviaje(vg.folio_asignacion)
                total_digital_liquidar = obtener_total_saldo_digital_por_folioviaje(vg.folio_asignacion)
                total_de_boletos_db = obtener_total_de_ventas_por_folioviaje(vg.folio_asignacion)
            
            total_folios_aforo = (
                int(settings.value('info_estudiantes').split(',')[0]) +
                int(settings.value('info_normales').split(',')[0]) +
                int(settings.value('info_chicos').split(',')[0]) +
                int(settings.value('info_ad_mayores').split(',')[0])
            )
            
            total_folios_aforos = total_folios_aforo + total_boletos_digitales

            if total_de_boletos_db:
                logging.info(f"Boletos en base de datos: {len(total_de_boletos_db)}")
                for boleto in total_de_boletos_db:
                    total_a_liquidar_bd += float(boleto[11])

                if ultima_venta_bd and len(total_de_boletos_db) != total_folios_aforo:
                    logging.info("Boletos en BD no coinciden con aforo. Se actualiza total aforo.")
                    total_folios_aforo = len(total_de_boletos_db)
            else:
                logging.info("No hay ventas registradas.")
                ultima_venta_bd = [0, 0]
                total_folios_aforo = 0
            
            total_liquidar_suma = total_a_liquidar_bd + total_digital_liquidar

            try:
                trama_dos_del_viaje = obtener_ultima_asignacion()
                logging.info(f"Última asignación: {trama_dos_del_viaje}")
            except Exception as e:
                logging.error(f"Error al obtener última asignación: {e}")
                trama_dos_del_viaje = [""] * 7

            instancia_impresora = inicializar_impresora()
            imprimir_tickets(instancia_impresora, settings, idUnidad, trama_dos_del_viaje, fecha, hora_actual, ultima_venta_bd, total_folios_aforo, total_a_liquidar_bd, total_boletos_digitales, total_digital_liquidar, ultima_venta_bd_digital, total_liquidar_suma)
            return True
        except Exception as e:
            print("Error en imprimir_ticket_de_corte: ",e)
            logging.error(f"Error en imprimir_ticket_de_corte: {e}")
            return not imprimir


    def inicializar_impresora():
        nc = '0x04c5'
        ns = '0x126e'
        return Usb(int(nc, 16), int(ns, 16), 0)


    def imprimir_tickets(impresora, settings, idUnidad, asignacion, fecha, hora, ultima_venta, total_folios, total_liquidar, total_boletos_digitales, total_digital_liquidar, ultima_venta_bd_digital, total_liquidar_suma):
        impresora.set(align='center')
        for _ in range(2):
            # General
            impresora.text("RESUMEN GENERAL\n")
            impresora.text(f"Fv: {asignacion[6]}  Sw: {vg.version_del_software}\n")
            impresora.text(f"Unidad: {idUnidad}    Serv: {settings.value('servicio')}\n")
            impresora.text(f"Ultimo folio de pago con efectivo: {ultima_venta[1]}\n\n")
            
            impresora.text("RESUMEN DE VENTAS CON EFECTIVO\n")
            impresora.text(f"Total a liquidar efectivo: $ {total_liquidar}\n")
            impresora.text(f"Total de folios efectivo: {total_folios}\n")
            imprimir_clasificacion_boletos(impresora, settings)
            impresora.text("\n")
            
            impresora.text("RESUMEN DE VENTAS DIGITALES\n")
            impresora.text(f"Total digital: ${total_digital_liquidar}\n")
            impresora.text(f"Total de folios digitales: {total_boletos_digitales}\n")
            imprimir_clasificacion_boletos_digitales(impresora, settings)
            impresora.text("\n")

            # Inicio
            impresora.text("INICIO DE VIAJE\n")
            impresora.text(f"Fecha y hora: {asignacion[4]} {asignacion[5]}\n")
            impresora.text(f"Quien abrio: {obtener_nombre_operador(settings, vg.nombre_de_operador_inicio, vg.numero_de_operador_inicio, vg.csn_chofer, 'inicio')}\n\n")

            # Fin
            impresora.text("FIN DE VIAJE\n")
            impresora.text(f"Fecha y hora (impresion): {fecha} {hora}\n")
            impresora.text(f"Quien cerro: {obtener_nombre_operador(settings, vg.nombre_de_operador_final, vg.numero_de_operador_final, vg.csn_chofer, 'final')}\n")
            impresora.cut()


    def imprimir_clasificacion_boletos(impresora, settings):
        for clave in ['info_estudiantes', 'info_normales', 'info_chicos', 'info_ad_mayores']:
            nombre = {
                'info_estudiantes': "Estud",
                'info_normales': "Normal",
                'info_chicos': "Menor",
                'info_ad_mayores': "Ad.May"
            }[clave]
            cantidad, monto = settings.value(clave).split(',')
            impresora.text(f"{nombre}:       {cantidad}  $       {monto}\n")
            
    def imprimir_clasificacion_boletos_digitales(impresora, settings):
        for clave in ['info_estudiantes_digital', 'info_normales_digital', 'info_chicos_digital', 'info_ad_mayores_digital']:
            nombre = {
                'info_estudiantes_digital': "Estud",
                'info_normales_digital': "Normal",
                'info_chicos_digital': "Menor",
                'info_ad_mayores_digital': "Ad.May"
            }[clave]
            cantidad, monto = settings.value(clave).split(',')
            impresora.text(f"{nombre}:       {cantidad}  $       {monto}\n")
        
            
    def obtener_nombre_operador(settings, nombre, numero, csn, tipo):
        operador = None

        nombre_setting = settings.value(f'nombre_de_operador_{tipo}')
        numero_setting = settings.value(f'numero_de_operador_{tipo}')
        csn_setting = settings.value('csn_chofer')

        if nombre:
            if numero:
                return f"{numero} {nombre}"
            elif numero_setting:
                return f"{numero_setting} {nombre}"
            elif csn_setting:
                operador = obtener_operador_por_UID(csn_setting)
                if operador:
                    return f"{operador[1]} {operador[2]}"
                elif csn:
                    operador = obtener_operador_por_UID(csn)
                    if operador:
                        return f"{operador[1]} {operador[2]}"
                return nombre
            elif csn:
                operador = obtener_operador_por_UID(csn)
                if operador:
                    return f"{operador[1]} {operador[2]}"
                return nombre
            return nombre
        elif nombre_setting:
            if numero:
                return f"{numero} {nombre_setting}"
            elif numero_setting:
                return f"{numero_setting} {nombre_setting}"
            elif csn_setting:
                operador = obtener_operador_por_UID(csn_setting)
                if operador:
                    return f"{operador[1]} {operador[2]}"
                elif csn:
                    operador = obtener_operador_por_UID(csn)
                    if operador:
                        return f"{operador[1]} {operador[2]}"
                return nombre_setting
            elif csn:
                operador = obtener_operador_por_UID(csn)
                if operador:
                    return f"{operador[1]} {operador[2]}"
                return nombre_setting
            return nombre_setting
        elif numero:
            if csn_setting:
                operador = obtener_operador_por_UID(csn_setting)
                if operador:
                    return f"{operador[1]} {operador[2]}"
                elif csn:
                    operador = obtener_operador_por_UID(csn)
                    if operador:
                        return f"{operador[1]} {operador[2]}"
                return numero
            elif csn:
                operador = obtener_operador_por_UID(csn)
                if operador:
                    return f"{operador[1]} {operador[2]}"
                return numero
            return numero
        elif numero_setting:
            if csn_setting:
                operador = obtener_operador_por_UID(csn_setting)
                if operador:
                    return f"{operador[1]} {operador[2]}"
                elif csn:
                    operador = obtener_operador_por_UID(csn)
                    if operador:
                        return f"{operador[1]} {operador[2]}"
                return numero_setting
            elif csn:
                operador = obtener_operador_por_UID(csn)
                if operador:
                    return f"{operador[1]} {operador[2]}"
                return numero_setting
            return numero_setting
        elif csn_setting:
            operador = obtener_operador_por_UID(csn_setting)
            if operador:
                return f"{operador[1]} {operador[2]}"
        elif csn:
            operador = obtener_operador_por_UID(csn)
            if operador:
                return f"{operador[1]} {operador[2]}"
        return "----------"
except Exception as e:
    print("No hubo comunicacion con impresora")