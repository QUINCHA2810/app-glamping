#pragma once
// ============================================================================
//  Las cuatro operaciones de cifrado que usa el protocolo Tuya y el
//  descubrimiento UDP. En el ESP32 las implementa tuya_cripto_mbedtls.cpp con
//  el mbedtls del core (aes.h, gcm.h, md.h); en el PC, las pruebas de
//  firmware/pruebas_tuya/ las implementan con otra librería, para poder correr
//  los mismos vectores sin la placa.
// ============================================================================

#include <stddef.h>
#include <stdint.h>

namespace tuya {

// AES-128-ECB, bloque a bloque y sin relleno: n debe ser múltiplo de 16.
// entrada y salida pueden ser el mismo buffer.
bool aesEcb(bool cifrar, const uint8_t clave[16], const uint8_t* entrada, uint8_t* salida, size_t n);

// AES-128-GCM con IV de 12 bytes y tag de 16. aad puede ser nullptr con naad 0.
bool gcmCifrar(const uint8_t clave[16], const uint8_t iv[12], const uint8_t* aad, size_t naad,
               const uint8_t* entrada, size_t n, uint8_t* salida, uint8_t tag[16]);
// false si el tag no coincide (y entonces `salida` no sirve).
bool gcmDescifrar(const uint8_t clave[16], const uint8_t iv[12], const uint8_t* aad, size_t naad,
                  const uint8_t* entrada, size_t n, uint8_t* salida, const uint8_t tag[16]);

bool hmacSha256(const uint8_t* clave, size_t nclave, const uint8_t* datos, size_t n, uint8_t salida[32]);

}  // namespace tuya
