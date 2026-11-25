#pragma once
// Prueba del protocolo Tuya contra los vectores de tinytuya (tuya_vectores.h).
// Sin red ni dispositivos: solo arma y abre tramas. La corre el comando
// "autotest" del monitor serie y, en el PC, firmware/pruebas_tuya/.
// Nunca imprime claves.

namespace tuya {

// imprimir recibe cada línea, sin salto final. Devuelve el número de fallos (0 = todo OK).
int autotest(void (*imprimir)(const char* linea));

}  // namespace tuya
