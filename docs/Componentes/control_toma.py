import tinytuya
import time

# --- 1. DATOS DEL DISPOSITIVO ---
# No se escriben aquí: salen de devices.json (que Git ignora), como en test_local.py.
DEVICE_ID = "ID_DEL_DISPOSITIVO"   # devices.json: "id"
LOCAL_KEY = "LOCAL_KEY"            # devices.json: "key"
IP = "IP_DEL_TOMACORRIENTE"        # devices.json: "ip"

# --- 2. CONEXIÓN AL DISPOSITIVO ---
print("🔌 Conectando al Tomacorriente VTA+...")
toma = tinytuya.OutletDevice(DEVICE_ID, IP, LOCAL_KEY)
toma.set_version(3.4) # Versión confirmada por tu escáner

# --- 3. MENÚ INTERACTIVO ---
def menu():
    while True:
        print("\n" + "="*30)
        print("PANEL DE CONTROL GLAMPING")
        print("="*30)
        print("1. Encender Toma")
        print("2. Apagar Toma")
        print("3. Consultar Estado (Consumo/Voltaje)")
        print("4. Salir")
        
        opcion = input("\n Elige una opción (1-4): ")
        
        if opcion == '1':
            print("Transmitiendo orden: ENCENDIDO...")
            toma.turn_on()
            print(" ¡Comando enviado!")
            
        elif opcion == '2':
            print("Transmitiendo orden: APAGADO...")
            toma.turn_off()
            print(" ¡Comando enviado!")
            
        elif opcion == '3':
            print("Consultando al dispositivo...")
            estado = toma.status()
            print("\n Respuesta del toma:")
            print(estado)
            
        elif opcion == '4':
            print("Cerrando panel de control... ¡Hasta pronto!")
            break
            
        else:
            print(" Opción no válida, intenta de nuevo.")
            
        time.sleep(1) # Pequeña pausa para no saturar la consola

# --- 4. EJECUCIÓN ---
if __name__ == "__main__":
    menu()