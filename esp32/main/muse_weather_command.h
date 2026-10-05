/* Copyright (c) contains-studio. Licensed under the Apache License, Version 2.0. */
#pragma once

#include "cJSON.h"

/* Validate the entire forecast before changing the card or saved outfit. */
cJSON *muse_weather_command(const cJSON *params);
