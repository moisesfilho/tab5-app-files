/**
 * @file main.c
 * @brief Aplicativo Gerenciador de Arquivos para Tab5 OS
 */

#include "tab5_sdk.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void app_init(void)
{
    tab5_system_log(2, "tab5_files", "Aplicativo Arquivos iniciado");
}

static void app_open_file(const char *path)
{
    if (path != NULL && path[0] != '\0') {
        char msg[128];
        snprintf(msg, sizeof(msg), "Navegando: %s", path);
        tab5_system_log(2, "tab5_files", msg);
    }
}

static void app_resume(void)
{
    tab5_system_log(2, "tab5_files", "Arquivos retomado");
}

static void app_pause(void)
{
    tab5_system_log(2, "tab5_files", "Arquivos pausado");
}

static void app_destroy(void)
{
    tab5_system_log(2, "tab5_files", "Arquivos finalizado");
}

TAB5_APP_EXPORT int main(int argc, char **argv)
{
    (void)argc;
    (void)argv;

    tab5_lifecycle_callbacks_t cbs = {
        .on_init = app_init,
        .on_resume = app_resume,
        .on_pause = app_pause,
        .on_destroy = app_destroy,
        .on_open_file = app_open_file,
    };

    tab5_lifecycle_register(&cbs);
    app_init();
    return 0;
}
