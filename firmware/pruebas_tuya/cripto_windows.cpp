// tuya_cripto.h para el PC (Windows), con la API CNG (bcrypt.dll), que ya viene
// en Windows. Solo para las pruebas: en el ESP32 se usa tuya_cripto_mbedtls.cpp.
#include "../domo_nodo/tuya_cripto.h"

#include <windows.h>
#include <bcrypt.h>
#include <string.h>

#ifndef STATUS_SUCCESS
#define STATUS_SUCCESS ((NTSTATUS)0x00000000L)
#endif

namespace tuya {

namespace {

// Clave AES con el modo de encadenamiento pedido. false si algo falla.
bool claveAes(const wchar_t* modo, const uint8_t clave[16], BCRYPT_ALG_HANDLE* alg, BCRYPT_KEY_HANDLE* k) {
    if (BCryptOpenAlgorithmProvider(alg, BCRYPT_AES_ALGORITHM, nullptr, 0) != STATUS_SUCCESS) return false;
    if (BCryptSetProperty(*alg, BCRYPT_CHAINING_MODE, (PUCHAR)modo, (ULONG)((wcslen(modo) + 1) * sizeof(wchar_t)), 0) != STATUS_SUCCESS ||
        BCryptGenerateSymmetricKey(*alg, k, nullptr, 0, (PUCHAR)clave, 16, 0) != STATUS_SUCCESS) {
        BCryptCloseAlgorithmProvider(*alg, 0);
        return false;
    }
    return true;
}

void cerrar(BCRYPT_ALG_HANDLE alg, BCRYPT_KEY_HANDLE k) {
    BCryptDestroyKey(k);
    BCryptCloseAlgorithmProvider(alg, 0);
}

bool gcm(bool cifrar, const uint8_t clave[16], const uint8_t iv[12], const uint8_t* aad, size_t naad,
         const uint8_t* entrada, size_t n, uint8_t* salida, uint8_t* tag) {
    BCRYPT_ALG_HANDLE alg;
    BCRYPT_KEY_HANDLE k;
    if (!claveAes(BCRYPT_CHAIN_MODE_GCM, clave, &alg, &k)) return false;
    BCRYPT_AUTHENTICATED_CIPHER_MODE_INFO info;
    BCRYPT_INIT_AUTH_MODE_INFO(info);
    info.pbNonce = (PUCHAR)iv;
    info.cbNonce = 12;
    info.pbAuthData = (PUCHAR)aad;
    info.cbAuthData = (ULONG)naad;
    info.pbTag = tag;
    info.cbTag = 16;
    ULONG escrito = 0;
    NTSTATUS r = cifrar
        ? BCryptEncrypt(k, (PUCHAR)entrada, (ULONG)n, &info, nullptr, 0, salida, (ULONG)n, &escrito, 0)
        : BCryptDecrypt(k, (PUCHAR)entrada, (ULONG)n, &info, nullptr, 0, salida, (ULONG)n, &escrito, 0);
    cerrar(alg, k);
    return r == STATUS_SUCCESS && escrito == n;
}

}  // namespace

bool aesEcb(bool cifrar, const uint8_t clave[16], const uint8_t* entrada, uint8_t* salida, size_t n) {
    if (n % 16) return false;
    BCRYPT_ALG_HANDLE alg;
    BCRYPT_KEY_HANDLE k;
    if (!claveAes(BCRYPT_CHAIN_MODE_ECB, clave, &alg, &k)) return false;
    ULONG escrito = 0;
    NTSTATUS r = cifrar
        ? BCryptEncrypt(k, (PUCHAR)entrada, (ULONG)n, nullptr, nullptr, 0, salida, (ULONG)n, &escrito, 0)
        : BCryptDecrypt(k, (PUCHAR)entrada, (ULONG)n, nullptr, nullptr, 0, salida, (ULONG)n, &escrito, 0);
    cerrar(alg, k);
    return r == STATUS_SUCCESS && escrito == n;
}

bool gcmCifrar(const uint8_t clave[16], const uint8_t iv[12], const uint8_t* aad, size_t naad,
               const uint8_t* entrada, size_t n, uint8_t* salida, uint8_t tag[16]) {
    return gcm(true, clave, iv, aad, naad, entrada, n, salida, tag);
}

bool gcmDescifrar(const uint8_t clave[16], const uint8_t iv[12], const uint8_t* aad, size_t naad,
                  const uint8_t* entrada, size_t n, uint8_t* salida, const uint8_t tag[16]) {
    uint8_t t[16];
    memcpy(t, tag, 16);
    return gcm(false, clave, iv, aad, naad, entrada, n, salida, t);
}

bool hmacSha256(const uint8_t* clave, size_t nclave, const uint8_t* datos, size_t n, uint8_t salida[32]) {
    BCRYPT_ALG_HANDLE alg;
    BCRYPT_HASH_HANDLE h;
    if (BCryptOpenAlgorithmProvider(&alg, BCRYPT_SHA256_ALGORITHM, nullptr, BCRYPT_ALG_HANDLE_HMAC_FLAG) != STATUS_SUCCESS) return false;
    bool ok = BCryptCreateHash(alg, &h, nullptr, 0, (PUCHAR)clave, (ULONG)nclave, 0) == STATUS_SUCCESS;
    if (ok) {
        ok = BCryptHashData(h, (PUCHAR)datos, (ULONG)n, 0) == STATUS_SUCCESS &&
             BCryptFinishHash(h, salida, 32, 0) == STATUS_SUCCESS;
        BCryptDestroyHash(h);
    }
    BCryptCloseAlgorithmProvider(alg, 0);
    return ok;
}

}  // namespace tuya
