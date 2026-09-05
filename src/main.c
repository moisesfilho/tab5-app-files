#include "tab5_sdk.h"
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_ENTRIES 200
#define MAX_SCAN_ENTRIES 512
#define MAX_PATH 256

#define NVS_NS "tab5"
#define NVS_KEY_HIDDEN "files_hidden"

#ifndef LV_SYMBOL_LIST
#define LV_SYMBOL_LIST "\xef\x80\xba"
#endif

static tab5_dir_entry_t s_entries[MAX_ENTRIES];
static uint32_t s_entry_count = 0;
static char s_current_path[MAX_PATH] = "/sdcard";
static bool s_show_hidden = false;
static bool s_is_grid = true;
static bool s_ui_ready = false;

static tab5_ui_obj_t s_container = TAB5_UI_INVALID_OBJ;
static tab5_ui_obj_t s_btn_view = TAB5_UI_INVALID_OBJ;
static tab5_ui_obj_t s_btn_hidden = TAB5_UI_INVALID_OBJ;
static tab5_ui_obj_t s_item_handles[MAX_ENTRIES];
static int s_item_entry_idx[MAX_ENTRIES];
static uint32_t s_item_count = 0;

static void render_content(void);
static void load_directory(const char *path);

static void on_view_toggle(void *user_data)
{
    (void)user_data;
    s_is_grid = !s_is_grid;
    tab5_ui_label_set_text(s_btn_view, s_is_grid ? "LV_SYMBOL_LIST" : "LV_SYMBOL_IMAGE");
    render_content();
}

static void on_hidden_toggle(void *user_data)
{
    (void)user_data;
    s_show_hidden = !s_show_hidden;
    tab5_ui_label_set_text(s_btn_hidden, s_show_hidden ? LV_SYMBOL_EYE_OPEN : LV_SYMBOL_EYE_CLOSE);
    tab5_nvs_set_u8(NVS_NS, NVS_KEY_HIDDEN, s_show_hidden ? 1 : 0);
    load_directory(s_current_path);
}

static int entry_cmp(const void *a, const void *b)
{
    const tab5_dir_entry_t *ea = (const tab5_dir_entry_t *)a;
    const tab5_dir_entry_t *eb = (const tab5_dir_entry_t *)b;
    if (ea->is_dir != eb->is_dir) {
        return (ea->is_dir != 0) ? -1 : 1;
    }
    return strcmp(ea->name, eb->name);
}

static void format_size(uint32_t size, bool is_dir, char *buf, size_t len)
{
    if (is_dir) {
        snprintf(buf, len, "<DIR>");
        return;
    }
    if (size < 1024) {
        snprintf(buf, len, "%u B", size);
    } else if (size < 1024 * 1024) {
        snprintf(buf, len, "%.1f KB", (float)size / 1024.0f);
    } else {
        snprintf(buf, len, "%.1f MB", (float)size / (1024.0f * 1024.0f));
    }
}

static void format_date(uint32_t mtime, char *buf, size_t len)
{
    if (mtime == 0) {
        snprintf(buf, len, "--/--/---- --:--");
        return;
    }
    uint32_t days = mtime / 86400;
    uint32_t rem = mtime % 86400;
    int z = (int)days + 719468;
    int era = (z >= 0 ? z : z - 146096) / 146097;
    unsigned doe = (unsigned)(z - era * 146097);
    unsigned yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    int y = (int)yoe + era * 400;
    unsigned doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    unsigned mp = (5 * doy + 2) / 153;
    unsigned d = doy - (153 * mp + 2) / 5 + 1;
    unsigned mth = mp < 10 ? mp + 3 : mp - 9;
    y += (mth <= 2);
    unsigned hh = rem / 3600;
    unsigned mm = (rem % 3600) / 60;
    snprintf(buf, len, "%02u/%02u/%04u %02u:%02u", d, mth, (unsigned)y, hh, mm);
}

static void join_path(char *out, size_t out_size, const char *dir, const char *name)
{
    if (dir[0] != '\0' && dir[strlen(dir) - 1] == '/') {
        snprintf(out, out_size, "%s%s", dir, name);
    } else {
        snprintf(out, out_size, "%s/%s", dir, name);
    }
}

static void go_up(void)
{
    if (strcmp(s_current_path, "/sdcard") == 0 || strcmp(s_current_path, "/sdcard/") == 0) {
        return;
    }
    size_t len = strlen(s_current_path);
    while (len > 0 && s_current_path[len - 1] == '/') {
        len--;
    }
    while (len > 0 && s_current_path[len - 1] != '/') {
        len--;
    }
    if (len <= strlen("/sdcard")) {
        load_directory("/sdcard");
    } else {
        char parent[MAX_PATH];
        snprintf(parent, sizeof(parent), "%.*s", (int)(len - 1), s_current_path);
        load_directory(parent);
    }
}

static void on_item_click(int32_t idx)
{
    if (idx < 0) {
        go_up();
        return;
    }
    if ((uint32_t)idx >= s_entry_count) {
        return;
    }
    const tab5_dir_entry_t *entry = &s_entries[idx];
    char full_path[MAX_PATH];
    join_path(full_path, sizeof(full_path), s_current_path, entry->name);
    if (entry->is_dir) {
        load_directory(full_path);
    } else {
        tab5_file_assoc_open(full_path);
    }
}

static void render_content(void)
{
    if (!s_ui_ready || s_container == TAB5_UI_INVALID_OBJ) {
        return;
    }

    tab5_ui_obj_clean_deferred(s_container);
    tab5_ui_obj_scroll_to_top(s_container, false);
    s_item_count = 0;

    uint32_t pal_surface = tab5_ui_theme_get_color(TAB5_UI_COLOR_SURFACE);
    uint32_t pal_border = tab5_ui_theme_get_color(TAB5_UI_COLOR_BORDER);
    uint32_t pal_accent = tab5_ui_theme_get_color(TAB5_UI_COLOR_ACCENT);
    uint32_t pal_text = tab5_ui_theme_get_color(TAB5_UI_COLOR_TEXT);
    uint32_t pal_text_muted = tab5_ui_theme_get_color(TAB5_UI_COLOR_TEXT_MUTED);

    bool has_parent = (strcmp(s_current_path, "/sdcard") != 0 && strcmp(s_current_path, "/sdcard/") != 0);
    uint32_t total = (has_parent ? 1 : 0) + s_entry_count;

    if (total == 0) {
        tab5_ui_obj_set_flex_flow(s_container, TAB5_UI_FLEX_FLOW_COLUMN);
        tab5_ui_obj_t empty = tab5_ui_label_create(s_container, "Nenhum arquivo encontrado");
        tab5_ui_obj_set_align(empty, TAB5_UI_ALIGN_CENTER, 0, 0);
        tab5_ui_obj_set_style_text_color(empty, pal_text_muted, 255);
        return;
    }

    if (s_is_grid) {
        tab5_ui_obj_set_flex_flow(s_container, TAB5_UI_FLEX_FLOW_ROW_WRAP);
        tab5_ui_obj_set_scrollable(s_container, true);
        tab5_ui_obj_set_pad(s_container, 12);
        tab5_ui_obj_set_gap(s_container, 12);

        if (has_parent) {
            tab5_ui_obj_t tile = tab5_ui_container_create(s_container);
            tab5_ui_obj_set_size(tile, 108, 112);
            tab5_ui_obj_set_scrollable(tile, false);
            tab5_ui_obj_set_style_bg(tile, pal_surface, 255);
            tab5_ui_obj_set_style_border(tile, pal_border, 1);
            tab5_ui_obj_set_style_radius(tile, 10);
            tab5_ui_obj_set_pad(tile, 4);
            tab5_ui_obj_set_clickable(tile, true);
            tab5_ui_obj_set_flex_flow(tile, TAB5_UI_FLEX_FLOW_COLUMN);
            tab5_ui_obj_set_gap(tile, 6);

            tab5_ui_obj_t icon = tab5_ui_label_create(tile, LV_SYMBOL_LEFT);
            tab5_ui_obj_set_scrollable(icon, false);
            tab5_ui_obj_set_clickable(icon, false);
            tab5_ui_obj_set_style_text_size(icon, 28);
            tab5_ui_obj_set_align(icon, TAB5_UI_ALIGN_CENTER, 0, 0);
            tab5_ui_obj_set_style_text_color(icon, pal_accent, 255);

            tab5_ui_obj_t name = tab5_ui_label_create(tile, "..");
            tab5_ui_obj_set_scrollable(name, false);
            tab5_ui_obj_set_clickable(name, false);
            tab5_ui_obj_set_size(name, 100, TAB5_UI_SIZE_CONTENT);
            tab5_ui_obj_set_align(name, TAB5_UI_ALIGN_CENTER, 0, 0);
            tab5_ui_obj_set_style_text_color(name, pal_text, 255);

            s_item_handles[s_item_count] = tile;
            s_item_entry_idx[s_item_count] = -1;
            s_item_count++;
        }

        for (uint32_t i = 0; i < s_entry_count && s_item_count < MAX_ENTRIES; i++) {
            bool is_dir = s_entries[i].is_dir != 0;
            tab5_ui_obj_t tile = tab5_ui_container_create(s_container);
            tab5_ui_obj_set_size(tile, 108, 112);
            tab5_ui_obj_set_scrollable(tile, false);
            tab5_ui_obj_set_style_bg(tile, pal_surface, 255);
            tab5_ui_obj_set_style_border(tile, pal_border, 1);
            tab5_ui_obj_set_style_radius(tile, 10);
            tab5_ui_obj_set_pad(tile, 4);
            tab5_ui_obj_set_clickable(tile, true);
            tab5_ui_obj_set_flex_flow(tile, TAB5_UI_FLEX_FLOW_COLUMN);
            tab5_ui_obj_set_gap(tile, 6);

            tab5_ui_obj_t icon = tab5_ui_label_create(tile, is_dir ? LV_SYMBOL_DIRECTORY : LV_SYMBOL_FILE);
            tab5_ui_obj_set_scrollable(icon, false);
            tab5_ui_obj_set_clickable(icon, false);
            tab5_ui_obj_set_style_text_size(icon, 28);
            tab5_ui_obj_set_align(icon, TAB5_UI_ALIGN_CENTER, 0, 0);
            tab5_ui_obj_set_style_text_color(icon, is_dir ? pal_accent : pal_text_muted, 255);

            tab5_ui_obj_t name = tab5_ui_label_create(tile, s_entries[i].name);
            tab5_ui_obj_set_scrollable(name, false);
            tab5_ui_obj_set_clickable(name, false);
            tab5_ui_obj_set_size(name, 100, TAB5_UI_SIZE_CONTENT);
            tab5_ui_obj_set_align(name, TAB5_UI_ALIGN_CENTER, 0, 0);
            tab5_ui_obj_set_style_text_color(name, pal_text, 255);

            s_item_handles[s_item_count] = tile;
            s_item_entry_idx[s_item_count] = (int)i;
            s_item_count++;
        }
    } else {
        tab5_ui_obj_set_flex_flow(s_container, TAB5_UI_FLEX_FLOW_COLUMN);
        tab5_ui_obj_set_scrollable(s_container, true);
        tab5_ui_obj_set_pad(s_container, 8);
        tab5_ui_obj_set_gap(s_container, 4);

        if (has_parent) {
            tab5_ui_obj_t item = tab5_ui_container_create(s_container);
            tab5_ui_obj_set_size(item, TAB5_UI_PCT(100), 60);
            tab5_ui_obj_set_scrollable(item, false);
            tab5_ui_obj_set_style_bg(item, pal_surface, 255);
            tab5_ui_obj_set_style_border(item, pal_border, 1);
            tab5_ui_obj_set_style_radius(item, 8);
            tab5_ui_obj_set_pad(item, 4);
            tab5_ui_obj_set_clickable(item, true);
            tab5_ui_obj_set_flex_flow(item, TAB5_UI_FLEX_FLOW_ROW);
            tab5_ui_obj_set_gap(item, 8);

            tab5_ui_obj_t icon = tab5_ui_label_create(item, LV_SYMBOL_LEFT);
            tab5_ui_obj_set_scrollable(icon, false);
            tab5_ui_obj_set_clickable(icon, false);
            tab5_ui_obj_set_style_text_size(icon, 28);
            tab5_ui_obj_set_style_text_color(icon, pal_accent, 255);

            tab5_ui_obj_t name = tab5_ui_label_create(item, "..");
            tab5_ui_obj_set_scrollable(name, false);
            tab5_ui_obj_set_clickable(name, false);
            tab5_ui_label_set_wrap(name, false);
            tab5_ui_obj_set_flex_grow(name, 1);
            tab5_ui_obj_set_style_text_color(name, pal_text, 255);

            s_item_handles[s_item_count] = item;
            s_item_entry_idx[s_item_count] = -1;
            s_item_count++;
        }

        for (uint32_t i = 0; i < s_entry_count && s_item_count < MAX_ENTRIES; i++) {
            bool is_dir = s_entries[i].is_dir != 0;
            tab5_ui_obj_t item = tab5_ui_container_create(s_container);
            tab5_ui_obj_set_size(item, TAB5_UI_PCT(100), 60);
            tab5_ui_obj_set_scrollable(item, false);
            tab5_ui_obj_set_style_bg(item, pal_surface, 255);
            tab5_ui_obj_set_style_border(item, pal_border, 1);
            tab5_ui_obj_set_style_radius(item, 8);
            tab5_ui_obj_set_pad(item, 4);
            tab5_ui_obj_set_clickable(item, true);
            tab5_ui_obj_set_flex_flow(item, TAB5_UI_FLEX_FLOW_ROW);
            tab5_ui_obj_set_gap(item, 8);

            tab5_ui_obj_t icon = tab5_ui_label_create(item, is_dir ? LV_SYMBOL_DIRECTORY : LV_SYMBOL_FILE);
            tab5_ui_obj_set_scrollable(icon, false);
            tab5_ui_obj_set_clickable(icon, false);
            tab5_ui_obj_set_style_text_size(icon, 28);
            tab5_ui_obj_set_style_text_color(icon, is_dir ? pal_accent : pal_text_muted, 255);

            char size_buf[32];
            format_size(s_entries[i].size, is_dir, size_buf, sizeof(size_buf));
            char date_buf[32];
            format_date(s_entries[i].mtime, date_buf, sizeof(date_buf));
            char details_buf[72];
            snprintf(details_buf, sizeof(details_buf), "%s  %s", size_buf, date_buf);

            tab5_ui_obj_t info = tab5_ui_container_create(item);
            tab5_ui_obj_set_scrollable(info, false);
            tab5_ui_obj_set_clickable(info, false);
            tab5_ui_obj_set_pad(info, 0);
            tab5_ui_obj_set_size(info, TAB5_UI_SIZE_CONTENT, TAB5_UI_PCT(100));
            tab5_ui_obj_set_flex_flow(info, TAB5_UI_FLEX_FLOW_COLUMN);
            tab5_ui_obj_set_flex_grow(info, 1);

            tab5_ui_obj_t name = tab5_ui_label_create(info, s_entries[i].name);
            tab5_ui_obj_set_scrollable(name, false);
            tab5_ui_obj_set_clickable(name, false);
            tab5_ui_label_set_wrap(name, false);
            tab5_ui_obj_set_size(name, TAB5_UI_PCT(100), TAB5_UI_SIZE_CONTENT);
            tab5_ui_obj_set_style_text_color(name, pal_text, 255);

            tab5_ui_obj_t details = tab5_ui_label_create(info, details_buf);
            tab5_ui_obj_set_scrollable(details, false);
            tab5_ui_obj_set_clickable(details, false);
            tab5_ui_label_set_wrap(details, false);
            tab5_ui_obj_set_size(details, TAB5_UI_PCT(100), TAB5_UI_SIZE_CONTENT);
            tab5_ui_obj_set_style_text_color(details, pal_text_muted, 255);

            s_item_handles[s_item_count] = item;
            s_item_entry_idx[s_item_count] = (int)i;
            s_item_count++;
        }
    }
}

static void load_directory(const char *path)
{
    if (path == NULL || path[0] == '\0') {
        path = "/sdcard";
    }
    strncpy(s_current_path, path, sizeof(s_current_path) - 1);
    s_current_path[sizeof(s_current_path) - 1] = '\0';

    tab5_dir_entry_t buf[MAX_SCAN_ENTRIES];
    uint32_t count = 0;
    s_entry_count = 0;

    tab5_err_t scan_result = tab5_storage_scandir(s_current_path, buf, MAX_SCAN_ENTRIES, &count);
    char scan_log[96];
    snprintf(scan_log, sizeof(scan_log), "Listagem %s: resultado=%d itens=%u", s_current_path, (int)scan_result, count);
    tab5_system_log(2, "tab5_files", scan_log);

    if (scan_result == TAB5_OK) {
        for (uint32_t i = 0; i < count && s_entry_count < MAX_ENTRIES; i++) {
            const char *name = buf[i].name;
            if (strcmp(name, ".") == 0 || strcmp(name, "..") == 0) {
                continue;
            }
            if (!s_show_hidden && name[0] == '.') {
                continue;
            }
            s_entries[s_entry_count] = buf[i];
            s_entry_count++;
        }
    }

    qsort(s_entries, s_entry_count, sizeof(tab5_dir_entry_t), entry_cmp);

    char title[168];
    snprintf(title, sizeof(title), "Arquivos - %s", s_current_path);
    tab5_ui_app_bar_set_title(title);

    render_content();
}

static void build_files_ui(void)
{
    uint32_t pal_bg = tab5_ui_theme_get_color(TAB5_UI_COLOR_BG);

    tab5_ui_obj_t scr = tab5_ui_get_screen();
    tab5_ui_obj_set_scrollable(scr, false);

    s_container = tab5_ui_container_create(scr);
    int32_t display_w = 0;
    int32_t display_h = 0;
    tab5_ui_get_display_size(&display_w, &display_h);
    (void)display_w;
    tab5_ui_obj_set_size(s_container, TAB5_UI_PCT(100), display_h > 104 ? display_h - 104 : TAB5_UI_SIZE_CONTENT);
    tab5_ui_obj_set_align(s_container, TAB5_UI_ALIGN_TOP_LEFT, 0, 104);
    tab5_ui_obj_set_scrollable(s_container, true);
    tab5_ui_obj_set_style_bg(s_container, pal_bg, 255);
    tab5_ui_obj_set_style_border(s_container, 0, 0);

    s_ui_ready = true;
    load_directory(s_current_path);
}

static void app_init(void)
{
    tab5_system_log(2, "tab5_files", "Aplicativo Arquivos desacoplado iniciado");
    tab5_ui_app_bar_set_title("Arquivos");

    uint8_t hidden = 0;
    if (tab5_nvs_get_u8(NVS_NS, NVS_KEY_HIDDEN, &hidden) == TAB5_OK) {
        s_show_hidden = (hidden != 0);
    }

    s_btn_view = tab5_ui_app_bar_add_action_button("LV_SYMBOL_LIST", on_view_toggle, NULL);
    s_btn_hidden = tab5_ui_app_bar_add_action_button(
        s_show_hidden ? "LV_SYMBOL_EYE_OPEN" : "LV_SYMBOL_EYE_CLOSE", on_hidden_toggle, NULL);

    build_files_ui();

}

static void app_open_file(const char *path)
{
    if (path == NULL || path[0] == '\0') {
        return;
    }
    char dir[MAX_PATH];
    strncpy(dir, path, sizeof(dir) - 1);
    dir[sizeof(dir) - 1] = '\0';
    char *last_slash = strrchr(dir, '/');
    if (last_slash != NULL) {
        if (last_slash == dir) {
            last_slash[1] = '\0';
        } else {
            *last_slash = '\0';
        }
        load_directory(dir);
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

static void app_on_theme_changed(void)
{
    tab5_ui_clear_content();
    s_ui_ready = false;
    build_files_ui();
}

static void app_register_and_start(void)
{
    tab5_lifecycle_callbacks_t cbs = {
        .on_init = app_init,
        .on_resume = app_resume,
        .on_pause = app_pause,
        .on_destroy = app_destroy,
        .on_open_file = app_open_file,
    };

    tab5_lifecycle_register(&cbs);
    app_init();
}

TAB5_APP_EXPORT void tab5_app_on_theme_changed(bool dark)
{
    (void)dark;
    app_on_theme_changed();
}

TAB5_APP_EXPORT void tab5_app_on_ui_event(tab5_ui_obj_t obj, uint32_t event_type, int32_t event_val)
{
    (void)event_val;
    if (event_type != TAB5_UI_EVENT_CLICKED) {
        return;
    }

    if (obj == s_btn_view) {
        on_view_toggle(NULL);
        return;
    }
    if (obj == s_btn_hidden) {
        on_hidden_toggle(NULL);
        return;
    }

    for (uint32_t i = 0; i < s_item_count; i++) {
        if (obj == s_item_handles[i]) {
            on_item_click(s_item_entry_idx[i]);
            return;
        }
    }
}

TAB5_APP_EXPORT int app_main(void)
{
    app_register_and_start();
    return 0;
}

TAB5_APP_EXPORT int main(int argc, char **argv)
{
    (void)argc;
    (void)argv;
    app_register_and_start();
    return 0;
}
