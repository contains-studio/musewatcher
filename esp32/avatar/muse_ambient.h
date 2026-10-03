/* Ambient animation controls. The character renderer retains its original
 * Meta copyright and the upstream artwork license exclusion. */
#pragma once

#include <stdint.h>
#include <time.h>

typedef enum {
    MUSE_AMBIENT_UNSET,
    MUSE_AMBIENT_MORNING,       /* 05:00–10:59 */
    MUSE_AMBIENT_DAY,           /* 11:00–16:59 */
    MUSE_AMBIENT_EVENING,       /* 17:00–20:59 */
    MUSE_AMBIENT_NIGHT,         /* 21:00–04:59 */
} muse_ambient_period_t;

typedef enum {
    MUSE_WEATHER_UNKNOWN,
    MUSE_WEATHER_CLEAR,
    MUSE_WEATHER_CLOUDY,
    MUSE_WEATHER_RAIN,
    MUSE_WEATHER_SNOW,
    MUSE_WEATHER_WIND,
} muse_ambient_weather_t;

/* Uses CONFIG_MUSE_TIME_ZONE (POSIX TZ, default UTC0) set during app startup.
 * Invalid device time returns UNSET until SNTP has synchronized. */
muse_ambient_period_t muse_ambient_period_at(time_t utc);

/* Optional data-source hook. A caller must supply a real observation and its
 * expiry in Unix seconds; unknown/stale weather adds no weather effects.
 * No network request or allocation occurs in the frame renderer. */
void muse_pixel_set_weather(muse_ambient_weather_t weather, uint32_t expires_at);

#ifndef ESP_PLATFORM
/* Host preview only. These simulated inputs are never enabled on the board. */
void muse_pixel_ambient_preview(muse_ambient_period_t period,
                                muse_ambient_weather_t weather);
#endif
