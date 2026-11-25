// Corre en el PC el mismo autotest que el comando "autotest" del ESP32.
// Ver firmware/pruebas_tuya/README.md para compilarlo.
#include <stdio.h>
#include "../domo_nodo/tuya_autotest.h"

static void imprimir(const char* linea) { puts(linea); }

int main() {
    return tuya::autotest(imprimir) == 0 ? 0 : 1;
}
