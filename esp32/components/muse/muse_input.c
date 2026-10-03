// Modified by contains-studio for Muse Watcher (2026); see root CHANGES.md.
/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#include "muse_input.h"

#include <stdint.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "esp_attr.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "sdkconfig.h"
#if CONFIG_PM_ENABLE
#include "esp_pm.h"
#endif

#include "muse_battery.h"
#include "muse_ble.h"
#include "muse_board.h"
#include "muse_chat.h"
#include "muse_console.h"
#include "muse_link.h"
#include "muse_mem.h"
#include "muse_menu.h"
#include "muse_settings.h"
#include "muse_serial_photo.h"
#include "muse_state.h"
#include "muse_ui.h"
#include "muse_voice.h"
#include "muse_wifi.h"
#if CONFIG_MUSE_BOARD_SENSECAP_WATCHER
#include "muse_ptt_sources.h"
#include "muse_wardrobe.h"
#include "muse_wardrobe_settings.h"
#endif
#if CONFIG_MUSE_WATCHER_CAMERA
#include "boards/watcher_camera.h"
#include "muse_wheel_gesture.h"
#endif

static const char *TAG = "muse_input";

#define POLL_MS 10
#define REST_POLL_MS 50        /* screen off and paused: still quick to wake */
#define REST_WAIT_MS 1000      /* paused, buttons that interrupt: the rest waits this long */
#define NAP_WAIT_MS 10000      /* ... and Wi-Fi napping */
#define POWER_MS 2000          /* refresh battery */
#define REST_POWER_MS 10000    /* ... while paused */
#define WIFI_NAP_MS (2 * 60 * 1000)   /* low power this long: Wi-Fi off until it ends */
#define DOUBLE_TICKS 35        /* 350 ms: a second aux press within this toggles phone setup */

#define GOODBYE_MS 1500        /* let the goodbye animation play */
#define HINT_TICKS 60          /* 0.6 s: warn that holding powers off */
#define LONG_TICKS 150         /* 1.5 s: power off */
#define SLEEP_CHECK_MS 100

#define SERIAL_RX 1024         /* the driver drops what doesn't fit, so a console line must */
#define SERIAL_LINE 1024
#define CHAT_MAX (192 * 1024)  /* a typed message, assembled from "chat+=" lines */

static QueueHandle_t s_queue;
static TaskHandle_t s_input;
static bool s_talk_down;
static bool s_cpu_low;      /* display stopped and the CPU allowed to sleep */
static volatile bool s_power_off_requested;
static volatile bool s_nap_now;   /* ">nap": asleep, as if on battery, nap without waiting WIFI_NAP_MS */

#if CONFIG_MUSE_BOARD_SENSECAP_WATCHER
#define PTT_EDGE_COUNT 16

typedef struct {
    unsigned source;
    muse_ptt_t type;
    bool wake;
} ptt_edge_t;

static QueueHandle_t s_ptt_edges;
static atomic_bool s_ptt_overflow;
static muse_ptt_sources_t s_ptt_sources;

/* One ordered queue for every producer. In particular, touch DOWN queued
 * before this poll's wheel UP keeps the combined hold continuous. */
static void post_source(unsigned source, muse_ptt_t type, bool wake)
{
    if (!s_ptt_edges) return;   /* input has not started yet */
    ptt_edge_t edge = { .source = source, .type = type, .wake = wake };
    if (xQueueSend(s_ptt_edges, &edge, 0) != pdTRUE) atomic_store(&s_ptt_overflow, true);
}
#endif

void muse_input_touch(muse_ptt_t type)
{
#if CONFIG_MUSE_BOARD_SENSECAP_WATCHER
    /* No queue wait, display lock or voice call on the LVGL thread. */
    post_source(MUSE_PTT_TOUCH, type, false);
#else
    (void)type;
#endif
}

static void post(muse_ptt_t type, bool wake)
{
#if CONFIG_MUSE_BOARD_SENSECAP_WATCHER
    post_source(MUSE_PTT_WHEEL, type, wake);
#else
    muse_input_event_t ev = { .type = type, .wake = wake };
    ESP_LOGI(TAG, "PTT %s%s", type == MUSE_PTT_DOWN ? "down" : "up", wake ? " (waking)" : "");
    xQueueSend(s_queue, &ev, 0);
#endif
}

#if CONFIG_MUSE_BOARD_SENSECAP_WATCHER
/* Retry an undelivered edge before consuming another input edge. This keeps
 * DOWN/UP and CANCEL/DOWN pairs intact even when voice is briefly busy. */
static bool flush_ptt(void)
{
    unsigned action;
    while ((action = muse_ptt_source_next(&s_ptt_sources)) != MUSE_PTT_ACTION_NONE) {
        muse_input_event_t ev = {
            .type = action == MUSE_PTT_ACTION_DOWN ? MUSE_PTT_DOWN
                  : action == MUSE_PTT_ACTION_CANCEL ? MUSE_PTT_CANCEL : MUSE_PTT_UP,
            .wake = action == MUSE_PTT_ACTION_DOWN && s_ptt_sources.wake,
        };
        if (xQueueSend(s_queue, &ev, 0) != pdTRUE) return false;
        muse_ptt_source_commit(&s_ptt_sources, action);
        ESP_LOGI(TAG, "PTT %s%s", ev.type == MUSE_PTT_DOWN ? "down"
                 : ev.type == MUSE_PTT_CANCEL ? "cancel" : "up", ev.wake ? " (waking)" : "");
    }
    return true;
}

static void poll_touch(void)
{
    if (atomic_exchange(&s_ptt_overflow, false)) {
        /* Lost input order cannot safely complete a note. Discard pending
         * edges and cancel any recording whose DOWN reached the voice queue. */
        xQueueReset(s_ptt_edges);
        s_ptt_sources.held = 0;
        s_ptt_sources.ending = s_ptt_sources.cancelled = s_ptt_sources.sent;
        ESP_LOGW(TAG, "PTT input overflow: cancelling recording");
    }
    bool blocked = muse_state_asleep();
#if CONFIG_MUSE_WATCHER_CAMERA
    blocked |= watcher_camera_state() != WATCHER_CAMERA_CLOSED;
#endif
    if (blocked) muse_ptt_source_set(&s_ptt_sources, MUSE_PTT_TOUCH, false, true, false);
    if (!flush_ptt()) return;
    ptt_edge_t edge;
    /* Bound this poll's work even if another task is producing edges. */
    for (unsigned i = 0; i < PTT_EDGE_COUNT && xQueueReceive(s_ptt_edges, &edge, 0) == pdTRUE; i++) {
        if (blocked && edge.source == MUSE_PTT_TOUCH) edge.type = MUSE_PTT_CANCEL;
        muse_ptt_source_set(&s_ptt_sources, edge.source, edge.type == MUSE_PTT_DOWN,
                            edge.type == MUSE_PTT_CANCEL, edge.wake);
        if (!flush_ptt()) return;
    }
}
#endif

static bool update_power(void);

static void power_off(void)
{
    ESP_LOGI(TAG, "shutting down");
    muse_state_set_asleep(false);
    update_power();
    muse_state_set_progress(0);
    muse_state_set_level(0);
    muse_state_set_mode(MUSE_MODE_OFF);
    muse_state_set_caption("GOODBYE!");
    vTaskDelay(pdMS_TO_TICKS(GOODBYE_MS));
    esp_err_t err = muse_board->power_off();
    /* Only reached if the board couldn't power off. */
    vTaskDelay(pdMS_TO_TICKS(500));
    ESP_LOGE(TAG, "power-off failed (%s)", esp_err_to_name(err));
    muse_state_set_mode(MUSE_MODE_IDLE);
    muse_state_set_caption("COULDN'T POWER OFF");
}

static void set_asleep(bool asleep, const char *why)
{
    if (asleep != muse_state_asleep()) {
        ESP_LOGI(TAG, "%s (%s)", asleep ? "sleeping" : "waking", why);
        muse_state_set_asleep(asleep);
        if (s_input) {
            xTaskNotifyGive(s_input);   /* out of wait_buttons() */
        }
        if (!asleep && !muse_wifi_connected()) {
            muse_wifi_apply();   /* the screen says reconnecting: don't sit out the backoff */
        }
    }
}

static void toggle_phone_setup(void)
{
    bool on = !muse_settings_ble_on();
    ESP_LOGI(TAG, "phone setup %s", on ? "on" : "off");
    muse_settings_set_ble_on(on);
    muse_ble_status_t b;
    muse_ble_status(&b);
    if (on && b.name[0]) {
        muse_state_set_caption("PHONE SETUP: %s", b.name);
    } else {
        muse_state_set_caption("PHONE SETUP %s", on ? "ON" : "OFF");
    }
}

/*
 * Aux button: short press sleeps, a 1.5 s hold powers off, any press wakes.
 * Two quick presses toggle BLE phone setup, so the sleep waits a moment to
 * see whether a second press follows.
 */
static void aux_button(bool pressed, bool edge)
{
    static int held;
    static bool swallow;
    static bool hinted;
    static int sleep_in;    /* ticks until a pending single press sleeps */
    static char saved_caption[64];

#if CONFIG_MUSE_WATCHER_CAMERA
    if (watcher_camera_state() != WATCHER_CAMERA_CLOSED) {
        held = sleep_in = 0;
        hinted = false;
        swallow = pressed;
        return;
    }
#endif

    if (sleep_in && --sleep_in == 0) {
        set_asleep(true, muse_board->aux_button);
    }
    if (edge && pressed) {
        held = 0;
        hinted = false;
        swallow = muse_state_asleep();
        if (swallow) {
            set_asleep(false, muse_board->aux_button);
        } else if (sleep_in) {
            sleep_in = 0;
            swallow = true;
            toggle_phone_setup();
        }
        return;
    }
    if (pressed && !swallow) {
        held++;
        if (held == HINT_TICKS) {
            uint32_t v = UINT32_MAX;
            muse_state_caption(saved_caption, sizeof(saved_caption), &v);
            muse_state_set_caption("HOLD TO POWER OFF");
            hinted = true;
        } else if (held == LONG_TICKS) {
            swallow = true;
            power_off();
        }
        return;
    }
    if (edge && !pressed && !swallow) {
        if (hinted) {
            muse_state_set_caption("%s", saved_caption);   /* let go early: cancel */
        } else {
            sleep_in = DOUBLE_TICKS;
        }
    }
}

/* No touch: the aux button opens the menu and steps down it; any press wakes. */
static void menu_button(bool pressed, bool edge)
{
    if (!edge || !pressed) {
        return;
    }
    if (muse_state_asleep()) {
        set_asleep(false, muse_board->aux_button);
    } else if (!s_talk_down) {
        muse_state_poke();
        muse_menu_key(MUSE_MENU_DOWN);
    }
}

static void aux_key(bool pressed, bool edge)
{
    if (muse_board->touch) {
        aux_button(pressed, edge);
    } else {
        menu_button(pressed, edge);
    }
}

/* Talk button: push-to-talk, or Select while the menu is open. Asleep, the
 * press wakes and is posted as a waking one: muse_voice records only if it's
 * still held once awake. */
static void talk_button(unsigned ev)
{
    bool talk_down = s_talk_down;
    static bool swallow;
#if CONFIG_MUSE_WATCHER_CAMERA
    static muse_wheel_gesture_t wheel;
    static bool bypass_until_release;
    bool camera = watcher_camera_state() != WATCHER_CAMERA_CLOSED;
    bool special = !camera && (muse_state_asleep() || muse_menu_is_open()
                               || muse_link_state() == MUSE_LINK_CONFIRM);
    if (special || (bypass_until_release && !camera)) {
        /* Waking, menu selection and pairing stay immediate; finish that
         * physical press on the same path even after its action changes mode. */
        bool held = wheel.down || talk_down || bypass_until_release;
        wheel = (muse_wheel_gesture_t){0};
        if (ev & MUSE_BTN_TALK_PRESS) held = true;
        if (ev & MUSE_BTN_TALK_RELEASE) held = false;
        bypass_until_release = held;
    } else {
        bypass_until_release = false;
        if (ev) muse_state_poke();
        unsigned edges = ((ev & MUSE_BTN_TALK_PRESS) ? MUSE_WHEEL_DOWN : 0)
                         | ((ev & MUSE_BTN_TALK_RELEASE) ? MUSE_WHEEL_UP : 0)
                         | ((ev & (MUSE_BTN_WHEEL_PREV | MUSE_BTN_WHEEL_NEXT)) ? MUSE_WHEEL_TURN : 0);
        unsigned action = muse_wheel_step(&wheel, edges,
                                           (uint32_t)(esp_timer_get_time() / 1000), camera);
        if (action & MUSE_WHEEL_CAMERA) {
            ESP_LOGI(TAG, "wheel double-click: camera preview/shutter");
            watcher_camera_preview_toggle();
        }
        if (action & MUSE_WHEEL_TAP) muse_ui_wheel_click();
        ev = ((action & MUSE_WHEEL_DOWN) ? MUSE_BTN_TALK_PRESS : 0)
             | ((action & MUSE_WHEEL_UP) ? MUSE_BTN_TALK_RELEASE : 0);
        if (camera && talk_down) ev |= MUSE_BTN_TALK_RELEASE;
    }
#endif

    /* A quick tap can latch press and release in the same poll, and a release
     * can land just before the next press; keep them ordered. */
    bool released = ev & MUSE_BTN_TALK_RELEASE;
    if ((talk_down || swallow) && released) {
        if (talk_down) {
            post(MUSE_PTT_UP, false);
        }
        talk_down = swallow = false;
        released = false;
    }
    if (!talk_down && !swallow && (ev & MUSE_BTN_TALK_PRESS)) {
        if (muse_link_talk_press()) {
            /* Confirmed a Muse app pairing (Link's setup button). */
            muse_state_poke();
            swallow = true;
        } else if (muse_state_asleep()) {
            set_asleep(false, muse_board->talk_button);
            post(MUSE_PTT_DOWN, true);
            talk_down = true;
        } else if (muse_menu_is_open()) {
            muse_state_poke();
            muse_menu_key(MUSE_MENU_SELECT);
            swallow = true;
        } else {
            post(MUSE_PTT_DOWN, false);
            talk_down = true;
        }
    }
    if ((talk_down || swallow) && released) {
        if (talk_down) {
            post(MUSE_PTT_UP, false);
        }
        talk_down = swallow = false;
    }
    s_talk_down = talk_down;
}

/* A pairing prompt wakes the screen and keeps it on; otherwise idle sleeps. */
static void check_sleep(void)
{
#if CONFIG_MUSE_WATCHER_CAMERA
    if (watcher_camera_state() != WATCHER_CAMERA_CLOSED) return;
#endif
    muse_ble_status_t ble;
    muse_ble_status(&ble);
    bool prompt = ble.passkey || muse_link_state() == MUSE_LINK_CONFIRM;
    if (prompt) {
        set_asleep(false, "pairing");
        return;
    }
    int after = muse_settings_sleep_s();
    float mode_t;
    if (after && !muse_state_asleep() && muse_state_mode(&mode_t) == MUSE_MODE_IDLE
        && muse_state_idle_secs() > after) {
        set_asleep(true, "auto-sleep");
    }
}

static void set_cpu_low(bool low)
{
#if CONFIG_PM_ENABLE
    esp_pm_config_t pm = {
        .max_freq_mhz = CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ,
        .min_freq_mhz = low ? CONFIG_XTAL_FREQ : CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ,
        .light_sleep_enable = low,
    };
    esp_err_t err = esp_pm_configure(&pm);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "power management: %s", esp_err_to_name(err));
    }
#else
    (void)low;
#endif
}

/*
 * On battery with the screen dark and voice resting: stop the display and let
 * the CPU drop to the crystal clock and light-sleep between polls, or until a
 * button interrupts (wait_buttons). Returns true while the display is stopped.
 * With a USB host attached (">nap" on the bench) the CPU stays at full speed:
 * at the crystal clock a long line of serial output can stall the USB console
 * until the host reopens the port.
 */
static bool update_power(void)
{
    static bool paused;
    bool pause = muse_board->display_pause && muse_state_on_battery() && muse_state_asleep()
                 && muse_ui_dark() && muse_voice_resting();
    bool want_low = pause && !muse_console_host();
    if (pause == paused && want_low == s_cpu_low) {
        return paused;
    }
    if (pause && !paused) {
        muse_board->display_pause(true);
    }
    if (want_low != s_cpu_low) {
        set_cpu_low(want_low);
    }
    if (!pause && paused) {
        muse_board->display_pause(false);
    }
    paused = pause;
    s_cpu_low = want_low;
    ESP_LOGI(TAG, "%s", s_cpu_low ? "low power: display paused"
                        : paused  ? "display paused (USB host: CPU at full speed)"
                                  : "full power");
    return paused;
}

/*
 * Low power for WIFI_NAP_MS: Wi-Fi off, since keeping it associated costs
 * more than the rest of the chip. It rejoins when the screen wakes or USB
 * power arrives; meanwhile nothing reaches the device over the network.
 * Not while a voice note recorded offline waits to go (for a while). Returns
 * true while napping.
 */
static bool update_wifi_nap(TickType_t now, bool paused)
{
    static TickType_t low_since;
    static bool napping;
    if (!s_cpu_low) {
        low_since = now;
    }
    if (!muse_state_asleep() && s_nap_now) {
        s_nap_now = false;
        muse_state_set_as_if_battery(false);
    }
    bool nap = paused && !muse_voice_notes_waiting()
               && (s_nap_now || (s_cpu_low && now - low_since >= pdMS_TO_TICKS(WIFI_NAP_MS)));
    if (nap != napping) {
        napping = nap;
        ESP_LOGI(TAG, "Wi-Fi %s", nap ? "napping" : "waking");
        muse_wifi_nap(nap);
    }
    return napping;
}

static void input_task(void *arg)
{
    (void)arg;
    bool aux_down = false;
    bool paused = false;
    TickType_t checked = xTaskGetTickCount() - pdMS_TO_TICKS(SLEEP_CHECK_MS);
    TickType_t powered = xTaskGetTickCount() - pdMS_TO_TICKS(POWER_MS);

    for (;;) {
        unsigned ev = muse_board->poll_buttons();
        if (ev & (MUSE_BTN_TALK_PRESS | MUSE_BTN_TALK_RELEASE)) {
            ESP_LOGI(TAG, "talk key:%s%s", ev & MUSE_BTN_TALK_PRESS ? " press" : "",
                     ev & MUSE_BTN_TALK_RELEASE ? " release" : "");
        }
#if CONFIG_MUSE_WATCHER_CAMERA
        if (ev & (MUSE_BTN_WHEEL_PREV | MUSE_BTN_WHEEL_NEXT)) {
            if (muse_state_asleep()) set_asleep(false, "wheel turn");
            else muse_ui_wheel_turn(ev & MUSE_BTN_WHEEL_NEXT ? 1 : -1);
        }
        talk_button(ev);  /* the held-wheel deadline also advances without an edge */
#else
        if (ev & (MUSE_BTN_TALK_PRESS | MUSE_BTN_TALK_RELEASE)) talk_button(ev);
#endif
#if CONFIG_MUSE_BOARD_SENSECAP_WATCHER
        poll_touch();
#endif
        /* A latched key (the 1.75's PMU) can report press and release in the
         * same poll, and a release can land just before the next press; keep
         * them ordered, as talk_button does. */
        bool aux_press = ev & MUSE_BTN_AUX_PRESS;
        bool aux_release = ev & MUSE_BTN_AUX_RELEASE;
        bool aux_edge = false;
        if (aux_down && aux_release) {
            aux_down = false;
            aux_release = false;
            aux_edge = true;
            aux_key(false, true);
        }
        if (!aux_down && aux_press) {
            aux_down = aux_edge = true;
            aux_key(true, true);
            if (aux_release) {
                aux_down = false;
                aux_key(false, true);
            }
        }
        if (!aux_edge) {
            aux_key(aux_down, false);
        }

        if (s_power_off_requested) {
            s_power_off_requested = false;
            power_off();
        }

        TickType_t now = xTaskGetTickCount();
        if (now - checked >= pdMS_TO_TICKS(SLEEP_CHECK_MS)) {
            checked = now;
            check_sleep();
        }

        if (now - powered >= pdMS_TO_TICKS(paused ? REST_POWER_MS : POWER_MS)) {
            powered = now;
            muse_power_t p = { .battery_pct = -1 };
            if (muse_board->read_power && muse_board->read_power(&p) == ESP_OK) {
                muse_state_set_power(&p);
                muse_battery_note_power(&p, muse_state_on_battery());
            }
        }

        paused = update_power();
        bool napping = update_wifi_nap(now, paused);
        muse_battery_note_state(muse_state_asleep(), s_cpu_low);
        /* Paused, anything but a button (a pairing prompt, an image) is
         * noticed within REST_WAIT_MS. Napping, nothing comes over the
         * network; USB power arriving is noticed within NAP_WAIT_MS. */
        if (paused && muse_board->wait_buttons) {
            muse_board->wait_buttons(napping ? NAP_WAIT_MS : REST_WAIT_MS);
        } else {
            vTaskDelay(pdMS_TO_TICKS(paused ? REST_POLL_MS : POLL_MS));
        }
    }
}

/* Reads the rest of a line into buf; false if it was too long (the rest is dropped). */
static bool read_line(char *buf, size_t cap)
{
    size_t len = 0;
    bool whole = true;
    uint8_t c;
    while (muse_console_getc(&c) && c != '\n') {
        if (c == '\r') {
            continue;
        }
        if (len < cap - 1) {
            buf[len++] = (char)c;
        } else {
            whole = false;
        }
    }
    buf[len] = '\0';
    return whole;
}

#if CONFIG_MUSE_HATCH
#define CHAT_OVER_SERIAL "true"

/*
 * "chat+=TEXT" adds a piece of a message and "chat=TEXT" adds the last piece
 * and sends it (TEXT escaped: \n \r \t \\). Every line is acknowledged, and the
 * host waits for that before the next: the driver drops what it has no room for.
 */
static char *s_chat;        /* the message so far */
static size_t s_chat_len;

static void chat_line(char *piece, bool last, bool whole)
{
    size_t n = muse_hatch_unescape(piece);
    if (!s_chat) {
        s_chat = heap_caps_malloc(CHAT_MAX, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        s_chat_len = 0;
    }
    const char *err = !whole ? "LINE TOO LONG" : !s_chat ? "OUT OF MEMORY" : s_chat_len + n >= CHAT_MAX ? "TOO LONG" : NULL;
    if (err) {
        free(s_chat);
        s_chat = NULL;
        muse_hatch_console("error", err, NULL);
        return;
    }
    memcpy(s_chat + s_chat_len, piece, n);
    s_chat_len += n;
    s_chat[s_chat_len] = '\0';
    muse_hatch_console("ack", NULL, "\"bytes\":%u", (unsigned)s_chat_len);
    if (last) {
        muse_hatch_text_turn(s_chat);   /* frees it */
        s_chat = NULL;
    }
}

/* Drops a half-sent message and ends a typed turn. */
static void chat_cancel(void)
{
    free(s_chat);
    s_chat = NULL;
    muse_hatch_text_cancel();
}

/* Serial photos use the same send path as the camera's Send photo button.
 * No camera is opened here. The host supplies a JPEG a line at a time. */
static uint8_t *s_photo;
static size_t s_photo_len;
static size_t s_photo_cap;
static atomic_bool s_photo_sending;
static SemaphoreHandle_t s_photo_output_lock;

static void photo_clear(void)
{
    free(s_photo);
    s_photo = NULL;
    s_photo_len = s_photo_cap = 0;
}

/* All strings below are fixed status codes: neither image data nor server
 * response text is ever printed to the console. */
static void photo_error(const char *code)
{
    printf("@photo {\"type\":\"error\",\"code\":\"%s\"}\n", code);
    fflush(stdout);
}

static void photo_sent(bool sent, const char *error, void *ctx)
{
    (void)error;
    (void)ctx;
    xSemaphoreTake(s_photo_output_lock, portMAX_DELAY);
    if (sent) printf("@photo {\"type\":\"sent\"}\n");
    else photo_error("SEND_FAILED");
    fflush(stdout);
    atomic_store(&s_photo_sending, false);
    xSemaphoreGive(s_photo_output_lock);
}

static void photo_line(const char *piece, bool last, bool whole)
{
    if (atomic_load(&s_photo_sending)) {
        photo_clear();
        photo_error("BUSY");
        return;
    }
    uint8_t decoded[SERIAL_LINE / 4 * 3];
    size_t n;
    if (!whole || !muse_serial_photo_decode(piece, strlen(piece), last, decoded, sizeof(decoded), &n)) {
        photo_clear();
        photo_error(whole ? "INVALID_BASE64" : "LINE_TOO_LONG");
        return;
    }
    if (n > MUSE_SERIAL_PHOTO_MAX - s_photo_len) {
        photo_clear();
        photo_error("TOO_LARGE");
        return;
    }
    size_t wanted = s_photo_len + n;
    if (wanted > s_photo_cap) {
        size_t cap = s_photo_cap ? s_photo_cap * 2 : 4096;
        if (cap < wanted) cap = wanted;
        if (cap > MUSE_SERIAL_PHOTO_MAX) cap = MUSE_SERIAL_PHOTO_MAX;
        uint8_t *next = heap_caps_realloc(s_photo, cap, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        if (!next) {
            photo_clear();
            photo_error("OUT_OF_MEMORY");
            return;
        }
        s_photo = next;
        s_photo_cap = cap;
    }
    if (n) memcpy(s_photo + s_photo_len, decoded, n);
    s_photo_len = wanted;
    printf("@photo {\"type\":\"ack\",\"bytes\":%u}\n", (unsigned)s_photo_len);
    fflush(stdout);
    if (!last) return;
    if (s_photo_len < 4 || s_photo[0] != 0xff || s_photo[1] != 0xd8
        || s_photo[s_photo_len - 2] != 0xff || s_photo[s_photo_len - 1] != 0xd9) {
        photo_clear();
        photo_error("INVALID_JPEG");
        return;
    }
    if (!s_photo_output_lock) s_photo_output_lock = xSemaphoreCreateMutex();
    if (!s_photo_output_lock) {
        photo_clear();
        photo_error("OUT_OF_MEMORY");
        return;
    }
    /* A very quick server ACK may race the enqueue return. Keep accepted
     * ahead of the callback event without holding up the voice worker. */
    xSemaphoreTake(s_photo_output_lock, portMAX_DELAY);
    atomic_store(&s_photo_sending, true);
    bool accepted = muse_voice_send_photo(s_photo, s_photo_len, photo_sent, NULL);
    photo_clear();   /* the send API copies the bytes before it returns */
    if (accepted) printf("@photo {\"type\":\"accepted\"}\n");
    else {
        atomic_store(&s_photo_sending, false);
        photo_error("BUSY_OFFLINE_OR_INVALID");
    }
    fflush(stdout);
    xSemaphoreGive(s_photo_output_lock);
}
#else
#define CHAT_OVER_SERIAL "false"   /* no PSRAM: replies come over Link, and only short ones */
#endif

/*
 * Bench: puts the face in a mode ("face=thinking") until the voice path or
 * another "face=" moves it on. "face=happy" goes back to idle with the happy
 * hop, as a finished turn does.
 */
static void set_face(const char *name)
{
    static const char *const modes[MUSE_MODE_COUNT] = {
        [MUSE_MODE_BOOT] = "boot",
        [MUSE_MODE_IDLE] = "idle",
        [MUSE_MODE_LISTENING] = "listening",
        [MUSE_MODE_THINKING] = "thinking",
        [MUSE_MODE_SPEAKING] = "speaking",
        [MUSE_MODE_ERROR] = "error",
        [MUSE_MODE_OFF] = "off",
    };
    muse_state_poke();
    bool happy = !strcmp(name, "happy");
    if (happy) {
        name = "idle";   /* where a finished turn hops to */
    }
    for (int m = 0; m < MUSE_MODE_COUNT; m++) {
        if (!strcmp(name, modes[m])) {
            muse_state_set_mode(MUSE_MODE_IDLE);   /* only idle leaves "off" */
            muse_state_set_mode((muse_mode_t)m);
            if (happy) {
                muse_state_make_happy();
            }
            return;
        }
    }
    printf("@face.error unknown face \"%s\": boot idle listening thinking speaking error off happy\n", name);
    fflush(stdout);
}

/*
 * Console-only commands; false for setup commands. Their buffers are taken
 * per command: without PSRAM, static ones would hold internal RAM for good.
 */
static bool console_command(char *line, bool whole)
{
#if CONFIG_MUSE_BOARD_SENSECAP_WATCHER
    if (!strcmp(line, "outfit") || !strncmp(line, "outfit=", 7)) {
        esp_err_t err = !whole ? ESP_ERR_INVALID_ARG : !strcmp(line, "outfit")
            ? ESP_OK : muse_wardrobe_settings_set(line + 7);
        if (err == ESP_OK) {
            printf("@outfit {\"ok\":true,\"outfit_id\":\"%s\"}\n", muse_wardrobe_current());
        } else {
            printf("@outfit {\"ok\":false,\"error\":{\"code\":\"%s\"}}\n",
                   err == ESP_ERR_INVALID_ARG ? "invalid_params" : "storage_error");
        }
        fflush(stdout);
        return true;
    }
#endif
    if (!strcmp(line, "status")) {
        size_t cap = 1024;   /* long SSID, host and VM names escaped: past 512 */
        char *json = heap_caps_malloc(cap, MUSE_BIG_CAPS);
        if (json) {
            muse_ble_status_json(json, cap);
            printf("@status {\"board\":\"%s\",\"chat\":%s,\"device\":%s}\n", muse_board->name,
                   CHAT_OVER_SERIAL, json);
            fflush(stdout);
            free(json);
        }
        return true;
    }
    bool reset = !strcmp(line, "power.reset");
    if (reset || !strcmp(line, "power")) {
        if (reset) {
            muse_battery_reset();
        }
        const char *saved = muse_battery_saved_json();
        if (saved) {
            printf("@power.saved %s\n", saved);
        }
        size_t cap = 2048;   /* two dozen power locks */
        char *json = heap_caps_malloc(cap, MUSE_BIG_CAPS);
        if (json) {
            muse_battery_json(json, cap);
            printf("@power %s\n", json);
            free(json);
        }
        fflush(stdout);
        return true;
    }
    if (!strcmp(line, "nap")) {
        muse_state_set_as_if_battery(true);
        set_asleep(true, "serial");
        s_nap_now = true;
        return true;
    }
    if (!strncmp(line, "face=", 5)) {
        set_face(line + 5);
        return true;
    }
#if CONFIG_MUSE_WATCHER_CAMERA
    /* Exercise the same local preview/review path over USB. These diagnostic
     * commands never send a photograph to Muse or print image bytes. */
    if (whole && !strncmp(line, "wheel=", 6)) {
        if (!strcmp(line + 6, "next")) muse_ui_wheel_turn(1);
        else if (!strcmp(line + 6, "prev")) muse_ui_wheel_turn(-1);
        else if (!strcmp(line + 6, "click")) muse_ui_wheel_click();
        else return false;
        return true;
    }
#if LV_USE_SNAPSHOT
    if (whole && !strncmp(line, "reader=", 7)) {
        muse_ui_reply_update("usb-reader-fixture", line + 7);
        return true;
    }
#endif
    if (whole && !strncmp(line, "camera.", 7)) {
        const char *action = line + 7;
        watcher_camera_state_t state = watcher_camera_state();
        if (!strcmp(action, "preview")) {
            muse_state_poke();
            if (state == WATCHER_CAMERA_CLOSED) watcher_camera_preview_toggle();
        } else if (!strcmp(action, "freeze")) {
            if (state == WATCHER_CAMERA_LIVE) watcher_camera_preview_toggle();
        } else if (!strcmp(action, "retake")) {
            watcher_camera_retake();
        } else if (!strcmp(action, "close")) {
            watcher_camera_close();
        } else if (strcmp(action, "status")) {
            return false;
        }
        char error[96];
        watcher_camera_status(error, sizeof(error));
        printf("@camera {\"state\":%d,\"error\":%s}\n", (int)watcher_camera_state(), error[0] ? "true" : "false");
        fflush(stdout);
        return true;
    }
#endif
#if CONFIG_MUSE_HATCH
    if (!strcmp(line, "photo.cancel")) {
        photo_clear();
        if (atomic_load(&s_photo_sending)) photo_error("ALREADY_SUBMITTED");
        else {
            printf("@photo {\"type\":\"cancelled\"}\n");
            fflush(stdout);
        }
        return true;
    }
    bool photo_last = !strncmp(line, "photo=", 6);
    if (photo_last || !strncmp(line, "photo+=", 7)) {
        photo_line(line + (photo_last ? 6 : 7), photo_last, whole);
        return true;
    }
#endif
    if (strncmp(line, "chat", 4) != 0) {
        return false;
    }
#if CONFIG_MUSE_HATCH
    if (!strcmp(line, "chat.cancel")) {
        chat_cancel();
        return true;
    }
    bool last = !strncmp(line, "chat=", 5);
    if (last || !strncmp(line, "chat+=", 6)) {
        chat_line(line + (last ? 5 : 6), last, whole);
        return true;
    }
    return false;
#else
    (void)whole;
    muse_hatch_console("error", "THIS BOARD CAN'T CHAT OVER SERIAL", NULL);
    return true;
#endif
}

/*
 * Bench testing over the USB cable: 'd' / 'u' act as the talk button going
 * down / up, so the voice path can be driven without a finger on the button;
 * 'm' plays a built-in MP3 through the reply decoder; 'a' / 's' press the
 * menu's Down / Select; 'p' sends a screenshot; 'z' / 'w' sleep and wake.
 * A line starting with '>' is a setup command, the same "key=value" text as
 * the BLE CMD characteristic, or one of the console's own: "status" prints
 * the device's state, "power" the battery meter (muse_battery.h) and
 * "power.reset" starts it over, "nap" sleeps and leaves Wi-Fi at once (as
 * two minutes asleep on battery would; 'w' rejoins), "face=" shows a face
 * (see set_face), and "chat=" sends a typed message to Hatch (see chat_line
 * and tools/muse/chat.py).
 */
static void serial_task(void *arg)
{
    if (muse_console_install(SERIAL_RX) != ESP_OK) {
        vTaskDelete(NULL);
    }
    for (;;) {
        uint8_t c;
        if (!muse_console_getc(&c)) {
            continue;
        }
        if (c == 'm') {
            muse_voice_request_mp3test();
        } else if (c == 'a' || c == 's') {
            muse_state_poke();
            muse_menu_key(c == 'a' ? MUSE_MENU_DOWN : MUSE_MENU_SELECT);
        } else if (c == 'p') {
            muse_ui_request_snapshot();
        } else if (c == 'z' || c == 'w') {
            set_asleep(c == 'z', "serial");
        } else if (c == '>') {
            char *line = heap_caps_malloc(SERIAL_LINE, MUSE_BIG_CAPS);
            char none[1];   /* no room: the line is read and dropped */
            bool whole = read_line(line ? line : none, line ? SERIAL_LINE : sizeof(none));
            if (line && !console_command(line, whole)) {
                muse_ble_command(line);
            }
            free(line);
        } else if (c == 'd' || c == 'u') {
            muse_state_poke();
#if CONFIG_MUSE_BOARD_SENSECAP_WATCHER
            post_source(MUSE_PTT_SERIAL, c == 'd' ? MUSE_PTT_DOWN : MUSE_PTT_UP, false);
#else
            post(c == 'd' ? MUSE_PTT_DOWN : MUSE_PTT_UP, false);
#endif
        }
    }
}

esp_err_t muse_input_start(QueueHandle_t queue)
{
    s_queue = queue;
#if CONFIG_MUSE_BOARD_SENSECAP_WATCHER
    s_ptt_edges = xQueueCreate(PTT_EDGE_COUNT, sizeof(ptt_edge_t));
    if (!s_ptt_edges) return ESP_ERR_NO_MEM;
#endif
    if (xTaskCreate(input_task, "muse_input", 4096, NULL, 6, &s_input) != pdPASS) {
#if CONFIG_MUSE_BOARD_SENSECAP_WATCHER
        vQueueDelete(s_ptt_edges);
        s_ptt_edges = NULL;
#endif
        return ESP_FAIL;
    }
    /* Bench-test and setup console; the input still works if it can't start. */
    xTaskCreate(serial_task, "muse_serial", 3584, NULL, 5, NULL);
    return ESP_OK;
}

void muse_input_request_power_off(void)
{
    s_power_off_requested = true;
}
