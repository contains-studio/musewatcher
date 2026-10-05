#pragma once

#include "freertos/task.h"

BaseType_t xTaskCreateWithCaps(TaskFunction_t task, const char *name,
                              unsigned stack_depth, void *params,
                              unsigned priority, TaskHandle_t *out_handle,
                              unsigned caps);
void vTaskDeleteWithCaps(TaskHandle_t task);
