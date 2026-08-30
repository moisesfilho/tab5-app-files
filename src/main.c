/**
 * @file main.c
 * @brief Aplicativo Gerenciador de Arquivos para Tab5 OS
 */

#include "tab5_sdk.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <dirent.h>
#include <sys/stat.h>
#include <unistd.h>
#include <time.h>

#define MAX_ENTRIES 128
#define MAX_PATH_LEN 512

typedef struct {
    char name[128];
    bool is_dir;
    size_t size;
    time_t mtime;
} file_item_t;

static char s_current_dir[MAX_PATH_LEN] = "/sdcard";
static file_item_t s_entries[MAX_ENTRIES];
static int s_entry_count = 0;
static bool s_show_hidden = false;

static void format_size(size_t size, bool is_dir, char *buf, size_t buf_len)
{
    if (is_dir) {
        snprintf(buf, buf_len, "<DIR>");
        return;
    }
    if (size < 1024) {
        snprintf(buf, buf_len, "%u B", (unsigned int)size);
    } else if (size < 1024 * 1024) {
        snprintf(buf, buf_len, "%.1f KB", (float)size / 1024.0f);
    } else {
        snprintf(buf, buf_len, "%.1f MB", (float)size / (1024.0f * 1024.0f));
    }
}

static int compare_entries(const void *a, const void *b)
{
    const file_item_t *ea = (const file_item_t *)a;
    const file_item_t *eb = (const file_item_t *)b;
    if (ea->is_dir != eb->is_dir) {
        return ea->is_dir ? -1 : 1; // Diretórios primeiro
    }
    return strcmp(ea->name, eb->name);
}

static void update_files_view(void)
{
    char title[MAX_PATH_LEN + 32];
    snprintf(title, sizeof(title), "Arquivos - %s", s_current_dir);
    tab5_ui_app_bar_set_title(title);

    char *buf = (char *)malloc(16384);
    if (buf == NULL) {
        return;
    }

    size_t offset = 0;
    offset += snprintf(buf + offset, 16384 - offset,
                       "====================================================\n"
                       " Diretorio: %s\n"
                       "====================================================\n"
                       " %-4s %-24s %-10s %s\n"
                       "----------------------------------------------------\n",
                       s_current_dir, "TIPO", "NOME", "TAMANHO", "MODIFICACAO");

    if (s_entry_count == 0) {
        offset += snprintf(buf + offset, 16384 - offset, " (Nenhum arquivo ou pasta encontrado)\n");
    } else {
        for (int i = 0; i < s_entry_count && offset < 15000; i++) {
            char size_str[32];
            format_size(s_entries[i].size, s_entries[i].is_dir, size_str, sizeof(size_str));

            char date_str[32];
            if (s_entries[i].mtime > 0) {
                struct tm tm_info;
                localtime_r(&s_entries[i].mtime, &tm_info);
                strftime(date_str, sizeof(date_str), "%d/%m/%Y %H:%M", &tm_info);
            } else {
                snprintf(date_str, sizeof(date_str), "--/--/---- --:--");
            }

            offset += snprintf(buf + offset, 16384 - offset,
                               " %-4s %-24.24s %-10s %s\n",
                               s_entries[i].is_dir ? "[DIR]" : "     ",
                               s_entries[i].name,
                               size_str,
                               date_str);
        }
    }

    offset += snprintf(buf + offset, 16384 - offset,
                       "----------------------------------------------------\n"
                       " Total: %d item(ns)\n", s_entry_count);

    tab5_ui_obj_t ta = tab5_ui_get_main_textarea();
    if (ta != NULL) {
        tab5_ui_textarea_set_text(ta, buf);
    }
    free(buf);
}

static void load_directory(const char *path)
{
    if (path == NULL || path[0] == '\0') {
        path = "/sdcard";
    }
    strncpy(s_current_dir, path, sizeof(s_current_dir) - 1);
    s_entry_count = 0;

    DIR *dir = opendir(s_current_dir);
    if (dir != NULL) {
        struct dirent *entry;
        while ((entry = readdir(dir)) != NULL && s_entry_count < MAX_ENTRIES) {
            if (strcmp(entry->d_name, ".") == 0 || strcmp(entry->d_name, "..") == 0) {
                continue;
            }
            if (!s_show_hidden && entry->d_name[0] == '.') {
                continue;
            }

            char full_path[MAX_PATH_LEN];
            snprintf(full_path, sizeof(full_path), "%s/%s", s_current_dir, entry->d_name);

            struct stat st;
            memset(&st, 0, sizeof(st));
            stat(full_path, &st);

            file_item_t *item = &s_entries[s_entry_count++];
            strncpy(item->name, entry->d_name, sizeof(item->name) - 1);
            item->is_dir = S_ISDIR(st.st_mode);
            item->size = (size_t)st.st_size;
            item->mtime = st.st_mtime;
        }
        closedir(dir);
    }

    if (s_entry_count > 0) {
        qsort(s_entries, s_entry_count, sizeof(file_item_t), compare_entries);
    }

    if (strcmp(s_current_dir, "/sdcard") != 0 && strcmp(s_current_dir, "/sdcard/") != 0) {
        if (s_entry_count < MAX_ENTRIES) {
            // Insere .. no início
            for (int i = s_entry_count; i > 0; i--) {
                s_entries[i] = s_entries[i - 1];
            }
            strcpy(s_entries[0].name, "..");
            s_entries[0].is_dir = true;
            s_entries[0].size = 0;
            s_entries[0].mtime = 0;
            s_entry_count++;
        }
    }

    update_files_view();
}

static void on_refresh_clicked(void *user_data)
{
    (void)user_data;
    load_directory(s_current_dir);
    tab5_sound_play_beep(1200, 30);
    tab5_ui_show_toast("Lista atualizada", 1000);
}

static void on_toggle_hidden_clicked(void *user_data)
{
    (void)user_data;
    s_show_hidden = !s_show_hidden;
    load_directory(s_current_dir);
    tab5_sound_play_beep(1000, 30);
    tab5_ui_show_toast(s_show_hidden ? "Exibindo ocultos" : "Ocultos escondidos", 1200);
}

static void app_init(void)
{
    tab5_system_log(2, "tab5_files", "Aplicativo Arquivos iniciado");
    tab5_ui_app_bar_set_title("Arquivos");
    tab5_ui_app_bar_add_action_button("LV_SYMBOL_REFRESH", on_refresh_clicked, NULL);
    tab5_ui_app_bar_add_action_button("LV_SYMBOL_EYE_OPEN", on_toggle_hidden_clicked, NULL);
    load_directory("/sdcard");
}

static void app_open_file(const char *path)
{
    if (path != NULL && path[0] != '\0') {
        struct stat st;
        if (stat(path, &st) == 0 && S_ISDIR(st.st_mode)) {
            load_directory(path);
        } else {
            load_directory("/sdcard");
        }
    }
}

static void app_resume(void)
{
    tab5_system_log(2, "tab5_files", "Arquivos retomado");
    load_directory(s_current_dir);
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
