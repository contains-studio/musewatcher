"""Compile the real snapshot worker with a deferred task and owned LVGL buffer."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HEADERS = {
    'sdkconfig.h': '#define CONFIG_SPIRAM 1\n#define CONFIG_FREERTOS_TASK_CREATE_ALLOW_EXT_MEM 1\n#define CONFIG_SPIRAM_XIP_FROM_PSRAM TEST_EXTERNAL\n',
    'esp_err.h': 'typedef int esp_err_t;\n',
    'esp_log.h': '#define ESP_LOGI(...) ((void)0)\n',
    'esp_heap_caps.h': '#include <stddef.h>\n#define MALLOC_CAP_SPIRAM 1\n#define MALLOC_CAP_INTERNAL 2\n#define MALLOC_CAP_8BIT 4\nvoid *heap_caps_malloc(size_t,int);\nvoid heap_caps_free(void*);\nsize_t heap_caps_get_free_size(int);\nsize_t heap_caps_get_largest_free_block(int);\n',
    'freertos/FreeRTOS.h': '#pragma once\ntypedef int BaseType_t;\n#define pdPASS 1\n',
    'freertos/task.h': '#pragma once\n#include "freertos/FreeRTOS.h"\ntypedef void *TaskHandle_t;\ntypedef void (*TaskFunction_t)(void *);\nBaseType_t xTaskCreate(TaskFunction_t,const char*,unsigned,void*,unsigned,TaskHandle_t*);\nvoid vTaskDelete(TaskHandle_t);\n',
    'freertos/idf_additions.h': '#include "freertos/task.h"\nBaseType_t xTaskCreateWithCaps(TaskFunction_t,const char*,unsigned,void*,unsigned,TaskHandle_t*,unsigned);\nvoid vTaskDeleteWithCaps(TaskHandle_t);\n',
    'mbedtls/base64.h': '#include <stddef.h>\nint mbedtls_base64_encode(unsigned char*,size_t,size_t*,const unsigned char*,size_t);\n',
    'lvgl.h': '#pragma once\n#include <stdint.h>\n#ifndef LV_USE_SNAPSHOT\n#define LV_USE_SNAPSHOT 1\n#endif\n#define LV_COLOR_FORMAT_RGB565 1\ntypedef struct { int unused; } lv_obj_t;\ntypedef struct { struct { unsigned w,h,stride; } header; unsigned char *data; } lv_draw_buf_t;\nlv_draw_buf_t *lv_snapshot_take(lv_obj_t*,int);\nvoid lv_draw_buf_destroy(lv_draw_buf_t*);\n',
}
HARNESS = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include "lvgl.h"
#include "freertos/idf_additions.h"
#include "muse_snapshot.h"
static TaskFunction_t worker;
static void *argument;
static bool task_fail, capture_fail, in_worker;
static int captures, destroyed, writes, deletes, allocated;
static bool malloc_fail, write_fail;
static size_t free_memory=20000, largest=12000;
static char output[1024];
static unsigned char pixels[12]={1,2,3,4,99,99,5,6,7,8,99,99};
static lv_draw_buf_t snapshot={.header={2,2,6},.data=pixels};
lv_draw_buf_t *lv_snapshot_take(lv_obj_t *s,int cf) { assert(!in_worker);const unsigned char original[]={1,2,3,4,99,99,5,6,7,8,99,99};memcpy(pixels,original,sizeof(pixels));captures++;return capture_fail?NULL:&snapshot; }
void lv_draw_buf_destroy(lv_draw_buf_t *b) {assert(!in_worker && b==&snapshot);memset(pixels,0xee,sizeof(pixels));destroyed++;}
void *heap_caps_malloc(size_t n,int caps) {if(malloc_fail)return NULL;allocated++;return malloc(n);}
void heap_caps_free(void *p) {if(p){allocated--;free(p);}}
size_t heap_caps_get_free_size(int caps) {return free_memory;}
size_t heap_caps_get_largest_free_block(int caps) {return largest;}
BaseType_t xTaskCreateWithCaps(TaskFunction_t f,const char*n,unsigned s,void*a,unsigned p,TaskHandle_t*h,unsigned caps) {
 assert(TEST_EXTERNAL && !in_worker && !worker && caps==5); if(task_fail)return 0; worker=f;argument=a;return pdPASS;
}
BaseType_t xTaskCreate(TaskFunction_t f,const char*n,unsigned s,void*a,unsigned p,TaskHandle_t*h) {assert(!TEST_EXTERNAL && !in_worker && !worker);if(task_fail)return 0;worker=f;argument=a;return pdPASS;}
void vTaskDeleteWithCaps(TaskHandle_t t) {assert(TEST_EXTERNAL && in_worker);deletes++;}
void vTaskDelete(TaskHandle_t t) {assert(!TEST_EXTERNAL && in_worker);deletes++;}
void muse_console_write(const void *b,size_t n) {assert(strlen(output)+n<sizeof(output));strncat(output,b,n);writes++;}
bool muse_console_write_timeout(const void*b,size_t n,unsigned ms) {assert(in_worker ? ms>0 : ms==0);if(write_fail)return false;muse_console_write(b,n);return true;}
int mbedtls_base64_encode(unsigned char *out,size_t cap,size_t *len,const unsigned char *in,size_t n) {
 assert(in_worker && n==4 && cap>=8);
 const unsigned char row1[]={1,2,3,4},row2[]={5,6,7,8};
 assert(!memcmp(in,row1,4) || !memcmp(in,row2,4));
 const char *encoded=in[0]==1?"AQIDBA==":"BQYHCA==";
 memcpy(out,encoded,8);*len=8;return 0;
}
static void drain(void) {TaskFunction_t f=worker;worker=NULL;in_worker=true;f(argument);in_worker=false;}
int main(void) {
 lv_obj_t screen={0};
 muse_snapshot_start(&screen);
#if LV_USE_SNAPSHOT
 assert(worker && captures==1 && destroyed==1 && allocated==1 && writes==0); // UI returns before any USB writes.
 muse_snapshot_start(&screen);assert(captures==1); // Bounded, no queued second capture.
 drain();assert(destroyed==1 && deletes==1 && allocated==0);
 assert(strcmp(output,"\nSNAP BEGIN 2 2 144\nAQIDBA==\nBQYHCA==\nSNAP END\n")==0);
 assert(destroyed==1); // LVGL buffer was released before scheduling.
 task_fail=true;muse_snapshot_start(&screen);assert(!worker && destroyed==2);
 task_fail=false;capture_fail=true;muse_snapshot_start(&screen);assert(!worker && destroyed==2);
 capture_fail=false;free_memory=3000;int prior=captures;muse_snapshot_start(&screen);assert(captures==prior);
 free_memory=20000;largest=1024;muse_snapshot_start(&screen);assert(captures==prior);
 largest=12000;malloc_fail=true;muse_snapshot_start(&screen);assert(!worker && allocated==0);malloc_fail=false;output[0]=0;muse_snapshot_start(&screen);assert(worker);drain();assert(destroyed==4 && allocated==0);
 write_fail=true;muse_snapshot_start(&screen);drain();assert(allocated==0);write_fail=false;output[0]=0;muse_snapshot_start(&screen);assert(worker);drain();assert(allocated==0);
#else
 assert(!worker && captures==0 && strcmp(output,"\nSNAP OFF\n")==0);
#endif
 return 0;
}
'''

class SnapshotTest(unittest.TestCase):
    def test_background_transfer_and_cleanup(self):
        self.run_variant(True)

    def test_internal_stack_fallback(self):
        self.run_variant(True, external=False)

    def test_snapshot_disabled(self):
        self.run_variant(False)

    def run_variant(self, enabled, external=True):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            for name, text in HEADERS.items():
                path = tmp / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
            (tmp / 'test.c').write_text(HARNESS)
            command = ['cc', '-std=c11', '-Wall', '-Wextra', '-Wno-unused-parameter',
                       '-Wno-unused-variable', f'-DLV_USE_SNAPSHOT={int(enabled)}', f'-DTEST_EXTERNAL={int(external)}',
                       '-fsanitize=address,undefined', '-g', '-I', str(tmp), '-I',
                       str(ROOT / 'components/muse'), str(tmp / 'test.c'),
                       str(ROOT / 'components/muse/muse_snapshot.c'), '-o', str(tmp / 'test')]
            built = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            run = subprocess.run([str(tmp / 'test')], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
