// tuya_cripto.h con el mbedtls que trae el core ESP32 de Arduino (3.x).
#include "tuya_cripto.h"

#include <string.h>
#include "mbedtls/aes.h"
#include "mbedtls/gcm.h"
#include "mbedtls/md.h"

namespace tuya {

bool aesEcb(bool cifrar, const uint8_t clave[16], const uint8_t* entrada, uint8_t* salida, size_t n) {
    if (n % 16) return false;
    mbedtls_aes_context aes;
    mbedtls_aes_init(&aes);
    int r = cifrar ? mbedtls_aes_setkey_enc(&aes, clave, 128) : mbedtls_aes_setkey_dec(&aes, clave, 128);
    for (size_t i = 0; r == 0 && i < n; i += 16)
        r = mbedtls_aes_crypt_ecb(&aes, cifrar ? MBEDTLS_AES_ENCRYPT : MBEDTLS_AES_DECRYPT, entrada + i, salida + i);
    mbedtls_aes_free(&aes);
    return r == 0;
}

bool gcmCifrar(const uint8_t clave[16], const uint8_t iv[12], const uint8_t* aad, size_t naad,
               const uint8_t* entrada, size_t n, uint8_t* salida, uint8_t tag[16]) {
    mbedtls_gcm_context gcm;
    mbedtls_gcm_init(&gcm);
    int r = mbedtls_gcm_setkey(&gcm, MBEDTLS_CIPHER_ID_AES, clave, 128);
    if (r == 0) r = mbedtls_gcm_crypt_and_tag(&gcm, MBEDTLS_GCM_ENCRYPT, n, iv, 12, aad, naad, entrada, salida, 16, tag);
    mbedtls_gcm_free(&gcm);
    return r == 0;
}

bool gcmDescifrar(const uint8_t clave[16], const uint8_t iv[12], const uint8_t* aad, size_t naad,
                  const uint8_t* entrada, size_t n, uint8_t* salida, const uint8_t tag[16]) {
    mbedtls_gcm_context gcm;
    mbedtls_gcm_init(&gcm);
    int r = mbedtls_gcm_setkey(&gcm, MBEDTLS_CIPHER_ID_AES, clave, 128);
    // auth_decrypt compara el tag en tiempo constante y borra la salida si no coincide.
    if (r == 0) r = mbedtls_gcm_auth_decrypt(&gcm, n, iv, 12, aad, naad, tag, 16, entrada, salida);
    mbedtls_gcm_free(&gcm);
    return r == 0;
}

bool hmacSha256(const uint8_t* clave, size_t nclave, const uint8_t* datos, size_t n, uint8_t salida[32]) {
    const mbedtls_md_info_t* info = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
    return info && mbedtls_md_hmac(info, clave, nclave, datos, n, salida) == 0;
}

}  // namespace tuya
